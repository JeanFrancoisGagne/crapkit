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
"""
from __future__ import annotations

from lizard_languages.typescript import TypeScriptStates

from .errors import ToolError

_ANGLE_DELTAS = {"<": 1, ">": -1}


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
