"""Evaluate local pretrained encoders against the frozen caption/image sample."""
import argparse
import csv
import hashlib
import html
import importlib.metadata
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps
from huggingface_hub import HfApi, snapshot_download

from .prepare import DATA, ROOT

MODELS = {
    'openclip': 'laion/CLIP-ViT-B-32-laion2B-s34B-b79K',
    'siglip2': 'google/siglip2-base-patch32-256',
}


def synchronize(device):
    if device == 'mps':
        torch.mps.synchronize()
    elif device == 'cuda':
        torch.cuda.synchronize()


def download_model(name):
    repo = MODELS[name]
    # Pin the resolved revision for reproducible replay without tracking weights in Git.
    metadata_path = DATA / 'models' / name / 'revision.json'
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
    else:
        info = HfApi().model_info(repo, token=False)
        metadata = {'repo': repo, 'revision': info.sha}
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, indent=2))
    patterns = (['open_clip_config.json', 'open_clip_model.safetensors'] if name == 'openclip'
                else ['*.json', '*.model', 'model.safetensors'])
    cached = (DATA / 'models/hub' / ('models--' + repo.replace('/', '--'))
              / 'snapshots' / metadata['revision'])
    required = (patterns if name == 'openclip' else ['config.json', 'model.safetensors',
                'preprocessor_config.json', 'tokenizer_config.json', 'tokenizer.json',
                'tokenizer.model', 'special_tokens_map.json'])
    if all((cached / filename).exists() for filename in required):
        return cached, metadata
    snapshot = Path(snapshot_download(repo, revision=metadata['revision'], token=False,
                                    allow_patterns=patterns,
                                    cache_dir=str(DATA / 'models' / 'hub')))
    return snapshot, metadata


def load_encoder(name, device):
    snapshot, metadata = download_model(name)
    if name == 'openclip':
        import open_clip
        config = json.loads((snapshot / 'open_clip_config.json').read_text())
        checkpoint = snapshot / 'open_clip_model.safetensors'
        if not checkpoint.exists():
            checkpoint = snapshot / 'model.safetensors'
        model, _, preprocess = open_clip.create_model_and_transforms(
            config.get('model_name', 'ViT-B-32'), pretrained=str(checkpoint), device=device,
            image_mean=config.get('preprocess_cfg', {}).get('mean'),
            image_std=config.get('preprocess_cfg', {}).get('std'))
        tokenizer = open_clip.get_tokenizer('ViT-B-32')

        def image_features(images):
            return model.encode_image(torch.stack([preprocess(im) for im in images]).to(device))

        def text_features(texts):
            return model.encode_text(tokenizer(texts).to(device))
    else:
        checkpoint = snapshot / 'model.safetensors'
        from transformers import AutoModel, AutoProcessor
        model = AutoModel.from_pretrained(snapshot, local_files_only=True).to(device)
        processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)

        def image_features(images):
            inputs = processor(images=images, return_tensors='pt').to(device)
            features = model.get_image_features(**inputs)
            return features.pooler_output if hasattr(features, 'pooler_output') else features

        def text_features(texts):
            # SigLIP models expect lowercase text, fixed length padding (64 tokens).
            inputs = processor(text=[t.lower() for t in texts], padding='max_length',
                               max_length=64, truncation=True, return_tensors='pt').to(device)
            features = model.get_text_features(**inputs)
            return features.pooler_output if hasattr(features, 'pooler_output') else features
    model.eval()
    with checkpoint.open('rb') as file:
        metadata['checkpoint_sha256'] = hashlib.file_digest(file, 'sha256').hexdigest()
    metadata['parameters'] = sum(p.numel() for p in model.parameters())
    metadata['device'] = device
    return model, image_features, text_features, metadata


def normalize(features):
    if not torch.isfinite(features).all():
        raise ValueError('Encoder produced non-finite features')
    norms = features.float().norm(dim=-1, keepdim=True)
    if (norms == 0).any():
        raise ValueError('Encoder produced zero-length features')
    return (features.float() / norms).cpu().numpy()


def score_retrieval(image_vectors, text_vectors, targets):
    scores = text_vectors @ image_vectors.T
    ranking = np.argsort(-scores, axis=1, kind='stable')
    ranks = np.argmax(ranking == np.asarray(targets)[:, None], axis=1) + 1
    metrics = {f'recall@{k}': float(np.mean(ranks <= k)) for k in (1, 5, 10)}
    metrics.update({'mrr': float(np.mean(1 / ranks)), 'median_rank': float(np.median(ranks)),
                    'mean_rank': float(np.mean(ranks))})
    return scores, ranking, ranks, metrics


def render_review(records, queries, results, destination):
    # The same deterministic 12 captions plus the 12 poorest ranks are shown.
    indices = sorted(set(list(range(0, len(queries), max(1, len(queries) // 12)))[:12]
                         + sorted(range(len(queries)), key=lambda i: results['ranks'][i], reverse=True)[:12]))
    sections = []
    for i in indices:
        query = queries[i]
        target = records[query['target']]
        cards = [f'<figure><strong>Paired source image</strong><img src="{(ROOT / target["path"]).as_uri()}">'
                 f'<figcaption>{html.escape(target["image_id"])}</figcaption></figure>']
        for index in results['ranking'][i][:5]:
            record = records[int(index)]
            path = (ROOT / record['path']).as_uri()
            match = record['image_id'] == query['image_id']
            cards.append(f'<figure class="{"correct" if match else ""}"><img src="{path}">'
                         f'<figcaption>{html.escape(record["image_id"])}'
                         f'{" — paired image" if match else ""}<br>Similarity: {results["scores"][i,index]:.3f}'
                         f'<br>{html.escape(record["captions"][0])}</figcaption></figure>')
        sections.append(f'<section><h2>{html.escape(query["text"])}</h2><p>Paired image rank: '
                        f'{results["ranks"][i]}</p><div class="row">{"".join(cards)}</div></section>')
    destination.write_text('<!doctype html><meta charset="utf-8"><title>Image retrieval review</title>'
                          '<style>body{font:16px system-ui;margin:30px;background:#f5f5f5}.row{display:flex;gap:12px}'
                          'figure{margin:0;padding:10px;background:white;flex:1;min-width:0}'
                          'img{width:100%;height:180px;object-fit:contain}figcaption{font-size:12px}'
                          '.correct{outline:3px solid #159447}section{margin-bottom:40px}</style>'
                          '<h1>Caption → image retrieval</h1><p>Green border: paired source image. '
                          'Other results may also fit the caption but lack relevance labels. '
                          'These captions describe scenes; this is not a campaign suitability test.</p>'
                          + ''.join(sections))


def run(name, batch_size=8):
    path = DATA / 'sample_200/manifest.json'
    manifest_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads(path.read_text())
    records = manifest['records']
    queries = [{'image_id': r['image_id'], 'text': caption, 'target': i}
               for i, r in enumerate(records) for caption in r['captions']]
    for record in records:
        if hashlib.sha256((ROOT / record['path']).read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Frozen image hash mismatch')
    device = 'mps' if torch.backends.mps.is_available() else 'cpu'
    out = DATA / 'results' / name
    out.mkdir(parents=True, exist_ok=True)
    print(f'Loading {name} on {device}', flush=True)
    started = time.perf_counter()
    model, encode_images, encode_texts, metadata = load_encoder(name, device)
    synchronize(device)
    metadata['setup_seconds_excluding_prior_downloads'] = time.perf_counter() - started
    metadata.update({'manifest_sha256': manifest_hash, 'batch_size': batch_size,
                     'encoding_version': 'exif_rgb_default_preprocess_normalized_v1',
                     'torch': torch.__version__, 'numpy': np.__version__,
                     'open_clip_torch': importlib.metadata.version('open_clip_torch'),
                     'transformers': importlib.metadata.version('transformers')})
    cache = out / 'embeddings.npz'
    cache_meta = out / 'embedding_metadata.json'
    signature = {k: metadata[k] for k in ('repo', 'revision', 'manifest_sha256', 'encoding_version',
                                         'torch', 'transformers', 'open_clip_torch')}
    if cache.exists() and cache_meta.exists() and json.loads(cache_meta.read_text())['signature'] == signature:
        vectors = np.load(cache)
        image_vectors, text_vectors = vectors['images'], vectors['texts']
        timings = json.loads(cache_meta.read_text())['timings']
        print('Reusing cached embeddings', flush=True)
    else:
        timings = {}
        with torch.inference_mode():
            # Warm up each tower before measuring batch work.
            with Image.open(ROOT / records[0]['path']) as image:
                encode_images([ImageOps.exif_transpose(image).convert('RGB')])
            encode_texts([queries[0]['text']])
            synchronize(device)
            started = time.perf_counter()
            images = []
            for start in range(0, len(records), batch_size):
                batch = []
                for record in records[start:start + batch_size]:
                    with Image.open(ROOT / record['path']) as image:
                        batch.append(ImageOps.exif_transpose(image).convert('RGB'))
                images.append(normalize(encode_images(batch)))
                print(f'Images: {min(start+batch_size,len(records))}/{len(records)}', flush=True)
            synchronize(device)
            timings['image_encoding_seconds'] = time.perf_counter() - started
            started = time.perf_counter()
            texts = [normalize(encode_texts([q['text'] for q in queries[start:start + batch_size]]))
                     for start in range(0, len(queries), batch_size)]
            synchronize(device)
            timings['text_encoding_seconds'] = time.perf_counter() - started
        image_vectors, text_vectors = np.concatenate(images), np.concatenate(texts)
        np.savez(cache, images=image_vectors, texts=text_vectors)
        cache_meta.write_text(json.dumps({'signature': signature, 'timings': timings}, indent=2))
    started = time.perf_counter()
    scores, ranking, ranks, metrics = score_retrieval(image_vectors, text_vectors, [q['target'] for q in queries])
    timings['ranking_seconds'] = time.perf_counter() - started
    report = {**metadata, **timings, 'image_count': len(records), 'query_count': len(queries),
              'embedding_dimension': image_vectors.shape[1], 'metrics': metrics,
              'protocol': 'No training or tuning. All 400 human English captions search the same 200-image gallery. '
                          'Only the caption source image is a labelled positive. Pilot, not campaign evaluation.'}
    (out / 'summary.json').write_text(json.dumps(report, indent=2))
    with (out / 'rankings.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=['query_index', 'caption', 'paired_image_id', 'paired_image_rank',
                                             'top1_image_id', 'top1_similarity', 'top5_image_ids'])
        writer.writeheader()
        for i, query in enumerate(queries):
            writer.writerow({'query_index': i, 'caption': query['text'], 'paired_image_id': query['image_id'],
                             'paired_image_rank': int(ranks[i]), 'top1_image_id': records[ranking[i,0]]['image_id'],
                             'top1_similarity': float(scores[i,ranking[i,0]]),
                             'top5_image_ids': '|'.join(records[j]['image_id'] for j in ranking[i,:5])})
    render_review(records, queries, {'scores': scores, 'ranking': ranking, 'ranks': ranks}, out / 'review.html')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=MODELS, required=True)
    parser.add_argument('--batch-size', type=int, default=8)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('batch size must be positive')
    run(args.model, args.batch_size)
