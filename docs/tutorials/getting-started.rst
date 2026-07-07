#################
 Getting started
#################

Go from an empty environment to escaping and unescaping your first HTML.

Install turbohtml from PyPI:

.. code-block:: console

    $ pip install turbohtml

Open a Python prompt and escape some text for safe inclusion in an HTML page:

.. testcode::

    import turbohtml

    print(turbohtml.escape("5 > 3 & 2 < 4"))

.. testoutput::

    5 &gt; 3 &amp; 2 &lt; 4

By default ``escape`` escapes quotation marks too, which you want inside an attribute value:

.. testcode::

    print(turbohtml.escape('name="O\'Brien"'))

.. testoutput::

    name=&quot;O&#x27;Brien&quot;

Reverse the process: turn HTML character references back into text:

.. testcode::

    print(turbohtml.unescape("Tom &amp; Jerry, caf&eacute;"))

.. testoutput::

    Tom & Jerry, café

Stay with the string helpers below, or continue to :doc:`tokenizing` to break whole documents into tokens.

********************
 Linkify plain text
********************

One more string-in, string-out helper rounds out the getting-started toolkit: :func:`turbohtml.clean.linkify` finds the
URLs in a run of text and wraps each one in an anchor, leaving the surrounding characters untouched. It is the quickest
way to turn a plain message into clickable HTML:

.. testcode::

    from turbohtml.clean import linkify

    print(linkify("Visit https://example.com today"))

.. testoutput::

    Visit <a href="https://example.com" rel="nofollow">https://example.com</a> today

Every generated link carries ``rel="nofollow"`` by default, so untrusted text stays safe to publish.

************************
 Normalize Unicode text
************************

One more string helper cleans up Unicode itself. The same character can be typed as one code point or as a base letter
plus a combining mark, so ``"café"`` need not equal ``"café"`` even though they look identical.
:func:`turbohtml.detect.normalize` folds text to a Unicode normalization form, so the two compare equal:

.. testcode::

    from turbohtml.detect import normalize

    composed = "café"  # e-acute as one code point
    decomposed = "café"  # plain e followed by a combining acute accent
    print(composed == decomposed)
    print(normalize("NFC", composed) == normalize("NFC", decomposed))

.. testoutput::

    False
    True

Reach for ``NFC`` before you compare or store text; the :doc:`/how-to/encoding` guide covers the other three forms. With
the string helpers in hand, continue to :doc:`tokenizing` to break whole documents into tokens.
