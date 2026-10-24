"""Allow connector deletion to unlink, but not edit, append-only audit records.

Revision ID: phase5a009e
Revises: phase5a009d
"""

from collections.abc import Sequence

from alembic import op

revision: str = "phase5a009e"
down_revision: str | None = "phase5a009d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE OR REPLACE FUNCTION openship_connector_audit_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF TG_OP = 'UPDATE' THEN
                    IF OLD.connector_id IS NOT NULL
                        AND NEW.connector_id IS NULL
                        AND NEW.id = OLD.id
                        AND NEW.visibility = OLD.visibility
                        AND NEW.project_id IS NOT DISTINCT FROM OLD.project_id
                        AND NEW.consuming_project_id IS NOT DISTINCT FROM OLD.consuming_project_id
                        AND NEW.connector_revision IS NOT DISTINCT FROM OLD.connector_revision
                        AND NEW.actor_user_id IS NOT DISTINCT FROM OLD.actor_user_id
                        AND NEW.action = OLD.action
                        AND NEW.outcome = OLD.outcome
                        AND NEW.details_json = OLD.details_json
                        AND NEW.created_at = OLD.created_at THEN
                        RETURN NEW;
                    END IF;
                END IF;
                RAISE EXCEPTION 'Connector audit events are append-only';
            END $$"""
        )
        return

    op.execute("DROP TRIGGER trg_connector_audit_append_only_update")
    op.execute(
        """CREATE TRIGGER trg_connector_audit_append_only_update BEFORE UPDATE ON connector_audit_events
        WHEN NOT (
            OLD.connector_id IS NOT NULL AND NEW.connector_id IS NULL
            AND NEW.id IS OLD.id
            AND NEW.visibility IS OLD.visibility
            AND NEW.project_id IS OLD.project_id
            AND NEW.consuming_project_id IS OLD.consuming_project_id
            AND NEW.connector_revision IS OLD.connector_revision
            AND NEW.actor_user_id IS OLD.actor_user_id
            AND NEW.action IS OLD.action
            AND NEW.outcome IS OLD.outcome
            AND NEW.details_json IS OLD.details_json
            AND NEW.created_at IS OLD.created_at
        ) BEGIN SELECT RAISE(ABORT, 'Connector audit events are append-only'); END"""
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """CREATE OR REPLACE FUNCTION openship_connector_audit_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Connector audit events are append-only';
            END $$"""
        )
        return

    op.execute("DROP TRIGGER trg_connector_audit_append_only_update")
    op.execute(
        """CREATE TRIGGER trg_connector_audit_append_only_update BEFORE UPDATE ON connector_audit_events
        BEGIN SELECT RAISE(ABORT, 'Connector audit events are append-only'); END"""
    )
