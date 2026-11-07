"""Bounded download input and validated data-file formats."""

import csv
import io
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.openship.artifacts.contracts import ArtifactRef, StagedArtifact


class DownloadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=4096)


class DownloadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: ArtifactRef
    name: str
    format: Literal["csv", "json"]
    sample: str


class DownloadObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes
    name: str
    format: Literal["csv", "json"]
    staged: StagedArtifact | None = None


def validate_download(name: str, content: bytes) -> Literal["csv", "json"]:
    """Validate supported UTF-8 data; do not infer format from an HTTP MIME label."""
    try:
        text = content.decode("utf-8-sig")
        if not text or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise ValueError("invalid text")
        if name.lower().endswith(".json"):
            json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            return "json"
        if name.lower().endswith(".csv"):
            rows = csv.reader(io.StringIO(text, newline=""), strict=True)
            if not next(rows, None):
                raise ValueError("empty CSV")
            for _ in rows:
                pass
            return "csv"
    except (UnicodeError, ValueError, csv.Error, RecursionError):
        raise ValueError("unsupported or invalid data file") from None
    raise ValueError("unsupported or invalid data file")
