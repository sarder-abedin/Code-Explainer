"""Command line front door."""
from shop import cart
from .pricing import total


def main():
    return total(cart.items())
