"""crapkit's production functions and types, loaded by name when a test runs.

A test reads crapkit's answer through this module and its expected value from
model_score, kit.exact or an oracle under oracles/. Nothing here is imported
statically, so the import closure of a test that uses it holds no crapkit
module (kit/closure.py follows import statements, not import_module calls):
the expected side can never borrow the code it checks.

Rows are built by field name and only with the fields the running crapkit's
type declares, so a retro replay against an older crapkit, whose rows carry
fewer fields, still builds them.
"""
from __future__ import annotations

from functools import lru_cache
import importlib
import inspect


@lru_cache(maxsize=None)
def load(name: str):
    """`module:attribute` under the crapkit package, e.g. `score:crap`."""
    module, _, attribute = name.partition(":")
    found = importlib.import_module(f"crapkit.{module}")
    for part in attribute.split("."):
        found = getattr(found, part)
    return found


def _takes(function, keyword: str) -> bool:
    parameters = inspect.signature(function).parameters.values()
    return any(p.name == keyword or p.kind is p.VAR_KEYWORD for p in parameters)


def call(name: str, *args, **keywords):
    """Call `module:attribute` with the keywords its crapkit version takes.

    A retro replay reaches a crapkit whose function predates a keyword: the
    call then runs without it, as that version's CLI called it, and the check
    reads what that version computes."""
    function = load(name)
    return function(*args, **{key: value for key, value in keywords.items()
                              if _takes(function, key)})


def make(type_name: str, **fields):
    """A NamedTuple built from the fields its crapkit version knows."""
    row_type = load(type_name)
    return row_type(**{key: value for key, value in fields.items() if key in row_type._fields})


def inventory_row(scope: str, path: str, name: str, start: int, end: int, ccn: int, **extra):
    """An InventoryRow with ccn_std = ccn_mod = ccn."""
    fields = {"nloc": 3, "params": 1, "nesting": 1, "cognitive": 0, "occurrence": 1,
              "inline_body": 0, **extra}
    return make("snapshot:InventoryRow", scope=scope, path=path, long_name=name, start=start,
                end=end, ccn_std=ccn, ccn_mod=ccn, ccn=ccn, **fields)


def scored_row(scope: str, path: str, name: str, start: int, end: int, ccn: int, cov: float,
               flag: str, crap: float, remedy: str, **extra):
    fields = {"nloc": 3, "params": 1, "nesting": 1, "cognitive": 0, "occurrence": 1,
              "inline_body": 0, **extra}
    return make("score:ScoredRow", scope=scope, path=path, long_name=name, start=start, end=end,
                ccn_std=ccn, ccn_mod=ccn, ccn=ccn, cov=cov, flag=flag, crap=crap, remedy=remedy,
                **fields)


def fn_coverage(name: str, start: int, end: int, *, invoked: bool, branches: tuple = (0, 0),
                statements: tuple = (0, 0)):
    """FnCoverage from (covered, total) pairs."""
    return make("coverage_istanbul:FnCoverage", name=name, start=start, end=end,
                invoked=invoked, branches_total=branches[1], branches_covered=branches[0],
                statements_total=statements[1], statements_covered=statements[0])


def file_churn(commits: int, authors: int, weight: float):
    return make("churn:FileChurn", commits=commits, authors=authors, weight=weight)
