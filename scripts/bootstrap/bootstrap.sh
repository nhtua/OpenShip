#!/usr/bin/env bash
# Bootstrap the controller, then delegate host configuration to Ansible.
set -euo pipefail
BOOTSTRAP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
Usage: bootstrap.sh {arch-docker|arch-docker-podman|arch-docker-rootless|arch-podman}
                    [--check|--dry-run] [--configure-only] [--verify]
                              [-- ANSIBLE_OPTIONS...]
Run as the ordinary developer account. Ansible configures the selected engine.
Docker is the primary target; arch-docker-podman sets up both rootful engines.
The arch-docker-rootless and arch-podman targets retain rootless alternatives.
If Ansible is missing on Arch, bootstrap installs ansible-core with pacman -Syu.
Use --configure-only for existing packages. Docker --verify checks a trusted
Python fixture and its cgroup settings; rootful probes also require a non-root
workload identity. The rootless Podman probe uses preloaded Alpine.
Extra Ansible options can select a remote inventory.
USAGE
}
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
if (($# == 0)); then usage; exit 2; fi
if [[ "$1" == -h || "$1" == --help ]]; then usage; exit 0; fi
target="$1"; shift
[[ "$target" =~ ^[a-z0-9-]+$ && -f "$BOOTSTRAP_DIR/playbooks/$target.yml" ]] || fail "Unknown target: $target"
[[ "$(id -u)" != 0 ]] || fail 'Run as the ordinary user; Ansible escalates only package tasks.'
check=0
configure_only=false
args=()
while (($#)); do
  case "$1" in
    --check|--dry-run) check=1 ;;
    --configure-only) configure_only=true ;;
    --verify) args+=(-e openship_verify=true) ;;
    --) shift; args+=("$@"); break ;;
    *) fail "Unknown option: $1 (pass Ansible options after --)" ;;
  esac
  shift
done
if ! command -v ansible-playbook >/dev/null 2>&1; then
  if ((check)); then
    printf 'Ansible is missing. Install ansible-core before running check mode.\n'
    printf 'On Arch: sudo pacman -Syu --needed ansible-core\n'
    exit 1
  fi
  [[ "$configure_only" == false ]] || fail 'Install ansible-core first or omit --configure-only.'
  command -v pacman >/dev/null 2>&1 || fail 'Install ansible-core with your controller package manager.'
  sudo pacman -Syu --needed ansible-core
fi
export ANSIBLE_CONFIG="$BOOTSTRAP_DIR/ansible.cfg"
# Resolve exactly the collection version recorded in requirements.yml. Check
# mode never installs dependencies; ansible-playbook reports a missing module.
if ((!check)); then ansible-galaxy collection install -r "$BOOTSTRAP_DIR/requirements.yml"; fi
if ((check)); then args+=(--check --diff)
elif [[ "$configure_only" == false || "$target" == arch-docker || "$target" == arch-docker-podman ]]; then args+=(--ask-become-pass); fi
connection=(-i 'localhost,' -c local)
for argument in "${args[@]}"; do
  if [[ "$argument" == -i || "$argument" == -i?* || "$argument" == --inventory || "$argument" == --inventory=* || "$argument" == --inventory-file || "$argument" == --inventory-file=* ]]; then
    connection=()
    break
  fi
done
exec ansible-playbook "${connection[@]}" "$BOOTSTRAP_DIR/playbooks/$target.yml" \
  -e "openship_configure_only=$configure_only" "${args[@]}"
