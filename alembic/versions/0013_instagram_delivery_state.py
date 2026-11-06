"""Persist Instagram publishing checkpoints for safe recovery."""
from alembic import op
import sqlalchemy as sa

revision = "0013_instagram_delivery_state"
down_revision = "0012_post_media_and_rag_cutoff"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publish_deliveries", sa.Column("provider_state", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("publish_deliveries", "provider_state")
