"""Lines coverage.py excludes: `# pragma: no cover` on a branch and on a whole def."""


def safe_ratio(covered, total):
    if total == 0:  # pragma: no cover
        return 0.0
    if covered > total:
        covered = total
    return covered / total


def debug_dump(value):  # pragma: no cover
    if value:
        print(value)
    return value


def platform_name(system):
    if system == "win32":
        return "windows"
    elif system == "darwin":  # pragma: no cover
        return "macos"
    return "linux"
