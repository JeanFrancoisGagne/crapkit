"""The crapkit module a test reads its output from, loaded at run time.

A test here computes its expected value from an outside source (json.loads,
hashlib, a model written from the docs) and asks crapkit for the other value.
Loading crapkit through importlib keeps it out of the test's static import
closure (kit/closure.py), which is where an expected value would have to come
from for crapkit to grade itself.
"""
from __future__ import annotations

import importlib


def crapkit(module: str):
    """crapkit.<module>, imported now."""
    return importlib.import_module(f"crapkit.{module}")
