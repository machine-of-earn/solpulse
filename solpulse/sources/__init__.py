"""Data sources. Each module exposes `collect(...) -> dict` and raises on failure.

Sources never return partial or estimated data. The snapshot layer catches the
exception and records the source as `error`, so a missing metric stays visibly
missing rather than silently becoming a plausible-looking zero.
"""
