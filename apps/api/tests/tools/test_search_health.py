import asyncio

import pytest

from src.openship.tools.search import health
from src.openship.tools.search.http import SearchToolError


@pytest.mark.parametrize("kind", ["brave", "tavily"])
def test_search_credential_check_makes_one_minimal_request_and_discards_results(
    monkeypatch, kind
):
    calls = []

    class FakeSearchHttp:
        def __init__(self, endpoint, **kwargs):
            calls.append(("init", endpoint, kwargs))

        async def get_json(self, **kwargs):
            calls.append(("get", kwargs))
            return {"web": {"results": [{"title": "unused response"}]}}

        async def post_json(self, **kwargs):
            calls.append(("post", kwargs))
            return {"results": [{"title": "unused response"}]}

    monkeypatch.setattr(health, "BoundedSearchHttp", FakeSearchHttp)
    assert asyncio.run(health.check_search_api_key(kind, "secret-sentinel")) == "valid"
    assert calls[0][2] == {"timeout_seconds": 10}
    assert calls[1][1]["headers"]
    assert len(calls) == 2
    if kind == "brave":
        assert calls[1][0] == "get"
        assert calls[1][1]["params"]["count"] == 1
        assert calls[1][1]["headers"]["X-Subscription-Token"] == "secret-sentinel"
    else:
        assert calls[1][0] == "post"
        assert calls[1][1]["json_body"]["max_results"] == 1
        assert calls[1][1]["headers"]["Authorization"] == "Bearer secret-sentinel"
    assert "unused response" not in str(calls)


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [("authentication_error", "invalid"), ("provider_unavailable", "unavailable")],
)
def test_search_credential_check_returns_closed_status_codes(
    monkeypatch, error_code, expected
):
    class FakeSearchHttp:
        def __init__(self, *_args, **_kwargs):
            pass

        async def get_json(self, **_kwargs):
            raise SearchToolError(error_code)

        async def post_json(self, **_kwargs):
            raise SearchToolError(error_code)

    monkeypatch.setattr(health, "BoundedSearchHttp", FakeSearchHttp)
    assert (
        asyncio.run(health.check_search_api_key("brave", "secret-sentinel")) == expected
    )


def test_search_credential_check_rejects_unknown_provider_without_network(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("unexpected HTTP call")

    monkeypatch.setattr(health, "BoundedSearchHttp", unexpected)
    assert (
        asyncio.run(health.check_search_api_key("other", "secret-sentinel"))
        == "unavailable"
    )
