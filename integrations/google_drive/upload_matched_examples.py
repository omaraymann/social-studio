"""Upload only the manifest-selected matched images and campaign documents."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re

from integrations.google_drive.client import connect_drive, upload_drive_file
from integrations.google_drive.files import (
    DriveUploadError, created_id, ensure_folder, find_named_item,
    result_text, save_progress,
)

DATASET = Path(__file__).resolve().parents[2] / "data/vision/matched_campaigns"


def selected_files(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    selected = []
    names = set()
    for group, suffix, mime in (("images", ".jpg", "image/jpeg"),
                                ("documents", ".txt", "text/plain")):
        for record in manifest[group]:
            relative = Path(record["path"])
            if (relative.is_absolute() or relative.parent != Path(group)
                    or relative.suffix != suffix or relative.name in names):
                raise ValueError(f"Invalid selected path: {relative}")
            path = directory / relative
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError(f"Checksum changed: {relative}")
            names.add(relative.name)
            selected.append({**record, "local_path": path, "group": group, "mime": mime})
    return selected


async def upload_matched_examples(directory=DATASET, *, dry_run=False):
    records = selected_files(directory)
    print(f"Selected: {sum(r['group'] == 'images' for r in records)} images, "
          f"{sum(r['group'] == 'documents' for r in records)} documents", flush=True)
    if dry_run:
        print("Dry run: checksums validated; no Google or model calls.")
        return

    state_path = directory / "drive_uploads.json"
    previous = json.loads(state_path.read_text()) if state_path.exists() else {"files": {}}
    state = {"files": dict(previous["files"]), "folders": {}, "paid_model_calls": 0}
    async with connect_drive(allow_uploads=True) as session:
        root = await ensure_folder(session, "Social Studio", "root")
        for group, parent_name in (("images", "Product Images"), ("documents", "Documents")):
            parent = await ensure_folder(session, parent_name, root)
            folder = await ensure_folder(session, "Matched Scenes", parent)
            state["folders"][group] = folder
        save_progress(state_path, state)
        for number, record in enumerate(records, 1):
            path = record["local_path"]
            folder = state["folders"][record["group"]]
            remote = await find_named_item(session, path.name, folder, record["mime"])
            prior = previous["files"].get(path.name)
            if remote and (not prior or prior["sha256"] != record["sha256"]
                           or prior["drive_file_id"] != remote):
                raise DriveUploadError(f"Cannot confirm existing contents of {path.name}; check Drive before retrying.")
            disposition = "Already present"
            if not remote:
                remote = created_id(await upload_drive_file(session, path, folder))
                disposition = "Uploaded"
            state["files"][path.name] = {"drive_file_id": remote, "sha256": record["sha256"],
                                        "group": record["group"], "scene_id": record.get("scene_id")}
            save_progress(state_path, state)
            print(f"[{number}/{len(records)}] {disposition}: {path.name}", flush=True)
        for group, folder in state["folders"].items():
            listing = result_text(await session.call_tool("listFolder", arguments={"folderId": folder, "pageSize": 100}))
            actual = set(re.findall(r"\(ID: ([A-Za-z0-9_-]+)\)", listing))
            expected = {item["drive_file_id"] for item in state["files"].values() if item["group"] == group}
            if actual != expected:
                raise DriveUploadError(f"Folder verification failed for {group}; check Drive before retrying.")
            print(f"Verified {len(expected)} {group}: https://drive.google.com/drive/folders/{folder}", flush=True)
        state["verified"] = True
        save_progress(state_path, state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(upload_matched_examples(dry_run=args.dry_run))
