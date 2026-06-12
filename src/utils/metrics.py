from __future__ import annotations

import re


def parse_metric_count(value: str | None) -> int | None:
    """Parse social metric counts such as 1.2K, 3.5M, or 12 comments."""
    if value is None:
        return None

    text = value.strip()
    if not text:
        return None

    match = re.search(
        r"(\d+(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(\s*[KkMmBb\u4e07\u4ebf\u5104])?",
        text,
    )
    if match is None:
        return None

    number_text = match.group(1).replace(",", "")
    suffix = (match.group(2) or "").strip().lower()

    try:
        number = float(number_text)
    except ValueError:
        return None

    multiplier = {
        "": 1,
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "\u4e07": 10_000,
        "\u4ebf": 100_000_000,
        "\u5104": 100_000_000,
    }.get(suffix)
    if multiplier is None:
        return None

    return int(number * multiplier)
