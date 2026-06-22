#######################
 From bleach (linkify)
#######################

`bleach <https://github.com/mozilla/bleach>`_ is end of life and has no successor for its linkifier, so
``turbohtml.linkify`` takes its place. The entry points keep bleach's names, so the import changes and the common case
is identical:

.. code-block:: python

    # bleach
    from bleach import linkify
    from bleach.linkifier import Linker, DEFAULT_CALLBACKS
    from bleach.callbacks import nofollow, target_blank

    # turbohtml
    from turbohtml.linkify import linkify, Linker, DEFAULT_CALLBACKS, nofollow, target_blank

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

One default differs from bleach, deliberately: turbohtml leaves an existing ``<a>`` untouched so linkifying is
idempotent, where bleach always reprocessed present links. Opt back in with ``process_existing=True`` to run the
callbacks over author-written anchors too (the callback reads ``link.existing`` to branch). bleach's ``protocols`` maps
to ``schemes``, which restricts the explicit URL schemes that autolink, and bleach's custom-TLD support maps to
``extra_tlds``, on top of a current IANA table you can regenerate where bleach shipped a frozen list. A bare domain such
as ``example.com`` still links only when its last label is a known TLD. The scan for link candidates runs in C, so
linkifying a page is faster than bleach's html5lib-based pass.
