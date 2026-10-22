---
type: runbook
summary: Provision and rotate managed Connector keys, migrate legacy search bindings, and restore with dispatch locked.
date: 2026-10-22
status: current
---

# Managed Connector operations

Managed Connector encryption and idempotency keys are independent of the
database and artifact volume. Keep both key files in a protected directory,
outside source control and outside ordinary database backups. The Compose API
and worker mount `./secrets` read-only at `/run/secrets`; the browser and
migration container receive no key mount. A missing key file disables managed
Connector setup and dispatch while leaving plain chat available.

Provisioning uses a stable, deployment-specific `CONNECTOR_INSTALLATION_ID`.
Set `CONNECTOR_OPERATOR_USER_ID` to the account that manages installation-wide
search connectors. The API and worker must receive the same values. Keep the
operator account under the deployment's normal account recovery controls.

## Initialize keys

From `apps/api`, create a private host directory, then initialize both keyrings
once:

```sh
mkdir -m 700 -p ../../secrets
uv run python -m src.openship.connectors.cli key-init \
  --encryption-file ../../secrets/openship-connector-encryption-keys.json \
  --hmac-file ../../secrets/openship-connector-idempotency-hmac-keys.json
```

The command uses exclusive file creation and mode `0600`. It refuses to
overwrite either file and never prints key material. Back up the files through
a separate protected secret-management process. Do not put them in the
database backup or commit them. Compose mounts the directory read-only into
the API and worker.

## Rotate encryption or idempotency keys

Run key maintenance from `apps/api` with the key and lock paths pointed at the
host directory that Compose mounts. Stop the API and workers, lock dispatch,
rotate the keyring, restart services so every process reads the same new active
key, and keep dispatch locked while re-encryption completes. Keeping the API
stopped also prevents an in-flight setup write from adding ciphertext under a
key that is about to be retired.

```sh
CONNECTOR_ENCRYPTION_KEY_FILE=../../secrets/openship-connector-encryption-keys.json \
CONNECTOR_IDEMPOTENCY_HMAC_KEY_FILE=../../secrets/openship-connector-idempotency-hmac-keys.json \
CONNECTOR_DISPATCH_LOCK_FILE=../../secrets/openship-managed-dispatch.lock \
uv run python -m src.openship.connectors.cli dispatch lock

CONNECTOR_ENCRYPTION_KEY_FILE=../../secrets/openship-connector-encryption-keys.json \
CONNECTOR_IDEMPOTENCY_HMAC_KEY_FILE=../../secrets/openship-connector-idempotency-hmac-keys.json \
CONNECTOR_DISPATCH_LOCK_FILE=../../secrets/openship-managed-dispatch.lock \
uv run python -m src.openship.connectors.cli key-rotate \
  --key-kind encryption --key-id encryption-2026-10

CONNECTOR_ENCRYPTION_KEY_FILE=../../secrets/openship-connector-encryption-keys.json \
CONNECTOR_IDEMPOTENCY_HMAC_KEY_FILE=../../secrets/openship-connector-idempotency-hmac-keys.json \
CONNECTOR_DISPATCH_LOCK_FILE=../../secrets/openship-managed-dispatch.lock \
uv run python -m src.openship.connectors.cli key-reencrypt --batch-size 100
```

`key-reencrypt` commits bounded batches. If interrupted, run it again; it finds
rows still carrying a non-active key ID. It retains credential-version IDs and
creates a fresh nonce for every rewritten ciphertext. After the command
verifies all retained ciphertext, retire the old encryption key:

```sh
CONNECTOR_ENCRYPTION_KEY_FILE=../../secrets/openship-connector-encryption-keys.json \
CONNECTOR_DISPATCH_LOCK_FILE=../../secrets/openship-managed-dispatch.lock \
uv run python -m src.openship.connectors.cli key-retire \
  --key-kind encryption --key-id encryption-2026-09
```

Rotate an HMAC key using `key-rotate --key-kind hmac`. Retain the previous HMAC
key for at least 24 hours after its final write. `key-retire --key-kind hmac`
checks unexpired request-ledger entries and refuses early retirement. The
command never deletes ciphertext or ledger rows to make retirement pass.
Unlock dispatch only after workers have the active keyring and the verification
commands have completed:

```sh
CONNECTOR_DISPATCH_LOCK_FILE=../../secrets/openship-managed-dispatch.lock \
uv run python -m src.openship.connectors.cli dispatch unlock
```

## Migrate legacy Brave or Tavily bindings

Migration is explicit by service and scope. Preview the selected service,
visibility, target project, credential presence and any project-default
change. The preview and import output contain no credential values. Import
reads only the selected `BRAVE_SEARCH_API_KEY` and/or `TAVILY_API_KEY` from the
process environment or the explicitly named source file.

```sh
uv run python -m src.openship.connectors.cli search-import-preview \
  --visibility project --project-id <project-uuid> \
  --service brave --default-service brave --env-file <legacy-env-file>

uv run python -m src.openship.connectors.cli search-import \
  --visibility project --project-id <project-uuid> \
  --actor-user-id <project-owner-uuid> \
  --service brave --default-service brave --env-file <legacy-env-file>
```

Choose `--visibility global` only when deliberately importing an installation
connector; do not pass a project ID or default service in that case. Global
import requires `--actor-user-id` to match the configured operator. Project
import requires that actor to own the target project. No service is imported
unless it is named with `--service`; defaults are changed only for explicitly
named project services. Review the preview before running the import command.
The import is one database transaction and stores credentials encrypted with
the current active key.

## Restore

Use this order so restored rows cannot be dispatched with missing or stale
keys:

1. Stop all workers and the API.
2. Restore the database, artifact volume and non-secret deployment
   configuration. Keep the separately backed-up key files out of this restore
   step.
3. Create the managed-dispatch lock marker in the host `secrets` directory.
4. Restore the encryption and HMAC keyrings separately, preserving restrictive
   ownership and file permissions.
5. Start the API and worker with the lock marker still mounted. Plain chat can
   remain available; managed search dispatch must remain blocked.
6. Run `key-reencrypt` while locked. It verifies every retained encrypted
   credential after processing pending batches. Verify the expected Connector
   scopes/defaults and run `uv run python -m src.openship.artifacts.maintenance verify`
   to check retained artifact bytes. Confirm provider key status through the
   provider's account controls or a separately authorized external check.
7. Unlock managed dispatch explicitly. Review a manual search run and reopen
   its retained evidence before returning normal traffic.

Never generate replacement keys automatically when a key file is missing.
Restore the original keyring or keep managed Connector dispatch locked until
the lost ciphertext has been handled through an explicit recovery decision.
