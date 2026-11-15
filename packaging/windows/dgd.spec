# PyInstaller recipe for DGD.exe, a folder app (not one file) that the installer ships.
#
# One file would unpack ~300 MB of GDAL, PROJ and SciPy to a temp folder on
# every start, and the UI starts the program again for every job. A folder
# starts in a couple of seconds instead.
#
# Build from a normal (not editable) install:  pip install . pyinstaller
#                                              pyinstaller --noconfirm packaging/windows/dgd.spec
# It builds on Linux and macOS too, which is how the frozen app gets tested
# without a Windows machine. Only Windows gets the icon.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

HERE = Path(SPECPATH)

# The UI page, its script and styles, and the imagery coverage map.
datas = collect_data_files("dgd")
# pystac finds its extensions (eo, projection, raster...) through package
# metadata entry points. Without the metadata it quietly finds none.
datas += copy_metadata("pystac", recursive=True)
hiddenimports = collect_submodules("dgd") + collect_submodules("pystac.extensions")

# rasterio (rasters) and pyogrio (geopandas writing files) each bring their own
# GDAL, and PyInstaller has no hook for either. Their compiled modules import
# others (rasterio.serde, pyogrio._geometry, ...) where PyInstaller cannot see,
# and GDAL and PROJ need the data folders each wheel ships next to its code.
SKIP = ("rasterio.rio", "pyogrio.tests")  # a command line and a test suite, both unused
for pkg in ("rasterio", "pyogrio"):
    hiddenimports += collect_submodules(pkg, filter=lambda name: not name.startswith(SKIP))
    datas += collect_data_files(pkg, subdir="gdal_data") + collect_data_files(pkg, subdir="proj_data")

a = Analysis(
    [str(HERE / "launcher.py")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "_tkinter", "IPython", "pytest", "ee", "geemap", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = HERE / "dgd.ico"
exe = EXE(
    pyz,
    a.scripts,
    # Unbuffered, so job progress reaches the UI line by line, and UTF-8, so
    # a place name with an accent does not crash a job's log on a Windows pipe.
    [("u", None, "OPTION"), ("X utf8_mode=1", None, "OPTION")],
    exclude_binaries=True,
    name="DGD",
    console=True,  # the window is the "it's running" sign; closing it quits
    icon=str(icon) if sys.platform == "win32" and icon.exists() else None,
    upx=False,  # UPX-packed exes trip antivirus heuristics
)
coll = COLLECT(exe, a.binaries, a.datas, name="DGD", upx=False)
