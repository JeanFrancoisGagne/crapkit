"""doctor WARNs on a coverage.py lane that `crapkit coverage` will refuse in a container.

The lane runner refuses a `coveragepy` lane inside a container unless the lane
says `container_ok = true` (docs/lanes.md#containers), and exits 5. doctor never
looked, so a devcontainer, a Codespace, a Codex cloud task or a CI job in a
container passed doctor and then refused its first coverage run. doctor now
reads the same two triggers and names the lane, the trigger and the key. WARN,
never FAIL: the config is right, this machine is the question.
"""
from types import SimpleNamespace

import pytest

from crapkit import lanes as lanes_module
from crapkit.cli import admin
from crapkit.config import Lane
from crapkit.doctor import container_lane_findings, container_marker
from crapkit.errors import ToolError


def lane(name: str, parser: str = "coveragepy", container_ok: bool = False) -> Lane:
    return Lane(name=name, command="python -m pytest", artifact=f".crapkit/cov/{name}.json",
                parser=parser, scopes=("src",), container_ok=container_ok)


@pytest.mark.parametrize("environ, dockerenv, marker", [
    ({"CRAPKIT_INSIDE_CONTAINER": "1"}, False, "CRAPKIT_INSIDE_CONTAINER=1"),
    ({"CRAPKIT_INSIDE_CONTAINER": "1"}, True, "CRAPKIT_INSIDE_CONTAINER=1"),
    ({}, True, "/.dockerenv exists"),
    ({"CRAPKIT_INSIDE_CONTAINER": "0"}, True, "/.dockerenv exists"),
    ({"CRAPKIT_INSIDE_CONTAINER": "0"}, False, None),
    ({}, False, None),
])
def test_the_marker_names_the_trigger_a_user_can_check(environ, dockerenv, marker):
    assert container_marker(environ, dockerenv) == marker


@pytest.mark.parametrize("variable", [None, "0", "1"])
@pytest.mark.parametrize("dockerenv", [False, True])
def test_doctor_reads_a_container_exactly_where_the_lane_runner_refuses(monkeypatch, variable,
                                                                       dockerenv):
    """Two readings of one fact. If they drift, doctor passes a lane coverage
    refuses, or WARNs about one it runs."""
    environ = {} if variable is None else {"CRAPKIT_INSIDE_CONTAINER": variable}
    monkeypatch.delenv("CRAPKIT_INSIDE_CONTAINER", raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(lanes_module, "Path", lambda text: SimpleNamespace(exists=lambda: dockerenv))

    refused = True
    try:
        lanes_module._refuse_container_python(lane("py"))
    except ToolError:
        pass
    else:
        refused = False
    assert refused == (container_marker(environ, dockerenv) is not None)


def test_a_coveragepy_lane_warns_naming_the_lane_the_trigger_and_the_key():
    (finding,) = container_lane_findings([lane("py")], "/.dockerenv exists")

    assert finding.level == "WARN"
    assert finding.text == (
        "lane 'py' runs a coverage.py suite and this is a container (/.dockerenv exists): "
        "`crapkit coverage` refuses it with exit 5; if the container is sized for the suite, "
        "set container_ok = true on the lane (docs/lanes.md#containers)")


def test_only_the_lanes_the_runner_would_refuse_are_named():
    found = container_lane_findings(
        [lane("py"), lane("ok", container_ok=True), lane("js", parser="istanbul"), lane("b")],
        "CRAPKIT_INSIDE_CONTAINER=1")

    assert [f.text.split()[1] for f in found] == ["'py'", "'b'"]


def test_outside_a_container_nothing_is_said():
    assert container_lane_findings([lane("py")], None) == ()


def test_the_doctor_command_reads_this_process_environment(monkeypatch):
    cfg = SimpleNamespace(lanes=[lane("py")])
    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")

    (finding,) = admin._doctor_container(cfg)
    assert "(CRAPKIT_INSIDE_CONTAINER=1)" in finding.text

    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "0")
    monkeypatch.setattr(admin, "Path", lambda text: SimpleNamespace(exists=lambda: False))
    assert admin._doctor_container(cfg) == []
