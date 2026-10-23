from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.openship.tools.aws.checks import (
    normalize_bucket_region,
    validate_caller_identity,
)
from src.openship.tools.aws.contracts import AwsCheckArguments


def test_aws_check_arguments_require_a_connector_and_allow_an_owned_binding():
    arguments = AwsCheckArguments.model_validate(
        {
            "connector_id": "bbd5d88d-f3a7-4d1f-8927-bdb6a7b7d7af",
            "resource_binding_id": "3654d452-607b-4442-9e96-ead7a704dad3",
        }
    )

    assert str(arguments.connector_id) == "bbd5d88d-f3a7-4d1f-8927-bdb6a7b7d7af"
    assert str(arguments.resource_binding_id) == "3654d452-607b-4442-9e96-ead7a704dad3"


def test_aws_check_rejects_extra_fields_and_malformed_identifiers():
    with pytest.raises(ValidationError):
        AwsCheckArguments.model_validate({"connector_id": "not-a-uuid"})
    with pytest.raises(ValidationError):
        AwsCheckArguments.model_validate(
            {
                "connector_id": "bbd5d88d-f3a7-4d1f-8927-bdb6a7b7d7af",
                "role_arn": "arn:aws:iam::123456789012:role/Admin",
            }
        )


@pytest.mark.parametrize(
    "location, expected",
    [(None, "us-east-1"), ("EU", "eu-west-1"), ("ca-west-1", "ca-west-1")],
)
def test_s3_location_constraints_are_normalized(location, expected):
    assert normalize_bucket_region(location) == expected


def test_caller_identity_must_match_the_pinned_account_and_role():
    identity = {
        "Account": "123456789012",
        "Arn": "arn:aws:sts::123456789012:assumed-role/ReadOnly/openship-abc",
    }
    assert (
        validate_caller_identity(
            identity,
            expected_account_id="123456789012",
            expected_role_arn="arn:aws:iam::123456789012:role/ReadOnly",
        ).account_id
        == "123456789012"
    )

    with pytest.raises(ValueError):
        validate_caller_identity(
            {**identity, "Account": "999999999999"},
            expected_account_id="123456789012",
            expected_role_arn="arn:aws:iam::123456789012:role/ReadOnly",
        )
