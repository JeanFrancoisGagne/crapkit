"""The argument parser and the process entry point: every subcommand's flags in
one place, the lazy --version action, and main()'s stream reconfiguration and
CrapkitError-to-exit-code mapping."""
from __future__ import annotations

import argparse
import os
import sys

from .. import __version__
from ..errors import ConfigError, CrapkitError
from ..invocation import _self

# The claude-* namespace, named here rather than read off the parser, because the
# guard has to answer before argparse sees the argv at all. A plugin's hooks.json
# ships machine-wide and can name a subcommand an older installed CLI does not
# have; argparse answers that with exit 2 and a usage dump, which on PostToolUse
# lands in the model's context on every edit. Silence is the only safe answer,
# and it is what makes every future plugin-ahead-of-CLI drift harmless.
_CLAUDE_SUBCOMMANDS = frozenset({"claude-hook"})


class _Handler:
    """A subcommand's handler, named at parser-build time and imported when it runs.

    argparse holds this where the function used to sit, and main() still calls
    args.func(args). Naming eight families to build the parser imported all
    eight, so `crapkit runs list` paid for the mutation engine and the verifier,
    and `hook-precommit` paid for them at every git commit.
    """

    __slots__ = ("_family", "_name")

    def __init__(self, family: str, name: str) -> None:
        self._family = family
        self._name = name

    def __call__(self, args):
        from importlib import import_module

        return getattr(import_module(f"{__package__}.{self._family}"), self._name)(args)

    def __repr__(self) -> str:
        return f"<{__package__}.{self._family}.{self._name}>"


def _version_line() -> str:
    """`crapkit <version>` — the program AND the number, so a pasted bug report
    says what produced it.

    The installed distribution answers, because pyproject.toml is where the
    number is published. `__version__` is the fallback for a source tree with
    nothing installed, which is where the git merge driver runs.

    importlib.metadata reads that number out of the installed METADATA file,
    dragging in email, csv, typing and importlib.resources to do it. Reading the
    same header straight costs a directory listing, so when it produces the
    number the package already carries, both sources say the same thing and
    neither import is worth making. A disagreement, or a distribution this scan
    cannot see, goes to importlib.metadata and takes its answer.
    """
    if _published_version() == __version__:
        return f"crapkit {__version__}"
    return f"crapkit {_metadata_version()}"


def _metadata_version() -> str:
    """The authority, for what reading a header cannot settle."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("crapkit")
    except PackageNotFoundError:
        return __version__


def _published_version() -> str | None:
    """The Version: header of the first crapkit dist-info on sys.path — the same
    file, found the same way, that importlib.metadata would answer from."""
    for entry in sys.path:
        metadata = _dist_info_metadata(entry)
        if metadata:
            return _version_field(metadata)
    return None


def _dist_info_metadata(entry: str) -> str | None:
    """<entry>/crapkit-*.dist-info/METADATA, when this path entry holds one."""
    try:
        names = os.listdir(entry or ".")
    except OSError:
        return None  # a zip, a stale path entry, an unreadable directory
    for name in names:
        if _is_crapkit_dist_info(name):
            return os.path.join(entry, name, "METADATA")
    return None


def _is_crapkit_dist_info(name: str) -> bool:
    low = name.lower()
    return low.startswith("crapkit-") and low.endswith(".dist-info")


def _version_field(path: str) -> str | None:
    """METADATA's Version: header. Headers stop at the first blank line; the
    long description below it is free to contain anything."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return _version_header(handle)
    except OSError:
        return None


def _version_header(lines) -> str | None:
    for line in lines:
        if not line.strip():
            return None
        if line.startswith("Version:"):
            return line.partition(":")[2].strip()
    return None


class _VersionAction(argparse.Action):
    """`--version`, resolved when it is asked for.

    argparse's own version action wants the finished string at parser-build
    time, and reading the installed distribution costs ~30ms. build_parser()
    runs on every invocation, `crapkit hook-precommit` included, and the hook is
    measured in what a developer waits for at every `git commit`.
    """

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(_version_line())
        parser.exit()


def cmd_help(args) -> int:
    """`crapkit help [TOPIC]`, the habit git, npm and docker all answer to.

    Without it `help` fell into the same invalid-choice branch as a typo: exit 2
    and a brace dump of 25 subcommand names, which never says that `--help` is
    the way to any one of them. It rebuilds the tree rather than capturing it,
    so nothing holds a second parser alive for the invocations that never ask.
    """
    parser = build_parser()
    _help_topic(parser, args.topic).print_help()
    return 0


def _help_topic(parser: argparse.ArgumentParser, topic: str | None):
    """The parser `help` prints from: one subcommand's, or the whole CLI's."""
    if topic is None:
        return parser
    topics = _help_topics(parser)
    if topic not in topics:
        raise ConfigError(f"no subcommand {topic!r}; `{_self()} help` lists them")
    return topics[topic]


def _help_topics(parser: argparse.ArgumentParser) -> dict:
    """Every subcommand the parser defines, by name."""
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(groups[0].choices)


# Every subcommand that reads a repo takes --repo, and none defaults it to the
# working directory: without the flag the root is found by walking up from
# there (ADR 0002), which is `cli._shared._command_root`'s business.
# A path argument is rebased from the working directory only when the root came
# from the walk (ADR 0002); under --repo it is root-relative as before.
_WHERE = " (repo-relative; without --repo, read from the working directory)"
_REPO_FLAG = {"default": None,
              "help": "crapkit root (default: the nearest crapkit.toml at or above cwd)"}


def build_parser() -> argparse.ArgumentParser:
    """The whole CLI surface, assembled without parsing anything. README's
    Subcommands table is checked against this parser's own subcommand set."""
    parser = argparse.ArgumentParser(prog="crapkit")
    parser.add_argument("--version", action=_VersionAction, default=argparse.SUPPRESS,
                        help="print the program name and its version")
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory", help="build the per-function complexity inventory snapshot")
    inv.add_argument("--repo", **_REPO_FLAG)
    inv.add_argument("--db", default=None, help="snapshot database path (default: <repo>/.crapkit/crap.sqlite)")
    inv.add_argument("--export", default=None, help="also write a canonical TSV export, relative to the repo")
    inv.add_argument("--json", action="store_true", help="print the run summary as JSON")
    inv.set_defaults(func=_Handler("scoring", "cmd_inventory"))

    cov = sub.add_parser("coverage", help="run coverage lanes, join onto a fresh inventory, write a scored run")
    cov.add_argument("--repo", **_REPO_FLAG)
    cov.add_argument("--lane", default=None,
                     help="run only this lane (default: all); the run is partial and never a "
                          "baseline, so rerun the rest with --reuse-unchanged")
    cov.add_argument("--reuse-artifacts", action="store_true", help="skip lane commands, parse existing artifacts")
    cov.add_argument("--reuse-unchanged", action="store_true",
                     help="rerun only lanes whose scope files changed since their artifact; reuse the rest")
    cov.add_argument("--export", default=None, help="write scored TSV export, relative to the repo")
    cov.add_argument("--sarif", default=None, metavar="PATH",
                     help="write over-target findings as SARIF 2.1.0, relative to the repo")
    cov.add_argument("--github", action="store_true",
                     help="print findings as GitHub workflow-command annotations")
    cov.add_argument("--json", action="store_true", help="print the run summary as JSON")
    cov.set_defaults(func=_Handler("scoring", "cmd_coverage"))

    nxt = sub.add_parser("next-item", help="the actionable queue as JSON, ranked by crap "
                                           "descending: what a refactor session takes next")
    nxt.add_argument("--repo", **_REPO_FLAG)
    nxt.add_argument("--top", type=int, default=1, help="return the next N items instead of one")
    nxt.add_argument("--exclude", action="append", default=[],
                     help="skip items whose path or function name contains this (repeatable)")
    nxt.add_argument("--scope", action="append", default=[], metavar="NAME",
                     help="restrict to this configured scope (repeatable); exact, not substring")
    nxt.add_argument("--claim", action="store_true",
                     help="hold the items handed out so another session skips them; "
                          "verify releases a claim once the function is at its ceiling, "
                          "and `crapkit claims release PATH NAME` hands one back by hand")
    nxt.set_defaults(func=_Handler("queue", "cmd_next_item"))

    clm = sub.add_parser("claims", help="the claims sessions hold, and the release that hands one back")
    clm.add_argument("action", nargs="?", default="list", choices=("list", "release"),
                     help="list (default): every open claim; release: close one (PATH NAME) "
                          "or, with --all, every one")
    clm.add_argument("target", nargs="*", metavar="ARG",
                     help="release: PATH NAME, taking either the bare identifier or the "
                          "long_name next-item printed")
    clm.add_argument("--all", action="store_true", help="release: close every open claim")
    clm.add_argument("--repo", **_REPO_FLAG)
    clm.add_argument("--json", action="store_true", help="machine output")
    clm.set_defaults(func=_Handler("queue", "cmd_claims"))

    runs_p = sub.add_parser("runs", help="run history, and the retention command that trims it")
    runs_p.add_argument("action", nargs="?", default="list", choices=("list", "prune"),
                        help="list (default): id, kind, verdict, commit, lane set, and "
                             "`baseline` on the run verify compares against (verdict=- is "
                             "a run that renders no verdict); prune: delete runs outside "
                             "the keep-set, then VACUUM")
    runs_p.add_argument("--keep", type=int, default=5,
                        help="prune: newest trusted runs to keep (default 5). A floor, not "
                             "a cap — the digest pair, passing verify baselines, runs an "
                             "override names and the newest non-hook run are kept too")
    runs_p.add_argument("--repo", **_REPO_FLAG)
    runs_p.add_argument("--json", action="store_true", help="machine output")
    runs_p.set_defaults(func=_Handler("reports", "cmd_runs"))

    ovr = sub.add_parser("overrides", help="the override audit trail")
    ovr.add_argument("--repo", **_REPO_FLAG)
    ovr.add_argument("--json", action="store_true", help="machine output")
    ovr.set_defaults(func=_Handler("reports", "cmd_overrides"))

    expl = sub.add_parser("explain", help="a function's trajectory across runs, plus its ratchet mark")
    expl.add_argument("path", help="source file" + _WHERE)
    expl.add_argument("name", help="function name or fragment")
    expl.add_argument("--repo", **_REPO_FLAG)
    expl.add_argument("--history", action="store_true",
                      help="also list the commits that touched this function (git log -L)")
    expl.add_argument("--tests", action="store_true",
                      help="also list the tests covering this function (coverage.py contexts)")
    expl.add_argument("--json", action="store_true", help="machine output")
    expl.set_defaults(func=_Handler("reports", "cmd_explain"))

    brf = sub.add_parser("brief", help="one function's whole start-editing packet: score, "
                                       "source, the rest of its file, gate rule, lane, mark, "
                                       "dark lines, twins, churn, coupling, commands")
    brf.add_argument("path", nargs="?", help="source file" + _WHERE)
    brf.add_argument("name", nargs="?",
                     help="function name: the bare identifier, the whole long_name "
                          "next-item printed, or the line it starts on")
    brf.add_argument("--repo", **_REPO_FLAG)
    brf.add_argument("--batch", type=int, default=None, metavar="N",
                     help="skip PATH NAME and emit a packet for each of the top N "
                          "queue items, assembled in one process; always JSON")
    brf.add_argument("--json", action="store_true", help="machine output (default: a short summary)")
    brf.set_defaults(func=_Handler("queue", "cmd_brief"))

    rsc = sub.add_parser("rescore", help="fresh complexity for named files overlaid on the latest run's coverage")
    rsc.add_argument("files", nargs="+", help="source files to re-analyze" + _WHERE)
    rsc.add_argument("--repo", **_REPO_FLAG)
    rsc.add_argument("--json", action="store_true", help="machine output (default: table)")
    rsc.add_argument("--gate", action="store_true",
                     help="exit 6 when a function this tree changed since HEAD is over its "
                          "scope ceiling: the pre-commit hook's ccn-only policy on the hook's "
                          "own selection, minus functions at or under their ratchet mark")
    rsc.set_defaults(func=_Handler("scoring", "cmd_rescore"))

    dig = sub.add_parser("digest", help="delta between the last two scored runs; silent when unchanged")
    dig.add_argument("--repo", **_REPO_FLAG)
    dig.add_argument("--alert", action="store_true", help="pipe a non-quiet digest through alert_command")
    dig.set_defaults(func=_Handler("reports", "cmd_digest"))

    trd = sub.add_parser("trend", help="totals per scored run: over-target count, CRAP load, average")
    trd.add_argument("--repo", **_REPO_FLAG)
    trd.add_argument("--json", action="store_true", help="print as JSON")
    trd.set_defaults(func=_Handler("reports", "cmd_trend"))

    rep = sub.add_parser("report", help="one self-contained HTML page: the ranked worklist, "
                                        "the per-scope grades, the trend, and a staleness banner")
    rep.add_argument("--repo", **_REPO_FLAG)
    rep.add_argument("--out", default=".crapkit/report.html", metavar="PATH",
                     help="where to write the page: repo-relative, or an absolute path "
                          "you name (default: .crapkit/report.html); the path is "
                          "printed on stdout")
    rep.set_defaults(func=_Handler("reports", "cmd_report"))

    tsc = sub.add_parser("test-scoped", help="run the configured isolated test command for the files' scope")
    tsc.add_argument("files", nargs="+", help="test files" + _WHERE)
    tsc.add_argument("--repo", **_REPO_FLAG)
    tsc.set_defaults(func=_Handler("verifying", "cmd_test_scoped"))

    ver = sub.add_parser("verify", help="full verdict vs the baseline snapshot: gate, ratchet, new failures")
    ver.add_argument("--repo", **_REPO_FLAG)
    # one baseline, named one way: two of these would leave the losing flag
    # silently ignored, and which one lost would be argument order
    picked = ver.add_mutually_exclusive_group()
    picked.add_argument("--baseline", type=int, default=None, help="baseline run id (default: latest scored run)")
    picked.add_argument("--base", default=None, metavar="REF",
                        help="measure the diff from merge-base(REF, HEAD); the baseline run must "
                             "then sit at or behind that fork point")
    picked.add_argument("--baseline-tsv", default=None, metavar="PATH",
                        help="read the baseline from a TSV written by --emit-baseline, not the store")
    ver.add_argument("--emit-baseline", default=None, metavar="PATH",
                     help="also write the baseline run as a portable TSV, relative to the repo")
    ver.add_argument("--reuse-artifacts", action="store_true", help="skip lane commands, parse existing artifacts")
    ver.add_argument("--reuse-unchanged", action="store_true",
                     help="rerun only lanes whose scope files changed since their artifact; reuse the rest")
    ver.add_argument("--override", default=None, metavar="REASON",
                     help="audited exemption for gate violations: alert + ratchet debt + snapshot record")
    ver.add_argument("--no-tighten", action="store_true",
                     help="pass the verdict without rewriting the ratchet; marks stay where they are")
    ver.add_argument("--sarif", default=None, metavar="PATH",
                     help="write gate/ratchet findings as SARIF 2.1.0, relative to the repo")
    ver.add_argument("--github", action="store_true",
                     help="print findings as GitHub workflow-command annotations")
    ver.add_argument("--json", action="store_true", help="print the verdict as JSON")
    ver.set_defaults(func=_Handler("verifying", "cmd_verify"))

    hook = sub.add_parser("hook-precommit", help="gate staged functions at min-CCN <= target; exit 6 on violation")
    hook.add_argument("--repo", **_REPO_FLAG)
    hook.set_defaults(func=_Handler("verifying", "cmd_hook_precommit"))

    # No --repo: the root comes from the edited file's own path, walked upward to
    # the first crapkit.toml and never past a .git entry. A session root passed in
    # would resolve a worktree edit to the mainline checkout's store.
    chk = sub.add_parser("claude-hook", help="advisory ccn check for one Claude Code "
                                             "PostToolUse edit read from stdin; silent "
                                             "unless a changed function is over its ceiling")
    chk.add_argument("--protocol", default="1", metavar="N",
                     help="hook payload protocol (default 1); anything else exits 0 silent")
    chk.set_defaults(func=_Handler("claude_hook", "cmd_claude_hook"))

    wl = sub.add_parser("worklist", help="ranked risk map: every admitted function, "
                                         "finished and no-lane rows included, so it never empties")
    wl.add_argument("--repo", **_REPO_FLAG)
    wl.add_argument("--top", type=int, default=None, help="cap the active list (default: config worklist_top)")
    wl.add_argument("--scope", action="append", default=[], metavar="NAME",
                    help="restrict to this configured scope (repeatable); exact, not substring")
    wl.add_argument("--batches", type=int, default=None, metavar="N",
                    help="split the active list into at most N batches with no shared "
                         "files, co-changing files kept together: one per agent session")
    wl.add_argument("--json", action="store_true", help="print as JSON")
    wl.set_defaults(func=_Handler("queue", "cmd_worklist"))

    ini = sub.add_parser("init", help="sniff the repo and write a starter crapkit.toml")
    ini.add_argument("--repo", **_REPO_FLAG)
    ini.set_defaults(func=_Handler("admin", "cmd_init"))

    doc = sub.add_parser("doctor", help="check that crapkit.toml still describes this repo")
    doc.add_argument("--repo", **_REPO_FLAG)
    doc.add_argument("--show-files", action="store_true", help="list every file each scope matched")
    doc.add_argument("--json", action="store_true",
                     help="one machine-readable report instead of lines: versions, store, "
                          "newest run, per-lane artifact stamps, problems, warnings")
    doc.add_argument("--tune", action="store_true",
                     help="print suggested [crapkit] parallelism knobs for this machine from "
                          "cpu count and recorded lane durations; writes nothing")
    doc.add_argument("--plugin-root", nargs="?", const="", default=None, metavar="PATH",
                     help="check an installed Claude Code plugin against this CLI instead of "
                          "reading a repo: manifest version and hook protocol, one line per "
                          "disagreement, silent when they agree. PATH is the plugin root or "
                          "any directory above it, ~/.claude included (the newest crapkit "
                          "install under it wins); with no PATH, the newest crapkit install "
                          "in Claude Code's plugin cache")
    doc.set_defaults(func=_Handler("admin", "cmd_doctor"))

    rat = sub.add_parser("ratchet", help="manage the committed marks file: seed new debt, prune gone code")
    rat.add_argument("action", choices=("seed", "prune", "merge", "move", "report"),
                     help="seed: mark over-target functions from the latest run; "
                          "prune: drop marks whose functions left the codebase "
                          "(a mark whose file git renamed follows it instead); "
                          "merge: 3-way git merge driver (BASE OURS THEIRS); "
                          "move: re-path marks at their recorded values (OLD NEW); "
                          "report: burn-down from the marks file's git history")
    # default=[] and not just nargs="*": argparse calls a ZERO_OR_MORE positional
    # with no default REQUIRED, so bare `crapkit ratchet` used to answer "the
    # following arguments are required: action, FILE" while report, seed and prune
    # all run with no file at all. cmd_ratchet keeps its own per-action arity check.
    rat.add_argument("files", nargs="*", metavar="FILE", default=[],
                     help="for merge: the three files git passes as %%O %%A %%B; "
                          "for move: OLD NEW, where a trailing '/' on OLD moves a directory")
    rat.add_argument("--json", action="store_true", help="machine output (report)")
    rat.add_argument("--enforce", action="store_true",
                     help="report: exit 1 on debt policy violations (age, repayment quota)")
    rat.add_argument("--repo", **_REPO_FLAG)
    rat.set_defaults(func=_Handler("ratchet_cmds", "cmd_ratchet"))

    wat = sub.add_parser("watch", help="rescore files as they change (polls tracked files from start)")
    wat.add_argument("--repo", **_REPO_FLAG)
    wat.add_argument("--interval", type=float, default=2.0, help="poll seconds (default 2)")
    wat.add_argument("--cycles", type=int, default=None,
                     help="stop after N polls (default: poll until ctrl-c)")
    wat.set_defaults(func=_Handler("admin", "cmd_watch"))

    srv = sub.add_parser("mcp", help="stdio MCP server exposing the read-side tools (JSON-RPC, no deps)")
    srv.add_argument("--repo", **_REPO_FLAG)
    srv.set_defaults(func=_Handler("analyses", "cmd_mcp"))

    mut = sub.add_parser("mutate", help="diff-scoped mutation testing: flip operators on changed lines, run the suite per mutant")
    mut.add_argument("--repo", **_REPO_FLAG)
    mut.add_argument("--files", nargs="*", default=None,
                     help="mutate these whole files instead of the working-tree diff vs HEAD "
                          "(files outside the scored corpus are named and skipped)")
    mut.add_argument("--max-mutants", type=int, default=100, help="hard cap per run (default 100)")
    mut.add_argument("--drop-pool", action="store_true",
                     help="remove the worktrees mutation_workers > 1 keeps in "
                          ".crapkit/mutate-pool/ and exit")
    mut.add_argument("--json", action="store_true", help="machine output")
    mut.set_defaults(func=_Handler("analyses", "cmd_mutate"))

    dup = sub.add_parser("duplication", help="near-duplicate functions by normalized line shingles")
    dup.add_argument("--repo", **_REPO_FLAG)
    dup.add_argument("--min-lines", type=int, default=8, help="smallest function considered (default 8)")
    dup.add_argument("--similarity", type=float, default=0.8,
                     help="containment threshold, shared/smaller (default 0.8)")
    dup.add_argument("--top", type=int, default=50, help="cap the pair list (default 50)")
    dup.add_argument("--json", action="store_true", help="machine output")
    dup.set_defaults(func=_Handler("analyses", "cmd_duplication"))

    cpl = sub.add_parser("coupling", help="files that co-change in the churn window: hidden dependencies")
    cpl.add_argument("--repo", **_REPO_FLAG)
    cpl.add_argument("--min-support", type=int, default=5, help="minimum shared commits (default 5)")
    cpl.add_argument("--min-confidence", type=float, default=0.5,
                     help="minimum max-direction co-change ratio (default 0.5)")
    cpl.add_argument("--top", type=int, default=50, help="cap the pair list (default 50)")
    cpl.add_argument("--json", action="store_true", help="machine output")
    cpl.set_defaults(func=_Handler("analyses", "cmd_coupling"))

    hlp = sub.add_parser("help", help="print one subcommand's help, or the command list")
    hlp.add_argument("topic", nargs="?", default=None, metavar="TOPIC",
                     help="the subcommand to explain (default: the whole CLI)")
    hlp.set_defaults(func=_Handler("parser", "cmd_help"))
    return parser


def _reconfigure_streams() -> None:
    """Piped streams (git hooks, CI, agents, MCP clients) carry UTF-8 everywhere
    modern; Windows hands pipes the legacy codepage instead, which renders as
    mojibake on the way out and, on the way in, turned `pkg/café.py` in a Claude
    Code payload into a path that does not exist, so the advisory hook and the
    MCP server answered a non-ASCII path with silence. A tty keeps its native
    encoding. Either way errors degrade to '?' — an exotic console must never
    turn an exit code into a traceback. A stream swapped for a StringIO (the
    in-process serve-loop tests) has no reconfigure and is left alone."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        if stream.isatty():
            stream.reconfigure(errors="replace")
        else:
            stream.reconfigure(encoding="utf-8", errors="replace")


def _unknown_claude_command(argv: list[str] | None) -> bool:
    """A `claude-*` first argument this build does not define.

    Answered before `parse_args` so argparse never gets to print its usage dump.
    Only this namespace is covered: a human's typo on any other subcommand is
    still an argparse error, because a human is there to read it.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or not args[0].startswith("claude-"):
        return False
    return args[0] not in _CLAUDE_SUBCOMMANDS


def _looks_like_a_path(arg: str) -> bool:
    """Shapes no subcommand name can take. Existence is deliberately not asked:
    `Path(arg).exists()` would let a directory named `inventory` in the cwd
    hijack a real subcommand."""
    return arg in (".", "..") or arg.startswith("~") or "/" in arg or os.sep in arg


def _path_first_arg(argv: list[str] | None) -> str | None:
    """A first argument shaped like a repository path, or None.

    Answered before `parse_args`, like the claude-* guard above it. `crapkit
    ./mini` reads as "score this repo", and argparse answers it with the
    invalid-choice dump of every subcommand name without ever printing the
    word repo, which is where the path goes: a flag on a subcommand.
    """
    args = sys.argv[1:] if argv is None else argv
    if args and _looks_like_a_path(args[0]):
        return args[0]
    return None


def _refuse_path_argument(arg: str) -> int:
    """Same exit code argparse already gave this argv, with the route the dump
    left out. Nothing that used to work changes: every argv reaching here was a
    usage error before."""
    print(f"crapkit: {arg!r} is not a subcommand; the repo is a flag on one, "
          f"e.g. `{_self()} inventory --repo {arg}` "
          f"(`{_self()} --help` lists the subcommands)", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    if _unknown_claude_command(argv):
        return 0
    named_path = _path_first_arg(argv)
    if named_path is not None:
        return _refuse_path_argument(named_path)
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CrapkitError as exc:
        print(f"crapkit: {exc}", file=sys.stderr)
        if getattr(args, "json", False):
            _print_error_object(exc)
        return exc.exit_code


def _print_error_object(exc: CrapkitError) -> None:
    """The one object `--json` promised, when the command died before printing
    its own: a wrapper reads the sentence naming the fix off stdout instead of
    "wrote no run summary". The stderr line and the exit code are unchanged."""
    from ._shared import _print_json

    _print_json({"error": {"exit": exc.exit_code, "kind": exc.kind, "message": str(exc)}})
