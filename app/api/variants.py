from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.database import get_db
from app.models import CampaignVisual, PlatformVariant, ScheduleSlot, SourcePost, User, utcnow
from app.schemas import AttachVisual, ScheduleCreate, ScheduleOut, VariantEdit, VariantOut, VariantReject
from app.services.post_media import ensure_media_ready, render_visual
from app.services.image_storage import storage_path
from app.services.review import (
    InvalidStatusTransition,
    VariantStatus,
    approve_variant,
    change_status,
    edit_variant,
    reject_variant,
)

router = APIRouter(tags=["variant review"])


def owned_variant(variant_id: int, user: User, db: Session) -> PlatformVariant:
    variant = db.scalar(
        select(PlatformVariant)
        .join(SourcePost, PlatformVariant.source_post_id == SourcePost.id)
        .where(PlatformVariant.id == variant_id, SourcePost.owner_id == user.id)
    )
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")
    return variant


def review_error(exc: ValueError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if isinstance(exc, InvalidStatusTransition) else status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=code, detail=str(exc))


@router.get("/posts/{post_id}/variants", response_model=list[VariantOut])
def list_variants(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    owns_post = db.scalar(select(SourcePost.id).where(SourcePost.id == post_id, SourcePost.owner_id == user.id))
    if not owns_post:
        raise HTTPException(status_code=404, detail="Source post not found")
    return db.scalars(
        select(PlatformVariant).where(PlatformVariant.source_post_id == post_id).order_by(PlatformVariant.id)
    ).all()


@router.get("/variants/{variant_id}", response_model=VariantOut)
def get_variant(variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_variant(variant_id, user, db)


@router.patch("/variants/{variant_id}", response_model=VariantOut)
def update_variant(payload: VariantEdit, variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    try:
        edit_variant(variant, payload.content)
    except ValueError as exc:
        raise review_error(exc)
    db.commit()
    db.refresh(variant)
    return variant


@router.post("/variants/{variant_id}/approve", response_model=VariantOut)
def approve(variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    try:
        approve_variant(variant)
    except ValueError as exc:
        raise review_error(exc)
    # Compare the saved snapshot and gate atomically against concurrent AI edits.
    reviewed = variant.reviewed_at
    snapshot = variant.content
    image_snapshot = variant.selected_image_sha256
    visual_snapshot = variant.selected_visual_id
    required_snapshot = variant.image_required
    db.rollback()
    changed = db.execute(update(PlatformVariant).where(PlatformVariant.id == variant_id,
        PlatformVariant.content == snapshot, PlatformVariant.status == 'draft',
        PlatformVariant.selected_image_sha256 == image_snapshot,
        PlatformVariant.selected_visual_id == visual_snapshot,
        PlatformVariant.image_required == required_snapshot,
        PlatformVariant.fact_recheck_required.is_(False)).values(status='approved',
            reviewed_at=reviewed, updated_at=reviewed, rejection_reason=None))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, 'Draft changed or needs a fresh fact check. Refresh before approving')
    db.commit()
    db.refresh(variant)
    return variant


@router.post("/variants/{variant_id}/reject", response_model=VariantOut)
def reject(payload: VariantReject, variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    try:
        reject_variant(variant, payload.reason)
    except ValueError as exc:
        raise review_error(exc)
    db.commit()
    db.refresh(variant)
    return variant


@router.post("/variants/{variant_id}/schedule", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def schedule(payload: ScheduleCreate, variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    destination = None
    if variant.platform == "instagram":
        from app.services.instagram_connections import available_connection
        destination = available_connection(user.id)
        if not destination:
            raise HTTPException(422, "Connect Instagram from the dashboard before scheduling")
    if payload.publish_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="Publishing time must be in the future")
    try:
        ensure_media_ready(variant)
        change_status(variant, VariantStatus.SCHEDULED)
    except ValueError as exc:
        raise review_error(exc)
    slot = ScheduleSlot(variant_id=variant.id, publish_at=payload.publish_at,
                        destination_account_id=destination["user_id"] if destination else None,
                        destination_account_name=destination["username"] if destination else None,
                        image_key_snapshot=variant.selected_image_key,
                        image_sha256_snapshot=variant.selected_image_sha256)
    db.add(slot)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Variant is already scheduled")
    db.refresh(slot)
    return slot


@router.post("/variants/{variant_id}/image", response_model=VariantOut)
def attach_post_image(variant_id: int, payload: AttachVisual, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    db.scalar(select(SourcePost).where(SourcePost.id == variant.source_post_id).with_for_update())
    # Serialize image edits with approval/scheduling on PostgreSQL.
    variant = db.scalar(select(PlatformVariant).where(PlatformVariant.id == variant.id).with_for_update().execution_options(populate_existing=True))
    if variant.status not in {"draft", "rejected"}:
        raise HTTPException(409, "Only draft or rejected posts can change their image")
    visual = db.scalar(select(CampaignVisual).where(
        CampaignVisual.id == payload.visual_id, CampaignVisual.source_post_id == variant.source_post_id))
    if not visual or visual.platform not in {"campaign", variant.platform}:
        raise HTTPException(422, "Select a ready image from this campaign and platform")
    try:
        key, digest = render_visual(visual)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    variant.selected_visual_id, variant.selected_image_key, variant.selected_image_sha256 = visual.id, key, digest
    variant.image_required = True
    if variant.status == "rejected":
        change_status(variant, VariantStatus.DRAFT)
    variant.rejection_reason, variant.reviewed_at = None, None
    variant.updated_at = utcnow()
    db.commit()
    db.refresh(variant)
    return variant


@router.delete("/variants/{variant_id}/image", response_model=VariantOut)
def text_only_post(variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    db.scalar(select(SourcePost).where(SourcePost.id == variant.source_post_id).with_for_update())
    variant = db.scalar(select(PlatformVariant).where(PlatformVariant.id == variant.id).with_for_update().execution_options(populate_existing=True))
    if variant.status not in {"draft", "rejected"}:
        raise HTTPException(409, "Only draft or rejected posts can change their image")
    variant.selected_visual_id, variant.selected_image_key, variant.selected_image_sha256 = None, None, None
    variant.image_required = False
    variant.reviewed_at = None
    db.commit()
    db.refresh(variant)
    return variant


@router.get("/variants/{variant_id}/image")
def post_image_file(variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    if not variant.selected_image_key or not storage_path(variant.selected_image_key).is_file():
        raise HTTPException(404, "No image is attached to this post")
    return FileResponse(storage_path(variant.selected_image_key), media_type="image/png",
                        filename=f"post-{variant.id}-{variant.platform}.png",
                        headers={"Cache-Control": "private, no-store"})
