from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analytics, auth, fact_checks, fix_suggestions, history, image_library, images, instagram, media_links, posts, publishing, rag, variants
from app.config import settings
from app.media_gateway import router as public_media_router

app = FastAPI(title="Social Media Studio", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(posts.router)
app.include_router(variants.router)
app.include_router(publishing.router)
app.include_router(history.router)
app.include_router(analytics.router)
app.include_router(fact_checks.router)
app.include_router(fix_suggestions.router)
app.include_router(rag.router)
app.include_router(images.router)
app.include_router(image_library.router)
app.include_router(media_links.router)
app.include_router(public_media_router)
app.include_router(instagram.router)


@app.get("/health", tags=["operations"])
def health():
    return {"status": "ok"}
