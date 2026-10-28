"""NATS connection configuration keeps role credentials private."""

import pytest

from src.openship.config import settings
from src.openship.messaging import connection
from src.openship.messaging.connection import (
    connect_nats,
    load_credentials,
    parse_servers,
)


def test_load_credentials_accepts_private_role_file_and_redacts_password(tmp_path):
    path = tmp_path / "relay.json"
    path.write_text('{"user":"relay","password":"test-password"}')
    path.chmod(0o600)

    credentials = load_credentials(path)

    assert credentials.user == "relay"
    assert "test-password" not in repr(credentials)
    assert "<redacted>" in repr(credentials)


def test_load_credentials_rejects_group_or_world_access(tmp_path):
    path = tmp_path / "relay.json"
    path.write_text('{"user":"relay","password":"test-password"}')
    path.chmod(0o640)

    with pytest.raises(ValueError, match="not be accessible"):
        load_credentials(path)


def test_parse_servers_trims_multiple_urls_and_rejects_other_schemes():
    assert parse_servers(" nats://one:4222, tls://two:4222 ") == [
        "nats://one:4222",
        "tls://two:4222",
    ]
    with pytest.raises(ValueError, match="NATS_SERVERS"):
        parse_servers("http://not-nats")


@pytest.mark.asyncio
async def test_connect_nats_forwards_scoped_inbox_prefix(tmp_path, monkeypatch):
    auth_path = tmp_path / "api.json"
    auth_path.write_text('{"user":"api","password":"test-password"}')
    auth_path.chmod(0o600)
    monkeypatch.setattr(settings, "nats_auth_file", auth_path)
    monkeypatch.setattr(settings, "nats_servers", "nats://localhost:4222")
    sentinel = object()
    received = {}

    async def fake_connect(**options):
        received.update(options)
        return sentinel

    monkeypatch.setattr(connection.nats, "connect", fake_connect)

    result = await connect_nats(
        name="openship-api-events",
        inbox_prefix=b"openship.installation.v1.inbox.api",
    )

    assert result is sentinel
    assert received["inbox_prefix"] == b"openship.installation.v1.inbox.api"


@pytest.mark.asyncio
async def test_tls_ca_initialization_error_does_not_include_file_path(
    tmp_path, monkeypatch
):
    auth_path = tmp_path / "relay.json"
    auth_path.write_text('{"user":"relay","password":"test-password"}')
    auth_path.chmod(0o600)
    ca_path = tmp_path / "private-ca.pem"
    monkeypatch.setattr(settings, "nats_auth_file", auth_path)
    monkeypatch.setattr(settings, "nats_tls_ca_file", ca_path)

    with pytest.raises(ValueError) as error:
        await connect_nats(name="openship-tls-config-test")

    assert (
        str(error.value)
        == "NATS_TLS_CA_FILE could not initialize certificate validation"
    )
    assert str(ca_path) not in str(error.value)
