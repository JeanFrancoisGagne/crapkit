"""A kernel-confirmed empty process group needs no system-wide membership scan."""
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from crapkit import _process_owner as owner
from hang_guard import communicate


def group_adapter(monkeypatch, outcome):
    probe = Mock(side_effect=outcome)
    scan = Mock(return_value=False)
    monkeypatch.setattr(owner, 'os', SimpleNamespace(killpg=probe))
    monkeypatch.setattr(owner, 'kill_process_tree', Mock())
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


def test_a_refused_probe_leaves_cleanup_to_the_zombie_aware_scan(monkeypatch):
    """Darwin refuses the probe with EPERM when the group's only member is an
    exited, unreaped leader: XNU's killpg1 skips zombies and answers EPERM when
    it found no one. The group still exists, so the scan decides, and the
    refusal alone never confirms cleanup (#78)."""
    probe, scan = group_adapter(monkeypatch, PermissionError(1, 'Operation not permitted'))
    scan.side_effect = [True, False]
    monkeypatch.setattr(owner, 'time', SimpleNamespace(sleep=Mock()))
    assert owner._group_exists(731) is True
    owner._ProcessGroup(731).stop()
    assert probe.call_args_list == [call(731, 0)] * 3
    assert scan.call_args_list == [call(731), call(731)]


@pytest.mark.skipif(not hasattr(os, 'waitid'), reason='no os.waitid: Windows, or macOS before 3.13')
def test_a_group_left_with_only_its_unreaped_leader_stops_on_the_real_kernel():
    """The state a lane leaves: procs waits with WNOWAIT, so the leader stays a
    zombie until the owner confirms the group stopped (#78)."""
    leader = subprocess.Popen([sys.executable, '-c', ''], start_new_session=True)
    try:
        os.waitid(os.P_PID, leader.pid, os.WEXITED | os.WNOWAIT)
        owner._ProcessGroup(leader.pid).stop()
    finally:
        leader.wait()


@pytest.mark.skipif(os.name == 'nt', reason='Windows stops a lane with a Job, not a process group')
def test_the_process_table_scan_finds_ps_off_the_callers_path(tmp_path, monkeypatch):
    """Every macOS lane stop reaches the ps scan, since the kernel refuses the
    probe on the unreaped leader. A crapkit started with a PATH that holds no
    ps, as the doctor tests start it, must still stop its lanes (#78)."""
    monkeypatch.setattr(owner, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setenv('PATH', str(tmp_path))
    member = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                              stdin=subprocess.PIPE, start_new_session=True)
    try:
        assert owner._group_active(member.pid) is True
    finally:
        communicate(member)
    assert owner._group_active(member.pid) is False
