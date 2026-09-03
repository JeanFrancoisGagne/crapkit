"""The audited override: three records or nothing.

An exemption exists only if all three surfaces carry it: the alert (a human
channel sees one line), the committed ratchet (the debt is diff-visible), and
the snapshot store (the run remembers). The alert fires first because it is the
step most likely to fail; a partial override fails loudly and grants nothing.
No environment-variable or silent bypass exists anywhere in crapkit.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import ConfigError, ToolError
from .keys import stated_key
from .ratchet import RatchetEntry, dump_ratchet, load_ratchet
from .store import SnapshotStore
from .verify import GateViolation


def record_override(
    *,
    store: SnapshotStore,
    run_id: int,
    root: Path,
    ratchet_file: str,
    alert_command: str,
    violations: list[GateViolation],
    reason: str,
    raise_marks: bool = True,
) -> None:
    _require_auditable_override(reason, alert_command)
    _alert_or_refuse(alert_command, root, violations, reason)

    # Audit before grant: the snapshot record lands BEFORE the ratchet write,
    # because the ratchet entry is the functional exemption. A failure between
    # the two leaves an audit trail with no grant, never a grant with no trail.
    store.write_overrides(run_id, [(v.path, v.long_name, v.crap, reason) for v in violations])

    _grant_ratchet_debt(root / ratchet_file, violations, raise_marks=raise_marks)


def _require_auditable_override(reason: str, alert_command: str) -> None:
    """Refuse an override that could not be audited even if every step succeeded."""
    if not reason.strip():
        raise ConfigError("an override requires a non-empty reason")
    if not alert_command.strip():
        raise ConfigError(
            "no alert_command configured — the override requires a visible alert line; "
            "set [crapkit] alert_command in crapkit.toml")


def _alert_or_refuse(alert_command: str, root: Path, violations: list[GateViolation],
                     reason: str) -> None:
    """Put the debt in front of a human first; a silent alert grants nothing."""
    summary = "; ".join(f"{v.path}:{v.start} {v.long_name} crap={v.crap:.1f}" for v in violations)
    line = f"crapkit OVERRIDE ({reason}): {summary}"
    # The line reaches the alert command on stdin, never interpolated into the
    # shell string: function names come from analyzed source and are not shell-safe.
    proc = subprocess.run(alert_command, shell=True, cwd=root, input=line + "\n",
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ToolError(
            f"override alert command failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[-300:]} — no alert, no override")


def _grant_ratchet_debt(ratchet_path: Path, violations: list[GateViolation], *,
                        raise_marks: bool) -> None:
    """The functional exemption: the debt enters the committed ratchet, diff-visible."""
    by_key = _marks_by_key(ratchet_path)
    for v in violations:
        key = stated_key(v)
        mark = _override_mark(by_key.get(key), v.crap, raise_marks=raise_marks)
        by_key[key] = RatchetEntry(key[0], key[1], round(mark, 4))
    ratchet_path.write_text(dump_ratchet(list(by_key.values())), encoding="utf-8", newline="\n")


def _marks_by_key(ratchet_path: Path) -> dict[tuple[str, str], RatchetEntry]:
    """Prior marks by (path, key name); an absent ratchet file is simply no marks."""
    # utf-8-sig: a marks file PowerShell 5.1 saved carries a BOM, and the other
    # readers of it already tolerate one.
    existing = load_ratchet(ratchet_path.read_bytes().decode("utf-8-sig")) if ratchet_path.is_file() else []
    return {(e.path, e.long_name): e for e in existing}


def _override_mark(prior: RatchetEntry | None, crap: float, *, raise_marks: bool) -> float:
    """The mark this override records for one function."""
    # raise_marks=False is the hook path: it synthesizes worst-case crap (no
    # coverage data), and letting that raise a measured mark would blind the
    # ratchet to a later real coverage collapse. The prior tighter mark stays,
    # so the NEXT verify still demands repayment; the override only lets this
    # one commit through.
    if prior is None:
        return crap
    if raise_marks:
        return max(prior.crap, crap)
    return prior.crap
