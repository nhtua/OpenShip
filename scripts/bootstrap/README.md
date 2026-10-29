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
    arch-podman.yml
  roles/
    rootless_podman/
      defaults/ files/ handlers/ tasks/
```

Only **arch-podman** is implemented. Contributors can add `arch-docker.yml`,
`debian-docker.yml` or other targets under `playbooks/` and reuse component
roles. Unimplemented targets fail before installing anything.

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
