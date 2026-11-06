# Independent library and Drive sync verification

Verified locally on 20 October 2026. This is workflow verification and two retrieval smoke checks, not a detection or retrieval benchmark. No paid model calls were made.

## Data revision

The 20 campaign collection documents and four reference notes were updated in place on Drive. Documents no longer contain scene IDs, image filenames, photo credits linking to a specific source image, or prescribed image selections. Image filenames now use opaque original image hashes. Product content remains explicitly fictional demonstration data. Original photo attribution is retained in the local research manifest.

Expected associations exist only in the ignored `data/vision/matched_campaigns/evaluation/expected_matches.json`. The old corpus is archived locally outside the selected runtime directories. Neither these evaluation associations nor the Open Images bounding-box annotations were ingested into RAG.

## Live sync

The configured folders contain 24 documents and 20 images. All 44 downloaded files matched the revised local names and SHA-256 checksums.

| Check | Result |
| --- | --- |
| First sync | 24 documents indexed; 20 images cached |
| Repeat sync | All 44 skipped as unchanged; no downloads or re-embedding |
| Failed/unsupported/missing files | 0 |
| Drive RAG chunks / embeddings | 61 / 61 |
| Image analysis status | All 20 pending VLM |
| Runtime document checks | No old scene IDs, image filenames or explicit photo mappings |
| Paid model calls | 0 |

The CLI uses the existing single dashboard user, ID 1. Documents are stored in that user's existing `social_studio_user_1` dataset with stable Drive-based document IDs. Existing manually uploaded sources are preserved. Sync metadata is persisted in `rag.drive_sync_files`; cached source bytes remain under the Git-ignored `data/drive_sync/` directory.

## Update safety

A temporary database fixture was indexed with nine chunks, then shortened and re-indexed. Exactly one chunk remained. The fixture was rolled back and confirmed absent afterwards. This verifies replacement removes stale trailing chunks without leaving test data behind.

Offline integration checks: **18 passed**. They cover the original upload helpers, independent document migration, recursive/paginated listing, strict response parsing, new and unchanged files, timestamp-only changes, changed image invalidation, cache corruption recovery, failed-index retries, and failed-listing removal protection.

## Topic-based retrieval smoke checks

These use ordinary text queries with no scene ID or image filename. For inspection, the smoke script requested the top three chunks with no similarity cutoff; these are not benchmark scores. The platform's default 0.70 cutoff remains unchanged.

| Query | Highest-ranked document | Similarity |
| --- | --- | --- |
| Study workspace for students with a desk lamp, monitor and keyboard | evening_media_workspace | 0.7066 |
| Tea campaign featuring a dark teapot and a pale cup | dark_teapot_and_cup | 0.7853 |

Full local smoke outputs and database counts are saved under the ignored `data/drive_sync/` directory.

## Current scope

Sync is a manual CLI workflow using the locally authorized Drive MCP account. It does not yet run automatically during campaign generation, have a dashboard control, or provide multi-user Google OAuth. It does not perform VLM analysis, create image embeddings, select images or orchestrate generation. Those are subsequent steps. See `integrations/google_drive/README.md` for commands, file limits, removal behavior and retry rules.
