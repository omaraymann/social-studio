import hashlib
import io
from urllib.parse import urlsplit

import pytest
from PIL import Image

from app.config import settings
from app.database import SessionLocal
from app.models import PlatformVariant
from app.services import instagram_media
from app.services.image_storage import storage_path


@pytest.fixture
def media_setup(client, auth_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "image_storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "public_media_base_url", "https://media.example.test")
    post = client.post("/posts", headers=auth_headers, json={
        "title": "Lamp", "markdown": "A lamp with a white shade."}).json()
    variant = client.post(f"/posts/{post['id']}/variants", headers=auth_headers,
                          json={"platforms": ["x"]}).json()[0]
    stream = io.BytesIO()
    Image.new("RGB", (640, 640), "blue").save(stream, "PNG")
    raw = stream.getvalue()
    key = "posts/selected.png"
    path = storage_path(key)
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    with SessionLocal() as db:
        row = db.get(PlatformVariant, variant["id"])
        row.selected_image_key = key
        row.selected_image_sha256 = hashlib.sha256(raw).hexdigest()
        db.commit()
    return variant["id"], raw, key


def make_link(client, headers, variant_id):
    response = client.post(f"/variants/{variant_id}/image-link", headers=headers)
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["image_url"])
    return url.path + "?" + url.query


def test_signed_link_serves_only_jpeg_and_preserves_original(client, auth_headers, media_setup):
    variant_id, original, key = media_setup
    url = make_link(client, auth_headers, variant_id)
    fetched = client.get(url)  # Instagram fetches without our application's login token.
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(fetched.content)) as image:
        assert image.format == "JPEG" and image.size == (640, 640)
    assert storage_path(key).read_bytes() == original
    assert client.get(url.split("?")[0]).status_code == 422
    assert client.post(f"/variants/{variant_id}/image-link").status_code in (401, 403)


def test_other_user_cannot_create_link(client, auth_headers, media_setup):
    variant_id, _, _ = media_setup
    credentials = {"email": "other@example.com", "password": "password123"}
    client.post("/auth/signup", json=credentials)
    token = client.post("/auth/login", json=credentials).json()["access_token"]
    response = client.post(f"/variants/{variant_id}/image-link",
                           headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_modified_and_expired_links_fail(client, auth_headers, media_setup, monkeypatch):
    variant_id, _, _ = media_setup
    now = int(instagram_media.time.time())
    monkeypatch.setattr(instagram_media.time, "time", lambda: now)
    url = make_link(client, auth_headers, variant_id)
    assert client.get(url[:-1] + ("0" if url[-1] != "0" else "1")).status_code == 404
    monkeypatch.setattr(instagram_media.time, "time", lambda: now + 901)
    assert client.get(url).status_code == 404


def test_missing_origin_and_changed_source_fail(client, auth_headers, media_setup, monkeypatch):
    variant_id, _, key = media_setup
    monkeypatch.setattr(settings, "public_media_base_url", None)
    assert client.post(f"/variants/{variant_id}/image-link", headers=auth_headers).status_code == 422
    monkeypatch.setattr(settings, "public_media_base_url", "https://media.example.test")
    storage_path(key).write_bytes(b"changed")
    response = client.post(f"/variants/{variant_id}/image-link", headers=auth_headers)
    assert response.status_code == 422 and "changed" in response.json()["detail"]


def test_export_changes_fail_integrity_check(client, auth_headers, media_setup):
    variant_id, _, _ = media_setup
    url = make_link(client, auth_headers, variant_id)
    image_id = urlsplit(url).path.rsplit("/", 1)[-1].removesuffix(".jpg")
    storage_path(f"instagram_exports/{image_id}.jpg").write_bytes(b"changed")
    assert client.get(url).status_code == 404


def test_tunnel_gateway_exposes_only_signed_media(client, auth_headers, media_setup):
    from fastapi.testclient import TestClient
    from app.media_gateway import app as gateway
    variant_id, _, _ = media_setup
    url = make_link(client, auth_headers, variant_id)
    with TestClient(gateway) as public:
        assert public.get(url).status_code == 200
        for path in ("/", "/docs", "/openapi.json", "/health", "/posts", "/auth/login", "/.env"):
            assert public.get(path).status_code == 404
        assert public.post(f"/variants/{variant_id}/image-link", headers=auth_headers).status_code == 404
