import base64
import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.database import SessionLocal
from app.llm.base import GenerationSettings, LLMError, LLMResult
from app.llm.openrouter import OpenRouterProvider
from app.llm.registry import get_llm_provider
from app.llm.vision import create_visual_brief, generate_image
from app.main import app
from app.models import GenerationRun
from app.services import campaign_visuals
from app.services.image_storage import decode_image, save_reference, storage_path


def photo_bytes(size=(2000, 1000), mode="RGB", format="PNG"):
    stream = io.BytesIO()
    Image.new(mode, size, "blue").save(stream, format=format)
    return stream.getvalue()


@pytest.fixture(autouse=True)
def local_image_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "image_storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")


def source(client, headers):
    return client.post("/posts", headers=headers, json={
        "title": "Blue reading chair", "markdown": "A blue chair for your reading corner. See the product details."
    }).json()["id"]


def attach(client, headers, post_id):
    response = client.post(f"/posts/{post_id}/images", headers=headers,
                           files={"file": ("chair.png", photo_bytes(), "image/png")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_preparation_keeps_original_resizes_without_cropping_and_removes_metadata():
    original = photo_bytes()
    result = save_reference(original)
    assert (result["width"], result["height"]) == (1600, 800)
    assert storage_path(result["original_key"]).read_bytes() == original
    with Image.open(storage_path(result["prepared_key"])) as prepared:
        assert not prepared.getexif()
    with pytest.raises(ValueError):
        decode_image(b"this is not a photo")
    with pytest.raises(ValueError, match="JPEG, PNG, or WebP"):
        decode_image(photo_bytes(size=(50, 50), format="GIF"))
    with pytest.raises(ValueError):
        storage_path("../../private")


def test_orientation_and_transparency_are_preserved():
    image = Image.new("RGB", (80, 40), "red")
    exif = Image.Exif(); exif[274] = 6
    stream = io.BytesIO(); image.save(stream, "JPEG", exif=exif)
    assert decode_image(stream.getvalue()).size == (40, 80)
    result = save_reference(photo_bytes(size=(50, 50), mode="RGBA"))
    with Image.open(storage_path(result["prepared_key"])) as prepared:
        assert prepared.mode == "RGBA"


def test_private_upload_and_reference_validation(client, auth_headers):
    post_id = source(client, auth_headers)
    image_id = attach(client, auth_headers, post_id)
    response = client.get(f"/posts/{post_id}/images/{image_id}/file", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert client.get(f"/posts/{post_id}/images/{image_id}/file").status_code in (401, 403)
    client.post("/auth/signup", json={"email": "other@example.com", "password": "password123"})
    token = client.post("/auth/login", json={"email": "other@example.com", "password": "password123"}).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/posts/{post_id}/images", headers=other).status_code == 404
    assert client.get(f"/posts/{post_id}/images/{image_id}/file", headers=other).status_code == 404
    assert client.post(f"/posts/{post_id}/visuals", headers=other,
                       json={"reference_image_ids": [image_id]}).status_code == 404
    other_post = source(client, auth_headers)
    assert client.post(f"/posts/{other_post}/visuals", headers=auth_headers,
                       json={"reference_image_ids": [image_id]}).status_code == 422
    assert client.post(f"/posts/{post_id}/images", headers=auth_headers,
                       files={"file": ("fake.png", b"not an image", "image/png")}).status_code == 422


class VisionFake:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResult(variants={"x": "A blue chair for a reading corner. Learn more."})


def test_campaign_text_receives_selected_images_and_selected_model(client, auth_headers):
    post_id = source(client, auth_headers)
    image_id = attach(client, auth_headers, post_id)
    fake = VisionFake()
    app.dependency_overrides[get_llm_provider] = lambda: fake
    try:
        response = client.post(f"/posts/{post_id}/variants", headers=auth_headers, json={
            "platforms": ["x"], "generation_mode": "llm", "call_to_action": "Learn more",
            "vision_model": "google/gemini-2.5-pro", "reference_image_ids": [image_id],
        })
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
    assert response.status_code == 201, response.text
    assert fake.calls[0]["settings"].model == "google/gemini-2.5-pro"
    assert fake.calls[0]["images"][0].startswith("data:image/png;base64,")
    runs = client.get(f"/posts/{post_id}/generations", headers=auth_headers).json()
    assert runs[0]["reference_image_ids"] == [image_id]
    assert runs[0]["model"] == "google/gemini-2.5-pro"


def test_visual_pipeline_saves_snapshot_result_usage_and_editable_text(client, auth_headers, monkeypatch):
    post_id = source(client, auth_headers)
    image_id = attach(client, auth_headers, post_id)
    fake = VisionFake()
    app.dependency_overrides[get_llm_provider] = lambda: fake
    try:
        client.post(f"/posts/{post_id}/variants", headers=auth_headers, json={
            "platforms": ["x"], "generation_mode": "llm", "call_to_action": "Learn more"})
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
    with SessionLocal() as db:
        run = db.query(GenerationRun).filter_by(source_post_id=post_id).one()
        run.rag_context = [{"title": "Product guide", "text": "The chair is available in blue."}]
        db.commit()

    async def brief(model, context, images):
        assert "Product guide" in context
        assert "Blue reading chair" in context
        assert len(images) == 1
        return {"headline": "Your reading corner", "call_to_action": "Learn more", "visual_brief": "Blue chair on a warm backdrop"}, {"cost": 0.001}

    async def image(model, prompt, images, ratio):
        assert model == "google/gemini-3.1-flash-image"
        assert ratio == "16:9"
        assert "Do not add lettering" in prompt
        return photo_bytes(size=(1280, 720)), {"cost": 0.04}

    monkeypatch.setattr(campaign_visuals, "create_visual_brief", brief)
    monkeypatch.setattr(campaign_visuals, "generate_image", image)
    response = client.post(f"/posts/{post_id}/visuals", headers=auth_headers, json={
        "reference_image_ids": [image_id], "image_model": "google/gemini-3.1-flash-image", "aspect_ratio": "16:9"})
    assert response.status_code == 202, response.text
    row = client.get(f"/posts/{post_id}/visuals", headers=auth_headers).json()[0]
    assert row["status"] == "succeeded"
    assert row["usage"]["generation"]["cost"] == 0.04
    assert row["context"]["retrieved_passages"][0]["title"] == "Product guide"
    assert "image_key" not in row
    assert client.get(f"/posts/{post_id}/visuals/{row['id']}/file", headers=auth_headers).status_code == 200
    edit = client.patch(f"/posts/{post_id}/visuals/{row['id']}", headers=auth_headers,
                        json={"headline": "A comfortable corner", "call_to_action": "View the chair"})
    assert edit.status_code == 200
    assert client.get(f"/posts/{post_id}/visuals", headers=auth_headers).json()[0]["headline"] == "A comfortable corner"


def test_failed_visual_keeps_planning_cost_and_can_be_retried(client, auth_headers, monkeypatch):
    post_id = source(client, auth_headers)
    image_id = attach(client, auth_headers, post_id)

    async def brief(*args):
        return {"headline": "Chair", "call_to_action": "Learn more", "visual_brief": "Studio scene"}, {"cost": 0.001}

    async def fail(*args):
        raise LLMError("OpenRouter credits are insufficient")

    monkeypatch.setattr(campaign_visuals, "create_visual_brief", brief)
    monkeypatch.setattr(campaign_visuals, "generate_image", fail)
    payload = {"reference_image_ids": [image_id]}
    assert client.post(f"/posts/{post_id}/visuals", headers=auth_headers, json=payload).status_code == 202
    row = client.get(f"/posts/{post_id}/visuals", headers=auth_headers).json()[0]
    assert row["status"] == "failed" and "credits" in row["error_message"]
    assert row["usage"]["planning"]["cost"] == 0.001
    assert "generation" not in row["usage"]
    assert client.post(f"/posts/{post_id}/visuals", headers=auth_headers, json=payload).status_code == 202


def test_model_reference_limit_rejected_before_paid_planning(client, auth_headers, monkeypatch):
    post_id = source(client, auth_headers)
    image_ids = [attach(client, auth_headers, post_id) for _ in range(4)]

    async def unexpected_call(*args):
        pytest.fail("Invalid references must be rejected before a paid planning call")

    monkeypatch.setattr(campaign_visuals, "create_visual_brief", unexpected_call)
    response = client.post(f"/posts/{post_id}/visuals", headers=auth_headers, json={
        "reference_image_ids": image_ids, "image_model": "google/gemini-2.5-flash-image"})
    assert response.status_code == 422
    assert "at most 3" in response.json()["detail"]
    assert client.get(f"/posts/{post_id}/visuals", headers=auth_headers).json() == []
    catalogue = client.get("/visual-models", headers=auth_headers).json()
    limits = {model["id"]: model["max_reference_images"] for model in catalogue["image"]}
    assert limits["google/gemini-2.5-flash-image"] == 3
    assert limits["google/gemini-3.1-flash-image"] == 4


async def _exercise_openrouter_contract():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.path.endswith("/images"):
            assert payload["input_references"][0]["image_url"]["url"].startswith("data:")
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(photo_bytes(size=(20, 20))).decode()}], "usage": {"cost": 0.04}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "headline": "Chair", "call_to_action": "Explore", "visual_brief": "Warm studio"})}}]})

    transport = httpx.MockTransport(handler)
    brief, _ = await create_visual_brief("google/gemini-2.5-flash", "source", ["data:image/png;base64,eA=="], transport)
    assert brief["headline"] == "Chair"
    assert requests[0]["messages"][1]["content"][1]["type"] == "image_url"
    raw, usage = await generate_image("google/gemini-2.5-flash-image", "prompt", ["data:image/png;base64,eA=="], "1:1", transport)
    assert decode_image(raw).size == (20, 20) and usage["cost"] == 0.04

    def text_handler(request):
        payload = json.loads(request.content)
        assert payload["messages"][1]["content"][1]["type"] == "image_url"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"x":"Chair"}'}}]})

    provider = OpenRouterProvider("test", "https://example.com", 10, httpx.MockTransport(text_handler))
    result = await provider.generate(system_prompt="system", user_prompt="source", platforms=["x"],
                                     settings=GenerationSettings("google/gemini-2.5-flash", .3, .9, None, 100),
                                     images=["data:image/png;base64,eA=="])
    assert result.variants == {"x": "Chair"}


def test_openrouter_multimodal_and_image_api_contract():
    asyncio.run(_exercise_openrouter_contract())


@pytest.mark.parametrize("with_photos", [True, False])
def test_combined_campaign_plans_platform_images_once_and_keeps_drafts_on_image_failure(client, auth_headers, monkeypatch, with_photos):
    post_id = source(client, auth_headers)
    image_id = attach(client, auth_headers, post_id) if with_photos else None

    class CombinedFake:
        name = "fake"
        calls = []

        async def generate(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResult(
                variants={"x": "A blue chair for your reading corner. Learn more.",
                          "linkedin": "Bring a blue accent to your reading corner. Explore this chair. Learn more."},
                visual_briefs={platform: {"headline": "Your reading corner", "call_to_action": "Learn more",
                                         "visual_brief": f"A blue chair in a {platform} composition"}
                               for platform in ("x", "linkedin")},
                cost_usd=0.002,
            )

    async def unnecessary_planning(*args):
        pytest.fail("Automatic visuals should use the plans from the campaign call")

    image_calls = []

    async def image(model, prompt, images, ratio):
        image_calls.append((model, prompt, images, ratio))
        if ratio == "16:9":
            raise LLMError("Temporary image provider failure")
        return photo_bytes(size=(1024, 1024)), {"cost": 0.039}

    fake = CombinedFake()
    monkeypatch.setattr(campaign_visuals, "create_visual_brief", unnecessary_planning)
    monkeypatch.setattr(campaign_visuals, "generate_image", image)
    app.dependency_overrides[get_llm_provider] = lambda: fake
    try:
        response = client.post(f"/posts/{post_id}/variants", headers=auth_headers, json={
            "platforms": ["x", "linkedin"], "generation_mode": "llm", "call_to_action": "Learn more",
            "generate_images": True, "reference_image_ids": [image_id] if with_photos else [], "image_style": "lifestyle scene",
        })
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
    assert response.status_code == 201, response.text
    assert len(fake.calls) == 1
    assert fake.calls[0]["visual_platforms"] == ["x", "linkedin"]
    assert "16:9" in fake.calls[0]["system_prompt"] and "1:1" in fake.calls[0]["system_prompt"]
    assert fake.calls[0]["settings"].max_output_tokens >= 3000
    assert len(image_calls) == 2
    assert all(call[0] == "google/gemini-2.5-flash-image" and len(call[2]) == int(with_photos) for call in image_calls)
    if not with_photos:
        assert all("topic illustration" in call[1] for call in image_calls)
    visuals = client.get(f"/posts/{post_id}/visuals", headers=auth_headers).json()
    by_platform = {row["platform"]: row for row in visuals}
    assert by_platform["x"]["status"] == "failed"
    assert by_platform["linkedin"]["status"] == "succeeded"
    assert by_platform["x"]["context"]["drafts"].keys() == {"x"}
    assert by_platform["linkedin"]["aspect_ratio"] == "1:1"
    assert by_platform["linkedin"]["usage"]["planning"]["included_in_generation_run"]
    variants = client.get(f"/posts/{post_id}/variants", headers=auth_headers).json()
    assert len(variants) == 2
    by_post_platform = {row["platform"]: row for row in variants}
    assert by_post_platform["linkedin"]["image_attached"]
    assert by_post_platform["linkedin"]["selected_visual_id"] == by_platform["linkedin"]["id"]
    assert by_post_platform["x"]["image_required"] and not by_post_platform["x"]["image_attached"]


def test_combined_campaign_rejects_too_many_photos_before_llm(client, auth_headers):
    post_id = source(client, auth_headers)
    fake = VisionFake()
    app.dependency_overrides[get_llm_provider] = lambda: fake
    try:
        response = client.post(f"/posts/{post_id}/variants", headers=auth_headers, json={
            "platforms": ["x"], "generation_mode": "llm", "generate_images": True,
            "reference_image_ids": [1, 2, 3, 4],
        })
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
    assert response.status_code == 422
    assert "at most 3" in response.json()["detail"]
    assert fake.calls == []


def test_invalid_combined_plan_saves_no_partial_drafts_or_images(client, auth_headers):
    post_id = source(client, auth_headers)

    class MissingPlans:
        name = "fake"

        async def generate(self, **kwargs):
            return LLMResult(variants={"x": "A blue chair. Learn more."}, cost_usd=0.002)

    app.dependency_overrides[get_llm_provider] = lambda: MissingPlans()
    try:
        response = client.post(f"/posts/{post_id}/variants", headers=auth_headers, json={
            "platforms": ["x"], "generation_mode": "llm", "generate_images": True, "call_to_action": "Learn more"})
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)
    assert response.status_code == 502
    assert client.get(f"/posts/{post_id}/variants", headers=auth_headers).json() == []
    assert client.get(f"/posts/{post_id}/visuals", headers=auth_headers).json() == []
    run = client.get(f"/posts/{post_id}/generations", headers=auth_headers).json()[0]
    assert run["status"] == "failed" and run["estimated_cost_usd"] == 0.002


def test_openrouter_combined_response_schema_and_parsing():
    async def exercise():
        def handler(request):
            payload = json.loads(request.content)
            schema = payload["response_format"]["json_schema"]["schema"]
            assert set(schema["required"]) == {"x", "visuals"}
            assert "x" in schema["properties"]["visuals"]["properties"]
            content = {"x": "Blue chair. Learn more.", "visuals": {"x": {
                "headline": "Reading corner", "call_to_action": "Learn more", "visual_brief": "A blue chair"}}}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})

        provider = OpenRouterProvider("test", "https://example.com", 10, httpx.MockTransport(handler))
        result = await provider.generate(system_prompt="system", user_prompt="source", platforms=["x"],
            visual_platforms=["x"], settings=GenerationSettings("google/gemini-2.5-flash", .3, .9, None, 2000))
        assert result.variants == {"x": "Blue chair. Learn more."}
        assert result.visual_briefs["x"]["headline"] == "Reading corner"
    asyncio.run(exercise())
