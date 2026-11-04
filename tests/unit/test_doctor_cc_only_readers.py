"""doctor's note for a cc-only scope a registered reader can now measure.

A scope that set `coverage_optional = true` because no reader read its language
learns from doctor when one does: the note names the language, the parser,
the toolchain (as doctor's runner line spells it) and the upgrade section. It
is a note, so doctor's exit code and `doctor --json` do not move.
"""
import pytest

from crapkit.cli.admin import _doctor_cc_only_readers
from crapkit.config import load_config_text

ANCHOR = "docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one"


def _config(languages: tuple[str, ...], optional: bool = True):
    quoted = ", ".join(f'"{lang}"' for lang in languages)
    key = "coverage_optional = true\n" if optional else ""
    lane = ("" if optional else '[[lane]]\nname = "x"\ncommand = "true"\n'
            'artifact = "x.lcov"\nparser = "lcov"\nscopes = ["core"]\n')
    return load_config_text(f'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "core"\n'
                            f'paths = ["core"]\nlanguages = [{quoted}]\n{key}\n{lane}')


def test_a_cc_only_rust_scope_gets_one_note_naming_its_reader(tmp_path):
    (finding,) = _doctor_cc_only_readers(tmp_path, _config(("rust",)))

    assert finding.level == "note"
    assert "'core'" in finding.text and "rust" in finding.text
    assert 'parser = "lcov"' in finding.text and "cargo llvm-cov" in finding.text
    assert ANCHOR in finding.text
    assert finding.text.isascii()


def test_a_cc_only_cpp_scope_gets_one_note_naming_cobertura_and_gcovr(tmp_path):
    (finding,) = _doctor_cc_only_readers(tmp_path, _config(("cpp",)))

    assert finding.level == "note"
    assert "'core'" in finding.text and "cpp" in finding.text
    assert 'parser = "cobertura"' in finding.text and "gcovr" in finding.text
    assert ANCHOR in finding.text


JACOCO_FROM = 'java through parser = "jacoco" from '


@pytest.mark.parametrize("marker, toolchain", [
    ("build.gradle.kts", "gradle"), ("build.gradle", "gradle"), ("pom.xml", "maven")])
def test_a_cc_only_java_scope_gets_one_note_naming_jacoco_and_the_toolchain_it_detects(
        tmp_path, marker, toolchain):
    """The note names the toolchain whose marker the repo root holds, and no other."""
    (tmp_path / marker).write_text("")

    (finding,) = _doctor_cc_only_readers(tmp_path, _config(("java",)))

    assert finding.level == "note"
    assert "'core'" in finding.text
    assert f"{JACOCO_FROM}{toolchain};" in finding.text
    assert ANCHOR in finding.text


@pytest.mark.parametrize("markers", [("build.gradle", "pom.xml"), ()], ids=["both", "neither"])
def test_a_java_repo_holding_both_markers_or_neither_hears_of_both_toolchains(tmp_path, markers):
    for marker in markers:
        (tmp_path / marker).write_text("")

    (finding,) = _doctor_cc_only_readers(tmp_path, _config(("java",)))

    assert f'{JACOCO_FROM}gradle, parser = "jacoco" from maven;' in finding.text


@pytest.mark.parametrize("languages", [
    ("python",), ("zig",), ("shell",), ("powershell",), ("swift",), ("objectivec",),
    ("go",), ("typescript",),
])
def test_a_scope_no_reader_init_lacks_a_live_lane_for_gets_none(tmp_path, languages):
    """python opted out where a live lane could measure it; the rest have no
    toolchain row that writes a format crapkit reads, and go's row reads only
    beside a go.mod, which tmp_path lacks."""
    assert _doctor_cc_only_readers(tmp_path, _config(languages)) == []


def test_a_cc_only_go_scope_beside_a_go_mod_gets_one_note_naming_coverprofile_and_go_test(
        tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app\n")

    (finding,) = _doctor_cc_only_readers(tmp_path, _config(("go",)))

    assert finding.level == "note"
    assert "'core'" in finding.text
    assert 'go through parser = "coverprofile" from go test;' in finding.text
    assert ANCHOR in finding.text


def test_a_rust_scope_without_coverage_optional_gets_none(tmp_path):
    assert _doctor_cc_only_readers(tmp_path, _config(("rust",), optional=False)) == []


def test_a_scope_holding_rust_and_zig_gets_one_note_naming_rust_alone(tmp_path):
    (finding,) = _doctor_cc_only_readers(tmp_path, _config(("rust", "zig")))

    assert "rust" in finding.text and "zig" not in finding.text
