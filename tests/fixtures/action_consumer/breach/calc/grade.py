def grade(score, attempts, late, bonus):
    if score > 90 and not late:
        return "A"
    if score > 80:
        return "B" if attempts < 3 else "C"
    if bonus:
        return "C"
    if late and attempts > 2:
        return "F"
    if attempts > 5 and not late:
        return "E"
    return "D"


def curve(scores, floor):
    return [max(score, floor) for score in scores]
