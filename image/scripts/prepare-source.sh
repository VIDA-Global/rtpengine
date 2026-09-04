#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
image_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
repo_root=$(git -C "$image_dir" rev-parse --show-toplevel)
output_dir="$image_dir/build/source"
archive="$output_dir/rtpengine-head.tar.gz"
manifest="$output_dir/source-manifest.json"

commit=$(git -C "$repo_root" rev-parse --verify 'HEAD^{commit}')
tree=$(git -C "$repo_root" rev-parse --verify 'HEAD^{tree}')
commit_timestamp=$(git -C "$repo_root" show -s --format=%cI "$commit")
source_date_epoch=$(git -C "$repo_root" show -s --format=%ct "$commit")
version=$(git -C "$repo_root" show "$commit:debian/changelog" | \
  awk 'NR == 1 { value = $2; gsub(/^\(/, "", value); gsub(/\)$/, "", value); print value }')

[[ $commit =~ ^[0-9a-f]{40}$ ]]
[[ $tree =~ ^[0-9a-f]{40}$ ]]
[[ $version =~ ^[0-9A-Za-z.+:~_-]+$ ]] || {
  printf 'Unable to derive a safe package version from HEAD: %s\n' "$version" >&2
  exit 65
}

mkdir -p -- "$output_dir"
tmp_archive="$output_dir/.rtpengine-head.tar.gz.$$"
tmp_manifest="$output_dir/.source-manifest.json.$$"
trap 'rm -f -- "$tmp_archive" "$tmp_manifest"' EXIT

# git archive reads committed objects only. Worktree changes and untracked files
# (including config.mk.new) cannot affect the build input.
git -C "$repo_root" archive --format=tar --prefix="rtpengine-$version/" "$commit" | gzip -9n >"$tmp_archive"
if command -v sha256sum >/dev/null 2>&1; then
  archive_sha256=$(sha256sum "$tmp_archive" | awk '{print $1}')
else
  archive_sha256=$(shasum -a 256 "$tmp_archive" | awk '{print $1}')
fi

cat >"$tmp_manifest" <<EOF
{
  "archive": "rtpengine-head.tar.gz",
  "archive_sha256": "$archive_sha256",
  "commit": "$commit",
  "commit_timestamp": "$commit_timestamp",
  "source_date_epoch": $source_date_epoch,
  "source_ref": "HEAD",
  "tree": "$tree",
  "version": "$version"
}
EOF

chmod 0644 "$tmp_archive" "$tmp_manifest"
mv -f -- "$tmp_archive" "$archive"
mv -f -- "$tmp_manifest" "$manifest"
trap - EXIT

printf 'Prepared committed HEAD %s (%s), SHA-256 %s\n' "$commit" "$version" "$archive_sha256"
