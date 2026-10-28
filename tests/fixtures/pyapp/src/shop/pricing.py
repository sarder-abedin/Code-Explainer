"""Price maths."""
import json  # stdlib: must not resolve to anything


def total(xs):
    if not xs:
        return 0
    return sum(x for x in xs if x > 0 and x < 100)
