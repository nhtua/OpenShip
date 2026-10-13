"""The public model-profile catalog: what this installation offers, and nothing secret.

A browser has to name a profile before it can ask for a turn, so the catalog
is the only place it can learn what exists. The breaks these catch: a catalog
that leaks a credential value or even the name of the binding it needs, a
profile offered as usable when its binding is absent, and a default that is
not one of the offered profiles.
"""

from fastapi.testclient import TestClient

from .conftest import (
    CREDENTIAL_BINDING,
    FIXTURE_ENDPOINT,
    SENTINEL_CREDENTIAL,
    configure_profiles,
    declare_two_profiles,
    profile_spec,
)

CATALOG = "/api/model-profiles"


def _declared_without_credentials(monkeypatch) -> None:
    """The same installation, with its credential binding absent.

    Only the non-secret configuration is set, so a case can see what a
    profile needs without ever having a value to leak.
    """
    configure_profiles(
        monkeypatch,
        [
            profile_spec("primary-fixture", allowed_fallbacks=["secondary-fixture"]),
            profile_spec("secondary-fixture"),
        ],
        "primary-fixture",
    )
    monkeypatch.delenv(CREDENTIAL_BINDING, raising=False)


def test_catalog_requires_authentication(client: TestClient):
    """An unauthenticated browser learns nothing about an installation."""
    response = client.get(CATALOG)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_catalog_offers_declared_profiles(client: TestClient, authorized_client: dict, monkeypatch):
    """The catalog is the installation's own list, with the default named."""
    declare_two_profiles(monkeypatch)

    response = client.get(CATALOG, headers=authorized_client)
    assert response.status_code == 200, f"catalog failed: {response.text}"

    body = response.json()
    assert body["default_profile_id"] == "primary-fixture"
    assert [profile["profile_id"] for profile in body["profiles"]] == [
        "primary-fixture",
        "secondary-fixture",
    ]

    primary = body["profiles"][0]
    assert primary["label"] == "Fixture profile primary-fixture"
    assert primary["adapter"] == "openai"
    assert primary["model"]
    assert primary["available"] is True
    assert primary["reason"] is None
    assert primary["allowed_fallback_profile_ids"] == ["secondary-fixture"], (
        "the browser can only offer a fallback the profile itself permits"
    )
    assert primary["capabilities"]["token_counting"] is False, (
        "a capability the profile does not qualify is never advertised"
    )


def test_catalog_reveals_no_secret(client: TestClient, authorized_client: dict, monkeypatch):
    """No credential value, binding name, or raw endpoint leaves the browser."""
    declare_two_profiles(monkeypatch)

    response = client.get(CATALOG, headers=authorized_client)
    assert response.status_code == 200, f"catalog failed: {response.text}"

    text = response.text
    assert SENTINEL_CREDENTIAL not in text, "a credential value is never in the catalog"
    assert CREDENTIAL_BINDING not in text, "not even the name of the binding it needs"
    assert FIXTURE_ENDPOINT not in text, "only a destination label, never a full endpoint"
    assert "credential_ref" not in text


def test_unconfigured_profile_explains_configuration_need(client: TestClient, authorized_client: dict, monkeypatch):
    """A profile whose binding is absent is listed, and says why it needs one."""
    _declared_without_credentials(monkeypatch)

    response = client.get(CATALOG, headers=authorized_client)
    assert response.status_code == 200, (
        "an installation with no usable profile is still a working installation"
    )

    body = response.json()
    primary = body["profiles"][0]
    assert primary["available"] is False
    assert primary["reason"] == "missing_credentials"
    assert primary["explanation"], "the browser needs words it can act on"
    assert SENTINEL_CREDENTIAL not in response.text


def test_unregistered_adapter_explains_the_gap(client: TestClient, authorized_client: dict, monkeypatch):
    """A profile naming an adapter this build does not have is not offered as usable."""
    configure_profiles(
        monkeypatch,
        [
            {
                **profile_spec("future-fixture"),
                "adapter": "anthropic",
                "api_version": "messages",
                "adapter_options": {"adapter": "anthropic", "server_side_fallback": False},
            }
        ],
        "future-fixture",
    )

    response = client.get(CATALOG, headers=authorized_client)
    assert response.status_code == 200, f"catalog failed: {response.text}"

    profile = response.json()["profiles"][0]
    assert profile["available"] is False
    assert profile["reason"] == "adapter_unavailable"


def test_catalog_reads_today_installation(client: TestClient, authorized_client: dict, monkeypatch):
    """The catalog is the installation's live view; only a run's own snapshot stays put."""
    declare_two_profiles(monkeypatch)

    first = client.get(CATALOG, headers=authorized_client)
    assert first.status_code == 200, f"catalog failed: {first.text}"
    assert [profile["profile_id"] for profile in first.json()["profiles"]] == [
        "primary-fixture",
        "secondary-fixture",
    ]

    configure_profiles(monkeypatch, [profile_spec("only-fixture")], "only-fixture")

    second = client.get(CATALOG, headers=authorized_client)
    assert second.status_code == 200, f"catalog failed: {second.text}"
    assert [profile["profile_id"] for profile in second.json()["profiles"]] == ["only-fixture"]
    assert second.json()["default_profile_id"] == "only-fixture"
