"""A module no test imports: its functions read untested."""


def orphan(a, b, c):
    if a:
        return 1
    if b:
        return 2
    if c:
        return 3
    return 0


def lonely(x):
    return x
