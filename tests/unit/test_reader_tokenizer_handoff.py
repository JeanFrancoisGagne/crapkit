"""What crapkit's readers hand the tokenizer they build on, and how they register.

Each reader extends lizard's tokenizer with its own patterns ahead of the
caller's additions and passes the caller's token class through; lizard's
extension chain asks for tokens that way. register() runs at import, so these
tests reach it again on a stock lizard.
"""
import lizard_languages
from lizard_languages.code_reader import CodeReader
from lizard_languages.go import GoReader
from lizard_languages.script_language import ScriptLanguageMixIn
from lizard_languages.zig import ZigReader
import pytest

from crapkit import lizardgolike, lizardlinecomment, lizardpowershell, lizardrust, lizardshell


def _recording(handed: list):
    def tokens(source_code, addition="", token_class=None):
        handed.append((source_code, addition, token_class))
        return iter(("t",))
    return staticmethod(tokens)


@pytest.mark.parametrize("reader, owner, stock, added", [
    (lizardshell.ShellReader, CodeReader, "generate_tokens", lizardshell._TOKEN_ADDITION),
    (lizardpowershell.PowerShellReader, ScriptLanguageMixIn, "generate_common_tokens",
     lizardpowershell._TOKEN_ADDITION),
    (lizardgolike.CorrectedGoReader, GoReader, "generate_tokens", lizardlinecomment.LINE_COMMENT),
    (lizardgolike.CorrectedZigReader, ZigReader, "generate_tokens",
     lizardgolike._ZIG_QUOTED_NAME + lizardlinecomment.LINE_COMMENT
     + lizardgolike._ZIG_STRING_LINE),
    (lizardrust.CorrectedRustReader, CodeReader, "generate_tokens",
     lizardlinecomment.LINE_COMMENT + lizardrust._HASH_TOKENS + lizardrust._LIFETIME),
])
def test_a_reader_hands_its_patterns_ahead_of_the_caller_s_and_the_token_class_on(
        monkeypatch, reader, owner, stock, added):
    handed = []
    monkeypatch.setattr(owner, stock, _recording(handed))

    assert list(reader.generate_tokens("x = 1\n", "|y", int)) == ["t"]
    assert handed == [("x = 1\n", added + "|y", int)]


def test_registering_powershell_on_a_stock_lizard_appends_the_reader(monkeypatch):
    monkeypatch.setattr(lizard_languages, "languages", lizardpowershell._stock_languages)

    assert lizardpowershell.register() is lizardpowershell.PowerShellReader
    assert lizard_languages.languages() == lizardpowershell._stock_languages() + [
        lizardpowershell.PowerShellReader]
