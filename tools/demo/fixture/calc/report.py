"""Turn scored rows into the lines a report prints."""


def tally(rows, low, high):
    """Count rows into three bins by score."""
    bins = {"low": 0, "mid": 0, "high": 0}
    for row in rows:
        if row["score"] < low:
            bins["low"] += 1
        elif row["score"] < high:
            bins["mid"] += 1
        else:
            bins["high"] += 1
    return bins


def render(bins, width):
    """One bar per bucket, clipped to width."""
    lines = []
    for name in ("low", "mid", "high"):
        count = bins.get(name, 0)
        lines.append("{:>4} {} {}".format(name, "#" * min(count, width), count))
    return "\n".join(lines)


def banner(bins, total):
    """The one line a reader sees first."""
    if not total:
        return "no rows"
    share = bins["high"] * 100 // total
    if share > 50:
        return "most rows are high"
    if share:
        return "{}% of rows are high".format(share)
    return "nothing high"
