def straight(a):
    b = a + 1
    c = b * 2
    return c


def one_if(a):
    if a:
        return 1
    return 0


def if_elif_else(score):
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    else:
        return "C"


def nested_in_else(a, b):
    if a:
        return 1
    else:
        if b:
            return 2
    return 0


def four_deep(a, b, c, d):
    if a:
        for x in b:
            while c:
                if d:
                    return x
    return 0


def flat_seven(a):
    if a == 1:
        return 1
    if a == 2:
        return 2
    if a == 3:
        return 3
    if a == 4:
        return 4
    if a == 5:
        return 5
    if a == 6:
        return 6
    if a == 7:
        return 7
    return 0


def three_loops(xs):
    total = 0
    for a in xs:
        for b in a:
            for c in b:
                total += c
    return total


def first_negative(xs):
    i = 0
    while i < len(xs):
        if xs[i] < 0:
            break
        i += 1
    return i


def ternary(a, b, c):
    return b if a else c


def boolean_return(a, b):
    return a and b


def one_except(text):
    try:
        return int(text)
    except ValueError:
        return 0
    finally:
        pass


def two_excepts(text):
    try:
        return int(text)
    except ValueError:
        return 0
    except TypeError:
        return -1


def with_block(path):
    with open(path) as handle:
        return handle.read()


def comprehension(xs):
    return [x for x in xs if x > 0]


async def async_loop(stream):
    total = 0
    async for item in stream:
        total += item
    return total
