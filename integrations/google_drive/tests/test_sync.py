"""Offline workflow tests: no Google, embedding downloads, or VLM calls."""
import asyncio
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from integrations.google_drive import sync
from test_upload_dataset import response

MODIFIED = '2026-10-20T18:00:00.000Z'
NEWER = '2026-10-20T19:00:00.000Z'
ROOTS = {'documents': 'docs', 'images': 'images'}


def item(file_id='doc1', name='workspace.txt', mime='text/plain', modified=MODIFIED):
    return (file_id, name, mime, modified)


def page(rows, token=None):
    content = f'Found {len(rows)} files (ordered by name):\n'
    content += '\n'.join(f'{name} ({mime}) [id: {fid}, path: My Drive] [created: {MODIFIED}, modified: {modified}]'
                         for fid, name, mime, modified in rows)
    if token:
        content += f'\n\nMore results available. Use pageToken: {token}\nPass orderBy: name again to keep this ordering.'
    return response(content)


class FakeSession:
    def __init__(self, listings, contents):
        self.listings = listings
        self.contents = contents
        self.downloads = []

    async def call_tool(self, tool, arguments):
        if tool == 'search':
            folder = arguments['query'].split("'")[1]
            rows, token = self.listings.get((folder, arguments.get('pageToken')), ([], None))
            return page(rows, token)
        assert tool == 'downloadFile'
        fid = arguments['fileId']
        self.downloads.append(fid)
        content = self.contents[fid]
        if isinstance(content, Exception):
            raise content
        Path(arguments['localPath']).write_bytes(content)
        return response('Downloaded')


class FakeStore:
    owner_id = 1

    def __init__(self):
        self.rows = {}
        self.saved = {}
        self.removed = []

    def get(self, fid):
        return self.rows.get(fid)

    def save(self, file, **values):
        defaults = {key: None for key in ('content_sha256', 'processor_version', 'status', 'document_id', 'cache_path', 'vlm_status')}
        self.rows[file.file_id] = {**defaults, **self.rows.get(file.file_id, {}), **file.__dict__, **values}

    def remove_index(self, document_id):
        self.removed.append(document_id)

    def commit(self):
        self.saved = deepcopy(self.rows)

    def rollback(self):
        self.rows = deepcopy(self.saved)

    def mark_missing(self, roots, run_id):
        missing = [r for r in self.rows.values() if r['root_folder'] in roots.values()
                   and r.get('last_seen_run') != run_id and r['status'] != 'missing']
        for r in missing:
            if r.get('document_id'):
                self.remove_index(r['document_id'])
            r['status'] = 'missing'
        return len(missing)


@pytest.fixture(autouse=True)
def version(monkeypatch):
    monkeypatch.setattr(sync, 'document_version', lambda: 'test-model-and-chunker')


def test_pagination_nested_folders_and_fail_closed_parsing():
    session = FakeSession({('docs', None): ([item('sub', 'Nested', sync.FOLDER_MIME)], 'next'),
                           ('docs', 'next'): ([item()], None),
                           ('sub', None): ([item('doc2', 'seating.txt')], None)}, {})
    files = asyncio.run(sync.discover_files(session, ROOTS))
    assert {f.file_id for f in files} == {'doc1', 'doc2'}
    assert {f.root_folder for f in files} == {'docs'}
    with pytest.raises(sync.DriveUploadError):
        sync.parse_search_page('Found 1 files (ordered by name):\nUnrecognized item')
    session.listings[('docs', 'next')] = ([item()], 'next')
    with pytest.raises(sync.DriveUploadError):
        asyncio.run(sync.discover_files(session, ROOTS))


def test_new_unchanged_modified_and_missing_document(tmp_path):
    session = FakeSession({('docs', None): ([item()], None)}, {'doc1': b'A fictional collection for students.'})
    store, indexed = FakeStore(), []

    def indexer(store, file, raw):
        indexed.append(raw)
        return 'drive:' + file.file_id

    def run():
        return asyncio.run(sync.sync_files(session, store, ROOTS, tmp_path, indexer=indexer))

    assert run()['indexed'] == 1
    assert run()['unchanged'] == 1
    assert len(session.downloads) == 1
    session.listings[('docs', None)] = ([item(modified=NEWER)], None)
    assert run()['metadata_only'] == 1
    assert len(indexed) == 1  # Changed timestamp alone does not cause re-embedding.
    session.contents['doc1'] = b'Updated fictional desk collection for students.'
    session.listings[('docs', None)] = ([item(modified='2026-10-20T20:00:00Z')], None)
    assert run()['indexed'] == 1
    assert len(indexed) == 2
    session.listings[('docs', None)] = ([], None)
    assert run()['missing'] == 1
    assert store.removed == ['drive:doc1']


def test_images_pending_vlm_and_byte_change_invalidates_analysis(tmp_path):
    def png(colour):
        buffer = BytesIO()
        Image.new('RGB', (4, 4), colour).save(buffer, format='PNG')
        return buffer.getvalue()

    session = FakeSession({('images', None): ([item('pic', 'photo.png', 'image/png')], None)}, {'pic': png('red')})
    store = FakeStore()
    def run():
        return asyncio.run(sync.sync_files(session, store, ROOTS, tmp_path))
    assert run()['images_cached'] == 1
    assert store.rows['pic']['vlm_status'] == 'pending'
    store.rows['pic']['vlm_status'] = 'completed'
    store.commit()
    assert run()['unchanged'] == 1
    assert store.rows['pic']['vlm_status'] == 'completed'
    session.contents['pic'] = png('blue')
    session.listings[('images', None)] = ([item('pic', 'photo.png', 'image/png', NEWER)], None)
    assert run()['images_cached'] == 1
    assert store.rows['pic']['vlm_status'] == 'pending'


def test_failed_index_is_retried_even_if_modified_time_is_unchanged(tmp_path):
    session = FakeSession({('docs', None): ([item()], None)}, {'doc1': b'Valid text with enough characters.'})
    store, calls = FakeStore(), []
    def indexer(store, file, raw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('Embedding unavailable')
        return 'drive:doc1'
    def run():
        return asyncio.run(sync.sync_files(session, store, ROOTS, tmp_path, indexer=indexer))
    assert run()['failed'] == 1
    assert store.rows['doc1']['status'] == 'error'
    assert run()['indexed'] == 1
    assert len(calls) == 2


def test_partial_listing_does_not_mark_anything_missing(tmp_path):
    class Broken(FakeSession):
        async def call_tool(self, tool, arguments):
            return response('Unexpected format')
    store = FakeStore()
    with pytest.raises(sync.DriveUploadError):
        asyncio.run(sync.sync_files(Broken({}, {}), store, ROOTS, tmp_path))
    assert not store.rows and not store.removed


def test_corrupted_cache_is_downloaded_again_without_reembedding(tmp_path):
    session = FakeSession({('docs', None): ([item()], None)}, {'doc1': b'Valid collection reference document.'})
    store, indexed = FakeStore(), []
    def indexer(store, file, raw):
        indexed.append(raw)
        return 'drive:doc1'
    def run():
        return asyncio.run(sync.sync_files(session, store, ROOTS, tmp_path, indexer=indexer))
    run()
    Path(store.rows['doc1']['cache_path']).write_bytes(b'corrupt')
    assert run()['metadata_only'] == 1
    assert len(indexed) == 1 and len(session.downloads) == 2
    assert sync.cache_valid(store.rows['doc1'])
