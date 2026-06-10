"""turbohtml: fast, typed HTML utilities powered by a C-accelerated core."""

from __future__ import annotations

from importlib.metadata import version

from ._html import Token, Tokenizer, TokenType, escape, tokenize, unescape

__version__ = version("turbohtml")
"""The installed package version."""

__all__ = [
    "Token",
    "TokenType",
    "Tokenizer",
    "__version__",
    "escape",
    "tokenize",
    "unescape",
]
