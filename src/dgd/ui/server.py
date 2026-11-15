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
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dgd import __version__

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent

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

    FINISHED = ("done", "failed", "stopped", "cancelled")

    @property
    def finished_state(self) -> bool:
        return self.status in self.FINISHED

    def public(self, full: bool = False) -> dict:
        d = asdict(self)
        d.pop("output_dir")
        d["log"] = self.log[-80:] if full else self.log[-1:]
        return d

    def save(self) -> None:
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "job.json").write_text(json.dumps(asdict(self), indent=1))


def build_argv(job: Job, cache_dir: str) -> tuple[str, list[str]]:
    """Translate a job into the exact command line it corresponds to. The UI runs the CLI, nothing clever."""
    p = job.params
    # --name=VALUE in one piece, so a name like '-north' is not read as a flag.
    common = [f"--name={job.name}", "--output-dir", job.output_dir, "--cache-dir", cache_dir, "--no-progress"]
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

    def command_for(self, job: Job) -> list[str]:
        _, argv = build_argv(job, self.cache_dir)
        job.log.append("dgd " + " ".join(argv))
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
        limit = LIMITS_KM2["graves_file"] if src == "file" else SOURCES[src].max_km2
    if size > limit:
        raise ValueError(f"area is {size:.2f} km2, the limit for this search is {limit:g} km2. Draw a smaller box.")
    return kind, name, bbox, params


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
        self._load_previous()

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
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        jid = f"{stamp}_{kind}_{uuid.uuid4().hex[:6]}"
        job = Job(id=jid, kind=kind, name=name, bbox=bbox, params=params, output_dir=str(self.output_root / jid))
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
            self._send(code, json.dumps(obj).encode(), "application/json")

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
                return self._json(200, {"version": __version__, "today": date.today().isoformat(), "limits_km2": LIMITS_KM2})
            if url.path in ("/api/sources", "/api/source_years"):
                from dgd.highres_sources import SOURCES, sources_for

                q = parse_qs(url.query)
                try:
                    bbox = [float(v) for v in q["bbox"][0].split(",")]
                except (KeyError, ValueError):
                    return self._json(400, {"error": "bbox missing"})
                if url.path == "/api/sources":
                    return self._json(200, [src.describe() for src in sources_for(bbox)])
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
                n = min(int(self.headers.get("Content-Length", "0")), 100_000)
                payload = json.loads(self.rfile.read(n) or b"{}")
                job = app.submit(payload)
            except (ValueError, json.JSONDecodeError) as exc:
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
