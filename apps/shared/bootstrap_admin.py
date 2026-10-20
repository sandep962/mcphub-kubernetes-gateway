"""Create one initial administrator key and print it once to stdout."""

from .database import bootstrap_admin


if __name__ == "__main__":
    print(bootstrap_admin())
