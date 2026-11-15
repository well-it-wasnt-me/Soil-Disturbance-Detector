import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from dgd import cli
from dgd.ui.server import App, LocalServer, area_km2, build_argv, make_handler, validate

KIDLINGTON = [-1.28, 51.81, -1.25, 51.83]
AMSTERDAM_SMALL = [4.9380, 52.3430, 4.9420, 52.3450]


def test_validate_accepts_and_fills_defaults():
    kind, name, bbox, params = validate({"kind": "trash", "bbox": KIDLINGTON, "name": "Kidlington é test"})
    assert kind == "trash" and name == "Kidlington  test" and params["baseline_years"] == 3
    assert params["start"] < params["end"]
    kind, _, _, params = validate({"kind": "burial_wide", "bbox": KIDLINGTON, "params": {"since": "2025-05-01"}})
    assert params["since"] == "2025-05-01" and params["end"] is None


@pytest.mark.parametrize("payload, message", [
    ({"kind": "nope", "bbox": KIDLINGTON}, "kind"),
    ({"kind": "trash", "bbox": [1, 2, 3]}, "bbox"),
    ({"kind": "trash", "bbox": [10, 50, 5, 51]}, "bbox"),
    ({"kind": "trash", "bbox": [0, 40, 5, 45]}, "limit"),
    ({"kind": "burial_wide", "bbox": KIDLINGTON, "params": {}}, "missing since"),
    ({"kind": "burial_wide", "bbox": KIDLINGTON, "params": {"since": "yesterday"}}, "YYYY-MM-DD"),
    ({"kind": "graves", "bbox": KIDLINGTON, "params": {"source": "pdok", "after_year": 2026}}, "does not cover"),
    ({"kind": "graves", "bbox": AMSTERDAM_SMALL, "params": {"source": "nope", "after_year": 2026}}, "source must be"),
    ({"kind": "graves", "bbox": AMSTERDAM_SMALL, "params": {"source": "pdok", "after_year": "someday"}}, "YYYY or 'latest'"),
    ({"kind": "graves", "bbox": AMSTERDAM_SMALL, "params": {"source": "pdok", "after_year": 2025, "before_year": "latest"}}, "earlier"),
    ({"kind": "graves", "bbox": AMSTERDAM_SMALL, "params": {"source": "pdok"}}, "year"),
    ({"kind": "graves", "bbox": AMSTERDAM_SMALL, "params": {"source": "pdok", "after_year": 2025, "before_year": 2026}}, "earlier"),
    ({"kind": "graves", "bbox": AMSTERDAM_SMALL, "params": {"source": "file", "after_path": "/no/such/file.tif"}}, "no such file"),
    ({"kind": "graves", "bbox": [4.90, 52.30, 4.99, 52.39], "params": {"source": "pdok", "after_year": 2026}}, "limit"),
])
def test_validate_rejects_bad_requests(payload, message):
    with pytest.raises(ValueError, match=message):
        validate(payload)


def test_area_is_roughly_right():
    assert area_km2([0, 0, 1, 1]) == pytest.approx(111.32 * 110.57, rel=0.01)


def test_argv_is_a_valid_cli_call(tmp_path):
    from dgd.ui.server import Job

    for kind, params in [
        ("trash", {"start": "2025-04-01", "end": "2025-09-30", "baseline_years": 3}),
        ("burial_wide", {"since": "2025-05-01", "end": None}),
        ("graves", {"source": "pdok", "after_year": "latest", "before_year": 2025}),
    ]:
        job = Job(id="x", kind=kind, name="Test", bbox=AMSTERDAM_SMALL, params=params, output_dir=str(tmp_path / kind))
        cmd, argv = build_argv(job, str(tmp_path / "cache"))
        args = cli.build_parser().parse_args(argv)
        assert args.command == cmd and args.progress_json and args.output_dir == str(tmp_path / kind)
    assert (tmp_path / "graves" / "search_area.geojson").exists()
    assert args.fetch == "pdok" and args.after_year == "latest" and args.before_year == "2025" and args.radius_m > 100


def _fake_execute(job):
    out = Path(job.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text("<html>report</html>")
    feats = [{"type": "Feature", "properties": {"id": "P001", "kind": "persistent"}, "geometry": {"type": "Point", "coordinates": [0, 0]}}]
    (out / "candidates.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    job.log.append("fake scan ran")


@pytest.fixture
def server(tmp_path):
    app = App(tmp_path / "ui", str(tmp_path / "cache"), port=0, execute=_fake_execute)
    httpd = LocalServer(("127.0.0.1", 0), None)
    app.port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app)
    app.runner.start()
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield app, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = conn.getresponse()
    data = r.read()
    return r.status, dict(r.getheaders()), data


def test_page_sets_token_and_cookie(server):
    app, port = server
    status, headers, body = _req(port, "GET", "/")
    assert status == 200 and app.token.encode() in body
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    body.decode("ascii")  # the page is ASCII only


def test_api_needs_token_cookie_and_local_host(server):
    app, port = server
    good = {"X-DGD-Token": app.token, "Cookie": f"dgd_token={app.token}"}
    assert _req(port, "GET", "/api/info")[0] == 403
    assert _req(port, "GET", "/api/info", headers={"X-DGD-Token": app.token})[0] == 403  # header alone is not enough
    assert _req(port, "GET", "/api/info", headers={"X-DGD-Token": "wrong", "Cookie": f"dgd_token={app.token}"})[0] == 403
    assert _req(port, "GET", "/api/info", headers=good)[0] == 200
    assert _req(port, "GET", "/api/info", headers={**good, "Host": "evil.example:80"})[0] == 403
    assert _req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": KIDLINGTON}, headers={"Cookie": f"dgd_token={app.token}"})[0] == 403


def test_job_lifecycle_results_and_traversal(server, tmp_path):
    app, port = server
    good = {"X-DGD-Token": app.token, "Cookie": f"dgd_token={app.token}", "Content-Type": "application/json"}
    status, _, body = _req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": KIDLINGTON, "name": "Test"}, headers=good)
    assert status == 201
    jid = json.loads(body)["id"]
    for _ in range(50):
        job = json.loads(_req(port, "GET", f"/api/jobs/{jid}", headers=good)[2])
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job["status"] == "done" and job["summary"]["candidates"] == 1 and job["summary"]["by_kind"] == {"persistent": 1}
    assert "fake scan ran" in job["log"]
    status, headers, data = _req(port, "GET", f"/results/{jid}/report.html", headers={"Cookie": f"dgd_token={app.token}"})
    assert status == 200 and b"report" in data and headers["Content-Type"].startswith("text/html")
    assert _req(port, "GET", f"/results/{jid}/report.html")[0] == 403  # no cookie, no results
    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")
    for evil in (f"/results/{jid}/../../secret.txt", f"/results/{jid}/..%2F..%2Fsecret.txt", "/results/nope/report.html"):
        assert _req(port, "GET", evil, headers={"Cookie": f"dgd_token={app.token}"})[0] == 404
    status, _, body = _req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": [0, 40, 5, 45]}, headers=good)
    assert status == 400 and b"limit" in body
    # A fresh UI over the same folder remembers the finished job.
    again = App(Path(app.output_root), "", port=0, execute=_fake_execute)
    assert again.jobs[jid].status == "done"


def test_failed_job_reports_the_reason(tmp_path):
    def boom(job):
        raise RuntimeError("Clouds won.")

    app = App(tmp_path / "ui", "", port=0, execute=boom)
    app.runner.start()
    job = app.submit({"kind": "trash", "bbox": KIDLINGTON})
    for _ in range(50):
        if job.status == "failed":
            break
        time.sleep(0.05)
    assert job.status == "failed" and "Clouds won" in job.error


def _wait(cond, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_cancel_queued_and_stop_running_subprocess(tmp_path):
    import sys

    app = App(tmp_path / "ui", "", port=0)
    # A stand-in for a long scan: a real child process that sleeps.
    app.runner.command_for = lambda job: [sys.executable, "-c", "import time; print('scanning', flush=True); time.sleep(60)"]
    app.runner.start()
    first = app.submit({"kind": "trash", "bbox": KIDLINGTON, "name": "long one"})
    second = app.submit({"kind": "trash", "bbox": KIDLINGTON, "name": "waiting"})
    assert _wait(lambda: first.status == "running" and "scanning" in first.log)
    assert app.stop(second.id).status == "cancelled"
    with pytest.raises(ValueError, match="stop the job"):
        app.remove(first.id)
    t0 = time.time()
    app.stop(first.id)
    assert _wait(lambda: first.status == "stopped", timeout=10), first.status
    assert time.time() - t0 < 10 and first.error == "stopped by the user"
    time.sleep(0.3)
    assert second.status == "cancelled" and second.started is None  # never ran


def test_remove_and_clear_hide_jobs_but_keep_files(server):
    app, port = server
    good = {"X-DGD-Token": app.token, "Cookie": f"dgd_token={app.token}", "Content-Type": "application/json"}
    ids = []
    for i in range(3):
        _, _, body = _req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": KIDLINGTON, "name": f"job {i}"}, headers=good)
        ids.append(json.loads(body)["id"])
    assert _wait(lambda: all(app.jobs[i].status == "done" for i in ids))
    assert _req(port, "POST", f"/api/jobs/{ids[0]}/remove", body={}, headers=good)[0] == 200
    listed = json.loads(_req(port, "GET", "/api/jobs", headers=good)[2])
    assert ids[0] not in [j["id"] for j in listed] and len(listed) == 2
    status, _, body = _req(port, "POST", "/api/jobs/clear", body={}, headers=good)
    assert status == 200 and json.loads(body)["cleared"] == 2
    assert json.loads(_req(port, "GET", "/api/jobs", headers=good)[2]) == []
    assert all((Path(app.jobs[i].output_dir) / "report.html").exists() for i in ids)  # files untouched
    assert _req(port, "POST", "/api/jobs/nope/stop", body={}, headers=good)[0] == 404
    assert not App(Path(app.output_root), "", port=0).jobs[ids[1]].public()["status"] == "running"
    assert App(Path(app.output_root), "", port=0).jobs[ids[1]].hidden  # stays hidden after a restart


def test_coverage_endpoints(server, monkeypatch):
    from dgd.highres_sources import SOURCES

    app, port = server
    good = {"X-DGD-Token": app.token, "Cookie": f"dgd_token={app.token}"}
    assert _req(port, "GET", "/api/coverage")[0] == 403
    status, _, body = _req(port, "GET", "/api/coverage", headers=good)
    fc = json.loads(body)
    assert status == 200 and {"naip", "pdok", "linz-nz"} <= {f["properties"]["key"] for f in fc["features"]}

    calls = []
    feature = {"type": "Feature", "properties": {"id": "a"}, "geometry": {"type": "Polygon", "coordinates": [[[4, 52], [4.1, 52], [4.1, 52.1], [4, 52]]]}}

    def flaky(bbox):
        calls.append(bbox)
        if len(calls) == 1:
            raise RuntimeError("OpenAerialMap is having a day")
        return [feature]

    monkeypatch.setattr(SOURCES["oam"], "footprints", flaky)
    status, _, body = _req(port, "GET", "/api/coverage/oam?bbox=4,52,5,53", headers=good)
    assert status == 200 and json.loads(body) == {"type": "FeatureCollection", "features": [], "error": "OpenAerialMap is having a day"}
    for _ in range(2):  # a failure is not remembered, a success is
        status, _, body = _req(port, "GET", "/api/coverage/oam?bbox=4,52,5,53", headers=good)
        assert status == 200 and json.loads(body)["features"] == [feature]
    assert len(calls) == 2
    for bad in ("0,40,5,45", "1,2,3", "5,52,4,53", "a,b,c,d"):  # a whole region at once, or nonsense
        assert _req(port, "GET", f"/api/coverage/oam?bbox={bad}", headers=good)[0] == 400, bad


def test_server_shrugs_off_browsers_that_hang_up(capsys):
    httpd = LocalServer(("127.0.0.1", 0), None)
    try:
        for exc in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(104, "reset")):
            try:
                raise exc
            except ConnectionError:
                httpd.handle_error(None, ("127.0.0.1", 38578))
        assert capsys.readouterr().err == ""
        try:
            raise ValueError("an actual bug")
        except ValueError:
            httpd.handle_error(None, ("127.0.0.1", 38578))
        assert "an actual bug" in capsys.readouterr().err  # real errors still get the full report
    finally:
        httpd.server_close()


def test_names_that_look_like_flags_stay_names(tmp_path):
    from dgd.ui.server import Job

    job = Job(id="x", kind="trash", name="-north", bbox=AMSTERDAM_SMALL, params={"start": "2025-04-01", "end": "2025-09-30"},
              output_dir=str(tmp_path))
    _, argv = build_argv(job, str(tmp_path / "cache"))
    assert cli.build_parser().parse_args(argv).name == "-north"


def test_stop_in_the_gap_before_the_process_starts_is_not_lost(tmp_path, monkeypatch):
    from dgd.ui import server as srv

    def no_popen(*a, **k):
        raise AssertionError("the stopped job's process was started anyway")

    monkeypatch.setattr(srv.subprocess, "Popen", no_popen)
    runner = srv.Runner("")
    runner.command_for = lambda job: ["true"]
    job = srv.Job(id="x", kind="trash", name="n", bbox=AMSTERDAM_SMALL, params={}, output_dir=str(tmp_path))
    job.status = "running"  # what run() sets just before _execute
    assert runner.stop(job) is True and job.status == "stopping"
    with pytest.raises(srv.JobStopped):
        runner._execute(job)
    assert runner.current is None


def _good(app):
    return {"X-DGD-Token": app.token, "Cookie": f"dgd_token={app.token}", "Content-Type": "application/json"}


def _done(app, port, jid):
    for _ in range(80):
        job = json.loads(_req(port, "GET", f"/api/jobs/{jid}", headers=_good(app))[2])
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_static_assets_need_no_token_and_nothing_else_is_served(server):
    app, port = server
    status, headers, body = _req(port, "GET", "/static/app.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript") and b"function route" in body
    assert _req(port, "GET", "/static/app.css")[0] == 200
    for evil in ("/static/server.py", "/static/../server.py", "/static/index.html"):
        assert _req(port, "GET", evil)[0] == 404


def test_settings_are_validated_saved_and_reach_the_command_line(server, tmp_path):
    from dgd.ui.server import Job

    app, port = server
    status, _, body = _req(port, "POST", "/api/settings", body={"dumping": {"min_score": 4, "min_area_m2": "300"}, "overpass_url": "https://op.example/api",
                                                                  "sources_off": ["oam"], "field_contact": "Duty officer\n123"}, headers=_good(app))
    assert status == 200
    saved = json.loads(body)
    assert saved["dumping"]["min_score"] == 4 and saved["sources_off"] == ["oam"] and saved["field_contact"] == "Duty officer 123"
    assert json.loads((app.output_root / "settings.json").read_text())["overpass_url"] == "https://op.example/api"
    for bad in ({"overpass_url": "javascript:alert(1)"}, {"dumping": {"min_score": "lots"}}, {"dumping": {"min_event_days": 0}}, {"sources_off": ["nope"]}):
        assert _req(port, "POST", "/api/settings", body=bad, headers=_good(app))[0] == 400
    trash = Job(id="t", kind="trash", name="T", bbox=KIDLINGTON, params={"start": "2025-04-01", "end": "2025-09-30"}, output_dir=str(tmp_path / "t"))
    _, argv = build_argv(trash, "", app.settings)
    assert argv[argv.index("--min-score") + 1] == "4" and "--min-area-m2" not in argv  # unchanged values stay off the line
    graves = Job(id="g", kind="graves", name="G", bbox=AMSTERDAM_SMALL, params={"source": "pdok", "after_year": 2025, "mass": True}, output_dir=str(tmp_path / "g"))
    _, argv = build_argv(graves, "", app.settings)
    assert "--mass" in argv and argv[argv.index("--overpass-url") + 1] == "https://op.example/api"
    sources = json.loads(_req(port, "GET", f"/api/sources?bbox={','.join(map(str, AMSTERDAM_SMALL))}", headers=_good(app))[2])
    assert "oam" not in [s["key"] for s in sources] and "pdok" in [s["key"] for s in sources]
    # A restarted UI reads them back.
    assert App(app.output_root, "", port=0).settings["dumping"]["min_score"] == 4


def test_case_lifecycle_review_log_and_export(server):
    import io
    import zipfile

    app, port = server
    g = _good(app)
    status, _, body = _req(port, "POST", "/api/cases", body={"name": "Case 114", "kind": "missing_person", "missing_since": "2025-05-02"}, headers=g)
    assert status == 201
    cid = json.loads(body)["id"]
    assert _req(port, "POST", "/api/cases", body={"name": "  "}, headers=g)[0] == 400
    assert _req(port, "POST", "/api/cases", body={"name": "x", "kind": "spy"}, headers=g)[0] == 400
    assert _req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": KIDLINGTON, "case_id": "case_nope"}, headers=g)[0] == 400
    jid = json.loads(_req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": KIDLINGTON, "name": "Wide", "case_id": cid}, headers=g)[2])["id"]
    assert _done(app, port, jid)["case_id"] == cid

    case = json.loads(_req(port, "GET", f"/api/cases/{cid}", headers=g)[2])
    assert case["searches"] == 1 and case["to_review"] == 1 and case["lead_list"][0]["id"] == "P001"
    assert _req(port, "POST", f"/api/jobs/{jid}/review", body={"lead": "P999", "status": "checked"}, headers=g)[0] == 400
    assert _req(port, "POST", f"/api/jobs/{jid}/review", body={"lead": "P001", "status": "buried"}, headers=g)[0] == 400
    status, _, body = _req(port, "POST", f"/api/jobs/{jid}/review", body={"lead": "P001", "status": "dismissed", "reason": "construction site"}, headers=g)
    assert status == 200 and json.loads(body)["status"] == "dismissed"
    _req(port, "POST", f"/api/jobs/{jid}/review", body={"lead": "P001", "note": "Builder confirmed by phone"}, headers=g)
    review = json.loads(_req(port, "GET", f"/api/jobs/{jid}/review", headers=g)[2])
    assert review["P001"]["reason"] == "construction site" and review["P001"]["notes"][0]["text"] == "Builder confirmed by phone"
    _req(port, "POST", f"/api/cases/{cid}/note", body={"text": "Drone flight booked"}, headers=g)
    _req(port, "POST", f"/api/cases/{cid}", body={"status": "field_visit"}, headers=g)
    case = json.loads(_req(port, "GET", f"/api/cases/{cid}", headers=g)[2])
    texts = [e["text"] for e in case["events"]]
    assert case["to_review"] == 0 and case["status"] == "field_visit"
    assert "P001 dismissed: construction site" in texts and "Drone flight booked" in texts and any("Status changed" in t for t in texts)
    listing = json.loads(_req(port, "GET", "/api/cases", headers=g)[2])
    assert listing["cases"][0]["name"] == "Case 114" and listing["activity"]
    assert _req(port, "GET", "/api/cases/case_nope", headers=g)[0] == 404

    assert _req(port, "GET", f"/export/case/{cid}.zip")[0] == 403  # no cookie, no export
    status, headers, data = _req(port, "GET", f"/export/case/{cid}.zip", headers={"Cookie": f"dgd_token={app.token}"})
    assert status == 200 and headers["Content-Disposition"] == 'attachment; filename="Case_114.zip"'
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "case.json" in names and f"searches/{jid}/report.html" in names and f"searches/{jid}/review.json" in names
    # A restarted UI still knows the case and the review.
    again = App(app.output_root, "", port=0)
    assert again.cases[cid].status == "field_visit" and again.reviews(jid)["P001"]["status"] == "dismissed"


def test_unassigned_search_can_join_a_case(server):
    app, port = server
    g = _good(app)
    cid = json.loads(_req(port, "POST", "/api/cases", body={"name": "Canal"}, headers=g)[2])["id"]
    jid = json.loads(_req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": KIDLINGTON}, headers=g)[2])["id"]
    _done(app, port, jid)
    assert json.loads(_req(port, "GET", "/api/cases", headers=g)[2])["unassigned"] == 1
    status, _, body = _req(port, "POST", f"/api/jobs/{jid}/case", body={"case_id": cid}, headers=g)
    assert status == 200 and json.loads(body)["case_id"] == cid
    assert _req(port, "POST", f"/api/jobs/{jid}/case", body={"case_id": "case_nope"}, headers=g)[0] == 400


def test_verify_names_changed_and_missing_files(server):
    from dgd.ui.server import _sha256

    app, port = server
    g = _good(app)
    jid = json.loads(_req(port, "POST", "/api/jobs", body={"kind": "trash", "bbox": KIDLINGTON}, headers=g)[2])["id"]
    _done(app, port, jid)
    out = Path(app.jobs[jid].output_dir)
    (out / "gone.txt").write_text("x")
    manifest = {"outputs_sha256": {"report.html": _sha256(out / "report.html"), "gone.txt": _sha256(out / "gone.txt"), "../escape.txt": "0"}}
    (out / "manifest.json").write_text(json.dumps(manifest))
    r = json.loads(_req(port, "POST", f"/api/jobs/{jid}/verify", headers=g)[2])
    assert r["ok"] == 2 and r["missing"] == ["../escape.txt"] and not r["changed"]
    (out / "report.html").write_text("<html>tampered</html>")
    (out / "gone.txt").unlink()
    r = json.loads(_req(port, "POST", f"/api/jobs/{jid}/verify", headers=g)[2])
    assert r["changed"] == ["report.html"] and "gone.txt" in r["missing"] and r["ok"] == 0


def test_cache_clear_only_touches_what_dgd_made(tmp_path):
    cache = tmp_path / "cache"
    (cache / "0123456789abcdef").mkdir(parents=True)
    (cache / "0123456789abcdef" / "pass.npy").write_bytes(b"x" * 100)
    (cache / "osm").mkdir()
    (cache / "osm" / "q.json").write_text("{}")
    (cache / "holiday_photos").mkdir()
    (cache / "holiday_photos" / "beach.jpg").write_bytes(b"keep me")
    (cache / "fedcba9876543210.txt").write_text("a file, not a folder")
    app = App(tmp_path / "ui", str(cache), port=0)
    usage = app.cache_usage()
    assert usage["groups"]["areas"]["bytes"] == 100 and usage["groups"]["areas"]["folders"] == 1 and usage["groups"]["osm"]["files"] == 1
    r = app.clear_cache("areas")
    assert r["removed_folders"] == 1 and not (cache / "0123456789abcdef").exists()
    assert (cache / "holiday_photos" / "beach.jpg").exists() and (cache / "osm").exists() and (cache / "fedcba9876543210.txt").exists()
    with pytest.raises(ValueError):
        app.clear_cache("everything")


def test_runner_turns_progress_lines_into_steps_and_keeps_them_out_of_the_log(tmp_path):
    import sys

    from dgd.progress import JsonProgress
    from dgd.ui.server import Job, Runner

    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from dgd.progress import JsonProgress\n"
        "p = JsonProgress(total_steps=3, min_interval=0)\n"
        "p.step('Searching imagery'); print('INFO dgd.stac: Found 4 passes', flush=True)\n"
        "p.step('Fetching 4 passes'); tick, done = p.counter(4, 'passes')\n"
        "[tick() for _ in range(4)]\n"
    ) % str(Path(__file__).resolve().parents[1] / "src")
    runner = Runner("")
    runner.command_for = lambda job: [sys.executable, "-c", script]
    job = Job(id="x", kind="trash", name="n", bbox=KIDLINGTON, params={}, output_dir=str(tmp_path))
    job.status = "running"
    runner._execute(job)
    assert job.steps == ["Searching imagery", "Fetching 4 passes"]
    assert job.progress == {"step": 2, "total": 3, "label": "Fetching 4 passes", "done": 4, "of": 4, "unit": "passes"}
    assert job.log == ["Found 4 passes"] or job.log[-1].endswith("Found 4 passes")
    assert not any(line.startswith(JsonProgress.PREFIX.strip()) for line in job.log)


def test_json_progress_throttles_but_always_reports_the_end():
    import io

    from dgd.progress import JsonProgress, parse_progress

    buf = io.StringIO()
    p = JsonProgress(total_steps=2, stream=buf, min_interval=3600)
    p.step("Fetching")
    tick, _ = p.counter(100, "passes")
    for _ in range(100):
        tick()
    lines = [parse_progress(x) for x in buf.getvalue().splitlines()]
    assert len(lines) == 3  # the step, the first tick, the last tick
    assert lines[-1]["done"] == 100 and lines[-1]["of"] == 100
    assert parse_progress("INFO not progress") is None and parse_progress(JsonProgress.PREFIX + "{broken") is None


def test_monthly_pass_counts():
    from datetime import date
    from types import SimpleNamespace

    from dgd.ui.server import monthly_pass_counts

    acqs = [SimpleNamespace(date=date(2025, 5, d), cloud_cover=c) for d, c in ((1, 5.0), (6, 60.0), (11, 19.9))] + [SimpleNamespace(date=date(2025, 6, 1), cloud_cover=20.0)]
    assert monthly_pass_counts(acqs) == {"2025-05": {"all": 3, "clear": 2}, "2025-06": {"all": 1, "clear": 0}}


def test_baseline_years_reach_back_to_the_start_of_the_archive(tmp_path):
    from datetime import date

    from dgd.ui.server import Job, max_baseline_years

    assert max_baseline_years(date(2025, 4, 1)) == 8      # 2017-04 is still after November 2016
    assert max_baseline_years(date(2026, 11, 15)) == 10   # capped
    assert max_baseline_years(date(2017, 3, 1)) == 0      # nothing earlier exists
    _, _, _, p = validate({"kind": "trash", "bbox": KIDLINGTON, "params": {"start": "2025-04-01", "end": "2025-09-30", "baseline_years": 8}})
    assert p["baseline_years"] == 8
    with pytest.raises(ValueError, match="at most 8 years"):
        validate({"kind": "trash", "bbox": KIDLINGTON, "params": {"start": "2025-04-01", "end": "2025-09-30", "baseline_years": 9}})
    with pytest.raises(ValueError, match="no earlier year"):
        validate({"kind": "trash", "bbox": KIDLINGTON, "params": {"start": "2017-01-01", "end": "2017-06-30", "baseline_years": 1}})
    _, _, _, p = validate({"kind": "burial_wide", "bbox": KIDLINGTON, "params": {"since": "2025-05-02", "baseline_years": 6}})
    assert p["baseline_years"] == 6
    job = Job(id="b", kind="burial_wide", name="B", bbox=KIDLINGTON, params=p, output_dir=str(tmp_path))
    _, argv = build_argv(job, "")
    args = cli.build_parser().parse_args(argv)
    assert args.baseline_years == 6 and args.since.isoformat() == "2025-05-02"


def test_jobs_follow_their_folder_when_the_outputs_move(tmp_path):
    import shutil

    first = App(tmp_path / "a" / "ui", "", port=0, execute=_fake_execute)
    first.runner.start()
    job = first.submit({"kind": "trash", "bbox": KIDLINGTON, "name": "Moved"})
    assert _wait(lambda: job.status == "done")
    # Copy the outputs somewhere else, the way another account or computer would see them,
    # with job.json still naming a home folder that does not exist here.
    moved = tmp_path / "b" / "ui"
    shutil.copytree(first.output_root, moved)
    jf = moved / job.id / "job.json"
    d = json.loads(jf.read_text())
    d["output_dir"] = f"/home/nobody-here/Projects/x/outputs/ui/{job.id}"
    jf.write_text(json.dumps(d))

    app = App(moved, "", port=0)
    assert app.jobs[job.id].output_dir == str((moved / job.id).resolve())
    assert app.clear_finished() == 1  # used to die making /home/nobody-here
    assert json.loads(jf.read_text())["output_dir"] == str((moved / job.id).resolve())
    assert app.result_path(job.id, "report.html") is not None
    assert app.set_review(job.id, {"lead": "P001", "status": "checked"})["status"] == "checked"


def test_an_unexpected_error_becomes_a_500_the_page_can_show(server, monkeypatch):
    app, port = server

    def boom():
        raise PermissionError("Permission denied: '/home/someone'")

    monkeypatch.setattr(app, "clear_finished", boom)
    status, _, body = _req(port, "POST", "/api/jobs/clear", body={}, headers=_good(app))
    assert status == 500 and "Permission denied" in json.loads(body)["error"]
    assert _req(port, "GET", "/api/info", headers=_good(app))[0] == 200  # and the server carries on
