# Initial image-understanding pilot

20 October 2026. Three image-only OpenRouter requests using `google/gemini-2.5-flash-lite`, prompt `image-products-v1`, preparation `exif-transpose-rgb-1024-v1`. No campaign documents or expected image/product labels were sent. This is a small diagnostic pilot, not an accuracy benchmark.

| Scene | Result | Reported charge (USD) |
| --- | --- | ---: |
| Cluttered workspace | Schema-valid, 12 predicted product instances | 0.0006071 |
| Crowded furnished lounge | Rejected: repeated chairs and four reversed boxes | 0.0007171 |
| Tea arrangement | API/provider request failed; no analysis returned | Unknown |

Known charges total **$0.0013242**. The third charge is not known and is not assumed to be zero. Exactly three generation requests were attempted. Re-running the pilot reused the workspace result, did not retry the failed lounge, and made only the previously unattempted tea request. No further paid requests were made.

## Observations

The workspace output found a monitor, laptop, tablet, keyboard, microphone, lamp, bottle, glasses, mouse, mousepad and coaster. Visual inspection of the overlay found generally plausible placement, but the mousepad box is loose, the coaster box is questionable, and the desk itself was omitted. This is not a declaration that all 12 detections are correct.

The lounge returned 16 entries, many repeated armchairs with progressively shifting x coordinates. Four boxes have xmin greater than xmax. The validator rejected the entire analysis. The local review page can display only the geometrically valid predictions for diagnosis; this does not promote the failed result into accepted data. Duplicate-box flags are heuristic review aids, not gold labels or automatic correction.

The tea request failed before the request helper returned a response body. The original logger retained only the exception type, so the exact HTTP/provider cause and charge could not be recovered from these artifacts. Error logging was subsequently improved to preserve sanitized exception messages, HTTP status and provider request/error IDs where available. It does not persist request headers, credentials or encoded image payloads. The request was not repeated.

## Implemented

- Image-only structured prompt and strict validation.
- Versioned PostgreSQL analysis cache and explicit attempt states.
- Three-image limit, price/capability preflight and no automatic retries/fallbacks.
- Saved raw responses, usage/cost and prepared-image provenance.
- Offline HTML review and predicted-box overlays.
- Source-hash checks and shared per-user sync/analysis locking.

Final database state for the 20-image library: one image marked completed, two marked error and 17 still pending. Completed means structurally validated, not human-approved. Failed results are not accepted for selection.

Targeted offline checks: **30 passed** across image-analysis and Google Drive integrations, including JSON/box rejection, image orientation, coordinate conversion, cache reuse, failed-attempt retry prevention, error metadata redaction and previous sync checks. The general application test suite could not load in the local integration virtual environment because its FastAPI/Starlette versions are incompatible; the targeted tests avoid importing the application server. No application-server dependency versions were changed during this task.

## Decision

Keep the remaining images unprocessed. Inspect the saved predictions and improve duplicate/box behavior before scaling. A separately recorded prompt revision or model comparison should use a fixed small development subset, retain this baseline, and get human-reviewed instance labels before reporting IoU, precision or recall. Do not infer performance from schema validity alone.

Review artifacts: local ignored `data/drive_sync/analysis/pilot_review_1.html` and its JSON companion. Implementation and run commands: `integrations/vision/README.md`.

References: [OpenRouter model pricing](https://openrouter.ai/google/gemini-2.5-flash-lite/pricing), [Google image understanding and box coordinate convention](https://ai.google.dev/gemini-api/docs/image-understanding).
