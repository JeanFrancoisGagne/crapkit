"""CRAPKIT_OVERRIDE_REASON passes a marked refusal and keeps the mark.

hook-precommit in process over a small repo. A marked function whose staged
ccn passed its mark is refused; with a reason set the commit is granted
through the full audit (alert, store, marks file), the mark keeps its value
byte for byte, and the marks file is staged only when the grant wrote it. The
next verify still reports the rise as a ratchet regression: a mark only falls.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import sqlite3
import stat

from cli_inproc_repo import add_knotty, commit_all, git, repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit import gate
from crapkit.cli import main, verifying
from crapkit.ratchet import metric_version
from crapkit.ratchetfile import RatchetFile
from crapkit.store import SnapshotStore

MARKS = "crapkit-ratchet.tsv"
REASON = "hotfix 412, decompose in the follow-up"
KNOTTY = "knotty ( n )"
LAST_IF = "  if (n > 7) { return 7; }\n"
ONE_MORE_IF = LAST_IF + "  if (n > 8) { return 8; }\n"
RISE_HEAD = "crapkit gate: 1 staged function(s) rose past their ratchet mark:"
RISE_FIX = "bring each one's ccn back to its mark or under it (a mark only falls, so no commit raises it)."
GRANTED = f"crapkit: override granted with full audit ({REASON})."
KEPT = "crapkit: 1 ratchet mark kept as it was (a mark only falls): {path} {name} {mark}"
CC_ONLY_SCOPE = """
[[scope]]
name = "cc"
paths = ["cc"]
languages = ["typescript"]
coverage_optional = true
"""
REFUSED_ARROW = "export const handlers = {\n  pick: ({ x }) => new Set<string>([x]).has(x),\n};\n"
ENCODINGS = {
    "utf-8": lambda text: text.encode("utf-8"),
    "utf-16-le-bom": lambda text: b"\xff\xfe" + text.encode("utf-16-le"),
    "crlf": lambda text: text.replace("\n", "\r\n").encode("utf-8"),
}


def run(argv, repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def marks_text(*rows: tuple[str, str, str]) -> str:
    """The marks file as crapkit writes it: stamp, key format, header, rows sorted."""
    lines = [f"# {metric_version()}", "# crapkit-keys=1", "path\tlong_name\tcrap"]
    return "\n".join(lines + ["\t".join(row) for row in sorted(rows)]) + "\n"


def legacy_marks_text(*rows: tuple[str, str, str]) -> str:
    """A marks file a release before 0.8.1 wrote, then edited by hand: no
    key-format line, and each mark as typed (7.5, not 7.5000)."""
    lines = [f"# {metric_version()}", "path\tlong_name\tcrap"]
    return "\n".join(lines + ["\t".join(row) for row in rows]) + "\n"


def committed(repo, *rows: tuple[str, str, str], encode=ENCODINGS["utf-8"], render=marks_text) -> bytes:
    """Commit the tree with `rows` as its marks file; return the file's bytes."""
    data = encode(render(*rows))
    (repo / MARKS).write_bytes(data)
    commit_all(repo, "marks")
    return data


def grow(repo, rel: str = "src/app.ts", twin: int = 1) -> int:
    """Stage one more branch in the `twin`th knotty of `rel`; return its first line."""
    path = repo / rel
    text = path.read_bytes().decode("utf-8")
    at = [i for i in range(len(text)) if text.startswith(LAST_IF, i)][twin - 1]
    path.write_bytes((text[:at] + ONE_MORE_IF + text[at + len(LAST_IF):]).encode("utf-8"))
    git(repo, "add", rel)
    starts = [n for n, line in enumerate(text.splitlines(), 1) if line.startswith("export function knotty")]
    return starts[twin - 1]


def add_scope_and_knotty(repo, source: str) -> None:
    """The cc-only scope in crapkit.toml, and a ccn-8 knotty in `source`."""
    with open(repo / "crapkit.toml", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(CC_ONLY_SCOPE)
    (repo / source).parent.mkdir(exist_ok=True)
    add_knotty(repo, source)


def staged_names(repo) -> list[str]:
    return git(repo, "diff", "--cached", "--name-only").splitlines()


def runs_and_overrides(repo) -> list[tuple[str, list]]:
    """Each stored run's kind with its override rows; [] when no store exists."""
    path = repo / ".crapkit" / "crap.sqlite"
    if not path.exists():
        return []
    store = SnapshotStore(path)
    try:
        return [(run["kind"], store.read_overrides(run["id"])) for run in store.list_runs()]
    finally:
        store._conn.close()


def alerts(repo) -> list[str]:
    log = repo / "alerts.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def clear_lines(capsys) -> list[str]:
    """The two clear-the-reason lines, as the receipt prints them on this platform."""
    verifying._print_clear_the_reason()
    return capsys.readouterr().out.splitlines()


@pytest.fixture()
def marked(repo, monkeypatch):
    """knotty (ccn 8) committed under the mark 7.3333, the reason set."""
    add_knotty(repo)
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", REASON)
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    return repo


# --- the grant ---------------------------------------------------------------------

def test_a_reason_grants_a_marked_rise_and_keeps_its_mark_byte_for_byte(marked, capsys):
    """Exit 0; one alert line names the function; one hook run and one override
    row at the range's high end (ccn 9, untested: 90); the marks file's bytes
    and parsed marks are what they were, and the file is not staged."""
    before = committed(marked, ("src/app.ts", KNOTTY, "7.3333"))
    parsed = RatchetFile.read(marked / MARKS).entries
    start = grow(marked)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 0, out + err
    assert out.splitlines() == [
        RISE_HEAD, f"  ccn   9  src/app.ts:{start}  {KNOTTY}  mark 7.3333", GRANTED,
        KEPT.format(path="src/app.ts", name=KNOTTY, mark="7.3333"), *clear_lines(capsys)]
    assert out.isascii()
    assert alerts(marked) == [f"crapkit OVERRIDE ({REASON}): src/app.ts:{start} {KNOTTY} crap=90.0"]
    assert runs_and_overrides(marked) == [("hook", [("src/app.ts", KNOTTY, 90.0, REASON)])]
    after = (marked / MARKS).read_bytes()
    assert hashlib.sha256(after).hexdigest() == hashlib.sha256(before).hexdigest()
    assert RatchetFile.read(marked / MARKS).entries == parsed
    assert MARKS not in staged_names(marked)


@pytest.mark.parametrize(("source", "mark"), [pytest.param("cc/rules.ts", "8.0000", id="cc-only"),
                                              pytest.param("src/fresh.ts", "72.0000", id="measured")])
def test_a_mixed_grant_marks_the_new_breach_keeps_the_marked_one_and_stages_the_file(
        marked, capsys, source, mark):
    """An unmarked ccn-8 function gains its scope's high end (Z24's values);
    the marked row keeps its bytes; the file the grant wrote is staged."""
    committed(marked, ("src/app.ts", KNOTTY, "7.3333"))
    grow(marked)
    add_scope_and_knotty(marked, source)
    git(marked, "add", "crapkit.toml", source)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 0, out + err
    assert (marked / MARKS).read_bytes().decode("utf-8") == marks_text(
        ("src/app.ts", KNOTTY, "7.3333"), (source, KNOTTY, mark))
    assert MARKS in staged_names(marked)
    assert KEPT.format(path="src/app.ts", name=KNOTTY, mark="7.3333") in out.splitlines(), out


def test_after_the_grant_verify_reports_the_rise_and_refuses_an_override(marked, capsys, monkeypatch):
    """cc-only, so the CRAP is the ccn: marked 8, committed at 9 through the
    grant. verify reads `recorded` 8.0 on a ratchet_regression item, and
    `verify --override` is refused with the regression and its escape."""
    git(marked, "checkout", "--", "src/app.ts")
    add_scope_and_knotty(marked, "cc/rules.ts")
    commit_all(marked, "rules")
    seed_artifacts(marked)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(marked)]) == 0
    committed(marked, ("cc/rules.ts", KNOTTY, "8.0000"))
    grow(marked, "cc/rules.ts")
    assert run(["hook-precommit"], marked, capsys)[0] == 0
    commit_all(marked, "rules grow past the mark")
    monkeypatch.delenv("CRAPKIT_OVERRIDE_REASON")

    code, out, _ = run(["verify", "--reuse-artifacts", "--json"], marked, capsys)
    refused, _, err = run(["verify", "--reuse-artifacts", "--override", REASON], marked, capsys)

    rises = [item for item in json.loads(out)["findings"] if item["kind"] == "ratchet_regression"]
    assert code == 6, out
    assert [(item["path"], item["long_name"], item["recorded"]) for item in rises] == [
        ("cc/rules.ts", KNOTTY, 8.0)]
    assert refused == 6, err
    assert "1 ratchet regression (cc/rules.ts knotty ( n )" in err, err
    assert err.rstrip().endswith("raise the mark by hand and commit it"), err


# --- variations ----------------------------------------------------------------------

def assert_nothing_granted(repo, before: bytes) -> None:
    """No alert line, no stored run, the marks file untouched and not staged."""
    assert alerts(repo) == []
    assert runs_and_overrides(repo) == []
    assert (repo / MARKS).read_bytes() == before
    assert MARKS not in staged_names(repo)


def test_a_marked_rise_beside_an_unread_file_exits_6_before_any_side_effect(marked, capsys):
    before = committed(marked, ("src/app.ts", KNOTTY, "7.3333"))
    grow(marked)
    (marked / "src" / "handlers.ts").write_bytes(REFUSED_ARROW.encode("utf-8"))
    git(marked, "add", "src/handlers.ts")

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 6, out + err
    assert RISE_HEAD in out and "crapkit: override refused:" in out, out
    assert_nothing_granted(marked, before)


def test_a_marked_rise_beside_a_claimed_name_exits_3_and_grants_nothing(marked, capsys, monkeypatch):
    """A name git gives in bytes that are not UTF-8 cannot be made on every
    file system, so the staged gate gains one beside the real rise."""
    before = committed(marked, ("src/app.ts", KNOTTY, "7.3333"))
    grow(marked)
    real = verifying._staged_gate
    claimed = gate.ChangedFile("src/caf\udce9.ts", gate.WHOLE, gate.UnreadableName("src/caf\udce9.ts", "src"))

    def with_claimed(*args, **kwargs):
        staged = real(*args, **kwargs)
        return staged._replace(changes=(*staged.changes, claimed))

    monkeypatch.setattr(verifying, "_staged_gate", with_claimed)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 3, out + err
    assert "src/caf\\xe9.ts" in err, err
    assert_nothing_granted(marked, before)


def test_a_warned_rise_under_a_reason_grants_and_records_nothing(marked, capsys):
    """Marked 30 at ccn 8 and staged at ccn 9: the range 9 to 90 straddles the
    mark and the ccn rose, so the hook warns and nothing refused it."""
    before = committed(marked, ("src/app.ts", KNOTTY, "30.0000"))
    grow(marked)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 0, out + err
    assert "rose in ccn and may pass their ratchet mark" in err, err
    assert out == "", out
    assert_nothing_granted(marked, before)


def test_a_blank_reason_is_refused_as_no_reason(marked, capsys, monkeypatch):
    before = committed(marked, ("src/app.ts", KNOTTY, "7.3333"))
    grow(marked)
    monkeypatch.setenv("CRAPKIT_OVERRIDE_REASON", "   ")

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 6, out + err
    assert out.startswith(RISE_HEAD) and out.endswith(RISE_FIX + "\n"), out
    assert_nothing_granted(marked, before)


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_a_kept_mark_leaves_the_marks_file_byte_identical_in_any_encoding(marked, capsys, encoding):
    before = committed(marked, ("src/app.ts", KNOTTY, "7.3333"), encode=ENCODINGS[encoding])
    grow(marked)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 0, out + err
    assert (marked / MARKS).read_bytes() == before
    assert MARKS not in staged_names(marked)


FORMATS = {"current": (marks_text, "7.3333"), "pre-0.8.1": (legacy_marks_text, "7.5")}


def as_committed(repo, render, mark: str) -> bytes:
    """The marks committed, nothing left beside the index."""
    return committed(repo, ("src/app.ts", KNOTTY, mark), render=render)


def edited_beside_the_index(repo, render, mark: str) -> bytes:
    """The marks committed, then one more row written and left unstaged."""
    committed(repo, ("src/app.ts", KNOTTY, mark), render=render)
    data = render(("src/app.ts", KNOTTY, mark), ("src/app.ts", "dispatch ( kind )", "56.0000")).encode("utf-8")
    (repo / MARKS).write_bytes(data)
    return data


def never_tracked(repo, render, mark: str) -> bytes:
    """knotty committed with no marks file, then a marks file git never tracked."""
    commit_all(repo, "knotty")
    data = render(("src/app.ts", KNOTTY, mark)).encode("utf-8")
    (repo / MARKS).write_bytes(data)
    return data


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("leave", [pytest.param(as_committed, id="committed"),
                                   pytest.param(edited_beside_the_index, id="unstaged-edit"),
                                   pytest.param(never_tracked, id="untracked")])
def test_a_kept_mark_writes_and_stages_no_marks(marked, capsys, leave, fmt):
    """A grant that only kept marks writes nothing and stages nothing. A file
    with no key-format line and a hand-typed 7.5 keeps those bytes, and an
    unstaged edit of the marks file, or a marks file git never tracked, stays
    out of the commit as it was."""
    render, mark = FORMATS[fmt]
    before = leave(marked, render, mark)
    grow(marked)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 0, out + err
    assert KEPT.format(path="src/app.ts", name=KNOTTY, mark=f"{float(mark):.4f}") in out.splitlines(), out
    assert (marked / MARKS).read_bytes() == before
    assert staged_names(marked) == ["src/app.ts"]


def test_a_second_twins_kept_mark_is_named_by_its_handle(marked, capsys):
    """Two knotty functions in one file: the first marked 72 (pardoned at ccn
    8), the second, `#2`, marked 7.3333 and staged at ccn 9."""
    add_knotty(marked)
    committed(marked, ("src/app.ts", KNOTTY, "72.0000"), ("src/app.ts", f"{KNOTTY}#2", "7.3333"))
    grow(marked, twin=2)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 0, out + err
    assert KEPT.format(path="src/app.ts", name=f"{KNOTTY}#2", mark="7.3333") in out.splitlines(), out


def test_two_kept_marks_share_one_line(marked, capsys):
    """Both knotty twins marked 7.3333 and staged at ccn 9: one plural line
    names both handles, and the marks file keeps its bytes, out of the commit."""
    add_knotty(marked)
    before = committed(marked, ("src/app.ts", KNOTTY, "7.3333"), ("src/app.ts", f"{KNOTTY}#2", "7.3333"))
    grow(marked)
    grow(marked, twin=2)

    code, out, err = run(["hook-precommit"], marked, capsys)

    assert code == 0, out + err
    kept = ("crapkit: 2 ratchet marks kept as they were (a mark only falls): "
            f"src/app.ts {KNOTTY} 7.3333; src/app.ts {KNOTTY}#2 7.3333")
    assert kept in out.splitlines(), out
    assert (marked / MARKS).read_bytes() == before
    assert staged_names(marked) == ["src/app.ts"]


# --- a store the grant cannot write -------------------------------------------------

STORE = ".crapkit/crap.sqlite"


def read_only(path) -> None:
    # On Windows os.chmod sets the file's read-only attribute, on POSIX its mode.
    path.chmod(stat.S_IREAD)


def cut_short(path) -> None:
    path.write_bytes(path.read_bytes()[:3000])


def stored(repo):
    """A store holding one run, as any earlier command leaves one."""
    path = repo / STORE
    path.parent.mkdir(exist_ok=True)
    store = SnapshotStore(path)
    store.write_run(commit="0" * 40, tool_versions={}, rows=[], kind="hook")
    store.close()
    return path


def mixed_grant(repo) -> bytes:
    """A marked rise beside a new breach, staged: a grant would add a mark and
    stage the marks file. Returns the marks file's bytes."""
    before = committed(repo, ("src/app.ts", KNOTTY, "7.3333"))
    grow(repo)
    add_scope_and_knotty(repo, "src/fresh.ts")
    git(repo, "add", "crapkit.toml", "src/fresh.ts")
    return before


def assert_no_part_granted(repo, before: bytes) -> None:
    """No alert line, the marks file unwritten and unstaged."""
    assert alerts(repo) == []
    assert (repo / MARKS).read_bytes() == before
    assert MARKS not in staged_names(repo)


ROOT_WRITES = pytest.mark.skipif(os.name != "nt" and os.geteuid() == 0,
                                 reason="root on POSIX writes a file whose mode refuses writes")
STORE_FIX = "the override records its audit there, so nothing was granted; {fix}, then commit again"


@pytest.mark.parametrize(("spoil", "cause", "fix"), [
    pytest.param(read_only, "is read only (attempt to write a readonly database)",
                 "make the store and its folder writable", id="read-only", marks=ROOT_WRITES),
    pytest.param(cut_short, "is damaged (database disk image is malformed)",
                 "restore it from a backup, or move it aside so the next run starts a new one",
                 id="cut-short")])
def test_a_store_the_grant_cannot_write_stops_it_at_exit_5_before_any_side_effect(
        marked, capsys, spoil, cause, fix):
    """A store read only, or cut short, cannot take the audit: one line names
    the store, the cause and the fix at exit 5, crapkit's tool fault, and the
    grant takes none of its three parts. No alert line, the store's bytes as
    they were, the marks file unwritten and unstaged."""
    before = mixed_grant(marked)
    store = stored(marked)
    spoil(store)
    held = store.read_bytes()
    try:
        code, out, err = run(["hook-precommit"], marked, capsys)
    finally:
        store.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert code == 5, out + err
    assert err.splitlines()[-1] == f"crapkit: the store {STORE} {cause}: {STORE_FIX.format(fix=fix)}", err
    assert store.read_bytes() == held
    assert_no_part_granted(marked, before)


def test_a_store_error_it_cannot_name_passes_through_as_sqlite_raised_it(marked, capsys, monkeypatch):
    """A store another connection holds locked past the busy timeout is
    neither read only nor damaged, so the grant does not call it either:
    SQLite's own error comes through, and the grant takes none of its parts."""
    before = mixed_grant(marked)
    holder = sqlite3.connect(stored(marked), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    # No busy wait, where the default waits 5 s: the held lock refuses at once.
    monkeypatch.setattr(sqlite3, "connect", functools.partial(sqlite3.connect, timeout=0))
    try:
        with pytest.raises(sqlite3.OperationalError, match="^database is locked$"):
            run(["hook-precommit"], marked, capsys)
    finally:
        holder.rollback()
        holder.close()

    assert_no_part_granted(marked, before)
