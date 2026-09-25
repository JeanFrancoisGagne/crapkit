"""Twins: one file gives several functions the same long name.

Two classes define run(); one carries a default holding double quotes, so its
long name and handle hold a double quote. helper is defined twice at module
level, the second definition replacing the first.
"""


class Fast:
    def run(self, mode="fast"):
        if mode == "fast":
            return 1
        if mode == "slow":
            return 2
        return 0


class Slow:
    def run(self):
        return -1


def helper(values):
    return [value for value in values if value]


def helper(values):
    if not values:
        return []
    return sorted(values)
