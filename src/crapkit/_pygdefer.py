"""Keep pygments out of a crapkit process that will never parse Erlang.

lizard imports every reader it ships, and `lizard_languages/erlang.py` binds
pygments at module scope — so `import lizard` drags in pygments, and behind it
importlib.metadata, email, zipfile and socket. Measured on this box: 42ms with
pygments, 16ms without, paid by every process that touches the analysis stack,
the pre-commit hook included.

crapkit analyzes eight languages (typescript, tsx, javascript, python, swift, go,
rust, shell). Erlang is not one of them and no scope can name it. The readers that need
pygments are still SHIPPED, not removed: `deferred_pygments()` puts proxies in
sys.modules for the duration of the lizard import, so the readers bind stand-ins
and the real package loads the first time anything reads or calls one. An .erl
file analyzed through lizard directly gets the same answer; it just pays the
import at that moment.

Nothing is installed once pygments is already imported: a consumer that wanted
it keeps the module it loaded.
"""
from __future__ import annotations

import importlib
import sys
import types
from contextlib import contextmanager

_NAMES = ("pygments", "pygments.token", "pygments.lexers")


def _evict() -> None:
    """Drop the proxies so importlib loads the real modules underneath them."""
    for name in _NAMES:
        if isinstance(sys.modules.get(name), (_Proxy, _LazyCallable)):
            del sys.modules[name]


class _LazyCallable:
    """A pygments function bound by `from pygments import lex` before it exists."""

    def __init__(self, module: str, attr: str) -> None:
        self._module, self._attr = module, attr

    def __call__(self, *args, **kwargs):
        _evict()
        return getattr(importlib.import_module(self._module), self._attr)(*args, **kwargs)


class _Proxy(types.ModuleType):
    """Stands in for a pygments module until something actually reads it."""

    def __getattr__(self, attr: str):
        if attr.startswith("__"):
            raise AttributeError(attr)
        _evict()
        return getattr(importlib.import_module(self.__name__), attr)


def _install() -> bool:
    """Proxy the three names lizard's Erlang reader binds. False when pygments
    is already loaded, which leaves the real module exactly where it is."""
    if "pygments" in sys.modules:
        return False
    stubs = {name: _Proxy(name) for name in _NAMES}
    stubs["pygments"].token = stubs["pygments.token"]
    stubs["pygments"].lexers = stubs["pygments.lexers"]
    stubs["pygments"].lex = _LazyCallable("pygments", "lex")
    sys.modules.update(stubs)
    return True


@contextmanager
def deferred_pygments():
    """Import lizard inside this: its readers bind the proxies, and the proxies
    come straight back out of sys.modules afterwards.

    Taking them out is the part that matters for anything but speed. A module
    left in sys.modules with no __path__ breaks the next `import
    pygments.formatters` a process makes; only the readers that already bound a
    proxy keep one, and each resolves itself on first use.
    """
    installed = _install()
    try:
        yield
    finally:
        if installed:
            _evict()
