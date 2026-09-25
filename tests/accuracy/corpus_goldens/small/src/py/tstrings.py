"""PEP 750 template strings (Python 3.14): a t-string evaluates to a Template."""


def greeting(name, formal):
    if formal:
        template = t"Dear {name},"
    else:
        template = t"Hi {name}!"
    return template


def render(template):
    parts = []
    for item in template:
        if isinstance(item, str):
            parts.append(item)
        else:
            parts.append(str(item.value).upper())
    return "".join(parts)
