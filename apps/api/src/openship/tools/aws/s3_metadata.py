"""Bounded, read-only S3 bucket versioning metadata through the AWS SDK."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError

from src.openship.capabilities.contracts import CapabilityRef, canonical_json
from src.openship.execution.aws_transport import AwsSdkError

from .checks import normalize_bucket_region, validate_caller_identity
from .contracts import (
    AwsS3MetadataArguments,
    AwsS3MetadataObservation,
    AwsToolError,
)
from .s3_storage import _connector_reference, _owned_binding

VersioningStatus = Literal["enabled", "suspended", "not_enabled"]
MfaDeleteStatus = Literal["enabled", "disabled", "not_reported"]
_MAX_OBSERVATION_BYTES = 64 * 1024


def normalize_versioning_configuration(
    response: object,
) -> tuple[VersioningStatus, MfaDeleteStatus]:
    """Normalize the documented AWS states without inferring absent values."""
    if not isinstance(response, dict):
        raise TypeError("versioning response is invalid")

    raw_status = response.get("Status")
    if raw_status is None:
        versioning_status: VersioningStatus = "not_enabled"
    elif raw_status == "Enabled":
        versioning_status = "enabled"
    elif raw_status == "Suspended":
        versioning_status = "suspended"
    else:
        raise ValueError("versioning status is invalid")

    raw_mfa = response.get("MFADelete")
    if raw_mfa is None:
        mfa_delete_status: MfaDeleteStatus = "not_reported"
    elif raw_mfa == "Enabled":
        mfa_delete_status = "enabled"
    elif raw_mfa == "Disabled":
        mfa_delete_status = "disabled"
    else:
        raise ValueError("MFA delete status is invalid")
    return versioning_status, mfa_delete_status


async def aws_s3_bucket_metadata(
    arguments: AwsS3MetadataArguments,
    context,
    control,
) -> AwsS3MetadataObservation:
    """Read the versioning state for one exact, explicitly selected binding."""
    binding = _owned_binding(context, arguments.resource_binding_id)
    reference = _connector_reference(context, binding)
    try:
        access = await context.aws_access_factory(reference)
    except AwsToolError:
        raise
    except Exception:  # noqa: BLE001 - credential and database details stay private.
        raise AwsToolError("aws_unavailable") from None

    try:
        configuration = access.configuration
        if (
            binding.account_id != configuration.expected_account_id
            or binding.expected_owner != configuration.expected_account_id
            or binding.region != configuration.region
        ):
            raise AwsToolError("aws_resource_unavailable")

        control.raise_if_stopped()
        caller_identity = await access.run(access.client("sts").get_caller_identity)
        validate_caller_identity(
            caller_identity,
            expected_account_id=binding.account_id,
            expected_role_arn=configuration.basic_role_arn,
        )

        control.raise_if_stopped()
        location = await access.run(
            access.client("s3").get_bucket_location,
            Bucket=binding.bucket_name,
            ExpectedBucketOwner=binding.expected_owner,
        )
        try:
            actual_region = normalize_bucket_region(location.get("LocationConstraint"))
        except (AttributeError, TypeError, ValueError):
            raise AwsToolError("aws_region_mismatch") from None
        if actual_region != binding.region or actual_region != configuration.region:
            raise AwsToolError("aws_region_mismatch")

        control.raise_if_stopped()
        versioning_response = await access.run(
            access.client("s3").get_bucket_versioning,
            Bucket=binding.bucket_name,
            ExpectedBucketOwner=binding.expected_owner,
        )
        try:
            versioning_status, mfa_delete_status = normalize_versioning_configuration(
                versioning_response
            )
        except (TypeError, ValueError):
            raise AwsToolError("aws_invalid_metadata_response") from None

        tool = _capability_ref(context)
        observation = AwsS3MetadataObservation(
            tool=tool,
            operation_id=str(context.operation_id),
            project_id=context.authorization.project_id,
            resource_id=binding.resource_object_id,
            resource_binding_id=binding.id,
            binding_revision=binding.revision,
            connector_id=reference.id,
            connector_revision=reference.configuration_revision,
            account_id=binding.account_id,
            region=binding.region,
            bucket_name=binding.bucket_name,
            versioning_status=versioning_status,
            mfa_delete_status=mfa_delete_status,
            request_count=access.request_budget.used,
            retrieved_at=datetime.now(UTC),
        )
        if len(canonical_json(observation.model_dump(mode="json"))) > _MAX_OBSERVATION_BYTES:
            raise AwsToolError("aws_output_limit_exceeded")
        return observation
    except AwsToolError:
        raise
    except AwsSdkError as error:
        code = error.code if error.code in AwsToolError._CODES else "aws_unavailable"
        raise AwsToolError(code) from None
    except (KeyError, TypeError, ValueError, ValidationError):
        raise AwsToolError("aws_identity_mismatch") from None
    finally:
        await access.aclose()


def project_s3_metadata_result(observation: AwsS3MetadataObservation, artifact_ref):
    return {
        "operation_id": observation.operation_id,
        "resource_id": str(observation.resource_id),
        "resource_binding_id": str(observation.resource_binding_id),
        "binding_revision": observation.binding_revision,
        "account_id": observation.account_id,
        "region": observation.region,
        "bucket_name": observation.bucket_name,
        "versioning_status": observation.versioning_status,
        "mfa_delete_status": observation.mfa_delete_status,
        "request_count": observation.request_count,
        "retrieved_at": observation.retrieved_at.isoformat(),
        "artifact": artifact_ref.model_dump(mode="json"),
    }


def _capability_ref(context) -> CapabilityRef:
    matches = [
        ref
        for ref in context.execution.allowed_capabilities
        if ref.id == "aws_s3_bucket_metadata"
    ]
    if len(matches) != 1:
        raise AwsToolError("aws_resource_unavailable")
    return matches[0]
