# Prompt and model improvement experiment

20 October 2026. The original prompt, raw outputs and evaluation artifacts were preserved. The same three development images and frozen evaluation rules were used. No remaining library images were processed.

## What changed

Added `image-products-v2`, leaving `image-products-v1` unchanged. The revised prompt:

- Requests category coverage, including supporting furniture, rather than repeated seating alone.
- Caps instances at 12 and explicitly says not to fill a quota (baseline cap: 16).
- Requires distinct physical instances and spatial descriptions for repeated objects.
- Clarifies tight object boundaries, excluding separate supports/stands from the named object.
- Explicitly checks y/x ordering and positive box area before output.

The temperature remains 0, output cap 4096, image preparation unchanged, and no campaign documents or gold labels are supplied. No automatic box repair, duplicate suppression or alias changes were applied. Review duplicate flags remain diagnostic only.

The analysis CLI now accepts explicit `--model` and `--prompt-version` arguments. Cache keys already include both, so an experiment cannot replace a baseline analysis. Reviews and evaluations accept variant-specific output paths to prevent mixed-model reports. Price guards were extended only to the explicitly requested Gemini 2.5 Flash comparison. Failures preserve any successful analysis of the same current source in the library's summary status; per-version failures remain separately recorded.

## Requests and reliability

| Arm | Attempted images | Schema-valid outputs | Outcome |
| --- | ---: | ---: | --- |
| Flash-Lite, original v1 baseline | 3 | 1 | Workspace valid; lounge invalid; tea API failure |
| Flash-Lite, revised v2 | 2 | 0 | Workspace and lounge HTTP 429; tea not attempted after stop |
| Flash, revised v2 | 3 | 3 | All three returned valid JSON and positive-area boxes |

The revised Flash-Lite lounge failure identified `limit_source=upstream_provider_shared_pool`. The API key still had credit available when checked. No failed request was automatically repeated, and no silent model fallback was introduced. The Flash run is the explicitly requested separate model arm, not a hidden retry of Flash-Lite.

**The prompt-only effect cannot be measured:** the Flash-Lite v2 arm produced no predictions. Differences between baseline and Flash combine a prompt revision with a model change. Three images are insufficient for a general model-quality claim.

## Detection results at the unchanged IoU threshold of 0.5

These are annotation-positive product-subset diagnostics, not official Open Images mAP. The original name aliases, class scope, group handling and matching algorithm were reused without changes.

| Image | Original Lite precision / recall | Revised Flash precision / recall | Original / revised matched IoU |
| --- | --- | --- | --- |
| Workspace | 66.7% / 50.0% | 66.7% / 50.0% | 0.728 / 0.818 |
| Lounge, baseline raw rejected output | 21.4% / 20.0% | 50.0% / 13.3% | 0.850 / 0.838 |
| Tea, baseline API failure | Not measurable | 100.0% / 25.0% | — / 0.971 |

For the **same two images with returned output in both arms** (workspace and lounge):

| Arm | TP | FP | FN | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original Lite v1 | 5 | 12 | 14 | 29.4% | 26.3% |
| Revised Flash v2 | 4 | 3 | 15 | 57.1% | 21.1% |

Thus, fewer false positives and better output validity did **not** improve object coverage. Precision rose while recall fell. The original lounge output remains rejected by the application; the table uses its raw predictions solely for research comparison.

Across all three Flash v2 outputs, TP=5, FP=3, FN=18 (62.5% precision, 21.7% recall). These totals are not directly comparable to the baseline's returned-output totals because baseline tea failed. Comparing actual accepted pipeline coverage across all three images gives 2/23 labelled products for the baseline and 5/23 for Flash v2, but detection recall is still weak.

## Visual inspection and limits

No Flash v2 response contained reversed/zero-area boxes or same-label overlapping pairs flagged by the existing review heuristic. This is an observed result on three inputs, not a guarantee that the prompt eliminated duplicates.

The lounge overlay is much less repetitive. It still labels a foreground sofa as an armchair and labels a coffee table as a side table. The workspace microphone box remains a poor match despite the tighter-box instruction, and the desk remains unpredicted. Matched-box IoU averages describe only successfully matched objects.

The frozen evaluator leaves labels such as `planter` and `tea bowl` outside its current aliases. Other class disagreements include a pale drinking vessel labelled Bowl by the dataset but tea cup by the model. These are exposed as unscored predictions/class mismatches rather than silently merged to raise the scores. Broadly correct visual descriptions are not the same as exact dataset-category detection. Any future taxonomy update should be versioned and rerun on every arm.

## Cost

Five new generation requests were attempted: three Flash requests and two rate-limited Flash-Lite requests. Reported Flash costs were $0.0035464, $0.0033214 and $0.0028214, totalling **$0.0096892**. Neither rate-limited request returned a charge, so their costs remain unknown rather than assumed zero. Public model/key status checks and all scoring/review steps were non-generation requests.

## Decision

Do not claim that detection is solved or process the remaining 17 images automatically. Flash v2 is a more reliable candidate for scene descriptions and human-reviewed suggestions, but current detection recall and category consistency are inadequate for unattended product cropping. Keep this baseline and compare the next change on a small fixed development set before an untouched-image check. No production default model was changed.

## Files and reproduction

- Prompt definitions: `app/llm/image_analysis.py`.
- Explicit model/prompt CLI: `integrations/vision/analyse.py`.
- Offline variant review: `integrations/vision/review.py` with `--model`, `--prompt-version`, `--tag`.
- Frozen scoring: `integrations/vision/evaluate.py` with `--review-path`, `--output-dir`.
- Offline comparison: `python -m integrations.vision.compare_pilot`.
- Full graph-ready CSV and comparison JSON: ignored `data/drive_sync/analysis/experiments/prompt_model_v2/`.
- Flash v2 visual comparison: ignored `data/drive_sync/analysis/evaluation/flash_v2/comparison.html`.
- Original visual comparison: ignored `data/drive_sync/analysis/evaluation/comparison.html`.

Targeted offline checks: **41 passed**, including preserved baseline contracts, separate prompt cache keys and refusal of invalid/excessive advertised prices. The pre-existing general application test-environment limitation documented in `VLM_PILOT_REPORT.md` remains; this task did not modify the application's server dependency versions.
