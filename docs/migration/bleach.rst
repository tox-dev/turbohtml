#############
 From bleach
#############

.. package-meta:: bleach mozilla/bleach

`bleach <https://github.com/mozilla/bleach>`_ was the standard Python HTML allowlist sanitizer and linkifier, built on
html5lib. Two jobs live in one library: ``bleach.clean`` strips markup down to an allowed set of tags, attributes, and
URL schemes so user-supplied HTML is safe to render, and ``bleach.linkify`` scans plain text for URLs and wraps them in
``<a>`` tags. It powered comment fields, wikis, and message bodies across the Django ecosystem for a decade.

bleach reached end of life with no maintained successor. turbohtml covers both jobs from its ``turbohtml.clean`` module:
``bleach.clean`` maps to the allowlist :class:`~turbohtml.clean.Sanitizer` (with a drop-in
:func:`turbohtml.migration.bleach.clean` shim), and ``bleach.linkify`` maps to :func:`turbohtml.clean.linkify`. Both run
their filtering in C, ship full type annotations, and take a frozen, thread-safe configuration.

*********************
 turbohtml vs bleach
*********************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - bleach
    - - Scope
      - Full WHATWG parser, serializer, sanitizer, linkifier, selectors
      - Sanitize and linkify only, over html5lib
    - - Feature breadth
      - Escape/strip/remove per tag, value-rewriting attribute filter, forced attributes, regenerable IANA TLD table
      - Allow/strip tags, bool attribute callback, ``css_sanitizer`` for style scrubbing
    - - Performance
      - Filtering and link scan in C
      - Pure-Python over html5lib
    - - Typing
      - Fully annotated, ``py.typed``
      - Untyped
    - - Dependencies
      - None (self-contained C extension)
      - html5lib, plus tinycss2 for CSS sanitizing
    - - Maintenance
      - Active
      - End of life, no successor

Feature overlap
===============

The shared surface ports one-to-one:

- ``bleach.clean(text, tags=, attributes=, protocols=, strip=, strip_comments=)`` ->
  :func:`turbohtml.migration.bleach.clean` (same signature) or a native :class:`~turbohtml.clean.Policy` +
  :class:`~turbohtml.clean.Sanitizer`.
- ``bleach.linkify(text, ...)`` and the reusable ``Linker`` -> :func:`turbohtml.clean.linkify` and
  :class:`turbohtml.clean.Linker`.
- The ``nofollow`` and ``target_blank`` callbacks and ``DEFAULT_CALLBACKS`` keep their names in :mod:`turbohtml.clean`.
- bleach's default allowed tags, attributes, and URL schemes are the turbohtml migration baseline (``DEFAULT_TAGS``,
  ``DEFAULT_ATTRIBUTES``, ``DEFAULT_SCHEMES``), so an unconfigured ``clean`` behaves the same.

What turbohtml adds
===================

- A frozen, thread-safe :class:`~turbohtml.clean.Policy`, where sharing a configured ``bleach.Cleaner`` across threads
  was a documented footgun.
- An :class:`~turbohtml.clean.OnDisallowed` enum that names escape, strip, and remove, where bleach overloaded the two
  booleans ``strip`` and ``strip_comments``.
- An ``attribute_filter`` that returns a replacement value or ``None`` to drop, where bleach's attribute callable only
  returned a bool.
- ``set_attributes`` to force attributes (for example ``rel="noopener"``) onto every kept instance of a tag, which
  bleach could only approximate through a linkify callback.
- A safety baseline that removes ``<script>``, ``on*`` handlers, and ``javascript:`` URLs by construction, so no policy
  can re-admit them.
- An IANA TLD table you can regenerate and extend with ``Linkify.extra_tlds``, where bleach shipped a frozen list.
- Idempotent linkifying: an existing ``<a>`` is left untouched unless you opt in with ``Linkify.process_existing``.

What bleach has that turbohtml does not
=======================================

- A pluggable html5lib filter pipeline (``bleach.sanitizer.Cleaner(filters=[...])``) let you insert arbitrary html5lib
  ``Filter`` classes into the clean pass. turbohtml has no equivalent filter chain; express the transform through
  ``attribute_filter``, ``set_attributes``, or a post-parse walk over the tree instead.
- A fully configurable safety baseline. bleach kept whatever you listed, including ``<script>`` if you allowed it;
  turbohtml's baseline is fixed. No equivalent, by design.

Performance
===========

The sanitizer leads bleach by about fifty times and the linkifier by five to twenty times:

.. bench-table::
    :file: bench/bleach.json

****************
 How to migrate
****************

Sanitizing
==========

The bleach-compatible shim keeps ``clean``'s signature, so the import is the only change:

.. code-block:: python

    # bleach
    from bleach import clean

    # turbohtml
    from turbohtml.migration.bleach import clean

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `bleach <https://bleach.readthedocs.io/>`__
      - turbohtml
    - - ``clean(text, ...)``
      - :func:`turbohtml.migration.bleach.clean` (shim) or :func:`turbohtml.clean.sanitize` (native)
    - - ``tags=``, ``attributes=``, ``protocols=``
      - ``Policy.tags``, ``Policy.attributes``, ``Policy.url_schemes`` on :class:`~turbohtml.clean.Policy`
    - - ``strip=True`` / ``strip=False``
      - :class:`~turbohtml.clean.OnDisallowed` (``STRIP`` / ``ESCAPE``)
    - - ``strip_comments=``
      - ``Policy.strip_comments``
    - - ``css_sanitizer=``
      - ``Policy.css_properties`` (scrubs both the ``style`` attribute and, when ``style`` is allowed, the ``<style>``
        element body)

``clean(text, tags=..., attributes=..., protocols=..., strip=..., strip_comments=...)`` maps onto a
:class:`~turbohtml.clean.Policy`. ``attributes`` accepts bleach's list, per-tag dict, or callable forms; ``strip``
chooses between dropping a disallowed tag and keeping its children (``True``) and escaping it (``False``, the default):

.. testcode::

    from turbohtml.migration.bleach import clean

    print(clean("<p>Hi <a href='http://x'>link</a></p><script>evil()</script>"))

.. testoutput::

    &lt;p&gt;Hi <a href="http://x">link</a>&lt;/p&gt;&lt;script&gt;evil()&lt;/script&gt;

For new code prefer the native :class:`~turbohtml.clean.Policy`/:class:`~turbohtml.clean.Sanitizer` API: a frozen,
thread-safe policy, an :class:`~turbohtml.clean.OnDisallowed` enum that names escape, strip, and remove where bleach
overloaded two booleans, and an ``attribute_filter`` that rewrites or drops a value where bleach's callable only
returned a bool.

Linkifying
==========

The entry points keep bleach's names, so the import changes and the common case is identical:

.. code-block:: python

    # bleach
    from bleach import linkify
    from bleach.linkifier import Linker, DEFAULT_CALLBACKS
    from bleach.callbacks import nofollow, target_blank

    # turbohtml
    from turbohtml.clean import linkify, Linker, Linkify, DEFAULT_CALLBACKS, nofollow, target_blank

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `bleach <https://bleach.readthedocs.io/>`__
      - turbohtml
    - - ``linkify(text, ...)``
      - :func:`turbohtml.clean.linkify`
    - - ``Linker(...)``
      - :class:`turbohtml.clean.Linker`
    - - ``DEFAULT_CALLBACKS``
      - ``DEFAULT_CALLBACKS``
    - - ``nofollow``, ``target_blank``
      - :func:`turbohtml.clean.nofollow`, :func:`turbohtml.clean.target_blank`
    - - callback ``(attrs, new)`` with ``(namespace, name)`` keys
      - a single :class:`~turbohtml.clean.LinkCandidate` (``url``, ``text``, ``attrs``)
    - - ``new`` flag
      - ``LinkCandidate.existing`` (inverted)
    - - ``protocols=``
      - ``Linkify.schemes``

``linkify(text, Linkify(callbacks=..., skip_tags=..., parse_email=...))``, the reusable
:class:`~turbohtml.clean.Linker`, and the ``nofollow``/``target_blank`` defaults work as before: the six knobs are now
fields of a frozen :class:`~turbohtml.clean.Linkify` config. Only custom callbacks change shape. bleach passed ``(attrs,
new)`` where ``attrs`` was keyed by ``(namespace, name)`` tuples with a ``"_text"`` pseudo-key for the text; turbohtml
passes a single :class:`~turbohtml.clean.LinkCandidate` with plain ``url``, ``text``, and ``attrs`` (a ``dict[str,
str]``), and a callback returns it to keep the link or ``None`` to leave the text bare. bleach's ``new`` flag becomes
``LinkCandidate.existing`` (inverted: ``new=True`` is ``existing=False``). Porting a callback means reading fields
instead of tuple keys:

.. testcode::

    from turbohtml.clean import LinkCandidate, Linkify, linkify


    def shorten(link: LinkCandidate) -> LinkCandidate | None:
        link.text = link.url.removeprefix("https://").removeprefix("http://")
        return link


    print(linkify("read https://example.com/page", Linkify(callbacks=[shorten])))

.. testoutput::

    read <a href="https://example.com/page">example.com/page</a>

bleach's ``protocols`` maps to the ``Linkify.schemes`` field, which restricts the explicit URL schemes that autolink,
and bleach's custom-TLD support maps to ``Linkify.extra_tlds``, on top of a current IANA table you can regenerate where
bleach shipped a frozen list. A bare domain such as ``example.com`` still links only when its last label is a known TLD.

**********************
 Gotchas and pitfalls
**********************

- turbohtml's safety baseline (``<script>``, ``on*`` handlers, ``javascript:`` URLs) is not configurable, so even a
  permissive ``attributes`` callable cannot re-admit them, where bleach faithfully kept whatever you allowed.
- The bleach-compatible ``clean`` shim does not take a ``css_sanitizer`` object; passing one raises
  ``NotImplementedError``. Configure CSS scrubbing through ``Policy.css_properties`` on the native
  :func:`~turbohtml.clean.sanitize` instead: it vets a kept ``style`` attribute and, when ``style`` is in
  ``Policy.tags``, the ``<style>`` element body against the same property allowlist, dropping unsafe declarations while
  keeping selectors and block nesting.
- turbohtml leaves an existing ``<a>`` untouched so linkifying is idempotent, where bleach always reprocessed present
  links. Opt back in with the ``Linkify.process_existing`` field to run the callbacks over author-written anchors too
  (the callback reads ``LinkCandidate.existing`` to branch).
- Linkify callbacks read :class:`~turbohtml.clean.LinkCandidate` fields (``url``, ``text``, ``attrs``), not bleach's
  ``(namespace, name)`` tuple keys or the ``"_text"`` pseudo-key; a straight copy of a bleach callback will not run.
