from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, JSON, Index, text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourcePost(Base):
    __tablename__ = "source_posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    variants: Mapped[list["PlatformVariant"]] = relationship(cascade="all, delete-orphan")


class PlatformVariant(Base):
    __tablename__ = "platform_variants"
    __table_args__ = (
        UniqueConstraint("source_post_id", "platform", name="uq_source_platform"),
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'scheduled', 'published')",
            name="ck_platform_variants_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    fact_recheck_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    selected_visual_id: Mapped[int | None] = mapped_column(ForeignKey("campaign_visuals.id", ondelete="SET NULL"), nullable=True)
    selected_image_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    selected_image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    @property
    def image_attached(self):
        return self.selected_image_key is not None


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'succeeded', 'failed')", name="ck_generation_runs_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(150))
    prompt_version: Mapped[str] = mapped_column(String(50))
    examples_version: Mapped[str] = mapped_column(String(50))
    platforms: Mapped[str] = mapped_column(String(100))
    audience: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(String(50))
    tone: Mapped[str] = mapped_column(String(50))
    call_to_action: Mapped[str] = mapped_column(String(500))
    temperature: Mapped[float] = mapped_column(Float)
    top_p: Mapped[float] = mapped_column(Float)
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    rag_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    rag_top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rag_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rag_context: Mapped[list | None] = mapped_column(JSON, nullable=True)
    rag_min_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_image_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"
    __table_args__ = (
        UniqueConstraint("variant_id", name="uq_schedule_slots_variant_id"),
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed')", name="ck_schedule_slots_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("platform_variants.id", ondelete="CASCADE"), index=True)
    destination_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    destination_account_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    image_key_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_sha256_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def image_attached(self):
        return self.image_key_snapshot is not None


class PublishDelivery(Base):
    __tablename__ = "publish_deliveries"
    __table_args__ = (
        UniqueConstraint("schedule_slot_id", name="uq_publish_deliveries_schedule_slot_id"),
        UniqueConstraint("idempotency_key", name="uq_publish_deliveries_idempotency_key"),
        CheckConstraint("status IN ('processing', 'succeeded', 'failed')", name="ck_publish_deliveries_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_slot_id: Mapped[int] = mapped_column(ForeignKey("schedule_slots.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    content_snapshot: Mapped[str] = mapped_column(Text)
    provider_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="processing")
    attempt_count: Mapped[int] = mapped_column(default=1)
    external_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    image_key_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_sha256_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PublishAttempt(Base):
    __tablename__ = "publish_attempts"
    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_number", name="uq_publish_attempt_number"),
        CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed', 'uncertain')",
            name="ck_publish_attempts_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("publish_deliveries.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int]
    status: Mapped[str] = mapped_column(String(30), default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstagramConnection(Base):
    __tablename__ = "instagram_connections"
    __table_args__ = (
        Index("uq_instagram_connected_account", "instagram_user_id", unique=True,
              sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
        CheckConstraint("status IN ('active', 'disconnected', 'expired', 'needs_reconnect')", name="ck_instagram_connection_status"),
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    instagram_user_id: Mapped[str] = mapped_column(String(100))
    username: Mapped[str] = mapped_column(String(100))
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InstagramOAuthAttempt(Base):
    __tablename__ = "instagram_oauth_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FactCheck(Base):
    __tablename__ = "fact_checks"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'succeeded', 'failed')", name="ck_fact_checks_status"),
        Index("uq_fact_checks_processing_variant", "variant_id", unique=True,
              sqlite_where=text("status = 'processing'"), postgresql_where=text("status = 'processing'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("platform_variants.id", ondelete="CASCADE"), index=True)
    draft_snapshot: Mapped[str] = mapped_column(Text)
    source_snapshot: Mapped[str] = mapped_column(Text)
    draft_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(50))
    extraction_model: Mapped[str] = mapped_column(String(150))
    audit_model: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default="processing")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RevisionSuggestion(Base):
    __tablename__ = "revision_suggestions"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'succeeded', 'failed')", name="ck_revision_suggestions_status"),
        Index("uq_revision_suggestions_processing_variant", "variant_id", unique=True,
            sqlite_where=text("status = 'processing'"), postgresql_where=text("status = 'processing'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("platform_variants.id", ondelete="CASCADE"), index=True)
    fact_check_id: Mapped[int] = mapped_column(ForeignKey("fact_checks.id", ondelete="CASCADE"))
    draft_snapshot: Mapped[str] = mapped_column(Text)
    draft_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    call_to_action: Mapped[str | None] = mapped_column(String(500), nullable=True)
    suggested_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(150))
    prompt_version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="processing")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CampaignImage(Base):
    __tablename__ = "campaign_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    reference_role: Mapped[str] = mapped_column(String(20), default='product', server_default='product')
    original_key: Mapped[str] = mapped_column(String(200))
    prepared_key: Mapped[str] = mapped_column(String(200))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    original_width: Mapped[int] = mapped_column(Integer)
    original_height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CampaignVisual(Base):
    __tablename__ = "campaign_visuals"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'processing', 'succeeded', 'failed')", name="ck_campaign_visuals_status"),
        Index("uq_campaign_visuals_active_post", "source_post_id", "platform", unique=True,
              sqlite_where=text("status IN ('queued', 'processing')"),
              postgresql_where=text("status IN ('queued', 'processing')")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_post_id: Mapped[int] = mapped_column(ForeignKey("source_posts.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(30), default="campaign", server_default="campaign")
    vision_model: Mapped[str] = mapped_column(String(150))
    image_model: Mapped[str] = mapped_column(String(150))
    aspect_ratio: Mapped[str] = mapped_column(String(10))
    style: Mapped[str] = mapped_column(String(100))
    reference_image_ids: Mapped[list] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    step: Mapped[str] = mapped_column(String(30), default="queued")
    headline: Mapped[str | None] = mapped_column(String(100), nullable=True)
    call_to_action: Mapped[str | None] = mapped_column(String(150), nullable=True)
    visual_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
