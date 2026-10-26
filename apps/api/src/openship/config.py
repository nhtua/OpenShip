from pathlib import Path
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True
    )

    # Model profiles (non-secret). Credentials stay in their own named bindings.
    model_profiles_json: str = ""
    model_default_profile_id: str = ""
    model_max_tokens: int = 4000
    # Installation ceilings, pinned when a run is submitted. Per-request and
    # per-tool network timeouts remain separate from this overall deadline.
    workflow_run_timeout_seconds: int = Field(default=1800, ge=1, le=3600)
    model_context_tokens: int = Field(default=128000, ge=1)
    model_max_context_bytes: int = Field(default=2 * 1024 * 1024, ge=1)

    # Optional deployment ceiling for maintained tools. Unset leaves normal
    # enablement to the persisted operator policy; an explicit empty value
    # preserves a legacy chat-only deployment.
    tool_enabled_ids: str | None = None
    tool_concurrency: int = Field(default=1, ge=1, le=128)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Keep the existing ignore-empty behavior for every setting except
        # TOOL_ENABLED_IDS, where an explicit empty string means an empty
        # deployment ceiling. Pydantic constructs both sources using the
        # model-wide ignore-empty flag, so reload and filter their values here.
        for source in (env_settings, dotenv_settings):
            source.env_ignore_empty = False
            loaded = source._load_env_vars()
            filtered = {}
            prefix = (source.env_prefix or "").lower()
            for key, value in loaded.items():
                normalized = key.lower()
                if prefix and normalized.startswith(prefix):
                    normalized = normalized[len(prefix) :]
                if value == "" and normalized != "tool_enabled_ids":
                    continue
                filtered[key] = value
            source.env_vars = filtered
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    @property
    def tool_enabled_ceiling(self) -> frozenset[str] | None:
        if self.tool_enabled_ids is None:
            return None
        return frozenset(
            item.strip() for item in self.tool_enabled_ids.split(",") if item.strip()
        )

    # Managed Connector keys are provisioned separately and read lazily by
    # credential setup/dispatch. Missing files do not affect plain chat.
    connector_installation_id: UUID | None = None
    connector_encryption_key_file: Path = Path(
        "/run/secrets/openship-connector-encryption-keys.json"
    )
    connector_idempotency_hmac_key_file: Path = Path(
        "/run/secrets/openship-connector-idempotency-hmac-keys.json"
    )
    connector_dispatch_lock_file: Path = Path(
        "/run/secrets/openship-managed-dispatch.lock"
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
    artifact_cloud_observation_reservation_bytes: int = Field(
        default=64 * 1024, ge=1, le=64 * 1024
    )
    artifact_upload_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    artifact_incomplete_uploads_per_conversation: int = Field(default=2, ge=1, le=2)
    artifact_staging_retention_seconds: int = Field(default=600, ge=60)

    # Database
    database_url: str = "postgresql+psycopg2://postgres:change_me@localhost:5432/openship"

    # JetStream transport. Role credentials live in a protected JSON file with
    # top-level `user` and `password` keys; they never belong in the server URL.
    nats_servers: str = "nats://127.0.0.1:4222"
    nats_auth_file: Path | None = None
    nats_tls_ca_file: Path | None = None
    messaging_publish_batch_size: int = Field(default=32, ge=1, le=256)
    messaging_publish_lease_seconds: int = Field(default=30, ge=5, le=300)
    # Aggregate broker capacity is an explicit installation declaration. Multiple
    # processes must budget their combined slots before changing this topology.
    messaging_run_max_ack_pending: int = Field(default=4, ge=1, le=1024)
    messaging_maintenance_max_ack_pending: int = Field(default=1, ge=1, le=1024)
    worker_run_concurrency: int = Field(default=4, ge=1, le=128)
    worker_background_concurrency: int = Field(default=1, ge=1, le=128)

    @model_validator(mode="after")
    def validate_messaging_capacity(self):
        if self.worker_run_concurrency > self.messaging_run_max_ack_pending:
            raise ValueError("worker run concurrency exceeds aggregate run capacity")
        if (
            self.worker_background_concurrency
            > self.messaging_maintenance_max_ack_pending
        ):
            raise ValueError(
                "worker background concurrency exceeds aggregate maintenance capacity"
            )
        return self

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
        """Compatibility view; authorization callers must use persisted policy."""
        ceiling = self.tool_enabled_ceiling
        if ceiling is not None:
            return ceiling
        from src.openship.capabilities.catalog import list_installed_tool_definitions

        return frozenset(
            item.id for item in list_installed_tool_definitions()
        )


settings = Settings()
