"""A small, standard-library example of ASCII slug generation."""

import re


def slugify(text: str, max_length: int | None = None) -> str:
    """Join ASCII runs with hyphens, optionally limiting the result's length."""
    if not isinstance(text, str):
        raise TypeError("slugify expects a string")
    if max_length is not None and (
        isinstance(max_length, bool)
        or not isinstance(max_length, int)
        or max_length <= 0
    ):
        raise ValueError("max_length must be a positive integer")
    slug = "-".join(re.findall(r"[A-Za-z0-9]+", text)).lower()
    if max_length is not None:
        slug = slug[:max_length].rstrip("-")
    return slug
