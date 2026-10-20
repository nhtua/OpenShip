from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.contracts import CapabilityRef


class SearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SearchArguments(SearchContract):
    query: str = Field(min_length=1, max_length=512)
    max_results: StrictInt = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def _valid_query(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("query must be trimmed")
        if len(value.encode("utf-8")) > 2048:
            raise ValueError("query exceeds the UTF-8 byte limit")
        if len(value.split()) > 75:
            raise ValueError("query exceeds the word limit")
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError("query contains a control character")
        return value


class SearchItem(SearchContract):
    title: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=2048)
    snippet: str = Field(max_length=2048)
    rank: StrictInt = Field(ge=1, le=10)

    @field_validator("title")
    @classmethod
    def _title_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 512:
            raise ValueError("title exceeds the UTF-8 byte limit")
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError("title contains a control character")
        return value

    @field_validator("url")
    @classmethod
    def _safe_url(cls, value: str) -> str:
        if any(unicodedata.category(char) == "Cc" or char.isspace() for char in value):
            raise ValueError("result URL contains whitespace or control characters")
        try:
            parts = urlsplit(value)
            _ = parts.port
        except ValueError as exc:
            raise ValueError("result URL is invalid") from exc
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            raise ValueError(
                "result URL must be an absolute HTTP(S) URL without user information"
            )
        return value

    @field_validator("snippet")
    @classmethod
    def _snippet_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 2048:
            raise ValueError("snippet exceeds the UTF-8 byte limit")
        return value


class SearchObservation(SearchContract):
    schema_version: int = 1
    tool: CapabilityRef
    query: str = Field(min_length=1, max_length=512)
    observed_at: datetime
    requested_count: StrictInt = Field(ge=1, le=10)
    returned_count: StrictInt = Field(ge=0, le=10)
    items: tuple[SearchItem, ...] = Field(max_length=10)
    truncated: StrictBool = False
    provider_metadata: dict[str, str | int | float | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent_counts_and_size(self) -> SearchObservation:
        if self.returned_count != len(self.items):
            raise ValueError("returned_count must match items")
        if len({item.rank for item in self.items}) != len(self.items):
            raise ValueError("result ranks must be unique")
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("normalized observation exceeds the byte limit")
        return self


class SearchToolResult(SearchContract):
    operation_id: str = Field(min_length=1, max_length=64)
    tool: CapabilityRef
    observed_at: datetime
    query: str = Field(min_length=1, max_length=512)
    items: tuple[SearchItem, ...] = Field(max_length=10)
    truncated: StrictBool = False
    artifact: ArtifactRef

    @model_validator(mode="after")
    def _bounded_model_result(self) -> SearchToolResult:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 16 * 1024:
            raise ValueError("model-facing search result exceeds the byte limit")
        return self
