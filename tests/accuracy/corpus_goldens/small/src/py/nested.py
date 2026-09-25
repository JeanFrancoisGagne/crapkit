"""Three-deep nested functions, each with branches of its own, and module code after a def."""


def outer(rows, limit):
    def middle(row):
        def inner(cell):
            if cell is None:
                return 0
            if cell > limit:
                return limit
            return cell
        if not row:
            return []
        return [inner(cell) for cell in row]
    kept = []
    for row in rows:
        if row is None:
            continue
        kept.append(middle(row))
    return kept


def unused_branchy(flag, other):
    if flag and other:
        return 2
    if flag or other:
        return 1
    return 0


DEFAULT_LIMIT = 10
TABLE = outer([[1, 20, None]], DEFAULT_LIMIT)
