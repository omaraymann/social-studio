# Evaluation against existing Open Images annotations

20 October 2026. Evaluated saved `image-products-v1` outputs from `google/gemini-2.5-flash-lite`. This evaluation made **zero new model requests** and did not alter any predictions or ground-truth boxes.

## Results at IoU ≥ 0.5

These are class-normalized, annotation-scoped product-detection diagnostics. The lounge row scores its raw response for research even though the production validator rejected it. Tea returned no predictions, so detection precision and localization cannot be measured for that attempt.

| Scene | Gold product boxes | TP | FP | FN | Precision | Recall | Mean IoU of matched boxes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Workspace | 4 | 2 | 1 | 2 | 66.7% | 50.0% | 0.728 |
| Crowded lounge, raw rejected output | 15 | 3 | 11 | 12 | 21.4% | 20.0% | 0.850 |
| Tea arrangement, API failure | 4 | — | — | — | Not measurable | Not measurable | Not measurable |

For the two returned outputs combined: TP=5, FP=12, FN=14; micro precision=29.4%, micro recall=26.3%. This excludes the API-failed image and is not full-system accuracy. The high matched IoU on the lounge reflects only its three successful matches, not good overall detection.

The actual pipeline accepts only the workspace output. Across all three attempts, treating rejected/failed outputs as producing no usable detections yields TP=2, FP=1, FN=21, or 8.7% recall of the 23 in-scope gold instances. Precision is 66.7% among the three scorable accepted predictions, with nine additional predictions unscored. Only one of the three attempts produced an accepted analysis. Neither metric is an estimate of performance across the full 20-image library.

## What matched and failed

**Workspace:** the monitor matched at IoU 0.767; the bottle matched at 0.689. The microphone was identified, but its box included extra stand area and reached only 0.442, below the threshold. That counts as one FP and one FN. The desk was not predicted, adding another FN.

The other nine predictions—laptop, tablet, keyboard, lamp, wine glass, drinking glass, mouse, mousepad and coaster—are not scored in this image. Their classes do not have corresponding in-scope boxes here, or are not mapped into the fixed scope. Unscored does not mean correct or hallucinated. The dataset alone cannot resolve their detection precision using the loaded annotations.

**Lounge:** one sofa and two chairs matched, with IoUs 0.864, 0.763 and 0.924. Four reversed boxes count as false positives; seven additional unmatched chair predictions also count as false positives, including a duplicate competing for an already-matched chair. Two valid predictions lie inside an annotated chair group and are ignored under this diagnostic's group policy. Misses include three chairs, two sofas, two coffee tables, four houseplants and a flowerpot. The application still rejects this entire analysis.

**Tea:** source labels give one teapot and three bowls after removing a near-identical overlapping Kettle annotation for the same teapot. The API failure produced no predictions; there is nothing to compare for detection quality. Its end-to-end usable recall is zero, reported separately from raw model-output quality.

## Exact evaluation rules and limits

1. Read existing `reference_annotations.json`; no manual boxes or new image labels were created.
2. Check that the original image hash equals the source hash used for inference. The three inputs have no EXIF rotation, and prepared images preserve aspect ratio, so normalized source boxes and predicted boxes share axes.
3. Use a fixed product taxonomy, saved in full with the results. Examples: desktop computer monitor → monitor; wine bottle → bottle; armchair → chair; sofa → Couch. No labels are changed to force a match after looking at IoU.
4. Exclude broad/environment labels such as Furniture, Building, Office building, Office supplies and Tableware. Use Couch rather than the redundant Studio couch subtype and Houseplant rather than Plant. This keeps the diagnostic within the physical-product task and avoids counting parent/subtype boxes twice. Overlapping Kettle/Teapot boxes with IoU ≥ 0.9 are collapsed by preferring Teapot.
5. Match same-class, positive-area boxes one-to-one at IoU ≥ 0.5. Assignment maximizes the number of matches first and summed IoU second. Each gold instance can be claimed only once. A poorly localized prediction creates an FP and leaves its gold instance as an FN. Invalid boxes in annotated classes are FPs, not silently repaired.
6. Predictions of classes without a loaded positive annotation remain unscored. Verified negative image-level labels were not downloaded, so this evaluation does not classify every remaining detection as false positive. Open Images explicitly treats unannotated classes separately from negative classes in its [evaluation protocol](https://storage.googleapis.com/openimages/web/evaluation.html).
7. Exclude group-of boxes from individual-instance recall; ignore unmatched valid predictions whose same-class group covers at least 50% of their predicted area. Do not award a group TP. This is a documented simplification of the official group protocol.

**This is not official Open Images mAP.** There is no confidence-ranked AP curve, full hierarchy expansion or official group-of weighting. Scene descriptions, attributes, topics and eventual image retrieval were not scored by this box evaluation. These results expose current detection problems; they do not support a strong accuracy claim.

## Saved artifacts and repeatability

- Code: `integrations/vision/evaluate.py`
- Offline run: `/Users/omar/.codex/flyrank-review-venv/bin/python -m integrations.vision.evaluate --owner-id 1`
- Full per-prediction matches, IoUs, FPs, FNs, unscored detections, aliases and annotation exclusions: ignored `data/drive_sync/analysis/evaluation/pilot_evaluation.json`
- Graph-ready table: ignored `data/drive_sync/analysis/evaluation/per_image.csv`
- Side-by-side source boxes versus predictions: ignored `data/drive_sync/analysis/evaluation/comparison.html`

Targeted offline checks: **37 passed**, including the prior pipeline/sync checks plus matching cardinality, aliases, duplicate penalties, invalid-box penalties, unannotated-class handling, group-box handling and overlapping Kettle/Teapot deduplication. The general application test-environment limitation noted in `VLM_PILOT_REPORT.md` remains unchanged.
