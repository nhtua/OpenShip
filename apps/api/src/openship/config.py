from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"
    model_max_tokens: int = 4000

    # Database
    database_url: str = "postgresql+psycopg2://postgres:change_me@localhost:5432/openship"

    # JWT
    jwt_secret: str = "dev-secret-change-in-production"
    jwt_expiry_hours: int = 24

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Worker queue settings
    worker_lease_seconds: int = 30
    worker_heartbeat_interval: int = 10
    worker_poll_interval: int = 2
    reconciler_interval: int = 60

    # Retention settings
    event_retention_days: int = 30
    checkpoint_retention_days: int = 30
    log_retention_days: int = 7

    class Config:
        env_file = ".env"


settings = Settings()
