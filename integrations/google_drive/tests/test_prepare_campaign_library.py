"""Fixture migration keeps evaluation hints out of runtime documents."""
import hashlib
import json
from pathlib import Path

from integrations.google_drive.prepare_campaign_library import prepare_library


def test_prepare_removes_photo_associations_and_preserves_private_matches(tmp_path):
    for group in ('images', 'documents'):
        (tmp_path / group).mkdir()
    image_name, doc_name = 'SC01_abc123.jpg', 'SC01_workspace_brief.txt'
    (tmp_path / 'images' / image_name).write_bytes(b'photo fixture')
    (tmp_path / 'documents' / doc_name).write_text('''SC01 explicit paired brief
MATCHED IMAGE
Image filename: SC01_abc123.jpg
SCENE PRODUCTS
SC01-P01 — Studio Example Lamp
Visible reference: A pale desk lamp.
FICTIONAL CAMPAIGN POLICY
Fictional only.
CAMPAIGN REQUEST
Audience: Students.
Goal: Explore desk lighting.
Generate an Instagram draft with the matching photo.
EVIDENCE BOUNDARIES
RELATED WEB REFERENCE
https://example.com/workspaces
IMAGE CREDIT
https://example.com/private-photo-association
''')
    manifest = {'images': [{'scene_id': 'SC01', 'image_id': 'abc123', 'title': 'Study lighting',
                            'path': 'images/' + image_name}],
                'documents': [{'path': 'documents/' + doc_name}]}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    (tmp_path / 'pairs.json').write_text(json.dumps([{'scene_id': 'SC01', 'image_filename': image_name,
                                                    'document_filename': doc_name}]))
    (tmp_path / 'drive_uploads.json').write_text(json.dumps({'files': {
        image_name: {'drive_file_id': 'imageID'}, doc_name: {'drive_file_id': 'docID'}}}))
    prepared = prepare_library(tmp_path)
    text = (tmp_path / 'documents/study_lighting.txt').read_text()
    assert 'A pale desk lamp' in text
    for hint in ('SC01', '.jpg', 'IMAGE CREDIT', 'private-photo-association', 'MATCHED IMAGE'):
        assert hint not in text
    assert 'FICTIONAL' in text
    assert (tmp_path / 'images/abc123.jpg').exists()
    assert not (tmp_path / 'pairs.json').exists()
    private = json.loads((tmp_path / 'evaluation/expected_matches.json').read_text())
    assert private['pairs'][0]['document_filename'] == 'study_lighting.txt'
    assert private['pairs'][0]['image_filename'] == 'abc123.jpg'
    assert prepared['documents'][0]['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert prepare_library(tmp_path) == prepared  # Rerun is a no-op.
