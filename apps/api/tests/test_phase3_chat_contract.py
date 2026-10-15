"""Phase 3 uses durable run commands and explicitly declared model profiles."""

import json
import uuid

import pytest

from src.openship.config import Settings
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import load_profiles
from tests.conftest import profile_spec


@pytest.mark.parametrize("profiles", ["", "[]"])
def test_single_provider_settings_cannot_create_an_implicit_profile(profiles):
    settings = Settings(
        _env_file=None,
        model_profiles_json=profiles,
        model_default_profile_id="",
        openai_api_key="fixture-only-value",
        openai_model="fixture-only-model",
        openai_base_url="https://fixture.provider.test/v1",
    )

    with pytest.raises(ProviderError) as refused:
        load_profiles(settings)

    assert refused.value.code == "configuration_error"
    assert "MODEL_PROFILES_JSON" in refused.value.safe_message


def test_explicit_profiles_require_a_named_default():
    settings = Settings(
        _env_file=None,
        model_profiles_json=json.dumps([profile_spec("primary-fixture")]),
        model_default_profile_id="",
    )

    with pytest.raises(ProviderError) as refused:
        load_profiles(settings)

    assert refused.value.code == "configuration_error"
    assert "MODEL_DEFAULT_PROFILE_ID" in refused.value.safe_message


def test_retired_chat_endpoint_is_not_registered(client, authorized_client):
    response = client.post(
        f"/api/chat/{uuid.uuid4()}/messages",
        json={"content": "This endpoint has been removed"},
        headers=authorized_client,
    )

    assert response.status_code == 404
    assert not any(path.startswith("/api/chat/") for path in client.get("/openapi.json").json()["paths"])


def test_unconfigured_catalog_reports_a_safe_configuration_error(client, authorized_client, monkeypatch):
    monkeypatch.setattr("src.openship.config.settings.model_profiles_json", "")
    monkeypatch.setattr("src.openship.config.settings.model_default_profile_id", "")

    response = client.get("/api/model-profiles", headers=authorized_client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "configuration_error"
    assert "MODEL_PROFILES_JSON" in response.json()["error"]["message"]
