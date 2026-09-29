"""PEP 695 type parameters on a function, a class and a type alias (Python 3.12)."""

type Pair[K, V] = tuple[K, V]


def first[T](items: list[T], default: T) -> T:
    for item in items:
        if item is not None:
            return item
    return default


class Stack[T]:
    def __init__(self) -> None:
        self.items: list[T] = []

    def push(self, item: T) -> None:
        if item is None:
            raise ValueError("no None on the stack")
        self.items.append(item)

    def pop_or[D](self, default: D) -> T | D:
        if self.items:
            return self.items.pop()
        return default
