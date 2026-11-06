"""Link campaign images to the platform whose draft planned them."""
from alembic import op
import sqlalchemy as sa

revision = "0011_platform_visuals"
down_revision = "0010_campaign_images"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("campaign_visuals", sa.Column("platform", sa.String(30), nullable=False, server_default="campaign"))
    op.drop_index("uq_campaign_visuals_active_post", table_name="campaign_visuals")
    op.create_index("uq_campaign_visuals_active_post", "campaign_visuals", ["source_post_id", "platform"], unique=True,
                    sqlite_where=sa.text("status IN ('queued', 'processing')"),
                    postgresql_where=sa.text("status IN ('queued', 'processing')"))


def downgrade():
    op.drop_index("uq_campaign_visuals_active_post", table_name="campaign_visuals")
    # Multiple platform jobs may be active. Preserve their history but mark them
    # interrupted before restoring the older single-job constraint.
    op.execute("UPDATE campaign_visuals SET status='failed', step='failed', "
               "error_message='Interrupted by schema downgrade' WHERE status IN ('queued', 'processing')")
    op.drop_column("campaign_visuals", "platform")
    op.create_index("uq_campaign_visuals_active_post", "campaign_visuals", ["source_post_id"], unique=True,
                    sqlite_where=sa.text("status IN ('queued', 'processing')"),
                    postgresql_where=sa.text("status IN ('queued', 'processing')"))
