import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "next_version.py"
spec = importlib.util.spec_from_file_location("next_version", SCRIPT)
nv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nv)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty git repository to make commits and tags in, as the current directory."""
    monkeypatch.chdir(tmp_path)
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}.items():
        monkeypatch.setenv(k, v)
    subprocess.run(["git", "init", "-q", "-b", "main"], check=True)

    def commit(message):
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", message], check=True)

    def tag(name):
        subprocess.run(["git", "tag", name], check=True)

    return commit, tag


@pytest.mark.parametrize("message, expected", [
    ("feat: draw boxes", "minor"),
    ("fix: tiles download", "patch"),
    ("perf(scan): faster", "patch"),
    ("feat(ui)!: new case format", "major"),
    ("fix: reader\n\nBREAKING CHANGE: old caches are ignored", "major"),
    ("chore: first release", None),
    ("ci: artifact link", None),
    ("docs: typo", None),
    ("Merge pull request #2 from someone/ui", None),
    ("Initial commit", None),
])
def test_level_of_a_commit(message, expected):
    assert nv.level(message) == expected


def test_biggest_change_since_the_last_release_wins(repo):
    commit, tag = repo
    commit("feat: first")
    tag("v1.0.0")
    commit("fix: a bug")
    assert nv.next_release()[0] == "1.0.1"
    commit("feat: a thing")
    commit("fix: another bug")
    assert nv.next_release()[0] == "1.1.0"
    commit("feat!: everything changed")
    assert nv.next_release()[0] == "2.0.0"


def test_housekeeping_alone_is_no_release_unless_forced(repo):
    commit, tag = repo
    commit("feat: first")
    tag("v1.2.3")
    assert nv.next_release() == (None, "v1.2.3", [])  # nothing since the tag
    commit("chore: tidy")
    commit("ci: faster")
    assert nv.next_release()[0] is None
    assert nv.next_release("patch")[0] == "1.2.4"
    assert nv.next_release("major")[0] == "2.0.0"


def test_malformed_tags_are_not_releases(repo):
    commit, tag = repo
    commit("feat: first")
    tag("v1.0.0")
    commit("ci: artifact link")
    tag("v.1.0.1")  # the slip that made this script ignore anything but vX.Y.Z
    tag("nightly")
    commit("fix: something")
    assert nv.last_release() == ("v1.0.0", (1, 0, 0))
    assert nv.next_release()[0] == "1.0.1"


def test_highest_tag_wins_not_the_latest_made(repo):
    commit, tag = repo
    commit("feat: a")
    tag("v1.9.0")
    tag("v1.10.0")
    assert nv.last_release() == ("v1.10.0", (1, 10, 0))


def test_first_release_starts_from_nothing(repo):
    commit, _ = repo
    commit("Initial commit")
    commit("feat: it works")
    assert nv.next_release()[0] == "0.1.0"


def test_current_version_matches_setuptools_scm_scheme(repo):
    commit, tag = repo
    commit("feat: first")
    tag("v1.0.0")
    assert nv.current_version() == "1.0.0"
    commit("fix: second")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert nv.current_version() == f"1.0.1.dev1+g{sha}"


def test_release_notes_group_the_changes(repo, tmp_path, capsys):
    commit, tag = repo
    commit("feat: first")
    tag("v1.0.0")
    commit("fix(ui): stop button")
    commit("chore: tidy")
    commit("feat: windows installer")
    notes = tmp_path / "notes.md"
    assert nv.main(["--notes", str(notes), "--repo", "owner/name"]) == 0
    assert capsys.readouterr().out.strip() == "1.1.0"
    text = notes.read_text()
    assert "### Features\n\n- windows installer" in text and "### Fixes\n\n- stop button" in text
    assert "tidy" not in text and "DGD-Setup-1.1.0.exe" in text
    assert "https://github.com/owner/name/compare/v1.0.0...v1.1.0" in text
