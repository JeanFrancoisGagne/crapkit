"""Shell enters the file universe as a cc-only language, on a reader lizard lacks.

Two constants admit it and no third. Shell has no `foo_test.sh` convention to
exclude — bats keeps its cases in `.bats` files, which no scope's language claims,
and the two dotted spellings a shell suite does use (`foo.test.sh`, `foo.spec.sh`)
are already cut by the `**/*.test.*` and `**/*.spec.*` globs init has always
written. Inventing a `**/*_test.sh` glob would delete production files from repos
that name scripts that way.

Skipping registration is the hazard the measurement half guards: lizard answers
`(get_reader_for(filename) or CLikeReader)`, and CLikeReader accepts `f() { }`
because it looks like C, so an unregistered shell corpus reports plausible numbers
that are wrong.
"""
import os
import subprocess
import sys
from pathlib import Path

import crapkit
from crapkit.analyze import analyze_source
from crapkit.config import Config, Scope, load_config_text
from crapkit.scaffold import DEFAULT_EXCLUDES, sniff_scopes, source_candidates
from crapkit.universe import scan_files

SRC = str(Path(crapkit.__file__).resolve().parent.parent)

SHELL_SCOPE = Scope(name="scripts", paths=("scripts",), languages=("shell",))

# base 1, + if, elif, for, &&, ||
DEPLOY = """deploy() {
  if [ -z "$1" ]; then
    return 1
  elif [ "$1" = "all" ]; then
    for host in $HOSTS; do
      ping "$host" && echo up || echo down
    done
  fi
}
"""
DEPLOY_CCN = 6


# --- the config seam ----------------------------------------------------------

def test_a_scope_can_declare_shell():
    cfg = load_config_text(
        '[[scope]]\nname = "scripts"\npaths = ["scripts"]\nlanguages = ["shell"]\n')
    assert cfg.scopes[0].languages == ("shell",)


def test_a_shell_scope_can_be_coverage_optional():
    """cc-only is the whole shape for shell: neither coverage parser reads a
    shell suite, and most shell in a repo is production-only anyway."""
    cfg = load_config_text('[[scope]]\nname = "scripts"\npaths = ["scripts"]\n'
                           'languages = ["shell"]\ncoverage_optional = true\n')
    assert cfg.coverage_optional_scopes == frozenset({"scripts"})


# --- the file universe --------------------------------------------------------

def test_both_shell_extensions_join_the_scope():
    uni = scan_files(["scripts/deploy.sh", "scripts/lib.bash", "scripts/notes.md"],
                     Config(scopes=(SHELL_SCOPE,)))
    assert uni.by_scope == {"scripts": ["scripts/deploy.sh", "scripts/lib.bash"]}


def test_the_dotted_test_spellings_are_already_excluded_and_need_no_new_glob():
    """`**/*.test.*` and `**/*.spec.*` predate shell and already claim both
    spellings a shell suite uses. No `**/*_test.sh` is added: `deploy_test.sh`
    is production code in plenty of repos."""
    cfg = Config(scopes=(SHELL_SCOPE,), exclude_globs=DEFAULT_EXCLUDES)
    uni = scan_files(["scripts/deploy.sh", "scripts/deploy.test.sh",
                      "scripts/deploy.spec.sh"], cfg)

    assert uni.by_scope == {"scripts": ["scripts/deploy.sh"]}
    assert not [glob for glob in DEFAULT_EXCLUDES if glob.endswith(("_test.sh", "_test.bash"))]


def test_init_never_proposes_a_dotted_shell_test_as_source():
    assert source_candidates(["scripts/deploy.sh", "scripts/deploy.test.sh"]) == [
        "scripts/deploy.sh"]


def test_init_sniffs_a_shell_directory_as_a_shell_scope():
    assert sniff_scopes(["scripts/deploy.sh", "scripts/lib.bash"]) == {"scripts": ("shell",)}


# --- the measurement ----------------------------------------------------------

def _reader_after_importing_analyze(filename: str) -> str:
    """The reader a FRESH interpreter resolves once it has imported nothing but
    crapkit.analyze. In-process this proves nothing: importing the reader module
    registers it, and some other test already has. A pool worker imports
    crapkit.analyze and nothing else, so that is the import under test."""
    code = ("import lizard, crapkit.analyze\n"
            f"print(lizard.get_reader_for({filename!r}).__name__)\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([SRC, env.get("PYTHONPATH", "")])
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_importing_the_analysis_module_alone_registers_the_shell_reader():
    assert _reader_after_importing_analyze("scripts/deploy.sh") == "ShellReader"


def test_bash_resolves_to_the_shell_reader_too():
    assert _reader_after_importing_analyze("scripts/lib.bash") == "ShellReader"


def test_crapkit_reads_the_hand_counted_ccn_off_a_shell_file():
    (record,) = analyze_source("scripts/deploy.sh", DEPLOY)

    assert record.ccn == DEPLOY_CCN


def test_top_level_script_code_is_not_reported_as_a_function():
    """Statements outside any function belong to lizard's `*global*` pseudo
    function, exactly like Python module level. A script that is one long
    sequence reports nothing, and that is the answer, not a parse failure."""
    assert analyze_source("scripts/run.sh", "set -e\nfor f in *; do echo $f; done\n") == []
