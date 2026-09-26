"""The Action's comment builder reads each file the Action's steps wrote through
a repotext kind, so a byte that is not UTF-8 reads as U+FFFD and the comment is
still built. A strict read of git's error ended the step with a
UnicodeDecodeError, and no comment was posted."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _builder():
    spec = importlib.util.spec_from_file_location("crapkit_action_comment",
                                                  ROOT / "tools" / "action" / "comment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ERRORS = [
    # id, the bytes git wrote on stderr, the text the note quotes
    ("latin1-byte", b"fatal: bad path caf\xe9.py\n", "fatal: bad path caf�.py"),
    ("utf8-bom", b"\xef\xbb\xbffatal: bad revision\n", "fatal: bad revision"),
    ("utf8", "fatal: bad path caf\xe9.py\n".encode(), "fatal: bad path caf\xe9.py"),
]


@pytest.mark.parametrize("raw, quoted", [row[1:] for row in ERRORS], ids=[row[0] for row in ERRORS])
def test_the_builder_quotes_git_s_error_whatever_its_bytes(tmp_path, raw, quoted):
    error = tmp_path / "changed.error"
    error.write_bytes(raw)
    empty = tmp_path / "changed.txt"
    empty.write_bytes(b"")

    code = _builder().main(["--changed-z", str(empty), "--changed-error", str(error),
                            "--out", str(tmp_path / "c.md")])

    assert code == 0
    assert f"The base diff failed (`{quoted}`)" in (tmp_path / "c.md").read_text(encoding="utf-8")


def test_a_saved_changed_list_with_a_byte_that_is_not_utf8_still_counts(tmp_path):
    """The README's saved-payload route passes --changed, one name per line."""
    changed = tmp_path / "changed.txt"
    changed.write_bytes(b"src/caf\xe9.py\nsrc/a.py\n")

    code = _builder().main(["--changed", str(changed), "--out", str(tmp_path / "c.md")])

    assert code == 0
    assert "2 changed files" in (tmp_path / "c.md").read_text(encoding="utf-8")


CUTS = [
    # id, the character repeated past GitHub's limit (1, 2, 3 and 4 UTF-8 bytes)
    ("ascii", "a"), ("latin", "\xe9"), ("cjk", "上"), ("emoji", "\U0001f680"),
]


@pytest.mark.parametrize("char", [row[1] for row in CUTS], ids=[row[0] for row in CUTS])
def test_a_cut_request_body_keeps_whole_characters_under_the_limit(char):
    module = _builder()
    line = char * 97 + "\n"
    text = module.MARKER + "\n" + line * (module._BODY_LIMIT // len(line.encode()) + 5)

    sent = module.request_text(text)

    assert len(sent.encode("utf-8")) <= module._BODY_LIMIT
    assert "�" not in sent
    assert sent.startswith(module.MARKER + "\n")
    assert sent.endswith("\n\n" + module._CUT_NOTE)
    kept = sent[len(module.MARKER) + 1:-len(module._CUT_NOTE) - 2].split("\n")
    assert set(kept) == {line[:-1]}
    json.dumps({"body": sent})
