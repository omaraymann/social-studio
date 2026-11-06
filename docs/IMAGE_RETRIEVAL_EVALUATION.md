# Image retrieval evaluation — 24 October 2026

**Status:** completed standalone zero-shot pilot. Automatic dashboard integration is described in [IMAGE_RETRIEVAL_WORKFLOW.md](IMAGE_RETRIEVAL_WORKFLOW.md).
The retrieval and latency numbers below are measured results; campaign rewrite/selector relevance evaluation remains pending.

Hardware: Apple M4, 16 GB unified memory, Apple GPU through PyTorch MPS. Optional
experiment environment: `/Users/omar/.codex/social-studio-detector-venv/bin/python`.
The backend, dashboard, and existing campaign workers were not used in this experiment.

Sample: 200 images, fixed before inference using seed 42: five images from each
of 36 geographic locales, plus one extra from 20 seeded locales. Both unchanged
human English captions per image supply 400 queries. Captions and photos come from
the [official Crossmodal-3600 release](https://google.github.io/crossmodal-3600/).
Photos were taken from its official archive, with attribution and license metadata retained.

**Provisional integration candidate:** SigLIP 2 Base patch32/256. It led this pilot
while remaining practical on the laptop. This is not proof of campaign suitability,
exact product identity recognition, or performance on a large production gallery.

## Measured retrieval results

200 fixed images, 400 original human English caption queries; seed 42. No training, tuning, generated labels, or paid inference. Each query searches all 200 images.

| Model | Parameters | Recall@1 | Recall@5 | Recall@10 | MRR | Image encoding | Text encoding |
|---|---:|---:|---:|---:|---:|---:|---:|
| laion/CLIP-ViT-B-32-laion2B-s34B-b79K | 151,277,313 | 80.25% | 96.00% | 98.25% | 0.874 | 4.35s | 4.50s |
| google/siglip2-base-patch32-256 | 376,856,066 | 84.75% | 98.25% | 99.50% | 0.907 | 4.89s | 6.42s |

Timings cover the whole gallery/query batch after warmup, including preprocessing. Download/load time is excluded. Apple MPS and default batch size 8 were used. These are descriptive timings, not a controlled performance benchmark.

Recall@K measures whether the paired source image occurs in the top K. With 200 images, random top-1 retrieval is 0.5%. Other candidate images may also match a caption but have no relevance label. This is not a campaign suitability score.

This small sample is exploratory. Models may have seen overlapping public images during pretraining; overlap has not been audited. Model selection using this pilot requires a separate later evaluation on different images. No thresholds were tuned.

Full rankings, model revisions, runtime versions, image hashes, embeddings, and offline visual reviews are retained under `data/image_retrieval/` (Git-ignored).

## Provenance

- Model: `laion/CLIP-ViT-B-32-laion2B-s34B-b79K`; revision: `1a25a446712ba5ee05982a381eed697ef9b435cf`.
  Manifest SHA256: `ab77d8088ece5bc58347a02e6fd2b48b5da2bf83280c9cf3ff6344fdc265bc3c`.
- Model: `google/siglip2-base-patch32-256`; revision: `94dffa8cb1179de3e03f091dbc3917e5d5a9ae84`.
  Manifest SHA256: `ab77d8088ece5bc58347a02e6fd2b48b5da2bf83280c9cf3ff6344fdc265bc3c`.

## Warm single-query latency

50 sequential caption searches, batch size 1, after 5 warmups per model. Includes tokenization, text encoding, normalization, GPU synchronization, CPU transfer, and ranking 200 cached image vectors. Excludes model load, image indexing, network, web server/frontend, and query rewriting. These are local measurements, not a production service guarantee.

| Model | Median | 95th percentile |
|---|---:|---:|
| laion/CLIP-ViT-B-32-laion2B-s34B-b79K | 12.95 ms | 22.68 ms |
| google/siglip2-base-patch32-256 | 24.08 ms | 34.90 ms |

## Saved artifacts and validation

- Code and setup: `experiments/image_retrieval/README.md`.
- Frozen IDs, captions, file hashes, and photographer metadata: `data/image_retrieval/sample_200/manifest.json`.
- All selected photos and captions: `data/image_retrieval/sample_200/gallery.html`.
- Every caption ranking and model summary: `data/image_retrieval/results/{openclip,siglip2}/`.
- Actual single-query timings: each model's `latency.json` (50 queries, five warmups).
- Visual review pages: each model's `review.html`, showing a common fixed selection plus worst-ranked queries.
- Small portable comparison: `experiments/image_retrieval/results/comparison.csv` and `REPORT.md`.
- Model repository revisions and checkpoint SHA256s are in the model summaries.
- The raw image archive SHA256 is in the frozen manifest.

Checks completed: image decoding and source hashes; 200 distinct image IDs; 400
captions; all 36 locales represented; ranking-metric check against known synthetic
ranks; all CSV Recall@K counts verified against summaries; cached replay reproduced
retrieval metrics; offline cached replay verified; Python compilation and Git whitespace checks passed.

Raw datasets, model weights, image embeddings, and full local outputs are excluded
from Git by `data/image_retrieval/`. No training, query rewriting, production relevance
threshold calibration, or paid inference was performed. No statement of statistical
significance is made from this small pilot.
