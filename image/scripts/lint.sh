#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly script_dir
image_dir=$(dirname -- "$script_dir")
readonly image_dir

missing=()
for command in shellcheck yamllint; do
  command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
if ((${#missing[@]})); then
  printf 'lint: missing required tools: %s\n' "${missing[*]}" >&2
  printf 'Install shellcheck and yamllint before retrying.\n' >&2
  exit 127
fi

cd "$image_dir"
shellcheck scripts/*.sh provision/*.sh
yamllint -c .yamllint ../.github/workflows/rtpengine-ami.yml
