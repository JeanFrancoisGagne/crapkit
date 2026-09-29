"""Paste lines into a real shell the way a reader does, and read what each one printed.

paste(shell, lines, cwd, env) runs each line as if typed at that shell's prompt
and returns one Pasted(code, stdout) per line.

- cmd and cmd-delayed (cmd.exe with /v:on): one `cmd /d /s /c "LINE"` process
  per line; /s makes cmd.exe strip the outer quotes and run LINE as typed.
- powershell (Windows PowerShell 5.1), pwsh (PowerShell 7) and bash: one
  process for all lines, from a script file. Each line is parsed when it runs
  (Invoke-Expression, eval), so a line the shell cannot parse fails alone and
  the lines after it still run.

The shells a platform's readers paste into (SHELLS) come from the accuracy
plan: cmd.exe, PowerShell 5.1, pwsh 7 and Git Bash on Windows, bash elsewhere.
A shell that is not installed fails the test that needs it, naming the fix.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import sys

import hang_guard
from accuracy.kit import tiers

SHELLS = {"win32": ("cmd", "cmd-delayed", "powershell", "pwsh", "bash")}
POSIX_SHELLS = ("bash",)
INSTALL = {
    "pwsh": "install PowerShell 7 (winget install Microsoft.PowerShell) or put pwsh on PATH",
    "powershell": "Windows PowerShell 5.1 ships with Windows; put powershell.exe on PATH",
    "bash": "install git for Windows, whose bin/bash.exe is Git Bash, or bash on POSIX",
}
_END = re.compile(r"^@@end (\d+) (-?\d+)\s*$")


class ShellMissing(AssertionError):
    """A shell the plan names is not installed here."""


@dataclass(frozen=True)
class Pasted:
    code: int
    stdout: str
    stderr: str = ""


def here(platform: str = sys.platform) -> tuple[str, ...]:
    return SHELLS.get(platform, POSIX_SHELLS)


def _git_bash() -> str | None:
    git = shutil.which("git")
    candidates = [Path(git).resolve().parents[1] / "bin" / "bash.exe"] if git else []
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin"
                      / "bash.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


def _find(shell: str) -> str | None:
    if shell.startswith("cmd"):
        return os.environ.get("COMSPEC") or shutil.which("cmd")
    if shell == "bash" and sys.platform == "win32":
        return _git_bash()
    return shutil.which(shell)


def executable(shell: str) -> str:
    found = _find(shell)
    if not found:
        raise ShellMissing(f"{shell} is not installed here: {INSTALL.get(shell, 'install it')}")
    return found


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _cmd(shell: str, line: str, cwd: Path, env: dict) -> Pasted:
    delayed = "/v:on " if shell == "cmd-delayed" else ""
    command = f'"{executable(shell)}" /d {delayed}/s /c "{line}"'
    done = hang_guard.run(command, cwd=cwd, env=env, text=True, encoding="utf-8",
                          errors="replace")
    return Pasted(done.returncode, done.stdout, done.stderr)


POWERSHELL_SCRIPT = """[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$lines = @({lines})
for ($i = 0; $i -lt $lines.Count; $i++) {{
  $line = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($lines[$i]))
  Write-Output "@@begin $i"
  $global:LASTEXITCODE = 0
  $ok = $true
  try {{ Invoke-Expression $line; $ok = $? }} catch {{ $ok = $false; Write-Output "$_" }}
  $code = if ($LASTEXITCODE -ne 0) {{ $LASTEXITCODE }} elseif ($ok) {{ 0 }} else {{ 1 }}
  Write-Output "@@end $i $code"
}}
"""
BASH_LINE = "echo \"@@begin {i}\"\neval \"$(printf '%s' '{b64}' | base64 -d)\"\necho \"@@end {i} $?\"\n"


def _script(shell: str, lines: list[str]) -> tuple[str, str]:
    """(file suffix, script text) that runs each line in turn between markers."""
    if shell == "bash":
        return ".sh", "".join(BASH_LINE.format(i=i, b64=_b64(line)) for i, line in enumerate(lines))
    quoted = ", ".join(f"'{_b64(line)}'" for line in lines)
    return ".ps1", POWERSHELL_SCRIPT.format(lines=quoted)


def _argv(shell: str, script: Path) -> list[str]:
    if shell == "bash":
        return [executable(shell), str(script)]
    return [executable(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script)]


def _chunks(stdout: str) -> dict[int, Pasted]:
    found: dict[int, Pasted] = {}
    chunk: list[str] = []
    for text in stdout.splitlines():
        end = _END.match(text)
        if end:
            found[int(end[1])] = Pasted(int(end[2]), "\n".join(chunk))
        chunk = [] if end or text.startswith("@@begin ") else [*chunk, text]
    return found


def split(stdout: str, count: int, stderr: str = "") -> list[Pasted]:
    """Each line's output between its @@begin and @@end markers, with its code."""
    found = _chunks(stdout)
    missing = [number for number in range(count) if number not in found]
    if missing:
        raise AssertionError(f"lines {missing} printed no end marker:\n{stdout[-2000:]}\n"
                             f"{stderr[-2000:]}")
    return [found[number] for number in range(count)]


def _batch(shell: str, lines: list[str], cwd: Path, env: dict, scratch: Path) -> list[Pasted]:
    suffix, text = _script(shell, lines)
    script = scratch / f"paste-{shell}{suffix}"
    script.write_bytes(text.encode("utf-8-sig" if suffix == ".ps1" else "utf-8"))
    done = hang_guard.run(_argv(shell, script), cwd=cwd, env=env, text=True, encoding="utf-8",
                          errors="replace")
    return split(done.stdout, len(lines), done.stderr)


def paste(shell: str, lines: list[str], cwd: Path, env: dict, scratch: Path) -> list[Pasted]:
    """Every line run as typed at `shell`'s prompt, in order."""
    tiers.require_process(shell)
    if shell.startswith("cmd"):
        return [_cmd(shell, line, cwd, env) for line in lines]
    return _batch(shell, lines, cwd, env, scratch)
