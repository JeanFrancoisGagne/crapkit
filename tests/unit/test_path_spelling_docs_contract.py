r"""The path spellings the pages promise, fed through the code that reads them.

docs/configuration.md, docs/lanes.md, docs/agent-json.md and README.md name the
spellings crapkit reads as git's: a crapkit.toml written on Windows and read on
Linux, a Git Bash or WSL path typed on Windows, a runner's `./` or absolute test
id, an `--exclude` fragment typed with a backslash. Each test reads the page,
checks that it names the spelling, and runs the spelling through the reader the
page describes, so a page cannot promise a reading the code no longer makes. A
line the pages quote (a doctor WARN, a lane failure, a refusal) is taken from the
code that prints it.
"""
from __future__ import annotations

import ntpath
import re
from pathlib import Path

import pytest

from crapkit import config, doctor, lanes, procs
from crapkit.cli import _shared
from crapkit.cli._shared import _repo_relative
from crapkit.cli.queue import _path_fragment
from crapkit.cli.verifying import _test_files
from crapkit.config import load_config_text
from crapkit.repopath import file_separators, native
from crapkit.universe import exclude_matcher
from crapkit.verify import dirty_failure_ids

from path_spellings import need_case_insensitive, only_posix, only_windows

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "[[scope]]\nname = 'api'\npaths = ['api']\nlanguages = ['python']\n"


def _page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _section(name: str, heading: str) -> str:
    """The text under `heading` in the page, up to the next heading of the same
    or a higher level."""
    text = _page(name)
    level = heading.split(" ")[0]
    start = text.index(f"\n{heading}\n")
    rest = text[start + len(heading) + 2:]
    end = re.search(rf"^#{{1,{len(level)}}} ", rest, re.MULTILINE)
    return rest[:end.start()] if end else rest


def _prose(text: str) -> str:
    """The text with each run of whitespace read as one space, so a phrase the
    page wraps across two lines still reads as one."""
    return " ".join(text.split())


def _row(section: str, key: str) -> str:
    """The table row of a config key, `| `key` | ...`."""
    return next(line for line in section.splitlines() if line.startswith(f"| `{key}` |"))


# -- [exclude] globs -------------------------------------------------------------

EXCLUDE_SPELLINGS = [
    ("src\\gen\\**", "src/gen/**"),
    ("**\\gen\\**", "**/gen/**"),
    ("./src/gen/**", "src/gen/**"),
    ("/src/gen/**", "src/gen/**"),
    ("src/gen/", "src/gen/**"),
]


@pytest.mark.parametrize("raw, read", EXCLUDE_SPELLINGS)
def test_the_exclude_section_names_each_glob_spelling_the_loader_folds(raw, read):
    section = _section("docs/configuration.md", "## `[exclude]`")
    assert f"`{raw}`" in section, raw
    assert f"`{read}`" in section, read

    cfg = load_config_text(SCOPE + f"[exclude]\nglobs = ['{raw}']\n")

    assert cfg.exclude_globs == (read,)
    assert exclude_matcher(cfg.exclude_globs)("src/gen/client.py")


def test_the_exclude_section_says_doctor_warns_on_a_glob_that_matches_nothing():
    section = _prose(_section("docs/configuration.md", "## `[exclude]`"))

    assert "matches no tracked file" in section


# -- path_prefix -----------------------------------------------------------------

PREFIX_SPELLINGS = ["api\\", "./api/", ".\\api\\", "/api/"]


def _lane_prefix(prefix: str) -> str:
    text = (SCOPE + "[[lane]]\nname = 'py'\ncommand = 'python -m pytest --cov'\n"
            f"artifact = '.crapkit/cov/py.json'\nparser = 'coveragepy'\nscopes = ['api']\n"
            f"cwd = 'api'\npath_prefix = '{prefix}'\n")
    (lane,) = load_config_text(text).lanes
    return lane.path_prefix


@pytest.mark.parametrize("raw", PREFIX_SPELLINGS)
def test_each_page_names_the_path_prefix_spellings_that_read_as_the_directory(raw):
    row = _row(_section("docs/configuration.md", "## `[[lane]]`"), "path_prefix")
    subdirectory = _prose(_section("docs/lanes.md", "### Running from a subdirectory"))
    assert f"`{raw}`" in row, row
    assert f"`{raw}`" in subdirectory

    assert _lane_prefix(raw) == _lane_prefix("api/")


def test_a_path_prefix_of_dot_reads_as_no_prefix():
    row = _row(_section("docs/configuration.md", "## `[[lane]]`"), "path_prefix")
    assert "`.` reads as no prefix" in row

    assert _lane_prefix(".") == ""


# -- ratchet_file ----------------------------------------------------------------

@pytest.mark.parametrize("raw", ["gates\\ratchet.tsv", "./gates/ratchet.tsv"])
def test_the_ratchet_file_row_names_the_spellings_that_open_one_file(raw):
    row = _row(_section("docs/configuration.md", "## `[crapkit]`"), "ratchet_file")
    assert f"`{raw}`" in row, row

    cfg = load_config_text(f"[crapkit]\nratchet_file = '{raw}'\n" + SCOPE)

    assert cfg.ratchet_file == "gates/ratchet.tsv"


# -- the File paths table --------------------------------------------------------

WINDOWS_ALIASES = [
    ("/c/repo/src/a.py", "C:\\repo\\src\\a.py"),
    ("/mnt/c/repo/src/a.py", "C:\\repo\\src\\a.py"),
    ("\\\\?\\C:\\repo", "C:\\repo"),
    ("\\\\?\\UNC\\localhost\\C$\\repo", "C:\\repo"),
    ("\\\\localhost\\C$\\repo", "C:\\repo"),
]


@pytest.mark.parametrize("raw, drive", WINDOWS_ALIASES)
def test_the_file_paths_table_names_each_windows_alias_and_its_drive_spelling(raw, drive):
    """repopath.native takes the OS as a flag, so the Windows reading is checked
    on every OS."""
    section = _section("docs/configuration.md", "## File paths and root discovery")
    assert f"`{raw}`" in section, raw
    assert f"`{drive}`" in section, drive

    assert ntpath.normpath(native(raw, windows=True)) == drive


def test_the_file_paths_table_names_the_network_share_refusal_and_the_mapped_drive():
    section = _prose(_section("docs/configuration.md", "## File paths and root discovery"))

    assert "`\\\\server\\share\\repo`" in section
    assert "exits 3 before any lane starts" in section
    assert "Map the share to a drive letter" in section


ABSOLUTE_SCOPES = ["/home/dev/repo/web", "/c/repo/web", "/mnt/c/repo/web",
                   "\\\\server\\share\\web", "//server/share/web", "C:/repo/web"]


def _scope_paths(raw: str, root: Path) -> tuple[str, ...]:
    text = f"[[scope]]\nname = 'web'\npaths = ['{raw}']\nlanguages = ['python']\n"
    return load_config_text(text, root=root).scopes[0].paths


@pytest.mark.parametrize("raw", ABSOLUTE_SCOPES)
def test_each_absolute_scope_path_the_pages_name_is_refused_at_load(tmp_path, raw):
    """A drive-letter scope path was refused before 0.8.1 too, so the upgrade
    page, which lists what scored 0 files, leaves it out."""
    section = _section("docs/configuration.md", "## File paths and root discovery")
    upgrade = _section("docs/upgrading.md", "## Config paths that 0.8.1 reads on every OS")
    assert f"`{raw}`" in section, raw
    assert (f"`{raw}`" in upgrade) is (":" not in raw), raw

    with pytest.raises(config.ConfigError):
        _scope_paths(raw, tmp_path)


def test_an_absolute_scope_path_inside_the_checkout_is_refused_with_its_relative_spelling(tmp_path):
    """On Windows the checkout's own drive spelling is refused for its drive, so
    the path that gets the hint is the Git Bash one, `/c/...`."""
    (tmp_path / "web").mkdir()
    spelled = (tmp_path / "web").resolve().as_posix()
    if spelled[1:2] == ":":
        spelled = "/" + spelled[0].lower() + spelled[2:]

    with pytest.raises(config.ConfigError, match="write 'web'"):
        _scope_paths(spelled, tmp_path)


def test_a_path_in_another_letter_case_reads_as_the_listed_case(tmp_path):
    section = _section("docs/configuration.md", "## File paths and root discovery")
    assert "`SRC/App.py`" in section and "`src/app.py`" in section
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("", encoding="utf-8")
    need_case_insensitive(tmp_path)

    assert _repo_relative("SRC/App.py", tmp_path) == "src/app.py"


@only_posix
def test_a_backslash_in_a_cli_argument_stays_a_literal_on_posix(tmp_path):
    section = _prose(_section("docs/configuration.md", "## File paths and root discovery"))
    assert "a literal filename character on POSIX" in section

    assert _repo_relative("src\\a.py", tmp_path) == "src\\a.py"


@only_windows
def test_a_backslash_in_a_cli_argument_separates_on_windows(tmp_path):
    assert _repo_relative("src\\a.py", tmp_path) == "src/a.py"


def test_a_path_read_out_of_a_file_separates_at_a_backslash_on_every_os():
    """crapkit.toml, a coverage report and a JUnit report can be written on the
    other OS, so the page promises one reading everywhere, and says a tracked
    name holding a backslash is therefore unsupported."""
    section = _prose(_section("docs/configuration.md", "## File paths and root discovery"))
    assert "separate at `\\` on every OS" in section
    assert "is unsupported" in section

    assert file_separators("src\\pkg\\a.py") == "src/pkg/a.py"


# -- next-item --exclude and get_next_item's exclude ------------------------------

def test_the_exclude_fragment_pages_name_the_dot_slash_spelling():
    readme = next(line for line in _page("README.md").splitlines()
                  if line.startswith("| `next-item "))
    mcp = _row(_page("docs/agent-json.md"), "get_next_item")
    for text in (readme, mcp):
        assert "`./pkg/legacy`" in text, text

    assert _path_fragment("./pkg/legacy", folds=False) == "pkg/legacy"


@only_windows
def test_an_exclude_fragment_with_a_backslash_reads_as_git_spells_it_on_windows():
    assert _path_fragment("pkg\\legacy", folds=False) == "pkg/legacy"


@only_posix
def test_an_exclude_fragment_with_a_backslash_stays_literal_on_posix():
    assert _path_fragment("pkg\\legacy", folds=False) == "pkg\\legacy"


def test_an_exclude_fragment_in_another_case_reads_as_the_listed_case_where_the_disk_folds():
    assert _path_fragment("PKG/Legacy", folds=True) == _path_fragment("pkg/legacy", folds=True)


# -- dirty_failures --------------------------------------------------------------

def test_the_dirty_failures_row_names_each_id_spelling_verify_matches(tmp_path):
    row = _row(_page("docs/agent-json.md"), "dirty_failures")
    for spelling in ("`web\\src\\app.test.ts`", "`./web/src/app.test.ts`", "absolute"):
        assert spelling in row, row
    absolute = str(tmp_path / "web" / "src" / "app.test.ts")
    ids = [f"{spelling}::renders" for spelling in
           ("web/src/app.test.ts", "web\\src\\app.test.ts", "./web/src/app.test.ts", absolute)]

    assert dirty_failure_ids(ids, {"web/src/app.test.ts"}, _test_files(tmp_path, set(ids))) == ids


# -- the launcher token ----------------------------------------------------------

_TOKEN_ROW = re.compile(r"^\| `(\{python[^`]*\})` \| `([^`]+)` \| `([^`]+)` \|", re.MULTILINE)


def _token_rows() -> list[tuple[str, str, str]]:
    return _TOKEN_ROW.findall(_section("docs/configuration.md", "### The launcher token"))


def test_the_launcher_token_table_has_the_bare_and_the_venv_form():
    tokens = [token for token, _, _ in _token_rows()]

    assert "{python}" in tokens and "{python:.venv}" in tokens, tokens


def test_each_launcher_token_row_is_what_the_loader_expands_on_each_os():
    for token, windows, posix in _token_rows():
        assert config.expand_launchers(token, windows=True) == windows, token
        assert config.expand_launchers(token, windows=False) == posix, token


@pytest.mark.parametrize("page", ["docs/lanes.md", "plugin/skills/crapkit-onboard/SKILL.md"])
def test_the_pages_that_describe_init_s_launcher_name_the_token(page):
    text = _prose(_page(page))

    assert "`{python:.venv}`" in text and "`{python}`" in text, page


def test_the_upgrade_page_tells_an_0_8_0_config_to_swap_its_launcher_for_the_token():
    section = _prose(_section("docs/upgrading.md", "## Config paths that 0.8.1 reads on every OS"))

    assert "`{python:.venv}`" in section
    assert "re-seed" in section


# -- lines the pages quote -------------------------------------------------------

def test_the_exclude_section_quotes_doctor_s_warn_for_a_glob_that_matches_nothing():
    section = _section("docs/configuration.md", "## `[exclude]`")

    (finding,) = doctor.unmatched_globs((("./src/gen/**", "src/gen/**"),), ["src/app.py"])

    assert f"{finding.level} {finding.text}" in section, finding


def test_the_file_paths_section_quotes_doctor_s_warn_for_a_name_holding_a_backslash():
    section = _section("docs/configuration.md", "## File paths and root discovery")

    (finding,) = doctor.backslash_names(["src/app.py", "src/pkg/we\\ird.py"])

    assert f"{finding.level} {finding.text}" in section, finding


def _unmeasured_line(prefix: str) -> str:
    text = (SCOPE + "[[lane]]\nname = 'py'\ncommand = 'python -m pytest --cov'\n"
            "artifact = '.crapkit/cov/py.json'\nparser = 'coveragepy'\nscopes = ['api']\n"
            f"path_prefix = '{prefix}'\n")
    (lane,) = load_config_text(text).lanes
    return lanes._unmeasured_message(lane, {"web/src/calc.py": []}, ["api"])


def test_the_subdirectory_section_quotes_the_warning_that_names_the_path_prefix_read():
    section = _prose(_section("docs/lanes.md", "### Running from a subdirectory"))
    line = _unmeasured_line("web")
    tail = line[line.index("or path_prefix"):]

    assert "crapkit: lane 'py' measured 1 file(s), none of them under the paths its scopes declare" \
        in section
    assert line.startswith("lane 'py' measured 1 file(s), none of them under the paths")
    assert f"`{tail}`" in section, tail


def test_the_cwd_row_and_the_upgrade_page_quote_the_failure_of_a_cwd_that_names_nothing(tmp_path):
    row = _row(_section("docs/configuration.md", "## `[[lane]]`"), "cwd")
    upgrade = _prose(_section("docs/upgrading.md", "## Config paths that 0.8.1 reads on every OS"))
    missing = tmp_path / "nope"

    with pytest.raises(OSError) as failed:
        procs.run_bounded("echo never", 30, cwd=missing)

    quoted = str(failed.value).replace(str(missing), "<path>")
    assert f"`lane 'py' FAILED: {quoted}`" in row, quoted
    assert f"`{quoted}`" in upgrade, quoted


@only_windows
def test_the_file_paths_table_quotes_the_end_of_the_network_share_refusal():
    section = _prose(_section("docs/configuration.md", "## File paths and root discovery"))

    with pytest.raises(_shared.ConfigError) as refused:
        _shared._refuse_a_share(Path("\\\\server\\share\\repo"))

    message = str(refused.value)
    tail = message[message.index("Map the share"):]
    assert f"`{tail}`" in section, tail
