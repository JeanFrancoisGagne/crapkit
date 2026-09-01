"""Read the ledger's own text format into rows."""


def coerce(word):
    """A number when the word is one, the stripped word otherwise."""
    text = word.strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    if text.count(".") == 1 and text.replace(".", "").isdigit():
        return float(text)
    return text


def read_row(line, cols):
    """One line to one dict, or None when the line is not a row."""
    if not line or line.startswith("#"):
        return None
    parts = [coerce(word) for word in line.split(",")]
    if len(parts) != len(cols):
        raise ValueError("expected {} cols, got {}".format(len(cols), len(parts)))
    row = dict(zip(cols, parts))
    if row.get("score") is None:
        row["score"] = 0
    return row


def parse(text, cols):
    """Every row in the text, skipping blanks and comments."""
    rows = []
    for line in text.splitlines():
        row = read_row(line.strip(), cols)
        if row is not None:
            rows.append(row)
    return rows
