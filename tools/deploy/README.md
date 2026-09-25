# Deploy suite tools

`tests/deploy` holds the deploy cells. Each cell installs crapkit the way a
user does (pip, pipx, uv, a plugin marketplace, a hook route) and drives it
through the surface that user touches. The tools here build the images the
Linux cells run in, install the same toolchain natively on Windows and macOS,
and run any cell, packet or cadence in either place. `pins.toml` is the one
source of every version and hash.

## Run

    python tools/deploy/run.py --packet deploy-kit             # the kit's own tests, in crapkit-deploy:core
    python tools/deploy/run.py --cell lin-pip-start-py311      # one cell (repeat --cell for more)
    python tools/deploy/run.py --packet deploy-channels -n 4   # one packet's cells, 4 xdist workers
    python tools/deploy/run.py --cadence nightly --image full  # every nightly Linux cell the full image holds
    python tools/deploy/run.py --native                        # on Windows or macOS: the same cells, natively
    python tools/deploy/run.py --build-only --image gui        # build one image, or skip it when unchanged
    python tools/deploy/run.py --repeat 2 ...                  # two fresh containers; exit 1 if a verdict differs

A Linux run builds `crapkit-deploy:<image>` (default `core`) or reuses it when
its label says it was built from the same Dockerfile, context files and pins.
It then exports the tree under test (`export.py`: a git bundle plus a tarball
of the files git shows) into `<out>/in`, and runs `tests/deploy` in a fresh
container as uid 1000 with `--network none`. Inside, `entry.sh` builds the
candidate wheel and sdist (`candidate.py`) before pytest starts. The source
tree is never in an image, so a crapkit change rebuilds nothing.

`--native` needs the toolchain first: `python tools/deploy/toolchain.py`
installs the pinned uv, CPythons, Node, PortableGit, pwsh, wheelhouse and npm
caches under `%LOCALAPPDATA%\crapkit-deploy` (or
`~/Library/Caches/crapkit-deploy`).

Output lands in `.crapkit/deploy-out/` (`--out` moves it):

| File | Holds |
|---|---|
| `junit-<n>.xml` | one per run; each cell's properties name its id, channel, image digest and toolchain hash |
| `transcripts/<test>.txt` and `.json` | every command a test ran, with cwd, exit code, output and duration |
| `build.json` | build seconds, image size, builder, `docker system df` before and after |
| `versions-<image>.txt` | what each tool in a new image prints, held to `pins.toml` |

## Images

One Dockerfile, `tests/deploy/docker/Dockerfile`, five targets:

| Target | Holds |
|---|---|
| `cells` | Debian trixie (snapshot.debian.org), uv, CPython 3.10 to 3.14, Node 22, git, pipx, prek, the npm fixture cache, the runner venv, the wheelhouse |
| `core` | cells + Claude Code (and floors 2.1.139, 2.1.138), Codex (and floor 0.121.0), the Cursor agent |
| `full` | core + Gemini CLI, OpenCode, Copilot CLI, Cline, Continue, Crush, Amp, oh-my-pi, Junie, Goose, Aider, both Agent SDKs |
| `ci` | cells + act, actions/checkout and actions/setup-python at their pinned SHAs, a runner tool-cache Python |
| `gui` | full + xvfb, VS Code, Zed |

The wheelhouse is the last layer of every target, so a crapkit release
rebuilds one small layer. A new image must print every pinned version
(`entry.sh versions`) or `run.py` removes its tag.

Measured on 2026-09-24 with Docker Desktop 29.8.0 and the daemon's BuildKit
0.33.0 on a 24-core Windows machine. "Warm" changed entry.sh, which rebuilds
the last two layers; a new crapkit release also refetches the wheelhouse stage
(39 s in the prototype). "Compressed" is the content a registry or the GitHub
Actions cache would hold.

| Image | On disk | Compressed | Cold (`--no-cache`) | Warm | No change |
|---|---|---|---|---|---|
| `cells` | 1.83 GB | 494 MB | 356 s | 22.5 s | 0.9 s |
| `core` | 5.29 GB | 1.40 GB | 455 s | 26.3 s | 1.2 s |
| `full` | 13.62 GB | 3.71 GB | 1178 s | 22.3 s | 0.8 s |
| `ci` | 1.87 GB | 506 MB | 231 s | 21.2 s | 2.2 s |
| `gui` | 15.96 GB | 4.31 GB | 1402 s | 41.8 s | 1.1 s |

About 6 minutes of each cold `full` and `gui` build is the export of their
layers into the daemon. The cold rebuilds reproduced `image-manifest.lock` for
all five images: every tool version, Debian package, npm package integrity,
runner package, wheel sha256 and downloaded-binary tree hash.

The kit's own tests (`--packet deploy-kit -n 4`) pass in every image, twice
from fresh containers with the same verdicts: 48 pass and 1 skips (a
Windows-only path test) in 16 to 63 s per run. Natively on Windows, 49 pass
in 75 to 100 s. The offline upgrade test (crapkit 0.7.6 adopted, then pip
upgraded to the candidate under `--network none`) takes 8 to 27 s.

## Changing a pin

1. Edit `pins.toml` (and `tests/deploy/docker/harness-*/package.json` for an npm harness).
2. `python tools/deploy/lock.py` refreshes `wheelhouse.lock` when a wheel set changed.
3. `python tools/deploy/run.py --build-only --image <image>` for each image that holds it.
4. `python tools/deploy/lock.py manifest --image crapkit-deploy:<image>` refreshes that image's block of `image-manifest.lock`.
5. Commit all of it. `lock.py --check` and `lock.py manifest --check --image ...` exit 1 on drift.

## Writing a cell

```python
from kit import docsnip
from kit.cells import cell

PACKET = "deploy-channels"


@cell("lin-pip-start-py311", channel="pip venv", harness="none",
      scenario="fresh: README 60-second start", use_cases="install",
      os="linux", image="core", cadence="push")
def test_readme_install_line(box, candidate):
    venv = box.root / "venv"
    box.run([box.toolchain.python("3.11"), "-m", "venv", str(venv)], expect=0)
    box.prepend_path(venv / "bin")
    install = docsnip.commands(docsnip.fence("README.md", "The 60-second start"))[0]
    box.script(install, expect=0)

    assert candidate.version in box.run(["crapkit", "--version"], expect=0).stdout
```

The fixtures (`tests/deploy/conftest.py`): `box` is a fresh sandbox, `candidate`
the build under test, `templates` the session's fixture-repo cache,
`toolchain` the pinned tools, `transcript` this test's record.
`tests/deploy/kit/__init__.py` lists every kit module and resource folder with
what it is for, and `test_kit_pieces` fails on one the list does not name, so
a packet that adds a piece adds its line there.

Rules the kit holds a cell to:

- Run every command through `box.run` or `box.script`. They use the sandbox
  environment (an allowlist, a fresh HOME, offline pip and uv) and write the
  transcript. A process started any other way sees this machine instead, so
  `test_kit_isolation` fails any module at the top of `tests/deploy` (the
  cells, their helpers and `conftest.py`; the `kit` package is exempt) that
  imports `subprocess`, `pty` or `multiprocessing`, or calls `os.system`,
  `os.popen`, `os.startfile`, `os.exec*`, `os.spawn*`, `os.posix_spawn*`,
  `os.fork*`, `asyncio.create_subprocess_*` or `hang_guard.run`. The failure
  names each module and what it starts. A module that has to start a process
  itself, such as Zed, which runs until its window closes while `box.run`
  waits for the child to exit, needs an entry in `REACHES_THE_MACHINE` there:
  the names it uses and why. The rule also fails on any collected test that
  is neither a `@cell` nor marked `kit`: no job selects it. Several tests may
  share a cell id. A helper test marked `@pytest.mark.kit` runs in every job
  and under `--packet` for the packet its module's `PACKET` names.
- Each command gets the 120 s hang bound. A whole package install (`npm ci`,
  a large `pip install`) passes `bound=sandbox.SLOW`: `npm ci` of the 420
  fixture packages took 2 to 3 minutes on a loaded Windows machine.
- Every harness's update switch is set in the sandbox (environment and home
  files, `sandbox.QUIET` and `QUIET_FILES`), and a run fails when a harness
  binary changes between session start and end. Call
  `box.put_harnesses_on_path()` before running a harness by name, as a user
  who installed it has it: omp's `#!/usr/bin/env bun` exits 127 without it.
- Take install lines from `docsnip`, never retyped. A moved fence fails
  naming the page and heading.
- Take versions from `kit.wheels` (`n_minus_1()`, `releases()`), never literals.
- Take repos from `repos.checkout` and the npm SDK install from
  `repos.npm_fixtures`; both build once per session.
- Point GitHub URLs at `gitmirror.make(box)`; `publish` and `release_to` move
  main and the tags the way a release does.
- Linux cells run in a container, where crapkit's container guard refuses a
  coverage.py lane. Assert that refusal, or apply the `container_ok` key from
  docs/lanes.md#containers, as the cell's scenario says.
