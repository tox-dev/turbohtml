``minify_js()`` writes ``===`` and ``!==`` as ``==`` and ``!=`` whenever both operands are certain to share a type, not
only for ``typeof``, ``!`` and ``void``: ``+a!==+a``, ``1/+a===1/b``, ``""+a===b+""``, ``a>>>0===b>>>0`` and ``delete
a.b===!c`` all lose a byte.
