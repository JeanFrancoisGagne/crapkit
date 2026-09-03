# The demo images

`docs/demo.gif` and `docs/demo.svg` are generated. Regenerate both with:

    python tools/demo/generate.py

It needs `Pillow`, `pytest-cov` and `bash` on PATH (Git Bash on Windows), and it
writes nothing outside `docs/`.

## What it runs

The generator copies `fixture/` into a temp directory, replays the commit plan in
`history/stages.json` to give the repo a real history, and then runs five commands
against it, capturing stdout, stderr and the exit status of each:

| # | Command | What the frame shows |
|---|---|---|
| 1 | `crapkit init` | the scopes it sniffed and the lane it detected |
| 2 | `crapkit coverage` | the lane running and a scored run |
| 3 | `crapkit worklist --top 5` | the ranked functions |
| 4 | `cat >> calc/grade.py <<'PY' …` then `crapkit claude-hook --protocol 1` | a shell heredoc adds a function at ccn 7 and the per-edit advisory reports it, exit 2 |
| 5 | `git add calc/grade.py` then `crapkit hook-precommit` | the commit gate refuses the staged file, exit 6 |

Nothing is scripted output. Change what a command prints and the next regeneration
shows the new words.

Two spellings meet in step 1. The frames type `crapkit init`, which is what a reader
installs, and the generator runs `python -m crapkit init` with this checkout's `src/`
on `PYTHONPATH`. Same code, and the module spelling is what keeps the demo describing
the tree it was generated in rather than whatever `crapkit` a PATH resolves first.
Every next step crapkit prints spells itself the way it was started, so under the
module run that is the interpreter's absolute path; `demo_run.redact` folds it back to
`crapkit` before the absolute-path check sees the frame.

## Why the fixture has a history

`history/` holds the earlier version of the two files that change twice. Worklist risk
is complexity times recency-weighted churn, so in a single-commit repo every file weighs
1.0 and the ranking is ccn order. Replaying four commits at fixed dates gives the ranking
something to rank.

## Determinism

Two runs on an unchanged tree write byte-identical images, so regenerating them is a
no-op in `git status`. Three things buy that:

- The fixture repo is committed at a **fixed identity and fixed dates**, so the commit
  sha the frames print is the same every time.
- Every captured line goes through `demo_run.redact`, which replaces the temp repo path
  in all its spellings, wall-clock stamps and durations. `demo_run.absolute_paths` then
  refuses the whole render if a machine path survived.
- Nothing in `demo_render.py` reads the clock or anything but the bundled font.

`tests/unit/test_demo_generator.py` pins the frame count, both byte-stabilities, the
absolute-path check and its positive control, and that the SVG parses as XML.

## The font

`fonts/DejaVuSansMono.ttf` is DejaVu Sans Mono (family "DejaVu Sans Mono", style "Book"),
copied from `matplotlib/mpl-data/fonts/ttf/` of matplotlib 3.10.8, which redistributes the
DejaVu release. `fonts/LICENSE_DEJAVU` is the license that came with it: the Bitstream
Vera license, which grants the right to redistribute. It is bundled rather than looked up
on the machine because a substituted font changes every glyph in the render, and because
the machine these images were generated on ships only Consolas, which cannot be
redistributed.

## The files

| Path | What it is |
|---|---|
| `generate.py` | the entry point: build, record, render, refuse |
| `demo_repo.py` | the throwaway git repo and its commit plan |
| `demo_run.py` | the five steps, the capture and the redaction |
| `demo_render.py` | transcript to frames, frames to GIF and SVG |
| `fixture/` | the demo repo's final tree, carrying no `.git` |
| `history/` | earlier versions of two files, plus `stages.json` |
| `steps/heredoc_body.txt` | the function the heredoc appends |
