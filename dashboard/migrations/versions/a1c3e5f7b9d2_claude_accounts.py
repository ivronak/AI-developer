"""Claude accounts (web login, several accounts) + the account picked per change; the setup-token is retired

Revision ID: a1c3e5f7b9d2
Revises: f8a2c4e6b1d3
Create Date: 2026-11-04 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c3e5f7b9d2'
down_revision = 'f8a2c4e6b1d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'claude_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('slug', sa.String(length=16), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='new'),
        sa.Column('plan', sa.String(length=30), nullable=False, server_default=''),
        sa.Column('email_hint', sa.String(length=60), nullable=False, server_default=''),
        sa.Column('models', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('connectors', sa.JSON(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('checked_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('alerted', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('claude_accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_claude_accounts_created_at'), ['created_at'], unique=False)
    with op.batch_alter_table('changes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('agent_account_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_changes_agent_account_id'), ['agent_account_id'], unique=False)

    conn = op.get_bind()
    # an existing install: the server user's own web login (made by hand on the server before this) becomes the first,
    # default account. Its status is learned on the first check; a fresh database starts with no account at all.
    if conn.execute(sa.text("SELECT COUNT(*) FROM app_settings")).scalar():
        conn.execute(sa.text("INSERT INTO claude_accounts (name, slug, is_default, status, plan, email_hint, models, last_error, alerted, created_at) "
                             "VALUES ('Main account', '', 1, 'new', '', '', '[]', '', 0, CURRENT_TIMESTAMP)"))
    # the owner retired the setup-token (2026-11-04): every Claude Code run now uses a web-login account
    conn.execute(sa.text("UPDATE app_settings SET value = '\"\"' WHERE key = 'cc_oauth_token_enc'"))
    conn.execute(sa.text("DELETE FROM app_settings WHERE key IN ('cc_login_mode', 'cc_accounts_check')"))


def downgrade():
    # the cleared token cannot come back (it was removed on purpose); runs fall back to the API key
    with op.batch_alter_table('changes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_changes_agent_account_id'))
        batch_op.drop_column('agent_account_id')
    with op.batch_alter_table('claude_accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_claude_accounts_created_at'))
    op.drop_table('claude_accounts')
