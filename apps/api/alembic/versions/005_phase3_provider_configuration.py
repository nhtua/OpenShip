"""add run-pinned model configuration and provider attempts

Snapshots are nullable and never backfilled: a run from before profiles keeps a
null snapshot and is reported as "Provider not recorded". Nothing here invents
a provider for a historical run.

Downgrading is a decision, not a default: with any Phase 3 snapshot or recorded
attempt present, this revision refuses rather than erasing what a run pinned.

Revision ID: hij012
Revises: ghi789
Create Date: 2026-10-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'hij012'
down_revision: Union[str, None] = 'ghi789'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _count_protected_data(bind) -> int:
    """How much pinned configuration a downgrade would erase."""
    from sqlalchemy import inspect as sa_inspect

    columns = {c['name'] for c in sa_inspect(bind).get_columns('runs')}
    if 'model_configuration' not in columns:
        return 0
    snapshots = bind.execute(
        sa.text("SELECT COUNT(*) FROM runs WHERE model_configuration IS NOT NULL")
    ).fetchone()[0]
    attempts = bind.execute(sa.text("SELECT COUNT(*) FROM provider_attempts")).fetchone()[0]
    return int(snapshots or 0) + int(attempts or 0)


def upgrade() -> None:
    # The run's own provider configuration, frozen at submission.
    with op.batch_alter_table('runs') as batch_op:
        batch_op.add_column(sa.Column('model_selection', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('model_configuration', sa.Text(), nullable=True))

    # One row per bounded generation request a worker made for a run.
    op.create_table(
        'provider_attempts',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('fence', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.String(128), nullable=False),
        sa.Column('profile_revision', sa.String(64), nullable=False),
        sa.Column('adapter', sa.String(32), nullable=False),
        sa.Column('requested_model', sa.String(128), nullable=False),
        sa.Column('actual_model', sa.String(128), nullable=True),
        sa.Column('destination_label', sa.String(120), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('outcome', sa.String(32), nullable=False, server_default='started'),
        sa.Column('error_code', sa.String(50), nullable=True),
        sa.Column('usage', sa.Text(), nullable=True),
        sa.Column('reserved_cost_usd', sa.Numeric(12, 6), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('started', 'succeeded', 'failed', 'cancelled')",
            name='ck_provider_attempts_outcome',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'ordinal', name='uq_provider_attempts_run_id_ordinal'),
    )
    op.create_index('ix_provider_attempts_run_id', 'provider_attempts', ['run_id'])


def downgrade() -> None:
    bind = op.get_bind()
    protected = _count_protected_data(bind)
    if protected:
        raise ValueError(
            "refusing to downgrade: this installation has pinned model "
            f"configuration and provider attempts ({protected} recorded). Erasing "
            "them is a destructive decision; keep this revision or remove the "
            "data first."
        )

    op.drop_index('ix_provider_attempts_run_id', table_name='provider_attempts')
    op.drop_table('provider_attempts')
    with op.batch_alter_table('runs') as batch_op:
        batch_op.drop_column('model_configuration')
        batch_op.drop_column('model_selection')
