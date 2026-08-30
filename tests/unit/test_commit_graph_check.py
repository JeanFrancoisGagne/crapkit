"""doctor: a commit-graph written without changed-path Bloom filters.

Every per-file history walk crapkit makes — churn, `brief`, `explain --history`
— asks git which commits touched one path. Without the Bloom filters git opens
every tree on the way; on the flagship consumer's repo (72,470 commits) that
walk is 1,147 ms instead of 194 ms. The chunk table says which shape a repo
has, so the check is a file read, not a git process.
"""
import struct

from crapkit.cli.admin import _doctor_commit_graph

SIGNATURE = b"CGPH"
FIX = "git commit-graph write --reachable --changed-paths"


def graph_bytes(*chunks: bytes) -> bytes:
    """A commit-graph header and chunk table naming `chunks`. The chunk bodies
    are what doctor never reads, so there are none."""
    head = SIGNATURE + bytes([1, 1, len(chunks), 0])
    offset = 8 + (len(chunks) + 1) * 12
    toc = b"".join(struct.pack(">4sQ", cid, offset) for cid in chunks)
    return head + toc + struct.pack(">4sQ", b"\0\0\0\0", offset)


def info_dir(root):
    path = root / ".git" / "objects" / "info"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_graph(root, *chunks: bytes) -> None:
    (info_dir(root) / "commit-graph").write_bytes(graph_bytes(*chunks))


def warnings(root) -> list[str]:
    return [f.text for f in _doctor_commit_graph(root)]


def test_a_commit_graph_without_bloom_filters_is_warned_about(tmp_path):
    write_graph(tmp_path, b"OIDF", b"OIDL", b"CDAT", b"GDA2")

    (text,) = warnings(tmp_path)

    assert FIX in text, text
    assert "Bloom" in text


def test_the_finding_is_a_warning_and_never_a_failure(tmp_path):
    write_graph(tmp_path, b"OIDF", b"CDAT")
    assert [f.level for f in _doctor_commit_graph(tmp_path)] == ["WARN"]


def test_a_commit_graph_with_bloom_filters_says_nothing(tmp_path):
    write_graph(tmp_path, b"OIDF", b"OIDL", b"CDAT", b"BIDX", b"BDAT")
    assert warnings(tmp_path) == []


def test_a_repo_with_no_commit_graph_says_nothing(tmp_path):
    """Silence is deliberate: there is no shape to fix, and git decides when a
    repo is big enough to want one."""
    info_dir(tmp_path)
    assert warnings(tmp_path) == []


def test_a_repo_with_no_git_directory_says_nothing(tmp_path):
    assert warnings(tmp_path) == []


def test_a_chained_commit_graph_is_read_layer_by_layer(tmp_path):
    """Split graphs are the shape `git maintenance` writes. One layer without
    the filters is enough to send the walk down the slow path."""
    graphs = info_dir(tmp_path) / "commit-graphs"
    graphs.mkdir()
    (graphs / "graph-aaa.graph").write_bytes(graph_bytes(b"OIDF", b"BIDX", b"BDAT"))
    (graphs / "graph-bbb.graph").write_bytes(graph_bytes(b"OIDF", b"CDAT"))
    (graphs / "commit-graph-chain").write_text("aaa\nbbb\n", encoding="utf-8")

    assert len(warnings(tmp_path)) == 1


def test_a_fully_filtered_chain_says_nothing(tmp_path):
    graphs = info_dir(tmp_path) / "commit-graphs"
    graphs.mkdir()
    for name in ("aaa", "bbb"):
        (graphs / f"graph-{name}.graph").write_bytes(graph_bytes(b"OIDF", b"BIDX", b"BDAT"))
    (graphs / "commit-graph-chain").write_text("aaa\nbbb\n", encoding="utf-8")

    assert warnings(tmp_path) == []


def test_a_linked_worktree_reads_the_shared_object_store(tmp_path):
    """A worktree's .git is a file naming its own git directory, and the graph
    lives with the objects the main repo owns — which `commondir` names."""
    main = tmp_path / "main" / ".git"
    (main / "objects" / "info").mkdir(parents=True)
    (main / "objects" / "info" / "commit-graph").write_bytes(graph_bytes(b"OIDF", b"CDAT"))
    linked = main / "worktrees" / "wt"
    linked.mkdir(parents=True)
    (linked / "commondir").write_text("../..\n", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {linked}\n", encoding="utf-8")

    assert len(warnings(tree)) == 1


def test_a_root_below_the_repo_top_reads_the_top_object_store(tmp_path):
    """A crapkit root one directory down is the monorepo layout PR #23 added.
    `root/.git` is then neither a file nor a directory, and a reader that looks
    only there finds no graph and warns about nothing — the warning that exists
    to keep per-file history walks fast, never shown to the layout that has the
    biggest histories."""
    write_graph(tmp_path, b"OIDF", b"CDAT")
    nested = tmp_path / "app"
    nested.mkdir()

    assert len(warnings(nested)) == 1


def test_a_file_that_is_not_a_commit_graph_says_nothing(tmp_path):
    (info_dir(tmp_path) / "commit-graph").write_bytes(b"not a graph at all")
    assert warnings(tmp_path) == []


def test_a_truncated_commit_graph_says_nothing(tmp_path):
    (info_dir(tmp_path) / "commit-graph").write_bytes(SIGNATURE)
    assert warnings(tmp_path) == []
