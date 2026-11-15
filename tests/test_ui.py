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
        assert args.command == cmd and args.no_progress and args.output_dir == str(tmp_path / kind)
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
