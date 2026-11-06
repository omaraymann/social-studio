# Social Media Studio

A backend that turns one published article into platform-specific social drafts, sends them through human review, schedules approved content, and publishes it reliably.

## Baseline status

The five baseline phases are complete. Phase 6 adds optional LLM-powered campaign generation through OpenRouter while preserving deterministic generation for offline and zero-cost use.

```text
Article -> source post -> X/LinkedIn variants -> validation -> approval
        -> schedule -> durable worker -> publisher adapter -> attempt history
```

The included adapters are Mock X, Mock LinkedIn, and a real Discord webhook. The mocks make the complete workflow testable without external accounts; Discord demonstrates a real platform delivery.

## Run locally

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Then open the React dashboard at `http://localhost:5173`. Swagger remains available at `http://localhost:8000/docs`. The dashboard, API, background worker, and migration runner operate as separate services against PostgreSQL.

## Product photos and campaign visuals

In the campaign composer, turn on **Add product pictures** to attach reference
photos, and tick **Generate campaign images** for automatic images alongside
the selected platform drafts. Photos are optional; without them, the model plans
a topic illustration. Use photos of the same product. Automatic generation uses
the cheaper **Nano Banana (Gemini 2.5 Flash Image)** and accepts at most three
reference photos. Text-only generation can accept up to four photos.

The selected campaign model (Gemini 2.5 Flash or Gemini 2.5 Pro) receives the source,
photos, audience, goal, tone, CTA, visual preferences, and optional RAG passages.
In the same structured response, it returns the platform drafts plus one visual
brief, headline, and image CTA per platform. The backend validates that response
and automatically sends each brief and the reference photos to the image model.
Planning is included in the campaign generation call; there is no additional
planning call per automatic image. Each selected platform adds one paid image call.

Our initial design presets are X landscape 16:9, LinkedIn square 1:1, and Discord
landscape 16:9. These are application choices rather than platform upload limits.
Each plan includes guidance for that platform's audience and presentation. Images
are linked to their campaign and platform, and retain the corresponding draft and
RAG evidence snapshot. A successful automatic image is composed with its headline
and CTA and attached directly inside that platform's post card. Review the text
and image together before approval. An image failure does not remove the completed
text drafts, but approval waits until an image is selected or **Use text only** is
explicitly chosen.

Inside a post, expand **Change image or edit image text** (or **Choose or generate
a post image**) to select another attempt using **Use this image in post**, edit
the image text, or expand **Regenerate an image or try other settings**. A manual regeneration
makes a separate paid planning call and image call. Its planner uses the saved
source, draft, and latest successful generation's RAG evidence without a new search.
Review all outputs for changes to product appearance.

The headline and CTA remain editable before post approval. **Save image text**
updates the attached draft's composed image. **Download exact post image** exports
the exact bytes that scheduling will preserve and publishing will send. The
candidate editor also offers a browser-rendered preview, whose font rendering may
differ slightly from the final server-composed post image. Generate another attempt with the other image model to compare
results. Saved attempts retain their reference IDs, campaign/evidence snapshot,
brief, model IDs, reported costs per stage, latency, and any failure. Unknown costs
are displayed as unknown. These costs are shown on visual cards and are separate
from the workspace's campaign generation totals. For automatic images, the visual
card identifies planning as included in the campaign call to avoid double counting.

The visual reference picker enforces the selected model's limit before paid
planning begins: Nano Banana accepts up to three photos; Nano Banana 2 supports
up to four in this prototype. Initially only one attached photo is selected.
Use views of the same product rather than photos of different products.

Apply migrations through `0012_post_media_and_rag_cutoff` with `alembic upgrade head` and restart the
API. Install the updated requirements (including Pillow). `IMAGE_STORAGE_DIR`
defaults to `data/campaign_images`, excluded from Git; keep this directory on
persistent storage alongside the database. The existing Docker bind mount retains
these files locally. Files are served only through authenticated, owner-scoped
endpoints; database records store file keys, not image bytes or base64 inputs.

Uploads are validated by decoding their actual contents, restricted to still
JPEG/PNG/WebP images of at most 10 MB and 25 million pixels. Original bytes are
retained locally. Working copies correct EXIF orientation, preserve aspect ratio
and alpha, strip metadata, and downscale to at most 1600 pixels on the longest edge
without cropping or upscaling. This size is an application setting, not a model
limit. Generated raster outputs are decoded and validated before storage.

Jobs run as FastAPI background tasks in this prototype. Status and stage persist,
but jobs do not survive an API restart; interrupted jobs become failed after ten
minutes, allowing an explicit new attempt. There is no automatic paid retry.
Scheduling freezes an immutable composed image and checksum with the text post.
Delivery retries use the same image snapshot and stop if it is missing or changed;
they never silently fall back to text only. Image selection and caption changes
are blocked after approval. Discord sends text and the PNG together as a multipart
webhook request. Its uncertain outcomes (e.g. a read timeout after sending) stop
for manual reconciliation rather than automatically posting duplicates. X and
LinkedIn remain explicitly labeled mock publishers; real account/OAuth adapters
are still needed for live posting there. Canva integration is a future extension.

Timed delivery requires a running worker. With the existing local virtualenv and
Docker PostgreSQL, start it in a separate terminal from the repository root:

```bash
/Users/omar/.codex/flyrank-review-venv/bin/python -m scripts.run_local worker
```

This worker can deliver already-due schedules, including live Discord posts.
`python -m scripts.run_local api` and `python -m scripts.run_local migrate` also
adapt the Docker database hostname for local execution without changing `.env`.
In a full Docker deployment, run the existing `worker` service and share persistent
`IMAGE_STORAGE_DIR` with the API.

Starter reference photos and product descriptions are documented in
[data/product_images/abo/README.md](data/product_images/abo/README.md).
For the first test, pair a single product photo with its matching source text and
leave RAG off unless relevant product documents have been indexed.

## Campaign knowledge base (RAG)

Retrieval now filters the top-k results using a configurable **minimum cosine
similarity** (default `0.70`). Preview and generation use the same cutoff; rejected
passages never enter the campaign prompt or automatic visual plans. If none pass,
generation continues using the supplied source only, and the audit records an
empty context and the cutoff. Database failures still surface as failures.
The dashboard displays raw cosine scores rather than relevance percentages.
`0.70` is a provisional application setting, not a calibrated relevance probability.
Validate it with relevant and irrelevant documents from the intended campaign domain.

Start the separate pgvector database and initialize its schema before using document upload:

```bash
[ -f rag/.env ] || cp rag/.env.example rag/.env
# Set the same RAG_DB_PASSWORD in rag/.env and the root .env
docker compose --env-file rag/.env -f rag/docker-compose.yml up -d --wait
docker compose --env-file rag/.env -f rag/docker-compose.yml exec -T db psql -U flyrank_rag -d flyrank_rag -v ON_ERROR_STOP=1 < rag/sql/init.sql
```

Install the updated Python dependencies and restart the API. In the dashboard, upload PDF, TXT, or Markdown files in **Campaign knowledge**. The live job panel reports text extraction, BGE-small chunking, embedding, pgvector indexing, and worker RAM. Documents are isolated by authenticated user in the RAG database. In the campaign composer, turn on **Use retrieved knowledge (RAG)**, optionally select documents, and preview matching passages before generation. Leaving it off keeps the existing source-only campaign flow. The API container connects through `host.docker.internal`; a local API process uses `127.0.0.1` from the root `.env`.

The starter source corpus and provenance manifest are in [rag/data/raw/social_studio_eval](rag/data/raw/social_studio_eval/README.md). The generation model receives retrieved passages as supporting context; the dashboard shows each passage and similarity, while human review remains part of campaign approval.

To create a ready-to-review demo campaign:

```powershell
python scripts/seed_demo.py
```

## Main API workflow

| Step | Endpoint | Result |
|---|---|---|
| Sign up | `POST /auth/signup` | Creates the campaign owner |
| Add content | `POST /posts` | Stores the source article |
| Generate | `POST /posts/{id}/variants` | Produces distinct X and LinkedIn drafts |
| Audit AI | `GET /posts/{id}/generations` | Shows model settings, tokens, cost, latency, repairs, and outcome |
| Review | `PATCH /variants/{id}`, `POST /variants/{id}/approve` | Human edits and approves each draft |
| Schedule | `POST /variants/{id}/schedule` | Queues approved content for a UTC time |
| Observe | `GET /schedules/{id}/history` | Shows every processing attempt and outcome |
| Retry | `POST /schedules/{id}/retry` | Safely requeues a failed delivery when allowed |

## Reliability behaviour

- A database lease lets only one worker claim a due schedule.
- Every real adapter call receives a stable idempotency key and creates an attempt record.
- Transient failures are retried with exponential backoff up to the configured limit.
- Expired mock-platform claims can be recovered automatically.
- An interrupted Discord request is marked uncertain instead of being blindly repeated, because Discord webhooks do not offer an idempotency-key guarantee.
- Restarting the worker does not lose pending schedules because the queue is stored in PostgreSQL.

## Test

```powershell
python -m pip install -r requirements-dev.txt
pytest
```

## Configuration

Copy `.env.example` to `.env`. Discord is optional: set `DISCORD_WEBHOOK_URL` only when publishing to a webhook channel you control. Never commit `.env` or a webhook URL.

For LLM generation, set `OPENROUTER_API_KEY` in `.env` and submit:

```json
{
  "platforms": ["x", "linkedin"],
  "generation_mode": "llm",
  "audience": "data engineers",
  "goal": "traffic",
  "tone": "educational",
  "call_to_action": "Read the full guide"
}
```

The provider is `google/gemini-2.5-flash` by default. Sampling and token limits are backend configuration, prompts include versioned platform examples, and an invalid draft receives one low-temperature repair attempt. Automated tests never call the paid provider.

## Baseline limitations

The scheduler polls PostgreSQL rather than using a separate queue broker. Audience engagement analytics remain a future extension point.

## Instagram image links (preparation only)

Connect the account through the dashboard using the OAuth setup described below.
Set `PUBLIC_MEDIA_BASE_URL` to an HTTPS address routing to this FastAPI app
(a tunnel during local development, or the deployed API origin). Restart the API
after editing configuration. This configuration does not start a tunnel.

An authenticated owner can call `POST /variants/{id}/image-link` for a variant
with a selected image. It creates a JPEG snapshot and returns `image_url` and
`expires_at` (Unix seconds). The original composed PNG remains unchanged;
JPEG compression may slightly change pixels. The signed link permits anyone
holding it to fetch only that JPEG for 15 minutes without an application login.
Generate links just before use, rather than when scheduling a future post.
The JPEG copy remains in private `IMAGE_STORAGE_DIR/instagram_exports/` after
the link expires; expiry disables access through the link, not local storage.

`GET /media/instagram/{image_id}.jpg?expires=...&signature=...` rejects expired,
modified, or missing images. The storage directory is not mounted publicly.
Keep signed URLs out of shared logs, and use a strong `JWT_SECRET`, which also
signs these links with a separate purpose. Link creation does not publish to
Instagram or connect MCP. The Instagram publisher validates image dimensions
and creates fresh links from scheduled snapshots at delivery time.

For a local Cloudflare Quick Tunnel, start the image-only gateway and tunnel
in separate terminals (using the project's Python environment):

```bash
python -m scripts.run_local media
```

```bash
/Users/omar/.codex/bin/cloudflared tunnel --url http://127.0.0.1:8001 --no-autoupdate
```

Copy the printed `https://...trycloudflare.com` origin to `PUBLIC_MEDIA_BASE_URL`
and update `INSTAGRAM_REDIRECT_URI` to the same origin plus `/instagram/oauth/callback`.
Register that exact callback in Meta and restart the API, media gateway and worker.
The tunnel exposes the signed JPEG route and a narrowly scoped OAuth callback relay;
password login, dashboard, API documentation, and link-creation routes are absent.
The gateway disables access logs so signed query strings are not logged there.
Quick Tunnels require no Cloudflare account, are intended for development,
and receive a new hostname on restart. Stop the tunnel with Ctrl+C, then stop
the gateway with Ctrl+C. Keep both processes and the laptop running during
Instagram's image fetch. These processes are not installed as startup services.

## Instagram accounts and publishing

Users connect their Business or Creator account through **Connected accounts →
Connect Instagram**. They authorize on Instagram's official website; passwords,
app secrets, and account tokens are never entered in the dashboard. Each workspace
has one Instagram connection, with authenticated encryption at rest. Publishing and
capabilities use the authenticated owner's saved connection, rather than a shared
`.env` token. One Instagram account can be active in only one workspace at a time.

The app owner must perform the one-time [Instagram OAuth setup](docs/INSTAGRAM_OAUTH_SETUP.md).
This configures our shared Meta app, HTTPS callback, app secret and encryption key.
End users do not create developer apps or copy tokens. Apply migration
`0014_instagram_oauth` and restart services after configuration changes. For existing
installations, `python -m scripts.import_instagram_connection` verifies and encrypts
the legacy test token, binds existing schedules to its account, and removes the
plaintext token from `.env`. It never overwrites an existing connection.

Select **instagram** when creating campaign drafts. Enable campaign image generation
to request a square visual, or select a ready image through the draft's image panel.
Instagram drafts cannot be approved without an image. Captions are limited to 2,200
characters and five hashtags by our generation profile. Single-image feed publishing
is supported; Reels, Stories, carousels, comments, engagement insights, and MCP are not
implemented here. Use a square image for the simplest compatible input. The publisher
accepts aspect ratios from 4:5 to 1.91:1 and source widths of at least 320px, converts
to a JPEG up to 1080px wide without cropping, and rejects output above 8 MB.

Review and approve text plus image, then schedule a time or use **Publish now** on
the scheduled draft. The configured destination handle is displayed on the card.
Publication is live. To automatically deliver only Instagram schedules:

```bash
python -m scripts.run_local worker --platform instagram
```

The unfiltered worker also handles other platforms and may deliver already-due live
Discord posts. The Instagram-only worker leaves those schedules untouched. Keep the
worker, image gateway, tunnel, laptop, and database running for local scheduled delivery.
Dashboard polling shows processing stages, errors, and links to published posts.

At delivery time the publisher verifies the token's account ID, creates a fresh signed
image URL from the scheduled image bytes, checks that the URL serves the correct JPEG,
creates a media container, polls until ready, publishes it, and records its media ID and
available permalink. Container IDs and stages are persisted in `publish_deliveries.provider_state`;
tokens and signed URLs are never saved in delivery history. A confirmed post remains
successful even if fetching its permalink fails. Known unpublished containers can be
resumed safely on retry. Uncertain publish outcomes block both automatic and manual
republication and require an operator to reconcile with Instagram; there is no automatic
reconciliation UI. Processing has a 3-minute deadline and Instagram worker leases last
at least 4 minutes. OAuth tokens are renewed before expiry by the running worker.
Expired or revoked connections require the user to reconnect. Disconnect removes
the locally saved token and blocks future publishing; an in-flight post may finish.
It does not revoke the grant on Instagram itself.

`GET /instagram/connection` returns safe connection metadata;
`POST /instagram/connection/verify` checks live identity without publishing; `GET /schedules/publishers`
reports capabilities for the authenticated workspace. Automated publishing tests mock
Meta and image-hosting HTTP calls. A live unpublished-container check separately verifies
the actual image-fetch and container-creation path; it does not prove successful live publishing.

A subsequent real single-image post was published successfully after recovering
from an expired tunnel. The user confirmed the published caption and image matched
the approved draft. See the [Instagram publishing acceptance report](evaluations/results/INSTAGRAM_PUBLISHING_REPORT.md)
for database evidence, the failed first attempt, successful retry, and test limitations.

## Workspace analytics and next steps

The dashboard includes lifetime workspace analytics backed by `GET /analytics/summary`: campaign and status counts, delivery success, and recorded AI tokens, cost coverage, and latency. All metrics are scoped to the authenticated owner. Missing provider cost is displayed as unknown. These are operational metrics; engagement analytics still require platform data.

See [PROJECT_REVIEW.md](PROJECT_REVIEW.md) for the architecture review, known reliability gaps, a prioritized feature roadmap, and CV wording grounded in the implementation.

## On-demand fact checks

Each saved platform draft has a **Check facts** button. It checks source agreement using the shared service in `app/services/factuality.py`: Flash extracts factual spans, then the configured audit model checks evidence and qualifiers. `JUDGE_AUDIT_MODEL` defaults to `google/gemini-2.5-pro`. Each click can make two paid requests through the configured OpenRouter account; generation remains unchanged.

The panel shows **Needs review**, **No issues detected**, **No factual claims found**, or **Evaluation failed**, plus claim reasons, exact source evidence, available cost, and completion time. No issues detected is not a guarantee of accuracy. Save edits before checking; unsaved edits and changed source/draft snapshots mark old checks outdated. Approval remains a manual choice. Model judgments can still miss claims or overflag mixed promotional wording.

Owner-scoped endpoints:

- `POST /variants/{id}/fact-check`: evaluates the saved snapshot and persists success or failure.
- `GET /variants/{id}/fact-check`: returns the latest check, or null if never checked.

The `fact_checks` table preserves all attempts, source/draft snapshots, version/model metadata, full judge results and usage. Concurrent checks for the same draft are rejected; interrupted checks become retryable after a timeout. Failed stages are never reported as passing checks, and missing usage is unknown.

Apply migrations through `0009_rag_generation_audit` with `alembic upgrade head` before running the updated API. The existing Docker migration service applies it automatically on startup. The migration adds RAG mode, model, k, and retrieved-passage snapshots to generation audits. Workspace usage totals cover generation; judge costs are shown on draft cards.


## Optional AI fix suggestions

On a saved draft with a current successful fact check containing unsupported or contradicted claims, click **Suggest a fix**. One additional model call proposes a minimal revision using the source, draft and flagged claims. Review the **Before / Suggested** comparison, then choose **Apply suggestion** or **Dismiss**. Suggestions and available costs persist across reloads.

Applying saves an editable draft and makes the previous fact check outdated. Approval is blocked until a successful fresh check finishes against the revised draft and current source. **Recheck facts** uses the existing two-stage judge; it does not approve automatically. Review any findings before approving. Failed or outdated checks cannot unlock approval. Concurrent edits, source changes, newer checks and repeated application invalidate the proposal; concurrent approval cannot bypass the recheck gate.

- `POST /variants/{id}/fix-suggestions` with `{"fact_check_id": <latest check id>}` creates a proposal.
- `GET /variants/{id}/fix-suggestions/latest` returns the latest proposal or null.
- `POST /variants/{id}/fix-suggestions/{suggestion_id}/apply` explicitly saves it.

The `revision_suggestions` table records the original snapshot, hashes, associated check, full proposed revision, model/prompt version, usage, latency, failures and application time. Platform limits are validated before offering or applying a revision. A generation run's original call to action is validated verbatim when it is still present in the draft; older template/imported drafts without generation metadata rely on the preservation instruction in the revision prompt. The model can still make errors, so the comparison and fresh check remain necessary. Suggestion costs appear separately on draft cards and are excluded from generation analytics.

## Standalone image retrieval research

The isolated [image retrieval module](experiments/image_retrieval/README.md) compares
OpenCLIP and SigLIP 2 on 200 fixed Crossmodal-3600 photos and 400 human English
caption queries. Both run locally on Apple MPS without paid inference or training.
See the [measured retrieval and latency results](docs/IMAGE_RETRIEVAL_EVALUATION.md)
and the [proposed campaign integration and query-rewrite evaluation](docs/IMAGE_RETRIEVAL_WORKFLOW.md).
The dashboard now supports automatic library-photo selection; the experiment remains isolated.
Apply migration `0015_image_reference_roles`, index the Photo library, then enable
“Automatically find photos from my library” with campaign image generation.
The extra vision selection call can choose zero to two scene references; final
post approval remains in the existing review flow. See the workflow document for
local environment setup, costs, limitations, and the pending campaign relevance evaluation.
