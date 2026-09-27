"""tools/accuracy/corpus.py: build members from partial clones, check their pins, pack,
publish once per digest and fetch.

The expected values come from git itself and from the rules in corpus.py's
docstring: a member's pins are the object ids `git rev-parse <commit>:<path>`
prints in the source repository, a build holds only the pinned paths and the
license, a moved pin exits 1 naming the path, a pack of one tree is one byte
string whatever the files' mtimes, a digest that is already a release is not
published again, and a history bundle starts at `history_since`.
"""
import gzip
import importlib.util
import io
import os
from pathlib import Path
import sys
import tarfile

import pytest

from accuracy.kit import repos

TOOL = Path(__file__).resolve().parents[3] / "tools" / "accuracy" / "corpus.py"
DAY = 86_400


def _load():
    if "accuracy_corpus_tool" not in sys.modules:
        spec = importlib.util.spec_from_file_location("accuracy_corpus_tool", TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["accuracy_corpus_tool"]


corpus = _load()


# --- digest, slices ------------------------------------------------------------------------

def _member(**extra) -> dict:
    return {"url": "https://example.org/m.git", "commit": "a" * 40, "tag": "v1",
            "paths": ["src"], "trees": ["b" * 40], "license": "LICENSE",
            "languages": ["python"], **extra}


def test_the_digest_names_the_member_tables_alone():
    table = {"member": {"m": _member()}, "small": {"git_test_date_now": 1}}
    reordered = {"member": {"m": dict(reversed(list(_member().items())))}}
    moved = {"member": {"m": _member(commit="c" * 40)}}

    assert corpus.digest(table) == corpus.digest(reordered) == corpus.digest({"member":
                                                                              {"m": _member()}})
    assert corpus.digest(moved) != corpus.digest(table)
    assert len(corpus.digest(table)) == 12
    assert corpus.tag(table) == f"corpus-{corpus.digest(table)}"


def test_a_slice_takes_its_files_and_the_member_license():
    member = _member(slice=["src/a/x.py", "src/b/y.py"], license="COPYING")

    assert corpus.slice_files("m", member) == [("src/a/x.py", "vendor/m/x.py"),
                                               ("src/b/y.py", "vendor/m/y.py"),
                                               ("COPYING", "vendor/m/COPYING")]
    assert corpus.slice_files("m", _member()) == []


def test_an_unknown_member_exits_1(tmp_path, capsys):
    table = tmp_path / "corpus.toml"
    table.write_text('[member.m]\nurl = "u"\n', encoding="utf-8")

    code = corpus.main(["build", "--out", str(tmp_path / "out"), "--member", "nope"], table)

    assert code == 1
    assert "corpus.toml names no member nope" in capsys.readouterr().err


# --- building from a partial clone ---------------------------------------------------------

FILES = {"src/a.py": "def f():\n    return 1\n", "src/deep/b.py": "def g():\n    return 2\n",
         "lib/c.py": "def h():\n    return 3\n", "other/skip.py": "x = 1\n",
         "LICENSE": "MIT\n"}


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """A repository to clone from, serving partial clones by commit id."""
    first = repos.Commit(files={"old.txt": "old\n"}, date=repos.EPOCH - 40 * DAY, message="old")
    second = repos.Commit(files=FILES, date=repos.EPOCH, message="tree")
    root = repos.build(repos.Spec(steps=(first, second)), tmp_path_factory.mktemp("src") / "r").root
    repos.git(root, "config", "uploadpack.allowFilter", "true")
    repos.git(root, "config", "uploadpack.allowAnySHA1InWant", "true")
    return root


def _pinned(source: Path, paths: list[str], **extra) -> dict:
    commit = repos.git(source, "rev-parse", "HEAD").strip()
    trees = [repos.git(source, "rev-parse", f"{commit}:{path}").strip() for path in paths]
    return {"url": source.as_uri(), "commit": commit, "tag": "v1", "paths": paths,
            "trees": trees, "license": "LICENSE", "languages": ["python"], **extra}


def _files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@pytest.mark.process
def test_a_member_holds_its_pinned_paths_and_license_and_nothing_else(source, tmp_path):
    table = {"member": {"m": _pinned(source, ["src", "lib/c.py"])}}

    assert corpus.build(tmp_path / "out", table) == ["m"]
    assert _files(tmp_path / "out" / "m") == ["LICENSE", "crapkit.toml", "lib/c.py", "src/a.py",
                                              "src/deep/b.py"]
    assert (tmp_path / "out" / "DIGEST").read_text(encoding="utf-8") == corpus.digest(table) + "\n"
    assert 'paths = ["src", "lib/c.py"]' in (tmp_path / "out" / "m" / "crapkit.toml").read_text(
        encoding="utf-8")


@pytest.mark.process
def test_every_built_file_holds_the_bytes_git_stores(source, tmp_path):
    """A built file hashes to the blob its pin names, on every OS: Git for Windows
    turns core.autocrlf on system-wide, and a checkout that honours it writes
    CRLF files that a Linux build does not."""
    member = _pinned(source, ["lib/c.py", "src/a.py"])

    corpus.build(tmp_path / "out", {"member": {"m": member}})
    built = [repos.git(tmp_path, "hash-object", "--no-filters",
                       str(tmp_path / "out" / "m" / path)).strip()
             for path in member["paths"]]

    assert built == member["trees"]


@pytest.mark.process
def test_a_moved_pin_exits_1_naming_the_path(source, tmp_path, capsys):
    member = _pinned(source, ["src", "lib/c.py"])
    member["trees"][1] = "0" * 40
    table = tmp_path / "corpus.toml"
    table.write_text(_toml(member), encoding="utf-8")

    code = corpus.main(["build", "--out", str(tmp_path / "out")], table)

    assert code == 1
    assert f"m: lib/c.py at {member['commit'][:12]} is " in capsys.readouterr().err


def _toml(member: dict) -> str:
    lines = ["[member.m]"] + [f"{key} = {_value(value)}" for key, value in member.items()]
    return "\n".join(lines) + "\n"


def _value(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    return str(value) if isinstance(value, int) else '"' + str(value) + '"'


@pytest.mark.process
def test_the_pin_line_is_what_git_prints(source):
    member = _pinned(source, ["src"])
    work = source  # the source repository holds the commit already

    line = corpus.pin_line(work, "m", member)

    assert line == (f'[member.m] trees = ["{member["trees"][0]}"] '
                    f"git_test_date_now = {repos.EPOCH + DAY}")


@pytest.mark.process
def test_the_history_bundle_starts_at_history_since(source, tmp_path):
    since = "2025-06-01"
    member = _pinned(source, ["src"], history_since=since)
    bundle = tmp_path / "history" / "m.bundle"

    corpus.history_bundle(tmp_path / "work", bundle, member)
    clone = tmp_path / "clone"
    repos.git(tmp_path, "clone", "-q", str(bundle), clone.name)

    assert repos.git(clone, "log", "--format=%s").split() == ["tree"]
    assert repos.git(clone, "rev-parse", "HEAD:src/a.py").strip() == repos.git(
        source, "rev-parse", "HEAD:src/a.py").strip()


# --- pack, publish, fetch ----------------------------------------------------------------

def _tree(root: Path, stamp: int) -> Path:
    for name, text in {"b/2.txt": "two", "a.txt": "one", "b/1.txt": "uno"}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(text.encode("utf-8"))
        os.utime(root / name, (stamp, stamp))
    return root


def test_one_tree_packs_to_one_byte_string(tmp_path):
    first = corpus.pack(_tree(tmp_path / "one", 1_000_000), tmp_path / "one.tar.gz")
    second = corpus.pack(_tree(tmp_path / "two", 2_000_000), tmp_path / "two.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress((tmp_path / "one.tar.gz").read_bytes()))) as tar:
        members = [(member.name, member.mtime, member.uid) for member in tar.getmembers()]

    assert first == second
    assert members == [("a.txt", 0, 0), ("b/1.txt", 0, 0), ("b/2.txt", 0, 0)]


class FakeGh:
    """Answers `gh release view` as published or not and records every call."""

    def __init__(self, published: bool, create_code: int = 0, tarball: Path | None = None):
        self.published, self.create_code, self.tarball, self.calls = (published, create_code,
                                                                      tarball, [])

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("release", "download") and self.published:
            target = Path(args[args.index("--dir") + 1])
            (target / "corpus.tar.gz").write_bytes(self.tarball.read_bytes())
        code = {"view": 0 if self.published else 1, "create": self.create_code,
                "download": 0 if self.published else 1}.get(args[1], 0)
        return type("Done", (), {"returncode": code, "stderr": "gh said no"})()


TABLE = {"member": {"m": _member()}}


def test_a_published_digest_is_not_published_again(tmp_path, monkeypatch):
    gh = FakeGh(published=True)
    monkeypatch.setattr(corpus, "gh", gh)

    line = corpus.publish(tmp_path / "c.tar.gz", TABLE, "owner/repo", dry_run=False)

    assert line == f"{corpus.tag(TABLE)} is already published on owner/repo; nothing to do"
    assert [call[:2] for call in gh.calls] == [("release", "view")]


def test_a_new_digest_is_one_prerelease_never_marked_latest(tmp_path, monkeypatch):
    gh = FakeGh(published=False)
    monkeypatch.setattr(corpus, "gh", gh)

    line = corpus.publish(tmp_path / "c.tar.gz", TABLE, "owner/repo", dry_run=False)

    created = gh.calls[-1]
    assert line == f"published {corpus.tag(TABLE)} on owner/repo"
    assert created[:3] == ("release", "create", corpus.tag(TABLE))
    assert {"--prerelease", "--latest=false"} <= set(created)


def test_a_dry_run_prints_the_call_and_makes_no_release(tmp_path, monkeypatch):
    gh = FakeGh(published=False)
    monkeypatch.setattr(corpus, "gh", gh)

    line = corpus.publish(tmp_path / "c.tar.gz", TABLE, "owner/repo", dry_run=True)

    assert line.startswith(f"would run: gh release create {corpus.tag(TABLE)} ")
    assert [call[:2] for call in gh.calls] == [("release", "view")]


def test_a_failed_publish_is_infra(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(corpus, "gh", FakeGh(published=False, create_code=1))
    tarball = tmp_path / "c.tar.gz"
    tarball.write_bytes(b"")
    table = tmp_path / "corpus.toml"
    table.write_text(_toml(_member()), encoding="utf-8")

    assert corpus.main(["publish", str(tarball)], table) == 3
    assert "exited 1: gh said no" in capsys.readouterr().err


def test_fetch_downloads_and_unpacks_once(tmp_path, monkeypatch):
    built = _tree(tmp_path / "built", 1)
    (built / "DIGEST").write_text(corpus.digest(TABLE) + "\n", encoding="utf-8")
    corpus.pack(built, tmp_path / "c.tar.gz")
    gh = FakeGh(published=True, tarball=tmp_path / "c.tar.gz")
    monkeypatch.setattr(corpus, "gh", gh)

    first = corpus.fetch(tmp_path / "cache", TABLE, "owner/repo")
    second = corpus.fetch(tmp_path / "cache", TABLE, "owner/repo")

    assert first == second == tmp_path / "cache" / corpus.tag(TABLE)
    assert (first / "b" / "1.txt").read_text(encoding="utf-8") == "uno"
    assert [call[:2] for call in gh.calls] == [("release", "download")]


def _fetch_main(tmp_path, monkeypatch, gh) -> int:
    monkeypatch.setattr(corpus, "gh", gh)
    monkeypatch.chdir(tmp_path)
    table = tmp_path / "corpus.toml"
    table.write_text(_toml(_member()), encoding="utf-8")
    return corpus.main(["fetch", "--dest", "cache"], table)


def test_fetch_prints_the_folder_it_filled_as_an_absolute_path(tmp_path, monkeypatch, capsys):
    """CI writes the printed folder to CRAPKIT_ACCURACY_CORPUS and mounts it,
    both of which need a path that holds from any working directory."""
    built = _tree(tmp_path / "built", 1)
    corpus.pack(built, tmp_path / "c.tar.gz")

    code = _fetch_main(tmp_path, monkeypatch, FakeGh(published=True, tarball=tmp_path / "c.tar.gz"))

    printed = Path(capsys.readouterr().out.strip())
    assert code == 0 and printed.is_absolute()
    assert (printed / "b" / "1.txt").read_text(encoding="utf-8") == "uno"


def test_a_digest_with_no_release_is_infra_and_says_how_to_publish_it(tmp_path, monkeypatch,
                                                                     capsys):
    code = _fetch_main(tmp_path, monkeypatch, FakeGh(published=False))

    err = capsys.readouterr().err
    assert code == 3
    assert "exited 1: gh said no" in err and "corpus.py build --out DIR" in err
