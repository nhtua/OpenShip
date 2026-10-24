"""Bounded, read-only S3 bucket versioning metadata through the AWS SDK."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError

from src.openship.capabilities.contracts import CapabilityRef, canonical_json
from src.openship.connectors.aws.read_catalog import load_aws_read_catalog
from src.openship.execution.aws_transport import AwsSdkError

from .checks import (
    aws_connector_reference,
    expected_owner_parameters,
    resolve_bucket_region,
    validate_caller_identity,
)
from .contracts import (
    AwsS3MetadataArgumentsV2,
    AwsS3MetadataObservationV2,
    AwsS3MetadataResultV2,
    AwsToolError,
)

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
    arguments: AwsS3MetadataArgumentsV2,
    context,
    control,
) -> AwsS3MetadataObservationV2:
    """Read versioning metadata for one direct S3 bucket target."""
    reference = aws_connector_reference(context, arguments.connector_id)
    try:
        access = await context.aws_access_factory(reference)
    except AwsToolError:
        raise
    except Exception:  # noqa: BLE001 - credential and database details stay private.
        raise AwsToolError("aws_unavailable") from None

    try:
        configuration = access.configuration
        control.raise_if_stopped()
        caller_identity = await access.run(access.client("sts").get_caller_identity)
        validate_caller_identity(
            caller_identity,
            expected_account_id=configuration.expected_account_id,
            expected_role_arn=configuration.basic_role_arn,
        )

        region = await resolve_bucket_region(arguments, access, control)

        control.raise_if_stopped()
        versioning_response = await access.run(
            access.client("s3", region=region).get_bucket_versioning,
            Bucket=arguments.bucket_name,
            **expected_owner_parameters(arguments.expected_owner),
        )
        try:
            versioning_status, mfa_delete_status = normalize_versioning_configuration(
                versioning_response
            )
        except (TypeError, ValueError):
            raise AwsToolError("aws_invalid_metadata_response") from None

        tool = _capability_ref(context)
        target = {"bucket_name": arguments.bucket_name}
        if arguments.expected_owner is not None:
            target["expected_owner"] = arguments.expected_owner
        observation = AwsS3MetadataObservationV2(
            tool=tool,
            operation_id=context.operation_id,
            project_id=context.authorization.project_id,
            connector_id=reference.id,
            connector_revision=reference.configuration_revision,
            catalog_digest=load_aws_read_catalog().digest,
            account_id=configuration.expected_account_id,
            region=region,
            bucket_name=arguments.bucket_name,
            target=target,
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


def project_s3_metadata_result(
    observation: AwsS3MetadataObservationV2, artifact_ref
):
    return AwsS3MetadataResultV2(
        operation_id=observation.operation_id,
        connector_id=observation.connector_id,
        connector_revision=observation.connector_revision,
        account_id=observation.account_id,
        region=observation.region,
        bucket_name=observation.bucket_name,
        versioning_status=observation.versioning_status,
        mfa_delete_status=observation.mfa_delete_status,
        request_count=observation.request_count,
        retrieved_at=observation.retrieved_at,
        artifact=artifact_ref,
    ).model_dump(mode="json")


def _capability_ref(context) -> CapabilityRef:
    matches = [
        ref
        for ref in context.execution.allowed_capabilities
        if ref.id == "aws_s3_bucket_metadata"
    ]
    if len(matches) != 1:
        raise AwsToolError("aws_resource_unavailable")
    return matches[0]
