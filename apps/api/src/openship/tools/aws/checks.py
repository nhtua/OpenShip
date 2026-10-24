"""Shared validation for pinned AWS role and direct S3 targets."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import ValidationError

from src.openship.execution.aws_transport import AwsSdkError

from .contracts import AwsCheckArguments, AwsCheckResult, AwsToolError

_REGION = re.compile(
    r"^(?:(?:af|ap|ca|eu|il|me|mx|sa)-[a-z0-9-]+-\d|"
    r"us-(?!gov-|iso-|isob-|isof-|isoeast-)[a-z0-9-]+-\d)$"
)


def normalize_bucket_region(location_constraint: str | None) -> str:
    """Normalize the two historical S3 location response values."""
    if location_constraint is None:
        return "us-east-1"
    if location_constraint == "EU":
        return "eu-west-1"
    if not isinstance(location_constraint, str) or not _REGION.fullmatch(
        location_constraint
    ):
        raise ValueError("bucket location is invalid")
    return location_constraint


def validate_caller_identity(
    identity: dict,
    *,
    expected_account_id: str,
    expected_role_arn: str,
):
    """Validate the assumed-role identity without returning its session ARN."""
    account_id = identity.get("Account") if isinstance(identity, dict) else None
    arn = identity.get("Arn") if isinstance(identity, dict) else None
    role_name = expected_role_arn.rsplit("/", 1)[-1]
    prefix = f"arn:aws:sts::{expected_account_id}:assumed-role/{role_name}/"
    if account_id != expected_account_id:
        raise ValueError("caller account does not match the pinned Connector")
    if not isinstance(arn, str) or not arn.startswith(prefix) or not arn[len(prefix) :]:
        raise ValueError("caller role does not match the pinned Connector")
    return _Identity(account_id=account_id, role_arn=expected_role_arn)


class _Identity:
    __slots__ = ("account_id", "role_arn")

    def __init__(self, *, account_id: str, role_arn: str):
        self.account_id = account_id
        self.role_arn = role_arn


def aws_connector_reference(context, connector_id):
    refs = [
        ref
        for ref in getattr(context.execution, "connector_refs", ())
        if ref.kind == "aws" and ref.id == connector_id
    ]
    if len(refs) != 1:
        raise AwsToolError("aws_resource_unavailable")
    return refs[0]


def expected_owner_parameters(expected_owner: str | None) -> dict[str, str]:
    """Return optional S3 owner confirmation without inventing an owner."""
    return {"ExpectedBucketOwner": expected_owner} if expected_owner else {}


async def resolve_bucket_region(arguments, access, control) -> str:
    """Use an explicit region directly, otherwise ask S3 for its location."""
    if arguments.region is not None:
        if not _REGION.fullmatch(arguments.region):
            raise AwsToolError("aws_invalid_parameters")
        return arguments.region

    control.raise_if_stopped()
    parameters = {
        "Bucket": arguments.bucket_name,
        **expected_owner_parameters(arguments.expected_owner),
    }
    try:
        location = await access.run(
            access.client("s3").get_bucket_location,
            **parameters,
        )
        return normalize_bucket_region(location.get("LocationConstraint"))
    except AwsSdkError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise AwsToolError("aws_region_mismatch") from None


async def aws_check_connection(
    arguments: AwsCheckArguments,
    context,
    control,
) -> AwsCheckResult:
    """Check only the configured, pinned role identity."""
    try:
        reference = aws_connector_reference(context, arguments.connector_id)
        access = await context.aws_access_factory(reference)
    except AwsToolError:
        raise
    except Exception:  # noqa: BLE001 - credentials and database details stay private.
        raise AwsToolError("aws_unavailable") from None

    try:
        control.raise_if_stopped()
        identity_response = await access.run(access.client("sts").get_caller_identity)
        configuration = access.configuration
        identity = validate_caller_identity(
            identity_response,
            expected_account_id=configuration.expected_account_id,
            expected_role_arn=configuration.basic_role_arn,
        )
        return AwsCheckResult(
            operation_id=str(context.operation_id),
            status="ready",
            account_id=identity.account_id,
            role_arn=identity.role_arn,
            region=configuration.region,
            checked_at=datetime.now(UTC),
        )
    except AwsToolError:
        raise
    except AwsSdkError as error:
        raise AwsToolError(
            error.code if error.code in AwsToolError._CODES else "aws_unavailable"
        ) from None
    except (KeyError, TypeError, ValueError, ValidationError):
        raise AwsToolError("aws_identity_mismatch") from None
    finally:
        await access.aclose()
