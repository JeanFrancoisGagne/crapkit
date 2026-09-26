"""init reads a repo's package.json and .gitignore, and never leaves a half-done init.

package.json is JSON, and init reads it by the JSON rule: UTF-8, a byte-order
mark read past as npm reads past it. A root package.json in UTF-16 (the
PowerShell 5.1 Out-File default) or holding a byte that is not UTF-8 ends init
with exit 3 before it writes any file, naming the file, the bytes and the fix.
A nested one, a test fixture say, is skipped with one warning line naming it.
init used to die on either with a traceback after crapkit.toml was written, and
a BOM cost the js lane in silence.

.gitignore is appended as bytes, as git reads it: every byte already there
stays, and the entries take the file's own line ending, so `git diff` shows
only added lines. A UTF-16 .gitignore, which git cannot read, is named and left
as it was.

init writes .gitignore before crapkit.toml, and a second init over a
crapkit.toml an earlier run left behind finishes the .gitignore step instead of
refusing, so a stopped first run can no longer lock .crapkit/ out of .gitignore.
"""
import codecs
import shutil
import subprocess
from pathlib import Path

import pytest

from raw_git import checkout, commit, git, repository

from crapkit.cli.parser import main

JS = b"export function f(x) {\n  if (x) { return 1; }\n  return 2;\n}\n"
PKG = ('{\n  "name": "demo",\n  "author": "AUTHOR",\n  "scripts": {"test": "vitest run"},\n'
       '  "devDependencies": {"vitest": "^2.0.0"}\n}\n')


def _package(author: str, encoding: str = "utf-8", prefix: bytes = b"", crlf: bool = False) -> bytes:
    body = PKG.replace("AUTHOR", author)
    return prefix + (body.replace("\n", "\r\n") if crlf else body).encode(encoding)


def _repo(tmp_path, files: dict[bytes, bytes], config: dict[str, str] | None = None) -> Path:
    """A clone of one commit holding `files` byte for byte, checked out under
    `config` (core.autocrlf=false unless it says otherwise)."""
    root = repository(tmp_path / "repo")
    commit(root, files={b"src/app.js": JS, **files})
    for key, value in (config or {}).items():
        git(root, "config", key, value)
    checkout(root)
    return root


def _init(root: Path, capsys):
    code = main(["init", "--repo", str(root)])
    return code, capsys.readouterr()


def _lanes(root: Path) -> list[str]:
    return [line for line in (root / "crapkit.toml").read_text(encoding="utf-8").splitlines()
            if line == "[[lane]]"]


# --- package.json ------------------------------------------------------------

READABLE = [
    # id, package.json bytes: each one npm reads and init takes a js lane from
    ("package-json-ascii", _package("Rene")),
    ("package-json-valid-accent", _package("René")),
    ("package-json-cjk-emoji", _package("渡辺 \U0001f680")),
    ("package-json-crlf", _package("Rene", crlf=True)),
    ("package-json-utf8-bom", _package("Rene", prefix=codecs.BOM_UTF8)),
]


@pytest.mark.parametrize("body", [row[1] for row in READABLE], ids=[row[0] for row in READABLE])
def test_init_reads_a_package_json_the_way_npm_does(tmp_path, capsys, body):
    """A UTF-8 BOM used to cost the js lane in silence: json.loads refused the
    BOM and the reader took the refusal for an empty file."""
    root = _repo(tmp_path, {b"package.json": body})

    code, out = _init(root, capsys)

    assert code == 0, out.err
    assert _lanes(root) == ["[[lane]]"]
    assert out.err == ""


NPM = shutil.which("npm")
NPM_ROWS = [row for row in READABLE if row[0] in ("package-json-ascii", "package-json-utf8-bom")]


@pytest.mark.skipif(NPM is None, reason="npm is not on PATH (every GitHub-hosted runner has it)")
@pytest.mark.parametrize("body", [row[1] for row in NPM_ROWS], ids=[row[0] for row in NPM_ROWS])
def test_npm_reads_the_package_json_init_took_a_lane_from(tmp_path, body):
    """npm is the oracle for what a package.json holds: it reads past a BOM."""
    (tmp_path / "package.json").write_bytes(body)

    done = subprocess.run([NPM, "pkg", "get", "name"], cwd=tmp_path, capture_output=True,
                          timeout=120)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == b'"demo"'


REFUSED = [
    # id, root package.json bytes, the bytes the refusal names
    ("package-json-cp1252-byte", _package("René", "cp1252"), "byte e9 at offset 36"),
    ("package-json-cp1252-only-byte", _package("Ren’", "cp1252"), "byte 92 at offset 36"),
    ("package-json-utf16", _package("Rene", "utf-16"),
     "first bytes ff fe = UTF-16, the PowerShell 5.1 Out-File default"),
    ("package-json-utf16-be", codecs.BOM_UTF16_BE + _package("Rene", "utf-16-be"),
     "first bytes fe ff = UTF-16, the PowerShell 5.1 Out-File default"),
]


@pytest.mark.parametrize("body, named", [row[1:] for row in REFUSED], ids=[row[0] for row in REFUSED])
def test_init_refuses_a_root_package_json_that_is_not_utf8_before_writing_any_file(
        tmp_path, capsys, body, named):
    """npm reads a stray cp1252 byte as U+FFFD, but a lane built from a file
    init could not read is a guess, and the traceback it used to end in came
    after crapkit.toml was written. The refusal names the file, the bytes and
    the fix, and nothing is written."""
    root = _repo(tmp_path, {b"package.json": body, b".gitignore": b"build/\n"})

    code, out = _init(root, capsys)

    assert code == 3
    assert out.err == (f"crapkit: init wrote no file: package.json is not UTF-8 ({named}); "
                       "save it as UTF-8\n")
    assert not (root / "crapkit.toml").exists()
    assert (root / ".gitignore").read_bytes() == b"build/\n"


NESTED = [
    # id, bytes of tests/fixtures/package.json, the bytes the warning names
    ("nested-latin1", _package("René", "latin-1"), "byte e9 at offset 36"),
    ("nested-utf16", _package("Rene", "utf-16"),
     "first bytes ff fe = UTF-16, the PowerShell 5.1 Out-File default"),
]


@pytest.mark.parametrize("body, named", [row[1:] for row in NESTED], ids=[row[0] for row in NESTED])
def test_init_skips_a_nested_package_json_it_cannot_read_with_one_warning(
        tmp_path, capsys, body, named):
    """A fixture's package.json describes nobody's test runner, and a
    Latin-1 one ended init for the whole repo."""
    root = _repo(tmp_path, {b"package.json": _package("Rene"),
                            b"tests/fixtures/package.json": body})

    code, out = _init(root, capsys)

    assert code == 0, out.err
    assert _lanes(root) == ["[[lane]]"]
    assert out.err == (f"crapkit: init skipped tests/fixtures/package.json: it is not UTF-8 "
                       f"({named}); save it as UTF-8\n")


def test_a_workspace_package_json_with_a_bom_still_routes_the_lane(tmp_path, capsys):
    """The BOM rule holds below the root: the one workspace naming a runner
    gets the lane."""
    root_package = b'{"name": "mono", "private": true, "scripts": {"test": "npm run test -ws"}}\n'
    workspace = codecs.BOM_UTF8 + _package("Rene")
    root = _repo(tmp_path, {b"package.json": root_package, b"web/package.json": workspace,
                            b"web/src/app.js": JS})

    code, out = _init(root, capsys)

    toml = (root / "crapkit.toml").read_text(encoding="utf-8")
    assert code == 0, out.err
    assert 'cwd = "web"' in toml


# --- .gitignore --------------------------------------------------------------

IGNORES = [
    # id, .gitignore bytes as committed, git config for the checkout, .gitattributes
    ("gitignore-ascii-lf", b"build/\n*.log\n", {}, None),
    ("gitignore-crlf-autocrlf-false", b"build/\r\n*.log\r\n", {}, None),
    ("gitignore-crlf-as-text-unset", b"build/\r\n*.log\r\n", {}, b".gitignore -text\n"),
    ("gitignore-lf-checked-out-crlf-autocrlf-true", b"build/\n*.log\n",
     {"core.autocrlf": "true"}, None),
    ("gitignore-cp1252-byte", b"# fichiers g\xe9n\xe9r\xe9s\nbuild/\n", {}, None),
    ("gitignore-cp1252-crlf", b"# fichiers g\xe9n\xe9r\xe9s\r\nbuild/\r\n", {}, None),
    ("gitignore-valid-accent", "# fichiers générés\nbuild/\n".encode(), {}, None),
    ("gitignore-utf8-bom", codecs.BOM_UTF8 + b"build/\n", {}, None),
    ("gitignore-no-final-newline", b"build/", {}, None),
    ("gitignore-crlf-no-final-newline", b"build/\r\n*.log", {}, None),
]


def _numstat(root: Path, path: str) -> tuple[int, int]:
    counts = git(root, "diff", "--numstat", "--", path).split()
    return (int(counts[0]), int(counts[1])) if counts else (0, 0)


@pytest.mark.parametrize("before, config, attributes", [row[1:] for row in IGNORES],
                         ids=[row[0] for row in IGNORES])
def test_init_appends_to_a_gitignore_and_keeps_every_byte_it_held(
        tmp_path, capsys, before, config, attributes):
    """A CRLF .gitignore came back with every line rewritten to LF, so the first
    commit after init diffed the whole file, and a cp1252 comment ended init."""
    files = {b".gitignore": before, **({b".gitattributes": attributes} if attributes else {})}
    root = _repo(tmp_path, files, config)
    on_disk = (root / ".gitignore").read_bytes()
    newline = b"\r\n" if b"\r\n" in on_disk else b"\n"

    code, out = _init(root, capsys)

    after = (root / ".gitignore").read_bytes()
    tail = after[len(on_disk):]
    assert code == 0, out.err
    assert after.startswith(on_disk)
    assert tail.replace(newline, b"\n").endswith(b"# crapkit\n.crapkit/\n")
    assert tail.count(b"\n") == tail.count(newline)
    # An unterminated last line gains its line ending, which git counts as that
    # line changed although every byte of it stays; every other row adds only.
    assert _numstat(root, ".gitignore")[1] == (not on_disk.endswith(b"\n"))


@pytest.mark.parametrize("before", ["build/\n".encode("utf-16"),
                                    codecs.BOM_UTF16_BE + "build/\n".encode("utf-16-be")],
                         ids=["gitignore-utf16-le", "gitignore-utf16-be"])
def test_init_names_a_utf16_gitignore_and_leaves_it_as_it_was(tmp_path, capsys, before):
    """git cannot read a UTF-16 .gitignore at all; appending to it would ignore
    nothing, and init used to die on it after writing crapkit.toml."""
    root = _repo(tmp_path, {b".gitignore": before})

    code, out = _init(root, capsys)

    assert code == 0, out.err
    assert (root / ".gitignore").read_bytes() == before
    assert _numstat(root, ".gitignore") == (0, 0)
    assert (root / "crapkit.toml").is_file()
    assert (f"crapkit: left .gitignore as it was: it is UTF-16 (first bytes {before[:2].hex(' ')}, "
            "the PowerShell 5.1 Out-File default), which git cannot read; save it as UTF-8 "
            "and add .crapkit/") in out.err


# --- init order and a second init ---------------------------------------------

def test_init_writes_gitignore_before_crapkit_toml(tmp_path, capsys, monkeypatch):
    """A run stopped between the two writes leaves .gitignore extended and no
    crapkit.toml, so the next init starts over instead of refusing."""
    root = _repo(tmp_path, {b".gitignore": b"build/\n"})
    real_write_text = Path.write_text

    def stopped(self, *args, **kwargs):
        if self.name == "crapkit.toml":
            raise KeyboardInterrupt
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", stopped)
    with pytest.raises(KeyboardInterrupt):
        main(["init", "--repo", str(root)])
    monkeypatch.undo()
    capsys.readouterr()
    extended = (root / ".gitignore").read_bytes()

    code, out = _init(root, capsys)

    assert extended == b"build/\n\n# crapkit\n.crapkit/\n"
    assert code == 0, out.err
    assert (root / "crapkit.toml").is_file()
    assert (root / ".gitignore").read_bytes() == extended


@pytest.mark.parametrize("before", [b"# caf\xe9\nbuild/\n", b"build/\r\n", b""],
                         ids=["cp1252-gitignore", "crlf-gitignore", "no-gitignore"])
def test_a_second_init_finishes_the_gitignore_a_stopped_first_run_left_undone(
        tmp_path, capsys, before):
    """0.8.0 wrote crapkit.toml first and died on a cp1252 .gitignore; the next
    init refused with `crapkit.toml already exists`, so .crapkit/ was never
    ignored. Now the second init adds what is missing, says so and leaves
    crapkit.toml byte for byte."""
    files = {b".gitignore": before} if before else {}
    root = _repo(tmp_path, files)
    assert _init(root, capsys)[0] == 0
    toml = (root / "crapkit.toml").read_bytes()
    if before:
        (root / ".gitignore").write_bytes(before)
    else:
        (root / ".gitignore").unlink()

    code, out = _init(root, capsys)

    assert code == 0, out.err
    assert (root / "crapkit.toml").read_bytes() == toml
    assert (root / ".gitignore").read_bytes().startswith(before)
    assert out.out == ("crapkit.toml was already there and init left it as it was; "
                       "it finished the step an earlier run left undone\n"
                       "added to .gitignore: .crapkit/\n")


def test_a_second_init_with_nothing_left_to_do_still_refuses(tmp_path, capsys):
    root = _repo(tmp_path, {})
    assert _init(root, capsys)[0] == 0
    toml = (root / "crapkit.toml").read_bytes()

    code, out = _init(root, capsys)

    assert code == 3
    assert "crapkit.toml already exists" in out.err and "edit it instead" in out.err
    assert (root / "crapkit.toml").read_bytes() == toml


@pytest.mark.parametrize("before", ["build/\n".encode("utf-16"),
                                    codecs.BOM_UTF16_BE + "build/\n".encode("utf-16-be")],
                         ids=["gitignore-utf16-le", "gitignore-utf16-be"])
def test_a_second_init_over_a_utf16_gitignore_names_the_gitignore_alone(tmp_path, capsys, before):
    """The step left undone is .gitignore. The refusal named it and then said
    `crapkit.toml already exists ... edit it instead`, sending the reader to a
    file that needs nothing."""
    root = _repo(tmp_path, {})
    assert _init(root, capsys)[0] == 0
    toml = (root / "crapkit.toml").read_bytes()
    (root / ".gitignore").write_bytes(before)

    code, out = _init(root, capsys)

    assert code == 3
    assert out.err == (f"crapkit: left .gitignore as it was: it is UTF-16 (first bytes "
                       f"{before[:2].hex(' ')}, the PowerShell 5.1 Out-File default), which git "
                       "cannot read; save it as UTF-8 and add .crapkit/\n")
    assert (root / "crapkit.toml").read_bytes() == toml
    assert (root / ".gitignore").read_bytes() == before


def test_a_second_init_adds_the_entries_of_the_lanes_the_config_declares(tmp_path, capsys):
    """The lanes come from crapkit.toml as it stands, so a lane added by hand
    gets its artifact directory ignored too."""
    root = _repo(tmp_path, {})
    assert _init(root, capsys)[0] == 0
    config = root / "crapkit.toml"
    config.write_text(config.read_text(encoding="utf-8") + (
        '\n[[lane]]\nname = "js"\ncommand = "npx vitest run --coverage"\n'
        'artifact = "coverage/coverage-final.json"\nparser = "istanbul"\nscopes = ["src"]\n'),
        encoding="utf-8")
    (root / ".gitignore").unlink()

    code, out = _init(root, capsys)

    assert code == 0, out.err
    assert (root / ".gitignore").read_bytes() == b"# crapkit\n.crapkit/\ncoverage/\n"
    assert out.out.endswith("added to .gitignore: .crapkit/, coverage/\n")


@pytest.mark.parametrize("before", [codecs.BOM_UTF8 + b"# crapkit\n.crapkit/\n",
                                    codecs.BOM_UTF8 + b".crapkit/\r\nbuild/\r\n"],
                         ids=["bom-lf", "bom-crlf-entry-first"])
def test_init_adds_nothing_to_a_bom_gitignore_that_already_ignores_the_store(
        tmp_path, capsys, before):
    """git reads a .gitignore past its BOM, so a `.crapkit/` first line behind
    one already ignores the store. init read the mark as part of that line and
    appended a second `.crapkit/`."""
    root = _repo(tmp_path, {b".gitignore": before})

    code, out = _init(root, capsys)

    assert code == 0, out.err
    assert (root / ".gitignore").read_bytes() == before
    assert "added to .gitignore" not in out.out
    assert git(root, "check-ignore", ".crapkit/x").strip() == b".crapkit/x"
