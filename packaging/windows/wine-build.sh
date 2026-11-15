#!/usr/bin/env bash
# Runs inside the Wine container (see wine.Dockerfile). Builds the installer
# from the source mounted read-only at /repo and copies it to /out.
# The same steps as .github/workflows/windows-app.yml, under Wine.
set -euo pipefail

py() { wine "$PYTHON" "$@"; }

rm -rf /build && mkdir /build && cd /build
cp -r /repo/pyproject.toml /repo/README.md /repo/LICENSE /repo/src /repo/packaging .
find src -name __pycache__ -prune -exec rm -rf {} +

echo "== Installing dgd"
py -m pip install --no-cache-dir --quiet .

echo "== Drawing the icon"
py packaging/windows/make_icon.py packaging/windows/dgd.ico

echo "== Freezing"
py -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build packaging/windows/dgd.spec

if [[ "${SKIP_SMOKE_TEST:-}" != "1" ]]; then
    echo "== Smoke testing the frozen app (under Wine)"
    py packaging/windows/smoke_test.py dist/DGD/DGD.exe
fi

echo "== Building the installer"
version=$(py -c "import dgd; print(dgd.__version__)" | tr -d '\r')
wine "$ISCC" /Q "/DAppVersion=$version" 'packaging\windows\dgd.iss'
wineserver --wait

cp dist/DGD-Setup-*.exe /out/
[[ -n "${HOST_UID:-}" ]] && chown "$HOST_UID:$HOST_GID" /out/DGD-Setup-*.exe  # rootful Docker writes as root
ls -l /out/DGD-Setup-*.exe
