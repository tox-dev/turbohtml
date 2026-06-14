###########
 Changelog
###########

.. towncrier-draft-entries:: Unreleased

.. towncrier release notes start

*********************
 v0.2.0 (2026-06-11)
*********************

Features - 0.2.0
================

- Add a WHATWG-conformant HTML tokenizer: :func:`turbohtml.tokenize` for whole strings, the streaming
  :class:`turbohtml.Tokenizer`, and the :class:`turbohtml.Token` / :class:`turbohtml.TokenType` types. The C state
  machine is validated against the html5lib-tests tokenizer conformance suite and bulk-scans text runs the way html5ever
  does. (:issue:`6`)
- Speed up ``escape`` and ``unescape``. ``escape`` classifies one-byte strings sixteen bytes at a time with NEON or
  SSE2, or a SWAR word elsewhere (on NEON, a single low-nibble table lookup matches all five specials at once). The
  sizing pass accumulates growth without branches, the writing pass copies clean stretches in bulk and rewrites only the
  positions a match bitmask singles out, and one SWAR pass probes UCS-2 / UCS-4 text for every special character instead
  of running one ``PyUnicode_FindChar`` sweep per character (UCS-4 uses a four-lane NEON vector). ``unescape`` hops
  between ``&`` occurrences and bulk-copies the clean spans instead of routing every character through a per-code-point
  emit, keeps output staging at the input's width until a reference widens it, and resolves the entities ``html.escape``
  emits with one comparison rather than the full binary search, so unescaping escaped real HTML runs about three times
  faster than the general lookup path alone. The benchmark now uses `pyperf <https://pyperf.readthedocs.io>`_ with
  multi-MiB real documents referenced as pinned git submodules under ``tools/bench-data`` - by :user:`gaborbernat`.
  (:issue:`7`)

*********************
 v0.1.1 (2026-06-09)
*********************

Packaging updates - 0.1.1
=========================

- Publish each wheel artifact in its own job so PEP 740 attestations finish within the Sigstore signing identity's
  lifetime, fixing the ``sigstore.oidc.ExpiredIdentity`` failure that blocked the first PyPI upload - by
  :user:`gaborbernat`. (:issue:`4`)

*********************
 v0.1.0 (2026-06-09)
*********************

Features - 0.1.0
================

- Add C-accelerated :func:`turbohtml.escape` and :func:`turbohtml.unescape`, matching :func:`python:html.escape` and
  :func:`python:html.unescape` byte for byte, with free-threading support and per-interpreter wheels for CPython 3.10
  through 3.15 - by :user:`gaborbernat`. (:issue:`1`)
- Speed up ``escape`` of non-ASCII (UCS-2/UCS-4) text that needs no escaping by probing for special characters with a
  vectorized scan instead of a scalar one, making it several times faster and ahead of :func:`python:html.escape` - by
  :user:`gaborbernat`. (:issue:`3`)

Improved documentation - 0.1.0
==============================

- Document the measured ``escape``/``unescape`` speedups over the standard library in the README and the docs, add a
  reproducible benchmark behind ``tox -e bench``, and give the API reference typed signatures with intersphinx links -
  by :user:`gaborbernat`. (:issue:`2`)

Miscellaneous internal changes - 0.1.0
======================================

- Automate releases the tox-dev way: git-tag-derived versioning, a towncrier-managed changelog, and a manual
  prepare-release workflow that tags and triggers the trusted-publishing wheel build - by :user:`gaborbernat`.
  (:issue:`1`)
