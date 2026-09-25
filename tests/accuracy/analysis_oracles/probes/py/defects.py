def floor_split(a, b, c):
    x = a // 4 + 1 if b else c
    return x


def close(self):
    self.inner.close()


def comma_run(a, b, x, y):
    if a or pick(x, y) or b:
        return 1
    return 0


def run_over_lines(a, b, c):
    return (a or b
            or c)


def two_comprehensions(a, b):
    return [p for p in a] + [q for q in b]


def fstring_operator(a, b):
    return f"{a or b}"


def dict_then_while(e):
    table = {1: 2}
    while e:
        e = table.get(e)
    return e


def keyword_named(cmd):
    cmd.do(1)


def with_recursive_helper(n):
    def countdown(k):
        if k <= 0:
            return 0
        return countdown(k - 1)
    return countdown(n)


def condition_comprehension(xs):
    if any(x for x in xs):
        return 1
    return 0


def element_ternary(xs):
    return [1 if x else 0 for x in xs]


def filter_then_for(xs):
    return [y for x in xs if x for y in x]


def ternary_iterable(a, b, c):
    return [x for x in (a if b else c)]


def is_even(n):
    return True if n == 0 else is_odd(n - 1)


def is_odd(n):
    return False if n == 0 else is_even(n - 1)
