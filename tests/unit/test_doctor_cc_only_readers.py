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


def test_a_cc_only_rust_scope_gets_one_note_naming_its_reader():
    (finding,) = _doctor_cc_only_readers(_config(("rust",)))

    assert finding.level == "note"
    assert "'core'" in finding.text and "rust" in finding.text
    assert 'parser = "lcov"' in finding.text and "cargo llvm-cov" in finding.text
    assert ANCHOR in finding.text
    assert finding.text.isascii()


def test_a_cc_only_cpp_scope_gets_one_note_naming_cobertura_and_gcovr():
    (finding,) = _doctor_cc_only_readers(_config(("cpp",)))

    assert finding.level == "note"
    assert "'core'" in finding.text and "cpp" in finding.text
    assert 'parser = "cobertura"' in finding.text and "gcovr" in finding.text
    assert ANCHOR in finding.text


@pytest.mark.parametrize("languages", [
    ("python",), ("zig",), ("shell",), ("powershell",), ("swift",), ("objectivec",),
    ("go",), ("typescript",),
])
def test_a_scope_no_reader_init_lacks_a_live_lane_for_gets_none(languages):
    """python opted out where a live lane could measure it; the rest have no
    toolchain row that writes a format crapkit reads."""
    assert _doctor_cc_only_readers(_config(languages)) == []


def test_a_rust_scope_without_coverage_optional_gets_none():
    assert _doctor_cc_only_readers(_config(("rust",), optional=False)) == []


def test_a_scope_holding_rust_and_zig_gets_one_note_naming_rust_alone():
    (finding,) = _doctor_cc_only_readers(_config(("rust", "zig")))

    assert "rust" in finding.text and "zig" not in finding.text
