"""
The JavaScript-minifier options object and its registration with the C core.

``JSMinify`` lives here, apart from :mod:`turbohtml._minify`, because handing the type to the
extension (``_register_js_minify``) puts it in the C module state, and through it the whole
defining module. ``_minify`` pulls in the render layer and module-level default instances; that
reference graph keeps the extension's module state alive at interpreter shutdown, so its teardown
(and the freelist drain it runs) never executes. Registering from this module, which imports only
``_html``, keeps the graph small enough to collect, the way :mod:`turbohtml._article` and
:mod:`turbohtml._structured_data` register their record types.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._html import _register_js_minify

__all__ = ["JSMinify"]


@dataclass(frozen=True, slots=True)
class JSMinify:
    """
    Which optional JavaScript-minifier passes to run.

    Whitespace, comment and numeric-literal minification is unconditional. ``mangle``
    renames local bindings to short names (the bulk of the size win) and ``fold`` runs
    constant folding and dead-code elimination; turning either off keeps that aspect of
    the source readable (e.g. ``mangle=False`` for debuggable output).

    :param mangle: rename local bindings to short names.
    :param fold: constant-fold and eliminate dead code.
    :raises TypeError: if constructed with an unexpected or extra argument.
    """

    mangle: bool = True
    fold: bool = True


# Hand the type to the serializer so Minify(minify_js=...) reaches it with a module-state
# pointer load (speed over a per-call import), not a global; mirrors the other type registers.
_register_js_minify(JSMinify)
