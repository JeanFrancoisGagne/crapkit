r"""Spellings of one path, for the tests that feed each through a reader.

A case-insensitive disk, a junction or symlink, and a UNC alias of a local drive
each name the checkout in a way its text does not show. These helpers build the
spelling on the OS that has it and say when the OS has none, so a test can skip
with that reason and still run on the CI job that has it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

WINDOWS = os.name == "nt"
only_windows = pytest.mark.skipif(not WINDOWS, reason="needs Windows path rules")
only_posix = pytest.mark.skipif(WINDOWS, reason="needs POSIX path rules")


def case_insensitive(folder: Path) -> bool:
    """Does the filesystem under `folder` open a name in another letter case?"""
    probe = folder / "Case-Probe"
    probe.write_text("", encoding="utf-8")
    try:
        return (folder / "case-probe").exists()
    finally:
        probe.unlink()


def need_case_insensitive(folder: Path) -> None:
    if not case_insensitive(folder):
        pytest.skip("needs a case-insensitive filesystem (Windows NTFS, macOS APFS)")


def need_case_sensitive(folder: Path) -> None:
    if case_insensitive(folder):
        pytest.skip("needs a case-sensitive filesystem (Linux ext4)")


def link_directory(link: Path, target: Path) -> None:
    """A junction on Windows needs no symlink privilege; elsewhere a symlink."""
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def admin_share(path: Path) -> str:
    r"""`path` through this machine's own admin share, `\\localhost\C$\...`,
    or a skip when the share is not open to this account."""
    resolved = str(path.resolve())
    alias = "\\\\localhost\\" + resolved[0] + "$" + resolved[2:]
    if not os.path.exists(alias):
        pytest.skip("needs the local admin share (\\\\localhost\\C$) open to this account")
    return alias


def lower_drive(path: Path) -> str:
    resolved = str(path.resolve())
    return resolved[0].lower() + resolved[1:]


# A share on another host. No host here serves one, so the name is one that
# fails at once: `.invalid` never resolves (RFC 2606).
REMOTE_SHARE = "\\\\fileserver.invalid\\share"


def mapped_drive(monkeypatch, local: Path, share: str = REMOTE_SHARE) -> None:
    r"""Make `local` resolve the way a mapped network drive does.

    On Windows `Path("Z:/repo").resolve()` answers with the share behind the
    letter, `\\server\share\repo`. `local` and every path below it now resolve
    to `share` plus the same tail, as if `local` were the root of a drive
    mapped to `share`; every other path resolves as before."""
    real = os.path.realpath
    prefix = os.path.normcase(str(local))

    def realpath(path, *args, **kwargs):
        text = os.path.abspath(os.fspath(path))
        if os.path.normcase(text) == prefix or os.path.normcase(text).startswith(prefix + os.sep):
            return share + text[len(prefix):]
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", realpath)
