"""A small, standard-library example of ASCII slug generation."""

import re


def slugify(text: str) -> str:
    """Join ASCII alphanumeric runs with hyphens and lowercase the result."""
    if not isinstance(text, str):
        raise TypeError("slugify expects a string")
    return "-".join(re.findall(r"[A-Za-z0-9]+", text)).lower()
