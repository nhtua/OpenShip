---
type: operations
summary: Configure the project AWS source and least-privilege basic role for Phase 5A.2 S3 storage observations.
date: 2026-10-23
status: active
---

# AWS S3 observation setup

Phase 5A.2 uses a dedicated AWS source credential only to assume the configured
basic read role. The worker does not use the source key for S3 or CloudWatch
requests. It assumes a short-lived role session, verifies the returned account
and role, and uses that session for the selected bucket observation.

The current browser setup accepts an access key ID and secret access key, with
an optional temporary session token and expiry. Do not use a personal IAM
identity. Create a dedicated machine principal, restrict its identity policy to
the one basic role ARN, and configure that role's trust policy to trust only
that exact source principal. The role ARN alone does not authenticate a caller.
The connector encrypts source credentials in the database; revoking a
connector purges the stored credential bytes and hides the revoked setup from
active connector lists while retaining non-secret revisions needed by history
and resource bindings.

## 1. Create the source principal

Use a dedicated IAM user access key for a localhost installation, or temporary
credentials issued to that dedicated principal. The source principal needs
only `sts:AssumeRole` for the configured basic role. It does not need S3 or
CloudWatch permissions. Attach no unrelated permissions to the source
principal.

Replace the account and role name in this identity policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::123456789012:role/OpenShipS3BasicRead"
    }
  ]
}
```

The role trust policy must name the dedicated source IAM principal as a
`Principal`. Avoid wildcard principals. This implementation does not send an
external ID or choose a role dynamically, so the configured role must be the
single role the source principal is authorized to assume. See AWS's guidance
for [creating a role for an IAM user](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-user.html)
and the [AssumeRole API](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html).

## 2. Create the basic read role

Set the role's maximum session duration to at least 15 minutes. OpenShip asks
STS for a 900-second session and does not chain roles. The role trust policy
must allow the source principal from step 1 to assume this role.

The role needs the read operations for the features you plan to use. Scope
`s3:GetBucketLocation` to the bucket ARN that will be bound. To use metadata,
also allow `s3:GetBucketVersioning` on that bucket. Storage observations also
use `cloudwatch:GetMetricData` with the broader account-level metric-read scope
shown below. OpenShip calls
`sts:GetCallerIdentity` to verify the role; AWS requires no IAM permission for
that operation. Do not add `s3:ListBucket`, object reads, writes, deletes,
CloudWatch Logs, or maintenance-role permissions for this flow.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadSelectedBucketRegion",
      "Effect": "Allow",
      "Action": "s3:GetBucketLocation",
      "Resource": "arn:aws:s3:::replace-with-bound-bucket"
    },
    {
      "Sid": "ReadCloudWatchMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    }
  ]
}
```

Add this separate statement only when you plan to use the bucket metadata tool:

```json
{
  "Sid": "ReadSelectedBucketVersioning",
  "Effect": "Allow",
  "Action": "s3:GetBucketVersioning",
  "Resource": "arn:aws:s3:::replace-with-bound-bucket"
}
```

`cloudwatch:GetMetricData` with `Resource: "*"` grants metric-read authority
broader than the selected S3 bucket. OpenShip's handler constrains its own
requests to the `AWS/S3` `BucketSizeBytes` metric and the selected bucket's
`BucketName` and `StorageType` dimensions, but IAM does not provide a
bucket-level boundary for this role policy. Use a dedicated role and review
that account-level CloudWatch metric-read exposure before connecting it. AWS
documents the [CloudWatch action and resource model](https://docs.aws.amazon.com/service-authorization/latest/reference/list_cloudwatch.html)
and the [GetMetricData operation](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricData.html).

The S3 operation-to-permission mapping is in AWS's [required S3 API
permissions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html).
The metadata handler uses the native [GetBucketVersioning API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketVersioning.html)
with the expected bucket owner. If bucket versioning was never enabled, AWS
omits `Status`; OpenShip records `Not enabled`. Missing MFA Delete metadata is
shown as `Not reported`, not inferred as disabled.
The handler's storage dimensions are the frozen list in the Phase 5
specification and AWS's [S3 metrics and dimensions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html).

## 3. Configure the project Connector

In **Global Settings**, the installation operator enables the installed tools
that projects may use. `TOOL_ENABLED_IDS` is an optional deployment ceiling;
if explicitly set, it can continue restricting tools enabled in Global
Settings. Ordinary policy and Connector changes do not require a process
restart. A configured Connector or bucket binding does not enable a tool by
itself.

In the project's Connector settings:

1. Add an AWS Connector and enter its name, AWS region, expected 12-digit
   account ID, and the exact basic-role ARN.
2. Enter the dedicated source key. If using temporary source credentials,
   enter the session token and its expiry together.
3. Save the setup. Run **Check status** to verify the source can assume the
   configured role and the assumed identity matches the configured account and
   role. This check does not query the selected bucket.
4. Add an environment and bind an existing general-purpose S3 bucket to that
   environment and Connector revision. Binding stores an explicit locator; it
   does not discover buckets or prove the bucket still exists.
5. Run the S3 storage observation for the selected binding. The storage check
   verifies account, bucket owner and region before reading CloudWatch metrics.

In a conversation belonging to the same project, choose **+ → S3 bucket
metadata** or **S3 bucket storage**, select the bound bucket and submit to run
the read directly. Metadata records versioning and MFA Delete state, the AWS
request count and retrieval time. It does not report a CloudWatch measurement
time. The normalized observation is retained as `s3-bucket-metadata.json`.
For automatic use, select the bucket in the composer's **S3 buckets** selector,
enable **Let the model use tools automatically**, and ask about its storage.
The bucket selector chooses targets; it does not attach or run a tool. The
connection check runs explicitly from Connector settings and is excluded
from the model's tool choices.

The storage observation reports CloudWatch's daily bucket-size measurements, not a
live object scan. Missing dimensions remain unknown, and values from different
measurement dates are not added together. Review the displayed measurement
time, freshness, coverage and evidence before treating a subtotal as complete.

## 4. Rotation and revocation

Use **Save** in the Connector dialog to replace the source credential. A
replacement creates a new immutable revision; existing resource bindings and
admitted runs remain pinned to their recorded revisions. Revoke removes the
Connector from active selection, disables execution and overwrites the
encrypted key ID, nonce and ciphertext with fixed non-secret sentinels. The
non-secret Connector and revision rows remain as referential history for
retained bindings, blockers and audit records. Create a new Connector and
explicitly bind resources to it when replacing a revoked setup.

Revocation overwrites the active database row. Existing database backups may
still contain its prior ciphertext until those backups expire; follow the
installation's backup retention and encryption-key retirement procedures.

Do not test prohibited AWS mutations against a user's bucket. The registered
SDK surface permits only `GetCallerIdentity`, `GetBucketLocation`,
`GetBucketVersioning` and `GetMetricData`; it exposes no bucket mutation
operation. Versioning access is limited to the metadata tool and does not
make that operation available to storage or connection checks.
