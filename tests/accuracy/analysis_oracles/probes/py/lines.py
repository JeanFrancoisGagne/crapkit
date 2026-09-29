def multi_line_fstring(x):
    cur = f"""a
    {x}
    b"""
    return cur


def yielded_triple():
    yield """a
    b"""
    return None


def raw_assigned():
    p = r"""a
    b"""
    return p
