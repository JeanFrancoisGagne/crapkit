"""init reads a repo's package.json and .gitignore the way npm and git read them.

Both files belong to the repo, and other tools read them as bytes: npm drops a
UTF-8 BOM and reads a stray byte as U+FFFD, and git matches .gitignore lines
byte for byte. init read both as strict UTF-8: a cp1252 byte ended init after
crapkit.toml was written, so a second init refused and .crapkit/ never got
ignored; a BOM package.json cost the js lane in silence; and a CRLF .gitignore
came back with every line the user wrote rewritten to LF. init now keeps the
.gitignore's bytes and line ending, reads package.json as npm does, and names
a UTF-16 file (the PowerShell 5.1 Out-File default) that neither tool reads.
"""
import codecs

import pytest

from raw_git import checkout, commit, repository

from crapkit.cli.parser import main

JS = b"export function f(x) {\n  if (x) { return 1; }\n  return 2;\n}\n"
PKG = ('{\n  "name": "demo",\n  "author": "AUTHOR",\n  "scripts": {"test": "vitest run"},\n'
       '  "devDependencies": {"vitest": "^2.0.0"}\n}\n')


def _package(author: str, encoding: str = "utf-8", prefix: bytes = b"", crlf: bool = False) -> bytes:
    body = PKG.replace("AUTHOR", author)
    return prefix + (body.replace("\n", "\r\n") if crlf else body).encode(encoding)


def _init(tmp_path, files: dict[bytes, bytes], capsys):
    root = repository(tmp_path)
    commit(root, files={b"src/app.js": JS, **files})
    checkout(root)
    code = main(["init", "--repo", str(root)])
    return root, code, capsys.readouterr()


PACKAGES = [
    # id, package.json bytes, whether init writes the js lane
    ("package-json-ascii", _package("Rene"), True),
    ("package-json-valid-accent", _package("René"), True),
    ("package-json-cjk-emoji", _package("渡辺 \U0001f680"), True),
    ("package-json-crlf", _package("Rene", crlf=True), True),
    ("package-json-cp1252-byte", _package("René", "cp1252"), True),
    ("package-json-utf8-bom", _package("Rene", prefix=codecs.BOM_UTF8), True),
    ("package-json-utf16", _package("Rene", "utf-16"), False),
]


@pytest.mark.parametrize("body, lane", [row[1:] for row in PACKAGES], ids=[row[0] for row in PACKAGES])
def test_init_reads_a_package_json_the_way_npm_does(tmp_path, capsys, body, lane):
    root, code, out = _init(tmp_path, {b"package.json": body}, capsys)

    toml = (root / "crapkit.toml").read_text(encoding="utf-8")
    assert code == 0, out.err
    assert ("[[lane]]" in toml.splitlines()) is lane
    assert ("crapkit: init read no test runner from package.json: it is UTF-16 (first bytes ff fe)"
            in out.err) is not lane


IGNORES = [
    # id, .gitignore bytes before init
    ("gitignore-ascii-lf", b"build/\n*.log\n"),
    ("gitignore-crlf", b"build/\r\n*.log\r\n"),
    ("gitignore-cp1252-byte", b"# fichiers g\xe9n\xe9r\xe9s\nbuild/\n"),
    ("gitignore-valid-accent", "# fichiers générés\nbuild/\n".encode()),
    ("gitignore-utf8-bom", codecs.BOM_UTF8 + b"build/\n"),
    ("gitignore-no-final-newline", b"build/"),
]


@pytest.mark.parametrize("before", [row[1] for row in IGNORES], ids=[row[0] for row in IGNORES])
def test_init_appends_to_a_gitignore_and_keeps_every_byte_it_held(tmp_path, capsys, before):
    root, code, out = _init(tmp_path, {b".gitignore": before}, capsys)

    after = (root / ".gitignore").read_bytes()
    newline = b"\r\n" if b"\r\n" in before else b"\n"
    assert code == 0, out.err
    assert after.startswith(before)
    assert after[len(before):].replace(newline, b"\n").endswith(b"# crapkit\n.crapkit/\n")
    assert b"\r\n" not in after[len(before):] or newline == b"\r\n"


def test_init_names_a_utf16_gitignore_and_leaves_it_as_it_was(tmp_path, capsys):
    """git cannot read a UTF-16 .gitignore at all; appending to it would ignore
    nothing, and init used to die on it after writing crapkit.toml."""
    before = "build/\n".encode("utf-16")

    root, code, out = _init(tmp_path, {b".gitignore": before}, capsys)

    assert code == 0, out.err
    assert (root / ".gitignore").read_bytes() == before
    assert (root / "crapkit.toml").is_file()
    assert ("crapkit: left .gitignore as it was: it is UTF-16 (first bytes ff fe, the PowerShell "
            "5.1 Out-File default), which git cannot read; save it as UTF-8 and add .crapkit/") in out.err
