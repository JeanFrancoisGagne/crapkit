"""POSIX family admission rules run beside native nested-process lifetime tests."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from crapkit import _process_family as family
from crapkit.errors import ToolError


@pytest.fixture()
def lock_adapter(monkeypatch):
    calls = []

    def flock(descriptor, operation):
        assert operation == 2
        os.fstat(descriptor.fileno())
        calls.append(descriptor)

    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(flock=flock, LOCK_EX=2))
    return calls


def test_family_preserves_ancestors_and_the_explicit_child_environment(monkeypatch, tmp_path):
    ancestor = str(tmp_path / 'ancestor')
    monkeypatch.setenv('CRAPKIT_COMMAND_FAMILIES', json.dumps([ancestor]))
    name, environment = family.command_environment({'ONLY_CHILD': 'value'})
    assert environment == {'ONLY_CHILD': 'value',
                           'CRAPKIT_COMMAND_FAMILIES': json.dumps([ancestor, name])}
    assert not Path(name).exists(), 'naming an unstarted command must not leave a directory'


def test_family_stops_admission_before_cleanup_and_removes_its_files(
        monkeypatch, tmp_path, lock_adapter):
    owned = family.Family(tmp_path / 'family')
    monkeypatch.setenv('CRAPKIT_COMMAND_FAMILIES', json.dumps([str(owned.path)]))
    with family.ancestor_leases():
        assert len(list(owned.path.glob('*.lease'))) == 1
    with owned.stopping():
        assert (owned.path / 'closed').exists()
        with pytest.raises(ToolError, match='ancestor command has stopped'):
            with family.ancestor_leases():
                pytest.fail('a stopped family cannot admit another guardian')
    assert not owned.path.exists()
    assert len(lock_adapter) == 5


@pytest.mark.parametrize('parents', [[], ['missing']])
def test_absent_ancestry_is_optional_but_a_missing_named_family_refuses(
        monkeypatch, tmp_path, lock_adapter, parents):
    paths = [str(tmp_path / name) for name in parents]
    monkeypatch.setenv('CRAPKIT_COMMAND_FAMILIES', json.dumps(paths))
    if parents:
        with pytest.raises(ToolError, match='cannot retain ownership'):
            with family.ancestor_leases():
                pytest.fail('a missing ancestor cannot be recreated during registration')
    else:
        with family.ancestor_leases():
            assert lock_adapter == []
    assert list(tmp_path.iterdir()) == []


def test_failed_family_setup_removes_its_empty_directory(monkeypatch, tmp_path):
    def fail_admission(_):
        raise PermissionError('admission denied')

    monkeypatch.setattr(Path, 'touch', fail_admission)
    with pytest.raises(PermissionError, match='admission denied'):
        family.Family(tmp_path / 'family')
    assert not (tmp_path / 'family').exists()
