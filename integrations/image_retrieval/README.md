# Campaign image library

`encoder.py` provides a cached, thread-safe local SigLIP 2 encoder. `library.py`
handles owner-scoped image records, incremental indexing, and pgvector search.
It uses separate 768-dimensional image vectors, not the existing BGE text vectors.

The dashboard can discover already synced Drive photos or accept library uploads.
Indexing runs locally with no paid requests. Automatic campaign generation adds
one VLM selection/planning call, then passes selected prepared photo bytes to the
existing image generator. General library assets are scene references; exact
product identities require the manual reference path.

See [workflow and setup](../../docs/IMAGE_RETRIEVAL_WORKFLOW.md) and
[retrieval measurements](../../docs/IMAGE_RETRIEVAL_EVALUATION.md).

The optional ML runtime versions used locally are listed in
`../../experiments/image_retrieval/requirements.txt`. Install them in an optional
environment alongside the backend dependencies. Keep the FastAPI/Starlette pair
compatible; the project's working local app environment is documented in the workflow.

The independent benchmark module remains under `experiments/image_retrieval/`.
No benchmark captions or expected image IDs are used in production selection.
