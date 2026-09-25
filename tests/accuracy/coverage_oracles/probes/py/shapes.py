"""Coverage probe shapes. ground_truth.tsv names each function's decision arms."""
import asyncio


def if_else(flag):
    if flag:
        return "yes"
    return "no"


def if_no_else(value):
    result = value
    if value < 0:
        result = -value
    return result


def for_loop(values):
    total = 0
    for value in values:
        total += value
    return total


def while_loop(count):
    steps = 0
    while count > 0:
        count -= 1
        steps += 1
    return steps


def match_case(command):
    match command:
        case "go":
            return 1
        case "stop":
            return 2
        case _:
            return 0


def and_or(left, right):
    return left and right or None


def comprehension_if(values):
    return [value for value in values if value > 0]


def branchless(log):
    log.append(1)
    fail(log)
    log.append(3)
    log.append(4)


def fail(log):
    raise ValueError(len(log))


def outer(flag):
    def inner(value):
        if value:
            return value
        return None
    if flag:
        return inner(flag)
    return None


async def async_if(flag):
    if flag:
        return 1
    return 0


class Box:
    def method(self, value):
        if value is None:
            return 0
        return value


def one_line(value): return value + 1


def body_on_signature(left,
                      right): return left + right


def excluded(value):  # pragma: no cover
    if value:
        return 1
    return 0


def exclude_also(value):
    if value is None:
        raise NotImplementedError("configured by exclude_also")
    return value


def run_async(flag):
    return asyncio.run(async_if(flag))
