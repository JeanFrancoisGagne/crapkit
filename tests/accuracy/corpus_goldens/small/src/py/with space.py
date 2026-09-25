"""A path with a space in it."""


def spaced(a, b):
    if a and b:
        return a + b
    return a or b
