"""Lane artifacts that land in the consumer's tree instead of under .crapkit/.

The measured failure: a 14-lane repo grew fifteen coverage-* directories and
seven junit files at its root, one per lane. Every lane ran, every score was
right, and nothing in the tree said which lane owned which file. Doctor warns
and never fails: a consumer whose lanes already write there must keep gating.
"""
from crapkit.config import Lane, Scope
from crapkit.doctor import artifact_litter, scope_top_dirs


def _lane(name: str, artifact: str, results: str = "") -> Lane:
    return Lane(name=name, command="run", artifact=artifact, parser="istanbul",
                scopes=("src",), results_artifact=results)


def _scope(*paths: str) -> Scope:
    return Scope(name="src", paths=paths, languages=("python",))


SRC_TOPS = frozenset({"src"})


def test_an_artifact_at_the_repo_root_is_litter():
    (found,) = artifact_litter([_lane("py", "coverage-py.json")], SRC_TOPS)

    assert found.lane == "py"
    assert found.path == "coverage-py.json"


def test_a_top_level_directory_of_its_own_is_litter_too():
    """Fifteen of these is what the tree actually grew. The file is nested; the
    directory it drags in is not."""
    (found,) = artifact_litter([_lane("js", "coverage/coverage-final.json")], SRC_TOPS)

    assert found.path == "coverage/coverage-final.json"


def test_anything_under_the_crapkit_store_is_clean():
    lanes = [_lane("py", ".crapkit/cov/py.json"),
             _lane("js", ".crapkit/cov/js/coverage-final.json")]

    assert artifact_litter(lanes, SRC_TOPS) == ()


def test_an_artifact_inside_a_scopes_own_tree_is_clean():
    """A monorepo package that holds both the code and its coverage output is
    not root litter: web/coverage/ sits beside the web/src the lane measures."""
    lane = _lane("web", "web/coverage/coverage-final.json")

    assert artifact_litter([lane], scope_top_dirs([_scope("web/src")])) == ()


def test_the_results_artifact_is_judged_by_the_same_rule():
    lane = _lane("py", ".crapkit/cov/py.json", results="junit.xml")

    (found,) = artifact_litter([lane], SRC_TOPS)

    assert (found.lane, found.path) == ("py", "junit.xml")


def test_both_paths_of_one_lane_report_separately():
    """Seven junit files at the root came from lanes whose coverage path was
    already being argued about. One finding per path, or one of them is silent."""
    lane = _lane("api", "coverage-api.json", results="junit-api.xml")

    assert [f.path for f in artifact_litter([lane], SRC_TOPS)] == ["coverage-api.json",
                                                                  "junit-api.xml"]


def test_a_lane_with_no_results_artifact_reports_once():
    assert len(artifact_litter([_lane("py", "cov.json")], SRC_TOPS)) == 1


def test_findings_keep_lane_declaration_order():
    lanes = [_lane("z", "z.json"), _lane("a", "a.json")]

    assert [f.lane for f in artifact_litter(lanes, SRC_TOPS)] == ["z", "a"]


def test_a_windows_spelled_path_is_read_as_the_same_path():
    assert artifact_litter([_lane("py", ".crapkit\\cov\\py.json")], SRC_TOPS) == ()


def test_scope_top_dirs_collapses_every_declared_path_to_its_first_component():
    scopes = [Scope(name="a", paths=("web/src", "web/lib"), languages=("typescript",)),
              Scope(name="b", paths=("api",), languages=("python",))]

    assert scope_top_dirs(scopes) == frozenset({"web", "api"})
