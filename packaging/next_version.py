"""Versions from git: the next release, from the commit messages since the last one.

    python packaging/next_version.py                  # 1.1.0, or nothing when no release is due
    python packaging/next_version.py --bump minor     # at least a minor release, whatever the commits say
    python packaging/next_version.py --notes notes.md # also write the release notes
    python packaging/next_version.py --current        # the version a build of this checkout gets

Commit messages follow Conventional Commits (``feat: ...``, ``fix: ...``), as
this repository always has. The biggest change since the last vX.Y.Z tag
decides the next version:

    feat!:, fix!: ... or a "BREAKING CHANGE:" line     major   1.4.2 -> 2.0.0
    feat:                                              minor   1.4.2 -> 1.5.0
    fix:, perf:                                        patch   1.4.2 -> 1.4.3
    chore:, ci:, docs:, test:, refactor:, style:, ...  no release on their own

Tags that are not vX.Y.Z (``v.1.0.1``, ``nightly``) are not releases and are
ignored. Needs only git and the standard library, so CI runs it bare.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
HEADER = re.compile(r"^(?P<type>[A-Za-z]+)(?:\([^)]*\))?(?P<bang>!)?:\s*(?P<desc>\S.*)$")
BREAKING = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
RANK = {None: 0, "patch": 1, "minor": 2, "major": 3}
LEVEL_OF_TYPE = {"feat": "minor", "fix": "patch", "perf": "patch"}

Version = tuple[int, int, int]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def last_release() -> tuple[str | None, Version]:
    """The newest vX.Y.Z tag reachable from HEAD, and its version. (None, 0.0.0) before the first."""
    found = [(tuple(map(int, m.groups())), tag) for tag in git("tag", "--merged", "HEAD").split()
             if (m := TAG.match(tag))]
    if not found:
        return None, (0, 0, 0)
    version, tag = max(found)
    return tag, version


def commits_since(tag: str | None) -> list[str]:
    """Full messages of the commits since ``tag``, newest first. Merge commits say nothing of their own."""
    out = git("log", "--no-merges", "--format=%B%x1e", f"{tag}..HEAD" if tag else "HEAD")
    return [m.strip() for m in out.split("\x1e") if m.strip()]


def level(message: str) -> str | None:
    """How big a release one commit calls for: major, minor, patch or None."""
    m = HEADER.match(message.splitlines()[0])
    if not m:
        return None
    if m["bang"] or BREAKING.search(message):
        return "major"
    return LEVEL_OF_TYPE.get(m["type"].lower())


def bump(version: Version, lvl: str) -> Version:
    major, minor, patch = version
    if lvl == "major":
        return major + 1, 0, 0
    if lvl == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def release_notes(messages: list[str], previous: str | None, new: str, repo: str | None = None) -> str:
    sections: dict[str, list[str]] = {"Breaking changes": [], "Features": [], "Fixes": []}
    for msg in messages:
        lvl = level(msg)
        if lvl is None:
            continue
        desc = HEADER.match(msg.splitlines()[0])["desc"].strip()
        sections["Breaking changes" if lvl == "major" else "Features" if lvl == "minor" else "Fixes"].append(desc)
    lines: list[str] = []
    for title, items in sections.items():
        if items:
            lines += [f"### {title}", "", *(f"- {item}" for item in items), ""]
    if not lines:
        lines = ["Maintenance release.", ""]
    lines += ["Windows: download `DGD-Setup-" + new + ".exe` below and double-click it.", ""]
    if repo and previous:
        lines.append(f"**Full changelog:** https://github.com/{repo}/compare/{previous}...v{new}")
    return "\n".join(lines).rstrip() + "\n"


def next_release(force: str = "auto") -> tuple[str | None, str | None, list[str]]:
    """(next version or None, previous tag, the commit messages it is made of)."""
    tag, current = last_release()
    messages = commits_since(tag)
    if not messages:
        return None, tag, messages
    lvl = max((level(m) for m in messages), key=RANK.__getitem__)
    if force != "auto" and RANK[force] > RANK[lvl]:
        lvl = force
    if lvl is None:
        return None, tag, messages
    return ".".join(map(str, bump(current, lvl))), tag, messages


def current_version() -> str:
    """What setuptools-scm makes of this checkout: X.Y.Z at a clean release tag, else a dev version after it."""
    tag, (major, minor, patch) = last_release()
    distance = int(git("rev-list", "--count", f"{tag}..HEAD" if tag else "HEAD"))
    dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    if tag and distance == 0 and not dirty:
        return f"{major}.{minor}.{patch}"
    sha = git("rev-parse", "--short", "HEAD").strip()
    version = f"{major}.{minor}.{patch + 1}.dev{distance}+g{sha}"
    return version + (f".d{date.today():%Y%m%d}" if dirty else "")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--bump", choices=["auto", "patch", "minor", "major"], default="auto",
                   help="Release at least this big even if the commits do not call for it. Default: auto.")
    p.add_argument("--notes", type=Path, help="Write the release notes (Markdown) to this file.")
    p.add_argument("--repo", help="owner/name on GitHub, for a compare link in the notes.")
    p.add_argument("--current", action="store_true", help="Print the version of this checkout instead.")
    args = p.parse_args(argv)
    if args.current:
        print(current_version())
        return 0
    new, previous, messages = next_release(args.bump)
    if new is None:
        why = "no commits" if not messages else "no feat or fix commits"
        print(f"Nothing to release since {previous or 'the first commit'}: {why}.", file=sys.stderr)
        return 0
    if args.notes:
        args.notes.write_text(release_notes(messages, previous, new, args.repo), encoding="utf-8")
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
