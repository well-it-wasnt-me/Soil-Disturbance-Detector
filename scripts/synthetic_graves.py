"""Plant fake graves in real imagery and see how many the detector finds.

There is no public archive of aerial images of murder victims' graves, and
there should not be. So this does what forensic researchers do with pig
carcasses, minus the pigs: it paints grave-shaped patches of disturbed soil
into real high resolution imagery, with real cows, ditches, molehills and
tractor tracks around them, and counts hits and false alarms.

A synthetic grave is a rotated rectangle 1.6 to 2.4 m by 0.6 to 1.0 m with
ragged edges, in one of three states: fresh soil, grass growing back in
clumps, or sod put back over it by somebody careful. See ``plant_graves``.

It is a test of whether the detector can see a grave-sized patch of soil
where there was none. It is not proof it would find a real one. Real graves
get covered with leaves, branches, turf put back by someone who thought
about it. Treat the numbers as an upper bound.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

GENERIC_SOIL = np.array([0.42, 0.36, 0.30, 0.38], dtype=np.float32)  # red, green, blue, nir


def scene_soil(img) -> np.ndarray:
    """Median color of the scene's bare ground, or a generic loam if there is none."""
    r, g, b = img.band("red"), img.band("green"), img.band("blue")
    if img.has_nir:
        n = img.band("nir")
        with np.errstate(invalid="ignore", divide="ignore"):
            bare = (n - r) / (n + r) < 0.15
    else:
        with np.errstate(invalid="ignore", divide="ignore"):
            bare = (2 * g - r - b) / (r + g + b) < 0.0
    bare &= np.isfinite(r)
    if bare.mean() > 0.02:
        idx = [img.roles[k] for k in ("red", "green", "blue", "nir") if k in img.roles]
        return np.array([np.nanmedian(img.data[i][bare]) for i in idx], dtype=np.float32)
    return GENERIC_SOIL[: len(img.roles)]


SCENARIOS = ("fresh", "regrowing", "turf")


def plant_graves(img, n: int, rng: np.random.Generator, scenario: str = "fresh", min_spacing_m: float = 25.0,
                 margin_m: float = 12.0) -> list[dict]:
    """Paint ``n`` graves into ``img.data`` in place. Returns their positions and sizes.

    Scenarios, from easy to nasty:

    ``fresh``      bare soil, mild clod texture, a halo of scattered spoil on
                   the grass around it. Days to a couple of weeks old.
    ``regrowing``  the same, with patchy grass back over half of it. Weeks to
                   months.
    ``turf``       somebody cut the sod, dug, and put the sod back. Grass
                   slightly stressed (less green, less NIR, a bit more red),
                   faint dark cut lines along the outline. The careful case.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    res = img.grid.res
    h, w = img.grid.shape
    soil = scene_soil(img)
    order = [img.roles[k] for k in ("red", "green", "blue", "nir") if k in img.roles]
    valid = np.isfinite(img.data).all(axis=0)
    graves, tries = [], 0
    margin = int(margin_m / res)

    def smooth_noise(shape, sigma_m, std):
        z = ndimage.gaussian_filter(rng.normal(0, 1, shape), max(0.5, sigma_m / res))
        return z / max(z.std(), 1e-9) * std

    while len(graves) < n and tries < 5000:
        tries += 1
        rc, cc = int(rng.integers(margin, h - margin)), int(rng.integers(margin, w - margin))
        if not valid[rc, cc]:
            continue
        if any(math.hypot(rc - g["row"], cc - g["col"]) * res < min_spacing_m for g in graves):
            continue
        length, width = rng.uniform(1.6, 2.4), rng.uniform(0.6, 1.0)
        angle = rng.uniform(0, math.pi)
        half = int(math.ceil((max(length, width) / 2 + 1.0) / res)) + 2
        r0, r1, c0, c1 = rc - half, rc + half + 1, cc - half, cc + half + 1
        if r0 < 0 or c0 < 0 or r1 > h or c1 > w:
            continue
        yy, xx = np.mgrid[r0:r1, c0:c1].astype(np.float32)
        dy, dx = (yy - rc) * res, (xx - cc) * res
        u = dx * math.cos(angle) + dy * math.sin(angle)
        v = -dx * math.sin(angle) + dy * math.cos(angle)
        # Signed distance to the outline, positive inside, with a ragged 5 cm wobble.
        sd = np.minimum(length / 2 - np.abs(u), width / 2 - np.abs(v)) + smooth_noise(u.shape, 0.2, 0.05)
        inside = np.clip((sd + res / 2) / res, 0, 1)
        sub = img.data[order, r0:r1, c0:c1].copy()
        if scenario in ("fresh", "regrowing"):
            tone = rng.choice([0.8, 1.1])  # moist darker subsoil, or dried lighter spoil
            texture = 1 + smooth_noise(u.shape, 0.08, 0.08)
            patch = soil[:, None, None] * tone * texture[None]
            cover = inside.copy()
            if scenario == "regrowing":
                # Grass comes back in clumps, not as a uniform tint.
                clumps = ndimage.gaussian_filter(rng.random(u.shape), max(0.5, 0.1 / res))
                cover = cover * (clumps < np.quantile(clumps, 0.5))
                cover = ndimage.gaussian_filter(cover.astype(np.float32), 0.5)
            # Spoil scattered on the grass in a ring up to ~40 cm out.
            ring = (sd < 0) & (sd > -0.4)
            spoil = ring & (rng.random(u.shape) < 0.25 * (1 + sd / 0.4))
            cover = np.maximum(cover, spoil.astype(np.float32) * 0.6)
            a = cover[None]
            new = (1 - a) * sub + a * patch
        else:
            stress = np.ones((len(order), 1, 1), dtype=np.float32)
            names = [k for k in ("red", "green", "blue", "nir") if k in img.roles]
            for i, k in enumerate(names):
                stress[i] = {"red": 1.04, "green": 0.95, "blue": 1.0, "nir": 0.90}[k]
            stressed = sub * (1 + (stress - 1) * inside[None])
            # Sod cuts are a few cm wide. At 10 cm pixels that is a third of a
            # pixel at 10% darker, which is about as subtle as it sounds.
            cut_frac = min(1.0, 0.03 / res)
            cut = (np.abs(sd) < res / 2).astype(np.float32) * 0.10 * cut_frac
            new = stressed * (1 - cut[None])
        img.data[order, r0:r1, c0:c1] = new.astype(np.float32)
        x, y = img.grid.transform * (cc + 0.5, rc + 0.5)
        graves.append({"row": rc, "col": cc, "x": x, "y": y, "length": length, "width": width,
                       "angle": math.degrees(angle), "scenario": scenario})
    return graves


def score_detection(cands, graves, res: float, hit_m: float = 1.5) -> dict:
    """Hits, misses and false alarms.

    A candidate hits a grave if its centroid is within ``hit_m`` plus half the
    grave length of the grave centre. ``false_ahead`` is, for each grave
    found, how many false alarms rank above it: the number of wrong spots a
    searcher checks before reaching it. Planting fifteen graves in four
    hectares makes plain ranks look better than a real one-grave case would,
    so ``false_ahead`` is the number that matters.
    """
    hit_rank: dict[int, int] = {}
    false_ranks = []
    for c in cands:
        cx, cy = c.geometry.centroid.x, c.geometry.centroid.y
        matched = [i for i, g in enumerate(graves) if math.hypot(cx - g["x"], cy - g["y"]) <= hit_m + g["length"] / 2]
        if matched:
            for i in matched:
                hit_rank.setdefault(i, c.rank)
        else:
            false_ranks.append(c.rank)
    false_ranks = np.array(false_ranks)
    ahead = sorted(int((false_ranks < r).sum()) for r in hit_rank.values())
    return {"graves": len(graves), "found": len(hit_rank), "ranks": sorted(hit_rank.values()), "false_ahead": ahead,
            "false_alarms": int(len(false_ranks)), "candidates": len(cands)}
