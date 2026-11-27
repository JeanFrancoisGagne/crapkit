"""GitHub workflow properties and messages use different encoding rules."""
import pytest

from crapkit.sarif import github_annotation
from crapkit.verify import Verdict, sarif_results


@pytest.mark.parametrize("path, encoded", [
    ("src/a,b.py", "src/a%2Cb.py"),
    ("src/a:b.py", "src/a%3Ab.py"),
    ("src/a%2Cb.py", "src/a%252Cb.py"),
    ("src/a\r\n::warning::b.py", "src/a%0D%0A%3A%3Awarning%3A%3Ab.py"),
])
def test_filename_stays_one_workflow_property(path, encoded):
    result = sarif_results(Verdict.passing(), [(path, 12)])[0]
    annotation = github_annotation(result)

    assert annotation == (f"::warning file={encoded},line=12,title=crapkit/diff-uncovered"
                          "::changed line has no coverage: no lane ran it")
    assert len(annotation.splitlines()) == 1


def test_message_keeps_commas_and_colons_while_escaping_control_characters():
    result = sarif_results(Verdict.passing(), [("src/a.py", 12)])[0]
    result["message"]["text"] = "a,b: 20%\r\n::warning::still one message"

    assert github_annotation(result).endswith(
        "::a,b: 20%25%0D%0A::warning::still one message")
