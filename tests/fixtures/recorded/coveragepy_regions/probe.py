import functools

LIMIT = 3


def outer_full(a, b, c):
    def never(v): return v
    if a:
        x = 1
    else:
        x = 2
    if b:
        x += 1
    else:
        x -= 1
    return x


def outer_one(a):
    def called(v): return v
    if a:
        return called(1)
    return 0


def outer_multi(flag):
    def inner(value):
        if value:
            return 1
        return 2
    if flag:
        return 3
    return 4


def two_liners_then_multi(flag):
    def first(v): return v
    def second(v): return -v
    def third(v):
        if v:
            return 1
        return 2
    if flag:
        return first(1)
    return second(2)


ANSWER = 42
def top_after_statement(v): return v + ANSWER


def top_called(v): return v * 2


def nested_after_statement(a):
    base = a + 1
    def add(v): return v + base
    if a:
        return add(1)
    return base


def only_doc():
    """Nothing but a docstring."""
def after_doc(v): return v


def decorated_host(flag):
    @functools.lru_cache(maxsize=None)
    def cached(v):
        if v:
            return 1
        return 0
    return cached(flag)


@functools.lru_cache(maxsize=None)
def decorated_top(v):
    if v:
        return 1
    return 0


@staticmethod
def decorated_one(v): return v


class Shape:
    def one(self): return 1

    def two(self, flag):
        if flag:
            return 2
        return 3


def class_host(flag):
    class Local:
        def m(self): return flag
    if flag:
        return Local().m()
    return None


def genexpr_first(items):
    found = any(x for x in items if x > LIMIT)
    if found:
        return 1
    return 0


def only_nested():
    def lone(v): return v


def only_nested_multi():
    def lone_multi(v):
        if v:
            return 1
        return 0


def excluded_one(v): return v  # pragma: no cover


def stub_one(v): ...


async def async_one(v): return v


def multi_sig(
        a, b): return a + b


def outer_deep(a):
    def middle(b):
        def leaf(c): return c
        if b:
            return leaf(b)
        return 0
    if a:
        return middle(a)
    return 0


def wrap(items):
    total = len(items)
    def each(item):
        return item + total
    return [each(i) for i in items]


def wrap_gen(items):
    def pick(xs):
        return any(x for x in xs if x > LIMIT)
    if items:
        return pick(items)
    return False


def top_one_statement(v):
    return v + 1


def class_host_first(flag):
    class Inner:
        def first(self): return flag
        def second(self):
            return not flag
    return Inner().first() if flag else Inner().second()


def nested_doc_then_one(flag):
    def doc_only():
        """Nothing but a docstring."""
    def after(v): return v
    if flag:
        return after(1)
    return doc_only()


def deep_one(a):
    def mid(b):
        def tip(c): return c
        return tip(b)
    return mid(a)


def lambda_first(items):
    key = lambda item: -item  # noqa: E731
    if items:
        return sorted(items, key=key)
    return []


def pass_host(flag):
    def noop(v):
        pass
    if flag:
        return noop(1)
    return 0
