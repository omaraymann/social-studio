import asyncio
import io
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from app.database import SessionLocal
from app.llm.base import LLMResult
from app.llm.registry import get_llm_provider
from app.main import app
from app.models import CampaignVisual, PlatformVariant, PublishAttempt, ScheduleSlot
from app.publishers.base import PublishImage, PublishRequest, PublishResult, SocialPublisher
from app.publishers.discord import DiscordPublisher
from app.publishers.registry import PublisherRegistry, get_publisher_registry
from app.services import llm_generation
from app.services.image_storage import save_generated, storage_path
from app.services.worker import run_once
from rag import pipeline


@pytest.fixture(autouse=True)
def private_storage(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "image_storage_dir", str(tmp_path))


def make_variant(client, headers, platform="x"):
    post = client.post("/posts", headers=headers, json={"title": "Product guide", "markdown": "A useful lamp has a white shade and a bronze base."}).json()
    variant = client.post(f"/posts/{post['id']}/variants", headers=headers, json={"platforms": [platform]}).json()[0]
    return post, variant


def make_visual(post_id, platform="x"):
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), "#dad8cc").save(buffer, "PNG")
    key, width, height = save_generated(buffer.getvalue())
    with SessionLocal() as db:
        row = CampaignVisual(source_post_id=post_id, platform=platform, vision_model="test", image_model="test",
            aspect_ratio="16:9", style="test", reference_image_ids=[], context={}, status="succeeded", step="ready",
            headline="A reading corner", call_to_action="Explore the lamp", visual_brief="Lamp on a side table",
            image_key=key, width=width, height=height)
        db.add(row); db.commit(); db.refresh(row)
        return row.id


class RecordingPublisher(SocialPublisher):
    supports_images = True

    def __init__(self):
        self.requests = []

    async def publish(self, request):
        self.requests.append(request)
        return PublishResult(external_post_id="test-post")


def test_rag_filters_low_and_nonfinite_scores_without_dropping_boundary(monkeypatch):
    monkeypatch.setattr(pipeline, "embed_query", lambda query: np.array([1., 0.]))

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, parameters): assert parameters[-1] == 5
        def fetchall(self):
            return [(str(index), "Document", 0, "passage", score, None)
                    for index, score in enumerate((.91, .70, .69, .2, float("nan")))]

    class Connection:
        def cursor(self): return Cursor()

    rows = pipeline.search_user_chunks(Connection(), 1, "lamp", top_k=5, min_similarity=.70)
    assert [row["similarity"] for row in rows] == [.91, .70]
    assert pipeline.search_user_chunks(Connection(), 1, "lamp", top_k=5, min_similarity=.95) == []
    with pytest.raises(ValueError):
        pipeline.search_user_chunks(Connection(), 1, "lamp", min_similarity=float("nan"))


def test_no_qualifying_rag_chunks_uses_source_only_and_records_cutoff(client, auth_headers, monkeypatch):
    post = client.post("/posts", headers=auth_headers, json={"title": "Lamp", "markdown": "A lamp with a white shade and a bronze base."}).json()

    @contextmanager
    def connection(*args): yield object()

    observed = []
    monkeypatch.setattr(llm_generation, "connect_rag_database", connection)
    monkeypatch.setattr(llm_generation, "ensure_studio_tables", lambda conn: None)
    monkeypatch.setattr(llm_generation, "search_user_chunks", lambda *args: observed.append(args[-1]) or [])

    class Fake:
        name = "fake"
        async def generate(self, **kwargs):
            assert "Retrieved background passages" not in kwargs["user_prompt"]
            assert "white shade" in kwargs["user_prompt"]
            return LLMResult(variants={"x": "A lamp with a white shade. Learn more."})

    app.dependency_overrides[get_llm_provider] = lambda: Fake()
    try:
        response = client.post(f"/posts/{post['id']}/variants", headers=auth_headers, json={
            "platforms": ["x"], "generation_mode": "llm", "call_to_action": "Learn more",
            "use_rag": True, "rag_min_similarity": .8})
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
    assert response.status_code == 201, response.text
    assert observed == [.8]
    run = client.get(f"/posts/{post['id']}/generations", headers=auth_headers).json()[0]
    assert run["rag_enabled"] and run["rag_context"] == [] and run["rag_min_similarity"] == .8


def test_selected_composed_image_is_frozen_and_delivered_with_text(client, auth_headers):
    post, variant = make_variant(client, auth_headers)
    visual_id = make_visual(post["id"])
    attached = client.post(f"/variants/{variant['id']}/image", headers=auth_headers, json={"visual_id": visual_id})
    assert attached.status_code == 200 and attached.json()["image_attached"]
    preview = client.get(f"/variants/{variant['id']}/image", headers=auth_headers)
    assert preview.status_code == 200
    assert client.get(f"/variants/{variant['id']}/image").status_code in (401, 403)
    assert client.post(f"/variants/{variant['id']}/approve", headers=auth_headers).status_code == 200
    schedule = client.post(f"/variants/{variant['id']}/schedule", headers=auth_headers, json={
        "publish_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}).json()
    assert schedule["image_attached"]
    assert client.delete(f"/variants/{variant['id']}/image", headers=auth_headers).status_code == 409
    assert client.patch(f"/posts/{post['id']}/visuals/{visual_id}", headers=auth_headers, json={
        "headline": "Changed", "call_to_action": "Changed"}).status_code == 409
    publisher = RecordingPublisher()
    app.dependency_overrides[get_publisher_registry] = lambda: PublisherRegistry({"x": publisher})
    try:
        response = client.post(f"/schedules/{schedule['id']}/publish", headers=auth_headers)
        duplicate = client.post(f"/schedules/{schedule['id']}/publish", headers=auth_headers)
    finally:
        app.dependency_overrides.pop(get_publisher_registry, None)
    assert response.status_code == 200 and response.json()["image_attached"]
    assert duplicate.json()["already_published"] and len(publisher.requests) == 1
    assert publisher.requests[0].content == variant["content"]
    assert publisher.requests[0].image.content == preview.content


def test_image_requirement_and_cross_platform_checks(client, auth_headers):
    post, variant = make_variant(client, auth_headers)
    with SessionLocal() as db:
        row = db.get(PlatformVariant, variant["id"]); row.image_required = True; db.commit()
    assert client.post(f"/variants/{variant['id']}/approve", headers=auth_headers).status_code == 422
    wrong_platform = make_visual(post["id"], "linkedin")
    assert client.post(f"/variants/{variant['id']}/image", headers=auth_headers,
                       json={"visual_id": wrong_platform}).status_code == 422
    another, _ = make_variant(client, auth_headers)
    wrong_campaign = make_visual(another["id"])
    assert client.post(f"/variants/{variant['id']}/image", headers=auth_headers,
                       json={"visual_id": wrong_campaign}).status_code == 422
    response = client.delete(f"/variants/{variant['id']}/image", headers=auth_headers)
    assert not response.json()["image_required"]
    assert client.post(f"/variants/{variant['id']}/approve", headers=auth_headers).status_code == 200


@pytest.mark.parametrize("problem", ["missing", "unsupported"])
def test_caption_edit_updates_draft_image_and_media_problems_never_send_text_only(client, auth_headers, problem):
    post, variant = make_variant(client, auth_headers)
    visual_id = make_visual(post["id"])
    client.post(f"/variants/{variant['id']}/image", headers=auth_headers, json={"visual_id": visual_id})
    before = client.get(f"/variants/{variant['id']}/image", headers=auth_headers).content
    client.patch(f"/posts/{post['id']}/visuals/{visual_id}", headers=auth_headers,
                 json={"headline": "A different headline", "call_to_action": "View details"})
    after = client.get(f"/variants/{variant['id']}/image", headers=auth_headers).content
    assert before != after
    client.post(f"/variants/{variant['id']}/approve", headers=auth_headers)
    schedule = client.post(f"/variants/{variant['id']}/schedule", headers=auth_headers, json={
        "publish_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}).json()
    with SessionLocal() as db:
        slot = db.get(ScheduleSlot, schedule["id"])
        if problem == "missing":
            storage_path(slot.image_key_snapshot).unlink()
    publisher = RecordingPublisher()
    if problem == "unsupported":
        publisher.supports_images = False
    app.dependency_overrides[get_publisher_registry] = lambda: PublisherRegistry({"x": publisher})
    try:
        response = client.post(f"/schedules/{schedule['id']}/publish", headers=auth_headers)
    finally:
        app.dependency_overrides.pop(get_publisher_registry, None)
    assert response.status_code == 502
    assert ("missing" if problem == "missing" else "cannot deliver images") in response.json()["detail"]
    assert publisher.requests == []


def test_discord_multipart_contains_text_and_png_and_uncertain_delivery_is_not_retried(client, auth_headers):
    captured = []

    def success(request):
        captured.append(request)
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'name="files[0]"' in request.content
        assert b'filename="post.png"' in request.content
        assert b'"content": "A product post"' in request.content
        assert b"PNGDATA" in request.content
        return httpx.Response(200, json={"id": "discord-message", "attachments": [{"id": "attachment"}]})

    publisher = DiscordPublisher("https://example.com/webhook", transport=httpx.MockTransport(success))
    request = PublishRequest("A product post", "key", PublishImage(b"PNGDATA", "post.png"))
    assert asyncio.run(publisher.publish(request)).external_post_id == "discord-message"
    assert len(captured) == 1

    post, variant = make_variant(client, auth_headers, "discord")
    client.post(f"/variants/{variant['id']}/approve", headers=auth_headers)
    schedule = client.post(f"/variants/{variant['id']}/schedule", headers=auth_headers,
        json={"publish_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}).json()
    with SessionLocal() as db:
        slot = db.get(ScheduleSlot, schedule["id"])
        slot.publish_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("Timed out after request sent", request=request)

    registry = PublisherRegistry({"discord": DiscordPublisher("https://example.com/webhook", transport=httpx.MockTransport(timeout))})
    asyncio.run(run_once(registry=registry))
    with SessionLocal() as db:
        assert db.get(ScheduleSlot, schedule["id"]).status == "failed"
        assert db.scalar(select(PublishAttempt)).status == "uncertain"
    assert not asyncio.run(run_once(registry=registry))
    assert len(calls) == 1
