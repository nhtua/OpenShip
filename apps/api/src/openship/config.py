from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Model profiles (non-secret). Credentials stay in their own named bindings.
    model_profiles_json: str = ""
    model_default_profile_id: str = ""
    model_max_tokens: int = 4000

    # Maintained Phase 4 tools. Credentials are separate fixed environment bindings.
    tool_enabled_ids: str = ""

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
