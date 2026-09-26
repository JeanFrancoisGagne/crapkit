---
status: accepted
---

# A pasted command never opens with a quote, so cmd.exe loses a spaced venv

When `python -m crapkit` started crapkit, every next step and refusal it prints opens with the interpreter's path (`src/crapkit/invocation.py`). On Windows a reader pastes that line into cmd.exe, Windows PowerShell, pwsh or Git Bash, and each reads a path its own way. Git Bash reads a bare backslash as an escape. PowerShell reads a line that opens with a double quote as a string, so `"C:\Program Files\Python311\python.exe" -m crapkit coverage` stops at `-m` with a parse error. cmd.exe hands the line to the program as typed. crapkit spells the path with forward slashes and quotes only the segment that needs it, `C:/"Program Files"/Python311/python.exe`, so the line never opens with a quote, and cmd.exe, PowerShell, pwsh and Git Bash all run it.

One case has no line that runs in all four: a venv's `python.exe` in a directory whose name holds a space. A venv's `python.exe` is a launcher that reads its own name off the command line it was started with. Unless that line opens with a double quote, the name ends at the first space, and the launcher hands the rest to the base interpreter as a script path (exit 2). From cmd.exe the line is what the reader typed, and PowerShell cannot take one that opens with a quote. Before it quotes anything, crapkit looks for a spelling of the same file with no space: the directories a link crosses, resolved, then the 8.3 short name the volume keeps. Only a real spaced directory on a volume with no 8.3 names is left, and for that one crapkit prints the quoted-segment spelling, which runs in PowerShell, pwsh and Git Bash and fails in cmd.exe.

Up to 0.8.0 crapkit printed that path whole in double quotes. That line ran in cmd.exe and Git Bash and failed in both PowerShells, for every spaced path, venv or not. The trade is deliberate: PowerShell is the shell Windows Terminal opens by default, and coding agents on Windows run their commands through PowerShell or Git Bash. The same spelling serves the install line a pytest-cov note prints for a lane that names its python by path.

Alternatives we did not take:

- The whole path in double quotes, the 0.8.0 spelling: PowerShell and pwsh fail on every spaced path.
- `& "path"`, PowerShell's call operator: a syntax error in cmd.exe and Git Bash.
- Bare `python` when PATH resolves it to the running interpreter: a shell with another PATH reaches another interpreter, which is the case the git hook's absolute path exists for.
- Guessing the shell from the parent process: the reader pastes into whatever shell they have open, often not the one that started crapkit.

Consequence: in cmd.exe, a reader whose venv sits in such a directory puts the whole path in double quotes by hand, or activates the venv and runs `crapkit`. `tests/e2e/test_next_step_pastes_e2e.py` pins the limit as a strict xfail for cmd.exe, so a spelling that fixes it turns that test red and this record stale.
