"""Turn a transcript into the animated GIF and the animated SVG.

The screen is a scrolling buffer, the way a terminal is: rows are pushed on the
bottom and the viewport shows the last `rows` of them. A frame is a snapshot of
that viewport plus how long it holds. Only the command lines are typed a few
characters at a time; output arrives in one frame and then sits still, because
the reading happens there.

Nothing here reads the clock or the filesystem beyond the bundled font, so two
renders of one transcript are byte-identical. That is what makes regenerating
the images on an unchanged tree a no-op in git.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
FONT = HERE / "fonts" / "DejaVuSansMono.ttf"

GIF_CEILING = 1_500_000

FONT_SIZE = 15
LINE_H = 20
PAD_X = 16
PAD_Y = 14
BASELINE = 15

TYPE_CHUNK = 3
TYPE_MS = 45
LINE_MS = 380
SETTLE_MS = 350
FINAL_MS = 8000
PROMPT = "$ "

BG = (18, 20, 24)
INK = {"cmd": (126, 214, 140), "out": (208, 212, 218),
       "err": (233, 180, 96), "note": (137, 143, 154)}
STYLES = ("cmd", "out", "err", "note")
_SHADES = 6


def _wrap(text: str, columns: int) -> list[str]:
    """A terminal's own hard wrap: no word breaking and no continuation indent."""
    if not text:
        return [""]
    return [text[i:i + columns] for i in range(0, len(text), columns)]


class Screen:
    """The scrolling buffer the frames are snapshots of."""

    def __init__(self, columns: int, rows: int) -> None:
        self.columns, self.rows = columns, rows
        self._buffer: list[tuple[str, str]] = []

    def push(self, style: str, text: str) -> None:
        for piece in _wrap(text, self.columns):
            self._buffer.append((style, piece))

    def set_last(self, style: str, text: str) -> None:
        self._buffer[-1] = (style, text[:self.columns])

    def frame(self, duration_ms: int) -> tuple:
        view = self._buffer[-self.rows:]
        pad = [("out", "")] * (self.rows - len(view))
        return (tuple(view) + tuple(pad), duration_ms)


def _typed(screen: Screen, command: str) -> list:
    """One frame per few characters, so the command reads as typed."""
    text = PROMPT + command
    screen.push("cmd", "")
    out = []
    for end in range(TYPE_CHUNK, len(text) + TYPE_CHUNK, TYPE_CHUNK):
        screen.set_last("cmd", text[:end])
        out.append(screen.frame(TYPE_MS))
    return out


def _continued(screen: Screen, lines: tuple) -> list:
    """The rest of a multi-line command (a heredoc body), one frame per line."""
    out = []
    for line in lines:
        screen.push("cmd", line)
        out.append(screen.frame(LINE_MS))
    return out


def _step_frames(screen: Screen, step: dict) -> list:
    shown = step["shown"]
    out = _typed(screen, shown[0]) + _continued(screen, tuple(shown[1:]))
    out.append(screen.frame(SETTLE_MS))
    for style, text in step["output"]:
        screen.push(style, text)
    if step["exit"]:
        screen.push("note", f"[exit {step['exit']}]")
    screen.push("out", "")
    out.append(screen.frame(int(step["hold"] * 1000)))
    return out


def frames(transcript: dict) -> list:
    """Every frame of the demo: (rows, duration in ms), oldest first."""
    screen = Screen(transcript["columns"], transcript["rows"])
    out = []
    for step in transcript["steps"]:
        out += _step_frames(screen, step)
    out.append(screen.frame(FINAL_MS))
    return out


def frame_text(frames_: list) -> list[str]:
    """Every line of text any frame shows. What the no-absolute-path check reads."""
    return [text for rows, _ in frames_ for _, text in rows]


def size(transcript: dict) -> tuple[int, int]:
    return (PAD_X * 2 + int(round(transcript["columns"] * _char_width())),
            PAD_Y * 2 + transcript["rows"] * LINE_H)


def _char_width() -> float:
    from PIL import ImageFont

    return ImageFont.truetype(str(FONT), FONT_SIZE).getlength("M")


def _shade(ink: tuple, level: int) -> tuple:
    share = level / _SHADES
    return tuple(round(BG[i] + (ink[i] - BG[i]) * share) for i in range(3))


def _palette() -> list[int]:
    """One fixed palette for every frame: an adaptive one per frame would move
    the colours between frames and cost the GIF its delta encoding."""
    colours = [BG] + [_shade(INK[style], level)
                      for style in STYLES for level in range(1, _SHADES + 1)]
    flat = [channel for colour in colours for channel in colour]
    return flat + flat[-3:] * (256 - len(colours))


def _palette_image():
    from PIL import Image

    image = Image.new("P", (1, 1))
    image.putpalette(_palette())
    return image


def _draw(rows: tuple, font, canvas: tuple, palette):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", canvas, BG)
    draw = ImageDraw.Draw(image)
    for index, (style, text) in enumerate(rows):
        if text:
            draw.text((PAD_X, PAD_Y + index * LINE_H + BASELINE), text, font=font,
                      fill=INK[style], anchor="ls")
    return image.quantize(palette=palette, dither=Image.Dither.NONE)


def render_gif(frames_: list, path: Path, canvas: tuple) -> None:
    from PIL import ImageFont

    font = ImageFont.truetype(str(FONT), FONT_SIZE)
    palette = _palette_image()
    images = [_draw(rows, font, canvas, palette) for rows, _ in frames_]
    images[0].save(path, save_all=True, append_images=images[1:], loop=0,
                   duration=[duration for _, duration in frames_], disposal=1)


def _svg_text(rows: tuple) -> str:
    out = []
    for index, (style, text) in enumerate(rows):
        if text:
            out.append(f'<text x="{PAD_X}" y="{PAD_Y + index * LINE_H + BASELINE}" '
                       f'class="{style}">{escape(text)}</text>')
    return "".join(out)


def _keyframes(start: float, end: float) -> tuple[str, str]:
    """values and keyTimes for one frame's visibility over the whole loop."""
    if start <= 0:
        return ("1;0;0", f"0;{end:.5f};1")
    if end >= 1:
        return ("0;1;1", f"0;{start:.5f};1")
    return ("0;1;0;0", f"0;{start:.5f};{end:.5f};1")


def _svg_frame(rows: tuple, start: float, end: float, total_s: float) -> str:
    values, times = _keyframes(start, end)
    return (f'<g opacity="0">{_svg_text(rows)}'
            f'<animate attributeName="opacity" calcMode="discrete" '
            f'values="{values}" keyTimes="{times}" dur="{total_s:.3f}s" '
            f'repeatCount="indefinite"/></g>')


def _svg_style() -> str:
    fills = "".join(f"text.{style}{{fill:rgb{INK[style]}}}" for style in STYLES)
    return (f"<style>text{{font-family:'DejaVu Sans Mono',Menlo,Consolas,monospace;"
            f"font-size:{FONT_SIZE}px;white-space:pre}}{fills}</style>")


def render_svg(frames_: list, path: Path, canvas: tuple) -> None:
    total_ms = sum(duration for _, duration in frames_)
    width, height = canvas
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="crapkit demo">{_svg_style()}'
             f'<rect width="{width}" height="{height}" fill="rgb{BG}"/>']
    elapsed = 0
    for rows, duration in frames_:
        parts.append(_svg_frame(rows, elapsed / total_ms,
                                (elapsed + duration) / total_ms, total_ms / 1000))
        elapsed += duration
    parts.append("</svg>\n")
    path.write_text("".join(parts), encoding="utf-8", newline="\n")
