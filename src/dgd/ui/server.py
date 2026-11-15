"""A small local web UI: a world map, a rectangle, a button.

The page runs in your browser, the work runs on this computer, exactly as
the command line would run it. Nothing leaves the machine except the same
imagery downloads the CLI makes.

It listens on 127.0.0.1 only and wants a per-session token on every API
call and a same-site cookie on every result file. That sounds fussy for a
local tool, but without it any web page you happen to visit could tell
your laptop to start downloading half of Europe. Local servers get abused
exactly because people assume nobody would bother.
"""

from __future__ import annotations

import http.server
import json
import logging
import math
import mimetypes
import os
import queue
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dgd import __version__
from dgd.progress import parse_progress

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent
STATIC_FILES = {"/static/app.js": ("app.js", "text/javascript; charset=utf-8"), "/static/app.css": ("app.css", "text/css; charset=utf-8")}

# Largest area per tool, square kilometres. Sentinel-2 scans cost per pass,
# not per pixel, up to about 10 km across. High resolution costs per pixel:
# 0.4 km2 at 8 cm is already 60 million pixels of somebody's afternoon.
LIMITS_KM2 = {"trash": 400.0, "burial_wide": 400.0, "graves_file": 2.0}
# OpenAerialMap footprints are asked for one map tile of this many degrees
# at a time. The page only asks once zoomed in far enough to need them.
OAM_TILE_DEG = 1.0
OAM_CACHE_S = 3600
WAYBACK_CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"


def area_km2(bbox) -> float:
    min_lon, min_lat, max_lon, max_lat = bbox
    mid = math.radians((min_lat + max_lat) / 2)
    return abs(max_lon - min_lon) * 111.32 * math.cos(mid) * abs(max_lat - min_lat) * 110.57


@dataclass
class Job:
    id: str
    kind: str  # trash, burial_wide, graves
    name: str
    bbox: list[float]
    params: dict
    output_dir: str
    status: str = "queued"
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    started: str | None = None
    finished: str | None = None
    steps: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    error: str | None = None
    summary: dict = field(default_factory=dict)
    hidden: bool = False
    case_id: str | None = None
    progress: dict = field(default_factory=dict)
    command: str = ""

    FINISHED = ("done", "failed", "stopped", "cancelled")

    @property
    def finished_state(self) -> bool:
        return self.status in self.FINISHED

    def public(self, full: bool = False) -> dict:
        d = asdict(self)
        d.pop("output_dir")
        d["log"] = self.log[-400:] if full else self.log[-1:]
        return d

    def save(self) -> None:
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "job.json").write_text(json.dumps(asdict(self), indent=1))


def build_argv(job: Job, cache_dir: str, settings: dict | None = None) -> tuple[str, list[str]]:
    """Translate a job into the exact command line it corresponds to. The UI runs the CLI, nothing clever."""
    p = job.params
    settings = settings or {}
    # --name=VALUE in one piece, so a name like '-north' is not read as a flag.
    common = [f"--name={job.name}", "--output-dir", job.output_dir, "--cache-dir", cache_dir, "--progress-json"]
    # The drawn box goes in as a polygon so results stay inside it. A bare
    # bbox would let the grid snap outward and report leads past the edge.
    aoi = Path(job.output_dir) / "search_area.geojson"
    aoi.parent.mkdir(parents=True, exist_ok=True)
    w, s, e, n = job.bbox
    aoi.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                   "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}]}))
    if job.kind == "trash":
        argv = ["scan", "--aoi", str(aoi), "--preset", "dumping", "--start", p["start"], "--end", p["end"],
                "--baseline-years", str(p.get("baseline_years", 3))]
        # Only thresholds changed in Settings go on the command line, so it
        # stays short and the preset keeps the say over everything else.
        for key, flag in (("min_score", "--min-score"), ("min_area_m2", "--min-area-m2"), ("min_event_days", "--min-event-days")):
            value = (settings.get("dumping") or {}).get(key)
            if value is not None and value != SHIPPED_DUMPING[key]:
                argv += [flag, f"{value:g}"]
        return "scan", argv + common
    if job.kind == "burial_wide":
        argv = ["scan", "--aoi", str(aoi), "--preset", "burial", "--since", p["since"]]
        if p.get("end"):
            argv += ["--end", p["end"]]
        return "scan", argv + common
    if job.kind == "graves":
        argv = ["graves", "--aoi", str(aoi)]
        src = p["source"]
        if src != "file":
            clat, clon = (s + n) / 2, (w + e) / 2
            half = max(abs(e - w) * 111320 * math.cos(math.radians(clat)), abs(n - s) * 110570) / 2
            argv += ["--fetch", src, "--lat", f"{clat:.7f}", "--lon", f"{clon:.7f}", "--radius-m", f"{half + 10:.0f}",
                     "--after-year", str(p["after_year"])]
            if p.get("before_year"):
                argv += ["--before-year", str(p["before_year"])]
        else:
            argv += ["--after", p["after_path"]]
            if p.get("before_path"):
                argv += ["--before", p["before_path"]]
            if p.get("bands"):
                argv += ["--bands", p["bands"]]
        if p.get("osm_file"):
            argv += ["--osm-file", p["osm_file"]]
        if p.get("mass"):
            argv += ["--mass"]
        if settings.get("overpass_url"):
            argv += ["--overpass-url", settings["overpass_url"]]
        return "graves", argv + common
    raise ValueError(f"unknown job kind {job.kind}")


def summarize(job: Job) -> dict:
    out = Path(job.output_dir)
    summary = {"report": "report.html" if (out / "report.html").exists() else None,
               "geojson": "candidates.geojson" if (out / "candidates.geojson").exists() else None}
    try:
        fc = json.loads((out / "candidates.geojson").read_text())
        feats = fc.get("features", [])
        summary["candidates"] = len(feats)
        kinds: dict[str, int] = {}
        for f in feats:
            k = f["properties"].get("kind", "grave")
            kinds[k] = kinds.get(k, 0) + 1
        summary["by_kind"] = kinds
    except Exception:
        summary["candidates"] = 0
    return summary


class JobStopped(Exception):
    pass


class Runner(threading.Thread):
    """One job at a time, each in its own process.

    Two scans in parallel would just fight over the same bandwidth and CPU.
    A separate process can be stopped cleanly at any moment, which a thread
    cannot, and a job that crashes or leaks memory takes nothing else down
    with it. Downloads already made stay in the cache, so a stopped scan
    restarts cheaply.
    """

    def __init__(self, cache_dir: str, execute=None):
        super().__init__(daemon=True, name="dgd-runner")
        self.queue: queue.Queue[Job] = queue.Queue()
        self.cache_dir = cache_dir
        self.execute = execute or self._execute
        self.current: tuple[Job, subprocess.Popen] | None = None
        self.lock = threading.Lock()
        self.settings: dict = {}

    def command_for(self, job: Job) -> list[str]:
        _, argv = build_argv(job, self.cache_dir, self.settings)
        job.command = "dgd " + shlex.join(argv)
        job.log.append(job.command)
        return [sys.executable, "-m", "dgd", *argv]

    def _execute(self, job: Job) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        posix = os.name == "posix"
        cmd = self.command_for(job)
        # Start and register under the lock, so a Stop either lands before
        # this (and the process never starts) or finds the process to signal.
        with self.lock:
            if job.status == "stopping":
                raise JobStopped()
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    bufsize=1, env=env, start_new_session=posix)
            self.current = (job, proc)
        try:
            for line in proc.stdout:
                prog = parse_progress(line)
                if prog is not None:
                    label = str(prog.get("label") or "")
                    if label and (not job.steps or job.steps[-1] != label):
                        job.steps.append(label)
                    job.progress = prog
                    continue
                line = _LOG_PREFIX.sub("", line.rstrip())
                if line:
                    job.log.append(line)
                    del job.log[:-500]
            rc = proc.wait()
        finally:
            with self.lock:
                self.current = None
        if job.status == "stopping":
            raise JobStopped()
        if rc != 0:
            errors = [x for x in job.log if x.startswith(("ERROR", "error:", "Traceback")) or "Error" in x]
            raise RuntimeError(errors[-1] if errors else (job.log[-1] if job.log else f"exit code {rc}"))

    def stop(self, job: Job) -> bool:
        """Stop the job if it is the one running. Returns True if it was signalled or will not start."""
        with self.lock:
            if self.current is None or self.current[0] is not job:
                if job.status == "running":
                    # Marked running but its process is not up yet. Flag it,
                    # and run() or _execute will stop it before it starts.
                    job.status = "stopping"
                    return True
                return False
            proc = self.current[1]
            job.status = "stopping"
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except ProcessLookupError:
            return True

        def reaper():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        threading.Thread(target=reaper, daemon=True).start()
        return True

    def run(self):
        while True:
            job = self.queue.get()
            if job.status != "queued":  # cancelled while waiting its turn
                continue
            job.status, job.started = "running", datetime.now(timezone.utc).isoformat(timespec="seconds")
            job.save()
            try:
                if job.status == "stopping":  # stopped in the instant after it was marked running
                    raise JobStopped()
                self.execute(job)
                # Results first, then the announcement. Anyone polling between
                # the two would see a finished job with nothing in it.
                job.summary = summarize(job)
                job.status = "done"
            except JobStopped:
                job.status, job.error = "stopped", "stopped by the user"
            except SystemExit as exc:  # input checks inside an in-process execute
                job.status, job.error = "failed", str(exc) or "invalid input"
            except Exception as exc:
                if job.status == "stopping":
                    job.status, job.error = "stopped", "stopped by the user"
                else:
                    log.warning("Job %s failed: %s", job.id, exc)
                    job.status, job.error = "failed", f"{exc}"
            finally:
                job.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
                job.summary = summarize(job)
                job.save()


_LOG_PREFIX = re.compile(r"^(INFO|DEBUG) dgd[.\w]*: ")


def _date(text: str | None, field_name: str, required: bool = True) -> str | None:
    if not text:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{field_name} must be YYYY-MM-DD")


def validate(payload: dict) -> tuple[str, str, list[float], dict]:
    """Check a job request. Raises ValueError with a message meant for humans."""
    kind = payload.get("kind")
    if kind not in ("trash", "burial_wide", "graves"):
        raise ValueError("kind must be trash, burial_wide or graves")
    try:
        bbox = [float(v) for v in payload.get("bbox", [])]
    except (TypeError, ValueError):
        raise ValueError("bbox must be four numbers")
    if len(bbox) != 4 or not (-180 <= bbox[0] < bbox[2] <= 180 and -85 <= bbox[1] < bbox[3] <= 85):
        raise ValueError("bbox must be min_lon, min_lat, max_lon, max_lat inside the world")
    name = re.sub(r"[^\x20-\x7e]", "", str(payload.get("name") or "").strip())[:120] or "Unnamed area"
    p = payload.get("params") or {}
    size = area_km2(bbox)
    params: dict = {}
    if kind == "trash":
        params["end"] = _date(p.get("end"), "end", False) or date.today().isoformat()
        params["start"] = _date(p.get("start"), "start", False) or (date.fromisoformat(params["end"]) - timedelta(days=182)).isoformat()
        params["baseline_years"] = int(p.get("baseline_years", 3))
        if not 1 <= params["baseline_years"] <= 6:
            raise ValueError("baseline years must be 1 to 6")
        limit = LIMITS_KM2["trash"]
    elif kind == "burial_wide":
        params["since"] = _date(p.get("since"), "missing since")
        params["end"] = _date(p.get("end"), "end", False)
        limit = LIMITS_KM2["burial_wide"]
    else:
        from dgd.highres_sources import LATEST, SOURCES, parse_year

        src = p.get("source")
        if src != "file" and src not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)} or file")
        params["source"] = src
        if src != "file":
            source = SOURCES[src]
            if not source.covers(bbox):
                raise ValueError(f"{source.name} does not cover this box")
            if not p.get("after_year"):
                raise ValueError("choose the year to search")
            params["after_year"] = parse_year(p["after_year"])
            if p.get("before_year"):
                params["before_year"] = parse_year(p["before_year"])
                order = lambda y: 9999 if y == LATEST else int(y)  # noqa: E731
                if order(params["before_year"]) >= order(params["after_year"]):
                    raise ValueError("the before year has to be earlier than the after year")
        else:
            if not p.get("after_path"):
                raise ValueError("give the path of the image to search")
            for key in ("after_path", "before_path"):
                if p.get(key):
                    path = Path(str(p[key])).expanduser()
                    if not path.is_file():
                        raise ValueError(f"no such file on this computer: {path}")
                    params[key] = str(path)
            if p.get("bands"):
                params["bands"] = str(p["bands"])
        if p.get("osm_file"):
            path = Path(str(p["osm_file"])).expanduser()
            if not path.is_file():
                raise ValueError(f"no such OpenStreetMap file: {path}")
            params["osm_file"] = str(path)
        if p.get("mass"):
            params["mass"] = True
        limit = LIMITS_KM2["graves_file"] if src == "file" else SOURCES[src].max_km2
    if size > limit:
        raise ValueError(f"area is {size:.2f} km2, the limit for this search is {limit:g} km2. Draw a smaller box.")
    return kind, name, bbox, params


# Settings ---------------------------------------------------------------------

# The dumping preset as shipped. Settings only reach the command line when they differ.
SHIPPED_DUMPING = {"min_score": 3.0, "min_area_m2": 300.0, "min_event_days": 14}
DEFAULT_SETTINGS = {
    "place_search": True,     # send typed place names to OpenStreetMap Nominatim
    "wayback": True,          # offer Esri Wayback history imagery, view only
    "offline_map": False,     # no tiles, no place search, no history: a blank map
    "overpass_url": "",       # own Overpass server for grave searches
    "sources_off": [],        # high resolution sources to leave out of the lists
    "field_contact": "",      # printed on every field sheet: who to call
    "dumping": dict(SHIPPED_DUMPING),
}


def clean_settings(payload: dict, current: dict | None = None) -> dict:
    """Merge a settings update into the current settings. Raises ValueError for humans."""
    out = json.loads(json.dumps(current or DEFAULT_SETTINGS))
    for key in ("place_search", "wayback", "offline_map"):
        if key in payload:
            out[key] = bool(payload[key])
    if "overpass_url" in payload:
        url = str(payload["overpass_url"] or "").strip()
        if url and not re.fullmatch(r"https?://\S+", url):
            raise ValueError("the Overpass server must be an http or https address")
        out["overpass_url"] = url
    if "field_contact" in payload:
        out["field_contact"] = _text(payload.get("field_contact"), 200)
    if "sources_off" in payload:
        from dgd.highres_sources import SOURCES

        off = [str(k) for k in payload["sources_off"] or []]
        unknown = [k for k in off if k not in SOURCES]
        if unknown:
            raise ValueError(f"unknown imagery source: {', '.join(unknown)}")
        out["sources_off"] = sorted(set(off))
    if "dumping" in payload:
        d = payload["dumping"] or {}
        limits = {"min_score": (0.5, 20.0), "min_area_m2": (50.0, 1_000_000.0), "min_event_days": (1, 365)}
        for key, (lo, hi) in limits.items():
            if key in d:
                try:
                    v = float(d[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key.replace('_', ' ')} must be a number") from None
                if not lo <= v <= hi:
                    raise ValueError(f"{key.replace('_', ' ')} must be between {lo:g} and {hi:g}")
                out["dumping"][key] = int(v) if key == "min_event_days" else v
    return out


# Cases ------------------------------------------------------------------------

CASE_KINDS = ("missing_person", "dumping", "other")
CASE_STATUSES = ("active", "field_visit", "on_hold", "closed")
REVIEW_STATUSES = ("unreviewed", "checked", "field_visit", "needs_imagery", "dismissed")
_ID = re.compile(r"[\w-]{1,64}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, limit: int) -> str:
    """Printable, trimmed, no control characters. Names and notes end up in reports and file names."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]


@dataclass
class Case:
    id: str
    name: str
    kind: str = "other"
    status: str = "active"
    created: str = field(default_factory=_now)
    missing_since: str | None = None
    description: str = ""
    events: list[dict] = field(default_factory=list)

    def event(self, kind: str, text: str, **extra) -> dict:
        e = {"time": _now(), "kind": kind, "text": text, **extra}
        self.events.append(e)
        return e


def clean_case(payload: dict, case: Case | None = None) -> dict:
    """Validated case fields from a create or update request."""
    out: dict = {}
    if case is None or "name" in payload:
        name = _text(payload.get("name"), 120)
        if not name:
            raise ValueError("give the case a name")
        out["name"] = name
    if "kind" in payload or case is None:
        kind = payload.get("kind") or "other"
        if kind not in CASE_KINDS:
            raise ValueError(f"kind must be one of {', '.join(CASE_KINDS)}")
        out["kind"] = kind
    if "status" in payload:
        if payload["status"] not in CASE_STATUSES:
            raise ValueError(f"status must be one of {', '.join(CASE_STATUSES)}")
        out["status"] = payload["status"]
    if "missing_since" in payload:
        out["missing_since"] = _date(payload.get("missing_since"), "missing since", False)
    if "description" in payload:
        out["description"] = _text(payload.get("description"), 2000)
    return out


def read_leads(job: Job) -> list[dict]:
    """The leads of a finished job, from its candidates.geojson, in rank order."""
    path = Path(job.output_dir) / "candidates.geojson"
    try:
        feats = json.loads(path.read_text()).get("features", [])
    except (OSError, ValueError):
        return []
    leads = []
    for f in feats:
        p = f.get("properties") or {}
        if not p.get("id"):
            continue
        leads.append({
            "id": p["id"], "kind": p.get("kind") or "grave", "rank": p.get("rank"),
            "lat": p.get("lat"), "lon": p.get("lon"),
            "score": p.get("score_mean", p.get("evidence")),
            "area_m2": p.get("area_m2"), "length_m": p.get("length_m"), "width_m": p.get("width_m"),
            "first_seen": p.get("first_seen"), "last_clear_look": p.get("last_clear_look"), "landcover": p.get("landcover"),
        })
    return sorted(leads, key=lambda r: (r["rank"] is None, r["rank"] or 0))


def monthly_pass_counts(acqs, clear_below: float = 20.0) -> dict[str, dict[str, int]]:
    """Sentinel-2 passes per month, and how many had granule cloud cover under ``clear_below`` percent."""
    out: dict[str, dict[str, int]] = {}
    for a in acqs:
        c = out.setdefault(a.date.strftime("%Y-%m"), {"all": 0, "clear": 0})
        c["all"] += 1
        if a.cloud_cover < clear_below:
            c["clear"] += 1
    return out


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _du(path: Path) -> tuple[int, int]:
    """Bytes and files under a folder."""
    size = n = 0
    for f in path.rglob("*"):
        if f.is_file():
            size += f.stat().st_size
            n += 1
    return size, n


# Only these are ever deleted by Clear: per-area folders the scans make (named
# by a 16 character grid hash), and the two named caches. Pointing --cache-dir
# at a folder with other things in it must never cost those things.
_AREA_DIR = re.compile(r"[0-9a-f]{16}")
CACHE_GROUPS = {"areas": "Sentinel-2 passes and land cover", "osm": "OpenStreetMap answers", "linz": "New Zealand imagery index"}


def _cache_group(child: Path) -> str | None:
    if not child.is_dir():
        return None
    if child.name in ("osm", "linz"):
        return child.name
    return "areas" if _AREA_DIR.fullmatch(child.name) else None


class App:
    def __init__(self, output_root: Path, cache_dir: str, port: int, execute=None):
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(24)
        self.port = port
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.runner = Runner(cache_dir, execute)
        self._coverage: dict | None = None
        self._oam: dict[tuple, tuple[float, list]] = {}
        self._s2: dict[tuple, tuple[float, dict]] = {}
        self.cases: dict[str, Case] = {}
        self.settings = self._load_settings()
        self.runner.settings = self.settings
        self._load_previous()
        self._load_cases()

    # Settings -------------------------------------------------------------------

    @property
    def settings_path(self) -> Path:
        return self.output_root / "settings.json"

    def _load_settings(self) -> dict:
        try:
            saved = json.loads(self.settings_path.read_text())
            return clean_settings(saved if isinstance(saved, dict) else {})
        except (OSError, ValueError):
            return json.loads(json.dumps(DEFAULT_SETTINGS))

    def update_settings(self, payload: dict) -> dict:
        with self.lock:
            self.settings = clean_settings(payload, self.settings)
            self.runner.settings = self.settings
            self.settings_path.write_text(json.dumps(self.settings, indent=1))
        return self.settings

    def public_settings(self) -> dict:
        return {**self.settings, "cache_dir": str(Path(self.runner.cache_dir).expanduser()), "output_dir": str(self.output_root),
                "shipped_dumping": SHIPPED_DUMPING}

    # Cases ----------------------------------------------------------------------

    @property
    def cases_dir(self) -> Path:
        return self.output_root / "cases"

    def _load_cases(self) -> None:
        for f in sorted(self.cases_dir.glob("*.json")):
            try:
                case = Case(**json.loads(f.read_text()))
                self.cases[case.id] = case
            except Exception:
                log.warning("Could not read case file %s", f)

    def _save_case(self, case: Case) -> None:
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cases_dir / f".{case.id}.json.tmp"
        tmp.write_text(json.dumps(asdict(case), indent=1))
        os.replace(tmp, self.cases_dir / f"{case.id}.json")

    def create_case(self, payload: dict) -> Case:
        fields = clean_case(payload)
        case = Case(id=f"case_{uuid.uuid4().hex[:10]}", **fields)
        case.event("created", f"Case opened: {case.name}")
        with self.lock:
            self.cases[case.id] = case
            self._save_case(case)
        return case

    def update_case(self, cid: str, payload: dict) -> Case:
        case = self.cases.get(cid)
        if case is None:
            raise KeyError(cid)
        fields = clean_case(payload, case)
        with self.lock:
            if "status" in fields and fields["status"] != case.status:
                case.event("status", f"Status changed to {fields['status'].replace('_', ' ')}")
            for k, v in fields.items():
                setattr(case, k, v)
            self._save_case(case)
        return case

    def add_note(self, cid: str, text: str) -> dict:
        case = self.cases.get(cid)
        if case is None:
            raise KeyError(cid)
        text = _text(text, 4000)
        if not text:
            raise ValueError("the note is empty")
        with self.lock:
            e = case.event("note", text)
            self._save_case(case)
        return e

    def case_jobs(self, cid: str) -> list[Job]:
        return sorted((j for j in self.jobs.values() if j.case_id == cid), key=lambda j: j.created)

    def assign_case(self, jid: str, cid: str | None) -> Job:
        job = self.jobs.get(jid)
        if job is None:
            raise KeyError(jid)
        if cid and cid not in self.cases:
            raise ValueError("no such case")
        job.case_id = cid or None
        job.save()
        if cid:
            with self.lock:
                self.cases[cid].event("job", f"Search added to the case: {job.name}", job=job.id)
                self._save_case(self.cases[cid])
        return job

    def _job_stats(self, job: Job) -> dict:
        leads = read_leads(job) if job.status == "done" else []
        review = self.reviews(job.id)
        status = {lead["id"]: (review.get(lead["id"]) or {}).get("status", "unreviewed") for lead in leads}
        return {
            "leads": len(leads),
            "to_review": sum(v == "unreviewed" for v in status.values()),
            "field_visits": sum(v == "field_visit" for v in status.values()),
            "dismissed": sum(v == "dismissed" for v in status.values()),
            "checked": sum(v == "checked" for v in status.values()),
            "by_kind": {k: sum(lead["kind"] == k for lead in leads) for k in sorted({lead["kind"] for lead in leads})},
        }

    def case_summary(self, case: Case) -> dict:
        jobs = self.case_jobs(case.id)
        stats = [self._job_stats(j) for j in jobs]
        times = [e["time"] for e in case.events] + [j.finished or j.created for j in jobs]
        return {
            **{k: v for k, v in asdict(case).items() if k != "events"},
            "searches": len(jobs),
            "running": sum(j.status in ("queued", "running", "stopping") for j in jobs),
            "areas": len({tuple(round(v, 4) for v in j.bbox) for j in jobs}),
            "leads": sum(s["leads"] for s in stats),
            "to_review": sum(s["to_review"] for s in stats),
            "field_visits": sum(s["field_visits"] for s in stats),
            "graves_to_review": sum(s["to_review"] for s, j in zip(stats, jobs, strict=True) if j.kind == "graves"),
            "last_activity": max(times) if times else case.created,
        }

    def case_detail(self, cid: str) -> dict:
        case = self.cases.get(cid)
        if case is None:
            raise KeyError(cid)
        jobs = self.case_jobs(cid)
        leads, events = [], list(case.events)
        for j in jobs:
            review = self.reviews(j.id)
            events.append({"time": j.created, "kind": "job", "text": f"Search queued: {j.name}", "job": j.id})
            if j.finished:
                what = (f"{j.summary.get('candidates', 0)} leads" if j.status == "done" else (j.error or j.status))
                events.append({"time": j.finished, "kind": "job", "text": f"Search {j.status}: {j.name}, {what}", "job": j.id})
            if j.status == "done":
                for lead in read_leads(j):
                    r = review.get(lead["id"]) or {}
                    leads.append({**lead, "job": j.id, "job_name": j.name, "job_kind": j.kind,
                                  "status": r.get("status", "unreviewed"), "reason": r.get("reason", "")})
        stats = self.case_summary(case)
        return {**stats, "events": sorted(events, key=lambda e: e["time"], reverse=True),
                "jobs": [{**j.public(), **self._job_stats(j)} for j in jobs], "lead_list": leads}

    def activity(self, limit: int = 12) -> list[dict]:
        out = []
        for case in self.cases.values():
            out += [{**e, "case": case.id, "case_name": case.name} for e in case.events]
            for j in self.case_jobs(case.id):
                if j.finished:
                    out.append({"time": j.finished, "kind": "job", "case": case.id, "case_name": case.name, "job": j.id,
                                "text": f"Search {j.status}: {j.name}" + (f", {j.summary.get('candidates', 0)} leads" if j.status == "done" else "")})
        return sorted(out, key=lambda e: e["time"], reverse=True)[:limit]

    # Reviews --------------------------------------------------------------------

    def reviews(self, jid: str) -> dict:
        job = self.jobs.get(jid)
        if job is None:
            return {}
        try:
            data = json.loads((Path(job.output_dir) / "review.json").read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def set_review(self, jid: str, payload: dict) -> dict:
        """Record a decision or a note on one lead. Kept in the job folder, beside the results it is about."""
        job = self.jobs.get(jid)
        if job is None:
            raise KeyError(jid)
        lead = str(payload.get("lead") or "")
        if not _ID.fullmatch(lead) or lead not in {x["id"] for x in read_leads(job)}:
            raise ValueError("no such lead in this search")
        status = payload.get("status")
        if status is not None and status not in REVIEW_STATUSES:
            raise ValueError(f"status must be one of {', '.join(REVIEW_STATUSES)}")
        note = _text(payload.get("note"), 4000)
        reason = _text(payload.get("reason"), 300)
        if status is None and not note:
            raise ValueError("nothing to record")
        with self.lock:
            data = self.reviews(jid)
            entry = data.setdefault(lead, {"status": "unreviewed", "notes": []})
            if status is not None:
                entry["status"] = status
                entry["reason"] = reason
            if note:
                entry.setdefault("notes", []).append({"time": _now(), "text": note})
            entry["updated"] = _now()
            path = Path(job.output_dir) / "review.json"
            tmp = path.with_name(".review.json.tmp")
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, path)
            case = self.cases.get(job.case_id or "")
            if case is not None:
                if status is not None:
                    words = {"checked": "marked checked", "field_visit": "planned for a field visit", "needs_imagery": "needs better imagery",
                             "dismissed": "dismissed", "unreviewed": "set back to review"}[status]
                    case.event("review", f"{lead} {words}" + (f": {reason}" if reason else ""), job=jid, lead=lead)
                if note:
                    case.event("note", f"{lead}: {note}", job=jid, lead=lead)
                self._save_case(case)
        return entry

    # Files and hashes -----------------------------------------------------------

    def verify(self, jid: str) -> dict:
        """Recompute the SHA-256 of every output the manifest lists. Changed or missing files are named."""
        job = self.jobs.get(jid)
        if job is None:
            raise KeyError(jid)
        out = Path(job.output_dir)
        try:
            manifest = json.loads((out / "manifest.json").read_text())
        except (OSError, ValueError):
            return {"job": jid, "files": 0, "ok": 0, "changed": [], "missing": [], "error": "no manifest.json"}
        changed, missing, ok = [], [], 0
        for rel, digest in (manifest.get("outputs_sha256") or {}).items():
            f = (out / rel).resolve()
            if not f.is_relative_to(out.resolve()) or not f.is_file():
                missing.append(rel)
            elif _sha256(f) != digest:
                changed.append(rel)
            else:
                ok += 1
        return {"job": jid, "files": ok + len(changed) + len(missing), "ok": ok, "changed": changed, "missing": missing,
                "manifest_sha256": _sha256(out / "manifest.json"), "checked": _now()}

    def export_case(self, cid: str) -> Path:
        """One zip with the case record and every search folder in it. The caller deletes it."""
        case = self.cases.get(cid)
        if case is None:
            raise KeyError(cid)
        fd, name = tempfile.mkstemp(prefix="dgd_case_", suffix=".zip")
        os.close(fd)
        with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("case.json", json.dumps(self.case_detail(cid), indent=1, default=str))
            for job in self.case_jobs(cid):
                base = Path(job.output_dir)
                for f in sorted(base.rglob("*")):
                    if f.is_file() and not f.name.startswith("."):
                        z.write(f, f"searches/{job.id}/{f.relative_to(base)}")
        return Path(name)

    # Cache ----------------------------------------------------------------------

    def cache_usage(self) -> dict:
        root = Path(self.runner.cache_dir).expanduser()
        groups = {k: {"label": v, "bytes": 0, "files": 0, "folders": 0} for k, v in CACHE_GROUPS.items()}
        if root.is_dir():
            for child in root.iterdir():
                g = _cache_group(child)
                if g:
                    size, n = _du(child)
                    groups[g]["bytes"] += size
                    groups[g]["files"] += n
                    groups[g]["folders"] += 1
        probe = root if root.exists() else root.parent
        free = shutil.disk_usage(probe).free if probe.exists() else None
        return {"path": str(root), "groups": groups, "free_bytes": free}

    def clear_cache(self, what: str) -> dict:
        if what not in CACHE_GROUPS:
            raise ValueError(f"what must be one of {', '.join(CACHE_GROUPS)}")
        if any(j.status in ("running", "stopping") for j in self.jobs.values()):
            raise ValueError("a job is running and may be reading the cache. Wait for it to finish.")
        root = Path(self.runner.cache_dir).expanduser()
        removed = 0
        if root.is_dir():
            for child in root.iterdir():
                if _cache_group(child) == what:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        return {"removed_folders": removed, **self.cache_usage()}

    # Sentinel-2 availability ----------------------------------------------------------

    def s2_monthly(self, bbox, years: int = 4) -> dict:
        """Passes per month over the box for the last few years. Asks Earth Search once a day per box."""
        from dgd.aoi import grid_from_bbox
        from dgd.stac import COLLECTION_C1, EarthSearchSource

        key = (*(round(v, 3) for v in bbox), years)
        hit = self._s2.get(key)
        if hit and time.time() - hit[0] < 86400:
            return hit[1]
        today = date.today()
        start = date(today.year - years + 1, 1, 1)
        acqs = EarthSearchSource(cache_dir=None, max_cloud=100, collections=(COLLECTION_C1,)).search(grid_from_bbox(*bbox), start, today)
        data = {"years": list(range(start.year, today.year + 1)), "through": today.isoformat(), "clear_below": 20,
                "months": monthly_pass_counts(acqs)}
        self._s2[key] = (time.time(), data)
        return data

    def _load_previous(self) -> None:
        for f in sorted(self.output_root.glob("*/job.json")):
            try:
                d = json.loads(f.read_text())
                job = Job(**d)
                if job.status in ("queued", "running", "stopping"):
                    job.status, job.error = "stopped", "interrupted: the UI was closed before this finished"
                self.jobs[job.id] = job
            except Exception:
                continue

    def submit(self, payload: dict) -> Job:
        kind, name, bbox, params = validate(payload)
        cid = payload.get("case_id") or None
        if cid is not None and cid not in self.cases:
            raise ValueError("no such case")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        jid = f"{stamp}_{kind}_{uuid.uuid4().hex[:6]}"
        job = Job(id=jid, kind=kind, name=name, bbox=bbox, params=params, output_dir=str(self.output_root / jid), case_id=cid)
        with self.lock:
            self.jobs[jid] = job
        job.save()
        self.runner.queue.put(job)
        return job

    def stop(self, jid: str) -> Job:
        job = self.jobs.get(jid)
        if job is None:
            raise KeyError(jid)
        if job.status == "queued":
            job.status, job.error = "cancelled", "cancelled before it started"
            job.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
            job.save()
        elif job.status in ("running", "stopping"):
            self.runner.stop(job)
        return job

    def remove(self, jid: str) -> Job:
        """Hide a finished job from the list. Its files stay on disk."""
        job = self.jobs.get(jid)
        if job is None:
            raise KeyError(jid)
        if not job.finished_state:
            raise ValueError("stop the job before removing it")
        job.hidden = True
        job.save()
        return job

    def clear_finished(self) -> int:
        n = 0
        with self.lock:
            for job in self.jobs.values():
                if job.finished_state and not job.hidden:
                    job.hidden = True
                    job.save()
                    n += 1
        return n

    def wayback(self) -> list[dict]:
        """Esri World Imagery Wayback releases, newest first. For looking, not for analysis.

        Esri's terms cover viewing these tiles, not running detectors on them,
        so they stay a map layer and never become an input.
        """
        if getattr(self, "_wayback", None) is None:
            try:
                from dgd.highres_sources import _get

                cfg = json.loads(_get(WAYBACK_CONFIG, timeout=30))
                rel = []
                for num, item in cfg.items():
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", item.get("itemTitle", ""))
                    rel.append({"release": int(num), "date": m.group(1) if m else item.get("itemTitle", "")})
                self._wayback = sorted(rel, key=lambda r: r["date"], reverse=True)
            except Exception as exc:
                log.warning("Esri Wayback list unavailable: %s", exc)
                return []
        return self._wayback

    def coverage(self) -> dict:
        """Footprints of the national programs, which cover their whole country or state."""
        if self._coverage is None:
            from dgd.highres_sources import coverage_geojson

            self._coverage = coverage_geojson()
        return self._coverage

    def oam_footprints(self, bbox) -> list[dict]:
        """OpenAerialMap images in one tile, remembered for an hour. Their servers have better things to do."""
        from dgd.highres_sources import SOURCES

        key = tuple(round(v, 4) for v in bbox)
        hit = self._oam.get(key)
        if hit and time.time() - hit[0] < OAM_CACHE_S:
            return hit[1]
        feats = SOURCES["oam"].footprints(bbox)
        self._oam[key] = (time.time(), feats)
        return feats

    def result_path(self, jid: str, rel: str) -> Path | None:
        job = self.jobs.get(jid)
        if job is None:
            return None
        base = Path(job.output_dir).resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            return None
        return target


def make_handler(app: App):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}
    index_html = (STATIC / "index.html").read_text(encoding="utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = f"dgd-ui/{__version__}"

        def log_message(self, fmt, *args):  # keep the terminal for things that matter
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _body(self) -> dict:
            n = min(int(self.headers.get("Content-Length", "0") or 0), 200_000)
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        def _bbox(self, query) -> list[float] | None:
            try:
                bbox = [float(v) for v in parse_qs(query)["bbox"][0].split(",")]
            except (KeyError, ValueError):
                return None
            return bbox if len(bbox) == 4 else None

        def _send_file(self, path: Path, ctype: str, filename: str):
            """Stream a file in chunks. A case export can be bigger than anyone wants in memory twice."""
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1 << 20)

        def _host_ok(self) -> bool:
            # A page on some other site resolving its name to 127.0.0.1 would
            # still send its own Host header. Refusing it closes that door.
            return self.headers.get("Host", "") in allowed_hosts

        def _cookie_ok(self) -> bool:
            cookies = dict(c.strip().split("=", 1) for c in self.headers.get("Cookie", "").split(";") if "=" in c)
            return secrets.compare_digest(cookies.get("dgd_token", ""), app.token)

        def _token_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-DGD-Token", ""), app.token) and self._cookie_ok()

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain")
            url = urlparse(self.path)
            if url.path == "/":
                body = index_html.replace("__DGD_TOKEN__", app.token).replace("__DGD_VERSION__", __version__).encode()
                cookie = f"dgd_token={app.token}; Path=/; HttpOnly; SameSite=Strict"
                return self._send(200, body, "text/html; charset=utf-8", {"Set-Cookie": cookie})
            if url.path in STATIC_FILES:
                name, ctype = STATIC_FILES[url.path]
                return self._send(200, (STATIC / name).read_bytes(), ctype)
            m = re.fullmatch(r"/export/case/([\w-]+)\.zip", url.path)
            if m:
                if not self._cookie_ok():
                    return self._send(403, b"open the UI first", "text/plain")
                case = app.cases.get(m.group(1))
                if case is None:
                    return self._send(404, b"no such case", "text/plain")
                path = app.export_case(case.id)
                try:
                    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", case.name).strip("_") or case.id
                    return self._send_file(path, "application/zip", f"{safe}.zip")
                finally:
                    path.unlink(missing_ok=True)
            if url.path.startswith("/results/"):
                if not self._cookie_ok():
                    return self._send(403, b"open the UI first", "text/plain")
                parts = url.path.split("/", 3)
                if len(parts) < 4:
                    return self._send(404, b"not found", "text/plain")
                target = app.result_path(parts[2], parts[3])
                if target is None:
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".geojson":
                    ctype = "application/geo+json"
                return self._send(200, target.read_bytes(), ctype)
            if not url.path.startswith("/api/"):
                return self._send(404, b"not found", "text/plain")
            if not self._token_ok():
                return self._json(403, {"error": "bad token"})
            if url.path == "/api/info":
                return self._json(200, {"version": __version__, "today": date.today().isoformat(), "limits_km2": LIMITS_KM2,
                                        "settings": app.public_settings()})
            if url.path == "/api/settings":
                return self._json(200, app.public_settings())
            if url.path == "/api/sources_all":
                from dgd.highres_sources import SOURCES

                return self._json(200, [src.describe() for src in SOURCES.values()])
            if url.path == "/api/cache":
                return self._json(200, app.cache_usage())
            if url.path == "/api/cases":
                with app.lock:
                    cases = list(app.cases.values())
                unassigned = sum(1 for j in app.jobs.values() if not j.case_id and not j.hidden)
                return self._json(200, {"cases": sorted((app.case_summary(c) for c in cases), key=lambda c: c["last_activity"], reverse=True),
                                        "activity": app.activity(), "unassigned": unassigned})
            m = re.fullmatch(r"/api/cases/([\w-]+)", url.path)
            if m:
                try:
                    return self._json(200, app.case_detail(m.group(1)))
                except KeyError:
                    return self._json(404, {"error": "no such case"})
            m = re.fullmatch(r"/api/jobs/([\w-]+)/review", url.path)
            if m:
                if m.group(1) not in app.jobs:
                    return self._json(404, {"error": "no such job"})
                return self._json(200, app.reviews(m.group(1)))
            if url.path == "/api/s2_passes":
                bbox = self._bbox(url.query)
                if bbox is None:
                    return self._json(400, {"error": "bbox missing"})
                try:
                    return self._json(200, app.s2_monthly(bbox))
                except Exception as exc:
                    return self._json(200, {"months": {}, "years": [], "error": str(exc)})
            if url.path in ("/api/sources", "/api/source_years"):
                from dgd.highres_sources import SOURCES, sources_for

                q = parse_qs(url.query)
                try:
                    bbox = [float(v) for v in q["bbox"][0].split(",")]
                except (KeyError, ValueError):
                    return self._json(400, {"error": "bbox missing"})
                if url.path == "/api/sources":
                    off = set(app.settings.get("sources_off") or [])
                    return self._json(200, [src.describe() for src in sources_for(bbox) if src.key not in off])
                src = SOURCES.get((q.get("source") or [""])[0])
                if src is None:
                    return self._json(400, {"error": "unknown source"})
                try:
                    return self._json(200, {"years": [a.as_dict() for a in src.available(bbox, Path(app.runner.cache_dir))]})
                except Exception as exc:
                    return self._json(200, {"years": [], "error": str(exc)})
            if url.path == "/api/coverage":
                return self._json(200, app.coverage())
            if url.path == "/api/coverage/oam":
                try:
                    bbox = [float(v) for v in parse_qs(url.query)["bbox"][0].split(",")]
                    w, s, e, n = bbox
                    ok = 0 < e - w <= OAM_TILE_DEG + 1e-6 and 0 < n - s <= OAM_TILE_DEG + 1e-6
                except (KeyError, ValueError):
                    ok = False
                if not ok:
                    return self._json(400, {"error": f"bbox must be west,south,east,north, at most {OAM_TILE_DEG:g} degree across"})
                try:
                    return self._json(200, {"type": "FeatureCollection", "features": app.oam_footprints(bbox)})
                except Exception as exc:
                    return self._json(200, {"type": "FeatureCollection", "features": [], "error": str(exc)})
            if url.path == "/api/wayback":
                return self._json(200, app.wayback())
            if url.path == "/api/jobs":
                with app.lock:
                    jobs = sorted((j for j in app.jobs.values() if not j.hidden), key=lambda j: j.created, reverse=True)
                return self._json(200, [j.public() for j in jobs])
            if url.path.startswith("/api/jobs/"):
                job = app.jobs.get(url.path.rsplit("/", 1)[-1])
                return self._json(200, job.public(full=True)) if job else self._json(404, {"error": "no such job"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain")
            if not self._token_ok():
                return self._json(403, {"error": "bad token"})
            path = urlparse(self.path).path
            if path == "/api/jobs/clear":
                return self._json(200, {"cleared": app.clear_finished()})
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json(400, {"error": str(exc)})
            try:
                if path == "/api/settings":
                    app.update_settings(body)
                    return self._json(200, app.public_settings())
                if path == "/api/cache/clear":
                    return self._json(200, app.clear_cache(str(body.get("what", ""))))
                if path == "/api/cases":
                    return self._json(201, app.case_summary(app.create_case(body)))
                m = re.fullmatch(r"/api/cases/([\w-]+)(/note)?", path)
                if m:
                    if m.group(2):
                        return self._json(201, app.add_note(m.group(1), body.get("text", "")))
                    return self._json(200, app.case_summary(app.update_case(m.group(1), body)))
                m = re.fullmatch(r"/api/jobs/([\w-]+)/(review|verify|case)", path)
                if m:
                    jid, action = m.groups()
                    if action == "review":
                        return self._json(200, app.set_review(jid, body))
                    if action == "verify":
                        return self._json(200, app.verify(jid))
                    return self._json(200, app.assign_case(jid, body.get("case_id")).public())
            except KeyError:
                return self._json(404, {"error": "not found"})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            m = re.fullmatch(r"/api/jobs/([\w-]+)/(stop|remove)", path)
            if m:
                try:
                    job = app.stop(m.group(1)) if m.group(2) == "stop" else app.remove(m.group(1))
                except KeyError:
                    return self._json(404, {"error": "no such job"})
                except ValueError as exc:
                    return self._json(409, {"error": str(exc)})
                return self._json(200, job.public())
            if path != "/api/jobs":
                return self._json(404, {"error": "not found"})
            try:
                job = app.submit(body)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(201, job.public())

    return Handler


class LocalServer(http.server.ThreadingHTTPServer):
    """The standard threaded server, minus the panic when a browser hangs up.

    Reload the page while an answer is on its way and the browser drops the
    connection. Python then prints a full traceback for a broken pipe, which
    looks like a crash and is not one. Real errors still get the full report.
    """

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):  # broken pipe, reset, aborted
            log.debug("%s hung up before the answer was sent", client_address[0])
            return
        super().handle_error(request, client_address)


def serve(output_root: Path, cache_dir: str, port: int = 8765, open_browser: bool = True) -> None:
    import webbrowser

    app = App(output_root, cache_dir, port)
    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = LocalServer(("127.0.0.1", candidate), None)
            break
        except OSError:
            continue  # somebody else got there first, try the next door down
    if httpd is None:
        raise SystemExit(f"Ports {port} to {port + 19} are all busy. Pick another with --port.")
    if candidate != port:
        print(f"Port {port} is busy, using {candidate}.")
    app.port = candidate
    httpd.RequestHandlerClass = make_handler(app)
    port = candidate
    app.runner.start()
    url = f"http://127.0.0.1:{port}/"
    print(f"Disturbed Ground Detector UI at {url}  (Ctrl+C to stop). Results in {app.output_root}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Jobs still queued were not run.")
    finally:
        httpd.server_close()


__all__ = ["App", "Job", "LIMITS_KM2", "LocalServer", "OAM_TILE_DEG", "area_km2", "build_argv", "make_handler", "serve", "validate"]
