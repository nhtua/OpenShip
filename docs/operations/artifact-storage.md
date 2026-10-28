---
type: operations
summary: Configure, initialize, verify, back up, restore, and recover Phase 4A filesystem artifacts.
status: active
date: 2026-10-27
---

# Artifact Storage Operations

Phase 4A stores immutable artifact bytes on a local POSIX filesystem and their
identity, digest, ownership, and lifecycle in PostgreSQL. The API and runtime
worker must see the same directory and the same `ARTIFACT_STORAGE_ID`. A
missing or mismatched storage marker makes artifact access fail closed; requests
do not create a replacement root or silently accept another volume.

Phase 6A proposes adding JetStream to coordinated backup/restore without
changing artifact storage. Its [messaging runbook proposal](messaging.md)
covers the future native/dev/prod environments and execution gates. Those
commands are not shipped yet; the procedures here apply to the current stack.

## Configure a Compose installation

Copy the root example file and replace its signing key and storage UUID:

```bash
cp .env.example .env
python -c 'import secrets; print(secrets.token_urlsafe(48))'
uuidgen
```

Set the generated values for `JWT_SECRET` and `ARTIFACT_STORAGE_ID` in the root
`.env`. Keep that file out of version control. `ARTIFACT_STORAGE_ID` identifies
the artifact volume for this installation and must remain unchanged across
restarts, upgrades, and restores.

Search is optional. Current installations enable maintained tools in Global
Settings and configure managed Connectors using the [Connector runbook](managed-connectors.md).
`TOOL_ENABLED_IDS` is an optional deployment ceiling: unset allows managed
policy, while an explicitly empty value restricts the installation to no
tools. Legacy `BRAVE_SEARCH_API_KEY` / `TAVILY_API_KEY` worker bindings remain
for persisted old runs; they are not the normal managed-Connector setup path.

For the dev Compose stack, `start` initializes the named volume or verifies its
existing marker before starting application services. It also creates missing
dev NATS credentials. Keep `ARTIFACT_STORAGE_ID` stable; startup refuses to
replace a marker or to initialize a non-empty volume whose marker is missing.
The stack wrapper selects Docker by default and reads
`CONTAINER_ENGINE=docker` or `CONTAINER_ENGINE=podman` from the root `.env`:

```bash
scripts/openship-stack --environment dev start
```

The explicit `init-artifacts` command remains available for local or production
provisioning. In production, initialize messaging credentials first because the
worker container mounts its protected NATS credentials. `init-messaging`
refuses to overwrite existing credential files.

The initializer writes `.storage-id` only in an empty root. If it finds a
non-empty root without that marker or a marker with another UUID, it refuses to
change the contents. Do not delete or regenerate the marker to make a failing
mount appear healthy.

## Verify and recover

Run these commands from the repository root. They use the same volume and
identity as the API and worker:

```bash
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance verify
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance retention --dry-run
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance retention
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance purge <workspace-uuid> --dry-run
```

`verify` reads every ready artifact and checks its size and digest. The
`retention` command recovers expired staging transfers and interrupted deletes;
it does not delete old, ready user artifacts by age. Missing or damaged bytes
are reported and the metadata is marked unavailable when reconciliation runs.
There is no age-based purge by default. `purge <workspace-uuid> --dry-run`
prints the exact workspace, artifact IDs, expected states and SHA-256 digests
that an explicit purge would remove. Review this output, then rerun without
`--dry-run` to purge that workspace. Active run/operation input pins refuse the
purge. The command marks deletion intent in PostgreSQL before unlinking bytes;
an interrupted unlink remains a tombstone and `retention` can finish it. IDs
and digests remain in PostgreSQL after deletion. `ARTIFACT_*_QUOTA_*` settings
control conversation and project quotas.

## Drain, upgrade, and back up

Stop the worker with a grace period longer than the maximum tool execution
window, then stop the API and web process. The worker finishes its active
command before exiting; queued work remains durable in PostgreSQL.

```bash
docker compose stop -t 180 worker
docker compose stop api web
mkdir -p backups
docker compose exec -T postgres pg_dump -U openship -d openship > backups/openship.sql
```

Back up the `artifactdata` Docker volume while the API and worker are stopped.
The actual volume name has the Compose project prefix; find it with
`docker volume ls`, then substitute it below:

```bash
docker run --rm \
  -v <compose-project>_artifactdata:/data:ro \
  -v "$PWD/backups:/backup" \
  alpine tar -czf /backup/artifactdata.tar.gz -C /data .
```

Keep the SQL dump, artifact archive, and root `.env` storage UUID as one backup
set. PostgreSQL rows refer to the files by ID and digest, and the marker ties
those files to the configured storage identity. Upgrade by rebuilding the
services and starting them again after the backups complete:

```bash
docker compose up -d --build
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance verify
```

## Restore

Restore into the same `ARTIFACT_STORAGE_ID`. Stop API and worker, restore the
artifact archive to the named volume, restore PostgreSQL, then start the
services and run `verify`:

```bash
docker compose stop -t 180 worker
docker compose stop api web
docker run --rm \
  -v <compose-project>_artifactdata:/data \
  -v "$PWD/backups:/backup:ro" \
  alpine sh -c 'tar -xzf /backup/artifactdata.tar.gz -C /data'
cat backups/openship.sql | docker compose exec -T postgres psql -U openship -d openship
docker compose up -d
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance verify
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance verify-restored
```

If the database and file archive do not match, verification reports missing or
incorrect bytes, marks affected workspaces and artifacts unavailable, and
preserves their IDs, digests, and quota counters. `verify-restored` checks the
existing marker and bytes; it never initializes a replacement root, rebinds a
conversation to another `ARTIFACT_STORAGE_ID`, or repeats a dispatched search.
Verified unavailable files return to ready without changing quota counters.
Preserve the `.storage-id` marker in the archive. For an isolated recovery,
restore both the database and the named volume as a pair before starting
OpenShip, then run `verify-restored` before enabling work.

## Credential management and later execution

Phase 5 provides managed Connector credentials encrypted in PostgreSQL, with
encryption/HMAC keyrings protected separately. Preserve those keyrings and
the managed-dispatch lock across upgrades and restores; see the
[Connector runbook](managed-connectors.md). Legacy Phase 4 environment bindings
retain their historical compatibility behavior. Phase 6A messaging carries
references and does not move credential values into broker envelopes.

Phase 4 files are immutable artifact snapshots, each identified by its artifact
ID and digest. Phase 6 owns logical file identities/current heads, expected-base
edits, immutable version capture, and the per-step choice between mutable files
and immutable outputs. Repeated edits create new captured versions; they do not
rewrite an artifact already referenced by a run. Phase 6 also owns sandbox
driver installation/qualification and reproducible sandbox image builds; none
of those are prerequisites for Phase 4 trusted search or local workspace reads.
