"""A source file whose extension is upper case scores and gates as its lower-case twin.

The scope's extension match compared letter case exactly while lizard's reader
lookup does not, so `src/MAIN.CPP` held no function for inventory and the
commit gate passed a ccn-8 function in it at exit 0.
"""
import pytest

from raw_git import checkout, commit, repository, stage

from crapkit.analyze import analyze_files
from crapkit.cli.parser import main
from crapkit.config import Config, Scope
from crapkit.universe import scan_files

CONFIG = (b'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
          b'languages = ["python", "cpp"]\ncoverage_optional = true\n')
CFG = Config(target=6, exclude_globs=(),
             scopes=(Scope(name="src", paths=("src",), languages=("python", "cpp")),))
CPP = (b"int g(int a,int b,int c,int d,int e,int f,int h){ if(a)return 1; if(b)return 2; "
       b"if(c)return 3; if(d)return 4; if(e)return 5; if(f)return 6; if(h)return 7; return 0; }\n")
PY = b"def grade(s):\n" + b"".join(b"    if s == %d:\n        return %d\n" % (i, i) for i in range(7)) \
    + b"    return 0\n"
TWINS = [("src/main.cpp", "src/MAIN.CPP", CPP), ("src/tool.py", "src/Tool.PY", PY)]


@pytest.mark.parametrize("lower, upper, body", TWINS, ids=["cpp", "python"])
def test_an_upper_case_extension_scores_the_rows_its_lower_case_twin_scores(tmp_path, lower, upper, body):
    for path in (lower, upper):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(body)

    claimed = scan_files([lower, upper], CFG).by_scope["src"]
    records, _, _ = analyze_files(tmp_path, claimed, cache={})

    assert claimed == sorted([lower, upper])
    assert [r._replace(path=lower) for r in records[upper]] == records[lower]
    assert [r.ccn for r in records[lower]] == [8]


@pytest.mark.parametrize("path, body, name", [
    ("src/main.cpp", CPP, "src/main.cpp:1"),
    ("src/MAIN.CPP", CPP, "src/MAIN.CPP:1"),
    ("src/Tool.PY", PY, "src/Tool.PY:1"),
], ids=["control", "cpp-upper", "python-upper"])
def test_the_commit_gate_refuses_a_ccn8_function_whatever_the_extensions_case(tmp_path, capsys,
                                                                              path, body, name):
    root = repository(tmp_path / "repo")
    commit(root, files={b"crapkit.toml": CONFIG, b"src/keep.cpp": b"int keep(){return 0;}\n"})
    checkout(root)
    stage(root, path.encode(), body)

    assert main(["hook-precommit", "--repo", str(root)]) == 6
    assert name in capsys.readouterr().out
