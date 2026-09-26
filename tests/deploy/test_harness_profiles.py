"""lin-profiles-sim and win-profiles-sim: every harness profile, one cell each.

A cell does what a user of that harness does, with the harness itself
simulated from its profile: install crapkit from the candidate (the README's
pip line), measure a repo, paste the config docs/harnesses.md gives for the
harness into the file the harness reads, and read it back the way the harness
reads it. Then it starts `crapkit mcp` the way the profile's [spawn] says (the
working directory, the environment, the command lookup, a shell in between),
sends what [initialize] says the harness sends first, lists the tools and
calls two of them. A profile whose harness speaks through the MCP TypeScript
SDK is driven a second time through that SDK's own version, so its schema
checks are real. Aider has no MCP client: its cell runs the lint command.

Each cell records, as the JUnit property `evidence_inferred`, the profile
fields it relied on that nobody has observed, so a run's summary can count
the cells that pass only on inferred values.
"""
from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from kit import profiles, wheels, writers
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-harnesses"
KEYS = profiles.keys()
NODE_CLIENT = Path(__file__).resolve().parent / "kit" / "mcp_node_client.mjs"
TOOLS = 12
# docs/agent-json.md "MCP server": the revisions crapkit speaks back verbatim;
# any other offer gets the newest of them.
SPOKEN = ("2025-06-18", "2025-03-26", "2024-11-05")
NEWEST = SPOKEN[0]
# Each spawn and initialize field a sim cell acts on.
USED = ["spawn.cwd", "spawn.env", "spawn.resolve", "spawn.windows", "initialize.client", "initialize.sdk",
        "initialize.protocol", "initialize.discover_first"]
# Cells that fail on a crapkit bug a user would hit: (cell id, profile key) -> reason.
BUGS: dict[tuple[str, str], str] = {}


def params(cell_id: str) -> list:
    marks = {key: [pytest.mark.xfail(strict=True, reason=reason)]
             for (bug_cell, key), reason in BUGS.items() if bug_cell == cell_id}
    return [pytest.param(key, id=key, marks=marks.get(key, [])) for key in KEYS]


# --- the profile set ---------------------------------------------------------------------

def pins() -> dict:
    return tomllib.loads((wheels.SRC / "tools" / "deploy" / "pins.toml").read_text(encoding="utf-8"))


def pinned_version(real_cli: dict, pinned: dict) -> str:
    key = real_cli["pins_key"]
    if key in ("vscode", "zed"):
        return pinned["binary"][f"{key}-linux-x64"]["version"]
    return pinned["harness"][key]["version"] if key else real_cli["version"]


def assert_readable(profile) -> None:
    """The profile holds every section and tag, and its [real_cli] version is pins.toml's."""
    assert profiles.problems(profile) == []
    assert profile.real_cli["version"] == pinned_version(profile.real_cli, pins())
    names = [other.name for other in profiles.all_profiles()]
    assert len(KEYS) == 27 and names.count(profile.name) == 1


# --- one sim ----------------------------------------------------------------------------

def adopted_repo(box, templates) -> Path:
    return profiles.measured_repo(box, templates)


def configured_server(profile, box, repo: Path) -> writers.Server:
    text, source = writers.doc_config(profile)
    box.transcript.note(f"config from {source}")
    path = writers.write(profile, text, box, repo)
    box.transcript.note(f"{profile.name} reads {path}:\n{path.read_text(encoding='utf-8')}")
    return writers.parse(profile, path)


def expected_protocol(offer: str) -> str:
    return offer if offer in SPOKEN else NEWEST


def discover(client: McpClient) -> dict:
    """The `server/discover` a 2026-07-28 client sends before initialize."""
    request_id = next(client.ids)
    client.send({"jsonrpc": "2.0", "id": request_id, "method": "server/discover",
                 "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}})
    return client.reply(request_id)


def handshake(client: McpClient, profile) -> dict:
    init = profile.initialize
    if init["discover_first"]:
        assert discover(client)["error"]["code"] == -32601
    return client.initialize(init["protocol"], client=init["client_name"] or "sim")


def answer_of(result: dict, revision: str) -> dict:
    """A call's object from its text. structuredContent carries it too on
    2025-06-18 and is absent on an older revision, which does not define it."""
    assert ("structuredContent" in result) == (revision == NEWEST), sorted(result)
    return json.loads(result["content"][0]["text"])


def assert_calls(client: McpClient, profile, revision: str) -> None:
    item = client.call("get_next_item", {})
    assert not item["isError"], item["content"][0]["text"]
    assert answer_of(item, revision)["item"]["path"] == "calc/grade.py"
    worklist = client.call("list_worklist", {"top": 50})
    text = worklist["content"][0]["text"]
    assert not worklist["isError"] and json.loads(text)
    cap = profile.limits["output_tokens"]
    assert not cap or len(text) / 4 <= cap, f"list_worklist top 50 is {len(text)} chars, past {cap} tokens"


def drive_python(profile, started: profiles.Launch, box, candidate) -> None:
    box.transcript.note(f"{started.rule}: {started.argv} in {started.cwd}, env {sorted(started.env)}")
    with McpClient(started.argv, cwd=started.cwd, env=started.env, transcript=box.transcript) as client:
        info = handshake(client, profile)
        assert info["protocolVersion"] == expected_protocol(profile.initialize["protocol"])
        assert info["serverInfo"] == {"name": "crapkit", "version": candidate.version}
        assert len(client.tools()) == TOOLS
        assert_calls(client, profile, info["protocolVersion"])


def drive_sdk(profile, server: writers.Server, started: profiles.Launch, box, templates, candidate) -> None:
    """The same server through the TypeScript SDK version the harness ships."""
    fixtures = profiles.sdk_fixtures(box, templates)
    env_mode = "default" if profile.spawn["env"] == "sdk-default" else "inherit"
    argv = [box.toolchain["node"], str(NODE_CLIENT), "--fixtures", str(fixtures), "--sdk", profile.initialize["sdk"],
            "--command", server.command, "--cwd", str(started.cwd), "--env", env_mode,
            "--call", "get_next_item", "--arguments", "{}"]
    for arg in server.args:
        argv += ["--arg", arg]
    step = box.run(argv, env=None if env_mode == "default" else started.env, expect=0)
    out = json.loads(step.stdout)
    assert out["serverInfo"]["version"] == candidate.version and len(out["tools"]) == TOOLS
    assert not out["call"]["isError"]


def lint_file(box, server: writers.Server, repo: Path, relative: str):
    return box.run([*server.argv, relative], cwd=repo)


def drive_lint(server: writers.Server, box, repo: Path) -> None:
    """Aider appends the edited file to lint-cmd and reads any non-zero exit as lint errors."""
    (repo / "calc" / "clean.py").write_text("def double(x):\n    return 2 * x\n", encoding="utf-8")
    assert lint_file(box, server, repo, "calc/clean.py").exit == 0
    grade = repo / "calc" / "grade.py"
    grade.write_text(grade.read_text(encoding="utf-8").replace(
        '    return "D"', '    if attempts > 5:\n        return "E"\n    return "D"'), encoding="utf-8")
    assert lint_file(box, server, repo, "calc/grade.py").exit != 0


def record_inferred(record_property, profile) -> None:
    inferred = profile.inferred(USED)
    record_property("evidence_inferred", ",".join(inferred) or "none")


def sim(profile_key: str, box, templates, candidate, record_property) -> None:
    profile = profiles.load(profile_key)
    assert_readable(profile)
    record_inferred(record_property, profile)
    repo = adopted_repo(box, templates)
    server = configured_server(profile, box, repo)
    if not profile.mcp:
        return drive_lint(server, box, repo)
    assert server.command == "crapkit"
    started = profiles.launch(profile, server, box, repo)
    drive_python(profile, started, box, candidate)
    if profile.initialize["client"] == "ts-sdk":
        drive_sdk(profile, server, started, box, templates, candidate)


# --- the cells --------------------------------------------------------------------------

@pytest.mark.parametrize("profile_key", params("lin-profiles-sim"))
@cell("lin-profiles-sim", channel="config from docs/harnesses.md", harness="27 profiles",
      scenario="fresh: per profile write, parse, spawn per rule, initialize, tools/list, one call; "
               "TS-SDK profiles through their exact SDK alias",
      use_cases="MCP clients", os="linux", image="core", cadence="push", real_cli=False)
def test_lin_profiles_sim(profile_key, box, templates, candidate, record_property):
    sim(profile_key, box, templates, candidate, record_property)


# --- Windows: the PowerShell profile Zed's spawn runs ------------------------------------------

POWERSHELL = ["powershell.exe", "-NoLogo", "-Command"]
PROFILE_LINE = "crapkit-deploy: this PowerShell profile prints"


def profile_path(env: dict[str, str]) -> Path:
    done = subprocess.run([*POWERSHELL, "$PROFILE"], capture_output=True, text=True, env=env, check=True)
    return Path(done.stdout.strip())


def zed_on_windows(profile, box, templates, candidate) -> None:
    """Zed starts a context server through `powershell -C` without -NoProfile,
    so what the user's profile prints lands on the server's stdout first. The
    cell writes the runner's own profile, so it runs only where CI=true."""
    if os.environ.get("CI") != "true":
        pytest.skip("writes the machine's real PowerShell profile; runs only on a CI runner (CI=true)")
    repo = adopted_repo(box, templates)
    started = profiles.launch(profile, configured_server(profile, box, repo), box, repo)
    path = profile_path(started.env)
    assert path == profile_path(dict(os.environ)), "the spawn's $PROFILE is not the runner's profile"
    saved = path.read_bytes() if path.exists() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Write-Output '{PROFILE_LINE}'\n", encoding="utf-8")
        assert_profile_line_first(started, box, candidate)
    finally:
        path.write_bytes(saved) if saved is not None else path.unlink(missing_ok=True)


def assert_profile_line_first(started: profiles.Launch, box, candidate) -> None:
    with McpClient(started.argv, cwd=started.cwd, env=started.env, transcript=box.transcript) as client:
        client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": NEWEST, "capabilities": {}, "clientInfo": {"name": "Zed"}}})
        first = client.replies.get(timeout=120)
        assert first.strip() == PROFILE_LINE
        assert client.reply(1)["result"]["serverInfo"]["version"] == candidate.version


@pytest.mark.parametrize("profile_key", params("win-profiles-sim"))
@cell("win-profiles-sim", channel="per-harness config", harness="27 profiles",
      scenario="fresh: cross-spawn, cmd /c, powershell -C with a printing profile (CI only), CreateProcess "
               "shadow, shell:true", use_cases="MCP clients", os="windows", image=None, cadence="push",
      real_cli=False)
def test_win_profiles_sim(profile_key, box, templates, candidate, record_property):
    profile = profiles.load(profile_key)
    if profile.spawn["windows"] == "none":
        pytest.skip(f"{profile.name} has no Windows build")
    if profile.spawn["windows"] == "powershell-c":
        record_inferred(record_property, profile)
        return zed_on_windows(profile, box, templates, candidate)
    sim(profile_key, box, templates, candidate, record_property)


# --- tools/deploy/calibrate.py, without a harness ------------------------------------------------
# The kit's own tests of the comparison calibrate-all and calibrate.py share.
# They need no sandbox and run in every job.

calibrate = profiles.calibrate_module()
PROFILE = {"spawn": {"cwd": "workspace", "env": "inherit"},
           "initialize": {"protocol": "2025-06-18", "discover_first": False, "client_name": "one"},
           "limits": {"tool_prefix": "crapkit__"}, "real_cli": {"command": "one", "image": "full", "version": "1.0"}}


def observation(**changes) -> dict:
    seen = {section: dict(fields) for section, fields in PROFILE.items() if section != "real_cli"}
    for name, value in changes.items():
        section, field = name.split("__")
        seen[section][field] = value
    return {**seen, "seen": {"version": "1.0", "starts": 1, "env": ["HOME", "PATH"]}}


@pytest.mark.kit
def test_calibrate_drift_names_each_differing_field():
    assert calibrate.drift(PROFILE, observation()) == []
    lines = calibrate.drift(PROFILE, observation(spawn__cwd="home", initialize__protocol=None))
    assert lines == ["spawn.cwd: profile 'workspace', observed 'home'"]
    skipping = {**PROFILE, "real_cli": {**PROFILE["real_cli"], "calibrate_skip": ["spawn.cwd"]}}
    assert calibrate.drift(skipping, observation(spawn__cwd="home")) == []


@pytest.mark.kit
def test_calibrate_dump_reads_back_as_toml():
    seen = observation(limits__tool_prefix=None)
    assert tomllib.loads(calibrate.dump(seen)) == {**seen, "limits": {}}


def write_profile(root: Path, key: str, **real_cli) -> None:
    data = {**PROFILE, "real_cli": {**PROFILE["real_cli"], **real_cli}}
    (root / f"{key}.toml").write_text(calibrate.dump(data), encoding="utf-8")


def fake_runner(results: dict[str, dict]):
    """A runner that writes each harness's observation the way the cell does."""
    def run(keys: list[str], online: bool, out: Path) -> int:
        (out / "observed").mkdir(parents=True, exist_ok=True)
        for key in keys:
            if key in results:
                (out / "observed" / f"{key}.toml").write_text(calibrate.dump(results[key]), encoding="utf-8")
        return 0
    return run


@pytest.mark.kit
def test_calibrate_main_fails_on_a_pinned_drift_only(tmp_path, capsys):
    root = tmp_path / "profiles"
    root.mkdir()
    for key in ("alpha", "kiro"):
        write_profile(root, key)
    write_profile(root, "desktop-only", image="")
    assert calibrate.calibratable(root) == ["alpha", "kiro"]
    argv = ["--all", "--out", str(tmp_path / "out")]
    drifted = {"alpha": observation(spawn__env="allowlist"), "kiro": observation()}
    assert calibrate.main(argv, fake_runner(drifted), root) == 1
    assert "spawn.env: profile 'inherit', observed 'allowlist'" in capsys.readouterr().out
    assert (root / "observed" / "alpha.toml").is_file()
    latest_drift = {"alpha": observation(), "kiro": observation(spawn__cwd="home")}
    assert calibrate.main(argv, fake_runner(latest_drift), root) == 0
    assert calibrate.main(["kiro", "--out", str(tmp_path / "out")], fake_runner({}), root) == 0
    never = {key: {**observation(), "seen": {"version": "2.0", "starts": 0}} for key in ("alpha", "kiro")}
    assert calibrate.main(["kiro", "--out", str(tmp_path / "out")], fake_runner(never), root) == 0
    assert calibrate.main(["alpha", "--out", str(tmp_path / "out")], fake_runner(never), root) == 1
    assert "never started crapkit" in capsys.readouterr().out
    assert calibrate.main(["alpha", "--out", str(tmp_path / "out")], fake_runner({}), root) == 1
    assert "alpha: no observation" in capsys.readouterr().out


@pytest.mark.kit
def test_calibrate_refuses_an_unknown_harness(tmp_path):
    write_profile(tmp_path, "alpha")
    with pytest.raises(SystemExit):
        calibrate.main(["nosuch"], fake_runner({}), tmp_path)
    with pytest.raises(SystemExit):
        calibrate.main([], fake_runner({}), tmp_path)
    assert calibrate.selection(["alpha", "kiro"])[-2:] == ["-k", "alpha or kiro"]


@pytest.mark.kit
def test_a_recorded_version_drops_the_release_age():
    printed = "0.0.1790265644-gf7438f (released 2026-09-24T16:00:44.000Z, 1d ago)"
    assert profiles.RELEASE_AGE.sub("", printed) == "0.0.1790265644-gf7438f"
    assert profiles.RELEASE_AGE.sub("", "2.1.281 (Claude Code)") == "2.1.281 (Claude Code)"
