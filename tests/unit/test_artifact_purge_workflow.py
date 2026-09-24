"""The nightly artifact purge, run under the jq it runs with.

.github/workflows/artifact-purge.yml pages through the actions/artifacts
listing with `gh api` and reads each page with system jq: which artifacts are
expired, how many bytes they hold. GitHub can send `artifacts`,
`size_in_bytes` and `expired` as null or leave them out, and a page can fail.
A filter that read one of those wrong would delete a live artifact, keep an
expired one, or report a clean quota over a listing it never finished. These
run the step itself, with the real `gh` against a local API and each jq engine
first on PATH, and check which ids it deleted.
"""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "artifact-purge.yml"
_ABSENT = object()


def _artifact(ident: int, expired=True, size=1000) -> dict:
    fields = {"id": ident, "name": f"a{ident}", "expired": expired, "size_in_bytes": size}
    return {key: value for key, value in fields.items() if value is not _ABSENT}


# name -> (the pages the listing serves, the ids the purge must delete). A page
# is a list of artifacts, a whole response body, or the HTTP status it fails
# with. Past the last page the API answers an empty list, which is what ends
# the step's loop.
_LISTINGS = {
    "control": ([[_artifact(1), _artifact(2, expired=False), _artifact(3)]], [1, 3]),
    "artifacts-null": ([{"total_count": 0, "artifacts": None}], []),
    "size-in-bytes-null": ([[_artifact(1, size=None), _artifact(2, expired=False)]], [1]),
    "size-in-bytes-absent": ([[_artifact(1, size=_ABSENT), _artifact(2, expired=False, size=_ABSENT)]], [1]),
    "expired-null": ([[_artifact(1, expired=None), _artifact(2)]], [2]),
    "expired-absent": ([[_artifact(1, expired=_ABSENT), _artifact(2)]], [2]),
    "no-artifact": ([[]], []),
    "the-expired-artifact-on-page-2": (
        [[_artifact(ident, expired=False) for ident in range(1, 101)], [_artifact(101)]], [101]),
}


class _ArtifactsApi(BaseHTTPRequestHandler):
    """The listing, one page per `?page=N` from `server.pages`, and the
    deletes, recorded in `server.deleted` by id."""

    def do_GET(self):
        url = urlsplit(self.path)
        page = int(parse_qs(url.query).get("page", ["1"])[0])
        answer = self.server.pages[page - 1] if page <= len(self.server.pages) else []
        if isinstance(answer, int):
            self._answer(answer, {"message": f"fake {answer}"})
            return
        if isinstance(answer, list):
            answer = {"total_count": len(answer), "artifacts": answer}
        self._answer(200, answer)

    def do_DELETE(self):
        self.server.deleted.append(int(urlsplit(self.path).path.rsplit("/", 1)[1]))
        self.send_response(204)
        self.end_headers()

    def _answer(self, status: int, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _bash() -> str:
    """The bash on PATH, or Git's in place of the WSL launcher under System32,
    which cannot read the files this test writes."""
    bash = shutil.which("bash")
    if bash is None or "system32" in bash.lower():
        bash = _git_bash()
    if bash is None:
        pytest.skip("no bash on PATH to run the step under")
    return bash


def _git_bash():
    git = shutil.which("git")
    candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
    return str(candidate) if candidate and candidate.is_file() else None


def _purge_step() -> str:
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["purge"]["steps"]
    step = next((s for s in steps if s.get("name") == "Delete expired artifacts"), None)
    assert step is not None, "the purge job lost its 'Delete expired artifacts' step"
    return step["run"]


def _jq_first_on_path(tmp_path, engine: str) -> Path:
    """A directory whose `jq` runs `engine`. jq on Windows ends its lines with
    CRLF, which the ubuntu runner's jq never writes, so the CR goes."""
    binary = shutil.which(engine)
    if binary is None:
        pytest.skip(f"needs {engine} on PATH; the Linux CI jobs carry jq 1.7.1 and jq-1.6")
    shim = tmp_path / "jq-bin"
    shim.mkdir()
    (shim / "jq").write_text(f'#!/bin/bash\nset -o pipefail\n"{Path(binary).as_posix()}" "$@" | tr -d "\\r"\n',
                             encoding="utf-8", newline="\n")
    (shim / "jq").chmod(0o755)
    return shim


def _purge(tmp_path, engine: str, pages: list) -> tuple:
    """The step under `bash -e`, the shell a `run:` with no `shell:` gets on
    ubuntu, with `engine` as its jq and the `gh` on PATH routed to a local API
    serving `pages`. Returns the step's result and the ids it deleted."""
    if shutil.which("gh") is None:
        pytest.skip("needs gh on PATH, which the ubuntu-latest and windows-latest runners carry")
    shim = _jq_first_on_path(tmp_path, engine)
    script = tmp_path / "purge.sh"
    script.write_text(_purge_step(), encoding="utf-8", newline="\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ArtifactsApi)
    server.pages, server.deleted = pages, []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
    env.update(HTTP_PROXY=f"http://127.0.0.1:{server.server_address[1]}", GH_HOST="github.localhost",
               GH_TOKEN="x", GH_CONFIG_DIR=str(tmp_path / "gh"), DRY_RUN="false", TARGET_REPO="owner/repo",
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
               PATH=os.pathsep.join([str(shim), env["PATH"]]))
    try:
        result = subprocess.run([_bash(), "--noprofile", "--norc", "-e", script.as_posix()], env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=HANG_SECONDS)
    finally:
        server.shutdown()
        server.server_close()
    return result, sorted(server.deleted)


@pytest.mark.parametrize("engine", ["jq", "jq-1.6"])
@pytest.mark.parametrize("name", list(_LISTINGS))
def test_the_purge_deletes_exactly_the_expired_artifacts(tmp_path, name, engine):
    """An `expired` that is null or absent reads as live and is kept, and a
    size that is null or absent counts as nothing."""
    pages, expected = _LISTINGS[name]

    result, deleted = _purge(tmp_path, engine, pages)

    assert (result.returncode, deleted) == (0, expected), result.stdout + result.stderr
    assert f"deleted {len(expected)}, failed 0" in result.stdout, result.stdout


@pytest.mark.parametrize("engine", ["jq", "jq-1.6"])
def test_a_page_that_fails_fails_the_job_before_any_delete(tmp_path, engine):
    """A listing it never finished would report a clean quota and delete only
    what page 1 held."""
    result, deleted = _purge(tmp_path, engine, [[_artifact(1)], 502])

    assert (result.returncode != 0, deleted) == (True, []), result.stdout + result.stderr
    assert "fake 502" in result.stderr, result.stderr
