"""
turbohtml.__main__: a thin ``python -m turbohtml`` front end over the public API.

Every subcommand parses argv, reads one input (a file argument or stdin), calls the matching public function, and
writes the result (stdout or ``-o``); the transforms themselves live in the C core, so this module only wires argv to
them. It gives the toolkit the command-line surface ``html2text``, ``markdownify``, ``htmlmin``, ``inscriptis``,
``minify-html``, ``charset-normalizer``, and ``courlan`` each ship and turbohtml lacked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import parse
from ._html import Minify
from .clean import minify, minify_css, minify_js, sanitize
from .detect import detect

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the ``python -m turbohtml`` command line.

    :param argv: the argument vector, excluding the program name; ``None`` reads ``sys.argv``.
    :returns: the process exit code -- 0 on success, 1 when the library rejects the input or the file cannot be read.
    """
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, LookupError) as error:
        sys.stderr.write(f"turbohtml {args.command}: {error}\n")
        return 1


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser: one subcommand per public entry point, each sharing the input/output arguments."""
    parser = argparse.ArgumentParser(prog="python -m turbohtml", description="Command-line front end for turbohtml.")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("file", nargs="?", help="input file; omit or pass - to read stdin")
    shared.add_argument("-o", "--output", metavar="FILE", help="write to this file instead of stdout")
    commands = parser.add_subparsers(dest="command", required=True)

    minify_parser = commands.add_parser("minify", parents=[shared], help="minify an HTML document")
    minify_parser.add_argument(
        "--minify-css", action="store_true", help="also minify <style> bodies and style attributes"
    )
    minify_parser.set_defaults(handler=_run_minify)

    commands.add_parser("minify-css", parents=[shared], help="minify a CSS stylesheet").set_defaults(
        handler=_run_minify_css
    )
    commands.add_parser("minify-js", parents=[shared], help="minify a JavaScript source").set_defaults(
        handler=_run_minify_js
    )
    commands.add_parser("detect", parents=[shared], help="report the character encoding of bytes").set_defaults(
        handler=_run_detect
    )
    commands.add_parser("to-markdown", parents=[shared], help="render HTML as Markdown").set_defaults(
        handler=_run_to_markdown
    )
    commands.add_parser("to-text", parents=[shared], help="render HTML as layout-aware plain text").set_defaults(
        handler=_run_to_text
    )
    commands.add_parser("sanitize", parents=[shared], help="sanitize HTML against the default policy").set_defaults(
        handler=_run_sanitize
    )
    return parser


def _run_minify(args: argparse.Namespace) -> int:
    _write(minify(_read_text(args.file), Minify(minify_css=args.minify_css)), args.output)
    return 0


def _run_minify_css(args: argparse.Namespace) -> int:
    _write(minify_css(_read_text(args.file)), args.output)
    return 0


def _run_minify_js(args: argparse.Namespace) -> int:
    _write(minify_js(_read_text(args.file)), args.output)
    return 0


def _run_detect(args: argparse.Namespace) -> int:
    if (match := detect(_read_bytes(args.file))).encoding is None:
        sys.stderr.write("turbohtml detect: no encoding detected\n")
        return 1
    _write(f"{match.encoding}\n", args.output)
    return 0


def _run_to_markdown(args: argparse.Namespace) -> int:
    _write(parse(_read_text(args.file)).to_markdown(), args.output)
    return 0


def _run_to_text(args: argparse.Namespace) -> int:
    _write(parse(_read_text(args.file)).to_text(), args.output)
    return 0


def _run_sanitize(args: argparse.Namespace) -> int:
    _write(sanitize(_read_text(args.file)), args.output)
    return 0


def _read_text(path: str | None) -> str:
    """Read text input from ``path``, or stdin when it is ``None`` or ``-``."""
    if path is None or path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _read_bytes(path: str | None) -> bytes:
    """Read binary input from ``path``, or stdin when it is ``None`` or ``-``."""
    if path is None or path == "-":
        return sys.stdin.buffer.read()
    return Path(path).read_bytes()


def _write(text: str, output: str | None) -> None:
    """Write ``text`` to ``output``, or stdout when it is ``None`` or ``-``."""
    if output is None or output == "-":
        sys.stdout.write(text)
    else:
        Path(output).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
