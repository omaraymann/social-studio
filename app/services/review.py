from enum import Enum

from app.models import PlatformVariant, utcnow
from app.services.variants import validate_variant
from app.services.post_media import ensure_media_ready


class VariantStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"


ALLOWED_TRANSITIONS = {
    VariantStatus.DRAFT: {VariantStatus.APPROVED, VariantStatus.REJECTED},
    VariantStatus.APPROVED: {VariantStatus.SCHEDULED},
    VariantStatus.REJECTED: {VariantStatus.DRAFT},
    VariantStatus.SCHEDULED: {VariantStatus.PUBLISHED},
    VariantStatus.PUBLISHED: set(),
}


class InvalidStatusTransition(ValueError):
    pass


def change_status(variant: PlatformVariant, target: VariantStatus):
    current = VariantStatus(variant.status)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStatusTransition(f"Cannot change variant from {current.value} to {target.value}")
    variant.status = target.value
    variant.updated_at = utcnow()


def edit_variant(variant: PlatformVariant, content: str):
    current = VariantStatus(variant.status)
    if current not in {VariantStatus.DRAFT, VariantStatus.REJECTED}:
        raise InvalidStatusTransition(f"Cannot edit a {current.value} variant")
    validate_variant(variant.platform, content)
    variant.content = content
    if current is VariantStatus.REJECTED:
        change_status(variant, VariantStatus.DRAFT)
    variant.rejection_reason = None
    variant.reviewed_at = None
    variant.updated_at = utcnow()


def approve_variant(variant: PlatformVariant):
    ensure_media_ready(variant)
    if variant.fact_recheck_required:
        raise InvalidStatusTransition("Recheck facts before approving an AI-edited draft")
    validate_variant(variant.platform, variant.content)
    change_status(variant, VariantStatus.APPROVED)
    variant.rejection_reason = None
    variant.reviewed_at = utcnow()


def reject_variant(variant: PlatformVariant, reason: str):
    reason = reason.strip()
    if not reason:
        raise ValueError("Rejection reason is required")
    change_status(variant, VariantStatus.REJECTED)
    variant.rejection_reason = reason
    variant.reviewed_at = utcnow()
