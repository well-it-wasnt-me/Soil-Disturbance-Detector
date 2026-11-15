# Windows build tools under Wine, so DGD-Setup.exe can be built on Linux.
# Built and used by packaging/windows/build-on-linux.sh; nothing else needs it.
#
# Windows Python (NuGet's plain-folder package, no installer to coax through
# Wine), PyInstaller, dgd's dependencies, and Inno Setup.
FROM docker.io/library/debian:trixie-slim

ARG PYTHON_VERSION=3.13.17
ARG INNO_VERSION=6.7.4
# dgd's dependencies, from pyproject.toml, so they live in a cached layer.
ARG DEPS

ENV DEBIAN_FRONTEND=noninteractive WINEDEBUG=-all WINEPREFIX=/wine WINEARCH=win64 \
    PYTHON="C:\\Python\\python.exe" ISCC="C:\\Inno\\ISCC.exe"

RUN apt-get update \
    && apt-get install -y --no-install-recommends wine wine64 xvfb xauth curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN wineboot --init && wineserver --wait

RUN curl -fsSL -o /tmp/python.nupkg "https://api.nuget.org/v3-flatcontainer/python/${PYTHON_VERSION}/python.${PYTHON_VERSION}.nupkg" \
    && unzip -q /tmp/python.nupkg 'tools/*' -d /tmp/py \
    && mv /tmp/py/tools /wine/drive_c/Python && rm -rf /tmp/python.nupkg /tmp/py \
    && wine "$PYTHON" -m ensurepip --upgrade \
    && wine "$PYTHON" -m pip install --no-cache-dir --upgrade pip \
    && wineserver --wait

# The Inno Setup installer wants a display even when silent, hence xvfb.
RUN curl -fsSL -o /tmp/is.exe "https://files.jrsoftware.org/is/6/innosetup-${INNO_VERSION}.exe" \
    && xvfb-run -a wine /tmp/is.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER "/DIR=C:\\Inno" \
    && wineserver --wait && rm /tmp/is.exe \
    && test -f /wine/drive_c/Inno/ISCC.exe

RUN wine "$PYTHON" -m pip install --no-cache-dir "pyinstaller>=6.11,<7" $DEPS && wineserver --wait

COPY wine-build.sh /usr/local/bin/wine-build.sh
