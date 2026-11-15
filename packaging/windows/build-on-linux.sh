#!/usr/bin/env bash
# Build dist/DGD-Setup-<version>.exe on Linux, with Windows Python, PyInstaller
# and Inno Setup running under Wine in a container. Nothing is installed on
# this machine and nothing is pushed anywhere.
#
#   packaging/windows/build-on-linux.sh                    # build, smoke test, installer
#   SKIP_SMOKE_TEST=1 packaging/windows/build-on-linux.sh  # a few minutes faster
#
# Needs podman (preferred, files come out owned by you) or docker. The first
# run builds the build image, about 2 GB, and takes ten minutes or so. Later
# runs reuse it, and rebuild only the dependency layer when pyproject.toml's
# dependencies change.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/dist"
IMAGE=dgd-windows-build

if [[ -n "${CONTAINER:-}" ]]; then engine="$CONTAINER"
elif command -v podman >/dev/null; then engine=podman
elif command -v docker >/dev/null; then engine=docker
else echo "Needs podman or docker." >&2; exit 1
fi

# The version comes from git (docs/releasing.md). The container gets the source
# without .git, so it is worked out here and handed in.
version="$(cd "$ROOT" && python3 packaging/next_version.py --current)"
echo "Version $version"

deps="$(python3 -c 'import sys, tomllib; print(" ".join(tomllib.load(open(sys.argv[1], "rb"))["project"]["dependencies"]))' "$ROOT/pyproject.toml")"

echo "Build image ($engine)..."
"$engine" build -t "$IMAGE" -f "$HERE/wine.Dockerfile" --build-arg "DEPS=$deps" "$HERE"

mkdir -p "$OUT"
owner=()
[[ "$engine" == docker ]] && owner=(-e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)")
"$engine" run --rm "${owner[@]}" -e "SKIP_SMOKE_TEST=${SKIP_SMOKE_TEST:-}" -e "DGD_VERSION=$version" \
    -v "$ROOT:/repo:ro,z" -v "$OUT:/out:z" "$IMAGE" bash /usr/local/bin/wine-build.sh

echo "Done: $(ls -1 "$OUT"/DGD-Setup-*.exe | tail -1)"
