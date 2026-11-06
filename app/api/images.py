"""Authenticated product attachments and campaign visual jobs."""

from datetime import timedelta, timezone
from pathlib import PurePath

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.posts import owned_post
from app.config import settings
from app.core.security import current_user
from app.database import get_db
from app.llm.vision import IMAGE_MODELS, IMAGE_REFERENCE_LIMITS, VISION_MODELS
from app.models import CampaignImage, CampaignVisual, GenerationRun, PlatformVariant, SourcePost, User, utcnow
from app.services.post_media import render_visual
from app.schemas import CampaignImageOut, CampaignVisualOut, VisualRequest, VisualTextEdit
from app.services.campaign_visuals import campaign_visual_context, process_visual, selected_references
from app.services.image_storage import MAX_UPLOAD_BYTES, save_reference, storage_path

router = APIRouter(tags=["campaign images"])


@router.get("/visual-models")
def models(user: User = Depends(current_user)):
    return {"vision": [{"id": key, "name": value} for key, value in VISION_MODELS.items()],
            "image": [{"id": key, "name": value, "max_reference_images": IMAGE_REFERENCE_LIMITS[key]}
                      for key, value in IMAGE_MODELS.items()],
            "configured": bool(settings.openrouter_api_key)}


@router.post("/posts/{post_id}/images", response_model=CampaignImageOut, status_code=201)
async def attach_image(post_id: int, file: UploadFile = File(...),
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_post(post_id, user, db)
    count = db.scalar(select(func.count()).select_from(CampaignImage).where(CampaignImage.source_post_id == post_id))
    if count >= 10:
        raise HTTPException(422, "A campaign can have at most 10 attached images")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Each image must be 10 MB or smaller")
    try:
        prepared = save_reference(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    image = CampaignImage(source_post_id=post_id,
                          filename=PurePath(file.filename or "product-image").name[:300], **prepared)
    db.add(image)
    db.commit()
    db.refresh(image)
    return image


@router.get("/posts/{post_id}/images", response_model=list[CampaignImageOut])
def attachments(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_post(post_id, user, db)
    return db.scalars(select(CampaignImage).where(CampaignImage.source_post_id == post_id).order_by(CampaignImage.id)).all()


@router.get("/posts/{post_id}/images/{image_id}/file")
def attachment_file(post_id: int, image_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_post(post_id, user, db)
    try:
        image = selected_references(db, post_id, [image_id])[0]
    except ValueError as exc:
        raise HTTPException(404, "Image not found") from exc
    path = storage_path(image.prepared_key)
    if not path.is_file():
        raise HTTPException(404, "Image file is missing")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})


def expire_interrupted_jobs(db: Session, post_id: int) -> None:
    # Jobs run in this API process. A restart can interrupt them; never retry a paid call silently.
    rows = db.scalars(select(CampaignVisual).where(
        CampaignVisual.source_post_id == post_id, CampaignVisual.status.in_(["queued", "processing"]))).all()
    for row in rows:
        created = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
        if created < utcnow() - timedelta(minutes=10):
            row.status, row.step = "failed", "failed"
            row.error_message = "This generation was interrupted or expired. You can start a new attempt."
            row.completed_at = utcnow()
    db.commit()


@router.post("/posts/{post_id}/visuals", response_model=CampaignVisualOut, status_code=202)
def start_visual(post_id: int, payload: VisualRequest, tasks: BackgroundTasks,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = owned_post(post_id, user, db)
    limit = IMAGE_REFERENCE_LIMITS[payload.image_model]
    if len(set(payload.reference_image_ids)) > limit:
        raise HTTPException(422, f"{IMAGE_MODELS[payload.image_model]} accepts at most {limit} reference photos. Deselect extra photos.")
    if not settings.openrouter_api_key:
        raise HTTPException(503, "Configure OPENROUTER_API_KEY before generating images")
    try:
        references = selected_references(db, post_id, payload.reference_image_ids)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    run = db.scalar(select(GenerationRun).where(
        GenerationRun.source_post_id == post_id, GenerationRun.status == "succeeded"
    ).order_by(GenerationRun.id.desc()))
    drafts = db.scalars(select(PlatformVariant).where(PlatformVariant.source_post_id == post_id)).all()
    if payload.platform != "campaign" and payload.platform not in {variant.platform for variant in drafts}:
        raise HTTPException(422, "Generate a draft for this platform before creating its visual")
    context = campaign_visual_context(post, run, {variant.platform: variant.content for variant in drafts},
                                     platform=payload.platform, style=payload.style, aspect_ratio=payload.aspect_ratio,
                                     instructions=payload.instructions)
    expire_interrupted_jobs(db, post_id)
    row = CampaignVisual(source_post_id=post_id, vision_model=payload.vision_model,
                         platform=payload.platform,
                         image_model=payload.image_model, aspect_ratio=payload.aspect_ratio, style=payload.style,
                         reference_image_ids=[image.id for image in references], context=context)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An image generation is already running for this campaign and platform") from exc
    db.refresh(row)
    tasks.add_task(process_visual, row.id)
    return row


@router.get("/posts/{post_id}/visuals", response_model=list[CampaignVisualOut])
def list_visuals(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owned_post(post_id, user, db)
    expire_interrupted_jobs(db, post_id)
    return db.scalars(select(CampaignVisual).where(CampaignVisual.source_post_id == post_id).order_by(CampaignVisual.id.desc())).all()


def owned_visual(post_id: int, visual_id: int, user: User, db: Session) -> CampaignVisual:
    owned_post(post_id, user, db)
    visual = db.scalar(select(CampaignVisual).where(CampaignVisual.id == visual_id,
                                                  CampaignVisual.source_post_id == post_id))
    if visual is None:
        raise HTTPException(404, "Campaign visual not found")
    return visual


@router.get("/posts/{post_id}/visuals/{visual_id}/file")
def visual_file(post_id: int, visual_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    visual = owned_visual(post_id, visual_id, user, db)
    if visual.status != "succeeded" or not visual.image_key or not storage_path(visual.image_key).is_file():
        raise HTTPException(404, "Generated image is not available")
    return FileResponse(storage_path(visual.image_key), media_type="image/png",
                        filename=f"campaign-{post_id}-visual-{visual_id}.png",
                        headers={"Cache-Control": "private, no-store"})


@router.patch("/posts/{post_id}/visuals/{visual_id}", response_model=CampaignVisualOut)
def edit_visual_text(post_id: int, visual_id: int, payload: VisualTextEdit,
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    visual = owned_visual(post_id, visual_id, user, db)
    db.scalar(select(SourcePost).where(SourcePost.id == post_id).with_for_update())
    db.refresh(visual)
    if visual.status != "succeeded":
        raise HTTPException(409, "Wait until image generation completes")
    if not payload.headline.strip() or not payload.call_to_action.strip():
        raise HTTPException(422, "Headline and CTA cannot be blank")
    attached = db.scalars(select(PlatformVariant).where(PlatformVariant.selected_visual_id == visual.id)
                         .with_for_update().execution_options(populate_existing=True)).all()
    if any(variant.status not in {"draft", "rejected"} for variant in attached):
        raise HTTPException(409, "This image is used by an approved or scheduled post. Create a new attempt to change it.")
    visual.headline, visual.call_to_action = payload.headline.strip(), payload.call_to_action.strip()
    if attached:
        key, digest = render_visual(visual)
        for variant in attached:
            variant.selected_image_key, variant.selected_image_sha256 = key, digest
            variant.updated_at = utcnow()
    db.commit()
    db.refresh(visual)
    return visual
