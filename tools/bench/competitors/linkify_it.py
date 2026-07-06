"""linkify-it-py: the pure-Python link scanner markdown-it-py pulls in (finds, does not rewrite)."""

from __future__ import annotations

from linkify_it import LinkifyIt

REQUIREMENTS = ("linkify-it-py>=2.0.3",)

_LINKIFY = LinkifyIt()


def detect(case: tuple[str, str]) -> None:
    """Scan plain text for links with linkify-it-py: match for the spans or test for any link."""
    kind, text = case
    if kind == "find":
        _LINKIFY.match(text)
    else:
        _LINKIFY.test(text)


# linkify-it-py only finds link spans in plain text; it never rewrites HTML, so it maps to detect, not the parse-and-
# rewrite linkify op -- comparing its plain-text scan against turbohtml's full linkify would not be like-for-like.
OPERATIONS = {"detect": (detect, "linkify-it-py")}
