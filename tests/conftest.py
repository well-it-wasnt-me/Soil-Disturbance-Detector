import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

# Make src/ importable without installing.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dgd.aoi import grid_from_point  # noqa: E402
from dgd.stac import Acquisition, Scene  # noqa: E402

# Typical surface reflectance, band order blue, green, red, nir, swir16, swir22.
GRASS = np.array([0.03, 0.07, 0.04, 0.40, 0.20, 0.10], dtype=np.float32)
SOIL = np.array([0.08, 0.11, 0.15, 0.22, 0.30, 0.24], dtype=np.float32)
WASTE = np.array([0.16, 0.18, 0.19, 0.24, 0.27, 0.22], dtype=np.float32)


def make_acq(d: date, platform: str = "sentinel-2a") -> Acquisition:
    scene = Scene(
        id=f"FAKE_{d.isoformat()}",
        collection="fake",
        datetime=datetime(d.year, d.month, d.day, 10, tzinfo=timezone.utc),
        platform=platform,
        tile="00XXX",
        cloud_cover=5.0,
        hrefs={},
    )
    return Acquisition(platform=platform, date=d, scenes=(scene,))


class FakeSource:
    """In-memory SceneSource. ``frames`` maps date to a (6, rows, cols) array."""

    def __init__(self, frames: dict):
        self.frames = frames
        self.failures: list[str] = []

    def search(self, grid, start, end):
        return [make_acq(d) for d in sorted(self.frames) if start <= d <= end]

    def fetch_all(self, acqs, grid, on_done=None):
        out = {}
        for a in acqs:
            out[a.key] = self.frames[a.date]
            if on_done:
                on_done(a)
        return out

    def describe(self):
        return {"provider": "fake"}


def pass_dates(start: date, end: date, every: int = 5) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=every)
    return out


def build_world(grid, target_start: date, target_end: date, years: int = 3, seed: int = 0):
    """Synthetic passes with known truth.

    - background: grass with mild noise
    - ``dump``: grass in every baseline year, waste from 40 days into the target window on
    - ``plowed``: bare soil for a month at the same time every year, including the target
    - ``dig``: bare soil for ~25 days inside the target window, then grass again
    - one hazy pass in the target window that brightens everything
    """
    rng = np.random.default_rng(seed)
    h, w = grid.shape
    regions = {
        "dump": (slice(5, 12), slice(5, 14)),
        "plowed": (slice(20, 34), slice(20, 34)),
        "dig": (slice(40, 46), slice(8, 14)),
    }
    from dgd.anomaly import shift_years

    frames = {}
    windows = [(shift_years(target_start, -k), shift_years(target_end, -k)) for k in range(years, 0, -1)]
    windows.append((target_start, target_end))
    dump_from = target_start + timedelta(days=40)
    dig_from, dig_to = target_start + timedelta(days=60), target_start + timedelta(days=85)
    haze_day = None
    for wi, (ws, we) in enumerate(windows):
        for d in pass_dates(ws, we):
            arr = np.broadcast_to(GRASS[:, None, None], (6, h, w)).copy()
            arr *= rng.normal(1.0, 0.03, size=(1, h, w)).astype(np.float32)
            # Plowing every year, 30 to 60 days into the season.
            offset = (d - ws).days
            if 30 <= offset <= 60:
                arr[(slice(None), *regions["plowed"])] = SOIL[:, None, None]
            if wi == len(windows) - 1:
                if d >= dump_from:
                    arr[(slice(None), *regions["dump"])] = WASTE[:, None, None]
                if dig_from <= d <= dig_to:
                    arr[(slice(None), *regions["dig"])] = SOIL[:, None, None]
                if haze_day is None and offset > 100:
                    haze_day = d
                    arr[:3] += 0.08
            # A cloud now and then, so the NaN paths get exercised.
            if rng.random() < 0.2:
                r0 = rng.integers(0, h - 8)
                c0 = rng.integers(0, w - 8)
                arr[:, r0 : r0 + 8, c0 : c0 + 8] = np.nan
            frames[d] = arr.astype(np.float32)
    return frames, regions


@pytest.fixture
def small_grid():
    # 60 x 60 pixels somewhere quiet. The actual place does not matter, the data is fake.
    return grid_from_point(10.0, 20.0, 300)


@pytest.fixture
def synthetic_world(small_grid):
    ts, te = date(2024, 4, 1), date(2024, 9, 30)
    frames, regions = build_world(small_grid, ts, te)
    return small_grid, ts, te, FakeSource(frames), regions
