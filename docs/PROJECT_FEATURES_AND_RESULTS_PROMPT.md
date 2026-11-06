# Reusable project briefing prompt

Prepared from repository code and recorded experiments on 6 November 2026.
This describes a working local prototype and measured research pilots. Historical
reports retain the limitations and feature scope at the time of each experiment.

Copy the following prompt into a new conversation:

```text
Use the verified facts below to explain and present my Social Studio project.
Write a clear project overview, architecture, feature inventory, evaluation table,
measured improvements, and limitations. Adapt the explanation to the audience.
Do not invent business impact, users, time savings, model training, production
accuracy, or evaluation results. Distinguish percentage points from relative percent
changes, development results from test diagnostics, and software tests from model
quality measurements. Treat library sizes as recorded snapshots, not capacity limits.

PROJECT
Social Studio is an authenticated multimodal social campaign application. A user
imports an article or supplies source text, optionally retrieves document evidence,
generates platform-specific drafts and visuals, reviews/edits/checks them, approves,
schedules, and tracks publishing attempts.

STACK AND ARCHITECTURE
- Python, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, pgvector, React,
  JavaScript, Vite, Docker Compose, pytest, HTTPX, and OpenRouter model integration.
- JWT authentication and Argon2 password hashing; owner-scoped records and media.
- Separate API, frontend, migration runner, publishing worker and image gateway.
- Provider interfaces separate model integration and publishing adapters.
- AI audit records preserve model/prompt metadata, outputs, available token usage,
  costs, latency, errors, source snapshots and retrieved evidence.
- Latest verification: 222 pytest tests passed, frontend production build passed,
  whitespace checks passed, and one Alembic migration head, 0015.

CAMPAIGN AND DASHBOARD FEATURES
- Article import and pasted source content; deterministic templates or optional
  paid LLM generation with structured output and platform validation.
- Draft generation for X, LinkedIn, Discord and Instagram, configured using audience,
  goal, tone, call to action and visual preferences.
- Separate dashboard areas for adding documents, adding images, campaign generation,
  campaign review, activity, workspace overview and connected accounts.
- Document ingestion progress and failure reporting, completion notifications,
  collapsible document/photo libraries, and image indexing status.
- Editable drafts; approval and schedule management; delivery/attempt history.
- Workspace analytics for counts, publishing outcomes and recorded AI usage;
  these are operational analytics, not audience engagement measurements.

TEXT RAG
- Document upload/extraction, tokenizer-aware sentence grouping, configurable chunk
  budgets, optional sentence overlap/titles, and oversized-sentence fallback.
- Cached normalized embeddings, pgvector storage, similarity search, configurable k,
  and a low-similarity cutoff. Retrieval can return no qualifying evidence.
- Documents, chunks and embeddings stored separately; owner-scoped user collections.
- Optional RAG during generation; actual retrieved passages are visible and saved
  with generation evidence. Text-only generation remains available.
- BAAI/bge-small-en-v1.5 provides 384-dimensional text embeddings.
- Research compared three embedding models: E5-small-v2, E5-base-v2 and BGE-small;
  k values 1, 3, 5, 10 and 20; chunking variants; and adaptive retrieval experiments.
- SciFact corpus: 5,183 documents; 809 development queries; 300 test queries.
- Same-passage model comparison used 5,680 passages and k=5:
  E5-small: Precision@5 17.16%, Recall@5 76.36%, MRR@5 0.6808.
  BGE-small: Precision@5 18.15%, Recall@5 80.98%, MRR@5 0.7053.
  Development Recall@5 improvement: +4.62 percentage points, +6.05% relative.
  Development MRR improvement: +0.0245, +3.60% relative.
- Separate BGE chunking experiments: 256-token overlap configuration produced
  10,470 chunks and development Recall@5 81.80%; 512-token no-overlap configuration
  produced 5,658 chunks and development Recall@5 81.19%.
- On 300 test queries, BGE 256/overlap gave Precision@5 17.13%, Recall@5 76.65%,
  MRR@5 0.6823. BGE 512/no-overlap gave 16.87%, 76.15%, 0.6743 respectively.
  The test set was used for configuration comparison, so this is diagnostic and
  cannot be described as an untouched final test estimate.
- SciFact has incomplete positive relevance labels: unlabelled retrieved documents
  are not confirmed irrelevant. Precision is labelled-positive precision here.
- Adaptive development experiments expose context/recall tradeoffs. One bounded
  cap-five variant averaged 2.53 documents and 826 estimated context tokens versus
  fixed-five's 5 documents and 1,653 tokens, while recall fell from 81.19% to 75.04%.
  These are research alternatives, not evidence of a free performance improvement.
- Saved metrics, plots, reports and reproducibility metadata accompany experiments.

FACTUALITY AND REVISION
- On-demand two-stage checks: factual-span extraction followed by evidence audit.
- Supported, unsupported and contradicted findings with evidence and reasons;
  source passage IDs resolve to exact quotations. Non-factual promotional spans
  can be excluded while remaining visible in audits.
- Optional minimal AI revision suggestions with before/after review and explicit
  application. Applying a suggestion requires a fresh successful check before approval.
- Snapshot checks invalidate stale results after draft/source changes.
- Historical v1-to-v2 judge experiment on the same 34 completed synthetic drafts:
  provisional-label agreement rose from 58/64 (90.625%) to 64/64 (100%), +9.375
  percentage points. Unsupported recall in the reported runs rose from 9/15 to 15/15.
  The tuned retry completed 34/35 drafts; its failed evaluation was not a pass.
  Labels were assistant-authored, examples small and inspected during development;
  these figures are not independently established production accuracy.
- Later two-stage v7 tuning completed six user-provided drafts, with observed
  opinion/scope fixes, $0.101972 reported cost and 16.5-second average latency.
  No overall accuracy is justified for this six-draft tuning set.

MULTIMODAL IMAGE WORKFLOW
- Upload validated JPEG/PNG/WebP references; EXIF correction, resizing, metadata
  stripping and content validation; owner-scoped private media storage.
- Manual product-reference mode or automatic scene-reference library selection.
- Local cached SigLIP 2 embeddings: 768 dimensions, versioned model/preprocessing,
  incremental indexing of new/changed images, hash checks and owner filtering.
- Campaign LLM creates a short visual search query with drafts in the same call.
- Retrieve up to five library candidates. One extra VLM selection/planning call
  examines pixels and original campaign requirements, selects zero to two images,
  explains choices and supplies adaptation notes for lighting/clutter/background.
- Selected actual images and notes feed platform-specific image generation.
  No-match can stop image generation or use an explicitly chosen illustration fallback.
- General library images provide scene inspiration; their exact product identities
  are unverified. Manual attachments support deliberate product references.
- OpenRouter image generation uses Gemini image models, including the selected
  cheaper Gemini 2.5 Flash Image option; saved attempts distinguish model and cost.
- Square visual presets for LinkedIn/Instagram and landscape for X/Discord.
- Application-rendered editable headlines/CTAs; generated images attach directly
  to drafts and the exact approved image is preserved for scheduling/publishing.
- Per-attempt stages, errors, latency, reported costs and reference/evidence snapshots.

IMAGE RETRIEVAL EVALUATION
- Frozen Crossmodal-3600 sample: 200 images, 400 human English caption queries,
  36 geographic locales; every query searches all 200 images. No fine-tuning.
- OpenCLIP ViT-B/32: 151.3M parameters, Recall@1 80.25%, Recall@5 96.00%,
  Recall@10 98.25%, MRR 0.8736.
- SigLIP 2 base patch32/256: 376.9M parameters, Recall@1 84.75%, Recall@5 98.25%,
  Recall@10 99.50%, MRR 0.9068.
- SigLIP improvements: Recall@1 +4.50 percentage points (+5.61% relative),
  Recall@5 +2.25 points (+2.34% relative), MRR +0.0332 (+3.80% relative).
- On Mac M4 with Apple MPS, 50 sequential warm queries after five warmups:
  OpenCLIP median/p95 12.95/22.68 ms; SigLIP 2 24.08/34.90 ms.
  SigLIP improved retrieval on this pilot but was slower, not faster.
- SigLIP encoded the 200-image gallery in 4.89 seconds in the measured batch run.
- These are paired-source caption retrieval results, not campaign suitability or
  image-generation accuracy. Pretraining overlap was not audited; no claim of
  statistical significance or independently calibrated selector accuracy.

GOOGLE DRIVE MCP
- Python asynchronous MCP client connects to an existing Node.js Drive MCP server
  over stdio; supports discovery and calls for search/read/upload/folder operations.
- Manual recursive/paginated sync downloads docs/images and indexes docs for RAG.
- SHA-256/change detection skips unchanged files and reindexes changed content.
- Recorded verification: 24 documents + 20 images = 44 synced files; 61 document
  chunks/embeddings. Second sync skipped all 44 unchanged files with no downloads
  or re-embedding; zero paid model calls during that sync check.
- Production photo library subsequently indexed the 20 images using SigLIP 2.
- Drive sync is deterministic and manual; it is not autonomous tool selection,
  automatic per-campaign Drive scanning, or multi-user Google OAuth onboarding.

PUBLISHING AND INSTAGRAM
- Real Discord webhook delivery and real Instagram single-image feed publishing.
  X and LinkedIn publisher adapters are mocks, although drafts/images are generated.
- Instagram dashboard OAuth connection, state validation, server-side encrypted
  owner-bound token storage, refresh/reconnect/disconnect lifecycle.
- Signed temporary public image links through a separate image-only gateway/tunnel.
- Instagram media container creation, status polling and publication; IDs/permalinks
  and delivery stages recorded; uncertain publish outcomes block blind republication.
- Database-backed scheduling worker, attempts/retries, lease/state management and
  explicit operator review where the remote outcome is uncertain.
- One real Instagram post was manually confirmed to match approved text and image;
  it succeeded on attempt two after a pre-publication tunnel failure was repaired.
  This is one acceptance test, not a publishing success-rate benchmark.
- One live coffee campaign selected two photos and generated/attached one LinkedIn
  image in 28.44 seconds end-to-end, at $0.0412347 reported provider cost. This is
  one run, not an average or service-level guarantee. RAG was disabled for that check.

LIMITATIONS AND UNIMPLEMENTED WORK
- Ragas and DeepEval were discussed but are not integrated; campaign-domain judge,
  query rewriting, image selection and generation still need independently reviewed
  held-out evaluation examples.
- Earlier VLM detection/YOLO pilots are archived research, not the deployed image
  selection architecture; do not claim trained custom detectors or bounding-box use.
- Image jobs/indexing use local API background tasks and are not a durable external
  queue; local publishing requires running infrastructure and a reachable media URL.
- Incidental branding/reference fidelity and multi-reference composition require
  human review; images can primarily resemble one selected reference.
- No engagement tracking agent, autonomous marketing decisions, Reels/Stories/
  carousel publishing, GraphRAG, or real LinkedIn/X publishing is implemented.
- Do not claim cloud deployment, exactly-once remote delivery, production scale,
  reduced marketing cost/time, or improved engagement without separate evidence.
```

## Evidence locations

- `rag/data/results/scifact_models/REPORT.md`
- `rag/data/results/scifact_bge_chunking/summary.csv`
- `rag/data/results/scifact_adaptive/summary.csv`
- `rag/data/results/scifact_test_config_comparison/REPORT.md`
- `evaluations/results/JUDGE_TUNING_REPORT.md`
- `evaluations/results/small_user_drafts_v7_2026-10-11/REPORT.md`
- `docs/IMAGE_RETRIEVAL_EVALUATION.md`
- `docs/IMAGE_RETRIEVAL_WORKFLOW.md`
- `docs/DRIVE_SYNC_REPORT.md`
- `docs/LIVE_COFFEE_CAMPAIGN_TEST.md`
- `evaluations/results/INSTAGRAM_PUBLISHING_REPORT.md`
