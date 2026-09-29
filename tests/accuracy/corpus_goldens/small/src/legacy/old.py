"""A scope no lane measures: every function reads no-lane."""


def migrate(rows, version):
    out = []
    for row in rows:
        if version < 2 and "id" not in row:
            continue
        if version < 3:
            row = dict(row, v=3)
        out.append(row)
    return out


def noop():
    return None
