"""This test process made to look the way `uvx crapkit` starts crapkit.

uvx runs the console script from an environment in uv's cache. sys.prefix lies
under the cache root, which CACHEDIR.TAG marks; uv names itself in UV; and the
environment's bin sits first on the PATH of that one process. Tests of what
crapkit spells for its reader, and of what it finds on PATH, build that here.
"""
import sys
from pathlib import Path

# The tag line the cache directory spec fixes. uv and pipx both write it at the
# root of their caches.
CACHE_TAG = "Signature: 8a477f597d28d172789f06886806bc55\n"


def cached_env(cache: Path) -> Path:
    """An environment the way uv and pipx cache one: a directory under a cache
    root that CACHEDIR.TAG marks."""
    env = cache / "archive-v0" / "Ds2JZStGIUIB0F1a"
    (env / "bin").mkdir(parents=True)
    (cache / "CACHEDIR.TAG").write_text(CACHE_TAG, encoding="utf-8")
    return env


def run_from(env: Path, monkeypatch) -> None:
    """argv and prefix as the console script in `env` leaves them."""
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(sys, "argv", [str(env / "bin" / "crapkit"), "init"])


def as_uvx(tmp_path: Path, monkeypatch) -> Path:
    """This process as `uvx crapkit` starts it. Returns the environment's bin,
    where uvx's launcher sits."""
    env = cached_env(tmp_path / "uv" / "cache")
    run_from(env, monkeypatch)
    monkeypatch.setenv("UV", "/usr/local/bin/uv")
    return env / "bin"
