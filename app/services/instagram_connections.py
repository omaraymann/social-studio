"""Owner-scoped encrypted credentials and connection lifecycle."""

import json
from dataclasses import dataclass, field
from datetime import timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import InstagramConnection, utcnow
from app.services.instagram_oauth import InstagramOAuth, InstagramOAuthError, oauth_configured


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def cipher():
    try:
        return Fernet((settings.instagram_token_encryption_key or "").encode())
    except (ValueError, TypeError) as exc:
        raise InstagramOAuthError("Instagram token encryption is not configured correctly") from exc


def encrypt_token(owner_id, account_id, token):
    payload = json.dumps({"owner_id": owner_id, "account_id": account_id, "token": token}).encode()
    return cipher().encrypt(payload).decode()


def decrypt_token(row):
    try:
        payload = json.loads(cipher().decrypt(row.encrypted_token.encode()))
        if payload["owner_id"] != row.owner_id or payload["account_id"] != row.instagram_user_id:
            raise ValueError("Credential context mismatch")
        return payload["token"]
    except (InvalidToken, ValueError, TypeError, AttributeError, KeyError) as exc:
        raise InstagramOAuthError("Instagram credentials could not be read. Reconnect the account.") from exc


@dataclass(frozen=True)
class InstagramCredentials:
    owner_id: int
    account_id: str
    username: str
    access_token: str = field(repr=False)
    revision: str | None = None


def connection_summary(db, owner_id):
    row = db.get(InstagramConnection, owner_id)
    status = row.status if row else "disconnected"
    if row and row.expires_at and aware(row.expires_at) <= utcnow() and status == "active":
        status = "expired"
    connected = bool(row and status == "active" and row.encrypted_token)
    return {"connected": connected, "status": status,
            "username": row.username if row else None,
            "user_id": row.instagram_user_id if row else None,
            "expires_at": aware(row.expires_at) if row else None,
            "oauth_configured": oauth_configured(),
            "media_configured": bool(settings.public_media_base_url)}


def available_connection(owner_id):
    with SessionLocal() as db:
        row = db.get(InstagramConnection, owner_id) if owner_id else None
        if row:
            summary = connection_summary(db, owner_id)
            return summary if summary["connected"] else None
    return None


async def load_credentials(owner_id, oauth=None):
    with SessionLocal() as db:
        row = db.scalar(select(InstagramConnection).where(InstagramConnection.owner_id == owner_id).with_for_update())
        if not row:
            raise InstagramOAuthError("Connect an Instagram account from the dashboard first")
        if row.status != "active" or not row.encrypted_token:
            raise InstagramOAuthError("Reconnect Instagram from the dashboard before publishing")
        now = utcnow()
        expires = aware(row.expires_at)
        if expires and expires <= now:
            row.status = "expired"
            db.commit()
            raise InstagramOAuthError("Instagram connection expired. Reconnect from the dashboard.")
        token = decrypt_token(row)
        if expires and expires < now + timedelta(days=7) and aware(row.token_issued_at) < now - timedelta(days=1):
            token, lifetime = await (oauth or InstagramOAuth()).refresh(token)
            row.encrypted_token = encrypt_token(owner_id, row.instagram_user_id, token)
            row.expires_at, row.token_issued_at = now + timedelta(seconds=lifetime), now
            row.updated_at = now
            db.commit()
        return InstagramCredentials(owner_id, row.instagram_user_id, row.username, token, aware(row.updated_at).isoformat())


def assert_credentials_current(credentials):
    if credentials.revision is None:
        raise InstagramOAuthError("Instagram credentials need an owner-scoped connection")
    with SessionLocal() as db:
        row = db.get(InstagramConnection, credentials.owner_id)
        if (not row or row.status != "active" or not row.encrypted_token
                or row.instagram_user_id != credentials.account_id
                or aware(row.updated_at).isoformat() != credentials.revision):
            raise InstagramOAuthError("Instagram connection changed during delivery; retry after checking the connected account")


def mark_reconnect(owner_id, account_id, revision=None):
    with SessionLocal() as db:
        row = db.get(InstagramConnection, owner_id)
        if row and row.instagram_user_id == account_id and (
                revision is None or aware(row.updated_at).isoformat() == revision):
            row.status, row.updated_at = "needs_reconnect", utcnow()
            db.commit()


async def refresh_due_connections():
    """Called hourly by the Instagram worker, even when no posts are scheduled."""
    with SessionLocal() as db:
        owners = db.scalars(select(InstagramConnection.owner_id).where(
            InstagramConnection.status == "active", InstagramConnection.expires_at.is_not(None),
            InstagramConnection.expires_at < utcnow() + timedelta(days=7))).all()
    for owner_id in owners:
        try:
            await load_credentials(owner_id)
        except InstagramOAuthError:
            # Expiry is persisted by load_credentials; transient errors retry next hour.
            continue
