"""Collect saved pilot results into a compact CSV and readable report."""
import csv
import json
from pathlib import Path
from .prepare import DATA


def summarize():
    summaries = [json.loads(p.read_text()) for p in sorted((DATA / 'results').glob('*/summary.json'))]
    destination = Path(__file__).resolve().parent / 'results'
    destination.mkdir(exist_ok=True)
    fields = ['repo', 'revision', 'parameters', 'device', 'image_count', 'query_count',
              'recall@1', 'recall@5', 'recall@10', 'mrr', 'image_encoding_seconds', 'text_encoding_seconds',
              'ranking_seconds', 'embedding_dimension', 'manifest_sha256']
    with (destination / 'comparison.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for summary in summaries:
            writer.writerow({**summary, **summary['metrics']})
    lines = ['# Crossmodal-3600 retrieval pilot', '',
             '200 fixed images, 400 original human English caption queries; seed 42. '
             'No training, tuning, generated labels, or paid inference. Each query searches all 200 images.', '',
             '| Model | Parameters | Recall@1 | Recall@5 | Recall@10 | MRR | Image encoding | Text encoding |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for summary in summaries:
        m = summary['metrics']
        lines.append(f'| {summary["repo"]} | {summary["parameters"]:,} | {m["recall@1"]:.2%} | '
                     f'{m["recall@5"]:.2%} | {m["recall@10"]:.2%} | {m["mrr"]:.3f} | '
                     f'{summary["image_encoding_seconds"]:.2f}s | {summary["text_encoding_seconds"]:.2f}s |')
    lines.extend(['', 'Timings cover the whole gallery/query batch after warmup, including preprocessing. '
                  'Download/load time is excluded. Apple MPS and default batch size 8 were used. '
                  'These are descriptive timings, not a controlled performance benchmark.', '',
                  'Recall@K measures whether the paired source image occurs in the top K. '
                  'With 200 images, random top-1 retrieval is 0.5%. Other candidate images may also '
                  'match a caption but have no relevance label. This is not a campaign suitability score.', '',
                  'This small sample is exploratory. Models may have seen overlapping public images '
                  'during pretraining; overlap has not been audited. Model selection using this pilot '
                  'requires a separate later evaluation on different images. No thresholds were tuned.', '',
                  'Full rankings, model revisions, runtime versions, image hashes, embeddings, and '
                  'offline visual reviews are retained under `data/image_retrieval/` (Git-ignored).', '',
                  '## Provenance', ''])
    for summary in summaries:
        lines.extend([f'- Model: `{summary["repo"]}`; revision: `{summary["revision"]}`.',
                      f'  Manifest SHA256: `{summary["manifest_sha256"]}`.'])
    latency_paths = sorted((DATA / 'results').glob('*/latency.json'))
    if latency_paths:
        lines.extend(['', '## Warm single-query latency', '',
                      '50 sequential caption searches, batch size 1, after 5 warmups per model. '
                      'Includes tokenization, text encoding, normalization, GPU synchronization, '
                      'CPU transfer, and ranking 200 cached image vectors. Excludes model load, '
                      'image indexing, network, web server/frontend, and query rewriting. '
                      'These are local measurements, not a production service guarantee.', '',
                      '| Model | Median | 95th percentile |', '|---|---:|---:|'])
        for path in latency_paths:
            latency = json.loads(path.read_text())
            p = latency['percentiles']['total_ms']
            lines.append(f'| {latency["repo"]} | {p["p50"]:.2f} ms | {p["p95"]:.2f} ms |')
    (destination / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    summarize()
