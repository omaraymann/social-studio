import asyncio
import hashlib
import io
import logging
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.media_gateway import app as gateway
from app.models import InstagramConnection, InstagramOAuthAttempt, utcnow
from app.publishers.base import PublishImage, PublishRequest, PublisherError
from app.publishers.instagram import InstagramPublisher
from app.services.instagram_connections import decrypt_token, encrypt_token, load_credentials, connection_summary
from app.services.instagram_oauth import SCOPES, InstagramOAuth, InstagramOAuthError, get_instagram_oauth


@pytest.fixture
def oauth_settings(monkeypatch):
    monkeypatch.setattr(settings, "instagram_app_id", "app-123")
    monkeypatch.setattr(settings, "instagram_app_secret", "secret-only-for-server")
    monkeypatch.setattr(settings, "instagram_redirect_uri", "https://media.example.test/instagram/oauth/callback")
    monkeypatch.setattr(settings, "instagram_token_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "frontend_base_url", "http://localhost:5173")
    monkeypatch.setattr(settings, "instagram_access_token", None)
    monkeypatch.setattr(settings, "instagram_owner_user_id", None)
    monkeypatch.setattr(settings, "public_media_base_url", "https://media.example.test")


class FakeOAuth:
    def __init__(self, account="178400011", token="private-user-token"):
        self.account, self.token, self.calls = account, token, []

    async def exchange(self, code, redirect_uri):
        self.calls.append((code, redirect_uri))
        return {"user_id": self.account, "username": "teststudio", "access_token": self.token,
                "expires_in": 60 * 86400, "scopes": list(SCOPES)}


@pytest.fixture
def fake_oauth():
    fake = FakeOAuth()
    app.dependency_overrides[get_instagram_oauth] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_instagram_oauth, None)


def begin(client, headers):
    response = client.post("/instagram/oauth/start", headers=headers)
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert query["scope"] == [",".join(SCOPES)]
    return query["state"][0]


def finish(client, headers, state, **extra):
    return client.post("/instagram/oauth/complete", headers=headers,
                       json={"state": state, "code": "one-use-code", **extra})


def second_user(client):
    credentials = {"email": "second@example.com", "password": "password123"}
    client.post("/auth/signup", json=credentials)
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": "Bearer " + token}


def test_connect_encrypts_token_and_hides_credentials(client, auth_headers, oauth_settings, fake_oauth):
    state = begin(client, auth_headers)
    with SessionLocal() as db:
        attempt = db.scalar(select(InstagramOAuthAttempt))
        assert attempt.state_hash != state
    result = finish(client, auth_headers, state)
    assert result.status_code == 200, result.text
    assert result.json()["connected"] and result.json()["username"] == "teststudio"
    status = client.get("/instagram/connection", headers=auth_headers)
    assert status.headers["cache-control"] == "no-store"
    for secret in ("private-user-token", "secret-only-for-server", settings.instagram_token_encryption_key):
        assert secret not in status.text and secret not in result.text
    with SessionLocal() as db:
        row = db.get(InstagramConnection, 1)
        assert row.encrypted_token != fake_oauth.token and fake_oauth.token not in row.encrypted_token
        assert decrypt_token(row) == fake_oauth.token
        assert row.expires_at is not None
    assert len(fake_oauth.calls) == 1
    capabilities = client.get("/schedules/publishers", headers=auth_headers).json()
    instagram = next(item for item in capabilities if item["platform"] == "instagram")
    assert instagram["account_id"] == fake_oauth.account
    other = second_user(client)
    other_capabilities = client.get("/schedules/publishers", headers=other).json()
    other_instagram = next(item for item in other_capabilities if item["platform"] == "instagram")
    assert other_instagram["account_id"] is None
    assert other_instagram["account"] is None
    assert not other_instagram["configured"]


def test_state_replay_and_wrong_owner_blocked(client, auth_headers, oauth_settings, fake_oauth):
    state = begin(client, auth_headers)
    other = second_user(client)
    assert finish(client, other, state).status_code == 400
    assert not fake_oauth.calls
    assert finish(client, auth_headers, state).status_code == 200
    assert finish(client, auth_headers, state).status_code == 400
    assert len(fake_oauth.calls) == 1


def test_browser_binding_and_expiry(client, auth_headers, oauth_settings, fake_oauth):
    state = begin(client, auth_headers)
    with TestClient(app) as other_browser:
        assert finish(other_browser, auth_headers, state).status_code == 400
    with SessionLocal() as db:
        attempt = db.get(InstagramOAuthAttempt, hashlib.sha256(state.encode()).hexdigest())
        attempt.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert finish(client, auth_headers, state).status_code == 400
    assert not fake_oauth.calls


def test_denial_preserves_existing_connection(client, auth_headers, oauth_settings, fake_oauth):
    assert finish(client, auth_headers, begin(client, auth_headers)).status_code == 200
    state = begin(client, auth_headers)
    result = finish(client, auth_headers, state, denied=True, code=None)
    assert result.json()["cancelled"]
    assert client.get("/instagram/connection", headers=auth_headers).json()["connected"]
    assert len(fake_oauth.calls) == 1


def test_disconnect_cannot_resurrect_legacy_or_replay(client, auth_headers, oauth_settings, fake_oauth, monkeypatch):
    assert finish(client, auth_headers, begin(client, auth_headers)).status_code == 200
    state = begin(client, auth_headers)
    monkeypatch.setattr(settings, "instagram_owner_user_id", 1)
    monkeypatch.setattr(settings, "instagram_access_token", "legacy-token")
    monkeypatch.setattr(settings, "instagram_user_id", "178400011")
    assert client.delete("/instagram/connection", headers=auth_headers).status_code == 204
    assert not client.get("/instagram/connection", headers=auth_headers).json()["connected"]
    assert finish(client, auth_headers, state).status_code == 400
    with pytest.raises(InstagramOAuthError, match="Reconnect"):
        asyncio.run(load_credentials(1))
    with SessionLocal() as db:
        assert db.get(InstagramConnection, 1).encrypted_token is None


def test_account_isolation_and_transfer_only_after_disconnect(client, auth_headers, oauth_settings, fake_oauth):
    assert finish(client, auth_headers, begin(client, auth_headers)).status_code == 200
    other = second_user(client)
    assert not client.get("/instagram/connection", headers=other).json()["connected"]
    assert finish(client, other, begin(client, other)).status_code == 409
    assert client.delete("/instagram/connection", headers=auth_headers).status_code == 204
    assert finish(client, other, begin(client, other)).status_code == 200
    assert not client.get("/instagram/connection", headers=auth_headers).json()["connected"]


def test_callback_is_relay_only_and_code_uses_fragment(oauth_settings):
    with TestClient(gateway) as client:
        response = client.get("/instagram/oauth/callback", params={"code": "private-code", "state": "state-123"}, follow_redirects=False)
        assert response.status_code == 303
        target = urlsplit(response.headers["location"])
        assert target.netloc == "localhost:5173" and not target.query
        assert parse_qs(target.fragment)["code"] == ["private-code"]
        assert response.headers["referrer-policy"] == "no-referrer"
        assert client.post("/instagram/oauth/complete", json={}).status_code == 404


def test_missing_operator_setup_and_unauthenticated_access(client, oauth_settings, monkeypatch):
    assert client.post("/instagram/oauth/start").status_code in (401, 403)
    assert client.get("/instagram/connection").status_code in (401, 403)
    monkeypatch.setattr(settings, "instagram_app_secret", None)
    from app.services.instagram_oauth import authorization_url
    with pytest.raises(InstagramOAuthError, match="incomplete"):
        authorization_url("random")


def save_connection(owner=1, account="178400011", expires_days=60):
    with SessionLocal() as db:
        row = InstagramConnection(owner_id=owner, instagram_user_id=account, username="teststudio",
            encrypted_token=encrypt_token(owner, account, "private-token"), scopes=list(SCOPES), status="active",
            expires_at=utcnow() + timedelta(days=expires_days), token_issued_at=utcnow() - timedelta(days=30))
        db.add(row); db.commit()


def test_expiry_and_automatic_refresh(auth_headers, oauth_settings):
    save_connection(expires_days=2)
    class Refresh:
        async def refresh(self, token):
            assert token == "private-token"
            return "renewed-token", 60 * 86400
    credentials = asyncio.run(load_credentials(1, oauth=Refresh()))
    assert credentials.access_token == "renewed-token"
    assert "renewed-token" not in repr(credentials)
    with SessionLocal() as db:
        row = db.get(InstagramConnection, 1)
        assert decrypt_token(row) == "renewed-token"
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with pytest.raises(InstagramOAuthError, match="expired"):
        asyncio.run(load_credentials(1))
    with SessionLocal() as db:
        assert connection_summary(db, 1)["status"] == "expired"


def test_changed_scheduled_destination_fails_before_network(auth_headers, oauth_settings):
    save_connection(account="178400022")
    stream = io.BytesIO(); Image.new("RGB", (640, 640), "blue").save(stream, "PNG")
    calls = []
    pub = InstagramPublisher(transport=httpx.MockTransport(lambda req: calls.append(req)))
    request = PublishRequest("Lamp", "key", image=PublishImage(stream.getvalue(), "lamp.png"), owner_user_id=1,
                             save_state=lambda s: None, destination_account_id="178400011")
    with pytest.raises(PublisherError, match="destination"):
        asyncio.run(pub.publish(request))
    assert not calls


def test_credential_ciphertext_cannot_be_swapped_between_owners(auth_headers, oauth_settings):
    save_connection()
    with SessionLocal() as db:
        row = db.get(InstagramConnection, 1)
        row.instagram_user_id = "different-account"
        with pytest.raises(InstagramOAuthError):
            decrypt_token(row)


def test_real_oauth_protocol_contract_and_safe_logs(oauth_settings, caplog):
    def respond(request):
        if request.url.host == "api.instagram.com":
            form = parse_qs(request.content.decode())
            assert form["client_secret"] == ["secret-only-for-server"]
            assert form["redirect_uri"] == [settings.instagram_redirect_uri]
            return httpx.Response(200, json={"access_token": "short-token", "permissions": list(SCOPES)})
        if request.url.path == "/access_token":
            return httpx.Response(200, json={"access_token": "long-token", "expires_in": 5184000})
        assert request.headers["authorization"] == "Bearer long-token"
        return httpx.Response(200, json={"user_id": "178400011", "username": "teststudio", "account_type": "BUSINESS"})
    with caplog.at_level(logging.INFO, logger="httpx"):
        result = asyncio.run(InstagramOAuth(httpx.MockTransport(respond)).exchange("private-code", settings.instagram_redirect_uri))
    assert result["access_token"] == "long-token"
    for secret in ("short-token", "long-token", "secret-only-for-server", "private-code"):
        assert secret not in caplog.text


def test_missing_publish_permission_does_not_store_token(oauth_settings):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={
        "access_token": "private-token", "permissions": ["instagram_business_basic"]}))
    with pytest.raises(InstagramOAuthError, match="both"):
        asyncio.run(InstagramOAuth(transport).exchange("code", settings.instagram_redirect_uri))


def test_disconnect_during_exchange_cancels_linking(client, auth_headers, oauth_settings, fake_oauth):
    original = fake_oauth.exchange
    async def cancelled(code, redirect_uri):
        with SessionLocal() as db:
            attempt = db.scalar(select(InstagramOAuthAttempt))
            attempt.cancelled = True
            db.commit()
        return await original(code, redirect_uri)
    fake_oauth.exchange = cancelled
    assert finish(client, auth_headers, begin(client, auth_headers)).status_code == 409
    with SessionLocal() as db:
        assert db.get(InstagramConnection, 1) is None


def test_shared_publisher_uses_distinct_credentials_for_each_owner(client, auth_headers, oauth_settings):
    second_user(client)
    save_connection(owner=1, account="178400011")
    save_connection(owner=2, account="178400022")
    pub = InstagramPublisher()
    async def bind_both():
        return await asyncio.gather(pub.bound(1), pub.bound(2))
    first, second = asyncio.run(bind_both())
    assert pub.credentials is None
    assert first.credentials.account_id == "178400011"
    assert second.credentials.account_id == "178400022"
    assert first.credentials.owner_id == 1 and second.credentials.owner_id == 2
