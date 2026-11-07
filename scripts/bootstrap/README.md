# Host bootstrap

Prepare developer machines and single-node Linux hosts with Ansible. Each
playbook declares its environment's host dependencies; reusable roles own
configuration files and user services. Application startup stays in
`scripts/openship-stack`.

```text
scripts/bootstrap/
  bootstrap.sh
  ansible.cfg
  requirements.yml
  playbooks/
    arch-docker.yml
    arch-docker-podman.yml
    arch-docker-rootless.yml
    arch-podman.yml
  roles/
    rootful_engines/
      defaults/ files/ tasks/
    docker_engine/
      defaults/ files/ templates/ tasks/
    rootless_podman/
      defaults/ files/ handlers/ tasks/
```

**arch-docker** configures rootful Docker; **arch-docker-podman** configures
both rootful engines. **arch-docker-rootless** and **arch-podman** retain the
rootless alternatives, whose runsc resource enforcement is unqualified. Contributors can add
`debian-docker.yml` or other targets and reuse component roles. Unimplemented
targets fail before installing anything.

## Arch Linux with rootful Docker and Podman

The selected development profile uses host-root engine/runtime administration
and UID/GID 1000 sandbox workloads. Registering runsc does not give generated
code host-root privileges, but the engine sockets themselves grant powerful
host access and belong only to trusted manager infrastructure.

Configure both engines from the checkout:

```sh
scripts/bootstrap/bootstrap.sh arch-docker-podman --verify
```

If dependencies are installed, use `--configure-only --verify`. Both modes
prompt for sudo because system configuration and service management need it.
Docker-only hosts can select `arch-docker`. Dependencies are declared in the
`rootful_engines` role: Docker/Compose/buildx, Podman/podman-compose, runc and
AUR `gvisor-bin`. Missing packages trigger a full Arch upgrade; the existing
operator-provided AUR helper needs its own package authorization, as described
for the other profiles below. RootlessKit and rootless-extras are not needed
for these rootful targets.

| Endpoint | Service | Runtime selection |
| --- | --- | --- |
| `/var/run/docker.sock` | System `docker.service` / `docker.socket` | Existing default preserved (runc on a fresh host); sandbox requests select runsc |
| `/run/podman/podman.sock` | System `podman.socket` / socket-activated `podman.service` | runc |
| `/run/openship-sandbox/podman.sock` | `openship-sandbox-podman.socket` / service | runsc, scoped to this sandbox API |

Docker's daemon JSON is merged and validated, with backup of changed existing
configuration. Other settings/default runtimes are preserved; the runsc entry
uses `/usr/bin/runsc` and no `ignore-cgroups` flag. Supported runtime changes
are reloaded. Podman's system API services use a dedicated override file at
`/etc/openship/containers/podman-rootful.conf`; it supplies runtime executable
paths and clears runsc runtime flags. It does not change user/rootless
`containers.conf`. The separate Podman sandbox API avoids relying on its
Docker-compatible API's per-container runtime field. System unit files and the
normal Podman API drop-in are under `/etc/systemd/system/`.

Podman sockets are root-owned and private; use sudo for their host administration.
The role does not add users to rootful engine access groups, stop existing
rootless services or change their storage. Apply profile/unit changes while
OpenSandbox sandbox work is stopped. After configuration, normal CLI calls still
need the intended explicit endpoint/context; do not export a Podman socket as
the global Docker endpoint.

`--verify` uses the same trusted Python probe for both engines. It requires the
selected daemon privilege mode, checks Docker runtime selection or native
Podman `OCIRuntime`, and creates a fixture with `--user=1000:1000`, dropped
capabilities and `no-new-privileges`. It checks actual cgroup settings for one
CPU, 512 MiB memory, no swap and 128 host tasks, plus UID/GID, capability sets,
no-new-privileges and label-scoped physical container absence after removal.
A native Podman remote create receives no unsupported `--runtime` flag; the
service selects runsc and inspection verifies it. This optional host setup
probe is not isolation certification or a new Phase 6B delivery gate.
OpenSandbox owns runtime controls and lifecycle; OpenShip's smaller client
integration is described in the revised [spec](../../docs/superpowers/specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md).

For OpenShip's primary Docker deployment, select `CONTAINER_ENGINE=docker`,
`DOCKER_HOST=unix:///var/run/docker.sock` and
`OPENSHIP_SANDBOX_ENGINE_SOCKET=/var/run/docker.sock`. For OpenSandbox on
Podman use `/run/openship-sandbox/podman.sock`, with separate engine image
preload/qualification. The API and worker receive no engine socket.

## Arch Linux and derivatives with rootless Docker

This optional profile preserves the earlier rootless Docker setup. The primary
profile now uses rootful engine infrastructure with non-root sandbox workloads. Run as the ordinary developer account:

```sh
scripts/bootstrap/bootstrap.sh arch-docker-rootless --verify
```

The role installs `docker`, `docker-compose`, `docker-buildx`, `rootlesskit`,
`slirp4netns`, `fuse-overlayfs` and `shadow` from the official repositories,
and `gvisor-bin` plus `docker-rootless-extras` through an existing
operator-approved `yay`. Missing packages trigger a full Arch upgrade first.
AUR package installation needs separate noninteractive sudo; no AUR helper or
sudo policy is installed. To install those packages interactively instead:

```sh
sudo pacman -S --needed docker docker-compose docker-buildx rootlesskit slirp4netns fuse-overlayfs shadow
yay -S --needed gvisor-bin docker-rootless-extras
scripts/bootstrap/bootstrap.sh arch-docker-rootless --configure-only --verify
```

The initial package setup prompts for sudo. `--configure-only` uses only the
ordinary user's permissions and validates that all dependencies are present.
The account needs an active user systemd session and at least 65536 assigned
subordinate UIDs and GIDs. No ranges or system-wide services are modified.

The role owns these dedicated user paths:

- `~/.config/openship/docker/daemon.json`
- `~/.config/systemd/user/openship-docker.service`
- `~/.local/share/openship/docker/` for engine data
- `$XDG_RUNTIME_DIR/openship-docker/docker.sock` for the API

Configuration/data locations respect `XDG_CONFIG_HOME`/`XDG_DATA_HOME` when
set. The daemon JSON preserves unrelated existing settings and other runtimes,
sets runc as default, registers runsc with no `ignore-cgroups` flag, selects
the systemd cgroup driver and pins this daemon's storage/socket paths. It is
validated with `dockerd --validate`; existing changed configuration is backed
up. The service uses upstream `dockerd-rootless.sh` from the extras package,
with systemd delegation and separate RootlessKit state and PID files. Unit or
daemon configuration changes restart this dedicated user daemon; unchanged
configuration only ensures it is started. Run bootstrap with OpenShip and sandbox
work stopped before changing this daemon's configuration or service unit.

The system Docker daemon/socket, Docker group membership, existing Docker CLI
context and Podman configuration remain unmanaged. Explicitly target the
rootless endpoint when using OpenShip and when mounting the manager socket:

```sh
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/openship-docker/docker.sock"
export OPENSHIP_SANDBOX_ENGINE_SOCKET="$XDG_RUNTIME_DIR/openship-docker/docker.sock"
CONTAINER_ENGINE=docker scripts/openship-stack -e dev status
```

`--verify` first checks the daemon's security options for rootless mode and
refuses a rootful endpoint before pulling images or creating a container.
It pulls the digest-pinned trusted Python fixture if missing, validates runsc
selection before startup, and reads the running process's actual cgroup v2
CPU/memory/swap/PID settings. It runs a fixed identity probe, removes the
unique fixture and verifies absence. Uncertain create replies never cause a
second create. This is a startup/configuration probe, not pressure testing or
complete isolation qualification; the receipt explicitly records that limit.

Check mode skips configuration requiring dependencies that are not installed.
Syntax checking needs no host changes:

```sh
ANSIBLE_CONFIG=scripts/bootstrap/ansible.cfg ansible-playbook \
  -i localhost, -c local scripts/bootstrap/playbooks/arch-docker-rootless.yml --syntax-check
```

Sources: [Docker rootless setup](https://docs.docker.com/engine/security/rootless/),
[rootless resource constraints](https://docs.docker.com/engine/security/rootless/tips/#limiting-resources),
[gVisor Docker setup](https://gvisor.dev/docs/user_guide/quick_start/docker/).

## Arch Linux and derivatives with rootless Podman

Run from a checkout as the ordinary user who owns the rootless stack:

```sh
scripts/bootstrap/bootstrap.sh arch-podman --check
scripts/bootstrap/bootstrap.sh arch-podman
```

The small wrapper installs `ansible-core` with `sudo pacman -Syu --needed`
only when Ansible is missing, resolves the collection versions in
`requirements.yml`, and invokes the selected playbook. A real package setup
asks for the Ansible become password. Check mode (`--dry-run` is an alias)
never installs Ansible or collections; install controller prerequisites first
if they are absent. Ansible may create its own temporary/cache files in check
mode, but it does not apply managed configuration or package changes.

The playbook installs `podman`, `podman-compose`, `runc`, `passt`, `shadow` and
`gvisor-bin`. When packages are missing, it performs a full Arch system upgrade
before package installation. It uses `community.general.pacman` for official
packages and an existing operator-provided `yay` for the AUR gVisor package.
The AUR task uses `--noconfirm`: review and trust that package's build recipe
before applying. The playbook does not bootstrap an AUR helper. Supported
configuration requires Podman with `engine.runtimes_flags` support; the tested
host has Podman 6.1.1, podman-compose 1.6.0 and Ansible Core 2.21.3.

When gVisor is missing, `yay` also needs noninteractive sudo for its package
commands; Ansible's become password is not forwarded to nested sudo. The
playbook checks basic sudo availability before upgrading anything and makes
`yay` fail immediately if further authorization is missing. This is not proof
that every package command is permitted. If authorization is unavailable,
install `gvisor-bin` interactively with `yay -S gvisor-bin`, then rerun.
No sudo policy is installed or changed. See the
[upstream Ansible explanation](https://github.com/ansible/ansible/issues/85536).

For an existing host, skip package changes and optionally exercise a preloaded
Alpine-compatible image through the sandbox API:

```sh
scripts/bootstrap/bootstrap.sh arch-podman --configure-only --check
scripts/bootstrap/bootstrap.sh arch-podman --configure-only --verify
```

The optional probe never pulls an image. It checks the container's native
`OCIRuntime` before executing a trusted echo, then removes the container in
an `always` cleanup task. Override the default preloaded `alpine:latest` fixture
with `-- -e openship_probe_image=<preloaded-reference>`. This is not the Phase 6B
workload image or an isolation qualification.

The role manages only these files, using `XDG_CONFIG_HOME` when set:

- `~/.config/containers/containers.conf.d/90-openship-sandbox.conf`
- `~/.config/systemd/user/openship-sandbox-engine.service`
- `~/.config/systemd/user/openship-sandbox-engine.socket`

Their source files live under this role's `files/` directory. The main
`containers.conf` is preserved; the drop-in supplies `runc` as the CLI default
and `runsc` with `ignore-cgroups` for sandboxes. Only the dedicated socket at
`$XDG_RUNTIME_DIR/openship-sandbox/podman.sock` is enabled. The normal
`podman.socket` and `podman.service` remain unmanaged.

Identical files do not trigger handlers. Profile/service changes restart only
an active sandbox API process, preserving its socket inode and existing
containers. An active **socket unit** change fails before copying files:
stop the manager container and sandbox service/socket first, apply the
playbook, then recreate the manager container to mount the new socket inode.

The target account needs a working user systemd session and administrator-
assigned, non-overlapping subordinate UID/GID ranges. The role validates
these prerequisites rather than guessing allocations. For a production account
that must keep user services after logout, configure user lingering separately.

For SSH targets, pass normal Ansible options after `--`; target hosts require
Python, the selected Linux distribution and a prepared non-root account:

```sh
scripts/bootstrap/bootstrap.sh arch-podman -- \
  -i /path/to/inventory.ini -c ssh --limit single_node
```

Bootstrap installs the controller on the machine where the script runs, not
on SSH targets. Additional controller OS installers can be added as needed.
No production OpenShip deployment is performed or qualified by host setup.

Prepare OpenSandbox image pins, protected TOML and API credentials using the
[operator guide](../../docs/operations/opensandbox.md). Application image
Dockerfiles and the server TOML example remain in `deploy/sandbox/`; the
automated host configuration now belongs here.

## Kubernetes and Helm

Future cluster playbooks can use `kubernetes.core.k8s` for manifests and
`kubernetes.core.helm` for chart releases/values. Declare the collection and
its Python/Helm dependencies when adding that target. Keep host setup and
cluster deployment as distinct selections, with an explicit inventory/context.
No Kubernetes playbook, cluster mutation or Helm release is included yet.

Sources: [Ansible playbooks](https://docs.ansible.com/projects/ansible-core/devel/playbook_guide/playbooks_intro.html),
[Arch package module](https://docs.ansible.com/projects/ansible/latest/collections/community/general/pacman_module.html),
[user systemd services](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/systemd_service_module.html),
[Kubernetes objects](https://docs.ansible.com/projects/ansible/latest/collections/kubernetes/core/k8s_module.html),
[Helm releases](https://docs.ansible.com/projects/ansible/latest/collections/kubernetes/core/helm_module.html).
