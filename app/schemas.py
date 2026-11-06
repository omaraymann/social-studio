from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, model_validator


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr


class SourcePostCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    markdown: str | None = Field(default=None, min_length=20, max_length=100_000)
    url: HttpUrl | None = None

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.markdown is None) == (self.url is None):
            raise ValueError("Provide exactly one of markdown or url")
        if self.markdown is not None and not self.title:
            raise ValueError("title is required for Markdown posts")
        return self


class VariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform: str
    content: str
    status: str
    fact_recheck_required: bool = False
    rejection_reason: str | None
    reviewed_at: datetime | None
    updated_at: datetime
    created_at: datetime
    selected_visual_id: int | None = None
    selected_image_sha256: str | None = None
    image_required: bool = False
    image_attached: bool = False


class SourcePostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    content: str
    source_url: str | None
    content_hash: str
    created_at: datetime


class GenerateVariants(BaseModel):
    platforms: list[Literal["x", "linkedin", "discord", "instagram"]] = Field(min_length=1)
    generation_mode: Literal["deterministic", "llm"] = "deterministic"
    audience: str = Field(default="general professional audience", min_length=2, max_length=300)
    goal: Literal["awareness", "engagement", "traffic", "conversion"] = "awareness"
    tone: Literal["professional", "friendly", "educational", "energetic"] = "professional"
    call_to_action: str = Field(default="Read the full article", min_length=2, max_length=500)
    use_rag: bool = False
    rag_document_ids: list[str] = Field(default_factory=list, max_length=100)
    rag_top_k: int = Field(default=5, ge=1, le=10)
    rag_min_similarity: float = Field(default=0.70, ge=0, le=1)
    vision_model: Literal["google/gemini-2.5-flash", "google/gemini-2.5-pro"] | None = None
    reference_image_ids: list[int] = Field(default_factory=list, max_length=4)
    generate_images: bool = False
    auto_select_images: bool = False
    image_no_match_policy: Literal['stop', 'illustration'] = 'stop'
    image_model: Literal["google/gemini-2.5-flash-image", "google/gemini-3.1-flash-image"] = "google/gemini-2.5-flash-image"
    image_style: Literal["studio product photography", "lifestyle scene", "minimal editorial"] = "studio product photography"
    image_instructions: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def rag_requires_llm(self):
        if self.auto_select_images and (not self.generate_images or self.generation_mode != 'llm'):
            raise ValueError('Automatic image selection requires AI image generation')
        if self.auto_select_images and self.reference_image_ids:
            raise ValueError('Choose automatic library selection or manual product references')
        if self.use_rag and self.generation_mode != "llm":
            raise ValueError("RAG is available only with the LLM generator")
        if self.reference_image_ids and self.generation_mode != "llm":
            raise ValueError("Product images require the LLM generator")
        if self.generate_images and self.generation_mode != "llm":
            raise ValueError("Image generation requires the LLM generator")
        return self


class VariantEdit(BaseModel):
    content: str = Field(min_length=1, max_length=3_000)


class VariantReject(BaseModel):
    reason: str = Field(min_length=3, max_length=1_000)


class ScheduleCreate(BaseModel):
    publish_at: datetime

    @model_validator(mode="after")
    def timezone_is_required(self):
        if self.publish_at.tzinfo is None or self.publish_at.utcoffset() is None:
            raise ValueError("publish_at must include a timezone")
        return self


class ScheduleOut(BaseModel):
    destination_account_id: str | None = None
    destination_account_name: str | None = None
    model_config = ConfigDict(from_attributes=True)
    id: int
    variant_id: int
    publish_at: datetime
    status: str
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    image_attached: bool = False


class DeliveryOut(BaseModel):
    stage: str | None = None
    id: int
    schedule_slot_id: int
    platform: str
    idempotency_key: str
    status: str
    attempt_count: int
    external_post_id: str | None
    external_url: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    already_published: bool = False
    image_attached: bool = False


class PublishAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    delivery_id: int
    attempt_number: int
    status: str
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class PublishHistoryOut(BaseModel):
    delivery: DeliveryOut
    attempts: list[PublishAttemptOut]


class GenerationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_post_id: int
    provider: str
    model: str
    prompt_version: str
    examples_version: str
    platforms: str
    audience: str
    goal: str
    tone: str
    call_to_action: str
    temperature: float
    top_p: float
    top_k: int | None
    max_output_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    latency_ms: int | None
    repair_count: int
    rag_enabled: bool = False
    rag_top_k: int | None = None
    rag_model_id: str | None = None
    rag_context: list[dict] | None = None
    rag_min_similarity: float | None = None
    reference_image_ids: list[int] | None = None
    status: str
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class FactCheckClaim(BaseModel):
    claim: str
    verdict: Literal["supported", "contradicted", "unsupported"]
    source_excerpt: str | None
    reason: str


class FactCheckOut(BaseModel):
    id: int
    variant_id: int
    status: Literal["processing", "succeeded", "failed"]
    outcome: Literal["processing", "failed", "unknown", "needs_review", "no_issues"]
    stale: bool
    draft_snapshot: str
    claims: list[FactCheckClaim]
    error_message: str | None
    prompt_version: str
    extraction_model: str
    audit_model: str
    reported_cost_usd: float | None
    latency_ms: int | None
    created_at: datetime
    completed_at: datetime | None


class FixSuggestionRequest(BaseModel):
    fact_check_id: int = Field(gt=0)


class FixSuggestionOut(BaseModel):
    id: int
    variant_id: int
    fact_check_id: int
    status: Literal["processing", "succeeded", "failed"]
    stale: bool
    draft_snapshot: str
    suggested_content: str | None
    model: str
    prompt_version: str
    reported_cost_usd: float | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    applied_at: datetime | None


class CampaignImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_post_id: int
    filename: str
    reference_role: Literal['product','scene'] = 'product'
    width: int
    height: int
    original_width: int
    original_height: int
    created_at: datetime


class VisualRequest(BaseModel):
    vision_model: Literal["google/gemini-2.5-flash", "google/gemini-2.5-pro"] = "google/gemini-2.5-flash"
    image_model: Literal["google/gemini-2.5-flash-image", "google/gemini-3.1-flash-image"] = "google/gemini-2.5-flash-image"
    reference_image_ids: list[int] = Field(min_length=1, max_length=4)
    aspect_ratio: Literal["1:1", "16:9", "4:5"] = "1:1"
    style: Literal["studio product photography", "lifestyle scene", "minimal editorial"] = "studio product photography"
    instructions: str = Field(default="", max_length=1000)
    platform: Literal["campaign", "x", "linkedin", "discord", "instagram"] = "campaign"


class VisualTextEdit(BaseModel):
    headline: str = Field(min_length=1, max_length=100)
    call_to_action: str = Field(min_length=1, max_length=150)


class AttachVisual(BaseModel):
    visual_id: int = Field(gt=0)


class CampaignVisualOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_post_id: int
    platform: str = "campaign"
    vision_model: str
    image_model: str
    aspect_ratio: str
    style: str
    reference_image_ids: list[int]
    context: dict
    status: str
    step: str
    headline: str | None
    call_to_action: str | None
    visual_brief: str | None
    width: int | None
    height: int | None
    usage: dict | None
    latency_ms: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
