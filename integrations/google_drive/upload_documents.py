"""Prepare attributed campaign-source copies and upload them to Drive via MCP.

Only the explicit campaign corpus and the fictional lamp brief are selected.
This does not upload SciFact, code, evaluation labels, credentials or .env files.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from integrations.google_drive.client import connect_drive, upload_drive_file
from integrations.google_drive.files import (
    DriveUploadError, created_id, ensure_folder, find_named_item, save_progress,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIRECTORY = PROJECT_ROOT / "rag/data/raw/social_studio_eval"
STAGING_DIRECTORY = PROJECT_ROOT / "data/drive_documents"


def prepare_documents():
    STAGING_DIRECTORY.mkdir(parents=True, exist_ok=True)
    documents, names = [], set()
    for line in (CORPUS_DIRECTORY / "corpus.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        name = record["metadata"]["local_text_file"]
        if Path(name).name != name or not name.endswith(".txt") or name in names:
            raise ValueError("Invalid or duplicate source filename")
        names.add(name)
        text = (CORPUS_DIRECTORY / name).read_text()
        meta = record["metadata"]
        header = (f"Title: {record['title']}\n"
                  f"Publisher: {meta.get('publisher', 'See original source')}\n"
                  f"Source: {meta['source_url']}\n"
                  f"Originally retrieved: {meta.get('retrieved_date', 'Not recorded')}\n"
                  f"Reuse notes: {meta.get('reuse_notes', 'See original source')}\n"
                  "Purpose: private campaign-source research copy; source is historical, not current product availability.\n\n---\n\n")
        path = STAGING_DIRECTORY / name
        path.write_text(header + text)
        documents.append({"name": name, "path": str(path), "source_url": meta["source_url"],
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    lamp = Path.home() / "Desktop/lamp_product_brief.txt"
    if lamp.is_file():
        path = STAGING_DIRECTORY / lamp.name
        path.write_text("Fictional product brief for Social Studio testing. Not a real product listing.\n\n" + lamp.read_text())
        documents.append({"name": path.name, "path": str(path), "source_url": None,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (STAGING_DIRECTORY / "manifest.json").write_text(json.dumps(documents, indent=2))
    return documents


async def upload_documents(*, dry_run=False):
    documents = prepare_documents()
    print(f"Selected campaign documents: {len(documents)}", flush=True)
    if dry_run:
        for document in documents:
            print(document["name"])
        print("Dry run: local preparation only; no Google or model calls.")
        return

    state_path = STAGING_DIRECTORY / "drive_uploads.json"
    state = {"files": {}, "paid_model_calls": 0}
    async with connect_drive(allow_uploads=True) as session:
        root_id = await ensure_folder(session, "Social Studio", "root")
        folder_id = await ensure_folder(session, "Documents", root_id)
        state["folder_id"] = folder_id
        state["folder_url"] = f"https://drive.google.com/drive/folders/{folder_id}"
        save_progress(state_path, state)
        for number, document in enumerate(documents, 1):
            path = Path(document["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
                raise ValueError(f"Local document changed: {path.name}")
            remote_id = await find_named_item(session, path.name, folder_id, "text/plain")
            disposition = "Already present"
            if not remote_id:
                remote_id = created_id(await upload_drive_file(session, path, folder_id))
                disposition = "Uploaded"
                verified = await find_named_item(session, path.name, folder_id, "text/plain")
                if verified != remote_id:
                    raise DriveUploadError(f"Could not verify {path.name}; check Drive before retrying.")
            state["files"][path.name] = {"drive_file_id": remote_id, "source_sha256": document["sha256"],
                                        "source_url": document["source_url"]}
            save_progress(state_path, state)
            print(f"[{number}/{len(documents)}] {disposition}: {path.name}", flush=True)
        print(f"Finished: {state['folder_url']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(upload_documents(dry_run=args.dry_run))
    except (DriveUploadError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
