"""Strict, bounded profile snapshots shared by the runtime and host bridge."""

from __future__ import annotations

import math
from typing import Any, Literal, TypeAlias

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from yaml.events import (
    AliasEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

from src.openship.capabilities.contracts import canonical_digest, canonical_json

ProfileId: TypeAlias = Literal["logs-data", "iac-validation", "standard", "devops-shell"]
MAX_YAML_BYTES = 256 * 1024
_DIGEST = r"^[0-9a-f]{64}$"
_IMAGE_ID = r"^sha256:[0-9a-f]{64}$"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _bounded(value: str, limit: int) -> str:
    if len(value.encode("utf-8")) > limit:
        raise ValueError("string exceeds UTF-8 byte limit")
    return value


class ProfileMetadata(_Contract):
    label: str
    description: str
    use_for: tuple[str, ...] = Field(max_length=8)
    highlights: tuple[str, ...] = Field(max_length=8)
    limitations: tuple[str, ...] = Field(max_length=8)

    @field_validator("label")
    @classmethod
    def label_bytes(cls, value: str) -> str:
        return _bounded(value, 80)

    @field_validator("description")
    @classmethod
    def description_bytes(cls, value: str) -> str:
        return _bounded(value, 512)

    @field_validator("use_for", "highlights", "limitations")
    @classmethod
    def list_bytes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _bounded(item, 160)
        return value


class ProfileBinding(_Contract):
    image_tag: str = Field(min_length=1, max_length=512)
    image_id: str = Field(pattern=_IMAGE_ID)
    dependency_identity: str = Field(min_length=1, max_length=128)
    platform: Literal["linux/amd64"]
    fingerprint: str = Field(pattern=_DIGEST)
    engine_identity: str = Field(min_length=1, max_length=512)
    smoke_image_id: str = Field(pattern=_IMAGE_ID)
    smoke_fingerprint: str = Field(pattern=_DIGEST)
    smoke_passed: bool
    smoke_evidence_digest: str = Field(pattern=_DIGEST)

    @model_validator(mode="after")
    def matching_smoke(self) -> ProfileBinding:
        if (not self.smoke_passed or self.image_id != self.smoke_image_id
                or self.fingerprint != self.smoke_fingerprint):
            raise ValueError("smoke evidence does not match image and fingerprint")
        return self


class ProfileEntry(_Contract):
    enabled: bool
    extensions: dict[str, str] = Field(max_length=16)
    metadata: ProfileMetadata
    metadata_digest: str = Field(pattern=_DIGEST)
    runtime: ProfileBinding

    @field_validator("extensions")
    @classmethod
    def extension_bytes(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            _bounded(key, 64)
            _bounded(item, 256)
        return value

    @model_validator(mode="after")
    def matching_metadata(self) -> ProfileEntry:
        if self.metadata_digest != canonical_digest(self.metadata.model_dump(mode="json")):
            raise ValueError("metadata digest mismatch")
        return self


class ProfileCatalog(_Contract):
    schema_version: Literal[1]
    profiles: dict[ProfileId, ProfileEntry] = Field(max_length=4)

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_is_integer(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value


class _Loader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        result = {}
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise ValueError("merge keys are not allowed")
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise TypeError("mapping keys must be strings")
            if key in result:
                raise ValueError("duplicate mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _json_types(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _json_types(item)
    elif isinstance(value, list):
        for item in value:
            _json_types(item)
    elif value is None or type(value) in (str, bool, int) or type(value) is float and math.isfinite(value):
        return
    else:
        raise ValueError("non-JSON YAML scalar")


def decode_catalog(data: bytes) -> ProfileCatalog:
    if len(data) > MAX_YAML_BYTES:
        raise ValueError("catalog exceeds YAML byte limit")
    try:
        text = data.decode("utf-8")
        for token in yaml.scan(text, Loader=_Loader):
            if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise ValueError("YAML anchors, aliases, and explicit tags are forbidden")  # noqa: TRY004
        depth = scalars = documents = 0
        for event in yaml.parse(text, Loader=_Loader):
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > 1:
                    raise ValueError("only one YAML document is allowed")
            elif isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
                if depth > 16:
                    raise ValueError("YAML nesting exceeds limit")
            elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                depth -= 1
            elif isinstance(event, ScalarEvent):
                scalars += 1
                if scalars > 4096:
                    raise ValueError("YAML scalar count exceeds limit")
            elif isinstance(event, AliasEvent):
                raise ValueError("YAML aliases are forbidden")  # noqa: TRY004
        if documents != 1:
            raise ValueError("one YAML document is required")
        value = yaml.load(text, Loader=_Loader)
        _json_types(value)
        return ProfileCatalog.model_validate_json(canonical_json(value))
    except (yaml.YAMLError, UnicodeError, RecursionError, OverflowError, TypeError) as exc:
        raise ValueError("invalid profile catalog") from exc


def encode_catalog(catalog: ProfileCatalog) -> bytes:
    # Frozen models may contain mutable dictionaries or model_copy updates.
    validated = ProfileCatalog.model_validate_json(canonical_json(catalog.model_dump(mode="json")))
    output = yaml.safe_dump(validated.model_dump(mode="json"), allow_unicode=True, sort_keys=True).encode("utf-8")
    decode_catalog(output)
    return output


def catalog_digest(catalog: ProfileCatalog) -> str:
    validated = ProfileCatalog.model_validate_json(canonical_json(catalog.model_dump(mode="json")))
    return canonical_digest(validated.model_dump(mode="json"))
