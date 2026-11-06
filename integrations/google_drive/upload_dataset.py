"""Upload only the selected pilot images through MCP; no LLM calls.

Run from the project root:
    python -m integrations.google_drive.upload_dataset --dry-run
    python -m integrations.google_drive.upload_dataset
"""

import argparse
import asyncio
import hashlib
import json
import mimetypes
from pathlib import Path

from integrations.google_drive.client import (
    connect_drive, upload_drive_file,
)

from integrations.google_drive.files import (
    DriveUploadError, created_id, ensure_folder, find_named_item,
    result_text, save_progress,
)

DATASET_DIRECTORY = Path(__file__).resolve().parents[2] / "data/vision/detection_pilot"



def dataset_images(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    images, seen = [], set()
    for item in manifest["images"]:
        path = (directory / item["path"]).resolve(strict=True)
        if not path.is_relative_to((directory / "images").resolve()):
            raise ValueError("Only files in the selected images directory may be uploaded")
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("Only selected image files may be uploaded")
        if item["image_id"] in seen:
            raise ValueError("Duplicate image ID in manifest")
        seen.add(item["image_id"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Checksum changed: {path.name}")
        if item["pilot_split"] not in {"development", "test"}:
            raise ValueError("Unexpected dataset split")
        images.append((item, path))
    return images


async def upload_dataset(directory=DATASET_DIRECTORY, *, dry_run=False):
    images = dataset_images(directory)
    print(f"Selected: {len(images)} images. No annotations, previews or excluded images will be uploaded.", flush=True)
    if dry_run:
        for item, path in images:
            print(f"{item['pilot_split']}/{path.name}")
        print("Dry run: no connection, uploads, or paid model calls.")
        return

    # A fresh remote name lookup on each run also handles a previous interrupted upload.
    # This code never calls a sharing tool; existing parent permissions still apply.
    state_path = directory / "drive_uploads.json"
    state = {"files": {}, "paid_model_calls": 0}
    async with connect_drive(allow_uploads=True) as session:
        root_id = await ensure_folder(session, "Social Studio", "root")
        pictures_id = await ensure_folder(session, "Product Images", root_id)
        pilot_id = await ensure_folder(session, "Detection Pilot", pictures_id)
        split_ids = {name: await ensure_folder(session, name, pilot_id)
                     for name in ("development", "test")}
        state["folder_id"] = pilot_id
        state["folder_url"] = f"https://drive.google.com/drive/folders/{pilot_id}"
        save_progress(state_path, state)
        for number, (item, path) in enumerate(images, 1):
            parent_id = split_ids[item["pilot_split"]]
            mime_type = mimetypes.guess_type(path.name)[0]
            remote_id = await find_named_item(session, path.name, parent_id, mime_type)
            disposition = "Already present"
            if not remote_id:
                remote_id = created_id(await upload_drive_file(session, path, parent_id))
                disposition = "Uploaded"
                verified = await find_named_item(session, path.name, parent_id, mime_type)
                if verified != remote_id:
                    raise DriveUploadError(f"Could not verify uploaded image {path.name}; check Drive before retrying.")
            state["files"][item["image_id"]] = {
                "drive_file_id": remote_id, "parent_id": parent_id,
                "source_sha256": item["sha256"], "split": item["pilot_split"],
            }
            save_progress(state_path, state)
            print(f"[{number}/{len(images)}] {disposition}: {path.name}", flush=True)
        print(f"Finished: {state['folder_url']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(upload_dataset(dry_run=args.dry_run))
    except (DriveUploadError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
