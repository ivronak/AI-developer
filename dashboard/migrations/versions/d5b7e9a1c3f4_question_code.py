"""approval cards: the exact command, shown verbatim

Revision ID: d5b7e9a1c3f4
Revises: c3a91d5e7f20
Create Date: 2026-10-23 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5b7e9a1c3f4'
down_revision = 'c3a91d5e7f20'
branch_labels = None
depends_on = None


def upgrade():
    # additive only: old cards simply have no code
    with op.batch_alter_table('change_questions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('code', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('change_questions', schema=None) as batch_op:
        batch_op.drop_column('code')
