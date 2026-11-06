# Crossmodal-3600 retrieval pilot

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
