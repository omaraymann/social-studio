# Local YOLO comparison: in-domain diagnostic

22 October 2026. Ran `yolov8n-oiv7.pt` on the exact three prepared images used for the saved VLM experiment. No new training, fine-tuning, threshold sweep, Drive calls or OpenRouter generation calls were performed. No production model was selected or changed.

## Results

The existing evaluation was reused without changing its class aliases, product scope, group policy or IoU ≥ 0.5 matching. These are annotation-positive product-subset diagnostics, not official Open Images mAP or full-scene detection precision.

| Scene | YOLO TP / FP / FN | YOLO precision / recall | Flash v2 precision / recall | YOLO matched-box IoU |
| --- | --- | --- | --- | ---: |
| Workspace | 1 / 0 / 3 | 100.0% / 25.0% | 66.7% / 50.0% | 0.670 |
| Crowded lounge | 3 / 1 / 12 | 75.0% / 20.0% | 50.0% / 13.3% | 0.772 |
| Tea arrangement | 2 / 0 / 2 | 100.0% / 50.0% | 100.0% / 25.0% | 0.922 |
| **All three, micro aggregate** | **6 / 1 / 17** | **85.7% / 26.1%** | **62.5% / 21.7%** | — |

Both YOLO and Flash v2 returned output for all three images, so these aggregate denominators match: 23 in-scope individual gold boxes. The original Flash-Lite baseline had an API failure on tea and cannot be directly compared using its returned-output aggregate over only two images.

YOLO found six of 23 labelled products and missed 17. It made fewer scorable false detections than Flash v2, but the recall improvement is small. It performed worse on workspace recall. It is not a complete solution to product detection.

## What the detector actually produced

- Workspace: monitor and keyboard. Only the monitor is scored because this image has no keyboard box in the loaded in-scope annotations. Bottle, microphone and desk were missed.
- Lounge: four chair detections, one couch and a broad Building detection. Three instances matched individual labels. Two foreground chair boxes compete for the same labelled chair, creating one duplicate FP. One other chair detection is ignored inside the group-of annotation. Building is outside the product scope. Twelve in-scope instances were missed.
- Tea: two Bowl detections plus Food and Tea detections. The bowls matched; Food and Tea are outside the fixed product scope. The teapot and another bowl were missed.

There are 12 total predictions: six matches, one scorable false positive, one group-ignored chair and four unscored predictions. High precision here does not establish that all other visible products were detected or all unscored predictions are correct.

## Training-data concern

This is an **in-domain test**. The checkpoint is explicitly pretrained on Open Images and outputs its native category names, giving it a vocabulary/distribution advantage on our Open Images annotations. The checkpoint metadata records the Open Images V7 dataset configuration. The three source images are from the dataset's validation split; the official dataset configuration describes separate training and validation splits. [Ultralytics documentation](https://docs.ultralytics.com/datasets/detect/open-images-v7/).

That does not, by itself, prove these exact image IDs were absent from every stage of checkpoint development, parameter selection or pretraining. We did not obtain or audit its complete image-level training manifest. No unseen-data or dataset-independent generalization claim is made. This distinction is why the result must not be used alone to justify trusting the model on campaign photos.

Before choosing a model, freeze the candidate settings and evaluate on independent, realistic campaign/product images not used for prompt or threshold tuning, ideally newly captured photos with human-checked boxes. Keep selection/tuning images separate from that final check. We have not collected or evaluated that separate set in this task.

## Reproducible setup

- Checkpoint: `yolov8n-oiv7.pt`, downloaded from the official Ultralytics assets v8.4.0 release.
- Checkpoint training metadata: Ultralytics 8.0.157, checkpoint date 2023-08-22; dataset `/usr/src/ultralytics/ultralytics/cfg/datasets/open-images-v7.yaml`.
- Inference runtime: Ultralytics 8.4.183, PyTorch 2.14.1, torchvision 0.29.1, OpenCV 5.0.0.93.
- Device: CPU; detector input size 640; confidence threshold 0.25; NMS IoU 0.70; class-aware NMS; max detections 300; augmentation off; seed 0. These were selected before inference and not adjusted after seeing the results.
- Source raster: exact SHA-256-verified 1024-edge, EXIF-oriented prepared PNG from each original VLM attempt. YOLO maps letterboxed results back to that raster. The adapter converts XYXY pixels to the common normalized YXYX format.
- Checkpoint SHA-256, package versions, source/prepared hashes, settings, raw class IDs, confidence values and pixel boxes are saved in the ignored per-image artifacts.
- All model classes were available to inference; gold annotation classes were not passed to the detector. Scope filtering happens only during scoring.

Model loading took approximately 0.04 seconds; the first prediction included cold initialization and took approximately 4.09 seconds. Later predictions took approximately 0.045 seconds each. These three timings are not a controlled hardware benchmark.

The detector was installed in `/Users/omar/.codex/social-studio-detector-venv`, which reads existing base packages through a local `.pth` file and adds detector packages separately. The application's requirements and base environment were not changed. Wheel SHA-256 digests were checked against PyPI metadata. Local checkpoints, downloaded wheels, prepared images and results are all under the Git-ignored `data/drive_sync/` directory.

## Files

- Runner: `integrations/vision/yolo_pilot.py`.
- Optional dependency: `integrations/vision/requirements-detector.txt`.
- Run: `/Users/omar/.codex/social-studio-detector-venv/bin/python -m integrations.vision.yolo_pilot --owner-id 1`.
- Raw detections and provenance: `data/drive_sync/analysis/detector/yolov8n_oiv7_img640_conf025_nms070_cpu_v1/<image-id>/`.
- Metrics and graph-ready CSV: the same run directory's `evaluation/pilot_evaluation.json` and `evaluation/per_image.csv`.
- Side-by-side source-label/prediction review: the same `evaluation/comparison.html`.

A second live run verified all three outputs were reused from cache when input hashes, checkpoint hash, package versions and settings matched. No detector output is substituted for the VLM's descriptions, topics or database records. The other 17 images remain unprocessed.

Targeted offline integration checks: **46 passed**, including the detector coordinate conversion and rejection of invalid geometry. The pre-existing full application test-environment limitation remains as documented in `VLM_PILOT_REPORT.md`.
