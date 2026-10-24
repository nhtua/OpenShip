"""Local discovery of AWS services and supported SDK read parameters."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.openship.connectors.aws.read_catalog import load_aws_read_catalog


class AwsReadCatalogArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service: str | None = Field(default=None, min_length=1, max_length=64)
    operation: str | None = Field(default=None, min_length=1, max_length=128)
    limit: StrictInt = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def _operation_needs_service(self):
        if self.operation is not None and self.service is None:
            raise ValueError("service is required when looking up one operation")
        if self.operation is not None and self.cursor is not None:
            raise ValueError("operation lookup does not accept a cursor")
        return self


class AwsReadCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service: str
    operation: str
    api_name: str
    supported: bool
    unsupported_reason: str | None = None
    parameter_schema: dict[str, Any] | None = None


class AwsReadCatalogResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sdk_version: str = Field(min_length=1, max_length=32)
    operations: tuple[AwsReadCatalogItem, ...] = Field(max_length=50)
    next_cursor: str | None = Field(default=None, max_length=2048)


async def aws_read_catalog(
    arguments: AwsReadCatalogArguments,
    _context,
    control,
) -> AwsReadCatalogResult:
    """Return a local catalog page or one operation's parameter schema."""
    control.raise_if_stopped()
    catalog = load_aws_read_catalog()
    page = catalog.list_operations(
        service=arguments.service,
        operation=arguments.operation,
        limit=1 if arguments.operation is not None else arguments.limit,
        cursor=arguments.cursor,
    )
    return AwsReadCatalogResult(
        catalog_digest=catalog.digest,
        sdk_version=catalog.sdk_version,
        operations=tuple(
            AwsReadCatalogItem(
                service=item.service,
                operation=item.operation,
                api_name=item.api_name,
                supported=item.supported,
                unsupported_reason=item.unsupported_reason,
                parameter_schema=dict(item.input_schema)
                if arguments.operation is not None and item.supported
                else None,
            )
            for item in page.items
        ),
        next_cursor=page.next_cursor,
    )


__all__ = ["AwsReadCatalogArguments", "AwsReadCatalogItem", "AwsReadCatalogResult", "aws_read_catalog"]
