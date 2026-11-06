import hashlib

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.database import get_db
from app.llm.base import LLMError, LLMProvider
from app.llm.registry import get_llm_provider
from app.models import CampaignVisual, GenerationRun, PlatformVariant, SourcePost, User
from app.llm.vision import IMAGE_MODELS, IMAGE_REFERENCE_LIMITS
from app.services.campaign_visuals import process_visual, selected_references
from app.schemas import GenerationRunOut, GenerateVariants, SourcePostCreate, SourcePostOut, VariantOut
from app.services.ingestion import fetch_article
from app.services.llm_generation import generate_llm_variants
from app.services.variants import generate_variant, validate_variant

router = APIRouter(prefix="/posts", tags=["source posts"])


def owned_post(post_id: int, user: User, db: Session) -> SourcePost:
    post = db.scalar(select(SourcePost).where(SourcePost.id == post_id, SourcePost.owner_id == user.id))
    if not post:
        raise HTTPException(status_code=404, detail="Source post not found")
    return post


@router.post("", response_model=SourcePostOut, status_code=status.HTTP_201_CREATED)
def create_post(payload: SourcePostCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if payload.url:
        try:
            article = fetch_article(str(payload.url))
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        title, content, source_url = article.title, article.content, str(payload.url)
    else:
        title, content, source_url = payload.title, payload.markdown, None
    post = SourcePost(owner_id=user.id, title=title, content=content, source_url=source_url, content_hash=hashlib.sha256(content.encode()).hexdigest())
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@router.get("", response_model=list[SourcePostOut])
def list_posts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(SourcePost).where(SourcePost.owner_id == user.id).order_by(SourcePost.id.desc())).all()


@router.get("/{post_id}", response_model=SourcePostOut)
def get_post(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_post(post_id, user, db)


@router.get("/{post_id}/generations", response_model=list[GenerationRunOut])
def list_generations(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = owned_post(post_id, user, db)
    return db.scalars(
        select(GenerationRun).where(GenerationRun.source_post_id == post.id).order_by(GenerationRun.id.desc())
    ).all()


@router.post("/{post_id}/variants", response_model=list[VariantOut], status_code=status.HTTP_201_CREATED)
async def create_variants(
    payload: GenerateVariants,
    post_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    llm_provider: LLMProvider = Depends(get_llm_provider),
):
    post = owned_post(post_id, user, db)
    if payload.generate_images and len(set(payload.reference_image_ids)) > IMAGE_REFERENCE_LIMITS[payload.image_model]:
        raise HTTPException(422, f"{IMAGE_MODELS[payload.image_model]} accepts at most {IMAGE_REFERENCE_LIMITS[payload.image_model]} reference photos")
    if payload.reference_image_ids:
        try:
            selected_references(db, post_id, payload.reference_image_ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    existing = set(db.scalars(select(PlatformVariant.platform).where(PlatformVariant.source_post_id == post.id)).all())
    requested = list(dict.fromkeys(payload.platforms))
    if existing.intersection(requested):
        raise HTTPException(status_code=409, detail="A variant already exists for one or more requested platforms")
    if payload.generation_mode == "llm":
        try:
            variants = await generate_llm_variants(db, post, payload, llm_provider)
            if payload.generate_images:
                queued = db.scalars(select(CampaignVisual).where(
                    CampaignVisual.source_post_id == post.id, CampaignVisual.status == "queued",
                    CampaignVisual.platform.in_(requested))).all()
                for visual in queued:
                    background_tasks.add_task(process_visual, visual.id)
            return variants
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    variants = []
    for platform in requested:
        content = generate_variant(platform, post.title, post.content, post.source_url)
        validate_variant(platform, content)
        variant = PlatformVariant(source_post_id=post.id, platform=platform, content=content)
        db.add(variant)
        variants.append(variant)
    db.commit()
    for variant in variants:
        db.refresh(variant)
    return variants
