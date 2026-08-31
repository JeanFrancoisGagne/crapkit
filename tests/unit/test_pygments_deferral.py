"""pygments must not load in a crapkit process that will never parse Erlang.

lizard imports every reader it ships, and `lizard_languages/erlang.py` does
`import pygments.token` / `from pygments import lex, lexers` at module scope. So
pygments — and importlib.metadata, email, zipfile and socket behind it — loads
before any crapkit command can call FileAnalyzer. Measured here: `import lizard`
costs 42ms with it and 16ms without, and the pre-commit hook pays that on every
`git commit`.

Deferring it must change nothing about the answers, so the Erlang reader is
checked through the proxies as well: it is the one reader that actually needs
pygments, and it must still produce the identical function list.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import crapkit
from untraced_child import untraced_env

SRC = str(Path(crapkit.__file__).resolve().parent.parent)

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
"""

ERLANG = """-module(sample).
-export([classify/1]).

classify(N) when N > 10 -> big;
classify(N) when N > 5 -> medium;
classify(_) -> small.
"""

ANALYZE = """
import json, sys
{prelude}
import lizard
src = sys.argv[1]
info = lizard.FileAnalyzer(lizard.get_extensions(["ND"]))(src)
print(json.dumps([[f.long_name, f.cyclomatic_complexity, f.start_line, f.end_line]
                  for f in info.function_list]))
print(json.dumps("pygments" in sys.modules))
"""

DEFER_PRELUDE = ("from crapkit._pygdefer import deferred_pygments\n"
                 "with deferred_pygments():\n    import lizard\n")


def run_python(code: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    # Untraced: coverage started in the child imports pygments through its own
    # reporters, and every assertion below is about what a crapkit process
    # holds in sys.modules. Traced, they measure coverage instead.
    env = untraced_env()
    env["PYTHONPATH"] = os.pathsep.join([SRC, env.get("PYTHONPATH", "")])
    return subprocess.run([sys.executable, "-c", code, *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=180)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   text=True, encoding="utf-8")


def staged_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (repo / "src" / "base.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    (repo / "src" / "mod.py").write_text("def fn(n):\n    return n + 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    return repo


HOOK_RUN = """
import sys
from crapkit.cli import main
sys.argv = ["crapkit", "hook-precommit", "--repo", sys.argv[1]]
code = main()
print(f"exit={code} pygments={'pygments' in sys.modules}")
"""


def test_a_clean_hook_run_never_imports_pygments(tmp_path):
    repo = staged_repo(tmp_path)

    res = run_python(HOOK_RUN, str(repo))

    assert res.stdout.strip().endswith("exit=0 pygments=False"), res.stdout + res.stderr


def test_the_erlang_reader_gives_the_same_answer_through_the_proxies(tmp_path):
    sample = tmp_path / "sample.erl"
    sample.write_text(ERLANG, encoding="utf-8")

    plain = run_python(ANALYZE.format(prelude=""), str(sample))
    deferred = run_python(ANALYZE.format(prelude=DEFER_PRELUDE), str(sample))

    assert plain.returncode == 0 and deferred.returncode == 0, plain.stderr + deferred.stderr
    plain_fns, plain_loaded = plain.stdout.splitlines()
    deferred_fns, deferred_loaded = deferred.stdout.splitlines()
    assert json.loads(plain_fns) == json.loads(deferred_fns)
    assert json.loads(plain_fns), "the reader has to find something for this to prove anything"
    assert json.loads(plain_loaded) is True
    assert json.loads(deferred_loaded) is True, "reading Erlang pays for pygments, as it must"


REAL_PYGMENTS = """
import sys
from crapkit._pygdefer import deferred_pygments
with deferred_pygments():
    import lizard
print("proxied" if "pygments" in sys.modules else "absent")
import pygments.formatters
print(pygments.formatters.__name__, callable(pygments.formatters.get_formatter_by_name))
"""


def test_a_submodule_import_afterwards_gets_the_real_package():
    """The proxies come back out once lizard has bound them: a module left in
    sys.modules with no __path__ breaks the next `import pygments.anything`."""
    res = run_python(REAL_PYGMENTS)

    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["absent", "pygments.formatters True"]
