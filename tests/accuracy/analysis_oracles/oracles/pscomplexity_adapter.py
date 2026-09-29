"""PSComplexity 0.5.1 cyclomatic and cognitive complexity, read per unit.

PSComplexity (https://github.com/Fortigi/PSComplexity, PowerShell 7 only)
walks the PowerShell AST. Its README and src/Cyclomatic.ps1 count 1 plus each
if and elseif clause, each switch clause, each loop, each catch and trap, each
ternary, each -and and -or, each && and ||, each ?? and ??=, and each
ForEach-Object and Where-Object command with its aliases (foreach, %, where,
?). Its cognitive count is the Sonar paper's with PowerShell additions
(src/Cognitive.ps1): those commands and ?? score like a structure, and a
script block raises the nesting level. A unit is each function and filter,
each class member, and one `<script-body>` per file; a decision belongs to
the nearest unit around it.

measure() starts one pwsh process for the whole file list, runs
`Measure-PSComplexity -Detailed` from `root`, and answers
{(path, line): Unit} for every unit that is not the script body, where
`line` is where the unit starts. The transforms from these counts to
crapkit's documented ones live in test_powershell_oracles.py as rulings rows.
No crapkit import.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile

import hang_guard

VERSION = "0.5.1"
SCRIPT_BODY = "<script-body>"
# The pwsh script: the file list and the output file are its two parameters.
DRIVER = f"""param([string] $List, [string] $Out)
$ErrorActionPreference = 'Stop'
Import-Module PSComplexity -RequiredVersion {VERSION}
$paths = [System.IO.File]::ReadAllLines($List, [System.Text.Encoding]::UTF8)
$units = @(Measure-PSComplexity -Path $paths -Detailed)
$json = ConvertTo-Json -InputObject $units -Depth 6 -Compress
[System.IO.File]::WriteAllText($Out, $json, [System.Text.UTF8Encoding]::new($false))
"""


@dataclass(frozen=True)
class Unit:
    name: str
    ccn: int
    cognitive: int
    contributions: tuple  # (line, construct, amount) per cognitive increment


def _unit(record: dict) -> Unit:
    parts = tuple((part["Line"], part["Construct"], part["Amount"])
                  for part in record.get("Contributions") or ())
    return Unit(record["Unit"], record["Cyclomatic"], record["Cognitive"], parts)


def measure(root: Path, paths: list, pwsh: str = "pwsh") -> dict:
    work = Path(tempfile.mkdtemp(prefix="pscomplexity-"))
    (work / "list.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
    (work / "driver.ps1").write_text(DRIVER, encoding="utf-8")
    done = hang_guard.run([pwsh, "-NoProfile", "-NonInteractive", "-File", str(work / "driver.ps1"),
                           str(work / "list.txt"), str(work / "units.json")],
                          cwd=root, text=True, encoding="utf-8", errors="replace")
    out = work / "units.json"
    assert done.returncode == 0 and out.is_file(), done.stdout + done.stderr
    records = json.loads(out.read_text(encoding="utf-8"))
    return {(record["File"], record["Line"]): _unit(record) for record in records
            if record["Unit"] != SCRIPT_BODY}


def by_construct(unit: Unit, construct: str) -> int:
    """The cognitive points one construct kind added to a unit."""
    return sum(amount for _, kind, amount in unit.contributions if kind == construct)
