"""A `//` comment ends at its line's end, except in C, C++ and Objective-C.

lizard 1.24.0 tokenizes every language with one shared pattern, and its `//`
comment runs on past a backslash at the line's end into the next line, the way
a C preprocessor splices lines. Only C, C++ and Objective-C splice. In Java,
JavaScript, TypeScript, TSX, Vue, Swift, Rust, Go and Zig the comment ends at
its line, and the line lizard took with it was code. A Windows path in a
comment (`// C:\\dir\\`) was enough: over a signature it cost the function its
row, and over an `if` it hid the `if`, so the function ended at that if's `}`.

LINE_COMMENT is a tokenizer addition. lizard tries additions ahead of its own
patterns, so where both match at one position, this one wins.

register() adds it to lizard's Java, Swift, TypeScript and TSX readers in
place. JavaScript inherits the TypeScript reader's tokenizer and Vue calls it
by name, so those two follow. The readers keep their classes, so every check
that names a reader class still holds. crapkit's own Go, Zig and Rust readers
add LINE_COMMENT in their own tokenizers.

lizardtypescript's template mask finds template literals the way the tokenizer
finds them, so its comment pattern ends at the line too.
"""
from __future__ import annotations

from ._pygdefer import deferred_pygments

with deferred_pygments():  # lizard's Erlang reader would load pygments here
    import lizard
    from lizard_languages.java import JavaReader
    from lizard_languages.swift import SwiftReader
    from lizard_languages.tsx import TSXReader
    from lizard_languages.typescript import TypeScriptReader

LINE_COMMENT = r"|//[^\n]*"

# The readers whose tokenizer register() wraps. JavaScriptReader inherits
# TypeScriptReader's tokenizer, and VueReader calls TypeScriptReader's by name.
_READERS = (JavaReader, SwiftReader, TypeScriptReader, TSXReader)

# Every admitted suffix whose reader must end a comment at its line, probed
# through lizard's own resolution. Any filename picks the reader; the file is
# never opened.
_PROBE_SUFFIXES = ("java", "swift", "ts", "js", "cjs", "mjs", "tsx", "jsx", "vue")
_PROBE_SOURCE = "// a\\\nb\n"


def _ending_line_comments(stock):
    """STOCK, a reader's tokenizer, with LINE_COMMENT ahead of its patterns."""
    def generate_tokens(source_code, addition="", token_class=None):
        return stock(source_code, LINE_COMMENT + addition, token_class)

    generate_tokens.crapkit_stock = stock
    return staticmethod(generate_tokens)


def register() -> None:
    """End a `//` comment at its line in the readers of _READERS. Idempotent.

    Raises RuntimeError when a probe suffix still resolves to a reader whose
    comment takes the next line: that file would score wrong and look fine.
    """
    for reader in _READERS:
        if not hasattr(reader.generate_tokens, "crapkit_stock"):
            reader.generate_tokens = _ending_line_comments(reader.generate_tokens)
    for suffix in _PROBE_SUFFIXES:
        reader = lizard.get_reader_for(f"crapkit_registration_probe.{suffix}")
        if "b" not in list(reader.generate_tokens(_PROBE_SOURCE)):
            raise RuntimeError(_unregistered(suffix, reader))


def _unregistered(suffix: str, reader: type) -> str:
    return (f"crapkit.lizardlinecomment.register() did not take: lizard resolves "
            f"'{suffix}' to {reader.__name__}, whose `//` comment still takes the next "
            f"line. lizard {lizard.version} tokenizes or picks readers some other way; "
            f"rewrite register() against the new mechanism.")
