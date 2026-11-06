# Image-only VLM analysis pilot

This adds product/scene understanding to cached Drive photos. It does not yet add image embeddings, image retrieval, a dashboard panel, orchestration, cropping for generation or automatic use of predicted boxes.

## Run and review

From the repository root, use the existing Python environment:

```bash
/Users/omar/.codex/flyrank-review-venv/bin/python -m integrations.vision.analyse \
  --owner-id 1 \
  --file-id 1HcLPfi0X089BBm6vNawDhDMnFSjTdlvZ \
  --file-id 15HDBR6DM0Jc8MaiQshFCPzEls9yOgFsA \
  --file-id 1qDr3OQ7f3yMkAaLRAybBHBjSEGv5P-68
```

This selects the workspace, crowded lounge and tea arrangement used in the initial pilot. Only one to three explicit currently synced image IDs are accepted. Use the correct existing dashboard owner ID. This local CLI is an administrator workflow; it is not an authenticated dashboard endpoint. `OPENROUTER_API_KEY` is read through the existing settings, never printed or included in artifacts.

```bash
/Users/omar/.codex/flyrank-review-venv/bin/python -m integrations.vision.review --owner-id 1
```

The second command is offline: it reads saved PostgreSQL records and local artifacts, makes no model/API requests, and writes `data/drive_sync/analysis/pilot_review_1.html`. Open that HTML file to inspect predictions. Failed responses may have diagnostic overlays, but remain unavailable as accepted analyses. Possible duplicate boxes are flagged for inspection, not automatically removed.

## Model and costs

The initial low-cost model is `google/gemini-2.5-flash-lite`. The code verifies current public OpenRouter model metadata and refuses to proceed if image/structured-output support or the price guard fails. Recorded rates on 20 October 2026 were $0.10/M input tokens and $0.40/M output tokens. There is no automatic fallback to another model. The prompt uses temperature 0, disables reasoning and caps output at 4096 tokens. These are pilot settings, not tuned optimums.

The workflow makes at most one request for each selected analysis key, with no automatic retry. It stops further new calls if a charge is missing or a single reported charge exceeds $0.01. This is an after-response guard, not a provider-enforced total dollar limit. A failed or interrupted request may have consumed credits; unknown cost is never reported as zero. Public model-metadata GET requests are not generation calls.

## Evidence and coordinates

The model sees only a prepared image and a fixed image-analysis prompt. No filenames, campaign documents, retrieval results, manual image descriptions, expected matches or source detection labels are sent. The output schema includes scene description, topics, physical product instances, visible attributes, uncertainties and boxes. It excludes guesses about verified product identity, prices, specifications or offers. Scene/product content is model-generated evidence, not proof of those facts.

Images are EXIF-oriented, converted to RGB, resized without stretching to a maximum 1024-pixel edge, and saved as fresh PNG pixels without metadata. The model's `box_2d` uses integer `[ymin,xmin,ymax,xmax]` coordinates from 0 to 1000 on that prepared raster. `pixel_box` converts these to half-open `[left,top,right,bottom]` crop coordinates. Any future crop must use the same oriented raster or a correctly mapped equivalent, not unrotated original pixel coordinates.

Pydantic rejects malformed output, incomplete responses, unknown fields, non-integer/out-of-range/reversed/zero-area boxes and excessive objects. These checks establish a valid structure, not accurate localization. A schema-valid result can still omit products, hallucinate objects, give loose boxes or repeat detections. Human correction and gold-label evaluation remain necessary.

## Persistence and reuse

`rag.image_analyses` stores analysis JSON, usage, reported cost, response ID, status, image dimensions and artifact paths. Its cache key is:

`owner + Drive file ID + source SHA-256 + model ID + prompt version + preparation version`

A successful key is reused without another model call. Failed/processing keys are retained and blocked from automatic retry to prevent duplicate spending after uncertain failures. The workflow takes the same per-user advisory lock as Drive sync. A conditional source-hash update prevents an old result from marking a changed source completed. If image bytes change, sync marks them pending and the new hash forms a separate analysis key. Changing the prompt or preparation requires explicitly bumping its version.

Artifacts are under the Git-ignored `data/drive_sync/analysis/` directory: prepared image, prompt, provenance, raw provider response where available, validated analysis, box overlay, error diagnostics for new failures, and review notes. They contain no API key, Authorization headers or request data URLs. Original response text and images remain local research data. Model errors are not silently repaired; raw output is retained.

## Pilot result

See `docs/VLM_PILOT_REPORT.md`. The first run produced one schema-valid result, one invalid repeated/reversed-box result, and one provider/API failure. It is not ready to process the remaining 17 images or automatically choose crops. No automatic model/prompt tuning or retries were performed.

## Evaluate saved predictions using the existing labels

```bash
/Users/omar/.codex/flyrank-review-venv/bin/python -m integrations.vision.evaluate --owner-id 1
```

This is offline and makes no model calls. It reads the saved review records and original Open Images annotations, checks source-image identity/orientation, normalizes defined label aliases, and performs one-to-one IoU matching at 0.5. Raw failed output can be scored diagnostically without promoting it into accepted pipeline data. Unannotated classes remain unscored; group boxes and redundant category annotations follow the explicit policy in `docs/VLM_PILOT_EVALUATION.md`. This is a product-subset diagnostic, not official Open Images mAP. Results, per-image CSV and side-by-side HTML comparisons are saved under the ignored `data/drive_sync/analysis/evaluation/` directory. No evaluation labels are sent to the VLM or added to RAG.

## Explicit prompt/model experiments

The default prompt/model remains the original baseline. An explicitly requested comparison added `image-products-v2` and allows `google/gemini-2.5-flash` under a separate price guard. For example:

```bash
python -m integrations.vision.analyse --owner-id 1 --model google/gemini-2.5-flash \
  --prompt-version image-products-v2 --file-id YOUR_SYNCED_IMAGE_ID
python -m integrations.vision.review --owner-id 1 --model google/gemini-2.5-flash \
  --prompt-version image-products-v2 --tag flash_v2
python -m integrations.vision.evaluate --owner-id 1 \
  --review-path data/drive_sync/analysis/pilot_review_1_flash_v2.json \
  --output-dir data/drive_sync/analysis/evaluation/flash_v2
python -m integrations.vision.compare_pilot
```

Failed or processing analyses remain blocked from automatic retries. Review defaults now filter the original model and prompt rather than combining experiment arms; custom variants get separate tagged files. Evaluations refuse processing attempts. A failed experiment does not invalidate an already successful analysis of the same source bytes; the summary `completed` flag means a successful version exists, not that every experiment succeeded.

The comparison's revised Flash-Lite arm was rate-limited, while Flash returned valid results but still missed many labelled products. See `docs/VLM_IMPROVEMENT_EXPERIMENT.md` for exact scores, cost and limits. No prompt-only improvement or production-readiness claim is supported by this experiment.

## Local pretrained YOLO diagnostic

A separate optional environment contains `ultralytics==8.4.183` and its detector dependencies. The application environment and root requirements are unchanged. The local environment reuses the base project's installed packages through a `.pth` entry; the optional requirements file is intended for an environment that already has the project's/integration dependencies.

```bash
/Users/omar/.codex/social-studio-detector-venv/bin/python -m integrations.vision.yolo_pilot --owner-id 1
```

The runner uses `yolov8n-oiv7.pt` on the exact three original prepared VLM rasters, not the entire 20-image library. Settings are fixed before inference: CPU, 640-pixel input, confidence 0.25, class-aware NMS IoU 0.70. It stores all returned classes/confidences, pixel and normalized boxes, source/checkpoint hashes and package versions under the ignored `analysis/detector/` directory. Identical runs reuse saved detections.

The existing evaluator accepts a separate `detector_output_path`; local results are not disguised as provider responses and are not inserted into the VLM analysis table. Coordinate-conversion tests run without importing or downloading Ultralytics. No paid API calls are made.

Results, provenance and training-distribution limitations are in `docs/YOLO_PILOT_REPORT.md`. This is an Open Images–pretrained model evaluated on a few Open Images validation examples: an in-domain diagnostic, not proof of generalization to independently collected campaign images. Exact training-image membership was not independently audited. No deployment/model-selection decision should rely on these three scores alone.
