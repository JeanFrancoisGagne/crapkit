def documented(a):
    """A docstring is a string, not a comment."""
    # a comment line
    b = a + 1

    return b


def keyword_params(a, b=1, *args, c, d=2, **kwargs):
    return a


def positional_only(a, b, /, c, *, d):
    return a


def no_params():
    return 0


class Holder:
    def method(self, x):
        if x:
            return self
        return None

    @staticmethod
    def helper(x):
        return x


@property
def decorated(x):
    return x


def one_liner(x): return x


def outer(a):
    def middle(b):
        def innermost(c):
            return c
        return innermost
    return middle


def wrapped_signature(
    first,
    second,
):
    if first:
        return second
    return None
