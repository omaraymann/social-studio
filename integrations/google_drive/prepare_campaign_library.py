"""Remove evaluation hints from the local demo library and update its Drive files.

Only the previously uploaded fixture files recorded in the local checkpoint are
changed. Product descriptions remain fictional. Evaluation matches stay local.
"""
import argparse
import asyncio
import hashlib
import json
import re
import shutil
from pathlib import Path

from integrations.google_drive.client import connect_drive
from integrations.google_drive.files import result_text, save_progress
from integrations.google_drive.upload_matched_examples import DATASET

REVISION = "independent-library-v1"


def prepare_library(directory=DATASET):
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("library_revision") == REVISION:
        return manifest
    checkpoint = json.loads((directory / "drive_uploads.json").read_text())
    pairs = json.loads((directory / "pairs.json").read_text())
    archive = directory / "archive/explicitly_paired_v1"
    archive.mkdir(parents=True, exist_ok=True)
    # Keep the original evidence and migration input locally before changing it.
    for name in ("manifest.json", "pairs.json", "drive_uploads.json"):
        shutil.copyfile(directory / name, archive / name)
    changes, evaluation = [], []
    # Older manifests may store scene IDs only in the pair table.
    docs_by_name = {Path(r["path"]).name: r for r in manifest["documents"]}
    for image, pair in zip(manifest["images"], pairs, strict=True):
        assert image["scene_id"] == pair["scene_id"]
        doc = docs_by_name[pair["document_filename"]]
        old_image = directory / image["path"]
        old_doc = directory / doc["path"]
        original = old_doc.read_text()
        title = image["title"]
        new_doc_name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") + ".txt"
        product_section = original.split("SCENE PRODUCTS\n", 1)[1].split("FICTIONAL CAMPAIGN POLICY", 1)[0]
        product_section = re.sub(r"SC\d+-P\d+ — ", "", product_section)
        product_section = product_section.replace("Visible reference:", "Appearance:")
        product_section = product_section.replace("visible props are not automatically included", "accessories are not automatically included")
        request = original.split("CAMPAIGN REQUEST\n", 1)[1].split("EVIDENCE BOUNDARIES", 1)[0]
        audience_goal = request.split("Generate an Instagram draft", 1)[0].strip()
        source = original.split("RELATED WEB REFERENCE\n", 1)[1].splitlines()[0]
        content = f'''{title} — product collection notes

FICTIONAL DEMONSTRATION CATALOGUE
Studio Example Collection is an invented catalogue for campaign testing. Product names, prices and policies below are fictional. They are not verified offers or manufacturer specifications and must not be published as real commercial claims.

PRODUCTS AND APPEARANCE
{product_section.strip()}

MERCHANDISING CONTEXT
{audience_goal}
These products may be promoted individually or alongside complementary items. Selection depends on the requested topic, audience and product attributes. Other collections may also be relevant; no exclusive pairing is implied.

OFFER POLICY
Currency: GBP. The listed demonstration prices apply per item, not to a bundle. Availability is demonstration-only. No discount, stock level, launch date, shipping promise, warranty, sustainability certification or health benefit is supplied. Mention a price only for an explicitly requested fictional demo offer and label it as such.

FACTUAL BOUNDARIES
Appearance does not establish brand, material, dimensions, capacity, durability or compatibility. Descriptions such as metal-looking or fabric-looking are visual observations rather than technical specifications. For real marketing, replace invented catalogue fields with approved product documentation before publication.

GENERAL REFERENCE
{source}
This is general merchandising inspiration. The catalogue above was independently authored; it does not inherit the retailer's product identities, claims, prices or endorsement.
'''
        new_doc = directory / "documents" / new_doc_name
        new_doc.write_text(content)
        new_image = directory / "images" / f"{image['image_id']}.jpg"
        shutil.copyfile(old_image, new_image)
        for old_path in (old_doc, old_image):
            destination = archive / old_path.parent.name / old_path.name
            destination.parent.mkdir(exist_ok=True)
            shutil.move(old_path, destination)
        for record, old_name, path, kind in ((doc, pair['document_filename'], new_doc, 'documents'),
                                             (image, pair['image_filename'], new_image, 'images')):
            record['path'] = f"{kind}/{path.name}"
            record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            changes.append({'old_name': old_name, 'new_name': path.name, 'path': record['path'],
                            'kind': kind, 'drive_file_id': checkpoint['files'][old_name]['drive_file_id'],
                            'sha256': record['sha256']})
        evaluation.append({**pair, 'image_filename': new_image.name, 'document_filename': new_doc.name})
    for doc in manifest['documents']:
        if not Path(doc['path']).name.startswith('GUIDE_'):
            continue
        path = directory / doc['path']
        original = path.read_text()
        shutil.copyfile(path, archive / 'documents' / path.name)
        summary = original.split('INDEPENDENT SOURCE SUMMARY\n', 1)[1].split('SOCIAL STUDIO TEST ADAPTATION', 1)[0].strip()
        path.write_text(f'''{original.splitlines()[0]}
Source: {doc['source_url']}
Independent summary, prepared 20 October 2026.

{summary}

CAMPAIGN PLANNING NOTES
Choose complementary product categories appropriate to the campaign audience. Use approved catalogue records for prices and specifications. Describe each item's role clearly and distinguish individual offers from bundles. Visual appearance alone cannot establish material, performance or compatibility. Keep stock, delivery and warranty claims out unless verified documentation supplies them.

These notes provide general topic context. Studio Example catalogue entries are independently authored fictional test data; they are not products or offers verified by the linked retailer.
''')
        doc['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        changes.append({'old_name': path.name, 'new_name': path.name, 'path': doc['path'],
                        'kind': 'documents', 'drive_file_id': checkpoint['files'][path.name]['drive_file_id'],
                        'sha256': doc['sha256']})
    evaluation_dir = directory / 'evaluation'
    evaluation_dir.mkdir(exist_ok=True)
    save_progress(evaluation_dir / 'expected_matches.json', {'purpose': 'Evaluation only. Never ingest into RAG or show to the selecting model.', 'pairs': evaluation})
    (directory / 'pairs.json').unlink()
    manifest['library_revision'] = REVISION
    manifest['data_status'] = 'Independent topical documents and real reference photos; associations retained only in private evaluation files.'
    save_progress(manifest_path, manifest)
    save_progress(directory / 'library_revision.json', {'revision': REVISION, 'changes': changes, 'completed': []})
    return manifest


async def revise_drive_library(directory=DATASET):
    prepare_library(directory)
    journal_path = directory / 'library_revision.json'
    journal = json.loads(journal_path.read_text())
    checkpoint_path = directory / 'drive_uploads.json'
    checkpoint = json.loads(checkpoint_path.read_text())
    async with connect_drive(allow_uploads=True) as session:
        for number, change in enumerate(journal['changes'], 1):
            file_id = change['drive_file_id']
            if file_id not in journal['completed']:
                if change['kind'] == 'documents':
                    args = {'fileId': file_id, 'name': change['new_name'],
                            'content': (directory / change['path']).read_text()}
                    result_text(await session.call_tool('updateTextFile', arguments=args))
                else:
                    result_text(await session.call_tool('renameItem', arguments={'itemId': file_id, 'newName': change['new_name']}))
                journal['completed'].append(file_id)
                save_progress(journal_path, journal)
            checkpoint['files'].pop(change['old_name'], None)
            checkpoint['files'][change['new_name']] = {'drive_file_id': file_id, 'sha256': change['sha256'], 'group': change['kind']}
            save_progress(checkpoint_path, checkpoint)
            print(f"[{number}/{len(journal['changes'])}] Revised: {change['new_name']}", flush=True)
    checkpoint['verified'] = False  # Content verification is performed by sync/readback.
    save_progress(checkpoint_path, checkpoint)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-only', action='store_true')
    args = parser.parse_args()
    if args.local_only:
        prepare_library()
    else:
        asyncio.run(revise_drive_library())
