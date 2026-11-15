# Versions and releases

Nobody types a version number. It comes from git tags, and the tags come from the commit messages.

## Where the version comes from

The latest `vX.Y.Z` tag is the version. [setuptools-scm](https://setuptools-scm.readthedocs.io) reads it whenever dgd is built or installed and writes it to `src/dgd/_version.py`, which everything else reads through `dgd.__version__`: `dgd --version`, the map's footer, report footers and manifests, the Windows installer's name, its version info and `DGD.exe`'s.

| checkout | version |
| -------- | ------- |
| exactly at tag `v1.2.0`, no local changes | `1.2.0` |
| 3 commits after `v1.2.0` | `1.2.1.dev3+g1a2b3c4` |
| the same with uncommitted changes | `1.2.1.dev3+g1a2b3c4.d20261115` |
| a GitHub "Source code" zip of a release | its tag, via `.git_archival.txt` |
| no git at all | `0.0.0` |

Only tags shaped exactly `vX.Y.Z` count. `v.1.0.1` or `nightly` are ignored rather than breaking the build.

After pulling a new tag, refresh the installed version with `pip install -e .` (an editable install reads it only when installed). A branch that has not merged `main` since the last release still sees the release before: the tag sits on `main`'s merge commit.

## How a release happens

Merge to `main`. That is all. The **Release** workflow (`.github/workflows/release.yml`):

1. reads the commit messages since the last release and works out the next version (`packaging/next_version.py`):

    | commits since the last release | release | example |
    | ------------------------------ | ------- | ------- |
    | `feat!: ...`, `fix!: ...`, or a line `BREAKING CHANGE: ...` in the body | major | 1.4.2 → 2.0.0 |
    | `feat: ...` | minor | 1.4.2 → 1.5.0 |
    | `fix: ...`, `perf: ...` | patch | 1.4.2 → 1.4.3 |
    | only `chore:`, `ci:`, `docs:`, `test:`, `refactor:`, `style:`, `build:` | none | |

    The biggest one wins. A scope is fine: `fix(ui): ...`.

2. runs the tests, and builds and smoke-tests the Windows installer at that version,
3. only if all of that passed, tags the commit `vX.Y.Z` and publishes the GitHub release, with notes grouped from the `feat` and `fix` messages and `DGD-Setup-X.Y.Z.exe` attached.

Nothing is committed back to the repository. The tag is the whole record.

To release when the commits alone would not (say, only `chore:` work that should still ship), run **Actions > Release > Run workflow** and pick a bump.

To see what the next release would be, before merging:

```bash
python packaging/next_version.py                  # prints e.g. 1.5.0, or explains why there is none
python packaging/next_version.py --notes notes.md # and the release notes it would publish
python packaging/next_version.py --current        # the version a build of this checkout gets
```

Run it on `main` (or a branch with `main` merged in), where the last release tag is.

## By hand

Pushing a `vX.Y.Z` tag yourself still works: the **Windows app** workflow builds that tag and attaches the installer to its release, creating the release if needed. Keep to the `vX.Y.Z` shape, or nothing will see it.
