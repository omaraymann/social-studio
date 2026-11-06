from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.database import get_db
from app.models import PlatformVariant, ScheduleSlot, SourcePost, User
from app.publishers.registry import PublisherRegistry, get_publisher_registry
from app.schemas import DeliveryOut
from app.services.publishing import DeliveryInProgress, PublishFailed, publish_scheduled_variant

router = APIRouter(prefix="/schedules", tags=["publishing"])


@router.get("/publishers")
def publishers(user: User = Depends(current_user), registry: PublisherRegistry = Depends(get_publisher_registry)):
    return registry.capabilities(user.id)


@router.post("/{schedule_id}/publish", response_model=DeliveryOut)
async def publish_schedule(
    schedule_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    registry: PublisherRegistry = Depends(get_publisher_registry),
):
    row = db.execute(
        select(ScheduleSlot, PlatformVariant)
        .join(PlatformVariant, ScheduleSlot.variant_id == PlatformVariant.id)
        .join(SourcePost, PlatformVariant.source_post_id == SourcePost.id)
        .where(ScheduleSlot.id == schedule_id, SourcePost.owner_id == user.id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Schedule not found")
    schedule, variant = row
    if variant.status not in {"scheduled", "published"}:
        raise HTTPException(status_code=409, detail="Only scheduled variants can be published")
    if schedule.status == "completed" and variant.status != "published":
        raise HTTPException(status_code=409, detail="Schedule state is inconsistent")
    try:
        return await publish_scheduled_variant(db, schedule, variant, registry)
    except DeliveryInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PublishFailed as exc:
        schedule.status = "failed"
        schedule.last_error = str(exc)
        schedule.next_attempt_at = None
        schedule.lease_expires_at = None
        db.commit()
        raise HTTPException(status_code=502, detail=f"Publishing failed: {exc}")
