"""A dependency venv imports the suite's dependencies from where the parent does.

Four tests build a throwaway venv with the `dependency_venv` fixture and run
pytest, pytest-cov, coverage or crapkit inside it. A parent created with
--system-site-packages keeps an empty site-packages of its own and imports those
packages from the base install and the user site. A bridge that carried only the
parent's purelib and platlib handed the child that empty directory, and the
child failed with `No module named pytest`.
"""
import importlib.util
import subprocess

SUITE_DEPENDENCIES = ("pytest", "pytest_cov", "coverage", "lizard")


def _origin(python, name):
    code = ("import importlib.util as u; s = u.find_spec(" + repr(name) + "); "
            "print(s.origin if s else '')")
    done = subprocess.run([str(python), "-X", "utf8", "-c", code], capture_output=True,
                          text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


def _parent_origins():
    specs = {name: importlib.util.find_spec(name) for name in SUITE_DEPENDENCIES}
    return {name: spec.origin for name, spec in specs.items() if spec}


def test_the_child_imports_each_suite_dependency_from_the_parents_file(tmp_path, dependency_venv):
    python, _ = dependency_venv(tmp_path / "venv")
    parent = _parent_origins()

    child = {name: _origin(python, name) for name in parent}

    assert "pytest" in parent
    assert child == parent


def test_the_childs_own_site_still_wins_over_the_bridge(tmp_path, dependency_venv):
    """A test that installs a package into the child site must import that copy,
    not the parent's."""
    python, site = dependency_venv(tmp_path / "venv")
    (site / "coverage").mkdir()
    (site / "coverage" / "__init__.py").write_text("", encoding="utf-8")

    assert _origin(python, "coverage") == str(site / "coverage" / "__init__.py")
