"""Change coupling: files that keep landing in the same commits are coupled,
whatever the import graph says. Same git-log text the churn pass reads."""
from crapkit.coupling import change_coupling, partners

A = "\x01alice\x021000"
B = "\x01bob\x022000"


def _log(*commits):
    blocks = []
    for i, files in enumerate(commits):
        blocks.append((A if i % 2 == 0 else B) + "\n" + "\n".join(files) + "\n")
    return "\n".join(blocks)


def test_coupled_pair_with_support_and_confidence():
    log = _log(*[["src/a.ts", "src/b.ts"]] * 4, ["src/a.ts"], ["src/b.ts"])
    (pair,) = change_coupling(log, min_support=3, min_confidence=0.5)
    assert pair["files"] == ["src/a.ts", "src/b.ts"]
    assert pair["support"] == 4
    assert pair["confidence"] == 0.8, "4 of the 5 commits touching either also touched the other"


def test_confidence_is_the_max_of_both_directions():
    # b.ts appears in 4 commits, always with a.ts; a.ts appears in 8
    log = _log(*[["src/a.ts", "src/b.ts"]] * 4, *[["src/a.ts"]] * 4)
    (pair,) = change_coupling(log, min_support=3, min_confidence=0.9)
    assert pair["confidence"] == 1.0, "every b.ts commit touched a.ts"


def test_low_support_pairs_are_dropped():
    log = _log(["src/a.ts", "src/b.ts"], ["src/a.ts", "src/b.ts"])
    assert change_coupling(log, min_support=3, min_confidence=0.5) == []


def test_bulk_commits_do_not_couple_everything():
    wide = [f"src/f{i}.ts" for i in range(40)]
    log = _log(*[wide] * 5)
    assert change_coupling(log, min_support=3, min_confidence=0.5) == [], \
        "a 40-file commit says nothing about any particular pair"


def test_partners_name_the_other_side_of_every_pair_a_file_is_in():
    log = _log(*[["src/a.ts", "src/b.ts"]] * 4, *[["src/a.ts", "src/c.ts"]] * 3)
    out = partners(log.splitlines(), "src/a.ts", min_support=3, min_confidence=0.4)
    assert out == [{"path": "src/b.ts", "support": 4, "confidence": 1.0},
                   {"path": "src/c.ts", "support": 3, "confidence": 1.0}], \
        "b.ts leads: 4 shared commits beat 3 at equal confidence"


def test_a_files_partners_survive_a_global_top_that_would_cut_them():
    # 60 pairs outrank (a.ts, z.ts); the default cap of 50 applied before the
    # file cut would leave a.ts reading as uncoupled.
    noisy = [[f"src/n{i}.ts", f"src/m{i}.ts"] for i in range(60) for _ in range(9)]
    log = _log(*noisy, *[["src/a.ts", "src/z.ts"]] * 3)
    out = partners(log.splitlines(), "src/a.ts", min_support=3, min_confidence=0.5)
    assert [p["path"] for p in out] == ["src/z.ts"]


def test_partners_of_an_uncoupled_file_is_empty_not_an_error():
    log = _log(["src/a.ts"], ["src/b.ts"])
    assert partners(log.splitlines(), "src/a.ts", min_support=3, min_confidence=0.5) == []
