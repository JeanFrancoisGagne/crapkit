# Deploy suite tools

`tests/deploy` holds the deploy cells. Each cell installs crapkit the way a
user does (pip, pipx, uv, a plugin marketplace, a hook route) and drives it
through the surface that user touches. The tools here build the images the
Linux cells run in, install the same toolchain natively on Windows, macOS and
a bare Linux runner, and run any cell, packet or cadence in either place.
`pins.toml` is the one source of every version and hash.

## Run

    python tools/deploy/run.py --packet deploy-kit             # the kit's own tests, in crapkit-deploy:core
    python tools/deploy/run.py --cell lin-pip-start-py311      # one cell (repeat --cell for more)
    python tools/deploy/run.py --packet deploy-channels -n 4   # one packet's cells, 4 xdist workers
    python tools/deploy/run.py --cadence nightly --image full  # every nightly Linux cell the full image holds
    python tools/deploy/run.py --cadence weekly --image cells-arm64 --cell lin-arm64   # linux/arm64, under QEMU on x86_64
    python tools/deploy/run.py --cadence weekly --image full-latest --online --cell latest-harnesses
    python tools/deploy/run.py --faketime +400d --packet deploy-kit   # every process 400 days ahead
    python tools/deploy/run.py --native                        # the same cells on this machine, no container
    python tools/deploy/run.py --build-only --image gui        # build one image, or skip it when unchanged
    python tools/deploy/run.py --repeat 2 ...                  # two fresh containers; exit 1 if a verdict differs
    python tools/deploy/run.py --faketime +400d ...            # every process in the container 400 days ahead
    python tools/deploy/run.py --image cells-arm64 --cadence weekly --cell lin-arm64
                                                               # arm64: native on an arm64 host, else under QEMU
    python tools/deploy/run.py --online --image full-latest --cadence weekly
                                                               # the weekly cells with every harness at its newest release

A Linux run builds `crapkit-deploy:<image>` (default `core`) or reuses it when
its label says it was built from the same Dockerfile, context files and pins.
It then exports the tree under test (`export.py`: a git bundle plus a tarball
of the files git shows) into `<out>/in`, and runs `tests/deploy` in a fresh
container as uid 1000 with `--network none`. Inside, `entry.sh` builds the
candidate wheel and sdist (`candidate.py`) before pytest starts. The source
tree is never in an image, so a crapkit change rebuilds nothing.

The other flags:

| Flag | What it does |
|---|---|
| `--os linux`, `windows`, `macos` | selects the cells marked for that OS; the default is `linux` in a container and this machine's OS with `--native` |
| `--online` | runs the cells marked online, and only those, in a container with the network; without it no online cell runs |
| `--bake` | copies `<out>/in` into a `crapkit-deploy:<image>-baked` image, so the cells run from the copy inside the image and not from the mounted `<out>/in` |
| `--no-cache` | builds cold |
| `--cache local` or `gha` | where BuildKit keeps its layer cache; `gha` reads and writes the GitHub Actions cache on a docker-container builder |
| `--builder NAME` | the buildx builder; the default is the daemon's own when it runs the pinned BuildKit, else `crapkit-deploy` running the pinned BuildKit image |
| `--out DIR` | where the output below lands |

`--harness core` (the default) or `--harness full` adds the harnesses that
image holds. On Windows the toolchain also holds act and a checkout of each
action act runs offline, for the gha-action-windows cell. The kit finds every
tool through the `toolchain.json` that `toolchain.py` writes beside them, so
rerun `toolchain.py` after a pin changes; a warm rerun takes seconds.

Output lands in `.crapkit/deploy-out/` (`--out` moves it):

| File | Holds |
|---|---|
| `junit-<n>.xml` | one per run; each cell's properties name its id, channel, image digest and toolchain hash |
| `transcripts/<test>.txt` and `.json` | every command a test ran, with cwd, exit code, output and duration |
| `build.json` | build seconds, image size, builder, `docker system df` before and after |
| `versions-<image>.txt` | what each tool in a new image prints, held to `pins.toml` |
| `build-<image>.log` | the whole build log; a failed build prints its path and last 30 lines |
| `latest-drift.txt` | `full-latest` only: each harness whose newest release prints other than its pin |

## Native runs

`--native` runs the cells on this machine instead of in a container. Windows
and macOS cells always run this way, and so does lin-native-start on a bare
ubuntu runner. It needs the toolchain first: `python tools/deploy/toolchain.py
[--harness core|full|none]` installs the pinned uv, CPythons, Node, pipx,
prek (Windows and Linux), pwsh (Windows and macOS), PortableGit (Windows), the
harness binaries for this OS, the wheelhouse rows and the npm caches. On Linux
it keeps the machine's own git and `/usr/bin/python3`, as a user's machine
does. A rerun makes a step again only when its pin moved or the step stopped
halfway: each step writes the pin it was made from (a sha256 or a lock's
digest) to `<output>.pin` beside its output, and a download is kept when its
bytes already hash to its pinned sha256. The toolchain root:

| OS | Toolchain root |
|---|---|
| Windows | `%LOCALAPPDATA%\crapkit-deploy` |
| macOS | `~/Library/Caches/crapkit-deploy` |
| Linux | `~/.cache/crapkit-deploy` |

`CRAPKIT_DEPLOY_TOOLCHAIN_ROOT` moves it (see Environment). A native run
passes pytest `--basetemp C:\dt` on Windows, a path with no 8.3 short name a
tool could print back in another spelling, and `$TMPDIR/crapkit-deploy-tmp`
elsewhere. pytest empties its basetemp when it starts, so a second native run
on the same machine deletes the first one's sandboxes mid-run. Give each run
its own `CRAPKIT_DEPLOY_BASETEMP`.

## A fake clock

`--faketime SPEC` runs every process in the container under libfaketime, which
every image holds (the lin-clock cell). `HH:MM:SS` starts the run's clock at
that UTC time today; any other value is libfaketime's own spec, so `+400d`
runs 400 days ahead. run.py writes the library's path to `/etc/ld.so.preload`
and the offset to `/etc/faketimerc` and mounts both read-only, so a crapkit
that a cell starts from the sandbox's allowlisted environment still sees the
fake clock. Every process gets the same offset, and the clock keeps moving.
A native run has no libfaketime, so `--native --faketime` is refused. Two
limits:

- uv and other statically linked binaries never load the preload and keep the
  real clock. The images' uv is `x86_64-unknown-linux-musl`.
- Claude Code hangs under the preload, whatever the offset. In
  `crapkit-deploy:core`, `claude --version` returns at once on the real clock
  and was still running after 40 s under the preload at `+0` and at `+400d`,
  while `node` printed the faked date at once. A cell that starts Claude Code
  cannot run under `--faketime`.

## Environment

| Variable | Read by | Why it exists |
|---|---|---|
| `CRAPKIT_DEPLOY_REPO` | run.py | The repository every image is tagged under, `crapkit-deploy` when unset. A second checkout building other pins on the same Docker daemon sets its own, so neither replaces the other's tags. `lock.py manifest --image` then takes `<repo>:<image>`. |
| `CRAPKIT_DEPLOY_TOOLCHAIN_ROOT` | toolchain.py, `run.py --native` | The native toolchain's root, in place of the OS default above, so two checkouts on one machine each keep their own pins. |
| `CRAPKIT_DEPLOY_BASETEMP` | `run.py --native` | pytest's basetemp for native runs. pytest empties it when it starts, so two native runs at once on the default `C:\dt` wipe each other. |

## Images

One Dockerfile, `tests/deploy/docker/Dockerfile`, six targets, seven images.
Every image is linux/amd64 except `cells-arm64`, the `cells` target built for
linux/arm64:

| Image | Holds |
|---|---|
| `cells` | Debian trixie (snapshot.debian.org), uv, CPython 3.10 (below the floor, for the refusal cell) to 3.14 and the 3.15 prerelease, Node 22, git, pipx, prek, libfaketime, the npm fixture cache, the runner venv, the wheelhouse |
| `cells-arm64` | cells, built for linux/arm64 with the aarch64 binaries and wheel rows (the weekly lin-arm64 cell) |
| `core` | cells + Claude Code (and floors 2.1.139, 2.1.138), Codex (and floor 0.121.0), the Cursor agent |
| `full` | core + Gemini CLI, OpenCode, Copilot CLI, Cline, Continue, Crush, Amp, oh-my-pi, Junie, Goose, Aider, both Agent SDKs |
| `full-latest` | full + every harness again at its newest release, first on PATH (the weekly latest-harnesses cells, with `--online`) |
| `ci` | cells + act, actions/checkout and actions/setup-python at their pinned SHAs, a runner tool-cache Python |
| `gui` | full + xvfb, VS Code, Zed |

The wheelhouse is the last layer of every target, so a crapkit release
rebuilds one small layer. A new image must print every pinned version
(`entry.sh versions`) or `run.py` removes its tag. `full-latest` is held to
its pinned tools the same way. Its build args change once an ISO week, so it
rebuilds on the first run of each week, and every run writes
`<out>/latest-drift.txt`.

### cells-arm64 on an x86_64 machine

An x86_64 Docker host builds and runs `cells-arm64` under QEMU, which needs a
binfmt handler for arm64. Before it builds `cells-arm64` or runs a cell in
it, run.py starts the pinned Debian image for linux/arm64 once, which takes
about 3 s. When that fails it stops there and prints the fix:

    run: this Docker host cannot run linux/arm64 containers (<docker's last line>); register QEMU once with `docker run --privileged --rm tonistiigi/binfmt --install arm64`, or build and run cells-arm64 on an arm64 host

Run the `docker run --privileged --rm tonistiigi/binfmt --install arm64` line
and start run.py again. Docker Desktop dropped the handler twice in one day on
the machine that measured the table below, so expect to run it more than
once. A handler lost in the middle of a build ends it with
`exec format error` in the log; run.py then prints the log's path, its last
30 lines and the same QEMU line.

### Measured

Sizes on disk are `docker image ls` on 2026-09-25. The other columns were
measured on 2026-09-24 with Docker Desktop 29.8.0 and the daemon's BuildKit
0.33.0 on a 24-core Windows machine, before the 3.15 prerelease and the
aarch64 wheel rows added about 0.18 GB to each image. "Warm" changed
entry.sh, which rebuilds the last two layers; a new crapkit release also
refetches the wheelhouse stage (39 s in the prototype). "Compressed" is the
content a registry or the GitHub Actions cache would hold.

| Image | On disk | Compressed | Cold (`--no-cache`) | Warm | No change |
|---|---|---|---|---|---|
| `cells` | 2.01 GB | 494 MB | 356 s | 22.5 s | 0.9 s |
| `cells-arm64` | 1.90 GB | 508 MB | 1028 s | 27.8 s | 3.4 s |
| `core` | 5.46 GB | 1.40 GB | 455 s | 26.3 s | 1.2 s |
| `full` | 13.8 GB | 3.71 GB | 1178 s | 22.3 s | 0.8 s |
| `full-latest` | 22.1 GB | not measured | 3553 s | not measured | not measured |
| `ci` | 2.05 GB | 506 MB | 231 s | 21.2 s | 2.2 s |
| `gui` | 16.1 GB | 4.31 GB | 1402 s | 41.8 s | 1.1 s |

The `cells-arm64` and `full-latest` rows are from 2026-09-25. The
`cells-arm64` cold build is its first build under QEMU, its warm build
followed a wheel lock change, and its no-change time is the QEMU check. The `full-latest` 3553 s is a weekly rebuild:
every `@latest` layer over a cached `full`, with other builds running on the
machine.

About 6 minutes of each cold `full` and `gui` build is the export of their
layers into the daemon. The cold rebuilds reproduced `image-manifest.lock` for
all five amd64 images: every tool version, Debian package, npm package
integrity, runner package, wheel sha256 and downloaded-binary tree hash.

The kit's own tests (`--packet deploy-kit -n 4`) pass in every image, twice
from fresh containers with the same verdicts: 48 pass and 1 skips (a
Windows-only path test) in 16 to 63 s per run. Natively on Windows, 49 pass
in 75 to 100 s. The offline upgrade test (crapkit 0.7.6 adopted, then pip
upgraded to the candidate under `--network none`) takes 8 to 27 s.

## The GitHub Actions cache

`--cache gha` makes BuildKit read and write an image's layers in the GitHub
Actions cache, under `scope=crapkit-deploy-<image>` with `mode=min` (the
image's own layers, not those of the stages that feed it). A failed write never
fails the build. BuildKit 0.33 stores each layer blob once, keyed by its digest,
and uses the scope only to name the list of an image's blobs. Two images share
a stored layer only when their builds made the same blob, so a job that builds
a stage on its own runner stores it again even when another job cached it.

GitHub gives a repository 10 GB of Actions cache. Past it, GitHub keeps the
new entry and evicts the least recently used ones; it also drops any entry
unused for 7 days. A pull request's entries count toward the 10 GB, and only
re-runs of that pull request can read them.

| Job | Image | Cache | Why |
|---|---|---|---|
| `deploy-linux` | `core` | `gha` | ci.yml, every push and pull request; keeps the `core` scope warm |
| `nightly-linux-core` | `core` | `gha` | the scope `deploy-linux` keeps warm |
| `nightly-act` | `ci` | `gha` | 0.56 GB |
| `nightly-linux-full` | `full` | `local` | cold every night, see below |
| `nightly-gui` | `gui` | `local` | cold every night, see below |
| `lin-repeat` | `core` | `--no-cache` | a cold build is the point |
| `weekly-online` | `core` | `local` | builds `core` cold |
| `published-online` | `core` | `local` | builds `core` cold |
| `lin-clock` | `core` | `local` | blocked (faketime) |
| `weekly-py315` | `core` | `local` | blocked (prerelease-python) |
| `latest-harnesses` | `full` | `local` | blocked (latest-mode) |
| `weekly-arm64` | `cells` | `local` | blocked (arm64) |

Compressed sizes of the images built at 9707cc6d, read layer by layer from
`docker save` on 2026-09-25: `core` 1.45 GB, `ci` 0.56 GB, `full` 3.76 GB,
`gui` 4.36 GB. `gui` is `full` plus 0.70 GB, `full` is `core`'s first 18
layers (1.35 GB) plus 2.41 GB, and `ci` is `cells` plus 0.11 GB. What the
cache would hold, as the distinct blobs of the images each built with
`--no-cache` (first column) and of the images one builder built on each
other's layers (second column, with `full`'s first 18 layers taken as
`core`'s):

| Cached | Each job builds every stage itself | Each image builds on the cached layers below it |
|---|---|---|
| `core` and `ci`, as today | 1.98 GB | 1.57 GB |
| `core`, `ci`, `full` and `gui` | 10.04 GB | 4.64 GB |

### Why `full` and `gui` build cold

Cached the way `core` and `ci` are, with each job building every stage itself,
the four images come to 10.04 GB, and an evicted `core` makes the next push
build cold. So `nightly-linux-full` and `nightly-gui` build with
`--cache local` on a runner whose disk they free first: 1178 s and 1402 s on
the 24-core machine above, not yet measured on the 4-vCPU `ubuntu-24.04`
runner, under timeouts of 90 and 100 minutes. The first nightly run's minutes
go in each job's `measured` key in `tests/deploy/MAP.toml`, and they decide
whether the cold builds stay. The other ways to cache the two images:

| Way | What it costs |
|---|---|
| Push `full` and `gui` to a private GHCR package | GitHub's Packages billing page lists Container registry storage and transfer as free for now. A login and push step on main, `packages: read` on pull request jobs, and a pull request from a fork cannot pull a private package |
| Raise the repository's Actions cache limit past 10 GB | Pay-as-you-go storage since 2025-11-20, on a Pro, Team or Enterprise account |
| Build each image on the cached layers below it: `full` reads `core`'s scope, `gui` reads `full`'s | 4.64 GB for all four. `run.py` passes one scope per image today. `nightly-linux-full` and `nightly-gui` start together, so a first `gui` run finds no `full` scope and stores its own copy of `full`'s layers |

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
  imports `subprocess`, `pty`, `multiprocessing` or `asyncio.subprocess` (or
  a submodule of one) or `ProcessPoolExecutor`, or calls `os.system`,
  `os.popen`, `os.startfile`, `os.exec*`, `os.spawn*`, `os.posix_spawn*`,
  `os.fork*`, `asyncio.create_subprocess_*`, an event loop's
  `subprocess_exec` or `subprocess_shell`, or `hang_guard.run`. It follows
  import aliases (`import os as o` then `o.system`), but not a module bound
  by assignment or loaded by `importlib`. The failure names each module and
  what it starts, and a call through a module counts as that module:
  `subprocess.Popen` prints as `subprocess`. A module that has to start a
  process itself, such as Zed, which runs until its window closes while
  `box.run` waits for the child to exit, needs an entry in
  `REACHES_THE_MACHINE` there: the names the failure printed and why. The
  rule also fails on any collected test that is neither a `@cell` nor marked
  `kit`: no job selects it. Several tests may share a cell id. A helper test
  marked `@pytest.mark.kit` runs in every job and under `--packet` for the
  packet its module's `PACKET` names.
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

Then map the cell. `tests/deploy/MAP.toml` lists every cell under `[cell]`
with the packet, cadence, os and image its `@cell` gives (`os` as a list when
the tests run on Linux and Windows), and names it wherever a doc fence, a
channel or a use case it proves is listed. Each cell needs a run that selects
it on every cadence and OS it names: ci.yml's push jobs take any push cell,
and the map's `[jobs]` entries run the rest. The nightly Windows entries run
one packet per call, so a packet with a nightly Windows cell needs its own
`--packet` call there. `tests/unit/test_deploy_map.py` names each cell the map
lacks or gives other fields, and `tests/unit/test_deploy_workflows.py` names
each cell, cadence and OS no run selects.
