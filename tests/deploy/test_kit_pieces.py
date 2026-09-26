"""One fast test per kit piece, run in every job beside the isolation tests.

Each asserts through what a cell will use the piece for: the mirror answers a
GitHub URL, the index answers pip with PyPI's headers, the shim records a
start and still runs crapkit, the MCP clients reach all twelve tools, the
model stubs answer a harness-shaped request, and every repo template builds.
"""
from __future__ import annotations

import ast
import json
import os
import re
import threading
import urllib.request
from pathlib import Path

import pytest

from kit import cells, docsnip, gitmirror, hooks_rules, profiles, pyindex, repos, sandbox, shim, stub_anthropic, stub_openai, wheels
from kit.mcp_client import McpClient
from kit.transcript import Step, Transcript

pytestmark = pytest.mark.kit

WINDOWS = os.name == "nt"
KIT = Path(__file__).resolve().parent / "kit"
NODE_CLIENT = KIT / "mcp_node_client.mjs"
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


def test_a_crashed_or_signalled_step_says_so():
    crashed = Step(["pipx", "install", "crapkit"], "C:\\r", 3221226505, "", "", 5.3)
    killed = Step(["crapkit", "mcp"], "/r", -9, "", "", 1.0)

    assert "exit 3221226505 (0xC0000409, a crash)" in crashed.text()
    assert "exit -9 (signal 9)" in killed.text()


def test_a_cell_carries_its_cadence_os_and_image_markers():
    meta = {"id": "lin-x", "cadence": "weekly+published", "os": "linux", "image": "full", "real_cli": True}
    names = [mark.name for mark in cells.markers(meta)]

    assert names == ["deploy_cell", "weekly", "published", "linux", "real_cli", "image_full"]
    with pytest.raises(ValueError, match="cadence"):
        cells.markers({**meta, "cadence": "hourly"})


def test_a_cell_or_packet_filter_runs_only_what_it_names():
    meta = {"id": "lin-x", "packet": "deploy-channels"}

    assert cells.selected(None, [], None) and cells.selected(None, [], "deploy-kit")
    assert not cells.selected(None, ["lin-x"], None) and not cells.selected(None, [], "deploy-channels")
    assert cells.selected(meta, ["lin-x"], None) and not cells.selected(meta, ["lin-y"], None)
    assert cells.selected(meta, [], "deploy-channels") and not cells.selected(meta, [], "deploy-git")


def test_a_cells_junit_properties_name_the_image_and_the_toolchain(monkeypatch):
    monkeypatch.setenv("CRAPKIT_DEPLOY_IMAGE_DIGEST", "sha256:abc")
    pairs = dict(cells.properties({"id": "lin-x", "channel": "pip", "real_cli": False}))

    assert pairs["cell_id"] == "lin-x" and pairs["image_digest"] == "sha256:abc"
    assert len(pairs["toolchain_hash"]) == 64 and pairs["cell_real_cli"] == "False"
    assert pairs["cell_image"] == "none" and "cell_nonblocking" not in pairs


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

def test_every_spelling_of_the_github_url_reaches_the_mirror(box):
    mirror = gitmirror.make(box)
    heads = {url: box.run(["git", "ls-remote", url, "refs/heads/main"], expect=0).stdout.split()[:1]
             for url in gitmirror.UPSTREAM}

    assert heads == {url: [mirror.head("main")] for url in gitmirror.UPSTREAM}
    assert "https://github.com/JeanFrancoisGagne/crapkit" in heads and "git@github.com:JeanFrancoisGagne/crapkit" in heads


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

    assert index.simple.startswith("http://127.0.0.1:")
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
    # The process that started crapkit, through the .exe launcher on Windows.
    assert [start["ppid"] for start in starts] == [os.getpid(), os.getpid()]


def test_the_shim_is_a_real_executable_the_way_a_pip_install_is(box, candidate):
    shim_bin = shim.install(box, venv_crapkit(box))
    launcher = shim_bin / shim.launcher_name()

    assert [path.name for path in shim_bin.iterdir()] == [launcher.name]
    assert launcher.read_bytes()[:2] == (b"MZ" if WINDOWS else b"#!")


def test_the_shim_names_the_harness_behind_a_windows_launcher():
    from kit.shim_pkg.crapkit_shim import parents
    # this interpreter <- the venv's python.exe <- crapkit.exe <- the harness
    table = {40: (30, "python.exe"), 30: (20, "crapkit.exe"), 20: (10, "node.exe"), 10: (1, "cmd.exe")}

    assert parents(table, 40, r"C:\box\shim-bin\crapkit.EXE") == {"ppid": 20, "launcher_pid": 30}
    assert parents(table, 20, "crapkit") == {"ppid": 20}
    assert parents({}, 7, "crapkit") == {"ppid": 7}


def test_claude_code_spawns_the_shell_form_hook_on_every_edit_and_an_exec_form_one_only_where_its_if_admits(tmp_path):
    """The hook contract cells spawn what Claude Code spawns for each edit. The
    one shell-form handler runs on any Edit or Write, since claude-hook screens
    the file itself; a 0.8.0-style exec-form handler runs only for the edit its
    `if` admits. Both spawn `crapkit claude-hook --protocol 1`."""
    claude = profiles.load("claude-code")
    shell_form = {"type": "command", "command": "crapkit claude-hook --protocol 1"}
    exec_form = {"type": "command", "command": "crapkit", "args": ["claude-hook", "--protocol", "1"], "if": "Edit(*.py)"}
    edits = [("Edit", "calc/big.py"), ("Edit", "notes.md")]
    (tmp_path / "hooks").mkdir()
    for entry, admitted in ((shell_form, edits), (exec_form, edits[:1])):
        text = json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [entry]}]}})
        (tmp_path / "hooks" / "hooks.json").write_text(text, encoding="utf-8")
        assert len(hooks_rules.parse(text)) == 1
        assert hooks_rules.spawns(claude, tmp_path, edits) == [
            (tool, path, ["crapkit", "claude-hook", "--protocol", "1"]) for tool, path in admitted]


def test_a_shell_form_hook_spawns_as_its_shell_splits_it_and_each_harness_hears_it_where_it_reads():
    """The plugin's hook is one shell-form command line. A harness that keeps
    only `command` still passes its arguments, each harness sends its own
    PostToolUse shape, and each reads the advisory in one place: Claude Code
    from exit 2's stderr, Cursor, VS Code and Copilot CLI from JSON on exit 0."""
    shell_form = hooks_rules.Handler("PostToolUse", "Edit|Write", {"type": "command",
                                                                  "command": "crapkit claude-hook --protocol 1"})
    claude, cursor, copilot = (profiles.load(key) for key in ("claude-code", "cursor", "copilot-cli"))
    assert hooks_rules.argv(cursor, shell_form) == ["crapkit", "claude-hook", "--protocol", "1"]
    repo = Path("/repo")
    assert hooks_rules.payload(repo, "a.py", profile=copilot)["tool_input"]["path"] == str(repo / "a.py")
    assert hooks_rules.payload(repo, "a.py", profile=cursor)["hook_event_name"] == "postToolUse"
    assert hooks_rules.payload(repo, "a.py", profile=claude)["tool_input"]["file_path"] == str(repo / "a.py")
    advisory = json.dumps({"additionalContext": "crapkit advisory: grade ccn 9 > 6"})
    on_stdout = Step(["crapkit"], "/repo", 0, advisory, "", 0.1)
    on_stderr = Step(["crapkit"], "/repo", 2, "", "crapkit advisory: grade ccn 9 > 6", 0.1)
    assert [hooks_rules.heard(cursor, on_stdout), hooks_rules.heard(cursor, on_stderr)] == [True, False]
    assert [hooks_rules.heard(claude, on_stderr), hooks_rules.heard(claude, on_stdout)] == [True, False]


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


def test_the_npm_fixtures_install_once_per_session(tmp_path, toolchain, templates):
    first = sandbox.make(tmp_path / "a", Transcript("a"), toolchain=toolchain)
    second = sandbox.make(tmp_path / "b", Transcript("b"), toolchain=toolchain)
    installed = repos.npm_fixtures(first, templates)

    assert repos.npm_fixtures(second, templates) == installed
    assert (installed / "node_modules" / "sdk-1-12" / "package.json").exists()
    assert [step.argv[1] for step in first.transcript.steps] in ([], ["ci"])
    assert second.transcript.steps == []


@pytest.mark.parametrize("sdk", ["sdk-1-12", "sdk-1-23", "sdk-1-25", "sdk-1-29"])
def test_the_node_client_reaches_every_tool_through_the_sdk_a_harness_ships(box, candidate, templates, sdk):
    launcher = venv_crapkit(box)
    fixtures = repos.npm_fixtures(box, templates)
    step = box.run(["node", str(NODE_CLIENT), "--fixtures", str(fixtures), "--sdk", sdk, "--command", str(launcher),
                    "--arg", "mcp", "--cwd", str(box.root), "--env", "inherit"], expect=0)
    out = json.loads(step.stdout)

    assert out["serverInfo"]["name"] == "crapkit" and len(out["tools"]) == TOOLS


# A server that answers late, and first sends a notification and a reply to
# another id: the client must wait for its own reply, not for a timer.
SLOW_SERVER = '''import json, sys, time
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    time.sleep(1)
    for other in ({"method": "notifications/message", "params": {}}, {"id": 999, "result": {}},
                  {"id": message["id"], "result": {"echo": message["method"]}}):
        print(json.dumps({"jsonrpc": "2.0", **other}), flush=True)
'''


def test_the_python_client_waits_for_its_own_reply_and_never_on_a_timer(box):
    server = box.root / "slow_server.py"
    server.write_text(SLOW_SERVER, encoding="utf-8")
    with McpClient.in_box(box, [box.toolchain.python("3.12"), str(server)], cwd=box.root) as client:
        answers = [client.request("ping"), client.request("tools/list")]
        code = client.close()

    assert answers == [{"echo": "ping"}, {"echo": "tools/list"}] and code == 0
    assert "sleep(" not in (KIT / "mcp_client.py").read_text(encoding="utf-8")
    assert "setTimeout" not in NODE_CLIENT.read_text(encoding="utf-8")


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


def test_a_template_builds_once_and_every_later_sandbox_copies_it(tmp_path, toolchain, templates):
    first = sandbox.make(tmp_path / "a", Transcript("a"), toolchain=toolchain)
    second = sandbox.make(tmp_path / "b", Transcript("b"), toolchain=toolchain)
    one = repos.checkout(first, "brownfield", cache=templates)
    two = repos.checkout(second, "brownfield", cache=templates)

    assert second.transcript.steps == []
    heads = [box.run(["git", "rev-parse", "HEAD"], cwd=repo, expect=0).stdout for box, repo in
             ((first, one), (second, two))]
    assert heads[0] == heads[1] and one != two


# Three processes, as three xdist workers would, each holding the lock while
# it writes "in" then "out": interleaved lines mean two held it at once.
LOCK_HOLDER = '''import sys, time
from pathlib import Path
from kit import repos
with repos.file_lock(Path(sys.argv[1])):
    with open(sys.argv[2], "a") as log:
        print("in", file=log)
    time.sleep(.3)
    with open(sys.argv[2], "a") as log:
        print("out", file=log)
'''
THREE_HOLDERS = '''import subprocess, sys
children = [subprocess.Popen([sys.executable, *sys.argv[1:]]) for _ in range(3)]
sys.exit(max(child.wait() for child in children))
'''


def test_the_file_lock_serves_one_process_at_a_time(box, toolchain):
    holder, driver = box.root / "holder.py", box.root / "driver.py"
    holder.write_text(LOCK_HOLDER, encoding="utf-8")
    driver.write_text(THREE_HOLDERS, encoding="utf-8")
    log = box.root / "held.log"
    kit_path = os.pathsep.join([str(KIT.parent), str(KIT.parent.parent)])
    box.run([toolchain["runner_python"], str(driver), str(holder), str(box.root / "t.lock"), str(log)],
            env={"PYTHONPATH": kit_path}, expect=0)

    assert log.read_text(encoding="utf-8").split() == ["in", "out"] * 3


def test_a_lock_held_past_the_bound_fails_naming_the_lock(tmp_path):
    with repos.file_lock(tmp_path / "t.lock"):
        with pytest.raises(AssertionError, match=r"never got the lock on .*t\.lock within 0 s"):
            with repos.file_lock(tmp_path / "t.lock", bound=.2):
                pass


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


# --- the kit names its pieces -----------------------------------------------------------

# A name in the first column of kit/__init__.py's list: `  sandbox       an environment ...`,
# `  stub_anthropic, stub_openai` or `  stub_gh/` on a line of its own.
FIRST_COLUMN = re.compile(r"^  (\w[\w/, ]*?)(?: {2,}|$)", re.MULTILINE)


def kit_pieces(kit: Path = KIT) -> list[str]:
    """The kit's modules and resource folders."""
    return sorted({path.stem for pattern in ("*.py", "*/") for path in kit.glob(pattern)} - {"__init__", "__pycache__"})


def listed_pieces(kit: Path = KIT) -> set[str]:
    """The names in the first column of kit/__init__.py's list, read with its indent kept."""
    text = ast.get_docstring(ast.parse((kit / "__init__.py").read_text(encoding="utf-8")), clean=False) or ""
    return {name.strip(" /") for column in FIRST_COLUMN.findall(text) for name in column.split(",")}


def unlisted_pieces(kit: Path = KIT) -> list[str]:
    """The pieces kit/__init__.py's list does not name."""
    listed = listed_pieces(kit)
    return [piece for piece in kit_pieces(kit) if piece not in listed]


def test_kit_init_names_every_module_and_folder_the_kit_holds():
    unlisted = unlisted_pieces()

    assert unlisted == [], (f"tests/deploy/kit/__init__.py does not name {', '.join(unlisted)}: add a line to its "
                            "list saying what each is for, which is where tools/deploy/README.md sends a cell's author")


VARIABLE = re.compile(r"CRAPKIT_DEPLOY_[A-Z_]+")


def unlisted_variables(kit: Path = KIT) -> list[str]:
    """The CRAPKIT_DEPLOY_* variables the kit and its conftest name that kit/__init__.py's list leaves out."""
    named = {name for path in [*kit.glob("*.py"), kit.parent / "conftest.py"]
             for name in VARIABLE.findall(path.read_text(encoding="utf-8"))}
    return sorted(named - listed_pieces(kit))


def test_kit_init_names_every_variable_the_kit_reads():
    unlisted = unlisted_variables()

    assert unlisted == [], (f"tests/deploy/kit/__init__.py does not name {', '.join(unlisted)}: add each to its list "
                            "of the environment run.py hands the kit, with what it holds and what happens when unset")


def test_a_piece_is_listed_only_by_its_name_in_the_first_column(tmp_path):
    listed = '"""The kit.\n\n  sandbox       the state a cell runs in\n  stub_a, stub_b\n                two stubs\n  stub_gh/\n"""\n'
    (tmp_path / "__init__.py").write_text(listed, encoding="utf-8")
    for name in ("sandbox.py", "state.py", "stub_a.py", "stub_b.py", "stub_gh/gh", "shim_pkg/x.py", "__pycache__/x.pyc",
                 "client.mjs"):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("", encoding="utf-8")

    assert unlisted_pieces(tmp_path) == ["shim_pkg", "state"]
