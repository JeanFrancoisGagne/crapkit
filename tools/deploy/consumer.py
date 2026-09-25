"""Build the repository that calls crapkit's GitHub Action, the way an adopter's looks.

    python crapkit/tools/deploy/consumer.py --root . --seed-from ./crapkit

The deploy-action job checks crapkit out to `crapkit/` in its workspace and
runs this from the workspace root, so `uses: ./crapkit` then scores a
repository that is not crapkit. The act cells run it on a sandbox directory.
Either way the result is one git repository at --root:

  main      the files under tests/fixtures/action_consumer/base, then the
            Python quickstart's adoption: `crapkit init`, `crapkit coverage`,
            `crapkit ratchet seed`, and one commit of crapkit.toml,
            crapkit-ratchet.tsv and .gitignore. That commit is the fork point.
  feature   one commit on top of it with the planted breach
            (tests/fixtures/action_consumer/breach): `grade()` gains a branch,
            so it scores worse than its ratchet mark. HEAD is left here.

The seeding crapkit is installed from --seed-from (a pip requirement: a path,
a wheel or `crapkit==X`) into a throwaway venv outside --root, with pytest and
pytest-cov for the lane `init` writes. Its `.crapkit/` store is deleted after
the seed, because a CI checkout holds none. `crapkit/` goes in
.git/info/exclude, and the config `init` writes scopes only the consumer's
own tracked files, so the action's checkout is never scored.

--subdir puts the adopted package below the git top (a monorepo),
--container-ok commits `container_ok = true` on the lane (docs/lanes.md,
"Containers"), and --main-moves adds a commit on main after the fork, so a
pull request's base.sha is not its fork point. The last line printed is JSON:
{"root", "crapkit_root", "fork", "head", "main"}. Exits 1 when `git status
--porcelain` is not empty at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parents[1] / "tests" / "fixtures" / "action_consumer"
EXCLUDED = ["crapkit/"]
IDENTITY = ["-c", "user.name=crapkit deploy consumer", "-c", "user.email=consumer@example.com",
            "-c", "commit.gpgsign=false"]
WINDOWS = os.name == "nt"
LANE_PARSER = re.compile(r'^(parser = "coveragepy")$', re.MULTILINE)


# --- git ------------------------------------------------------------------------

def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *IDENTITY, *args], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def init_repo(root: Path) -> None:
    """A repository at root with main checked out and crapkit/ excluded."""
    if not (root / ".git").exists():
        git(root, "init", "-q", "-b", "main")
    exclude = root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    kept = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    exclude.write_text(kept + "".join(f"{line}\n" for line in EXCLUDED), encoding="utf-8")


def porcelain(root: Path) -> str:
    return git(root, "status", "--porcelain")


# --- files -------------------------------------------------------------------------

def copy_tree(source: Path, dest: Path) -> None:
    shutil.copytree(source, dest, dirs_exist_ok=True)


def in_container() -> bool:
    """crapkit's own test for the container guard (src/crapkit/lanes.py)."""
    return os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1" or Path("/.dockerenv").exists()


def with_container_ok(config: str) -> str:
    """crapkit.toml with `container_ok = true` on its coveragepy lane."""
    changed, count = LANE_PARSER.subn(r"\1\ncontainer_ok = true", config)
    if count != 1:
        raise SystemExit(f"consumer: expected one coveragepy lane in crapkit.toml, found {count}")
    return changed


# --- the seed ------------------------------------------------------------------------

def scripts(env_dir: Path) -> Path:
    return env_dir / ("Scripts" if WINDOWS else "bin")


def seed_venv(spec: str, where: Path) -> Path:
    """A venv holding the seeding crapkit and the lane's pytest-cov."""
    subprocess.run([sys.executable, "-m", "venv", str(where)], check=True)
    python = scripts(where) / ("python.exe" if WINDOWS else "python")
    subprocess.run([str(python), "-m", "pip", "install", "-q", spec, "pytest", "pytest-cov"], check=True)
    return scripts(where)


def run_crapkit(bin_dir: Path, target: Path, *args: str) -> None:
    env = dict(os.environ, PATH=os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    launcher = bin_dir / ("crapkit.exe" if WINDOWS else "crapkit")
    print(f"$ crapkit {' '.join(args)}", flush=True)
    subprocess.run([str(launcher), *args], cwd=target, env=env, check=True)


def adopt(bin_dir: Path, target: Path, container_ok: bool) -> None:
    """The quickstart's adoption: init, coverage, ratchet seed. The seed runs
    on the adopter's machine, which is no container: inside one it runs with
    container_ok, and the committed config keeps it only when asked."""
    run_crapkit(bin_dir, target, "init")
    config = target / "crapkit.toml"
    written = config.read_text(encoding="utf-8")
    config.write_text(with_container_ok(written) if in_container() else written, encoding="utf-8")
    run_crapkit(bin_dir, target, "coverage")
    run_crapkit(bin_dir, target, "ratchet", "seed")
    config.write_text(with_container_ok(written) if container_ok else written, encoding="utf-8")
    remove(target / ".crapkit")


def _writable_then_retry(function, path, _info) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onerror=_writable_then_retry)


def seed(spec: str, target: Path, container_ok: bool) -> None:
    where = Path(tempfile.mkdtemp(prefix="crapkit-seed-"))
    try:
        adopt(seed_venv(spec, where / "venv"), target, container_ok)
    finally:
        remove(where)


# --- the consumer ----------------------------------------------------------------------

def build(root: Path, spec: str, subdir: str = "", container_ok: bool = False, main_moves: bool = False) -> dict:
    target = root / subdir if subdir else root
    target.mkdir(parents=True, exist_ok=True)
    init_repo(root)
    copy_tree(FIXTURE / "base", target)
    commit(root, "calc: grades and a curve")
    seed(spec, target, container_ok)
    fork = commit(root, "adopt crapkit")
    git(root, "checkout", "-q", "-b", "feature")
    copy_tree(FIXTURE / "breach", target)
    head = commit(root, "grade: an E for repeated attempts")
    main = move_main(root) if main_moves else fork
    return {"root": str(root), "crapkit_root": str(target), "fork": fork, "head": head, "main": main}


def move_main(root: Path) -> str:
    """One commit on main after the fork; HEAD goes back to feature."""
    git(root, "checkout", "-q", "main")
    (root / "NOTES.md").write_text("Release notes live here.\n", encoding="utf-8")
    moved = commit(root, "notes: where release notes live")
    git(root, "checkout", "-q", "feature")
    return moved


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True, help="the git top to build at (the workspace)")
    parser.add_argument("--seed-from", required=True, help="pip requirement for the crapkit that seeds the ratchet")
    parser.add_argument("--subdir", default="", help="put the adopted package here, below the git top")
    parser.add_argument("--container-ok", action="store_true", help="commit container_ok = true on the lane")
    parser.add_argument("--main-moves", action="store_true", help="add a commit on main after the fork")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    root = args.root.resolve()
    built = build(root, args.seed_from, args.subdir, args.container_ok, args.main_moves)
    left = porcelain(root)
    if left:
        print(f"consumer: git status --porcelain is not empty:\n{left}", file=sys.stderr)
        return 1
    print(json.dumps(built))
    return 0


if __name__ == "__main__":
    sys.exit(main())
