from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import current_user
from app.database import get_db
from app.models import PlatformVariant, PublishAttempt, PublishDelivery, ScheduleSlot, SourcePost, User
from app.schemas import PublishAttemptOut, PublishHistoryOut, ScheduleOut
from app.services.publishing import delivery_payload

router = APIRouter(tags=["publish history"])


def owned_schedule(schedule_id: int, user: User, db: Session) -> ScheduleSlot:
    schedule = db.scalar(
        select(ScheduleSlot)
        .join(PlatformVariant, ScheduleSlot.variant_id == PlatformVariant.id)
        .join(SourcePost, PlatformVariant.source_post_id == SourcePost.id)
        .where(ScheduleSlot.id == schedule_id, SourcePost.owner_id == user.id)
    )
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


def history_item(delivery: PublishDelivery, db: Session) -> dict:
    attempts = db.scalars(
        select(PublishAttempt)
        .where(PublishAttempt.delivery_id == delivery.id)
        .order_by(PublishAttempt.attempt_number)
    ).all()
    return {"delivery": delivery_payload(delivery), "attempts": attempts}


@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(ScheduleSlot)
        .join(PlatformVariant, ScheduleSlot.variant_id == PlatformVariant.id)
        .join(SourcePost, PlatformVariant.source_post_id == SourcePost.id)
        .where(SourcePost.owner_id == user.id)
        .order_by(ScheduleSlot.publish_at.desc())
    ).all()


@router.get("/publish-history", response_model=list[PublishHistoryOut])
def list_publish_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    deliveries = db.scalars(
        select(PublishDelivery)
        .join(ScheduleSlot, PublishDelivery.schedule_slot_id == ScheduleSlot.id)
        .join(PlatformVariant, ScheduleSlot.variant_id == PlatformVariant.id)
        .join(SourcePost, PlatformVariant.source_post_id == SourcePost.id)
        .where(SourcePost.owner_id == user.id)
        .order_by(PublishDelivery.id.desc())
    ).all()
    return [history_item(delivery, db) for delivery in deliveries]


@router.get("/schedules/{schedule_id}/history", response_model=list[PublishAttemptOut])
def schedule_history(schedule_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    schedule = owned_schedule(schedule_id, user, db)
    delivery = db.scalar(select(PublishDelivery).where(PublishDelivery.schedule_slot_id == schedule.id))
    if not delivery:
        return []
    return db.scalars(
        select(PublishAttempt)
        .where(PublishAttempt.delivery_id == delivery.id)
        .order_by(PublishAttempt.attempt_number)
    ).all()


@router.post("/schedules/{schedule_id}/retry", response_model=ScheduleOut)
def retry_failed_schedule(schedule_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    schedule = owned_schedule(schedule_id, user, db)
    if schedule.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed schedules can be retried")
    if schedule.last_error and schedule.last_error.startswith("Outcome uncertain"):
        raise HTTPException(status_code=409, detail="Uncertain delivery requires manual reconciliation")
    schedule.status = "pending"
    schedule.next_attempt_at = datetime.now(timezone.utc)
    schedule.last_error = None
    db.commit()
    db.refresh(schedule)
    return schedule
