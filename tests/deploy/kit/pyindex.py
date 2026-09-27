"""A PEP 503 simple index on 127.0.0.1, serving wheel directories the way
PyPI serves releases, cache headers included.

uvx decides whether to look for a newer crapkit from the Cache-Control PyPI
sends, so a cell that asks what `uvx crapkit --version` returns after a
release needs an index that answers with the same headers. Poetry, PDM and
Pipenv also take an index URL where they take no find-links.

    with pyindex.serve([wheelhouse, candidate.dist]) as index:
        box.run(["uvx", "--index-url", index.simple, "crapkit", "--version"])
"""
from __future__ import annotations

import hashlib
import html
import re
import zipfile
from pathlib import Path

from kit.httpstub import Reply, Request, Stub

# What pypi.org sends: simple pages expire in ten minutes, files never change.
PAGE_CACHE = "max-age=600, public"
FILE_CACHE = "max-age=365000000, immutable, public"
SUFFIXES = (".whl", ".tar.gz", ".zip")
NOT_FOUND = Reply(404, b"not found")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def project_of(filename: str) -> str:
    if filename.endswith(".whl"):
        return normalize(filename.split("-", 1)[0])
    return normalize(filename.rsplit("-", 1)[0])


def requires_python(path: Path) -> str | None:
    """A wheel's Requires-Python, so pip and uv skip it on an older interpreter."""
    if not path.name.endswith(".whl"):
        return None
    with zipfile.ZipFile(path) as wheel:
        metadata = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
        found = re.search(r"^Requires-Python: (.+)$", wheel.read(metadata).decode("utf-8"), re.M)
    return found[1].strip() if found else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Index:
    def __init__(self, directories: list[Path]):
        self.files = {path.name: path for directory in directories for path in sorted(Path(directory).iterdir())
                      if path.name.endswith(SUFFIXES)}
        self.stub = Stub(self.answer)

    @property
    def simple(self) -> str:
        return f"{self.stub.url}/simple/"

    def projects(self) -> dict[str, list[Path]]:
        grouped: dict[str, list[Path]] = {}
        for name, path in self.files.items():
            grouped.setdefault(project_of(name), []).append(path)
        return grouped

    def _link(self, path: Path) -> str:
        requires = requires_python(path)
        attr = f' data-requires-python="{html.escape(requires)}"' if requires else ""
        return f'<a href="/files/{path.name}#sha256={_sha256(path)}"{attr}>{path.name}</a><br>'

    def _page(self, body: str) -> Reply:
        page = f"<!DOCTYPE html><html><body>{body}</body></html>".encode("utf-8")
        return Reply(200, page, (("Content-Type", "text/html"), ("Cache-Control", PAGE_CACHE)))

    def answer(self, request: Request) -> Reply:
        parts = [part for part in request.path.split("?")[0].split("/") if part]
        route = {"simple": self._simple, "files": self._file}.get(parts[0] if parts else "")
        return route(parts[1:]) if route else NOT_FOUND

    def _simple(self, rest: list[str]) -> Reply:
        if not rest:
            return self._page("".join(f'<a href="/simple/{name}/">{name}</a><br>' for name in sorted(self.projects())))
        paths = self.projects().get(normalize(rest[0]))
        return self._page("".join(map(self._link, paths))) if paths else NOT_FOUND

    def _file(self, rest: list[str]) -> Reply:
        path = self.files.get(rest[0]) if rest else None
        return Reply(200, path.read_bytes(), (("Cache-Control", FILE_CACHE),)) if path else NOT_FOUND

    def __enter__(self) -> "Index":
        self.stub.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stub.stop()


def serve(directories: list[Path]) -> Index:
    return Index([Path(directory) for directory in directories])
