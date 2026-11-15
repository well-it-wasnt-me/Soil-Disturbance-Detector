# Windows build tools under Wine, so DGD-Setup.exe can be built on Linux.
# Built and used by packaging/windows/build-on-linux.sh; nothing else needs it.
#
# Windows Python (NuGet's plain-folder package, no installer to coax through
# Wine), PyInstaller, dgd's dependencies, and Inno Setup 6, the same major
# version GitHub's Windows runners have.
FROM docker.io/library/debian:trixie-slim

# Bumping a version means updating its checksum: sha256sum of the download.
ARG PYTHON_VERSION=3.13.17
ARG PYTHON_SHA256=dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5
ARG INNO_VERSION=6.7.4
ARG INNO_SHA256=a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c
# dgd's dependencies, from pyproject.toml, so they live in a cached layer.
ARG DEPS

ENV DEBIAN_FRONTEND=noninteractive WINEDEBUG=-all WINEPREFIX=/wine WINEARCH=win64 XDG_RUNTIME_DIR=/tmp/xdg \
    PYTHON="C:\\Python\\python.exe" ISCC="C:\\Inno\\ISCC.exe"

# wine32 too: the Inno Setup 6 installer and compiler are 32-bit programs.
RUN dpkg --add-architecture i386 && apt-get update \
    && apt-get install -y --no-install-recommends wine wine64 wine32:i386 xvfb xauth curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -m 700 -p "$XDG_RUNTIME_DIR" && wineboot --init && wineserver --wait

RUN curl -fsSL -o /tmp/python.nupkg "https://api.nuget.org/v3-flatcontainer/python/${PYTHON_VERSION}/python.${PYTHON_VERSION}.nupkg" \
    && echo "${PYTHON_SHA256}  /tmp/python.nupkg" | sha256sum -c - \
    && unzip -q /tmp/python.nupkg 'tools/*' -d /tmp/py \
    && mv /tmp/py/tools /wine/drive_c/Python && rm -rf /tmp/python.nupkg /tmp/py \
    && wine "$PYTHON" -m ensurepip --upgrade \
    && wine "$PYTHON" -m pip install --no-cache-dir --upgrade pip \
    && wineserver --wait

# The Inno Setup installer wants a display even when silent, hence xvfb.
RUN tag="is-$(echo "$INNO_VERSION" | tr . _)" \
    && curl -fsSL -o /tmp/is.exe "https://github.com/jrsoftware/issrc/releases/download/${tag}/innosetup-${INNO_VERSION}.exe" \
    && echo "${INNO_SHA256}  /tmp/is.exe" | sha256sum -c - \
    && xvfb-run -a wine /tmp/is.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER "/DIR=C:\\Inno" \
    && wineserver --wait && rm /tmp/is.exe \
    && test -f /wine/drive_c/Inno/ISCC.exe

RUN wine "$PYTHON" -m pip install --no-cache-dir "pyinstaller>=6.11,<7" $DEPS && wineserver --wait

COPY wine-build.sh /usr/local/bin/wine-build.sh
