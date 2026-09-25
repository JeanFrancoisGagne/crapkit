# Worked examples from G. Ann Campbell, "Cognitive Complexity", SonarSource,
# version 1.7 (29 August 2023), written in Python. Java's labeled `continue`
# has no Python spelling, so sum_of_primes drops it.


def sum_of_primes(limit):
    total = 0
    for i in range(1, limit + 1):
        for j in range(2, i):
            if i % j == 0:
                continue
        total += i
    return total


def get_words(number):
    match number:
        case 1:
            return "one"
        case 2:
            return "a couple"
        case 3:
            return "a few"
        case _:
            return "lots"


def my_method(condition1, condition2):
    try:
        if condition1:
            for i in range(10):
                while condition2:
                    pass
    except (TypeError, ValueError):
        if condition2:
            pass


def my_method2(condition1):
    r = lambda: 1 if condition1 else 0
    return r


def logical_mix(a, b, c, d, e, f):
    if a and b and c or d or e and f:
        return 1
    return 0


def logical_not(a, b, c):
    if a and not (b and c):
        return 1
    return 0


def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def a_decorator(a, b):
    def inner(func):
        if a:
            print(b)
        func()
    return inner


def not_a_decorator(a, b):
    my_var = a * b
    def inner2(func):
        if my_var:
            print(b)
        func()
    return inner2
