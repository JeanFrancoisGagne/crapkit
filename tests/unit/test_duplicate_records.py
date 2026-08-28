"""One file, two functions, one name: the records that never reach the ratchet.

The ratchet keys a mark on `(path, long_name)` — spans drift with every edit,
names survive — and a store write keyed the same way keeps the last row it sees.
So when one file emits two functions under one name, only the last is marked and
only the last is gated. The earlier ones are measured, scored, and then dropped
without a word.

C makes this ordinary rather than exotic. Both arms of a `#ifdef` fork are
textually present, so a platform shim defines `plat_open` twice in one file and
lizard reports both. Python makes it ordinary too, for a different reason: a
method's long_name carries no class, so two classes in one module with an
`__init__` collide. Neither is a bug crapkit can fix — the ratchet cannot key on
a span — so it says so on stderr instead of losing the row in silence.

Anonymous functions are the one exemption, and they are exempt because the
collision there is total and already answered: lizard names every one of them
`(anonymous)`, so a file with two arrow callbacks would warn on every cold run
and name nothing a reader could act on. `packet.handles` gives those the
`(anonymous)#N` ordinal instead.
"""
from crapkit.analyze import analyze_source

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
    """The measurement the warning is about. Nothing here is wrong: lizard reads
    the file it was given, and the file really does define both arms."""
    records = analyze_source("src/plat.c", IFDEF_FORK)

    assert len(records) == 5
    assert len({r.long_name for r in records}) == 2


def test_the_fork_warns_once_and_names_both_collisions(capsys):
    analyze_source("src/plat.c", IFDEF_FORK)

    err = capsys.readouterr().err

    assert err.count("crapkit:") == 1, "one line per file, not one per record"
    assert "src/plat.c" in err
    assert "plat_open( const char * p)" in err
    assert "compute( int a , int b)" in err


def test_the_warning_says_which_record_survives(capsys):
    """A warning that only says `duplicate` leaves the reader to guess whether
    the mark on that name describes the first arm or the last."""
    analyze_source("src/plat.c", IFDEF_FORK)

    err = capsys.readouterr().err

    assert "ratchet" in err and "last" in err


def test_a_file_whose_names_are_all_distinct_is_silent(capsys):
    analyze_source("src/ok.c", UNIQUE)

    assert capsys.readouterr().err == ""


def test_two_anonymous_arrows_never_warn(capsys):
    """The exemption, pinned against the fixtures that produce it. Both
    callbacks really do collide on `(anonymous)`; crapkit answers that with an
    ordinal handle, not with a warning it would print on every TypeScript file
    holding two callbacks."""
    records = analyze_source("web/app.ts", ANONYMOUS_TWICE)

    assert [r.long_name for r in records].count("(anonymous)") == 2
    assert capsys.readouterr().err == ""


def test_the_check_is_language_blind(capsys):
    """Python collides for its own reason — a method's long_name carries no
    class — and the ratchet loses the row exactly the same way. A check keyed on
    the C family would have to be rewritten the first time this shape mattered
    somewhere else."""
    analyze_source("src/io.py", TWO_CLASSES)

    err = capsys.readouterr().err

    assert "src/io.py" in err
    assert "__init__( self , path )" in err
