"""Attach immutable composed images to posts and scheduled deliveries."""
from alembic import op
import sqlalchemy as sa

revision = "0012_post_media_and_rag_cutoff"
down_revision = "0011_platform_visuals"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("generation_runs", sa.Column("rag_min_similarity", sa.Float(), nullable=True))
    with op.batch_alter_table("platform_variants") as batch:
        batch.add_column(sa.Column("selected_visual_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("selected_image_key", sa.String(200), nullable=True))
        batch.add_column(sa.Column("selected_image_sha256", sa.String(64), nullable=True))
        batch.add_column(sa.Column("image_required", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_variant_selected_visual", "campaign_visuals", ["selected_visual_id"], ["id"], ondelete="SET NULL")
    for table in ("schedule_slots", "publish_deliveries"):
        op.add_column(table, sa.Column("image_key_snapshot", sa.String(200), nullable=True))
        op.add_column(table, sa.Column("image_sha256_snapshot", sa.String(64), nullable=True))


def downgrade():
    for table in ("schedule_slots", "publish_deliveries"):
        op.drop_column(table, "image_sha256_snapshot")
        op.drop_column(table, "image_key_snapshot")
    with op.batch_alter_table("platform_variants") as batch:
        batch.drop_constraint("fk_variant_selected_visual", type_="foreignkey")
        for column in ("image_required", "selected_image_sha256", "selected_image_key", "selected_visual_id"):
            batch.drop_column(column)
    op.drop_column("generation_runs", "rag_min_similarity")
