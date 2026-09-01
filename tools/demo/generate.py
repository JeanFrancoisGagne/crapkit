"""Regenerate docs/demo.gif and docs/demo.svg from real command output.

    python tools/demo/generate.py

Builds a throwaway git repo from tools/demo/fixture, runs the five demo commands
against it with THIS checkout's crapkit, and renders what they printed. The
frames carry no machine path, no clock and no duration, so a second run on an
unchanged tree writes the same bytes and git reports nothing.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

import demo_render  # noqa: E402
import demo_repo  # noqa: E402
import demo_run  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"


def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DOCS,
                        help="where demo.gif and demo.svg are written")
    return parser.parse_args(argv)


def _transcript() -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        return demo_run.record(demo_repo.build(Path(tmp) / "ledger"))


def _refuse_absolute(frames: list) -> None:
    """A frame naming a machine path would be committed forever."""
    dirty = demo_run.absolute_paths(demo_render.frame_text(frames))
    if dirty:
        raise SystemExit(f"a frame still carries an absolute path: {dirty[0]!r}")


def _report(gif: Path, svg: Path, frames: list) -> None:
    seconds = sum(duration for _, duration in frames) / 1000
    print(f"{len(frames)} frames, {seconds:.1f}s")
    print(f"{gif.as_posix()} {gif.stat().st_size} bytes")
    print(f"{svg.as_posix()} {svg.stat().st_size} bytes")
    if gif.stat().st_size > demo_render.GIF_CEILING:
        raise SystemExit(f"demo.gif is over the {demo_render.GIF_CEILING} byte ceiling")


def main(argv=None) -> int:
    demo_run.require_bash()
    args = _parse(argv)
    transcript = _transcript()
    frames = demo_render.frames(transcript)
    _refuse_absolute(frames)
    canvas = demo_render.size(transcript)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    gif, svg = args.out_dir / "demo.gif", args.out_dir / "demo.svg"
    demo_render.render_gif(frames, gif, canvas)
    demo_render.render_svg(frames, svg, canvas)
    _report(gif, svg, frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
