"""Go enters the file universe as a cc-only language.

Three constants admit a language, not two. Swift needed only `SUPPORTED_LANGUAGES`
and `LANGUAGE_EXTENSIONS` because Swift tests live in `Tests/` dirs and `_TEST_DIR`
already catches those. Go puts `foo_test.go` beside the source it tests, so without
a `**/*_test.go` default exclude every test file scores as production code.
"""
import json
from pathlib import Path

from crapkit.config import SUPPORTED_LANGUAGES, Config, Scope, load_config_text
from crapkit.scaffold import DEFAULT_EXCLUDES, sniff_scopes, source_candidates
from crapkit.universe import LANGUAGE_EXTENSIONS, scan_files

ROOT = Path(__file__).resolve().parent.parent.parent

GO_SCOPE = Scope(name="gosrc", paths=("cmd",), languages=("go",))


def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# --- the config seam ----------------------------------------------------------

def test_a_scope_can_declare_go():
    cfg = load_config_text(
        '[[scope]]\nname = "gosrc"\npaths = ["cmd"]\nlanguages = ["go"]\n')
    assert cfg.scopes[0].languages == ("go",)


def test_a_go_scope_can_be_coverage_optional():
    """cc-only is the whole v0.3 shape for Go: no parser, no lane, no artifact."""
    cfg = load_config_text('[[scope]]\nname = "gosrc"\npaths = ["cmd"]\n'
                           'languages = ["go"]\ncoverage_optional = true\n')
    assert cfg.coverage_optional_scopes == frozenset({"gosrc"})


# --- the file universe --------------------------------------------------------

def test_a_go_source_file_joins_its_scope():
    uni = scan_files(["cmd/main.go", "cmd/notes.md"], Config(scopes=(GO_SCOPE,)))
    assert uni.by_scope == {"gosrc": ["cmd/main.go"]}


def test_a_go_test_file_beside_its_source_is_excluded_by_default():
    """`_TEST_DIR` never fires: main_test.go sits in the source directory. The
    default exclude glob is the only thing keeping it out of the score."""
    cfg = Config(scopes=(GO_SCOPE,), exclude_globs=DEFAULT_EXCLUDES)
    uni = scan_files(["cmd/main.go", "cmd/main_test.go"], cfg)

    assert uni.by_scope == {"gosrc": ["cmd/main.go"]}


def test_init_never_proposes_a_go_test_file_as_source():
    assert source_candidates(["cmd/main.go", "cmd/main_test.go"]) == ["cmd/main.go"]


def test_init_sniffs_a_go_directory_as_a_go_scope():
    assert sniff_scopes(["cmd/main.go", "cmd/main_test.go"]) == {"cmd": ("go",)}


# --- the surfaces that publish the language set -------------------------------

def test_the_schema_language_enum_matches_the_supported_set():
    """An editor autocompletes scope languages from the schema; a language the
    schema omits reads as a typo in the one place a user is told what is legal."""
    schema = json.loads((ROOT / "crapkit.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["scope"]["items"]["properties"]["languages"]["items"]["enum"]

    assert set(enum) == SUPPORTED_LANGUAGES


def test_every_supported_language_has_extensions():
    assert set(LANGUAGE_EXTENSIONS) == SUPPORTED_LANGUAGES


def test_the_configuration_page_lists_every_language_and_extension():
    page = _doc("docs/configuration.md")
    missing = [token for lang, exts in LANGUAGE_EXTENSIONS.items()
               for token in (lang, *exts) if f"`{token}`" not in page]

    assert missing == []


def test_the_configuration_page_prints_the_default_excludes_init_writes():
    page = _doc("docs/configuration.md")
    missing = [glob for glob in DEFAULT_EXCLUDES if f'"{glob}"' not in page]

    assert missing == []
