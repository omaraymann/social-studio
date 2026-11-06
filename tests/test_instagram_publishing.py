import asyncio
import hashlib
import io
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import PlatformVariant, PublishAttempt, PublishDelivery, ScheduleSlot
from app.publishers.base import PublishImage, PublishRequest, PublisherError
from app.publishers.instagram import InstagramPublisher
from app.publishers.registry import PublisherRegistry, get_publisher_registry
from app.services.instagram_media import read_signed_image
from app.services.image_storage import storage_path
from app.services.worker import recover_expired_leases, run_once


@pytest.fixture
def instagram_settings(monkeypatch, tmp_path, auth_headers):
    monkeypatch.setattr(settings, "image_storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "instagram_access_token", "test-token-never-real")
    monkeypatch.setattr(settings, "instagram_user_id", "178400001")
    monkeypatch.setattr(settings, "instagram_owner_user_id", 1)
    monkeypatch.setattr(settings, "instagram_username", "teststudio")
    monkeypatch.setattr(settings, "public_media_base_url", "https://media.example.test")
    from cryptography.fernet import Fernet
    from app.models import InstagramConnection, utcnow
    from app.services.instagram_connections import encrypt_token
    monkeypatch.setattr(settings, "instagram_token_encryption_key", Fernet.generate_key().decode())
    with SessionLocal() as db:
        db.add(InstagramConnection(owner_id=1, instagram_user_id="178400001", username="teststudio",
            encrypted_token=encrypt_token(1, "178400001", "test-token-never-real"), status="active",
            scopes=["instagram_business_basic", "instagram_business_content_publish"],
            expires_at=utcnow() + timedelta(days=60)))
        db.commit()
    stream = io.BytesIO()
    Image.new("RGB", (640, 640), "#445588").save(stream, "PNG")
    return stream.getvalue()


def media_response(request):
    assert "authorization" not in request.headers
    parsed = urlsplit(str(request.url))
    query = parse_qs(parsed.query)
    image_id = parsed.path.rsplit("/", 1)[-1].removesuffix(".jpg")
    raw = read_signed_image(image_id, int(query["expires"][0]), query["signature"][0])
    return httpx.Response(200, content=raw, headers={"Content-Type": "image/jpeg"})


class Graph:
    def __init__(self, *, statuses=None, publish_error=None, permalink_error=False):
        self.statuses = list(statuses or ["FINISHED"])
        self.publish_error = publish_error
        self.permalink_error = permalink_error
        self.calls = []

    def __call__(self, request):
        assert request.headers["authorization"] == "Bearer test-token-never-real"
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/me"):
            return httpx.Response(200, json={"user_id": "178400001", "username": "teststudio"})
        if path.endswith("/178400001/media"):
            self.caption = parse_qs(request.content.decode())["caption"][0]
            return httpx.Response(200, json={"id": "111"})
        if path.endswith("/111"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"status_code": status})
        if path.endswith("/media_publish"):
            if self.publish_error:
                raise self.publish_error
            return httpx.Response(200, json={"id": "222"})
        if path.endswith("/222"):
            if self.permalink_error:
                return httpx.Response(500, json={"error": {"code": 2}})
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/test-post/"})
        raise AssertionError(f"Unexpected request: {request.method} {path}")


def publisher(graph):
    return InstagramPublisher(transport=httpx.MockTransport(graph), media_transport=httpx.MockTransport(media_response),
                              poll_seconds=0, poll_attempts=2)


def request_for(raw, state=None, save=None):
    return PublishRequest("A useful lamp. Learn more.", "stable-key", PublishImage(raw, "lamp.png"),
                          owner_user_id=1, provider_state=state or {}, save_state=save or (lambda state: None))


def test_publish_poll_and_resume_confirmed_post_without_duplicate(instagram_settings):
    graph = Graph(statuses=["IN_PROGRESS", "FINISHED"])
    saved = {}
    pub = publisher(graph)
    result = asyncio.run(pub.publish(request_for(instagram_settings, save=lambda state: saved.update(state))))
    assert result.external_post_id == "222" and result.external_url.endswith("/test-post/")
    assert saved["container_id"] == "111" and saved["stage"] == "published"
    assert graph.caption == "A useful lamp. Learn more."
    asyncio.run(pub.publish(request_for(instagram_settings, state=saved)))
    assert sum(path.endswith("/media_publish") for _, path in graph.calls) == 1
    assert sum(path.endswith("/178400001/media") for _, path in graph.calls) == 1


def test_processing_timeout_reuses_container(instagram_settings):
    graph = Graph(statuses=["IN_PROGRESS"])
    saved = {}
    pub = publisher(graph)
    with pytest.raises(PublisherError) as error:
        asyncio.run(pub.publish(request_for(instagram_settings, save=lambda state: saved.update(state))))
    assert error.value.retryable and not error.value.outcome_uncertain
    graph.statuses = ["FINISHED"]
    asyncio.run(pub.publish(request_for(instagram_settings, state=saved)))
    assert sum(path.endswith("/178400001/media") for _, path in graph.calls) == 1


def test_publish_timeout_blocks_blind_retry(instagram_settings):
    graph = Graph(publish_error=httpx.ReadTimeout("No confirmation"))
    saved = {}
    pub = publisher(graph)
    with pytest.raises(PublisherError) as error:
        asyncio.run(pub.publish(request_for(instagram_settings, save=lambda state: saved.update(state))))
    assert error.value.outcome_uncertain
    assert saved["stage"] == "publishing"
    with pytest.raises(PublisherError):
        asyncio.run(pub.publish(request_for(instagram_settings, state=saved)))
    assert sum(path.endswith("/media_publish") for _, path in graph.calls) == 1


def test_permalink_failure_does_not_retry_confirmed_publication(instagram_settings):
    result = asyncio.run(publisher(Graph(permalink_error=True)).publish(request_for(instagram_settings)))
    assert result.external_post_id == "222" and result.external_url is None


def test_wrong_workspace_or_token_account_never_posts(instagram_settings):
    graph = Graph()
    pub = publisher(graph)
    request = PublishRequest("caption", "key", PublishImage(instagram_settings, "image.png"), owner_user_id=2)
    with pytest.raises(PublisherError):
        asyncio.run(pub.publish(request))
    assert not graph.calls
    def wrong_identity(request):
        return httpx.Response(200, json={"user_id": "different", "username": "someoneelse"})
    pub = InstagramPublisher(transport=httpx.MockTransport(wrong_identity))
    with pytest.raises(PublisherError, match="different account"):
        asyncio.run(pub.publish(request_for(instagram_settings)))


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_failed_processing_never_publishes(instagram_settings, status):
    graph = Graph(statuses=[status])
    with pytest.raises(PublisherError):
        asyncio.run(publisher(graph).publish(request_for(instagram_settings)))
    assert not any(path.endswith("media_publish") for _, path in graph.calls)


def test_unavailable_tunnel_never_creates_container(instagram_settings):
    graph = Graph()
    pub = publisher(graph)
    pub.media_transport = httpx.MockTransport(lambda req: httpx.Response(404))
    with pytest.raises(PublisherError, match="tunnel"):
        asyncio.run(pub.publish(request_for(instagram_settings)))
    assert not any(method == "POST" for method, _ in graph.calls)


def prepare_schedule(client, headers, raw):
    post = client.post("/posts", headers=headers, json={"title": "Lamp", "markdown": "A useful lamp with a white shade."}).json()
    response = client.post(f"/posts/{post['id']}/variants", headers=headers, json={"platforms": ["instagram"]})
    assert response.status_code == 201, response.text
    variant = response.json()[0]
    assert client.post(f"/variants/{variant['id']}/approve", headers=headers).status_code == 422
    key = "posts/scheduled.png"
    path = storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    with SessionLocal() as db:
        row = db.get(PlatformVariant, variant["id"])
        row.selected_image_key = key
        row.selected_image_sha256 = hashlib.sha256(raw).hexdigest()
        db.commit()
    assert client.post(f"/variants/{variant['id']}/approve", headers=headers).status_code == 200
    response = client.post(f"/variants/{variant['id']}/schedule", headers=headers,
                           json={"publish_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    assert response.status_code == 201, response.text
    return response.json(), variant


def test_dashboard_delivery_saves_ids_link_and_blocks_duplicates(client, auth_headers, instagram_settings):
    schedule, variant = prepare_schedule(client, auth_headers, instagram_settings)
    graph = Graph()
    app.dependency_overrides[get_publisher_registry] = lambda: PublisherRegistry({"instagram": publisher(graph)})
    try:
        response = client.post(f"/schedules/{schedule['id']}/publish", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["external_post_id"] == "222"
        assert response.json()["stage"] == "published"
        duplicate = client.post(f"/schedules/{schedule['id']}/publish", headers=auth_headers)
        assert duplicate.json()["already_published"]
        assert sum(path.endswith("media_publish") for _, path in graph.calls) == 1
        history = client.get("/publish-history", headers=auth_headers).json()[0]
        assert history["delivery"]["external_url"].endswith("/test-post/")
    finally:
        app.dependency_overrides.pop(get_publisher_registry, None)
    with SessionLocal() as db:
        saved = db.scalar(select(PublishDelivery))
        assert saved.provider_state["container_id"] == "111"
        assert db.get(PlatformVariant, variant["id"]).status == "published"


def test_worker_runs_due_instagram_and_blocks_uncertain_manual_retry(client, auth_headers, instagram_settings):
    schedule, _ = prepare_schedule(client, auth_headers, instagram_settings)
    graph = Graph(publish_error=httpx.ReadTimeout("No confirmation"))
    registry = PublisherRegistry({"instagram": publisher(graph)})
    with SessionLocal() as db:
        row = db.get(ScheduleSlot, schedule["id"])
        row.publish_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert asyncio.run(run_once(registry=registry, platform="instagram"))
    with SessionLocal() as db:
        assert db.get(ScheduleSlot, schedule["id"]).status == "failed"
        assert db.scalar(select(PublishAttempt)).status == "uncertain"
    assert client.post(f"/schedules/{schedule['id']}/retry", headers=auth_headers).status_code == 409
    app.dependency_overrides[get_publisher_registry] = lambda: registry
    try:
        assert client.post(f"/schedules/{schedule['id']}/publish", headers=auth_headers).status_code == 502
        assert sum(path.endswith("media_publish") for _, path in graph.calls) == 1
    finally:
        app.dependency_overrides.pop(get_publisher_registry, None)


def test_lease_recovery_never_retries_instagram_as_mock(client, auth_headers, instagram_settings):
    schedule, _ = prepare_schedule(client, auth_headers, instagram_settings)
    with SessionLocal() as db:
        row = db.get(ScheduleSlot, schedule["id"])
        row.status = "processing"
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        delivery = PublishDelivery(schedule_slot_id=row.id, platform="instagram", idempotency_key="key", content_snapshot="caption")
        db.add(delivery)
        db.commit()
        recover_expired_leases(db, datetime.now(timezone.utc))
        assert row.status == "failed" and row.last_error.startswith("Outcome uncertain")


def test_expired_token_is_safe_and_actionable(instagram_settings):
    pub = InstagramPublisher(transport=httpx.MockTransport(lambda req: httpx.Response(400, json={
        "error": {"code": 190, "message": "secret provider text test-token-never-real"}})))
    with pytest.raises(PublisherError) as error:
        asyncio.run(pub.publish(request_for(instagram_settings)))
    assert "expired" in str(error.value)
    assert "test-token" not in str(error.value)
    assert not error.value.retryable


def test_due_worker_success_and_platform_isolation(client, auth_headers, instagram_settings):
    schedule, variant = prepare_schedule(client, auth_headers, instagram_settings)
    graph = Graph()
    with SessionLocal() as db:
        row = db.get(ScheduleSlot, schedule["id"])
        row.publish_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert not asyncio.run(run_once(registry=PublisherRegistry({"instagram": publisher(graph)}), platform="discord"))
    assert asyncio.run(run_once(registry=PublisherRegistry({"instagram": publisher(graph)}), platform="instagram"))
    with SessionLocal() as db:
        assert db.get(ScheduleSlot, schedule["id"]).status == "completed"
        assert db.get(PlatformVariant, variant["id"]).status == "published"
        assert db.scalar(select(PublishDelivery)).provider_state["media_id"] == "222"


def test_instagram_limits_and_incompatible_image(client, auth_headers, instagram_settings):
    post = client.post("/posts", headers=auth_headers, json={"title":"Lamp", "markdown":"A useful lamp with a white shade."}).json()
    variant = client.post(f"/posts/{post['id']}/variants", headers=auth_headers, json={"platforms":["instagram"]}).json()[0]
    assert client.patch(f"/variants/{variant['id']}", headers=auth_headers, json={"content":"x" * 2201}).status_code == 422
    stream = io.BytesIO()
    Image.new("RGB", (640, 200), "blue").save(stream, "PNG")
    graph = Graph()
    with pytest.raises(PublisherError, match="aspect ratio"):
        asyncio.run(publisher(graph).publish(request_for(stream.getvalue())))
    assert not graph.calls
