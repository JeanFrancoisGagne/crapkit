"""github.com/JeanFrancoisGagne/crapkit, answered by a local bare mirror.

The README's marketplace lines, `pip install git+https://...`, a pre-commit
`repo:` and an Action's `uses:` all name GitHub. The mirror is cloned from
the exported bundle (every branch and tag), and `url.<mirror>.insteadOf` rules
in the sandbox's $HOME/.gitconfig send each spelling of the upstream URL to
it, so those lines run verbatim with no network. A release is modelled by
moving the mirror's main and tags:

    mirror = gitmirror.make(box)                 # rules written to box HOME
    mirror.publish(candidate.staged, candidate.version)   # main -> candidate, tag v<version>
    mirror.release_to("0.7.6")                   # main back at the v0.7.6 tag
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SLUG = "JeanFrancoisGagne/crapkit"
UPSTREAM = (f"https://github.com/{SLUG}.git", f"https://github.com/{SLUG}",
            f"git@github.com:{SLUG}.git", f"git@github.com:{SLUG}",
            f"ssh://git@github.com/{SLUG}.git", f"ssh://git@github.com/{SLUG}")


def rules(url: str) -> str:
    """gitconfig lines sending every upstream spelling to `url`. git applies
    the longest matching prefix, so the .git spellings win over the bare ones."""
    lines = "".join(f"\tinsteadOf = {upstream}\n" for upstream in UPSTREAM)
    return f'[url "{url}"]\n{lines}'


@dataclass
class Mirror:
    path: Path
    box: object

    @property
    def url(self) -> str:
        return self.path.as_uri()

    def git(self, *args: str, env: dict | None = None, cwd=None) -> str:
        step = self.box.run(["git", f"--git-dir={self.path}", *args], env=env, cwd=cwd, expect=0)
        return step.stdout.strip()

    def tags(self) -> list[str]:
        return self.git("tag", "--list").split()

    def head(self, ref: str = "main") -> str:
        return self.git("rev-parse", ref)

    def release_to(self, version: str) -> str:
        """Point main at the v<version> tag: the mirror as it stood at that release."""
        commit = self.git("rev-parse", f"v{version}^{{commit}}")
        self.git("update-ref", "refs/heads/main", commit)
        return commit

    def publish(self, tree: Path, version: str) -> str:
        """Commit `tree` on top of main as release <version>: main moves to it and
        v<version> tags it, so `@main` and `@v<version>` both install it."""
        index = self.box.tmp / f"mirror-index-{version}"
        env = {"GIT_INDEX_FILE": str(index), "GIT_WORK_TREE": str(tree), **self.box.commit_env()}
        self.git("add", "--all", ".", env=env, cwd=tree)
        written = self.git("write-tree", env=env)
        commit = self.git("commit-tree", written, "-p", "main", "-m", f"Release {version}", env=env)
        self.git("update-ref", "refs/heads/main", commit)
        self.git("tag", "-f", f"v{version}", commit)
        return commit


def at(box) -> Mirror:
    """The mirror make() cloned into this box."""
    return Mirror(box.root / "mirror" / "crapkit.git", box)


def make(box, source: str | os.PathLike | None = None) -> Mirror:
    """Clone the exported mirror into the sandbox and point GitHub at it."""
    source = source or os.environ["CRAPKIT_DEPLOY_MIRROR"]
    mirror = at(box)
    box.run(["git", "clone", "-q", "--mirror", str(source), str(mirror.path)], expect=0)
    _main_is_head(mirror)
    gitconfig = box.home / ".gitconfig"
    gitconfig.write_text(gitconfig.read_text(encoding="utf-8") + rules(mirror.url), encoding="utf-8")
    return mirror


def _main_is_head(mirror: Mirror) -> None:
    """HEAD names main, as on GitHub. An export from a detached checkout (a
    linked worktree, a PR merge commit) carries no local main: it becomes
    origin/main, or the exported HEAD when there is no origin either."""
    heads = mirror.git("for-each-ref", "--format=%(refname)", "refs/heads/main", "refs/remotes/origin/main").split()
    if "refs/heads/main" not in heads:
        base = heads[0] if heads else "HEAD"
        mirror.git("update-ref", "refs/heads/main", mirror.git("rev-parse", base))
    mirror.git("symbolic-ref", "HEAD", "refs/heads/main")
