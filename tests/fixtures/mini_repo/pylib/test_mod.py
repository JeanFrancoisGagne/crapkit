from mod import guarded


def test_both_branches_and_the_comprehension():
    assert guarded(4, 1) == [1, 3]
    assert guarded(0, 0) == []
