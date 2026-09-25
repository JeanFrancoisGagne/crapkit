"""A non-ASCII path, and function names outside ASCII."""


def café(orders, sugar):
    if not orders:
        return 0
    total = 0
    for order in orders:
        if sugar and order > 2:
            total += order + 1
        else:
            total += order
    return total


def naïve_mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)
