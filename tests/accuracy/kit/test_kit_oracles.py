"""pins.toml agrees with the locks it describes, and kit.oracles finds and checks tools."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib

import pytest

from accuracy.kit import oracles, runlog

REPO = oracles.REPO
ACCURACY_TOOLS = REPO / "tools" / "accuracy"
PINS = oracles.load_pins()


def _locked(tier):
    text = (ACCURACY_TOOLS / f"requirements-{tier}.txt").read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", text, flags=re.M))


def _node_manifest(tier):
    text = (ACCURACY_TOOLS / "node" / tier / "package.json").read_text(encoding="utf-8")
    return json.loads(text)["dependencies"]


def test_every_pin_names_a_digest_a_source_and_what_its_version_says():
    for pin in PINS.values():
        assert pin.kind in oracles.KINDS, pin.name
        assert pin.tier in ("push", "nightly"), pin.name
        assert pin.url.startswith("https://"), pin.name
        assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256), pin.name
        assert pin.version_line, pin.name


def test_python_pins_equal_their_hashed_lock():
    """A push oracle is in both locks; a nightly one in the nightly lock."""
    push, nightly = _locked("push"), _locked("nightly")
    for pin in (pin for pin in PINS.values() if pin.kind == "python"):
        assert nightly.get(pin.package.lower()) == pin.version, pin.name
        if pin.tier == "push":
            assert push.get(pin.package.lower()) == pin.version, pin.name


def test_node_pins_equal_their_package_manifest():
    for pin in (pin for pin in PINS.values() if pin.kind == "node"):
        assert _node_manifest(pin.tier).get(pin.package) == pin.version, pin.name


SNAPSHOT = tomllib.loads(oracles.PINS.read_text(encoding="utf-8"))["snapshot"]
DOCKERFILE = (ACCURACY_TOOLS / "image" / "Dockerfile").read_text(encoding="utf-8")


def test_the_snapshot_pins_the_base_image_by_digest():
    """trixie, not bookworm: cargo-crap 0.5.0 needs glibc 2.39 and SwiftLint
    0.65.1 needs glibc 2.38 with GLIBCXX_3.4.32; bookworm ships glibc 2.36."""
    assert re.fullmatch(r"\d{8}T\d{6}Z", SNAPSHOT["debian"])
    assert re.fullmatch(r"debian:trixie-slim@sha256:[0-9a-f]{64}", SNAPSHOT["base"])


def test_the_dockerfile_builds_from_the_pinned_base_and_snapshot():
    assert f"ARG BASE={SNAPSHOT['base']}\n" in DOCKERFILE
    assert f"ARG DEBIAN_SNAPSHOT={SNAPSHOT['debian']}\n" in DOCKERFILE


def test_the_dockerfile_installs_every_binary_pin():
    """A binary pin no install-tools.sh line names would ship missing from the image."""
    named = {name for line in re.findall(r"install-tools\.sh \S*pins\.toml ([^\n]+)", DOCKERFILE)
             for name in line.split()}

    assert named == {pin.name for pin in PINS.values() if pin.kind == "binary"}


def _pin(**fields):
    base = {"name": "tool", "kind": "python", "tier": "push", "version": "1.0",
            "url": "https://example.invalid/tool", "sha256": "0" * 64, "version_line": "1.0"}
    base.update(fields)
    return oracles.Pin(**base)


def test_an_installed_python_oracle_is_found_at_its_version():
    found = oracles.locate("hypothesis", PINS)

    assert found.version == PINS["hypothesis"].version


def test_a_missing_python_oracle_names_the_install_command():
    pins = {"tool": _pin(package="crapkit-no-such-dist")}

    with pytest.raises(oracles.OracleMissing, match="requirements-push.txt"):
        oracles.locate("tool", pins)


def test_an_unpinned_name_is_refused():
    with pytest.raises(oracles.OracleMissing, match="no pin named radon2"):
        oracles.locate("radon2", PINS)


def test_a_node_oracle_is_read_from_its_package_manifest(monkeypatch, tmp_path):
    package = tmp_path / "push" / "node_modules" / "@scope" / "tool"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"version": "4.2.1"}', encoding="utf-8")
    monkeypatch.setenv(oracles.NODE_ROOT_ENV, str(tmp_path))
    pins = {"tool": _pin(kind="node", package="@scope/tool", version="4.2.1",
                         version_line="4.2.1")}

    assert oracles.locate("tool", pins).version == "4.2.1"
    monkeypatch.setenv(oracles.NODE_ROOT_ENV, str(tmp_path / "elsewhere"))
    with pytest.raises(oracles.OracleMissing, match="npm ci --prefix tools/accuracy/node/push"):
        oracles.locate("tool", pins)


@pytest.mark.process
def test_a_binary_answers_with_its_version_line():
    line = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    pins = {"tool": _pin(kind="binary", command=(sys.executable, "--version"),
                         version_line=line)}

    found = oracles.locate("tool", pins)

    assert found.version == line
    assert Path(found.where).samefile(sys.executable)


@pytest.mark.process
def test_a_binary_that_says_something_else_reports_its_first_line():
    pins = {"tool": _pin(kind="binary", command=(sys.executable, "--version"),
                         version_line="Python 2.7.18")}

    found = oracles.locate("tool", pins)

    assert found.version.startswith("Python 3.")
    assert oracles.drift(found, pins["tool"]) == (
        f"oracle tool is {found.version}, pins.toml says Python 2.7.18")


@pytest.mark.process
def test_bin_in_a_command_is_the_pinned_tool_not_the_program_that_asks(monkeypatch, tmp_path):
    """gocyclo's version comes from `go version -m {bin}`: {bin} is gocyclo's
    own file. Resolving it to the command's first word asks go about itself."""
    for name, mode in (("crapkit-probe-tool", 0o755), ("crapkit-probe-tool.cmd", 0o644)):
        (tmp_path / name).write_text("exit 0\n", encoding="utf-8")
        (tmp_path / name).chmod(mode)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    ask = (sys.executable, "-c", "import sys; print('module', sys.argv[1])", "{bin}")
    pins = {"crapkit-probe-tool": _pin(name="crapkit-probe-tool", kind="binary", command=ask,
                                       version_line="crapkit-probe-tool")}

    found = oracles.locate("crapkit-probe-tool", pins)

    assert found.version == "crapkit-probe-tool"
    assert Path(found.where).name.startswith("crapkit-probe-tool")


def test_a_binary_pinned_by_digest_hashes_its_file(tmp_path):
    script = tmp_path / "tool.sh"
    script.write_bytes(b"echo tool\n")
    digest = hashlib.sha256(b"echo tool\n").hexdigest()
    pin = _pin(kind="binary", command=(sys.executable,), check="sha256", path=str(script),
               sha256=digest, version_line="tool")

    assert oracles.locate("tool", {"tool": pin}).version == "tool"
    script.write_bytes(b"echo other\n")
    assert oracles.locate("tool", {"tool": pin}).version == "another build"


@pytest.mark.process
def test_a_binary_its_asking_program_knows_nothing_about_is_missing():
    """bugspots's version comes from `gem list --exact bugspots`, which prints
    nothing and exits 0 where Ruby is installed and the gem is not (a bare
    GitHub Ubuntu runner). That is a missing oracle, an infra miss, not a
    drift to an empty version ("oracle bugspots is , pins.toml says ...")."""
    pins = {"tool": _pin(kind="binary", command=(sys.executable, "-c", ""),
                         version_line="tool (1.0)")}

    with pytest.raises(oracles.OracleMissing, match="answers nothing.*accuracy image"):
        oracles.locate("tool", pins)


def test_a_binary_off_the_path_names_the_image():
    pins = {"tool": _pin(kind="binary", command=("crapkit-no-such-binary", "--version"))}

    with pytest.raises(oracles.OracleMissing, match="accuracy image"):
        oracles.locate("tool", pins)


def test_a_producer_is_never_located():
    with pytest.raises(oracles.OracleMissing, match="recorded producer"):
        oracles.locate("coverage-7.10.6", PINS)


def test_drift_names_both_versions():
    pin = PINS["radon"]
    found = oracles.Found("radon", "6.0.2", "radon")

    assert oracles.drift(found, pin) == "oracle radon is 6.0.2, pins.toml says 6.0.1"
    assert oracles.drift(oracles.Found("radon", "6.0.1", "radon"), pin) is None


def _drifted():
    return {"tool": _pin(package="hypothesis", version="0.0.1", version_line="0.0.1")}


@pytest.mark.parametrize("tier", ["nightly", "release"])
def test_drift_fails_a_nightly_or_release_test(tier):
    with pytest.raises(pytest.fail.Exception, match="oracle tool is .*, pins.toml says 0.0.1"):
        oracles.require("tool", tier, _drifted())


def test_drift_warns_on_push():
    with pytest.warns(oracles.OracleDriftWarning, match="pins.toml says 0.0.1"):
        oracles.require("tool", "push", _drifted())


def test_a_missing_oracle_fails_the_test_and_notes_an_infra_miss(monkeypatch, tmp_path):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("CRAPKIT_ACCURACY_LOG", str(log))
    pins = {"tool": _pin(package="crapkit-no-such-dist")}

    with pytest.raises(pytest.fail.Exception, match="is not installed"):
        oracles.require("tool", "push", pins)

    assert [note["kind"] for note in runlog.read(log)] == ["infra"]


def test_the_fixture_hands_back_a_checked_oracle(oracle):
    assert oracle("hypothesis").version == PINS["hypothesis"].version


def test_a_found_oracle_notes_its_version_for_the_receipt(monkeypatch, tmp_path):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("CRAPKIT_ACCURACY_LOG", str(log))

    oracles.require("hypothesis", "push")

    notes = runlog.read(log)
    assert runlog.summarize(notes)["oracles"] == {"hypothesis": PINS["hypothesis"].version}
