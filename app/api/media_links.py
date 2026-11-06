"""Owner-created, short-lived image URLs for external publishing services."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.variants import owned_variant
from app.core.security import current_user
from app.database import get_db
from app.models import User
from app.services.instagram_media import create_image_link

router = APIRouter(tags=["publishing media"])


@router.post("/variants/{variant_id}/image-link")
def image_link(variant_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    variant = owned_variant(variant_id, user, db)
    if not variant.selected_image_key:
        raise HTTPException(422, "Select a campaign image first")
    try:
        result = create_image_link(variant.selected_image_key, variant.selected_image_sha256)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
