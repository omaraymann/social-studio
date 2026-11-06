"""Keep scene references distinct from product identity photos."""
from alembic import op
import sqlalchemy as sa

revision='0015_image_reference_roles'
down_revision='0014_instagram_oauth'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('campaign_images',sa.Column('reference_role',sa.String(20),nullable=False,server_default='product'))


def downgrade():
    op.drop_column('campaign_images','reference_role')
