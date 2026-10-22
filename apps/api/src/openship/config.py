from pathlib import Path
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Model profiles (non-secret). Credentials stay in their own named bindings.
    model_profiles_json: str = ""
    model_default_profile_id: str = ""
    model_max_tokens: int = 4000
    # Installation ceilings, pinned when a run is submitted. Per-request and
    # per-tool network timeouts remain separate from this overall deadline.
    workflow_run_timeout_seconds: int = Field(default=1800, ge=1, le=3600)
    model_context_tokens: int = Field(default=128000, ge=1)
    model_max_context_bytes: int = Field(default=2 * 1024 * 1024, ge=1)

    # Maintained Phase 4 tools. Credentials are separate fixed environment bindings.
    tool_enabled_ids: str = ""
    tool_concurrency: int = Field(default=1, ge=1, le=128)

    # Managed Connector keys are provisioned separately and read lazily by
    # credential setup/dispatch. Missing files do not affect plain chat.
    connector_installation_id: UUID | None = None
    connector_encryption_key_file: Path = Path(
        "/run/secrets/openship-connector-encryption-keys.json"
    )
    connector_idempotency_hmac_key_file: Path = Path(
        "/run/secrets/openship-connector-idempotency-hmac-keys.json"
    )
    connector_operator_user_id: UUID | None = None
    connector_browser_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    connector_setup_max_body_bytes: int = Field(default=32 * 1024, ge=1024, le=128 * 1024)
    connector_write_rate_limit_per_minute: int = Field(default=20, ge=1, le=120)

    # Shared persistent local artifact storage. Initialization is an explicit
    # operator action; API requests never create or replace an empty root.
    artifact_root: Path = Path("var/artifacts")
    artifact_storage_id: UUID | None = None
    artifact_conversation_quota_bytes: int = Field(default=128 * 1024 * 1024, ge=1)
    artifact_conversation_quota_count: int = Field(default=100, ge=1)
    artifact_project_quota_bytes: int = Field(default=1024 * 1024 * 1024, ge=1)
    artifact_project_quota_count: int = Field(default=1000, ge=1)
    artifact_search_reservation_bytes: int = Field(default=64 * 1024, ge=1, le=64 * 1024)
    artifact_upload_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    artifact_incomplete_uploads_per_conversation: int = Field(default=2, ge=1, le=2)
    artifact_staging_retention_seconds: int = Field(default=600, ge=60)

    # Database
    database_url: str = "postgresql+psycopg2://postgres:change_me@localhost:5432/openship"

    # JWT
    jwt_secret: str = "dev-secret-change-in-production-123"
    jwt_expiry_hours: int = 24

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Worker queue settings
    worker_lease_seconds: int = 30
    # How often a running worker renews its lease. It is a write, so it stays
    # infrequent; observing a cancellation is the read below, not this.
    worker_heartbeat_interval: int = 10
    # The supervisor's read while a turn runs. It is what decides how quickly a
    # cancellation or a moved fence interrupts a blocked provider read, so it is
    # well below the two seconds the repo guarantees for local I/O.
    worker_cancellation_poll_seconds: float = 0.5
    worker_poll_interval: int = 2
    reconciler_interval: int = 60

    # Retention settings
    event_retention_days: int = 30
    checkpoint_retention_days: int = 30
    log_retention_days: int = 7

    @property
    def enabled_tool_ids(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.tool_enabled_ids.split(",") if item.strip()
        )


settings = Settings()
