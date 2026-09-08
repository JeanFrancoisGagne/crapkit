"""A kernel-confirmed empty process group needs no system-wide membership scan."""
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from crapkit import _process_owner as owner


def group_adapter(monkeypatch, outcome):
    probe = Mock(side_effect=outcome)
    scan = Mock(return_value=False)
    monkeypatch.setattr(owner, 'os', SimpleNamespace(killpg=probe))
    monkeypatch.setattr(owner, '_kill_pid', Mock())
    monkeypatch.setattr(owner, '_group_active', scan)
    return probe, scan


def test_a_kernel_confirmed_empty_group_needs_no_process_table_scan(monkeypatch):
    probe, scan = group_adapter(monkeypatch, ProcessLookupError())
    scan.side_effect = AssertionError('an empty group does not need a global scan')
    owner._ProcessGroup(731).stop()
    probe.assert_called_once_with(731, 0)
    scan.assert_not_called()


def test_a_group_that_still_exists_keeps_the_zombie_aware_scan(monkeypatch):
    probe, scan = group_adapter(monkeypatch, None)
    scan.side_effect = [True, False]
    pause = Mock()
    monkeypatch.setattr(owner, 'time', SimpleNamespace(sleep=pause))
    owner._ProcessGroup(731).stop()
    assert probe.call_args_list == [call(731, 0), call(731, 0)]
    assert scan.call_args_list == [call(731), call(731)]
    pause.assert_called_once_with(.01)


def test_a_probe_permission_failure_cannot_confirm_cleanup(monkeypatch):
    _, scan = group_adapter(monkeypatch, PermissionError('group probe denied'))
    with pytest.raises(PermissionError, match='group probe denied'):
        owner._ProcessGroup(731).stop()
    scan.assert_not_called()
