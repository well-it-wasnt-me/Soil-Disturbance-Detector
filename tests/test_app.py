import sys

from dgd import app

KIDLINGTON = [-1.28, 51.81, -1.25, 51.83]


def test_arguments_go_straight_to_the_cli(monkeypatch):
    # This is how the UI runs jobs in the packaged app: DGD.exe graves ...
    seen = []
    monkeypatch.setattr("dgd.cli.main", lambda argv: seen.append(argv) or 0)
    assert app.main(["graves", "--help"]) == 0 and seen == [["graves", "--help"]]


def test_double_click_opens_the_ui_with_results_in_documents(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(app, "running_instance", lambda port=app.PORT: None)
    monkeypatch.setattr(app, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr("dgd.cli.main", lambda argv: seen.append(argv) or 0)
    assert app.main([]) == 0
    assert seen == [["ui", "--output-dir", str(tmp_path / "Disturbed Ground Detector")]]


def test_second_double_click_reuses_the_running_ui(monkeypatch):
    opened = []
    monkeypatch.setattr(app, "running_instance", lambda port=app.PORT: "http://127.0.0.1:8765/")
    monkeypatch.setattr(app.webbrowser, "open", opened.append)
    monkeypatch.setattr("dgd.cli.main", lambda argv: (_ for _ in ()).throw(AssertionError("started a second server")))
    assert app.main([]) == 0 and opened == ["http://127.0.0.1:8765/"]


def test_a_failed_start_waits_so_the_message_can_be_read(monkeypatch, tmp_path, capsys):
    def busy(argv):
        raise SystemExit("Ports 8765 to 8784 are all busy. Pick another with --port.")

    paused = []
    monkeypatch.setattr(app, "running_instance", lambda port=app.PORT: None)
    monkeypatch.setattr(app, "documents_dir", lambda: tmp_path)
    monkeypatch.setattr(app, "_pause", lambda: paused.append(True))
    monkeypatch.setattr("dgd.cli.main", busy)
    assert app.main([]) == 1 and paused == [True]
    assert "all busy" in capsys.readouterr().out


def test_nothing_listening_means_no_running_instance():
    import socket

    with socket.socket() as s:  # grab a free port, then let it go
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert app.running_instance(port) is None


def test_packaged_app_runs_jobs_with_itself(monkeypatch, tmp_path):
    from dgd.ui import server as srv

    kind, name, bbox, params = srv.validate({"kind": "trash", "bbox": KIDLINGTON})
    job = srv.Job(id="x", kind=kind, name=name, bbox=bbox, params=params, output_dir=str(tmp_path))
    runner = srv.Runner("")
    assert runner.command_for(job)[:3] == [sys.executable, "-m", "dgd"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = runner.command_for(job)
    assert cmd[0] == sys.executable and cmd[1] == "scan" and "-m" not in cmd[:2]
