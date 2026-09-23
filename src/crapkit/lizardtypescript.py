"""Keep sibling expression arrows separate in lizard's TypeScript state machine.

The stock reader shares one function across comma-separated expression arrows.
Brackets need their own state, and a ternary colon must not start a type reader.
This extension replaces only a fresh reader instance's state before its first
token. It does not change installed code, reader registration, or token values.

An unparenthesized TypeScript arrow body containing an unmatched `<` before a
comma is ambiguous here: the comma can separate type arguments or expressions.
Refuse that file instead of guessing. Parentheses or a block around the arrow
body give the reader the missing delimiter. Generic arrow parameters still use
the stock declaration reader and remain supported.

Template literals are fixed in the source text instead, before any reader sees
it. The stock tokenizer reads a template as the regex `.*?` between two
backticks, so the first backtick inside one closes it: the opening backtick of a
template nested in `${...}`, or an escaped one in the text. It then counts every
brace inside a `${...}` to find its end, including one in a string or in a
nested template's text. Either way the reader sits inside a template until the
next backtick in the file, and every function after it is folded into the
enclosing one or dropped. `mask_templates` blanks exactly the characters that
mislead it, so the tokens that come out are the ones a flat template gives.
"""
from __future__ import annotations

import re

from lizard_languages.javascript import JavaScriptReader
from lizard_languages.tsx import TSXReader
from lizard_languages.typescript import TypeScriptReader, TypeScriptStates

from .errors import ToolError

_ANGLE_DELTAS = {"<": 1, ">": -1}

# The readers that tokenize with TypeScriptReader's template regex and nothing
# ahead of it that can swallow a quote or a backtick. VueReader is left out: its
# tag token runs `.*?` up to the next `>`, across quotes and backticks alike.
_TEMPLATE_READERS = (TypeScriptReader, JavaScriptReader, TSXReader)

# The tokens of lizard's pattern that can hold a quote, a backtick or a slash: a
# block or line comment, a template, a string, a digit-separated number. No other
# token it reads can, so a search for these finds the template starts lizard
# finds. Each alternative opens on its own first character, which lets the search
# skip every other one; the lookbehind after a number's first digit is lizard's
# token boundary, since a digit after a word character is part of that word.
_LIZARD_SKIPS = re.compile(
    r"/\*.*?\*/|//(?:\\\n|[^\n])*|(?P<tick>`)|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*?'"
    r"|\d(?<!\w\d)\d*'(?:\d+')*\d+|0(?<!\w0)x(?:[0-9A-Fa-f]+')+[0-9A-Fa-f]+"
    r"|0(?<!\w0)b(?:[01]+')+[01]+",
    re.S)
# The outermost template's text: an escaped backtick or `\${` misleads lizard,
# any other escape does not.
_OUTER_TEXT = re.compile(r"(?P<hide>\\(?:`|\$\{))|\\.|(?P<close>`)|(?P<open>\$\{)", re.S)
# A nested template's text sits inside lizard's brace count, so every brace in it goes.
_NESTED_TEXT = re.compile(r"(?P<hide>\\.|[{}])|(?P<close>`)|(?P<open>\$\{)", re.S)
# The code inside `${...}`.
_CODE = re.compile(
    r"(?P<hide>/\*.*?\*/|//[^\n]*|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')"
    r"|(?P<open>`)|(?P<brace>\{)|(?P<shut>\})|(?P<slash>/)", re.S)
_DEPTH = {"brace": 1, "shut": -1}
_REGEX_LITERAL = re.compile(r"/(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+/")
_BEFORE_REGEX = frozenset("(,=:[!&|?{};")
_MISLEADING = re.compile(r"[`{}]")


def reads_templates(reader) -> bool:
    """True for a reader class whose tokenizer `mask_templates` mirrors."""
    return reader in _TEMPLATE_READERS


def mask_templates(code: str) -> str:
    """CODE with every character that misreads a template literal blanked.

    Blanked means a space in its place, so every line and column stays put. A
    nested template's backticks go, with every brace in its text; so do a
    backtick or brace inside a string, comment or regex literal in `${...}`, an
    escaped backtick, and the `{` of an escaped `\\${`. A template the file
    never closes is left as lizard reads it. A file with nothing to blank comes
    back as the same string.
    """
    if "`" not in code:
        return code
    mask = _TemplateMask(code)
    pos = 0
    while hit := _LIZARD_SKIPS.search(code, pos):
        pos = mask.after(hit)
    return mask.applied()


class _Unterminated(Exception):
    """The file ended inside a template or one of its expressions."""


class _TemplateMask:
    def __init__(self, code: str):
        self.code = code
        self.hidden: list[int] = []

    def after(self, hit) -> int:
        """Where lizard's next token starts once it has read HIT."""
        if hit.lastgroup != "tick":
            return hit.end()
        kept = len(self.hidden)
        try:
            return self.template(hit.end(), _OUTER_TEXT)
        except _Unterminated:
            del self.hidden[kept:]
            return len(self.code)

    def template(self, pos: int, text) -> int:
        """The index just past the backtick that closes the template open at POS."""
        while hit := text.search(self.code, pos):
            pos = hit.end()
            if hit.lastgroup == "close":
                return pos
            if hit.lastgroup == "open":
                pos = self.expression(pos)
            else:
                self._hide(hit.start(), pos)
        raise _Unterminated

    def expression(self, pos: int) -> int:
        """The index just past the `}` that closes the `${` before POS."""
        depth = 1
        while depth:
            hit = _CODE.search(self.code, pos)
            if hit is None:
                raise _Unterminated
            depth += _DEPTH.get(hit.lastgroup, 0)
            pos = self._code_step(hit)
        return pos

    def _code_step(self, hit) -> int:
        kind = hit.lastgroup
        if kind == "hide":
            self._hide(hit.start(), hit.end())
        elif kind == "open":
            return self._nested(hit.end())
        elif kind == "slash":
            return self._regex_or_division(hit.start())
        return hit.end()

    def _nested(self, pos: int) -> int:
        self.hidden.append(pos - 1)
        end = self.template(pos, _NESTED_TEXT)
        self.hidden.append(end - 1)
        return end

    def _regex_or_division(self, start: int) -> int:
        literal = _REGEX_LITERAL.match(self.code, start)
        if literal is None or not _regex_may_start(self.code, start):
            return start + 1
        self._hide(start, literal.end())
        return literal.end()

    def _hide(self, start: int, end: int) -> None:
        self.hidden.extend(m.start() for m in _MISLEADING.finditer(self.code, start, end))

    def applied(self) -> str:
        if not self.hidden:
            return self.code
        chars = list(self.code)
        for i in self.hidden:
            chars[i] = " "
        return "".join(chars)


def _regex_may_start(code: str, slash: int) -> bool:
    """A `/` after an operator or an opening bracket starts a regex literal.

    Only asked inside `${...}`, so the `{` of the `${` stops the walk back.
    """
    i = slash - 1
    while code[i].isspace():
        i -= 1
    return code[i] in _BEFORE_REGEX


def uses_type_syntax(filename: str) -> bool:
    """The reader mode used by parsing and by analysis-cache identity."""
    return filename.lower().endswith((".ts", ".tsx"))


class ExpressionStates(TypeScriptStates):
    def __init__(self, context):
        super().__init__(context)
        self.ternaries = 0
        self.expression_body = False
        self.expression_child = False
        self.array_expression = False
        self.child_array = False
        self.last_line = 0
        self.type_depth = 0
        self.typed = uses_type_syntax(context.fileinfo.filename)

    def __call__(self, token, reader=None):
        answer = super().__call__(token, reader)
        self.last_line = self.context.current_line
        return answer

    def sub_state(self, state, callback=None, token=None):
        if isinstance(state, ExpressionStates):
            state.array_expression = self.child_array
        return super().sub_state(state, callback, token)

    def _arrow_function(self, token):
        if self.expression_body:
            child = self.__class__(self.context)
            child.expression_child = True
            self.next(self._state_global)
            self.sub_state(child, self._pop_function_from_stack)
            child._arrow_function(token)
        else:
            self.expression_body = token != "{"
            super()._arrow_function(token)

    def _pop_function_from_stack(self):
        if self.expression_body and self.started_function:
            fn = self.context.current_function
            fn.end_line = max(fn.start_line, self.last_line)
        super()._pop_function_from_stack()
        self.expression_body = False
        self.type_depth = 0

    def _state_global(self, token):
        self.child_array = token == "[" or (self.array_expression and token != "{")
        newline = self.context.newline
        if self.expression_body and self.array_expression:
            self.context.newline = False
        try:
            self._global_token(token)
        finally:
            self.context.newline = newline

    def _global_token(self, token):
        self._track_type_angles(token)
        self._separate_comma(token)
        if self._end_nested_expression(token):
            return
        if self._ternary_token(token) or self._square_token(token):
            return
        super()._state_global(token)

    def _track_type_angles(self, token):
        delta = _ANGLE_DELTAS.get(token, 0)
        if delta and self.typed and self.expression_body:
            self.type_depth = max(0, self.type_depth + delta)

    def _separate_comma(self, token):
        if token != "," or not self.started_function:
            return
        self._refuse_type_comma()
        if not self.expression_child:
            self._pop_function_from_stack()
            self.function_name = ""

    def _refuse_type_comma(self):
        if self.type_depth:
            fn = self.context.current_function
            raise ToolError(
                f"{fn.filename}:{fn.start_line}: expression-arrow body has '<' before a comma; "
                "lizard cannot distinguish type arguments from an expression separator here; "
                "wrap that arrow body in parentheses or a block")

    def _end_nested_expression(self, token):
        if self.expression_child and token in (",", ";"):
            self.statemachine_return()
            return True
        return False

    def _ternary_token(self, token):
        if token == "?":
            self.ternaries += 1
        elif token == ":" and self.ternaries:
            self.ternaries -= 1
            return True
        return False

    def _square_token(self, token):
        if token == "]":
            self.statemachine_return()
            return True
        if token != "[" or self._computed_property():
            return False
        self.function_name = ""
        self.sub_state(self.__class__(self.context))
        return True

    def _computed_property(self):
        return self.as_object and self._prev_token != "=" and not self._in_prop_value


class LizardExtension:
    def __call__(self, tokens, reader):
        reader.parallel_states = [ExpressionStates(reader.context)
                                  if type(state) is TypeScriptStates else state
                                  for state in reader.parallel_states]
        yield from tokens
