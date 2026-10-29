---
type: plan
summary: Ansible bootstrap with environment playbooks and shared host configuration.
status: implemented
date: 2026-10-29
---

# Environment bootstrap

Accepted direction: organize automated host configuration under
`scripts/bootstrap/`. Implement `playbooks/arch-podman.yml` for the user's
Arch-derived rootless Podman machine; future contributors add `arch-docker.yml`,
`debian-docker.yml` or cluster playbooks. Keep standard `podman.socket`
unmanaged and Kubernetes deployment separate from host preparation.

1. Replace the draft Bash configuration installer with a thin controller
   bootstrap and a real Ansible playbook. Pin its collection dependency.
2. Declare environment packages in the Arch playbook. Use the native pacman
   module and an operator-provided AUR helper for gVisor; never build as root.
3. Move automated runtime configuration and user units into the shared
   `rootless_podman` role. Retain application deployment examples in `deploy/`.
4. Use Ansible modules and handlers for idempotent file/service management.
   Validate subordinate IDs, user systemd and runtime defaults. Preserve
   socket inodes on service updates; require manager recreation for an active
   socket unit replacement.
5. Keep the optional trusted runtime probe, validate native `OCIRuntime`
   before execution and always clean up its known container.
6. Update usage, environment contribution guidance and Phase 6B references.
   Verify wrapper tests, package contracts, syntax, real check mode, apply
   with packages unchanged and a subsequent zero-change run. Review and
   commit/push through the repository workflow.

The earlier draft script and its uncommitted tests were superseded by this
conversion. Wrapper routing and declarative contracts are tested in
`tests/test_bootstrap.py`; real Ansible checks/apply provide runtime evidence.
Original sandbox packaging tests retain their assertions at the moved paths.
Package installation on a clean machine and full Phase 6B isolation remain
separate qualification; no existing package upgrades are needed for this host.

Verification: 25 focused wrapper/packaging/preflight tests pass, with Ruff,
Bash syntax and real Ansible syntax/check-mode validation. Ansible Core
2.21.3 with community.general 13.3.0 applied the role with package installation
skipped. A subsequent apply reported `changed=0`, `failed=0`. The final trusted
probe verified actual `runsc`, captured the expected echo and removed the
container. Review findings on socket inodes, inventory isolation and AUR
package convergence were fixed before completion.
