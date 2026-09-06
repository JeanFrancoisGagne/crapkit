"""Real parser output stays distinct through the CLI, store, claims and ratchet."""
import csv
import json

from conftest import cli_runner, git, git_commit_all, git_init_repo
from crapkit.ratchet import metric_version


run_cli = cli_runner(encoding="utf-8", env_extra={"PYTHONIOENCODING": "utf-8",
                                                "CRAPKIT_OVERRIDE_REASON": None})


def _source():
    body = " ".join(f"if (x > {n}) x++;" for n in range(7))
    return f"values.map((x) => {{ {body} return x; }}).filter((x) => {{ {body} return x; }});\n"


def _repo(root):
    git_init_repo(root)
    (root / "src").mkdir()
    (root / "src/app.ts").write_text(_source(), encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (root / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\nworklist_floor=1\n[[scope]]\nname="web"\n'
        'paths=["src"]\nlanguages=["typescript"]\ncoverage_optional=true\n', encoding="utf-8")
    git_commit_all(root, "two callbacks on one line")
    result = run_cli(root, "coverage", "--export", ".crapkit/rows.tsv")
    assert result.returncode == 0, result.stderr
    with (root / ".crapkit/rows.tsv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert len(rows) == 2
    assert {row["occurrence"] for row in rows} == {"1", "2"}
    return rows


def _json(root, *args):
    result = run_cli(root, *args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_same_line_callbacks_have_distinct_packets_and_claims(tmp_path):
    _repo(tmp_path)
    first = _json(tmp_path, "next-item", "--claim")["item"]
    second = _json(tmp_path, "next-item", "--claim")["item"]
    assert first["handle"] != second["handle"]
    assert _json(tmp_path, "next-item", "--claim")["empty"] is True
    packets = [_json(tmp_path, "brief", "src/app.ts", row["handle"], "--json")
               for row in (first, second)]
    assert {packet["scored"]["occurrence"] for packet in packets} == {1, 2}
    assert all(packet["source"] == _source().rstrip("\n") for packet in packets)
    result = run_cli(tmp_path, "brief", "src/app.ts", "1", "--json")
    assert result.returncode == 1
    assert "ambiguous" in result.stderr
    assert first["handle"] in result.stderr and second["handle"] in result.stderr
    _json(tmp_path, "claims", "release", "src/app.ts", first["handle"], "--json")
    assert _json(tmp_path, "next-item")["item"]["handle"] == first["handle"]


def test_legacy_mark_is_preserved_and_refused_by_seed_rescore_and_hook(tmp_path):
    rows = _repo(tmp_path)
    marks = tmp_path / "crapkit-ratchet.tsv"
    text = f'# {metric_version()}\nsrc/app.ts\t{rows[0]["long_name"]}\t99\nsafe.py\tg( )\t17\n'
    marks.write_text(text, encoding="utf-8")
    before = marks.read_bytes()
    for args in (("ratchet", "seed"), ("rescore", "src/app.ts", "--gate", "--json")):
        result = run_cli(tmp_path, *args)
        assert result.returncode == 3, result.stderr
        assert "legacy ratchet key identity is ambiguous" in result.stderr
        assert marks.read_bytes() == before
    source = tmp_path / "src/app.ts"
    source.write_text(_source().replace("return x;", "return x + 1;"), encoding="utf-8")
    git(tmp_path, "add", "src/app.ts")
    result = run_cli(tmp_path, "hook-precommit")
    assert result.returncode == 3, result.stderr
    assert "legacy ratchet key identity is ambiguous" in result.stderr
    assert marks.read_bytes() == before


def test_new_ratchet_has_one_mark_for_each_same_line_callback(tmp_path):
    _repo(tmp_path)
    result = run_cli(tmp_path, "ratchet", "seed")
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "crapkit-ratchet.tsv").read_text(encoding="utf-8")
    assert "# crapkit-keys=1\n" in text
    marks = [line for line in text.splitlines() if line.startswith("src/app.ts\t")]
    assert len(marks) == 2
    assert any("#2\t" in mark for mark in marks)
