def straight(a):
    b = a + 1
    return b


def four_deep(a, b, c, d):
    if a:
        if b:
            if c:
                if d:
                    return 1
    return 0


def nested_loops(n):
    t = 0
    for i in range(n):
        for j in range(n):
            for k in range(n):
                t += 1
    return t


def flat_seven(a):
    n = 0
    if a == 1:
        n += 1
    if a == 2:
        n += 1
    if a == 3:
        n += 1
    if a == 4:
        n += 1
    if a == 5:
        n += 1
    if a == 6:
        n += 1
    if a == 7:
        n += 1
    return n
