# Windows app

For people who want the map and none of the Python. One installer, then a desktop icon.

## Installing

1. Get `DGD-Setup-<version>.exe` (see [Building the installer](#building-the-installer) for where it comes from).
2. Double-click it. It installs for you alone, so it asks for no administrator password.
3. Windows may say **"Windows protected your PC"**. The installer is not code-signed, and SmartScreen distrusts anything unsigned it has not seen many times. Click **More info**, then **Run anyway**.

It goes to `%LOCALAPPDATA%\Programs\Disturbed Ground Detector` and puts **Disturbed Ground Detector** on the desktop and in the Start menu.

## Using it

Double-click the icon. A black window opens, then the map opens in your web browser. From there everything is as in [Map interface](ui.md).

- **The black window is the app.** Leave it open while you work. Close it to quit. A job still running stops with it.
- **Double-clicking again** while it runs just opens another browser tab on the same app.
- **Results** go to `Documents\Disturbed Ground Detector`, one folder per job. Installing, upgrading and uninstalling never touch them.
- **Downloaded imagery** is cached in `%LOCALAPPDATA%\dgd\Cache`, so a second look at the same place is quick. Safe to delete when you need the space.
- **Internet** is needed for imagery and for the map. Nothing about your searches leaves the computer except the imagery requests themselves (see [Map interface](ui.md)).

The installed `DGD.exe` is also the full command line. In a terminal:

```bat
"%LOCALAPPDATA%\Programs\Disturbed Ground Detector\DGD.exe" scan --help
```

Uninstall it from **Settings > Apps** like anything else.

Earth Engine (`dgd download --source ee`) is not in the app. Nobody needs it: the default imagery source has no account and no key.

## Building the installer

The installer is built on a Windows machine with [PyInstaller](https://pyinstaller.org) and [Inno Setup](https://jrsoftware.org/isinfo.php). Everything for it is in `packaging/windows/`.

### On GitHub

It runs when you push a tag starting with `v`, when a push changes the packaging (`packaging/windows/`, the workflow, `src/dgd/app.py`), and by hand from **Actions > Windows app > Run workflow** once the workflow is on `main`. When it is done, the run page has a `DGD-Setup` download. The workflow:

1. installs dgd and PyInstaller on a Windows runner,
2. freezes the app into `dist\DGD\` (`dgd.spec`),
3. smoke-tests the frozen app without the internet: a grave search on a painted field and the map page (`smoke_test.py`),
4. builds `DGD-Setup-<version>.exe` (`dgd.iss`),
5. installs it silently and runs the installed copy,
6. keeps the installer as the run's `DGD-Setup` artifact (at the bottom of the run's summary page, for signed-in users, for 90 days),
7. on a `v` tag, also attaches it to that tag's GitHub release, creating the release if there is none. That is the link to give people.

### By hand, on Windows

```bat
python -m venv build-env
build-env\Scripts\activate
pip install . "pyinstaller>=6.11,<7"
python packaging\windows\make_icon.py packaging\windows\dgd.ico
pyinstaller --noconfirm --clean packaging\windows\dgd.spec
python packaging\windows\smoke_test.py dist\DGD\DGD.exe
iscc /DAppVersion=0.2.0 packaging\windows\dgd.iss
```

Install with `pip install .`, not `-e`: PyInstaller collects an editable install unreliably.

### On Linux, with Wine

PyInstaller cannot cross-build, but Windows Python, PyInstaller and Inno Setup all run under [Wine](https://www.winehq.org). One script does it in a container, so nothing gets installed on your machine:

```bash
packaging/windows/build-on-linux.sh
```

It writes `dist/DGD-Setup-<version>.exe`, the same installer GitHub builds. It needs podman or docker (podman preferred: the output then belongs to you, not root). The first run builds a ~2 GB build image (`wine.Dockerfile`: Debian, Wine, Windows Python from NuGet, Inno Setup, dgd's dependencies), which takes ten minutes or so. After that a build takes a few minutes, most of it the smoke test; `SKIP_SMOKE_TEST=1` skips it.

The smoke test runs under Wine too, which is close to Windows but not Windows. Try the installer on a real Windows machine before handing it out.

### Testing the frozen app without Windows

The spec builds on Linux and macOS too, minus the icon and the installer. That is how to find what freezing broke without a Windows machine:

```bash
pip install . "pyinstaller>=6.11,<7"
pyinstaller --noconfirm packaging/windows/dgd.spec
python packaging/windows/smoke_test.py dist/DGD/DGD
```

### Why it is built the way it is

- **A folder, not a single exe.** The bundle carries GDAL, PROJ, SciPy and friends, about 450 MB unpacked. A single-file exe would unpack all of it to a temp folder on every start, and the map starts the program again for every job. The installer hides the folder anyway.
- **Hand-written hooks.** PyInstaller does not see what rasterio's and pyogrio's compiled modules import, nor the GDAL and PROJ data each wheel ships, and pystac finds its extensions through package metadata. The spec collects all three. If a new release of any of them breaks the frozen app, the smoke test is where it shows.
- **Not signed.** A code-signing certificate costs money every year. Until somebody pays for one, SmartScreen warns, and now and then an antivirus flags the PyInstaller bootloader. Both are false alarms: the source and the build log are public.
