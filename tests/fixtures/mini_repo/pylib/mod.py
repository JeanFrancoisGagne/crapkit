def guarded(a, b):
    if a and b:
        return [x for x in range(a) if x % 2]
    return []
