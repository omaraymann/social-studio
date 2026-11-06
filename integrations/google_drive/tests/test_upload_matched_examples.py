"""Offline checks for the matched-scene upload boundary and restart behavior."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path

import pytest

from integrations.google_drive import upload_matched_examples as uploader
from test_upload_dataset import FakeSession, response


@pytest.fixture
def matched_dataset(tmp_path):
    manifest = {"images": [], "documents": []}
    for group, name in (("images", "SC01_photo.jpg"), ("documents", "SC01_brief.txt")):
        (tmp_path / group).mkdir()
        path = tmp_path / group / name
        path.write_bytes(name.encode())
        manifest[group].append({"path": f"{group}/{name}", "scene_id": "SC01",
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "reference_annotations.json").write_text("DO NOT UPLOAD")
    return tmp_path


def test_upload_only_selected_files_and_resume(matched_dataset, monkeypatch):
    class MatchedSession(FakeSession):
        async def call_tool(self, name, arguments):
            if name == "listFolder":
                items = [i for i in self.items if i["parent"] == arguments["folderId"]]
                return response("\n".join(f"{i['name']} (ID: {i['id']})" for i in items))
            result = await super().call_tool(name, arguments)
            if name == "uploadFile" and Path(arguments["name"]).suffix == ".txt":
                self.items[-1]["mime"] = "text/plain"
            return result

    session = MatchedSession()

    @asynccontextmanager
    async def connect(**options):
        assert options == {"allow_uploads": True}
        yield session

    monkeypatch.setattr(uploader, "connect_drive", connect)
    asyncio.run(uploader.upload_matched_examples(matched_dataset))
    asyncio.run(uploader.upload_matched_examples(matched_dataset))
    state = json.loads((matched_dataset / "drive_uploads.json").read_text())
    assert session.upload_count == 2
    assert len(state["files"]) == 2
    assert state["folders"]["images"] != state["folders"]["documents"]
    assert state["verified"] is True
    assert state["paid_model_calls"] == 0


def test_changed_document_is_rejected_before_connecting(matched_dataset, monkeypatch):
    (matched_dataset / "documents/SC01_brief.txt").write_text("changed")
    monkeypatch.setattr(uploader, "connect_drive", lambda **kw: pytest.fail("Unexpected connection"))
    with pytest.raises(ValueError, match="Checksum changed"):
        asyncio.run(uploader.upload_matched_examples(matched_dataset))


def test_dry_run_makes_no_connection(matched_dataset, monkeypatch):
    monkeypatch.setattr(uploader, "connect_drive", lambda **kw: pytest.fail("Unexpected connection"))
    asyncio.run(uploader.upload_matched_examples(matched_dataset, dry_run=True))
    assert not (matched_dataset / "drive_uploads.json").exists()
