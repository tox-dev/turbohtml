"""
cchardet: the C uchardet binding, via the maintained ``faust-cchardet`` drop-in (same ``import cchardet`` + API).

The original ``cchardet`` is unmaintained and no longer builds on current CPython; ``faust-cchardet`` is its actively
maintained continuation, shipping the identical module name, ``detect()`` surface, and underlying uchardet engine.
"""

from __future__ import annotations

import cchardet

REQUIREMENTS = ("faust-cchardet>=2.1.19",)


def encoding(data: bytes) -> None:
    """Detect a byte stream's encoding with the cchardet/uchardet C engine."""
    cchardet.detect(data)


OPERATIONS = {"encoding": (encoding, "cchardet")}
