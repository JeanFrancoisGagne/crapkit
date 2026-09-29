"""kit.docrange: a model's cited doc lines, pinned by sha256."""
import hashlib

from accuracy.kit import docrange


def _repo(tmp_path, text: str):
    (tmp_path / "README.md").write_bytes(text.encode("utf-8"))
    return tmp_path


def test_the_digest_covers_the_cited_lines_joined_by_lf(tmp_path):
    repo = _repo(tmp_path, "one\ntwo\r\nthree\nfour\n")

    assert docrange.sha("README.md", 2, 3, repo) == hashlib.sha256(b"two\nthree").hexdigest()


def test_a_model_citing_current_text_is_fresh_and_an_edit_makes_it_stale(tmp_path):
    repo = _repo(tmp_path, "a\nb\nc\n")
    line = docrange.header("README.md", 1, 2, repo)
    model = tmp_path / "model_x.py"
    model.write_text(f'"""A model.\n\n{line}\n"""\n', encoding="utf-8")

    assert docrange.cited(model) == [("README.md", 1, 2, line.rsplit("=", 1)[1])]
    assert docrange.stale(model, repo) == []
    _repo(tmp_path, "a\nB\nc\n")
    [problem] = docrange.stale(model, repo)
    assert problem.startswith("model_x.py cites README.md:1-2, which changed")
