"""Synchronize configured Drive folders into user-scoped RAG and an image cache.

A manual, deterministic MCP workflow. No LLM or paid VLM requests are made.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import tempfile
import uuid

from integrations.google_drive.client import connect_drive
from integrations.google_drive.files import DriveUploadError, FOLDER_MIME, query_literal, result_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIRECTORY = PROJECT_ROOT / 'data/drive_sync'
GOOGLE_DOC = 'application/vnd.google-apps.document'
IMAGE_VERSION = 'image-validation-v1'
DOCUMENT_MIMES = {'text/plain', 'text/markdown', 'application/pdf', GOOGLE_DOC}
IMAGE_MIMES = {'image/jpeg', 'image/png', 'image/webp'}
MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    modified_time: str
    root_folder: str
    kind: str


def parse_search_page(text):
    """Strict adapter for this community server's text-only search response."""
    header = re.match(r'Found (\d+) files \(ordered by [^\n]+\):\n', text)
    if not header:
        raise DriveUploadError('Unrecognized Drive search format')
    rows = []
    pattern = r'^(.+) \(([^()]+)\) \[id: ([A-Za-z0-9_-]+), path: .*\] \[created: ([^,]+), modified: ([^\]]+)\]$'
    for match in re.finditer(pattern, text, re.MULTILINE):
        name, mime, file_id, _, modified = match.groups()
        try:
            datetime.fromisoformat(modified.replace('Z', '+00:00'))
        except ValueError as exc:
            raise DriveUploadError(f'Missing/invalid modification time for {file_id}') from exc
        rows.append((file_id, name, mime, modified))
    if len(rows) != int(header.group(1)):
        raise DriveUploadError('Could not parse every Drive file; no removal decisions will be made')
    token = re.search(r'^More results available\. Use pageToken: (\S+)$', text, re.MULTILINE)
    if 'More results available' in text and not token:
        raise DriveUploadError('Unrecognized Drive pagination format')
    return rows, token.group(1) if token else None


async def discover_files(session, roots):
    """Recursively enumerate only the selected roots, including every page."""
    files, visited_folders, file_ids = [], set(), set()
    queue = deque((folder, folder, kind) for kind, folder in roots.items())
    while queue:
        folder, root, kind = queue.popleft()
        if folder in visited_folders:
            raise ValueError('Configured Drive roots overlap or contain a repeated folder')
        visited_folders.add(folder)
        token, seen_tokens = None, set()
        while True:
            args = {'query': f'{query_literal(folder)} in parents and trashed = false',
                    'rawQuery': True, 'pageSize': 100, 'orderBy': 'name'}
            if token:
                args['pageToken'] = token
            rows, token = parse_search_page(result_text(await session.call_tool('search', arguments=args)))
            for file_id, name, mime, modified in rows:
                if mime == FOLDER_MIME:
                    queue.append((file_id, root, kind))
                else:
                    if file_id in file_ids:
                        raise DriveUploadError('Duplicate file across Drive pages or roots; retry the scan')
                    file_ids.add(file_id)
                    files.append(DriveFile(file_id, name, mime, modified, root, kind))
            if not token:
                break
            if token in seen_tokens:
                raise DriveUploadError('Drive returned a repeated pagination token')
            seen_tokens.add(token)
    return files


def document_version():
    from rag.pipeline import PLATFORM_CHUNKING, PLATFORM_MODEL
    return f'{PLATFORM_MODEL}:{PLATFORM_CHUNKING}:drive-extraction-v1'


def supported(file):
    if file.kind == 'images':
        return file.mime_type in IMAGE_MIMES
    return file.mime_type in DOCUMENT_MIMES and (
        file.mime_type == GOOGLE_DOC or Path(file.name).suffix.lower() in {'.txt', '.md', '.markdown', '.pdf'})


def cache_valid(record):
    path = Path(record.get('cache_path') or '')
    return (path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES
            and hashlib.sha256(path.read_bytes()).hexdigest() == record.get('content_sha256'))


async def download_source(session, file, cache_directory):
    temporary_dir = cache_directory / 'temporary'
    temporary_dir.mkdir(parents=True, exist_ok=True)
    suffix = '.txt' if file.mime_type == GOOGLE_DOC else Path(file.name).suffix.lower()
    with tempfile.NamedTemporaryFile(dir=temporary_dir, suffix=suffix, delete=False) as stream:
        temporary = Path(stream.name)
    temporary.unlink()  # downloadFile requires a non-existing target.
    args = {'fileId': file.file_id, 'localPath': str(temporary.resolve()), 'overwrite': False}
    if file.mime_type == GOOGLE_DOC:
        args['exportMimeType'] = 'text/plain'
    try:
        result_text(await session.call_tool('downloadFile', arguments=args))
        if not temporary.is_file() or temporary.is_symlink():
            raise ValueError('Drive download did not produce a regular file')
        if not 0 < temporary.stat().st_size <= MAX_BYTES:
            raise ValueError('Downloaded file must be between 1 byte and 10 MB')
        raw = temporary.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if file.kind == 'images':
            from PIL import Image
            with Image.open(temporary) as image:
                if image.width * image.height > 40_000_000 or image.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('Unsupported or oversized image')
                image.verify()
        destination = cache_directory / file.kind / f'{file.file_id}_{digest}{suffix}'
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(destination)
        return raw, digest, destination
    finally:
        temporary.unlink(missing_ok=True)


class SyncStore:
    """Durable PostgreSQL tracking; shares transactions with RAG indexing."""
    def __init__(self, connection, owner_id):
        from rag.vector_store import ensure_studio_tables
        self.connection, self.owner_id = connection, owner_id
        ensure_studio_tables(connection)
        connection.execute('''CREATE TABLE IF NOT EXISTS rag.drive_sync_files (
            owner_id INTEGER NOT NULL, file_id TEXT NOT NULL,
            root_folder TEXT NOT NULL, kind TEXT NOT NULL,
            name TEXT NOT NULL, mime_type TEXT NOT NULL, modified_time TEXT NOT NULL,
            content_sha256 TEXT, processor_version TEXT, status TEXT NOT NULL,
            document_id TEXT, cache_path TEXT, vlm_status TEXT,
            error_message TEXT, last_seen_run TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(owner_id, file_id)
        )''')
        connection.commit()

    def get(self, file_id):
        from psycopg.rows import dict_row
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('SELECT * FROM rag.drive_sync_files WHERE owner_id=%s AND file_id=%s',
                           (self.owner_id, file_id))
            return cursor.fetchone()

    def save(self, file, **values):
        fields = ['root_folder', 'kind', 'name', 'mime_type', 'modified_time', 'content_sha256',
                  'processor_version', 'status', 'document_id', 'cache_path', 'vlm_status',
                  'error_message', 'last_seen_run']
        record = {**(self.get(file.file_id) or {}), **file.__dict__, **values}
        self.connection.execute(
            f"INSERT INTO rag.drive_sync_files (owner_id,file_id,{','.join(fields)}) "
            f"VALUES ({','.join(['%s'] * (len(fields)+2))}) ON CONFLICT(owner_id,file_id) "
            f"DO UPDATE SET {','.join(f'{f}=EXCLUDED.{f}' for f in fields)},updated_at=now()",
            [self.owner_id, file.file_id] + [record.get(field) for field in fields])

    def remove_index(self, document_id):
        dataset = f'social_studio_user_{self.owner_id}'
        self.connection.execute('''DELETE FROM rag.embeddings e USING rag.chunks c
            WHERE e.chunk_id=c.id AND c.dataset=%s AND c.document_id=%s''', (dataset, document_id))
        self.connection.execute('DELETE FROM rag.chunks WHERE dataset=%s AND document_id=%s', (dataset, document_id))
        self.connection.execute('DELETE FROM rag.documents WHERE dataset=%s AND document_id=%s', (dataset, document_id))

    def mark_missing(self, roots, run_id):
        from psycopg.rows import dict_row
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('''SELECT * FROM rag.drive_sync_files WHERE owner_id=%s
                AND root_folder=ANY(%s) AND last_seen_run IS DISTINCT FROM %s AND status<>'missing' ''',
                (self.owner_id, list(roots.values()), run_id))
            missing = cursor.fetchall()
        for row in missing:
            if row['document_id']:
                self.remove_index(row['document_id'])
            file = DriveFile(**{key: row[key] for key in DriveFile.__dataclass_fields__})
            self.save(file, status='missing', vlm_status='missing' if row['kind'] == 'images' else None)
        self.connection.commit()
        return len(missing)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()


def index_drive_document(store, file, raw):
    """Atomically replace this Drive document's chunks, avoiding stale tails."""
    from rag.ingestion import extract_document
    from rag.pipeline import PLATFORM_CHUNKING, PLATFORM_MODEL, chunk_text, embed_passages, load_platform_model
    from rag.vector_store import store_chunks, store_documents, store_embeddings
    filename = file.name + '.txt' if file.mime_type == GOOGLE_DOC else file.name
    title, text = extract_document(filename, raw)
    if len(text) == 100_000 and len(raw) > 100_000:
        raise ValueError('Document exceeds the extraction limit; split it before sync')
    document_id = f'drive:{file.file_id}'
    chunks = chunk_text(text, title, load_platform_model().tokenizer)
    if not chunks:
        raise ValueError('No usable chunks')
    for chunk in chunks:
        chunk['document_id'] = document_id
    vectors = embed_passages([chunk['text'] for chunk in chunks])
    # Compute first; a failure preserves the previously indexed version.
    store.remove_index(document_id)
    dataset = f'social_studio_user_{store.owner_id}'
    store_documents(store.connection, [{'_id': document_id, 'title': title, 'text': text,
        'metadata': {'owner_id': store.owner_id, 'source_url': f'https://drive.google.com/file/d/{file.file_id}/view',
                     'drive_file_id': file.file_id, 'drive_modified_time': file.modified_time,
                     'original_filename': file.name, 'source': 'google_drive'}}], dataset)
    chunk_ids = store_chunks(store.connection, chunks, dataset, PLATFORM_CHUNKING)
    store_embeddings(store.connection, chunk_ids, vectors, PLATFORM_MODEL)
    return document_id


async def sync_files(session, store, roots, cache_directory=CACHE_DIRECTORY, *, indexer=index_drive_document):
    # Complete discovery before any removal decisions. A failed listing aborts.
    files = await discover_files(session, roots)
    run_id = str(uuid.uuid4())
    report = {'discovered': len(files), 'indexed': 0, 'images_cached': 0, 'unchanged': 0,
              'metadata_only': 0, 'unsupported': 0, 'failed': 0, 'missing': 0, 'paid_model_calls': 0}
    for file in files:
        old = store.get(file.file_id)
        version = document_version() if file.kind == 'documents' else IMAGE_VERSION
        try:
            if not supported(file):
                if old and old.get('document_id'):
                    store.remove_index(old['document_id'])
                store.save(file, status='unsupported', last_seen_run=run_id, error_message=None)
                report['unsupported'] += 1
            elif (old and old['status'] in {'indexed', 'cached'} and old['modified_time'] == file.modified_time
                  and old['name'] == file.name and old['processor_version'] == version
                  and old['root_folder'] == file.root_folder and old['kind'] == file.kind
                  and cache_valid(old)):
                store.save(file, last_seen_run=run_id)
                report['unchanged'] += 1
            else:
                raw, digest, path = await download_source(session, file, cache_directory)
                same_content = bool(old and old['content_sha256'] == digest and old['kind'] == file.kind
                                    and old['processor_version'] == version and old['status'] in {'indexed', 'cached'})
                values = {'content_sha256': digest, 'cache_path': str(path.resolve()),
                          'processor_version': version, 'error_message': None, 'last_seen_run': run_id}
                if file.kind == 'documents':
                    if same_content and old['name'] == file.name:
                        report['metadata_only'] += 1
                        document_id = old['document_id']
                    else:
                        document_id = await asyncio.to_thread(indexer, store, file, raw)
                        report['indexed'] += 1
                    values.update(status='indexed', document_id=document_id, vlm_status=None)
                else:
                    # No VLM call here. Preserve completed analysis only for identical bytes.
                    if old and old.get('document_id'):
                        store.remove_index(old['document_id'])
                    values.update(status='cached', document_id=None,
                                  vlm_status=old.get('vlm_status') if same_content else 'pending')
                    report['images_cached'] += 1
                store.save(file, **values)
            store.commit()
            print(f"{file.kind}: {file.name}", flush=True)
        except Exception as exc:
            store.rollback()
            store.save(file, status='error', error_message=f'{type(exc).__name__}: {str(exc)[:400]}', last_seen_run=run_id)
            store.commit()
            report['failed'] += 1
            print(f"Failed: {file.name}: {type(exc).__name__}", flush=True)
    report['missing'] = store.mark_missing(roots, run_id)
    return report


async def run_sync(owner_id, roots, *, scan_only=False, cache_directory=CACHE_DIRECTORY):
    if owner_id < 1 or set(roots) != {'documents', 'images'} or len(set(roots.values())) != 2:
        raise ValueError('Use a positive owner ID and two distinct folder IDs')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', folder) for folder in roots.values()):
        raise ValueError('Invalid Drive folder ID')
    async with connect_drive() as session:
        if scan_only:
            files = await discover_files(session, roots)
            return {'discovered': len(files), 'documents': sum(f.kind == 'documents' for f in files),
                    'images': sum(f.kind == 'images' for f in files), 'mode': 'scan-only'}
        from rag.vector_store import connect_rag_database
        with connect_rag_database() as connection:
            # A session lock prevents overlapping syncs for the same user.
            acquired = connection.execute("SELECT pg_try_advisory_lock(hashtext('drive-sync'),%s)", (owner_id,)).fetchone()[0]
            connection.commit()
            if not acquired:
                raise RuntimeError('A Drive sync for this user is already running')
            try:
                store = SyncStore(connection, owner_id)
                return await sync_files(session, store, roots, cache_directory)
            finally:
                connection.rollback()
                connection.execute("SELECT pg_advisory_unlock(hashtext('drive-sync'),%s)", (owner_id,))
                connection.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id', type=int, required=True, help='Existing dashboard user ID; never another user\'s ID')
    parser.add_argument('--documents-folder', required=True)
    parser.add_argument('--images-folder', required=True)
    parser.add_argument('--scan-only', action='store_true', help='List only; no downloads, indexing or DB writes')
    args = parser.parse_args()
    report = asyncio.run(run_sync(args.owner_id, {'documents': args.documents_folder, 'images': args.images_folder}, scan_only=args.scan_only))
    print(json.dumps(report, indent=2), flush=True)
    if report.get('failed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
