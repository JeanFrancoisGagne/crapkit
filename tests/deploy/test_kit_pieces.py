"""One fast test per kit piece, run in every job beside the isolation tests.

Each asserts through what a cell will use the piece for: the mirror answers a
GitHub URL, the index answers pip with PyPI's headers, the shim records a
start and still runs crapkit, the MCP clients reach all twelve tools, the
model stubs answer a harness-shaped request, and every repo template builds.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.request
from pathlib import Path

import pytest

from kit import cells, docsnip, gitmirror, pyindex, repos, sandbox, shim, stub_anthropic, stub_openai, wheels
from kit.mcp_client import McpClient
from kit.transcript import Step, Transcript

pytestmark = pytest.mark.kit

WINDOWS = os.name == "nt"
NODE_CLIENT = Path(__file__).resolve().parent / "kit" / "mcp_node_client.mjs"
TOOLS = 12


def venv_crapkit(box, spec: str = "crapkit") -> Path:
    """crapkit installed into a fresh venv; the launcher's path."""
    venv = box.root / "venv"
    box.run([box.toolchain.python("3.12"), "-m", "venv", str(venv)], expect=0)
    scripts = venv / ("Scripts" if WINDOWS else "bin")
    box.run([str(scripts / "python"), "-m", "pip", "install", "-q", spec], expect=0)
    return scripts / ("crapkit.exe" if WINDOWS else "crapkit")


# --- transcript and cells -------------------------------------------------------------

def test_a_transcript_writes_every_step_as_json_and_text(tmp_path):
    record = Transcript("tests/deploy/test_x.py::test_y[a b]")
    record.add(Step(["crapkit", "--version"], "/r", 0, "crapkit 0.8.1\n", "", 0.2))
    record.note("installed from the wheelhouse")
    text = record.write(tmp_path)

    assert text.name == "tests_deploy_test_x.py_test_y_a_b.txt"
    assert "$ crapkit --version    [cwd /r, exit 0, 0.2s]" in text.read_text(encoding="utf-8")
    assert json.loads(text.with_suffix(".json").read_text(encoding="utf-8"))["steps"][0]["exit"] == 0


def test_a_cell_carries_its_cadence_os_and_image_markers():
    meta = {"id": "lin-x", "cadence": "weekly+published", "os": "linux", "image": "full", "real_cli": True}
    names = [mark.name for mark in cells.markers(meta)]

    assert names == ["deploy_cell", "weekly", "published", "linux", "real_cli", "image_full"]
    with pytest.raises(ValueError, match="cadence"):
        cells.markers({**meta, "cadence": "hourly"})


def test_a_cells_junit_properties_name_the_image_and_the_toolchain(monkeypatch):
    monkeypatch.setenv("CRAPKIT_DEPLOY_IMAGE_DIGEST", "sha256:abc")
    pairs = dict(cells.properties({"id": "lin-x", "channel": "pip", "real_cli": False}))

    assert pairs["cell_id"] == "lin-x" and pairs["image_digest"] == "sha256:abc"
    assert len(pairs["toolchain_hash"]) == 64 and "cell_real_cli" not in pairs


# --- wheels and docs --------------------------------------------------------------------

def test_n_minus_1_and_the_old_releases_are_in_the_wheelhouse(candidate, toolchain):
    releases = wheels.releases()

    assert wheels.n_minus_1() == releases[-1]
    assert {"0.4.0", "0.7.6"} <= set(releases)
    assert wheels.release_wheel(toolchain["wheelhouse"], wheels.n_minus_1()).exists()
    assert wheels._key(candidate.version) > wheels._key(wheels.n_minus_1())
    assert candidate.wheel.exists() and candidate.sdist.exists()


def test_docsnip_reads_the_stamped_readme_and_names_a_moved_fence(candidate):
    start = docsnip.fence("README.md", "The 60-second start")

    assert docsnip.commands(start)[0] == "pip install crapkit"
    assert f"crapkit {candidate.version}\n" in (candidate.staged / "README.md").read_text(encoding="utf-8")
    with pytest.raises(docsnip.DocSnipError, match="README.md > The 90-second start"):
        docsnip.fence("README.md", "The 90-second start")


# --- the mirror and the index -------------------------------------------------------------

def test_the_github_url_clones_the_mirror_and_a_release_moves_main(box, candidate):
    mirror = gitmirror.make(box)
    mirror.publish(candidate.staged, candidate.version)
    listed = box.run(["git", "ls-remote", "https://github.com/JeanFrancoisGagne/crapkit.git"], expect=0).stdout

    assert f"refs/tags/v{candidate.version}" in listed
    assert mirror.head("main") == mirror.git("rev-parse", f"v{candidate.version}^{{commit}}")
    old = mirror.release_to("0.7.6")
    clone = box.root / "clone"
    box.run(["git", "clone", "-q", "git@github.com:JeanFrancoisGagne/crapkit", str(clone)], expect=0)
    assert box.run(["git", "rev-parse", "HEAD"], cwd=clone, expect=0).stdout.strip() == old


def test_the_index_serves_pip_with_pypis_cache_headers(box, toolchain, candidate):
    with pyindex.serve([Path(toolchain["wheelhouse"]), candidate.dist]) as index:
        with urllib.request.urlopen(index.simple + "crapkit/") as page:
            headers, body = page.headers, page.read().decode("utf-8")
        target = box.root / "downloaded"
        box.run([box.toolchain.python("3.12"), "-m", "pip", "download", "--no-deps", "-q", "-d", str(target),
                 "--index-url", index.simple, f"crapkit=={wheels.n_minus_1()}"],
                env={"PIP_CONFIG_FILE": os.devnull}, expect=0)

    assert headers["Cache-Control"] == pyindex.PAGE_CACHE
    assert f"crapkit-{candidate.version}-py3-none-any.whl#sha256=" in body
    assert [path.name for path in target.iterdir()] == [f"crapkit-{wheels.n_minus_1()}-py3-none-any.whl"]


# --- the shim and the MCP clients -----------------------------------------------------------

def test_the_shim_records_a_start_and_still_runs_crapkit(box, candidate):
    real = venv_crapkit(box)
    box.prepend_path(shim.install(box, real))
    version = box.run(["crapkit", "--version"], expect=0)
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=box.root) as client:
        client.initialize()
        client.close()
    starts = shim.starts(box)

    assert candidate.version in version.stdout
    assert Path(box.which("crapkit")).parent == box.root / "shim-bin"
    assert [start["argv"][1:] for start in starts] == [["--version"], ["mcp"]]
    assert '"method": "initialize"' in starts[1]["first_line"] and starts[1]["exit"] == 0
    assert starts[0]["cwd"] == str(box.root) and starts[0]["env"]["HOME"] == box.env["HOME"]


def test_the_python_client_reaches_every_tool(box, candidate):
    launcher = venv_crapkit(box)
    with McpClient.in_box(box, [str(launcher), "mcp"], cwd=box.root) as client:
        info = client.initialize("2025-06-18")
        names = [tool["name"] for tool in client.tools()]
        pong = client.request("ping")
        answer = client.call("get_next_item", {})

    assert info["serverInfo"]["name"] == "crapkit" and info["protocolVersion"] == "2025-06-18"
    assert len(names) == TOOLS and "get_next_item" in names
    assert pong == {}
    assert answer["isError"] and "crapkit" in answer["content"][0]["text"]


def node_fixtures(box) -> Path:
    """The npm-fixtures install, offline, holding each MCP SDK minor."""
    project = box.root / "node-fixtures"
    project.mkdir()
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(Path(box.toolchain["npm_fixtures"]) / name, project / name)
    box.run(["npm", "ci", "--offline", "--ignore-scripts"], cwd=project, expect=0)
    return project


@pytest.mark.parametrize("sdk", ["sdk-1-12", "sdk-1-29"])
def test_the_node_client_reaches_every_tool_through_the_sdk_a_harness_ships(box, candidate, sdk):
    launcher = venv_crapkit(box)
    fixtures = node_fixtures(box)
    step = box.run(["node", str(NODE_CLIENT), "--fixtures", str(fixtures), "--sdk", sdk, "--command", str(launcher),
                    "--arg", "mcp", "--cwd", str(box.root), "--env", "inherit"], expect=0)
    out = json.loads(step.stdout)

    assert out["serverInfo"]["name"] == "crapkit" and len(out["tools"]) == TOOLS


# --- the model stubs ---------------------------------------------------------------------

def _post(url: str, body: dict) -> bytes:
    request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return response.read()


def test_the_anthropic_stub_answers_a_tool_call_then_text_and_keeps_each_body():
    script = [{"tool_use": {"name": "Read", "input": {"file_path": "a.py"}}}, {"text": "done"}]
    with stub_anthropic.serve(script) as stub:
        first = json.loads(_post(stub.url + "/v1/messages", {"model": "m", "messages": []}))
        streamed = _post(stub.url + "/v1/messages", {"model": "m", "stream": True, "messages": [{"k": 1}]})

    assert first["stop_reason"] == "tool_use" and first["content"][0]["name"] == "Read"
    assert b"event: message_stop" in streamed and b'"text_delta"' in streamed
    assert [body.get("stream") for body in stub.bodies()] == [None, True]


def test_the_openai_stub_answers_chat_and_responses():
    script = [{"tool_call": {"name": "get_next_item", "arguments": {}}}, {"text": "done"}]
    with stub_openai.serve(script) as stub:
        chat = json.loads(_post(stub.url + "/v1/chat/completions", {"model": "m", "messages": []}))
        streamed = _post(stub.url + "/v1/responses", {"model": "m", "stream": True, "input": "hi"})

    assert chat["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_next_item"
    assert b"response.completed" in streamed and b'"output_text"' in streamed
    assert len(stub.bodies()) == 2


# --- repo templates -----------------------------------------------------------------------

EXPECTED = {"py-pytest": "calc/grade.py", "ts-vitest-only": "package-lock.json", "jest": "package-lock.json",
            "go-rust-shell": "go/main.go", "uv-project": "uv.lock", "poetry-project": "pyproject.toml",
            "pdm-project": "pyproject.toml", "pipenv-project": "Pipfile", "dot-venv": ".venv",
            "subdir-root": "packages/api/calc/grade.py", "submodule": "vendor/lib/go/main.go",
            "zero-commit": "calc/grade.py", "not-git": "calc/grade.py", "brownfield": "calc/legacy_4.py",
            "consumer": "calc/grade.py"}


@pytest.mark.parametrize("name", sorted(repos.TEMPLATES))
def test_every_template_builds_and_copies(box, templates, name):
    repo = repos.checkout(box, name, cache=templates)

    assert (repo / EXPECTED[name]).exists()
    assert (repo / ".git").exists() is (name != "not-git")


def test_the_file_lock_serves_one_holder_at_a_time(tmp_path):
    held, overlaps = [], []

    def hold():
        with repos.file_lock(tmp_path / "t.lock"):
            overlaps.append(len(held))
            held.append(1)
            held.pop()
    threads = [threading.Thread(target=hold) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert overlaps == [0, 0, 0, 0]


def test_a_sandbox_can_be_made_twice_from_one_toolchain(tmp_path, toolchain):
    first = sandbox.make(tmp_path / "a", Transcript("a"), toolchain=toolchain)
    second = sandbox.make(tmp_path / "b", Transcript("b"), toolchain=toolchain)

    assert first.home != second.home and first.env["PATH"] == second.env["PATH"]
