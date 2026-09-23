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
from .ratchet import RatchetEntry
from .ratchetfile import RatchetFile
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
    key_version: int | None = None,
    identity_rows=None,
    ratchet_input: RatchetFile | None = None,
    metric: str,
) -> None:
    """`metric` is the metric that scored the violations, and every caller
    names it. A measured grant (verify's, which may raise a mark) is refused
    by marks another metric recorded, the refusal verify itself gives, and by
    an empty metric. The hook's grant (`raise_marks=False`) synthesizes its
    numbers from ccn alone and compares no mark, so it keeps the recorded
    stamps: a stale file stays stale and verify keeps refusing it. `metric`
    then stamps only a file the grant creates."""
    saved = ratchet_input or RatchetFile.read(root / ratchet_file)
    text = _checked_grant_text(saved, violations, raise_marks=raise_marks, keys=key_version,
                               metric=metric)
    _validate_override_keys(text, identity_rows, key_version)
    _require_auditable_override(reason, alert_command)
    _alert_or_refuse(alert_command, root, violations, reason)

    # Audit before grant: the snapshot record lands BEFORE the ratchet write,
    # because the ratchet entry is the functional exemption. A failure between
    # the two leaves an audit trail with no grant, never a grant with no trail.
    store.write_overrides(run_id, [(*stated_key(v), v.crap, reason) for v in violations])

    saved.publish(text)  # the functional exemption: the debt enters the ratchet, diff-visible


def _validate_override_keys(text: str, rows, key_version: int | None) -> None:
    """Reject a mixed-format grant before its alert or either durable record."""
    if rows is None or key_version != 0:
        return
    from .ratchet import checked_key_version

    try:
        checked_key_version(text, rows)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _checked_grant_text(saved: RatchetFile, violations: list[GateViolation], *,
                        raise_marks: bool, keys: int | None, metric: str) -> str:
    """The marks file after the grant, refused before any side effect when a
    reader cannot prove its keys.

    Both texts are checked. The saved marks can already lack proof. The grant
    text can lack it too: a kept analysis 9 stamp gives the `(anonymous)` key
    the grant adds no proof, and every later reader, seed included, refused the
    file it wrote.
    """
    _check_reader(saved.text or "")
    granted = _granted_marks(saved.entries, violations, raise_marks=raise_marks)
    text = _grant_text(saved, granted, raise_marks=raise_marks, keys=keys, metric=metric)
    _check_reader(text)
    return text


def _check_reader(text: str) -> None:
    from .ratchet import check_reader_keys

    try:
        check_reader_keys(text)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


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


def _granted_marks(prior: list[RatchetEntry], violations: list[GateViolation], *,
                   raise_marks: bool) -> list[RatchetEntry]:
    """The marks after the grant: each violation's debt entered or kept."""
    by_key = {(e.path, e.long_name): e for e in prior}
    for v in violations:
        key = stated_key(v)
        mark = _override_mark(by_key.get(key), v.crap, raise_marks=raise_marks)
        by_key[key] = RatchetEntry(key[0], key[1], round(mark, 4))
    return list(by_key.values())


def _grant_text(saved: RatchetFile, granted: list[RatchetEntry], *, raise_marks: bool,
                keys: int | None, metric: str) -> str:
    """The marks file after the grant, stamped by the rule its numbers fall under.

    Every grant that may raise a mark goes through `measured`, which refuses
    an empty metric rather than keeping whatever stamp was there.
    """
    if raise_marks:
        return saved.measured(granted, metric, keys=keys)
    return saved.kept(granted, keys=keys, new_file_metric=metric)


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
