"""tools/release/README.md walks the chain `release.py plan` prints, all of it.

The README's command list once ended at `release.py verify VERSION`, one
command short of the last stage: `run surfaces` reads the surfaces back and
then dispatches deploy.yml's published cadence, whose red run fails the
release. A reader who followed the list never started that run. The same page
named four of the six files whose Codex `--ref` stage 1 rewrites.
"""
import re
from pathlib import Path

from test_release_tool import release

README = Path(__file__).resolve().parents[2] / "tools" / "release" / "README.md"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _first_block() -> list[str]:
    """The command list: the first fenced block on the page."""
    return _text().split("```", 2)[1].strip().splitlines()


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_command_list_runs_every_stage_of_the_plan_in_its_order():
    stages = list(dict.fromkeys(step.stage for step in release.plan("9.9.9")))
    runs = [line.split()[-2] for line in _first_block() if " run " in line]

    assert runs == stages


def test_the_command_list_is_all_release_py_commands():
    assert all(line.startswith("python tools/release/release.py ") for line in _first_block())


def test_the_page_says_how_to_wait_on_the_published_run():
    (published,) = [step for step in release.plan("9.9.9") if step.name == "published"]
    text = _flat(_text())

    assert "cadence=published" in " ".join(published.commands[0])
    assert "`run surfaces`" in text and "published cadence" in text
    assert "gh run list --workflow deploy.yml --limit 1" in text
    assert "gh run watch RUN_ID --exit-status" in text


def test_the_page_previews_the_release_body_with_notes():
    assert "python tools/release/release.py notes VERSION > notes.md" in _flat(_text())


def test_the_page_quotes_each_line_check_prints_for_a_gh_that_cannot_dispatch(tmp_path):
    kit = tmp_path / "tools" / "deploy" / "candidate.py"
    kit.parent.mkdir(parents=True)
    kit.write_text("# the deploy kit\n", encoding="utf-8")
    lines = (release.deploy_preflight(tmp_path, which=lambda name: None)
             + release.deploy_preflight(tmp_path, which=lambda name: name, token=lambda: ""))

    assert len(lines) == 2
    assert [line for line in lines if line not in _text().splitlines()] == []


def test_the_page_names_every_file_whose_codex_ref_stage1_rewrites():
    files = sorted({surface.path for surface in release.SURFACES if surface.pattern == "--ref v{v}"})
    text = _flat(_text())
    sentence = text[text.index("Stage 1 rewrites the `--ref`"):]
    sentence = sentence[:sentence.index("with the other version surfaces")]

    assert [path for path in files if path not in sentence] == []
    assert len(files) == 6
