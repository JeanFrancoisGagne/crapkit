"""f-strings whose replacement fields hold brackets, braces, quotes and format specs."""


def cell(row, key, width):
    if key not in row:
        return f"{'-':>{width}}"
    return f"{row[key]!s:>{width}}"


def label(item):
    tags = item.get("tags", [])
    if tags:
        return f"{item['name']} [{', '.join(tags)}]"
    return f"{ {'n': item['name']}['n'] }"


def nested_quotes(user):
    if user.get("admin"):
        return f"{user["name"]} (admin)"
    return f"{user.get("name", "anonymous")}"
