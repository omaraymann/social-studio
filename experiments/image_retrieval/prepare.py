"""Freeze a geographically stratified XM3600 sample and download its photos."""
import csv
import hashlib
import html
import io
import json
import random
import tarfile
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data/image_retrieval'
SEED = 42


def prepare():
    raw = DATA / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    annotation = raw / 'captions/captions.jsonl'
    if not annotation.exists():
        with urllib.request.urlopen('https://google.github.io/crossmodal-3600/web-data/captions.zip', timeout=90) as r:
            archive = r.read()
        (raw / 'captions.zip').write_bytes(archive)
        annotation.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            annotation.write_bytes(z.read('captions.jsonl'))
    if not (raw / 'image_attributions.csv').exists():
        with urllib.request.urlopen('https://google.github.io/crossmodal-3600/web-data/image_attributions.csv', timeout=90) as r:
            (raw / 'image_attributions.csv').write_bytes(r.read())
    records = [json.loads(line) for line in (DATA / 'raw/captions/captions.jsonl').read_text().splitlines()]
    with (DATA / 'raw/image_attributions.csv').open() as f:
        attributions = {r['ImageID']: r for r in csv.DictReader(f)}
    groups = defaultdict(list)
    for record in records:
        groups[record['image/locale']].append(record)
    rng = random.Random(SEED)
    locales = sorted(groups)
    extra = set(rng.sample(locales, 20))
    chosen = []
    for locale in locales:
        chosen.extend(rng.sample(sorted(groups[locale], key=lambda r: r['image/key']), 5 + (locale in extra)))
    chosen.sort(key=lambda r: r['image/key'])
    out = DATA / 'sample_200'
    (out / 'images').mkdir(parents=True, exist_ok=True)
    if not (raw / 'images.tgz').exists():
        from .download_archive import download
        download()
    selected_ids = {record['image/key'] for record in chosen}
    extracted = set()
    with tarfile.open(raw / 'images.tgz', 'r:gz') as archive:
        for member in archive:
            key = Path(member.name).stem
            if member.isfile() and key in selected_ids:
                if key in extracted:
                    raise ValueError(f'Duplicate archive image: {key}')
                content = archive.extractfile(member).read()
                Image.open(io.BytesIO(content)).verify()
                (out / 'images' / f'{key}.jpg').write_bytes(content)
                extracted.add(key)
    if extracted != selected_ids:
        raise ValueError(f'Missing selected images in official archive: {selected_ids - extracted}')

    def fetch(record):
        key = record['image/key']
        attr = attributions[key]
        url = 'https://open-images-dataset.s3.amazonaws.com/crossmodal-3600/images.tgz'
        target = out / 'images' / f'{key}.jpg'
        content = target.read_bytes()
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
        captions = record['en']['caption']
        if len(captions) != 2:
            raise ValueError(f'Expected two English captions for {key}')
        return {'image_id': key, 'locale': record['image/locale'], 'path': str(target.relative_to(ROOT)),
                'captions': captions, 'sha256': hashlib.sha256(content).hexdigest(),
                'width': width, 'height': height, 'download_url': url, 'attribution': attr}

    with ThreadPoolExecutor(max_workers=12) as pool:
        items = []
        for i, record in enumerate(pool.map(fetch, chosen), 1):
            items.append(record)
            if i % 25 == 0:
                print(f'Prepared {i}/200 images', flush=True)
    manifest = {'dataset': 'Crossmodal-3600', 'source': 'https://google.github.io/crossmodal-3600/',
                'seed': SEED, 'selection': '5 images from each of 36 locales; 20 seeded locales receive one extra',
                'gallery_count': len(items), 'query_count': sum(len(r['captions']) for r in items),
                'archive_sha256': hashlib.sha256((raw / 'images.tgz').read_bytes()).hexdigest(),
                'annotation_sha256': hashlib.sha256((DATA / 'raw/captions/captions.jsonl').read_bytes()).hexdigest(),
                'records': items}
    path = out / 'manifest.json'
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError('Existing frozen manifest differs; refusing to overwrite')
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    cards = []
    for record in items:
        attr = record['attribution']
        captions = '<br>'.join(html.escape(c) for c in record['captions'])
        cards.append(f'<article><img loading="lazy" src="images/{record["image_id"]}.jpg">'
                     f'<p>{captions}</p><small>{record["image_id"]} · locale {record["locale"]}<br>'
                     f'<a href="{html.escape(attr["OriginalLandingURL"], quote=True)}">'
                     f'{html.escape(attr["Author"])}</a> · '
                     f'<a href="{html.escape(attr["License"], quote=True)}">Image license</a></small></article>')
    (out / 'gallery.html').write_text('<!doctype html><meta charset="utf-8"><title>XM3600 pilot gallery</title>'
                                    '<style>body{font:16px system-ui;margin:25px;background:#f5f5f5}'
                                    'main{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:20px}'
                                    'article{background:white;padding:14px}img{width:100%;height:220px;object-fit:contain}'
                                    'small{color:#555}</style><h1>200 images · 400 human English captions</h1>'
                                    '<p>Fixed seed 42; sample chosen before inference.</p><main>' + ''.join(cards) + '</main>')
    print(f'Frozen manifest: {path}', flush=True)


if __name__ == '__main__':
    prepare()
