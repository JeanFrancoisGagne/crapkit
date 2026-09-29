"""PEP 758 (Python 3.14): except and except* may list types without parentheses."""


def parse_number(text):
    try:
        return int(text)
    except ValueError, TypeError:
        return None


def first_float(values):
    for value in values:
        try:
            return float(value)
        except ValueError, TypeError:
            continue
        finally:
            pass
    return 0.0
