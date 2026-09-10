#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly script_dir
image_dir=$(dirname -- "$script_dir")
readonly image_dir

missing=()
for command in packer python3; do
  command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
if ((${#missing[@]})); then
  printf 'validate-local: missing required tools: %s\n' "${missing[*]}" >&2
  printf 'Run make -C image init after installing Packer and Python 3.\n' >&2
  exit 127
fi

cd "$image_dir"
packer fmt -check -recursive packer
packer validate -syntax-only packer
for script in provision/*.sh; do bash -n "$script"; done
PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/rtpengine-ami-pycache" \
  python3 -m compileall -q -f assets provision tests
python3 -m unittest discover -s tests -p 'test_*.py'
printf 'AWS-free validation passed.\n'
