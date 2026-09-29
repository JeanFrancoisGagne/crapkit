"""The environment variables that point git at one particular repo.

git exports GIT_DIR (and, in a hook, GIT_INDEX_FILE and more) to a hook and to
`git bisect run`. A test that runs git in a temp dir with those still set works
in the repo that ran the suite instead: on 2026-09-28 the accuracy kit's
`git init` and `git config` made that repo bare and gave it the kit's A U Thor
identity, and 19 fix commits went out under it. git names the variables itself,
`git rev-parse --local-env-vars`, so the list follows the installed git.
"""
import functools
import os
import subprocess


@functools.cache
def repo_env_names() -> tuple[str, ...]:
    clean = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    done = subprocess.run(["git", "rev-parse", "--local-env-vars"], env=clean,
                          capture_output=True, text=True, check=True)
    return tuple(done.stdout.split())


def without_repo_env(environ) -> dict:
    """`environ` less every variable that points git at a repo."""
    names = repo_env_names()
    return {name: value for name, value in environ.items() if name not in names}
