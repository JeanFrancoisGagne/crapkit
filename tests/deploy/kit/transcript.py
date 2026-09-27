"""What a cell did, in the order it did it, for whoever reads a red cell.

Each cell gets one transcript: every command with its cwd, exit code, stdout,
stderr and duration, plus notes, attachments (shim records, state manifests)
and the versions of git, node and the OS the run used. It is written as JSON
and as plain text under <out>/transcripts/, and the text form is what a
failed assertion quotes.
"""
from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

TAIL = 2000


@dataclass
class Step:
    argv: list[str]
    cwd: str
    exit: int
    stdout: str
    stderr: str
    seconds: float
    note: str = ""

    def text(self) -> str:
        head = f"$ {' '.join(self.argv)}    [cwd {self.cwd}, exit {exit_text(self.exit)}, {self.seconds:.1f}s]"
        body = [f"  {self.note}"] if self.note else []
        body += _indented("stdout", self.stdout) + _indented("stderr", self.stderr)
        return "\n".join([head, *body])


def exit_text(code: int) -> str:
    """An exit code as a reader of a red cell needs it: a Windows crash
    status also in hex (3221226505 is 0xC0000409, a process that died
    without choosing its exit), a POSIX signal by number."""
    if code >= 0xC0000000:
        return f"{code} (0x{code:08X}, a crash)"
    return f"{code} (signal {-code})" if code < 0 else str(code)


def shown(text: str) -> str:
    """The text as a terminal leaves it: each line keeps what came after its
    last carriage return, so a clone's "Updating files:  37%" progress, written
    over itself thousands of times, no longer fills the tail an error ends."""
    lines = text.replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in lines)


def _indented(label: str, text: str) -> list[str]:
    text = shown(text)
    if not text.strip():
        return []
    tail = text[-TAIL:]
    return [f"  --- {label}" + (" (tail)" if len(text) > TAIL else "")] + [f"  {line}" for line in tail.splitlines()]


@dataclass
class Transcript:
    name: str
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attachments: dict[str, object] = field(default_factory=dict)

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def note(self, text: str) -> None:
        self.notes.append(text)

    def attach(self, name: str, data: object) -> None:
        self.attachments[name] = data

    def text(self) -> str:
        lines = [f"# {self.name}", *(f"# note: {note}" for note in self.notes)]
        return "\n".join(lines + [step.text() for step in self.steps]) + "\n"

    def data(self) -> dict:
        return {"name": self.name, "platform": platform.platform(), "notes": self.notes,
                "steps": [step.__dict__ for step in self.steps], "attachments": self.attachments}

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stem = safe_name(self.name)
        (directory / f"{stem}.json").write_text(json.dumps(self.data(), indent=2, default=str),
                                                encoding="utf-8")
        path = directory / f"{stem}.txt"
        path.write_text(self.text(), encoding="utf-8")
        return path


def safe_name(name: str) -> str:
    """A test node id as a file name every OS accepts."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:150]
