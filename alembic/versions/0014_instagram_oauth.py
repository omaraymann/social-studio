"""Owner-scoped encrypted Instagram connections and OAuth state."""
from alembic import op
import sqlalchemy as sa

revision = "0014_instagram_oauth"
down_revision = "0013_instagram_delivery_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("instagram_connections",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("instagram_user_id", sa.String(100), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disconnected', 'expired', 'needs_reconnect')", name="ck_instagram_connection_status"))
    op.create_index("uq_instagram_connected_account", "instagram_connections", ["instagram_user_id"], unique=True,
                    sqlite_where=sa.text("status = 'active'"), postgresql_where=sa.text("status = 'active'"))
    op.create_table("instagram_oauth_attempts",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.add_column("schedule_slots", sa.Column("destination_account_id", sa.String(100), nullable=True))
    op.add_column("schedule_slots", sa.Column("destination_account_name", sa.String(100), nullable=True))


def downgrade():
    op.drop_column("schedule_slots", "destination_account_name")
    op.drop_column("schedule_slots", "destination_account_id")
    op.drop_table("instagram_oauth_attempts")
    op.drop_table("instagram_connections")
