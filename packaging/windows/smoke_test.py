"""Run the frozen app the way a user and the UI would, without touching the internet.

    python packaging/windows/smoke_test.py dist/DGD/DGD.exe

Catches what freezing breaks: a module PyInstaller missed, GDAL or PROJ
without their data files, the UI page not bundled, jobs that cannot start
the app again. Run it with a Python that has numpy and rasterio (the build
environment); it only uses them to paint a test image.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

PORT = 8799


def paint_field(path: Path) -> None:
    """A 60 x 60 m grassy field at 10 cm with one bare patch the size of a grave, RGB plus NIR."""
    rng = np.random.default_rng(7)
    n = 600
    img = np.empty((4, n, n), dtype=np.uint8)
    for band, mean in enumerate((70, 110, 60, 170)):  # red, green, blue, nir: grass
        img[band] = np.clip(rng.normal(mean, 12, (n, n)), 0, 255)
    soil = (slice(290, 310), slice(285, 305))  # 2 m x 2 m
    for band, mean in enumerate((120, 100, 80, 110)):
        img[(band, *soil)] = np.clip(rng.normal(mean, 6, (20, 20)), 0, 255)
    # UTM 31N, a field near Orleans. Any projected CRS would do.
    with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=4, dtype="uint8", crs="EPSG:32631",
                       transform=from_origin(430000, 5310000, 0.1, 0.1)) as dst:
        dst.write(img)


def run(cmd: list, timeout: int = 600) -> str:
    print("$", " ".join(map(str, cmd)), flush=True)
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    print(p.stdout[-3000:], p.stderr[-3000:], f"({time.time() - t:.1f} s, exit {p.returncode})", sep="\n", flush=True)
    if p.returncode != 0:
        raise SystemExit(f"FAILED: {cmd[1]} exited with {p.returncode}")
    return p.stdout + p.stderr


def check_ui(exe: Path, out: Path) -> None:
    proc = subprocess.Popen([exe, "ui", "--no-browser", "--port", str(PORT), "--output-dir", out],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                raise SystemExit(f"FAILED: the UI exited with {proc.returncode}:\n{proc.stdout.read()}")
            try:
                page = opener.open(f"http://127.0.0.1:{PORT}/", timeout=5).read()
                css = opener.open(f"http://127.0.0.1:{PORT}/static/app.css", timeout=5).read()
                js = opener.open(f"http://127.0.0.1:{PORT}/static/app.js", timeout=5).read()
                break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("FAILED: the UI did not answer within two minutes")
        assert b"window.DGD" in page and b"__DGD_TOKEN__" not in page, "UI page not served or token not filled in"
        assert len(css) > 1000 and len(js) > 1000, "UI styles or script missing from the bundle"
        print(f"UI answered: page {len(page)} bytes, css {len(css)}, js {len(js)}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main(exe_arg: str) -> None:
    exe = Path(exe_arg).resolve()
    # Windows may still hold a file of the UI process just stopped; that is not a failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        version = run([exe, "--version"])
        assert "dgd" in version, version
        field = tmp / "field.tif"
        paint_field(field)
        # The grave search touches most of the stack: rasterio and GDAL, PROJ,
        # SciPy, shapely, geopandas and pyogrio writing files, matplotlib.
        run([exe, "graves", "--after", field, "--bands", "rgbn", "--no-osm", "--output-dir", tmp / "graves"])
        assert (tmp / "graves" / "manifest.json").exists(), "graves wrote no manifest"
        check_ui(exe, tmp / "ui")
    print("Smoke test passed.")


if __name__ == "__main__":
    main(sys.argv[1])
