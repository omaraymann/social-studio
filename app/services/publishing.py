from hashlib import sha256

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import PlatformVariant, PublishAttempt, PublishDelivery, ScheduleSlot, SourcePost, utcnow
from app.publishers.base import PublisherError, PublishImage, PublishRequest
from app.publishers.registry import PublisherRegistry
from app.services.image_storage import storage_path


class DeliveryInProgress(RuntimeError):
    pass


class PublishFailed(RuntimeError):
    def __init__(self, message, *, retryable=True, outcome_uncertain=False):
        super().__init__(message)
        self.retryable = retryable
        self.outcome_uncertain = outcome_uncertain


def make_idempotency_key(variant_id: int, schedule_slot_id: int) -> str:
    operation = f"variant:{variant_id}:schedule:{schedule_slot_id}"
    return sha256(operation.encode()).hexdigest()


def delivery_payload(delivery: PublishDelivery, already_published: bool = False) -> dict:
    return {
        "id": delivery.id,
        "schedule_slot_id": delivery.schedule_slot_id,
        "platform": delivery.platform,
        "idempotency_key": delivery.idempotency_key,
        "status": delivery.status,
        "attempt_count": delivery.attempt_count,
        "external_post_id": delivery.external_post_id,
        "external_url": delivery.external_url,
        "error_message": delivery.error_message,
        "created_at": delivery.created_at,
        "completed_at": delivery.completed_at,
        "already_published": already_published,
        "image_attached": delivery.image_key_snapshot is not None,
        "stage": (delivery.provider_state or {}).get("stage"),
    }


async def publish_scheduled_variant(
    db: Session,
    schedule: ScheduleSlot,
    variant: PlatformVariant,
    registry: PublisherRegistry,
) -> dict:
    key = make_idempotency_key(variant.id, schedule.id)
    delivery = db.scalar(select(PublishDelivery).where(PublishDelivery.idempotency_key == key))
    if delivery and delivery.status == "succeeded":
        return delivery_payload(delivery, already_published=True)
    if delivery and delivery.status == "processing":
        raise DeliveryInProgress("This publishing operation is already in progress")
    if delivery and delivery.error_message and delivery.error_message.startswith("Outcome uncertain"):
        raise PublishFailed(delivery.error_message, retryable=False, outcome_uncertain=True)

    if delivery:
        changed = db.execute(update(PublishDelivery).where(PublishDelivery.id == delivery.id,
            PublishDelivery.status == "failed", PublishDelivery.attempt_count == delivery.attempt_count)
            .values(status="processing", attempt_count=delivery.attempt_count + 1, error_message=None))
        if changed.rowcount != 1:
            db.rollback()
            raise DeliveryInProgress("This publishing operation is already in progress")
    else:
        delivery = PublishDelivery(
            schedule_slot_id=schedule.id,
            platform=variant.platform,
            idempotency_key=key,
            content_snapshot=variant.content,
            image_key_snapshot=schedule.image_key_snapshot,
            image_sha256_snapshot=schedule.image_sha256_snapshot,
        )
        db.add(delivery)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(PublishDelivery).where(PublishDelivery.idempotency_key == key))
        if existing and existing.status == "succeeded":
            return delivery_payload(existing, already_published=True)
        raise DeliveryInProgress("This publishing operation is already in progress")
    db.refresh(delivery)
    attempt = PublishAttempt(delivery_id=delivery.id, attempt_number=delivery.attempt_count)
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    def save_provider_state(state):
        delivery.provider_state = dict(state)
        db.commit()

    try:
        image = None
        if delivery.image_key_snapshot:
            try:
                raw = storage_path(delivery.image_key_snapshot).read_bytes()
            except (OSError, ValueError) as exc:
                raise PublisherError("Scheduled image is missing; restore it before retrying", retryable=False) from exc
            if delivery.image_sha256_snapshot and sha256(raw).hexdigest() != delivery.image_sha256_snapshot:
                raise PublisherError("Scheduled image changed since approval; publishing stopped", retryable=False)
            image = PublishImage(content=raw, filename=f"post-{variant.id}-{variant.platform}.png")
        publisher = registry.get(variant.platform)
        if image and not publisher.supports_images:
            raise PublisherError("This publisher cannot deliver images; publishing stopped", retryable=False)
        result = await publisher.publish(
            PublishRequest(content=delivery.content_snapshot, idempotency_key=key, image=image,
                           owner_user_id=db.scalar(select(SourcePost.owner_id).where(SourcePost.id == variant.source_post_id)),
                           provider_state=dict(delivery.provider_state or {}), save_state=save_provider_state,
                           destination_account_id=schedule.destination_account_id)
        )
    except PublisherError as exc:
        safe_error = ("Outcome uncertain; manual reconciliation required: " if exc.outcome_uncertain else "") + str(exc)
        delivery.status = "failed"
        delivery.error_message = safe_error[:500]
        delivery.completed_at = utcnow()
        attempt.status = "uncertain" if exc.outcome_uncertain else "failed"
        attempt.error_message = safe_error[:500]
        attempt.completed_at = utcnow()
        db.commit()
        raise PublishFailed(safe_error, retryable=exc.retryable and not exc.outcome_uncertain,
                            outcome_uncertain=exc.outcome_uncertain) from exc
    except Exception as exc:
        uncertain = delivery.platform == "instagram" and (delivery.provider_state or {}).get("stage") in {"publishing", "published"}
        safe_error = ("Outcome uncertain; manual reconciliation required: " if uncertain else "") + "Publisher failed unexpectedly"
        delivery.status = "failed"
        delivery.error_message = safe_error
        delivery.completed_at = utcnow()
        attempt.status = "uncertain" if uncertain else "failed"
        attempt.error_message = safe_error
        attempt.completed_at = utcnow()
        db.commit()
        raise PublishFailed(safe_error, retryable=not uncertain, outcome_uncertain=uncertain) from exc

    delivery.status = "succeeded"
    delivery.external_post_id = result.external_post_id
    delivery.external_url = result.external_url
    delivery.completed_at = utcnow()
    attempt.status = "succeeded"
    attempt.completed_at = utcnow()
    schedule.status = "completed"
    schedule.next_attempt_at = None
    schedule.lease_expires_at = None
    schedule.last_error = None
    variant.status = "published"
    variant.updated_at = utcnow()
    db.commit()
    db.refresh(delivery)
    return delivery_payload(delivery)
