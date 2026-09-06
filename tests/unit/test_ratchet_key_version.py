"""A key-format migration cannot silently move existing debt between callbacks."""
import pytest

from crapkit.ratchet import (RatchetEntry, check_key_groups, dump_ratchet,
                             read_key_version, read_stamp)


def _text(*entries, version=0):
    return dump_ratchet([RatchetEntry(*entry) for entry in entries],
                        stamp="crapkit-analysis=9 lizard=1.24.0", key_version=version)


def test_key_version_is_separate_from_metric_stamp():
    text = _text(("a.ts", "f( x )", 20), version=1)
    assert read_key_version(text) == 1
    assert read_stamp(text) == "crapkit-analysis=9 lizard=1.24.0"
    assert read_stamp(dump_ratchet([], stamp="", key_version=1)) == ""


@pytest.mark.parametrize("marker", ["2", "-1", "nan", "1\n# crapkit-keys=0"])
def test_unknown_or_duplicate_key_versions_are_refused(marker):
    with pytest.raises(ValueError, match="key identity"):
        read_key_version(f"# crapkit-keys={marker}\npath\tlong_name\tcrap\n")


def test_legacy_keys_promote_only_when_all_marks_have_known_safe_groups():
    text = _text(("a.py", "f( )", 20), ("b.py", "g( )#2", 30))
    assert check_key_groups(text, {("a.py", "f( )")}, set()) == 0
    assert check_key_groups(text, {("a.py", "f( )"), ("b.py", "g( )")}, set()) == 1
    assert check_key_groups(_text(), set(), set()) == 1


@pytest.mark.parametrize("marked_name", ["f( x )", "f( x )#2", "f( x )#3"])
def test_collision_refuses_the_entire_marked_name_group(marked_name):
    text = _text(("a.ts", marked_name, 20), ("safe.py", "g( )", 99))
    before = text
    with pytest.raises(ValueError, match="a.ts.*f"):
        check_key_groups(text, {("a.ts", "f( x )")}, {("a.ts", "f( x )")})
    assert text == before


def test_unmarked_collisions_do_not_reassign_unrelated_marks():
    text = _text(("safe.py", "g( )", 99))
    assert check_key_groups(text, {("safe.py", "g( )")}, {("a.ts", "f( x )")}) == 1


def test_current_keys_remain_current_when_a_marked_group_has_collisions():
    text = _text(("a.ts", "f( x )#2", 20), version=1)
    assert check_key_groups(text, set(), {("a.ts", "f( x )")}) == 1
