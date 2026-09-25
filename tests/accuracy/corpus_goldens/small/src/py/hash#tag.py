"""A path with a # in it."""


def tagged(tags, wanted):
    if not tags:
        return False
    for tag in tags:
        if tag == wanted:
            return True
    return False
