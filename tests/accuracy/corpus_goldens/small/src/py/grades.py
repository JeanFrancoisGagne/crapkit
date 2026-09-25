"""Grades: one function per remedy, so every label has a row.

ok, add-tests, decompose and split-lines each come from one function here, at
the corpus ceiling of 6.
"""


def letter(score):
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    return "F"


def curve(scores, mode, floor, ceiling, skip_none):
    result = []
    for score in scores:
        if score is None and skip_none:
            continue
        if score is None:
            score = floor
        if mode == "sqrt":
            score = int((score / 100) ** 0.5 * 100)
        elif mode == "flat":
            score = score + 5
        if score > ceiling:
            score = ceiling
        result.append(score)
    return result


def weighted(values, weights, cap):
    total = 0
    for value, weight in zip(values, weights):
        if value < 0:
            continue
        if weight > cap:
            weight = cap
        total += value * weight
    return total


def pick(a, b): return a if a > b else (b if b > 0 else 0)


def spread(values):
    if not values:
        return 0
    return max(values) - min(values)
