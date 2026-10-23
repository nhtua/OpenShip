"""Explicit model-free AWS identity and bound-bucket checks."""

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


def _reference(context, connector_id):
    refs = [
        ref
        for ref in getattr(context.execution, "connector_refs", ())
        if ref.kind == "aws" and ref.id == connector_id
    ]
    if len(refs) != 1:
        raise AwsToolError("aws_resource_unavailable")
    return refs[0]


async def aws_check_connection(
    arguments: AwsCheckArguments,
    context,
    control,
) -> AwsCheckResult:
    """Check the pinned role and, optionally, one pinned bucket binding."""
    try:
        reference = _reference(context, arguments.connector_id)
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
        bucket_verified = False
        if arguments.resource_binding_id is not None:
            binding = _owned_binding(context, arguments.resource_binding_id)
            if binding.connector_id != reference.id:
                raise AwsToolError("aws_resource_unavailable")
            location = await access.run(
                access.client("s3").get_bucket_location,
                Bucket=binding.bucket_name,
                ExpectedBucketOwner=binding.expected_owner,
            )
            actual_region = normalize_bucket_region(location.get("LocationConstraint"))
            if actual_region != binding.region or actual_region != configuration.region:
                raise AwsToolError("aws_region_mismatch")
            bucket_verified = True
        return AwsCheckResult(
            operation_id=str(context.operation_id),
            status="ready",
            account_id=identity.account_id,
            role_arn=identity.role_arn,
            region=configuration.region,
            bucket_verified=bucket_verified,
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


def _owned_binding(context, binding_id):
    from src.openship.resources.models import ResourceBinding

    pinned = [
        item
        for item in getattr(context.execution, "resource_binding_refs", ())
        if item.id == binding_id
    ]
    if len(pinned) != 1 or context.session_factory is None:
        raise AwsToolError("aws_resource_unavailable")
    with context.session_factory() as db:
        binding = (
            db.query(ResourceBinding)
            .filter(
                ResourceBinding.id == binding_id,
                ResourceBinding.project_id == context.authorization.project_id,
                ResourceBinding.revision == pinned[0].revision,
                ResourceBinding.connector_id == pinned[0].connector_id,
                ResourceBinding.connector_revision == pinned[0].connector_revision,
                ResourceBinding.state == "active",
            )
            .first()
        )
        if binding is None:
            raise AwsToolError("aws_resource_unavailable")
        return binding
