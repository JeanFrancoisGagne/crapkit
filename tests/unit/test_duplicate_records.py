"""One file, two functions, one name: the records and the line that names them.

Until 0.4.2 this was a loss. The ratchet keyed a mark on `(path, long_name)`, so
one function under a colliding name was marked and gated and the others were
neither. `keys` ends that: the first keeps the bare name and the rest take a
`#N` ordinal counted in file order, so every twin carries its own mark.

The stderr line stays, as information rather than a warning. A `#2` turning up
in a committed `crapkit-ratchet.tsv` is otherwise unexplained, and the shape is
ordinary in two languages at once. C: both arms of an `#ifdef` fork are
textually present, so a platform shim defines `plat_open` twice in one file and
lizard reports both. Python: a method's long_name carries no class, so two
classes in one module with an `__init__` collide.

Anonymous functions are exempt from the line. lizard names every one of them
`(anonymous)`, so a file with two arrow callbacks collides by construction and
the line would name nothing a reader could act on. They take the same ordinal
keys as any other twin, and `packet.handles` already addresses them as
`(anonymous)#N`.
"""
from crapkit.analyze import analyze_source
from crapkit.keys import key_names, key_of

# The platform-fork shape, both arms present. Five functions, two names.
IFDEF_FORK = """#ifdef _WIN32
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

#if defined(USE_FAST)
int compute(int a, int b) { if (a > b) return a; return b; }
#elif defined(USE_SLOW)
int compute(int a, int b) { if (a < b) return a; if (a == b) return 0; return b; }
#else
int compute(int a, int b) { return a + b; }
#endif
"""

UNIQUE = """int first(int a) {
    if (a) return 1;
    return 0;
}
int second(int a) {
    if (a) return 2;
    return 0;
}
"""

# Two arrow callbacks with no parameters. Both come out as bare `(anonymous)`.
ANONYMOUS_TWICE = """export function mount(list: number[]) {
  list.forEach(() => {
    if (list.length) return;
  });
  list.forEach(() => {
    if (list.length) return;
  });
}
"""

TWO_CLASSES = """class Reader:
    def __init__(self, path):
        self.path = path


class Writer:
    def __init__(self, path):
        self.path = path
"""


def test_an_ifdef_fork_yields_five_records_under_two_names():
    """The measurement the line is about. Nothing here is wrong: lizard reads
    the file it was given, and the file really does define both arms."""
    records = analyze_source("src/plat.c", IFDEF_FORK)

    assert len(records) == 5
    assert len({r.long_name for r in records}) == 2


def test_every_arm_of_the_fork_carries_its_own_key():
    """Two arms of `plat_open` and three of `compute`, five keys. Before the
    ordinal these were two, and three of the five functions were ungated."""
    records = analyze_source("src/plat.c", IFDEF_FORK)

    keys = key_names(records)

    assert sorted(key_of(keys, r)[1] for r in records) == [
        "compute( int a , int b)", "compute( int a , int b)#2", "compute( int a , int b)#3",
        "plat_open( const char * p)", "plat_open( const char * p)#2"]


def test_the_fork_reports_once_and_names_both_collisions(capsys):
    analyze_source("src/plat.c", IFDEF_FORK)

    err = capsys.readouterr().err

    assert err.count("crapkit:") == 1, "one line per file, not one per record"
    assert "src/plat.c" in err
    assert "plat_open( const char * p)" in err
    assert "compute( int a , int b)" in err


def test_the_line_says_how_the_twins_are_keyed(capsys):
    """A reader who sees `#2` in a diff of the marks file has to be able to find
    out what put it there. The old line promised the opposite — that the earlier
    records were lost — and that promise is now wrong."""
    analyze_source("src/plat.c", IFDEF_FORK)

    err = capsys.readouterr().err

    assert "own ratchet key" in err and "#2" in err
    assert "neither marked nor gated" not in err


def test_a_file_whose_names_are_all_distinct_is_silent(capsys):
    analyze_source("src/ok.c", UNIQUE)

    assert capsys.readouterr().err == ""


def test_two_anonymous_arrows_never_print_a_line(capsys):
    """The exemption, pinned against the fixtures that produce it. Both
    callbacks really do collide on `(anonymous)`; naming that in a line would
    fire on every TypeScript file holding two callbacks and say nothing."""
    records = analyze_source("web/app.ts", ANONYMOUS_TWICE)

    assert [r.long_name for r in records].count("(anonymous)") == 2
    assert capsys.readouterr().err == ""


def test_anonymous_twins_are_still_keyed_apart():
    """Silent is not exempt. Each callback carries its own mark, and the keys
    line up with the `(anonymous)#N` handles from #2 up."""
    keys = key_names(analyze_source("web/app.ts", ANONYMOUS_TWICE))

    assert sorted(keys.values()) == ["(anonymous)", "(anonymous)#2", "mount ( list )"]


def test_the_check_is_language_blind(capsys):
    """Python collides for its own reason — a method's long_name carries no
    class — and the key answers it the same way. A check keyed on the C family
    would have to be rewritten the first time this shape mattered elsewhere."""
    analyze_source("src/io.py", TWO_CLASSES)

    err = capsys.readouterr().err

    assert "src/io.py" in err
    assert "__init__( self , path )" in err
