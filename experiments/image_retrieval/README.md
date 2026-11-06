# Standalone campaign image retrieval research

This module evaluates pretrained image/text encoders independently of FastAPI,
Google Drive, the dashboard, RAG, and image generation. It does not call a paid API.

## Frozen pilot

- Dataset: [Crossmodal-3600](https://google.github.io/crossmodal-3600/).
- Seed 42; five images per geographic locale, plus one extra from 20 seeded locales.
- 200 images in the candidate gallery; both original English captions for every
  image form 400 text queries. No generated captions or campaign paraphrases.
- Source photos are extracted from the official XM3600 archive. Full source
  attributions and licenses are retained in each manifest record.
- Sample membership is fixed before inference. No manual preference for easy images.
- All other 3,400 images remain outside this pilot and are not used for selection.
- No training, fine-tuning, threshold fitting, or prompt optimization.

This is an exploratory retrieval pilot, not a final independent test set. If its
results influence model choice, a later final evaluation must use different images.
Pretraining overlap with these public images has not been audited.

## Encoders

1. `laion/CLIP-ViT-B-32-laion2B-s34B-b79K`, loaded with OpenCLIP.
2. `google/siglip2-base-patch32-256`, loaded with Transformers.

Each repository revision is pinned on first download and recorded in its summary.
Default model-specific image preprocessing is used, with EXIF orientation handled
and RGB conversion. SigLIP text is lowercased and padded to 64 tokens. CLIP uses
its own tokenizer and 77-token context. Features are L2-normalized and compared
with dot products. Neither the captions nor image IDs are supplied to the image encoder.

## Run

Run from the repository root. The currently configured optional environment is:

```bash
PYTHON=/Users/omar/.codex/social-studio-detector-venv/bin/python
$PYTHON -m experiments.image_retrieval.prepare
$PYTHON -m experiments.image_retrieval.benchmark --model openclip
$PYTHON -m experiments.image_retrieval.benchmark --model siglip2
$PYTHON -m experiments.image_retrieval.summarize
```

For a new environment, install `experiments/image_retrieval/requirements.txt` there.
The dataset and weights download on first use. Both benchmarks use Apple MPS when
available, otherwise CPU. Batch size defaults to 8. Only one model is loaded per
command. Successful embeddings are cached with the image manifest hash, model
revision, and encoding version; changed inputs invalidate the cache.

## Files and interpretation

All bulky artifacts are Git-ignored under `data/image_retrieval/`:

- `raw/`: original captions, attribution CSV, official image archive.
- `sample_200/manifest.json`: frozen image IDs, 400 captions, SHA256s, source metadata.
- `sample_200/images/`: selected actual photos.
- `sample_200/gallery.html`: all selected images with both captions and attribution links.
- `models/`: pinned weights and revision metadata.
- `results/{model}/summary.json`: metrics, timings, parameter count, versions.
- `results/{model}/rankings.csv`: every caption, paired image rank, top five IDs.
- `results/{model}/review.html`: identical deterministic examples plus each model's
  worst ranks. These worst cases are selected after scoring and are not random examples.
- `results/{model}/embeddings.npz`: saved normalized image and text vectors.

Recall@K is the proportion of captions whose paired source image ranks in the top
K. MRR is the mean reciprocal source-image rank. Only source-image matches are
labelled: other matching images may also be relevant but are unjudged. A similarity
score is not a calibrated correctness probability. Metrics on 200 candidates must
not be compared directly with those on a larger gallery.

Image encoding and text encoding timings include preprocessing and batch work,
after one warmup per encoder. Ranking timing covers all 400 queries together.
They are descriptive measurements, not controlled latency benchmarks. Model load
and download time is recorded separately. Cache replays preserve the original
encoding timings.

These scene captions test image/text alignment, not marketing suitability or
exact brand/product identity. Campaign briefs need an additional relevance-labelled
set before a campaign-specific accuracy claim.

A small report and comparison CSV are also saved under `experiments/image_retrieval/results/`.

## Dataset attribution and runtime notes

The captions are from Thapliyal et al., *Crossmodal-3600: A Massively Multilingual
Multimodal Evaluation Dataset* (EMNLP 2022), distributed under CC BY 4.0. Original
photographer attribution and image license URLs are retained in the manifest and
gallery. Captions are unchanged.

Transformers 5.19.0 emits a warning while constructing SigLIP 2's default text
configuration (CLIP-style BOS/EOS defaults against a temporary 32k vocabulary).
The loaded checkpoint's actual vocabulary and tokenizer were both verified as
256,000 entries. Retrieval text pooling uses the last token, not those default
BOS/EOS IDs. The official tokenizer/preprocessing and all pretrained weights were
kept unchanged; no coordinate, score, or label corrections were applied.

To measure sequential warm searches after both benchmark runs:

```bash
HF_HUB_OFFLINE=1 $PYTHON -m experiments.image_retrieval.latency
$PYTHON -m experiments.image_retrieval.summarize
```

This uses 50 fixed captions, batch size 1, and five warmups per model. Raw timings
and p50/p95 are saved in `data/image_retrieval/results/{model}/latency.json`.
