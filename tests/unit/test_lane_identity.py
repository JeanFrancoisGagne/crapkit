"""Each configured lane owns one result slot and distinct output paths."""
import pytest

from crapkit.config import load_config_text
from crapkit.errors import ConfigError


SCOPE = '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'


def lane(name, artifact):
    return (f'[[lane]]\nname = "{name}"\ncommand = "pytest"\n'
            f'artifact = "{artifact}"\nparser = "coveragepy"\nscopes = ["src"]\n')


@pytest.mark.parametrize('second_artifact', ['second.json', 'first.json'])
def test_duplicate_lane_names_are_refused_before_execution(second_artifact):
    text = SCOPE + lane('same', 'first.json') + lane('same', second_artifact)

    with pytest.raises(ConfigError, match='duplicate lane name'):
        load_config_text(text)


@pytest.mark.parametrize('alias', ['./cov.json', 'nested/../cov.json'])
def test_equivalent_artifact_paths_cannot_have_two_lane_owners(alias):
    text = SCOPE + lane('one', 'cov.json') + lane('two', alias)

    with pytest.raises(ConfigError, match='share the artifact path'):
        load_config_text(text)


def test_absolute_and_relative_artifacts_have_one_owner(tmp_path):
    absolute = (tmp_path / 'cov.json').as_posix()
    text = SCOPE + lane('one', 'cov.json') + lane('two', absolute)

    with pytest.raises(ConfigError, match='share the artifact path'):
        load_config_text(text, root=tmp_path)
