"""The three registrations, together, in one process.

Wave 2 admitted six languages across two lines of work, and they meet here:
`analyze` imports three reader modules, two of which rebind
`lizard_languages.languages` and one of which rebinds a class name in the same
module. Each module's own tests prove its reader alone; nothing proved the
chain, and a wrapper chain is exactly the shape that works one at a time and
breaks when a second one wraps it. Measured on two stand-in readers: with the
stamp-based guard the shell reader shipped with, each module stamping its own
name, one repeat call each leaves `[A, B, A, B]` on the list — a stamp answers
only for the OUTERMOST wrapper, and the module that registered first cannot see
its own. Both readers now ask the list whether it already carries them, which is
what these tests hold them to.

The chain also has to survive a spawned process, since `analyze_jobs` pools
past 16 files and a Windows child re-imports everything. That is asserted from
a real subprocess rather than from this one, which has already imported
everything by the time it runs.
"""
import subprocess
import sys

import pytest

from crapkit import analyze
from crapkit.lizardpowershell import PowerShellReader
from crapkit.lizardpowershell import register as register_powershell
from crapkit.lizardrust import CorrectedRustReader
from crapkit.lizardrust import register as register_rust
from crapkit.lizardshell import ShellReader
from crapkit.lizardshell import register as register_shell
from crapkit.universe import LANGUAGE_EXTENSIONS

# The suffix each crapkit-owned reader must answer to, and the reader itself.
CRAPKIT_READERS = {".rs": CorrectedRustReader, ".sh": ShellReader, ".bash": ShellReader,
                   ".ps1": PowerShellReader, ".psm1": PowerShellReader}


def _get_reader_for(name: str):
    from lizard_languages import get_reader_for

    return get_reader_for(name)


@pytest.mark.parametrize("suffix,reader", sorted(CRAPKIT_READERS.items()))
def test_every_crapkit_reader_resolves_with_the_other_two_registered(suffix, reader):
    """Importing `crapkit.analyze` is the whole registration story, and it has
    to hold for all three at once: the module scope that runs it is the one a
    pool child imports."""
    assert _get_reader_for(f"probe{suffix}") is reader


def test_registering_all_three_again_in_any_order_appends_nothing():
    """Idempotency has to be a property of the LIST, not of the function each
    module installed. Reverse order because that is the order a consumer
    importing the modules directly can produce, and each guard has to answer for
    a list some other module has since wrapped."""
    from lizard_languages import languages

    before = languages()

    for _ in range(2):
        register_powershell()
        register_shell()
        register_rust()

    assert languages() == before


def test_no_two_admitted_languages_claim_the_same_extension():
    """Scope assignment takes the first scope whose path AND extension match, so
    two labels sharing a suffix would make the winner depend on declaration
    order in someone's crapkit.toml."""
    claims: dict[str, list[str]] = {}
    for language, extensions in LANGUAGE_EXTENSIONS.items():
        for extension in extensions:
            claims.setdefault(extension, []).append(language)

    assert {ext: langs for ext, langs in claims.items() if len(langs) > 1} == {}


@pytest.mark.parametrize("extension", sorted({e for exts in LANGUAGE_EXTENSIONS.values()
                                              for e in exts}))
def test_every_admitted_extension_resolves_to_a_reader(extension):
    """lizard answers an undeclared suffix with CLikeReader rather than with a
    failure, so an extension admitted by mistake would score C-shaped nonsense
    and look fine. This asks lizard, not the fallback."""
    from lizard_languages import get_reader_for

    assert get_reader_for(f"probe{extension}") is not None


@pytest.mark.parametrize("extension", sorted({e for exts in LANGUAGE_EXTENSIONS.values()
                                              for e in exts}))
def test_a_draining_reader_gets_the_chain_that_survives_draining(extension):
    """`SwiftReplaceLabel.preprocess` returns a list where the other seven
    preprocessors yield, so any extension whose reader inherits it must take the
    second chain or every function in the file reads cognitive 0. Swift and
    Kotlin are the only two lizard ships; this is the check that fires the day a
    fifteenth language arrives on a third one."""
    from lizard_languages.swift import SwiftReplaceLabel

    reader = _get_reader_for(f"probe{extension}")
    drains = issubclass(reader, SwiftReplaceLabel)

    preprocessed = analyze._extensions_for(f"probe{extension}") is analyze._PREPROCESSED_EXTENSIONS
    assert drains == preprocessed


def test_a_spawned_child_importing_analyze_resolves_all_three():
    """A pool worker on Windows starts from a bare interpreter. This asserts the
    registrations ride the import rather than this process's history."""
    probe = ("import crapkit.analyze;"
             "from lizard_languages import get_reader_for as g;"
             "print([g('p' + s).__name__ for s in ('.rs', '.sh', '.ps1')])")

    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         check=True).stdout

    assert "['CorrectedRustReader', 'ShellReader', 'PowerShellReader']" in out
