# Google Drive MCP integration

`client.py` provides a reusable MCP session and wrappers for discovery, search, text reading, folder creation and local file upload. It connects to the existing sibling `google-drive-mcp` Node.js server. No LLM is required for these deterministic operations.

Install the Python SDK into the project's virtual environment:

```bash
python -m pip install -r integrations/google_drive/requirements.txt
```

## Selected image upload

From the Social Studio project directory:

```bash
python -m integrations.google_drive.upload_dataset --dry-run
python -m integrations.google_drive.upload_dataset
```

The uploader reads `data/vision/detection_pilot/manifest.json`, validates local image hashes, and uploads only the selected images to:

```text
Social Studio/
  Product Images/
    Detection Pilot/
      development/  # 8 images
      test/         # 12 images
```

Ground-truth annotations, previews, excluded candidates and local credentials are not uploaded. Filenames are image IDs; they do not disclose reference labels to the future VLM.

Progress is stored in the Git-ignored local dataset directory as `drive_uploads.json`. Each run looks up exact filenames in the destination folder, skips existing matches and stops on ambiguous duplicates. After a newly created file, it verifies the ID is discoverable under that filename and parent. Existing files are not overwritten or content-updated; name-based skipping is not a remote byte-checksum comparison.

The upload creates no sharing permissions. Existing parent-folder permissions, if any, still apply. It makes no OpenRouter/VLM/image-generation calls and consumes no model credits. Files use the account's ordinary Drive storage.

## Permissions and local authentication

The earlier demo requested only `drive.readonly`. Uploading additionally needs `drive.file`, which supports creating files and managing files opened/created through this application. The broad `drive` scope is not needed.

In the sibling Node server directory, request the two scopes explicitly:

```bash
GOOGLE_DRIVE_MCP_SCOPES=drive.readonly,drive.file npm run auth
```

Complete Google's consent flow as the user. If Google requires the scope to be configured on the OAuth application, add `https://www.googleapis.com/auth/drive.file` under Google Auth Platform > Data Access first.

`connect_drive()` retains the original read-only requested-scope default; the uploader explicitly uses `connect_drive(allow_uploads=True)`. Important: an environment scope setting is not a token sandbox. Once the underlying account grants write access, callers must still limit which tools can be used; requesting a read-only default does not revoke already granted scopes.

Credentials remain in `~/.config/google-drive-mcp/`, outside the repository. The current server and Node executable paths are configured for this Mac and this sibling-directory layout. This is a local single-user experiment, not the multi-user dashboard connection yet.

## Verification

Offline tests check restart deduplication, split folders, selected-only uploads, checksum changes, dry-run isolation, MCP errors and fail-closed response parsing:

```bash
python -m pytest integrations/google_drive/tests -q
```

## Campaign source documents

```bash
python -m integrations.google_drive.upload_documents --dry-run
python -m integrations.google_drive.upload_documents
```

The document uploader selects the 12 source texts explicitly listed in `rag/data/raw/social_studio_eval/corpus.jsonl`, plus the existing fictional `~/Desktop/lamp_product_brief.txt` when present. It prepares attributed copies under the Git-ignored `data/drive_documents/` directory and uploads them to `Social Studio/Documents`. Each research copy includes its title, original source link, publisher, recorded retrieval date and reuse notes. The original local sources are not changed. The lamp brief is explicitly marked fictional.

It does not upload SciFact, annotations, project source code, credentials, or directory-wide matches. It reuses exact-name folder/file lookups and records Drive IDs and source checksums in a local checkpoint. Existing same-name files are skipped rather than overwritten. Uploading these files does not automatically index them into RAG; Drive-to-RAG synchronisation is a separate next step.

## Matched multi-product scenes and briefs

```bash
python -m integrations.google_drive.upload_matched_examples --dry-run
python -m integrations.google_drive.upload_matched_examples
```

This uploader uses the separate Git-ignored `data/vision/matched_campaigns/manifest.json`. The prepared bundle contains 20 real scene photos, 20 scene-specific fictional campaign briefs and four independently written reference notes. Photos and documents go into `Matched Scenes` subfolders under `Product Images` and `Documents` respectively. The initial scene-ID pairing was subsequently removed from filenames and documents; expected matches now exist only in the local private `evaluation/` directory. Each photo's original source, author and license are retained in the local research manifest, outside the RAG corpus.

Only manifest-selected JPG/TXT files are uploaded. Detection annotations, contact sheets, candidate images and local checkpoints stay local. Source annotations have not been verified as exhaustive gold boxes. Campaign prices and specifications in these briefs are explicitly fictional; the linked retailer pages provide general inspiration rather than product identity matches.

The uploader validates checksums before connecting, records returned IDs after each upload, checks the final folder contents, and rejects unconfirmed same-name remote files instead of silently adopting them. No model calls or RAG ingestion occur here.

## Independent document/image retrieval and incremental sync

The matched library was revised to remove photo mappings from the runtime documents. `prepare_campaign_library.py` is the one-time fixture migration: it replaces photo-naming briefs with topical catalogue notes, removes scene prefixes from photo filenames, and updates the existing Drive files in place. It retains expected matches only in the local ignored `evaluation/` directory. `archive/` and manifest annotation fields are research artifacts, never retrieval input. Use Drive as the source of future document edits; the uploader does not overwrite independently edited files.

Run a scan from the repository root (replace folder IDs for another library):

```bash
/Users/omar/.codex/flyrank-review-venv/bin/python -m integrations.google_drive.sync \
  --owner-id 1 \
  --documents-folder 19WZVsbXFWZjoWzoVnWXoZyjsvptqtqbs \
  --images-folder 1WAwipz9tSWHlh0O3BZOqq5Om5xHVZ0mX \
  --scan-only
```

Remove `--scan-only` to synchronize. `--owner-id` must identify your existing dashboard user; this local administrator CLI is not a multi-user OAuth endpoint. The current Mac has a single dashboard user, ID 1. Authentication still uses the locally authorized Drive MCP account. The current database settings are loaded by the existing RAG configuration; credentials are not printed. This command is manual, with no scheduler, dashboard Sync button or campaign-time automatic invocation yet.

The sync recursively scans only the two selected folders and follows all result pages. It uses the server's modification times for quick change detection, SHA-256 for downloaded content and cache validation, and the embedding/chunking version for processing invalidation. Unchanged valid files are neither downloaded nor re-embedded. A timestamp-only change is downloaded and checked but does not trigger re-embedding. A document rename does re-embed because titles are included in chunks.

Documents: TXT, Markdown, PDF and Google Docs (exported as plain text), at most 10 MB per downloaded file. Empty/unextractable documents fail rather than becoming successful sync records. Each Drive file has a stable `drive:<file-id>` document ID within `social_studio_user_<owner-id>`. The existing BGE model runs locally. Updated documents replace all previous chunks/embeddings for that document atomically, so shortening a document cannot leave stale trailing chunks. A failed update preserves the previously indexed version and records an error; the next run retries it. The sync does not upload cached files back to Drive.

Images: JPEG, PNG and WebP, at most 10 MB and 40 million pixels. Files are downloaded and validated into the ignored `data/drive_sync/` cache. New or changed image bytes receive `vlm_status=pending`; byte-identical images retain their prior analysis state. No VLM requests, descriptions, bounding boxes, image embeddings or image-selection decisions are produced by sync.

`rag.drive_sync_files` in PostgreSQL records owner, Drive ID, folder, filename, modification time, content hash, processor version, status, local cache path and errors. A database lock prevents simultaneous syncs for the same user. A complete scan marks files removed from these folders as missing and removes their Drive-imported RAG entries; it does not delete anything from Drive or touch manually uploaded RAG documents. A failed/incomplete folder listing aborts before removal decisions. Unsupported files are tracked but not indexed. Shortcuts and other libraries are not followed. Cache versions are retained locally; cache garbage collection is not yet implemented.

The MCP server currently formats search results as text, so `sync.py` has a strict parser and stops on unrecognized formats rather than guessing. The workflow is deterministic Python calling MCP tools. An LLM orchestrator is a later step.
