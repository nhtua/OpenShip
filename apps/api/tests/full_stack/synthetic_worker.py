"""Run the real durable worker with a synthetic, cancellable search transport."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from src.openship.tools.search import http as search_http


def _record_call(
    call_log: Path, provider: str, query: str, credential_label: str | None = None
) -> None:
    call = {"provider": provider, "query": query}
    if credential_label is not None:
        call["credential_label"] = credential_label
    record = (json.dumps(call) + "\n").encode("utf-8")
    descriptor = os.open(call_log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, record)
    finally:
        os.close(descriptor)


class _SyntheticBody(httpx.AsyncByteStream):
    def __init__(self, payload: dict):
        self._content = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    async def __aiter__(self):
        yield self._content

    async def aclose(self) -> None:
        return None


class SyntheticSearchTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https":
            raise httpx.UnsupportedProtocol("synthetic search accepts only HTTPS-shaped requests")
        provider = request.url.host
        if provider == "api.search.brave.com":
            if request.method != "GET" or request.url.path != "/res/v1/web/search":
                raise httpx.UnsupportedProtocol("unexpected Brave request")
            query = request.url.params.get("q", "")
            presented_key = request.headers.get("X-Subscription-Token", "")
        elif provider == "api.tavily.com":
            if request.method != "POST" or request.url.path != "/search":
                raise httpx.UnsupportedProtocol("unexpected Tavily request")
            body = json.loads(await request.aread())
            query = body.get("query", "")
            authorization = request.headers.get("Authorization", "")
            presented_key = authorization.removeprefix("Bearer ")
        else:
            raise httpx.UnsupportedProtocol("unexpected search provider")

        call_log = Path(os.environ["SYNTHETIC_SEARCH_CALL_LOG"])
        credential_map_json = os.environ.get("SYNTHETIC_SEARCH_CREDENTIALS_JSON")
        if credential_map_json:
            credential_map = json.loads(credential_map_json)
            credential_label = next(
                (label for label, expected in credential_map.items() if expected == presented_key),
                "unmatched",
            )
            _record_call(call_log, provider, query, credential_label)
            if credential_label == "unmatched":
                return httpx.Response(
                    401,
                    headers={"content-type": "application/json"},
                    stream=_SyntheticBody({"error": "synthetic credential rejected"}),
                    request=request,
                )
        else:
            _record_call(call_log, provider, query)

        if query == "cancel-me" or query == "cancel full-stack dispatch":
            await asyncio.sleep(3600)
        elif query.startswith("hold:"):
            await asyncio.sleep(float(os.environ.get("SYNTHETIC_HOLD_SECONDS", "1")))

        if provider == "api.search.brave.com":
            payload = {
                "web": {
                    "results": [
                        {
                            "title": "Synthetic Brave result",
                            "url": "https://search.example.test/brave",
                            "description": "A fixture response from the Brave transport.",
                        }
                    ]
                }
            }
        else:
            payload = {
                "results": [
                    {
                        "title": "Synthetic Tavily result",
                        "url": "https://search.example.test/tavily",
                        "content": "A fixture response from the Tavily transport.",
                        "score": 0.95,
                    }
                ]
            }
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_SyntheticBody(payload),
            request=request,
        )

    async def aclose(self) -> None:
        return None


def _synthetic_transport(_endpoint: str) -> SyntheticSearchTransport:
    return SyntheticSearchTransport()


def main() -> None:
    # The endpoint and provider adapters stay production code. This worker
    # process substitutes only the network transport with deterministic local
    # JSON responses and records request identities without recording headers.
    search_http.PinnedHttpTransport = _synthetic_transport
    from tests.full_stack.legacy_worker_harness import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
