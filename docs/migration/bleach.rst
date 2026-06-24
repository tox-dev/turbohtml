#############
 From bleach
#############

.. image:: https://static.pepy.tech/badge/bleach/month
    :alt: bleach monthly downloads
    :target: https://pepy.tech/project/bleach

`bleach <https://github.com/mozilla/bleach>`_ was the standard HTML allowlist sanitizer and linkifier, built on
html5lib. It is end of life with no maintained successor, so its two jobs split across turbohtml: ``bleach.clean`` maps
to ``turbohtml.sanitizer`` and ``bleach.linkify`` maps to ``turbohtml.linkify``.

***************
 Why turbohtml
***************

Both replacements are fully type annotated, run the filtering and link scan in C, and expose a frozen, thread-safe
configuration where ``bleach.clean`` had a documented thread-safety footgun. The sanitizer leads bleach by an order of
magnitude and the linkifier by five to twenty times:

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - input
      - turbohtml
      - bleach
    - - sanitize comment (1 link, 1 script)
      - 1.5 µs
      - 78.1 µs (52.6x)
    - - sanitize post (4 KiB)
      - 42.1 µs
      - 1921 µs (45.7x)
    - - linkify prose (1 KiB)
      - 51 µs
      - 272 µs (5.4x)
    - - linkify markup (4 KiB)
      - 127 µs
      - 1562 µs (12.3x)

**************
 bleach.clean
**************

The bleach-compatible shim keeps ``clean``'s signature so the import is the only change:

.. code-block:: python

    # bleach
    from bleach import clean

    # turbohtml
    from turbohtml.migration.bleach import clean

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - bleach
      - turbohtml
    - - ``clean(text, ...)``
      - :func:`turbohtml.migration.bleach.clean` (shim) or :func:`turbohtml.sanitizer.sanitize` (native)
    - - ``tags=``, ``attributes=``, ``protocols=``
      - ``Policy.tags``, ``Policy.attributes``, ``Policy.url_schemes`` on :class:`~turbohtml.sanitizer.Policy`
    - - ``strip=True`` / ``strip=False``
      - :class:`~turbohtml.sanitizer.OnDisallowed` (``STRIP`` / ``ESCAPE``)
    - - ``strip_comments=``
      - ``Policy.strip_comments``
    - - ``css_sanitizer=``
      - ``Policy.css_properties``

``clean(text, tags=..., attributes=..., protocols=..., strip=..., strip_comments=...)`` maps onto a
:class:`~turbohtml.sanitizer.Policy`. ``attributes`` accepts bleach's list, per-tag dict, or callable forms; ``strip``
chooses between dropping a disallowed tag and keeping its children (``True``) and escaping it (``False``, the default):

.. testcode::

    from turbohtml.migration.bleach import clean

    print(clean("<p>Hi <a href='http://x'>link</a></p><script>evil()</script>"))

.. testoutput::

    &lt;p&gt;Hi <a href="http://x">link</a>&lt;/p&gt;&lt;script&gt;evil()&lt;/script&gt;

For new code prefer the native :class:`~turbohtml.sanitizer.Policy`/:class:`~turbohtml.sanitizer.Sanitizer` API: a
frozen, thread-safe policy, an :class:`~turbohtml.sanitizer.OnDisallowed` enum that names escape/strip/remove where
bleach overloaded two booleans, and an ``attribute_filter`` that rewrites or drops a value where bleach's callable only
returned a bool.

Pitfalls
========

- turbohtml's safety baseline (``<script>``, ``on*`` handlers, ``javascript:`` URLs) is not configurable, so even a
  permissive ``attributes`` callable cannot re-admit them, where bleach faithfully kept whatever you allowed.
- The bleach-compatible ``clean`` shim does not yet take a ``css_sanitizer`` argument (it raises), and ``<style>``
  element contents are dropped rather than scrubbed; the native sanitizer scrubs a kept ``style`` *attribute* against
  ``Policy.css_properties``.

****************
 bleach.linkify
****************

The entry points keep bleach's names, so the import changes and the common case is identical:

.. code-block:: python

    # bleach
    from bleach import linkify
    from bleach.linkifier import Linker, DEFAULT_CALLBACKS
    from bleach.callbacks import nofollow, target_blank

    # turbohtml
    from turbohtml.linkify import linkify, Linker, DEFAULT_CALLBACKS, nofollow, target_blank

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - bleach
      - turbohtml
    - - ``linkify(text, ...)``
      - :func:`turbohtml.linkify.linkify`
    - - ``Linker(...)``
      - :class:`turbohtml.linkify.Linker`
    - - ``DEFAULT_CALLBACKS``
      - ``DEFAULT_CALLBACKS``
    - - ``nofollow``, ``target_blank``
      - :func:`turbohtml.linkify.nofollow`, :func:`turbohtml.linkify.target_blank`
    - - callback ``(attrs, new)`` with ``(namespace, name)`` keys
      - a single :class:`~turbohtml.linkify.Link` (``url``, ``text``, ``attrs``)
    - - ``new`` flag
      - ``Link.existing`` (inverted)
    - - ``protocols=``
      - ``schemes=``

``linkify(text, callbacks=..., skip_tags=..., parse_email=...)``, the reusable :class:`~turbohtml.linkify.Linker`, and
the ``nofollow``/``target_blank`` defaults work as before. Only custom callbacks change shape. bleach passed ``(attrs,
new)`` where ``attrs`` was keyed by ``(namespace, name)`` tuples with a ``"_text"`` pseudo-key for the visible text;
turbohtml passes a single :class:`~turbohtml.linkify.Link` with plain ``url``, ``text``, and ``attrs`` (a ``dict[str,
str]``), and a callback returns it to keep the link or ``None`` to leave the text bare. bleach's ``new`` flag becomes
``Link.existing`` (inverted: ``new=True`` is ``existing=False``). Porting a callback means reading fields instead of
tuple keys:

.. testcode::

    from turbohtml.linkify import linkify, Link


    def shorten(link: Link) -> Link | None:
        link.text = link.url.removeprefix("https://").removeprefix("http://")
        return link


    print(linkify("read https://example.com/page", callbacks=[shorten]))

.. testoutput::

    read <a href="https://example.com/page">example.com/page</a>

bleach's ``protocols`` maps to ``schemes``, which restricts the explicit URL schemes that autolink, and bleach's
custom-TLD support maps to ``extra_tlds``, on top of a current IANA table you can regenerate where bleach shipped a
frozen list. A bare domain such as ``example.com`` still links only when its last label is a known TLD.

Pitfalls
========

- turbohtml leaves an existing ``<a>`` untouched so linkifying is idempotent, where bleach always reprocessed present
  links. Opt back in with ``process_existing=True`` to run the callbacks over author-written anchors too (the callback
  reads ``Link.existing`` to branch).
