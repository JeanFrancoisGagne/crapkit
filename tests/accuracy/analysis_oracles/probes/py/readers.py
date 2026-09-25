def annotated(a: int) -> dict[str,
                              int]:
    if a:
        return {}
    return {"a": a}


def defaults(a, bases=(),
             skip=frozenset()):
    if a:
        return bases
    return skip


def continued(a) \
        -> int:
    if a:
        return 1
    return 0


def fill_spec(x): return f"{x:(>10}"


def after_fill_spec(y):
    if y:
        return 1
    return 0


def three(a):
    def two(b):
        def one(c):
            return c
        return one
    return two


def parent(a):
    def helper(b): return b
    if a:
        return helper(a)
    return 0


def one_line_comp(x): return [a for a in x if a or x]


def two_line_comp(x):
    return [a for a in x if a or x]


def last_before_main(a):
    if a:
        return 1
    return 0


if __name__ == "__main__":
    last_before_main(1)
