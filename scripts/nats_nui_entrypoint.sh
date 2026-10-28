#!/bin/sh
set -eu

profile="${OPENSHIP_NUI_PROFILE:-local}"
case "$profile" in
  local|dev) ;;
  *) echo "NUI profile must be local or dev" >&2; exit 1 ;;
esac
context_file="openship-$profile.json"
source_context="/nats-cli-contexts/$context_file"
context_dir=/db/nui-contexts

if [ "$(id -u)" -ne 0 ]; then
  echo "NUI profile staging must run as root before the image drops privileges" >&2
  exit 1
fi

if [ -L /nats-cli-contexts ] || [ -L "$source_context" ] || [ ! -r "$source_context" ]; then
  echo "NUI connection profile is missing or unreadable: $source_context" >&2
  exit 1
fi

if [ -L "$context_dir" ] || [ -L "$context_dir/$context_file" ]; then
  echo "NUI data volume contains an unexpected symlink at the connection profile path" >&2
  exit 1
fi

mkdir -p "$context_dir"
cp "$source_context" "$context_dir/$context_file"
chown nui:nui "$context_dir" "$context_dir/$context_file"
chmod 0700 "$context_dir"
chmod 0600 "$context_dir/$context_file"

exec /entrypoint.sh "$@"
