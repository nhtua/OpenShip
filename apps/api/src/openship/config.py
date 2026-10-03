from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    # Database
    database_url: str = "postgresql://openship:openship@localhost:5432/openship"

    # JWT
    jwt_secret: str = "dev-secret-change-in-production"
    jwt_expiry_hours: int = 24

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_file = ".env"


settings = Settings()
