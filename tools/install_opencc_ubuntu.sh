#!/usr/bin/env bash
set -euo pipefail

. /etc/os-release
case "${ID:-}:${VERSION_CODENAME:-}" in
  ubuntu:noble) ;;
  *)
    echo "unsupported GitHub runner image: ${ID:-unknown} ${VERSION_CODENAME:-unknown}" >&2
    exit 2
    ;;
esac

source_list="${RUNNER_TEMP:-/tmp}/opencc-ubuntu.sources.list"
printf 'deb https://archive.ubuntu.com/ubuntu %s main universe\n' \
  "$VERSION_CODENAME" > "$source_list"

apt_options=(
  -o "Dir::Etc::sourcelist=$source_list"
  -o "Dir::Etc::sourceparts=-"
  -o "APT::Get::List-Cleanup=0"
  -o "Acquire::Retries=3"
  -o "Acquire::https::Timeout=20"
)

sudo timeout 180s apt-get "${apt_options[@]}" update
sudo timeout 180s apt-get "${apt_options[@]}" install \
  --yes --no-install-recommends opencc
