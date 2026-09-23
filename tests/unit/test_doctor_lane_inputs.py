"""doctor fails on a lane `inputs` entry that matches no path git can see.

git reads inputs as literal pathspecs, so a typo such as `scr` for `src` loads,
matches nothing, and leaves the lane reusable by --reuse-unchanged while its
real sources change. The config still loads, so every other command keeps
working; doctor names the entry as a problem and exits 1.
"""
import json

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.cli import main


def _inputs(root, lane: str, entries: list[str]) -> None:
    toml = root / "crapkit.toml"
    anchor = f'name = "{lane}"\n'
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace(anchor, f"{anchor}inputs = {json.dumps(entries)}\n", 1),
                    encoding="utf-8")


def _doctor(root, capsys) -> tuple[int, list[str]]:
    code = main(["doctor", "--json", "--repo", str(root)])
    payload = json.loads(capsys.readouterr().out)
    return code, [text for text in payload["problems"] if "inputs entry" in text]


def _unmatched(lane: str, entry: str) -> str:
    return (f"lane {lane!r}: inputs entry {entry!r} matches no file that is tracked, or "
            "untracked and not ignored, so --reuse-unchanged never sees a change through "
            "it; fix the spelling or drop the entry")


def test_an_input_that_matches_no_path_fails_doctor(repo, capsys):
    _inputs(repo, "unit", ["scr", "crapkit.toml"])

    assert _doctor(repo, capsys) == (1, [_unmatched("unit", "scr")])


def test_tracked_and_untracked_inputs_pass(repo, capsys):
    (repo / "web" / "draft.ts").write_text("export const draft = 1;\n", encoding="utf-8")
    _inputs(repo, "unit", ["src", "src/app.ts", "."])
    _inputs(repo, "ui", ["web/draft.ts"])

    assert _doctor(repo, capsys) == (0, [])


def test_an_ignored_path_fails_doctor(repo, capsys):
    """Reuse reads untracked changes with --exclude-standard, so an edit under an
    ignored directory never reruns the lane either."""
    (repo / "coverage").mkdir()
    (repo / "coverage" / "unit.json").write_text("{}", encoding="utf-8")
    _inputs(repo, "ui", ["coverage"])

    assert _doctor(repo, capsys) == (1, [_unmatched("ui", "coverage")])


def test_a_path_that_only_starts_like_an_input_does_not_match_it(repo, capsys):
    _inputs(repo, "unit", ["sr"])

    assert _doctor(repo, capsys) == (1, [_unmatched("unit", "sr")])


def test_no_inputs_asks_git_nothing(tmp_path):
    """tmp_path is no repo, so any git read would raise."""
    from crapkit.lane_changes import visible_paths

    assert visible_paths(tmp_path, []) == ()
