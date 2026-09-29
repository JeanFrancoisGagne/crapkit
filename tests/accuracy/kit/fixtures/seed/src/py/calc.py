"""The seed corpus's Python half: branches, a loop, a boolean chain and a one-line def."""


def classify(score, late):
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    else:
        grade = "C"
    if late and grade != "C":
        grade = grade + "-"
    return grade


def total(values, cap):
    result = 0
    for value in values:
        if value < 0:
            continue
        result += min(value, cap)
    return result


def valid(name):
    return bool(name) and name.isidentifier() and not name.startswith("_")


def twice(x): return x * 2


def unused(flag):
    if flag:
        return 1
    return 0
