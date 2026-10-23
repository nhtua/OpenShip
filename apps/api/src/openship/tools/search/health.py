"""Small, bounded credential checks for the managed search connectors."""

from __future__ import annotations

from typing import Literal

from .http import (
    BRAVE_SEARCH_ENDPOINT,
    TAVILY_SEARCH_ENDPOINT,
    BoundedSearchHttp,
    SearchToolError,
)

SearchCredentialStatus = Literal["valid", "invalid", "unavailable"]


class _StatusCheckControl:
    """Use a short fixed timeout without depending on a running tool invocation."""

    def request_timeout_seconds(self, limit: float) -> float:
        return min(limit, 10.0)

    def raise_if_stopped(self) -> None:
        return None


async def check_search_api_key(kind: str, api_key: str) -> SearchCredentialStatus:
    """Send one fixed, minimal provider request and discard its search results."""
    control = _StatusCheckControl()
    try:
        if kind == "brave":
            request = BoundedSearchHttp(BRAVE_SEARCH_ENDPOINT, timeout_seconds=10)
            await request.get_json(
                params={
                    "q": "OpenShip connector status check",
                    "count": 1,
                    "text_decorations": "false",
                    "result_filter": "web",
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                control=control,
            )
        elif kind == "tavily":
            request = BoundedSearchHttp(TAVILY_SEARCH_ENDPOINT, timeout_seconds=10)
            await request.post_json(
                json_body={
                    "query": "OpenShip connector status check",
                    "search_depth": "basic",
                    "topic": "general",
                    "max_results": 1,
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
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                control=control,
            )
        else:
            return "unavailable"
    except SearchToolError as exc:
        return "invalid" if exc.code == "authentication_error" else "unavailable"
    except Exception:  # noqa: BLE001 - never expose client, key or provider details.
        return "unavailable"
    return "valid"
