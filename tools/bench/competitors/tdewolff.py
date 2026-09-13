"""tdewolff/minify: the Go ``minify`` binary, invoked as a subprocess over its CLI for both JS and CSS."""

from __future__ import annotations

import subprocess

REQUIREMENTS = ()


def _run(kind: str, source: str) -> str:
    """Pipe source through the ``minify`` CLI over stdin for the given ``--type`` and return its stdout."""
    result = subprocess.run(["minify", f"--type={kind}"], input=source, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # the CLI writes the reason to stderr; raise it so the bench records the real message, not just an exit code
        reason = next(iter(result.stderr.splitlines()), f"minify exited {result.returncode}")
        raise RuntimeError(reason.removeprefix("ERROR: "))
    return result.stdout


def minify_js(source: str) -> str:
    """Minify JavaScript with the tdewolff ``minify`` binary in js mode."""
    return _run("js", source)


def minify_css(css: str) -> str:
    """Minify a stylesheet with the tdewolff ``minify`` binary in css mode."""
    return _run("css", css)


def minify(text: str) -> str:
    """Minify an HTML document with the tdewolff ``minify`` binary in html mode."""
    return _run("html", text)


OPERATIONS = {
    "minify-js-guards": (minify_js, "tdewolff"),
    "minify-js-propagation": (minify_js, "tdewolff"),
    "minify-js-var-initialization": (minify_js, "tdewolff"),
    "minify-js-unused-declarations": (minify_js, "tdewolff"),
    "minify-js-unlink": (minify_js, "tdewolff"),
    "minify-js-single-use": (minify_js, "tdewolff"),
    "minify-js-sequences": (minify_js, "tdewolff"),
    "minify-js": (minify_js, "tdewolff"),
    "minify-js-names": (minify_js, "tdewolff"),
    "minify-css": (minify_css, "tdewolff"),
    "minify-css-merges": (minify_css, "tdewolff"),
    "minify-css-conflicts": (minify_css, "tdewolff"),
    "minify": (minify, "tdewolff"),
}
