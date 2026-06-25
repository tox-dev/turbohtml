#########
 Linkify
#########

.. module:: turbohtml.linkify

Find URLs and email addresses in HTML and wrap them in ``<a>`` links, a successor to `bleach.linkify
<https://github.com/mozilla/bleach>`_. It is HTML-aware, so it never links inside an existing ``<a>``, a raw-text
element, or a caller's ``skip_tags``. A :class:`Linkify` configuration object carries the knobs: a callback receives
each generated :class:`Link` and returns it to keep the link or ``None`` to leave the text bare, ``process_existing``
runs the callbacks over ``<a>`` tags already in the input (a callback reads ``Link.existing`` to tell the two apart),
``extra_tlds`` extends bare-domain detection beyond the built-in IANA table, and ``schemes`` restricts which
explicit-scheme URLs autolink.

.. autofunction:: linkify

.. autoclass:: Linkify
    :members:

.. autoclass:: Linker
    :members: linkify

.. autoclass:: Link

.. autofunction:: nofollow

.. autofunction:: target_blank

To only *locate* links in plain text rather than rewrite HTML, use :class:`Detector`. It returns a :class:`LinkSpan` for
each match and accepts custom ``tlds`` and scheme-less ``schemes``.

.. autoclass:: Detector
    :members: find, has_link

.. autoclass:: LinkSpan
    :members:
