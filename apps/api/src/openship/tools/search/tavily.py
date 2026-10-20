"""Maintained Tavily web-search implementation, independent of Brave."""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import unquote

from pydantic import ValidationError

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef
from src.openship.providers.errors import ProviderError
from src.openship.tools.search.contracts import (
    SearchArguments,
    SearchItem,
    SearchObservation,
)
from src.openship.tools.search.http import (
    TAVILY_SEARCH_ENDPOINT,
    BoundedSearchHttp,
    SearchToolError,
)

_CREDENTIAL_BINDING = "TAVILY_API_KEY"


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise SearchToolError("invalid_response") from None
    parser = _TextOnly()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


def _truncate_utf8(value: str, maximum: int) -> tuple[str, bool]:
    raw = value.encode("utf-8")
    if len(raw) <= maximum:
        return value, False
    return raw[:maximum].decode("utf-8", "ignore"), True


def _reference() -> CapabilityRef:
    manifest = next(
        item for item in list_capability_manifests() if item.id == "tavily_web_search"
    )
    return CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )


async def search_tavily(
    arguments: SearchArguments,
    context,
    control,
) -> SearchObservation:
    """Issue one basic/general Tavily search and return a bounded observation."""
    try:
        credential = context.credential_resolver(_CREDENTIAL_BINDING)
    except Exception:  # noqa: BLE001 - resolver errors can contain secret/config details.
        raise SearchToolError("missing_credentials") from None
    if (
        not isinstance(credential, str)
        or not credential
        or credential != credential.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in credential)
    ):
        raise SearchToolError("missing_credentials")

    request = BoundedSearchHttp(
        TAVILY_SEARCH_ENDPOINT,
        max_response_bytes=min(
            context.execution.limits.max_http_response_bytes,
            2 * 1024 * 1024,
        ),
        timeout_seconds=context.execution.limits.operation_timeout_seconds,
    )
    try:
        payload = await request.post_json(
            json_body={
                "query": arguments.query,
                "search_depth": "basic",
                "topic": "general",
                "max_results": arguments.max_results,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
                "include_image_descriptions": False,
                "include_favicon": False,
                "auto_parameters": False,
                "include_usage": False,
            },
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {credential}",
                "Content-Type": "application/json",
            },
            control=control,
        )
    except SearchToolError:
        raise
    except ProviderError as exc:
        if exc.code == "cancelled":
            raise SearchToolError("cancelled") from None
        raise SearchToolError("provider_unavailable") from None
    except Exception:  # noqa: BLE001 - only fixed, provider-safe failures cross this boundary.
        raise SearchToolError("provider_unavailable") from None

    source_results = payload.get("results")
    if not isinstance(source_results, list):
        raise SearchToolError("invalid_response")

    truncated = len(source_results) > arguments.max_results
    items = []
    for rank, raw in enumerate(source_results[: arguments.max_results], start=1):
        if not isinstance(raw, dict):
            raise SearchToolError("invalid_response")
        title = raw.get("title")
        url = raw.get("url")
        snippet = raw.get("content", "")
        if snippet is None:
            snippet = ""
        if not isinstance(title, str) or not isinstance(url, str) or not isinstance(snippet, str):
            raise SearchToolError("invalid_response")

        title = _plain_text(title)
        snippet = _plain_text(snippet)
        if credential in title or credential in snippet or credential in unquote(url):
            truncated = True
            continue
        try:
            url_bytes = url.encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise SearchToolError("invalid_response") from None
        if len(url_bytes) > 2048:
            truncated = True
            continue
        title, title_truncated = _truncate_utf8(title, 512)
        snippet, snippet_truncated = _truncate_utf8(snippet, 2048)
        truncated = truncated or title_truncated or snippet_truncated
        if not title:
            truncated = True
            continue
        try:
            item = SearchItem(title=title, url=url, snippet=snippet, rank=rank)
        except ValidationError:
            truncated = True
            continue
        items.append(item)

    manifest_ref = _reference()
    try:
        return SearchObservation(
            schema_version=1,
            tool=manifest_ref,
            query=arguments.query,
            observed_at=datetime.now(UTC),
            requested_count=arguments.max_results,
            returned_count=len(items),
            items=tuple(items),
            truncated=truncated,
            provider_metadata={},
        )
    except ValidationError as exc:
        if any(
            "normalized observation exceeds the byte limit" in error["msg"]
            for error in exc.errors()
        ):
            raise SearchToolError("output_limit_exceeded") from None
        raise SearchToolError("invalid_response") from None
