"""Persist portable version-pinned test plans."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('plans', sa.Column('id', sa.String(), primary_key=True), sa.Column('document', sa.JSON(), nullable=False), sa.Column('updated_at', sa.String(), nullable=False))


def downgrade():
    op.drop_table('plans')
