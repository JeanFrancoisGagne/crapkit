"""The 2026-09-06 architecture review rebuilds from its own folder.

Its evidence folder was renamed to evidence/ so that no tracked path is too
long for a Windows clone. build-review.py still wrote every evidence link into
the old folder name, so a rebuild, which the folder README tells a reader to
run, put 22 links in the report to files that no longer exist, and
publish-review.py then refused the report it had just built. These tests
rebuild the report and publish it the way the scripts do, and hold both to the
committed folder.
"""
import gzip
import hashlib
import json
import shutil
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
REVIEW = "docs/architecture/2026-09-06-post-implementation"
NAME = "crapkit-architecture-rerun-2026-09-06"
REPORT = NAME + ".html"
# publish-review.py writes these three itself, so a workspace does not hold them.
WRITTEN = {"README.md", "manifest.json", ".gitattributes"}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        self.hrefs += [value for key, value in attrs if tag == "a" and key == "href"]


def _local_links(page: Path) -> list[str]:
    parser = _Links()
    parser.feed(page.read_text(encoding="utf-8"))
    return [href for href in parser.hrefs if not href.startswith(("#", "http:", "https:"))]


def _missing(links: list[str], folder: Path) -> list[str]:
    return [link for link in links if not (folder / link).is_file()]


def _tracked() -> list[Path]:
    listed = hang_guard.run(["git", "ls-files", "-z", "--", REVIEW], cwd=ROOT)
    assert listed.returncode == 0, listed.stderr.decode("utf-8", "replace")
    return [Path(path.decode("utf-8")).relative_to(REVIEW) for path in listed.stdout.split(b"\0") if path]


def _run(script: str, cwd: Path, *args: Path) -> str:
    done = hang_guard.run([sys.executable, script, *map(str, args)], cwd=cwd)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return done.stdout.decode("utf-8")


def _plain(path: Path) -> bytes:
    """The bytes a reader sees: unpacked, and with the line ending of the
    platform that wrote them removed."""
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return data.replace(b"\r\n", b"\n")


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _workspace_place(rel: Path) -> Path | None:
    """Where publish-review.py expects a committed file: the evidence files
    beside the review's own files, and root-evidence/ as a folder."""
    if rel.parts[0] == "evidence":
        return Path(*rel.parts[1:])
    return None if rel.name in WRITTEN else rel


def _workspace(target: Path) -> Path:
    for rel in _tracked():
        place = _workspace_place(rel)
        if place is not None:
            _copy(ROOT / REVIEW / rel, target / place)
    inventory = target / "project-inventory.json.gz"
    inventory.with_suffix("").write_bytes(gzip.decompress(inventory.read_bytes()))
    inventory.unlink()
    return target


def _files(folder: Path) -> set[str]:
    return {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}


def _differing(folder: Path) -> list[str]:
    committed = {rel.as_posix() for rel in _tracked()} - {"manifest.json"}
    assert _files(folder) - {"manifest.json"} == committed
    return [name for name in sorted(committed) if _plain(folder / name) != _plain(ROOT / REVIEW / name)]


def _manifest(folder: Path) -> dict:
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["files"]


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory) -> Path:
    review = tmp_path_factory.mktemp("review")
    for rel in _tracked():
        _copy(ROOT / REVIEW / rel, review / rel)
    _run("build-review.py", review)
    return review


@pytest.mark.parametrize("name", [REPORT, NAME + ".json"])
def test_a_rebuild_reproduces_the_committed_report(rebuilt, name):
    assert _plain(rebuilt / name) == _plain(ROOT / REVIEW / name), \
        f"build-review.py writes a different {name} from the one committed in {REVIEW}"


def test_every_evidence_link_in_a_rebuilt_report_names_a_committed_file(rebuilt):
    links = [link for link in _local_links(rebuilt / REPORT) if not link.endswith(".zip")]

    assert links, "the rebuilt report links no file, so this checked nothing"
    assert _missing(links, rebuilt) == [], f"links in {REPORT} that name no file in {REVIEW}"


def test_the_publish_flow_reproduces_the_committed_folder(tmp_path):
    workspace = _workspace(tmp_path / "workspace")
    _run("build-review.py", workspace)
    published = json.loads(_run("publish-review.py", workspace, tmp_path / "repo", tmp_path / "out"))
    target = tmp_path / "repo" / REVIEW

    assert published["broken_links"] == 0
    assert _differing(target) == [], "files publish-review.py writes differently from the committed ones"
    assert sorted(_manifest(target)) == sorted(_manifest(ROOT / REVIEW))


def test_every_link_in_the_published_copy_resolves_next_to_it(tmp_path):
    workspace = _workspace(tmp_path / "workspace")
    _run("build-review.py", workspace)
    _run("publish-review.py", workspace, tmp_path / "repo", tmp_path / "out")
    out = tmp_path / "out"

    assert any(link.endswith(".zip") for link in _local_links(out / REPORT)), "the report links no archive"
    assert _missing(_local_links(out / REPORT), out) == [], f"links in the published {REPORT} that name nothing"


def test_the_committed_manifest_binds_every_file_in_the_folder():
    files = _manifest(ROOT / REVIEW)
    tracked = {rel.as_posix() for rel in _tracked()} - {"manifest.json"}

    assert sorted(files) == sorted(tracked)
    stale = [name for name, record in files.items()
             if hashlib.sha256((ROOT / REVIEW / name).read_bytes()).hexdigest() != record["sha256"]]
    assert stale == [], "manifest.json records a sha256 these files no longer have"
