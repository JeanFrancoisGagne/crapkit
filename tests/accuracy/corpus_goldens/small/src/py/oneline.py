"""One-line defs: coverage.py cannot tell a body on the def line from the def statement."""


def twice(x): return x * 2


def clamp(value, low, high): return low if value < low else (high if value > high else value)


def signed(value): return -1 if value < 0 else 1


def called_on_two_lines(value):
    return value + 1
