def first[T](a: T) -> T:
    if a:
        return a
    return a


def second[T](a: T) -> T:
    return a


def bounded[T: (int, str), U: int](a, b=(),
                                   c=1):
    if a:
        return b
    return c
