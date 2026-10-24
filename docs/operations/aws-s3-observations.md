---
type: operations
summary: Configure the AWS basic role for direct S3 discovery and read-only observations.
date: 2026-10-24
status: active
---

# AWS S3 discovery and observations

OpenShip uses the project's configured AWS source credential only to assume the
configured basic role. The worker verifies the assumed account and role, then
uses short-lived role credentials for AWS reads. A bucket does not need an
OpenShip resource record, environment or binding. AWS decides whether each
requested operation is allowed by the role's effective policies.

The source principal needs only `sts:AssumeRole` for the one configured role.
The role trust policy must name that exact source principal. OpenShip does not
send an external ID or choose a role dynamically. See AWS's guidance for
[creating a role for an IAM user](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create-for-user.html)
and the [AssumeRole API](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html).

## 1. Create the source principal

Use a dedicated machine principal and a key intended for this installation.
The source principal does not need S3 or CloudWatch permissions. Its identity
policy should grant only `sts:AssumeRole` for the configured basic role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::123456789012:role/OpenShipBasicRead"
    }
  ]
}
```

Set the role trust policy to trust that source principal. Do not use a wildcard
principal.

## 2. Grant only the role reads you need

`sts:GetCallerIdentity` verifies the assumed role and requires no IAM grant.
For the maintained S3 helper operations, the basic role can use:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadBucketVersioning",
      "Effect": "Allow",
      "Action": "s3:GetBucketVersioning",
      "Resource": "arn:aws:s3:::replace-with-bucket-name"
    },
    {
      "Sid": "ReadS3Metrics",
      "Effect": "Allow",
      "Action": "cloudwatch:GetMetricData",
      "Resource": "*"
    }
  ]
}
```

The storage helper reads `AWS/S3` `BucketSizeBytes` metrics. An object-count
report uses the daily `AWS/S3` `NumberOfObjects` metric with
`StorageType=AllStorageTypes`. CloudWatch metric reads use account-level
`Resource: "*"`; the request is limited by OpenShip to the selected bucket's
metric dimensions, but IAM does not scope `GetMetricData` to a bucket. Review
that broader metric-read permission before connecting the role. AWS documents
the [CloudWatch action and resource model](https://docs.aws.amazon.com/service-authorization/latest/reference/list_cloudwatch.html),
the [GetMetricData API](https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricData.html),
and [S3 metrics and dimensions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html).

The following bucket-scoped S3 actions support direct reads when the user
requests those fields:

| Action | What OpenShip can read |
| --- | --- |
| `s3:ListAllMyBuckets` | Discover bucket names in the role's account. This is not required when the user supplies a known bucket name. |
| `s3:GetBucketLocation` | Resolve a bucket's region when a direct S3 helper request omits the region. Supplying the region skips this optional lookup. |
| `s3:GetBucketVersioning` | Report enabled, suspended or not-enabled versioning state. |
| `s3:GetBucketPolicyStatus` | Report AWS's policy-public status for the bucket policy. |
| `s3:GetBucketPublicAccessBlock` | Read the bucket-level Block Public Access settings. |
| `s3:GetBucketAcl` | Read the bucket owner and ACL grants. |
| `cloudwatch:GetMetricData` | Read daily bucket-size and object-count metrics when available. |

Scope bucket actions to the bucket ARN where AWS supports it. `ListBuckets`
uses `s3:ListAllMyBuckets` and cannot be scoped to one bucket. Generic AWS reads
also support other classified operations, including EC2 and RDS descriptions;
grant only the corresponding read actions for resources the project should
inspect. The pinned catalog is the set of operations OpenShip can call. IAM
permissions determine which of those operations this role can perform.

OpenShip does not download object bodies for these reports. Missing permissions
leave that field unknown while other permitted reads can still succeed. Do not
interpret a denied or absent public-access field as “private.” Policy status,
bucket-level Block Public Access and ACL grants describe different controls;
they do not by themselves prove every caller's effective access.

## 3. Configure the project Connector

In **Global Settings**, the installation operator enables managed tools for
projects. `TOOL_ENABLED_IDS` is an optional deployment ceiling; when set, it
can further restrict tools enabled in Global Settings. A configured Connector
does not enable a tool by itself.

In the project's Connector settings:

1. Add an AWS Connector and enter its name, default region, expected 12-digit
   account ID and exact basic-role ARN.
2. Enter the dedicated source key. If the key is temporary, enter its session
   token and expiry together.
3. Save the setup and run **Check status**. This checks that the source can
   assume the role and that the assumed account and role match the Connector.
   It does not query a bucket.
4. In a conversation, ask OpenShip to discover buckets or name a bucket
   directly. For a direct S3 helper request, provide the bucket name and,
   optionally, its region and expected owner. There is no bucket binding step.

For example, ask: “Discover my S3 buckets. For `example-bucket`, report size,
object count, versioning and public-access settings. Cite the evidence and mark
inaccessible fields unknown.” If listing is denied but the role can read a
named bucket, OpenShip can still make that direct read. If AWS denies an
operation, OpenShip reports that the AWS role lacks permission for it; it does
not switch connectors or fall back to source credentials.

The storage helper reports AWS's daily bucket-size measurements, not a live
object scan. The object-count metric is also daily. Missing dimensions remain
unknown, and measurements from different dates are not added together. Review
the measurement date, freshness, coverage and attached evidence before treating
a subtotal as complete.

## 4. Rotation and revocation

Use **Save** in the Connector dialog to replace source credentials. A
replacement creates a new immutable Connector revision; admitted runs remain
pinned to the revision they selected. Revoke removes the Connector from active
selection, disables new execution and purges its stored secret bytes. Non-secret
Connector revisions and historical AWS evidence remain available for history.

Existing backups can retain prior ciphertext until those backups expire; follow
the installation's backup retention and encryption-key retirement procedures.

Do not test prohibited AWS mutations against a user's resources. The maintained
AWS reader permits only operations classified as read-only in the pinned
catalog. The connection check is limited to role identity, while S3 helpers
read only their documented metadata and daily storage metrics.
