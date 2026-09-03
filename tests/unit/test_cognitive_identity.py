"""Per-function cognitive state must key on the function object, not its address.

Measured on the consumer repo: keying the state map on `id(fn)` let a freed FunctionInfo's
address be recycled under a later function, which then inherited a stranger's
running total. 377-379 rows moved between two runs of the same commit, and
appending one pass-through extension to the chain shifted the cognitive column on
20 of 708 files. The state map holds the FunctionInfo itself now, so no address
can be reused while its state is live.

The fake reader here is lizard's contract, no more: a `context.current_function`
that the tokenizer swings between functions, and a class name that decides which
language rules apply.
"""
import weakref

from crapkit.lizardcognitive import LizardExtension


class _FakeFn:
    """A FunctionInfo as the extension sees it. __slots__ pins the instance size,
    so a freed one and a fresh one land in the same allocator size class."""
    __slots__ = ("name", "cognitive_complexity", "cognitive_nesting", "__weakref__")

    def __init__(self, name: str) -> None:
        self.name = name
        self.cognitive_complexity = 0
        self.cognitive_nesting = 0


class _FakeContext:
    def __init__(self) -> None:
        self.current_function = None


class TypeScriptReader:
    """Name does not start with 'python', so the TS/JS rules run."""

    def __init__(self) -> None:
        self.context = _FakeContext()


def _recycling_tokens(reader, seen: dict):
    """Drive one function to a nonzero total, free it, then build a second one.

    The spacer token is load-bearing: it makes the extension rebind its own local
    `fn`, so the first function's last reference outside the state map is the one
    this generator drops.
    """
    first = _FakeFn("first")
    reader.context.current_function = first
    yield from _count_an_if()
    seen["first_total"] = first.cognitive_complexity

    reader.context.current_function = _FakeFn("spacer")
    yield "1"

    seen["freed_address"] = id(first)
    del first
    second = _FakeFn("second")
    seen["second"] = second
    reader.context.current_function = second
    yield "return"


def _count_an_if():
    yield from ("if", "(", "a", ")", "{")


def _run(reader, tokens) -> None:
    for _ in LizardExtension()(tokens, reader):
        pass


def test_a_recycled_function_address_never_inherits_the_other_functions_score():
    reader = TypeScriptReader()
    seen: dict = {}

    _run(reader, _recycling_tokens(reader, seen))

    assert seen["first_total"] == 1, "the if must have counted, or the test proves nothing"
    assert seen["second"].cognitive_complexity == 0, (
        "a function that reused a freed address inherited the freed function's total")


def _liveness_tokens(reader, seen: dict):
    first = _FakeFn("first")
    seen["ref"] = weakref.ref(first)
    reader.context.current_function = first
    yield from _count_an_if()

    reader.context.current_function = _FakeFn("spacer")
    yield "1"

    del first
    seen["alive_while_state_is_held"] = seen["ref"]() is not None
    yield "return"


def test_the_state_map_holds_the_function_it_scores():
    """The address cannot be recycled if the object is still referenced. Checked
    mid-stream: the state map dies with the generator frame at the end."""
    reader = TypeScriptReader()
    seen: dict = {}

    _run(reader, _liveness_tokens(reader, seen))

    assert seen["alive_while_state_is_held"] is True


def test_the_map_still_separates_two_functions_that_are_both_alive():
    reader = TypeScriptReader()
    a, b = _FakeFn("a"), _FakeFn("b")

    def tokens():
        reader.context.current_function = a
        yield from _count_an_if()
        reader.context.current_function = b
        yield "return"
        reader.context.current_function = a
        yield "if"

    _run(reader, tokens())

    assert (a.cognitive_complexity, b.cognitive_complexity) == (3, 0), (
        "the second if deepened by one nesting level; b must stay at zero")
