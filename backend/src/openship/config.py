"""OpenShip configuration."""

import os
from typing import Optional


class Config:
    """Application configuration."""

    # LLM Configuration
    LLM_API_KEY: Optional[str] = os.getenv("OPENS_LLM_API_KEY") or os.getenv(
        "OPENAI_API_KEY"
    )
    LLM_BASE_URL: str = os.getenv("OPENS_LLM_BASE_URL", "http://127.0.0.1:1212/v1")
    LLM_MODEL: str = os.getenv(
        "OPENS_LLM_MODEL",
        "qwen3.8-27b-turbo-fable-cold-fusion-735-882-heretic-uncensored-neo-coder-max-mtp",
    )
    LLM_PROVIDER: str = os.getenv(
        "OPENS_LLM_PROVIDER", "local"
    )  # openai, anthropic, local

    # Cloud Provider Configuration (mocked for testing)
    CLOUD_PROVIDER: str = os.getenv("OPENS_CLOUD_PROVIDER", "aws")  # aws, gcp, azure
    CLOUD_REGION: str = os.getenv("OPENS_CLOUD_REGION", "us-east-1")
    CLOUD_MOCK: bool = os.getenv("OPENS_CLOUD_MOCK", "true").lower() == "true"

    # Terraform Configuration
    TERRAFORM_BACKEND: str = os.getenv(
        "OPENS_TF_BACKEND", "local"
    )  # local, s3, gcs, azurerm
    TERRAFORM_APPLY: bool = os.getenv("OPENS_TF_APPLY", "false").lower() == "true"


config = Config()

