"""Private campaign references and image-generation history."""
from alembic import op
import sqlalchemy as sa

revision = "0010_campaign_images"
down_revision = "0009_rag_generation_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("generation_runs", sa.Column("reference_image_ids", sa.JSON(), nullable=True))
    op.create_table("campaign_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("source_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("original_key", sa.String(200), nullable=False),
        sa.Column("prepared_key", sa.String(200), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("original_width", sa.Integer(), nullable=False),
        sa.Column("original_height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaign_images_source_post_id", "campaign_images", ["source_post_id"])
    op.create_table("campaign_visuals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_post_id", sa.Integer(), sa.ForeignKey("source_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vision_model", sa.String(150), nullable=False),
        sa.Column("image_model", sa.String(150), nullable=False),
        sa.Column("aspect_ratio", sa.String(10), nullable=False),
        sa.Column("style", sa.String(100), nullable=False),
        sa.Column("reference_image_ids", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("step", sa.String(30), nullable=False),
        sa.Column("headline", sa.String(100)),
        sa.Column("call_to_action", sa.String(150)),
        sa.Column("visual_brief", sa.Text()),
        sa.Column("image_key", sa.String(200)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("usage", sa.JSON()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued', 'processing', 'succeeded', 'failed')", name="ck_campaign_visuals_status"),
    )
    op.create_index("ix_campaign_visuals_source_post_id", "campaign_visuals", ["source_post_id"])
    op.create_index("uq_campaign_visuals_active_post", "campaign_visuals", ["source_post_id"], unique=True,
                    sqlite_where=sa.text("status IN ('queued', 'processing')"),
                    postgresql_where=sa.text("status IN ('queued', 'processing')"))


def downgrade():
    op.drop_table("campaign_visuals")
    op.drop_table("campaign_images")
    op.drop_column("generation_runs", "reference_image_ids")
