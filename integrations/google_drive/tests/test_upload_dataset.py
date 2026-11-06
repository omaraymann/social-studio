"""Offline checks for uploads and restart behaviour; never contact Google."""

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from integrations.google_drive import upload_dataset as uploader
from integrations.google_drive.files import FOLDER_MIME


def response(text, error=False):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], is_error=error)


class FakeSession:
    def __init__(self):
        self.items = []
        self.upload_count = 0

    async def call_tool(self, name, arguments):
        if name == "search":
            query = arguments["query"]
            filename = re.search(r"name = '([^']+)'", query).group(1)
            parent = re.search(r"'([^']+)' in parents", query).group(1)
            mime = re.search(r"mimeType = '([^']+)'", query).group(1)
            found = [item for item in self.items if item["name"] == filename
                     and item["parent"] == parent and item["mime"] == mime]
            lines = [f"{item['name']} ({mime}) [id: {item['id']}, path: demo]" for item in found]
            return response(f"Found {len(found)} files (ordered by modifiedTime desc):\n" + "\n".join(lines))
        if name == "createFolder":
            item = {"id": f"folder_{len(self.items)}", "name": arguments["name"],
                    "parent": arguments["parent"], "mime": FOLDER_MIME}
        elif name == "uploadFile":
            self.upload_count += 1
            item = {"id": f"file_{len(self.items)}", "name": arguments["name"],
                    "parent": arguments["parentFolderId"], "mime": "image/jpeg"}
        else:
            raise AssertionError(f"Unexpected tool: {name}")
        self.items.append(item)
        return response(f"Created: {item['name']}\nID: {item['id']}")


@pytest.fixture
def dataset(tmp_path):
    (tmp_path / "images").mkdir()
    items = []
    for number, split in enumerate(("development", "test")):
        raw = f"fixture-{number}".encode()
        path = tmp_path / "images" / f"image_{number}.jpg"
        path.write_bytes(raw)
        items.append({"image_id": f"image_{number}", "path": f"images/{path.name}",
                      "sha256": hashlib.sha256(raw).hexdigest(), "pilot_split": split})
    # This excluded file must not be uploaded just because it exists locally.
    (tmp_path / "images" / "not_selected.jpg").write_bytes(b"excluded")
    (tmp_path / "manifest.json").write_text(json.dumps({"images": items}))
    return tmp_path


def test_resume_skips_existing_files_and_keeps_split_folders(dataset, monkeypatch):
    session = FakeSession()

    @asynccontextmanager
    async def connect(**options):
        assert options == {"allow_uploads": True}
        yield session

    monkeypatch.setattr(uploader, "connect_drive", connect)
    asyncio.run(uploader.upload_dataset(dataset))
    asyncio.run(uploader.upload_dataset(dataset))
    assert session.upload_count == 2
    state = json.loads((dataset / "drive_uploads.json").read_text())
    assert len(state["files"]) == 2
    assert state["files"]["image_0"]["parent_id"] != state["files"]["image_1"]["parent_id"]
    assert state["paid_model_calls"] == 0


def test_dry_run_never_connects(dataset, monkeypatch):
    def unexpected(**options):
        raise AssertionError("Dry run must not connect")
    monkeypatch.setattr(uploader, "connect_drive", unexpected)
    asyncio.run(uploader.upload_dataset(dataset, dry_run=True))
    assert not (dataset / "drive_uploads.json").exists()


def test_changed_image_stops_before_upload(dataset):
    (dataset / "images/image_0.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Checksum changed"):
        uploader.dataset_images(dataset)


def test_mcp_error_is_not_treated_as_success():
    with pytest.raises(uploader.DriveUploadError, match="permission"):
        uploader.created_id(response("Missing permission", error=True))


def test_unrecognized_search_response_stops_instead_of_creating_duplicates():
    class Unexpected:
        async def call_tool(self, *args, **kwargs):
            return response("A new response format")
    with pytest.raises(uploader.DriveUploadError, match="Unexpected search response"):
        asyncio.run(uploader.find_named_item(Unexpected(), "image.jpg", "parent", "image/jpeg"))


def test_duplicate_names_require_resolution():
    class Duplicates:
        async def call_tool(self, *args, **kwargs):
            return response("Found 2 files:\nimage.jpg [id: file_one, path: demo]\nimage.jpg [id: file_two, path: demo]")
    with pytest.raises(uploader.DriveUploadError, match="Multiple files"):
        asyncio.run(uploader.find_named_item(Duplicates(), "image.jpg", "parent", "image/jpeg"))
