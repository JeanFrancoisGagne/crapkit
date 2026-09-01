"""Grade a scored row."""

BANDS = (("A", 90), ("B", 80), ("C", 70), ("D", 60))


def grade(score):
    """The band letter for one score."""
    for letter, floor in BANDS:
        if score >= floor:
            return letter
    return "F"


def summarize(rows):
    """How many rows landed in each band."""
    counts = {}
    for row in rows:
        letter = grade(row["score"])
        counts[letter] = counts.get(letter, 0) + 1
    return counts
