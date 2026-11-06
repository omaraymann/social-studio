"""Shared file/folder operations for deterministic Drive uploads."""

import json
import re

from integrations.google_drive.client import create_drive_folder, search_drive_files

FOLDER_MIME = "application/vnd.google-apps.folder"

class DriveUploadError(RuntimeError):
    pass


def result_text(result):
    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.is_error:
        raise DriveUploadError(text)
    return text


def query_literal(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


async def find_named_item(session, name, parent_id, mime_type):
    query = (f"name = {query_literal(name)} and {query_literal(parent_id)} in parents "
             f"and mimeType = {query_literal(mime_type)} and trashed = false")
    text = result_text(await search_drive_files(session, query, page_size=100, raw_query=True))
    # This MCP server currently returns text rather than structured file objects.
    ids = re.findall(r"\[id: ([A-Za-z0-9_-]+), path:", text)
    if len(ids) > 1 or "More results available" in text:
        raise DriveUploadError(f"Multiple files named {name!r}; resolve the ambiguity in Drive first.")
    if not ids and not text.startswith("Found 0 files"):
        raise DriveUploadError("Unexpected search response; stopped to avoid creating duplicates.")
    return ids[0] if ids else None


def created_id(result):
    text = result_text(result)
    match = re.search(r"^ID: ([A-Za-z0-9_-]+)$", text, re.MULTILINE)
    if not match:
        raise DriveUploadError("Could not read the created file ID. Check Drive before restarting.")
    return match.group(1)


async def ensure_folder(session, name, parent_id):
    existing = await find_named_item(session, name, parent_id, FOLDER_MIME)
    return existing or created_id(await create_drive_folder(session, name, parent_id))



def save_progress(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(path)
