import asyncio
import json
from contextlib import asynccontextmanager

from integrations.google_drive import upload_documents as documents
from test_upload_dataset import FakeSession


def test_document_upload_resume_and_no_labels(tmp_path, monkeypatch):
    path = tmp_path / "source.txt"
    path.write_text("Attributed campaign source")
    import hashlib
    selected = [{"name": path.name, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                 "source_url": "https://example.org/source"}]
    monkeypatch.setattr(documents, "STAGING_DIRECTORY", tmp_path)
    monkeypatch.setattr(documents, "prepare_documents", lambda: selected)
    session = FakeSession()
    original = session.call_tool

    async def call(name, arguments):
        result = await original(name, arguments)
        if name == "uploadFile":
            session.items[-1]["mime"] = "text/plain"
        return result

    session.call_tool = call

    @asynccontextmanager
    async def connect(**options):
        assert options["allow_uploads"]
        yield session

    monkeypatch.setattr(documents, "connect_drive", connect)
    asyncio.run(documents.upload_documents())
    asyncio.run(documents.upload_documents())
    assert session.upload_count == 1
    state = json.loads((tmp_path / "drive_uploads.json").read_text())
    assert set(state["files"]) == {"source.txt"}
    assert state["paid_model_calls"] == 0


def test_prepare_documents_retains_source_and_fictional_note(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "source.txt").write_text("Original campaign evidence")
    record = {"title": "Example", "metadata": {"local_text_file": "source.txt", "source_url": "https://example.org/source",
              "publisher": "Example publisher", "reuse_notes": "Retain attribution"}}
    (corpus / "corpus.jsonl").write_text(json.dumps(record) + "\n")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "lamp_product_brief.txt").write_text("Luna lamp test brief")
    stage = tmp_path / "staging"
    monkeypatch.setattr(documents, "CORPUS_DIRECTORY", corpus)
    monkeypatch.setattr(documents, "STAGING_DIRECTORY", stage)
    monkeypatch.setattr(documents.Path, "home", lambda: tmp_path)
    prepared = documents.prepare_documents()
    assert len(prepared) == 2
    assert "Source: https://example.org/source" in (stage / "source.txt").read_text()
    assert "Original campaign evidence" in (stage / "source.txt").read_text()
    assert (stage / "lamp_product_brief.txt").read_text().startswith("Fictional product brief")
