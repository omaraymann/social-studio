"""Dashboard-owned Instagram authorization and connection management."""

import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import current_user
from app.database import get_db
from app.models import InstagramConnection, InstagramOAuthAttempt, User, utcnow
from app.publishers.base import PublisherError
from app.publishers.instagram import InstagramPublisher
from app.services.instagram_connections import cipher, connection_summary, encrypt_token
from app.services.instagram_oauth import (
    InstagramOAuth, InstagramOAuthError, authorization_url, get_instagram_oauth,
)

router = APIRouter(prefix="/instagram", tags=["Instagram"])
COOKIE_NAME = "instagram_oauth_browser"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def private_json(value):
    from fastapi.encoders import jsonable_encoder
    return JSONResponse(jsonable_encoder(value), headers={"Cache-Control": "no-store"})


@router.get("/connection")
def connection(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return private_json(connection_summary(db, user.id))


@router.post("/connection/verify")
async def verify_connection(user: User = Depends(current_user)):
    try:
        identity = await InstagramPublisher().connection(user.id)
    except PublisherError as exc:
        raise HTTPException(422, str(exc)) from exc
    return private_json({"connected": True, **identity})


@router.post("/oauth/start")
def start(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    try:
        url = authorization_url(state)
        cipher()
    except InstagramOAuthError as exc:
        raise HTTPException(503, str(exc)) from exc
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    db.execute(update(InstagramOAuthAttempt).where(InstagramOAuthAttempt.owner_id == user.id)
               .values(used_at=utcnow(), cancelled=True))
    db.add(InstagramOAuthAttempt(state_hash=digest(state), owner_id=user.id,
        browser_hash=digest(browser), redirect_uri=settings.instagram_redirect_uri,
        expires_at=utcnow() + timedelta(minutes=10)))
    db.commit()
    response = private_json({"authorization_url": url})
    response.set_cookie(COOKIE_NAME, browser, max_age=600, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/instagram/oauth")
    return response


class OAuthComplete(BaseModel):
    state: str = Field(min_length=20, max_length=200)
    code: str | None = Field(default=None, max_length=4096)
    denied: bool = False


@router.post("/oauth/complete")
async def complete(payload: OAuthComplete, user: User = Depends(current_user), db: Session = Depends(get_db),
                   browser: str | None = Cookie(default=None, alias=COOKIE_NAME),
                   oauth: InstagramOAuth = Depends(get_instagram_oauth)):
    if not browser:
        raise HTTPException(400, "Connection session missing. Start Connect Instagram again in this browser.")
    attempt = db.get(InstagramOAuthAttempt, digest(payload.state))
    if not attempt:
        raise HTTPException(400, "Invalid or expired Instagram connection request")
    changed = db.execute(update(InstagramOAuthAttempt).where(
        InstagramOAuthAttempt.state_hash == digest(payload.state),
        InstagramOAuthAttempt.owner_id == user.id,
        InstagramOAuthAttempt.browser_hash == digest(browser),
        InstagramOAuthAttempt.cancelled.is_(False),
        InstagramOAuthAttempt.used_at.is_(None), InstagramOAuthAttempt.expires_at > utcnow())
        .values(used_at=utcnow()).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "Invalid, expired, or already-used Instagram connection request")
    redirect_uri = attempt.redirect_uri
    db.commit()
    if payload.denied:
        response = private_json({"cancelled": True})
    else:
        if not payload.code:
            raise HTTPException(400, "Instagram did not return an authorization code. Connect again.")
        try:
            result = await oauth.exchange(payload.code, redirect_uri)
            encrypted = encrypt_token(user.id, result["user_id"], result["access_token"])
        except InstagramOAuthError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.scalar(select(User).where(User.id == user.id).with_for_update())
        db.refresh(attempt)
        if attempt.cancelled:
            raise HTTPException(409, "Connection request was cancelled or replaced. Start Connect Instagram again.")
        row = db.scalar(select(InstagramConnection).where(InstagramConnection.owner_id == user.id)
                        .execution_options(populate_existing=True))
        if not row:
            row = InstagramConnection(owner_id=user.id)
            db.add(row)
        now = utcnow()
        row.instagram_user_id, row.username = result["user_id"], result["username"]
        row.encrypted_token, row.status, row.scopes = encrypted, "active", result["scopes"]
        row.expires_at, row.token_issued_at = now + timedelta(seconds=result["expires_in"]), now
        row.updated_at = now
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "This Instagram account is already connected to another workspace. Disconnect it there first.") from exc
        response = private_json(connection_summary(db, user.id))
    response.delete_cookie(COOKIE_NAME, path="/instagram/oauth")
    return response


@router.delete("/connection", status_code=204)
def disconnect(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    row = db.scalar(select(InstagramConnection).where(InstagramConnection.owner_id == user.id).with_for_update())
    if row:
        row.status, row.encrypted_token, row.updated_at = "disconnected", None, utcnow()
    db.execute(update(InstagramOAuthAttempt).where(InstagramOAuthAttempt.owner_id == user.id)
               .values(used_at=utcnow(), cancelled=True))
    db.commit()
