"""Five new languages all the way through: git ls-files, the pool, the store.

The unit tests measure in this process, where the analysis module is already
imported. The path that runs a repo does not: `analyze_jobs` hands the file list
to a ProcessPoolExecutor once there are 16 or more of them, and a spawned child
on Windows imports `crapkit.analyze` and nothing else. Rust and shell needed that
because they register readers; these five need it for the other half of the
wiring — the cognitive extension's C++ rule, and the duplicate-name line,
both of which run inside the child and would be invisible if they ran anywhere
else.

Every number below is hand-counted from the source above it.
"""
import json
from pathlib import Path

import pytest

from crapkit.keys import key_names
from crapkit.store import SnapshotStore

from conftest import cli_runner, git_commit_all, git_init_repo

# `take` is straight-line code: `&&` marks an rvalue reference. `classify` is
# base 1 + three if links = 4, and Sonar-spec 3 (if +1, two else-ifs +1 each).
CPP = """#include <string>

void take(std::string &&s) {
}

int classify(int n) {
    if (n == 0) return 0;
    else if (n == 1) return 1;
    else if (n == 2) return 2;
    return 9;
}
"""
TAKE = ("take( std :: string && s)", 1, 0)
CLASSIFY = ("classify( int n)", 4, 3)

# A header carries real code in C. base 1 + two ifs = 3.
HEADER = """#pragma once

static inline int clamp(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}
"""
CLAMP = ("clamp( int v , int lo , int hi)", 3)

# Both arms of the fork are textually present, so one file defines one name
# twice. Each arm takes its own ratchet key: `plat_open( const char * p)` and
# the same name suffixed `#2`, counted in file order.
PLATFORM_FORK = """#ifdef _WIN32
static int plat_open(const char *p) {
    if (!p) return -1;
    return 1;
}
#else
static int plat_open(const char *p) {
    if (!p) return -1;
    if (p[0] == 0) return -2;
    return 2;
}
#endif
"""
PLAT_OPEN = "plat_open( const char * p)"

OBJC = """#import <Foundation/Foundation.h>

@implementation Probe
- (NSInteger)classify:(NSInteger)n {
    if (n == 0) return 0;
    if (n == 1) return 1;
    return 9;
}
@end
"""
OBJC_CLASSIFY = ("classify:( NSInteger )", 3)

JAVA = """class Probe {
    int classify(int n) {
        if (n == 0) return 0;
        else if (n == 1) return 1;
        else if (n == 2) return 2;
        return 9;
    }
}
"""
JAVA_CLASSIFY = ("Probe::classify( int n)", 4)

# The template's three branches are HTML attributes and score nothing; the
# method is base 1 + two if links = 3.
VUE = """<template>
  <span v-if="a && b">yes</span>
  <span v-else-if="a || b">maybe</span>
  <p v-for="row in rows" :key="row.id">{{ row.id }}</p>
</template>

<script>
export default {
  methods: {
    classify(n) {
      if (n === 0) return 0;
      else if (n === 1) return 1;
      return 9;
    },
  },
};
</script>
"""
VUE_CLASSIFY = ("classify ( n )", 3)

ZIG = """fn guard(a: bool, b: bool) bool {
    if (a and b) {
        return true;
    }
    return false;
}
"""
ZIG_GUARD = ("guard a : bool , b : bool", 3)

TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "cc"\npaths = ["src"]\nlanguages = ["cpp"]\n'
        'coverage_optional = true\n\n'
        '[[scope]]\nname = "ios"\npaths = ["ios"]\nlanguages = ["objectivec"]\n'
        'coverage_optional = true\n\n'
        '[[scope]]\nname = "ui"\npaths = ["ui"]\nlanguages = ["vue"]\n'
        'coverage_optional = true\n\n'
        '[[scope]]\nname = "jvm"\npaths = ["app"]\nlanguages = ["java"]\n'
        'coverage_optional = true\n\n'
        '[[scope]]\nname = "zg"\npaths = ["zigsrc"]\nlanguages = ["zig"]\n'
        'coverage_optional = true\n')

# analyze.analyze_jobs pools at 16 jobs. 17 C++ files plus 5 more clears it with
# room for the threshold to move a little.
CPP_FILES = 17


run_cli = cli_runner(timeout=300)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def family_repo(tmp_path: Path) -> Path:
    write(tmp_path / "crapkit.toml", TOML)
    for i in range(CPP_FILES):
        write(tmp_path / "src" / f"m{i:02d}.cpp", CPP)
    write(tmp_path / "src" / "clamp.h", HEADER)
    write(tmp_path / "src" / "plat.c", PLATFORM_FORK)
    write(tmp_path / "ios" / "Probe.m", OBJC)
    write(tmp_path / "ui" / "App.vue", VUE)
    write(tmp_path / "app" / "Probe.java", JAVA)
    write(tmp_path / "zigsrc" / "main.zig", ZIG)
    git_init_repo(tmp_path)
    git_commit_all(tmp_path, "init")
    return tmp_path


def inventory(repo: Path) -> dict:
    res = run_cli(repo, "inventory", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return {"summary": json.loads(res.stdout), "stderr": res.stderr}


def store_rows(repo: Path, run_id: int) -> dict[tuple[str, str], object]:
    """(path, long_name) -> row, straight out of the SQLite the run wrote."""
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    return {(row.path, row.long_name): row for row in store.read_rows(run_id)}


def test_every_language_lands_in_the_store_with_its_hand_counted_ccn(family_repo: Path):
    rows = store_rows(family_repo, inventory(family_repo)["summary"]["run_id"])

    measured = {
        "cpp": rows[("src/m00.cpp", CLASSIFY[0])].ccn,
        "header": rows[("src/clamp.h", CLAMP[0])].ccn,
        "objectivec": rows[("ios/Probe.m", OBJC_CLASSIFY[0])].ccn,
        "vue": rows[("ui/App.vue", VUE_CLASSIFY[0])].ccn,
        "java": rows[("app/Probe.java", JAVA_CLASSIFY[0])].ccn,
        "zig": rows[("zigsrc/main.zig", ZIG_GUARD[0])].ccn,
    }

    assert measured == {"cpp": CLASSIFY[1], "header": CLAMP[1],
                        "objectivec": OBJC_CLASSIFY[1], "vue": VUE_CLASSIFY[1],
                        "java": JAVA_CLASSIFY[1], "zig": ZIG_GUARD[1]}


def test_each_scope_claims_its_own_files(family_repo: Path):
    rows = store_rows(family_repo, inventory(family_repo)["summary"]["run_id"])

    assert {row.scope for row in rows.values()} == {"cc", "ios", "ui", "jvm", "zg"}


def test_a_vue_component_contributes_its_script_block_and_nothing_else(family_repo: Path):
    """Three template directives that a reader sees as branches, and one method.
    One row is the whole answer for the file."""
    rows = store_rows(family_repo, inventory(family_repo)["summary"]["run_id"])

    vue = [key for key in rows if key[0] == "ui/App.vue"]

    assert vue == [("ui/App.vue", VUE_CLASSIFY[0])]


def test_every_pool_worker_applied_the_rvalue_rule(family_repo: Path):
    """17 identical C++ files, dealt across the pool in chunks. A child that
    measured with the unpatched extension reports cognitive 1 for `take` on the
    files it handled, so a single distinct value is what catches it."""
    rows = store_rows(family_repo, inventory(family_repo)["summary"]["run_id"])

    take = {row.cognitive for key, row in rows.items() if key[1] == TAKE[0]}

    assert take == {TAKE[2]}, "an rvalue-reference parameter is not a condition"


def test_a_pool_worker_reports_the_platform_fork(family_repo: Path):
    """The line is printed where the analysis happens, which for a repo this
    size is a spawned child. A line written only on the parent's path would pass
    every unit test and never fire on a real run."""
    err = inventory(family_repo)["stderr"]

    assert PLAT_OPEN in err
    assert "src/plat.c" in err


def test_both_arms_of_the_fork_land_with_their_own_key(family_repo: Path):
    """What the line is about, measured. Both arms are scored and each takes its
    own ratchet key, so neither can grow behind the other's mark."""
    run_id = inventory(family_repo)["summary"]["run_id"]
    store = SnapshotStore(family_repo / ".crapkit" / "crap.sqlite")
    fork = [r for r in store.read_rows(run_id) if r.path == "src/plat.c"]

    assert [r.long_name for r in fork] == [PLAT_OPEN, PLAT_OPEN]
    assert sorted(key_names(fork).values()) == [PLAT_OPEN, f"{PLAT_OPEN}#2"]


def test_doctor_passes_on_a_cc_only_polyglot_repo(family_repo: Path):
    """None of the five has a coverage parser here, so every scope declares
    `coverage_optional`; without it a lane-less scope is a doctor FAIL."""
    inventory(family_repo)

    res = run_cli(family_repo, "doctor", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
