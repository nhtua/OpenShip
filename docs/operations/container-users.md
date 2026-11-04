---
type: operations
summary: Non-root identities and writable storage for OpenShip application containers.
status: current
date: 2026-11-04
---

# OpenShip application container users

Images built from `apps/api/Dockerfile` and `apps/web/Dockerfile` run their
application processes as UID/GID `1000:1000`. This covers the API, worker,
relay, migration/bootstrap commands, development web server, and production
web server. Development/build steps that install dependencies run before the
runtime user is selected. The production web server listens on container port
8080 so it does not need a privileged port.

The standard PostgreSQL and NATS images retain their own supported runtime
users. OpenSandbox is configured separately: only its manager receives the
Docker socket. A process that can control that socket remains host-privileged
in effect, regardless of its container UID. API, worker, gateway and generated
workloads must not receive the socket.

## Connector key files

The API and worker read the connector encryption and idempotency keyrings from
`/run/secrets`. The key loader requires a regular file with no group/other
permissions and an owner matching the process UID or root. With the application
running as UID 1000, provision both host key files as owner UID 1000 with mode
`0600`; a root-owned `0600` file is not readable by the non-root process even
though root is an allowed owner. Compose mounts this directory read-only; do not
loosen permissions to make a key readable.

## Messaging credentials and TLS files

The API, worker, relay and messaging-bootstrap containers also read role-specific
NATS credential files (and, in production, the NATS CA certificate). The
`scripts/init_messaging.py` provisioner creates its directory as mode `0700`
and files as `0600`, owned by the user that runs it. Run it as UID 1000 for the
default application identity, or securely transfer ownership of the generated
directory and app-readable files to UID 1000 while preserving restrictive
modes. Do not make NATS passwords or private TLS keys group/world readable.
The NATS server's own TLS private key must remain readable by the NATS server
identity; verify that separately when provisioning or rotating messaging
material.

## Artifact volume when upgrading an existing installation

The image prepares `/var/lib/openship/artifacts` as UID/GID 1000. A **new empty
named volume** inherits that directory ownership on first mount. Docker does
not change ownership in an existing non-empty volume when the image changes.

Before switching an installation with an existing artifact volume to the new
images:

1. Back up the artifact volume and record its exact name (`openship-dev_artifactdata`
   for the default development project, or `openship_artifactdata` in the
   production Compose file; a custom project name changes the development name).
2. Stop every API/worker process that can write artifacts.
3. In a maintenance window, run a one-off root-owned maintenance container
   against the verified volume, using the new API image:

   ```sh
   docker run --rm --user 0:0 \
     --volume <verified-artifact-volume>:/var/lib/openship/artifacts \
     --entrypoint chown <new-api-image> \
     -R 1000:1000 /var/lib/openship/artifacts
   ```

4. Verify the volume contents remain present and owned by `1000:1000`, then
   recreate the API and worker with the new images.

Do not run the example with an unverified volume name, against a live writer, or
without a recoverable backup. This ownership migration changes metadata
recursively but does not rewrite artifact contents. The repository change does
not modify or migrate any running container or volume automatically.
