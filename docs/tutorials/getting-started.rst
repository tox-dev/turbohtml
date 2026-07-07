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

Reach for ``NFC`` before you compare or store text; the :doc:`/how-to/encoding` guide covers the other three forms.

*************************
 Sanitize untrusted HTML
*************************

When the input is already HTML rather than plain text, clean it against an allowlist with
:func:`turbohtml.clean.sanitize`. A :class:`~turbohtml.clean.Policy` says what to keep; here it allows a ``<p>`` with a
``style`` attribute and, through ``allowed_styles``, keeps a ``color`` only when it is a hex value. A non-overridable
baseline still drops dangerous CSS, so the ``url(javascript:...)`` goes even though the property name is allowed:

.. testcode::

    from turbohtml.clean import sanitize, Policy

    policy = Policy(
        tags=frozenset({"p"}),
        attributes={"p": frozenset({"style"})},
        css_properties=frozenset({"color"}),
        allowed_styles={"*": {"color": [r"^#[0-9a-f]{3,6}$"]}},
    )
    print(sanitize('<p style="color: #0a0; color: url(javascript:x)">Hi</p>', policy))

.. testoutput::

    <p style="color: #0a0">Hi</p>

**************************
 Resolve a computed style
**************************

When you need the style a browser would apply rather than the raw rules, run the CSS cascade with
:func:`turbohtml.cssom.computed_style`. It reads the document's ``<style>`` sheets plus each element's inline ``style``,
orders the declarations by importance, the style attribute, specificity, and source order, then fills in inheritance and
initial values. Here ``#intro`` wins ``color`` through ``!important`` over its own inline rule, and the nested ``<em>``
inherits that color:

.. testcode::

    import turbohtml
    from turbohtml.cssom import computed_style

    doc = turbohtml.parse(
        "<html><head><style>"
        "p { color: gray } .lead { font-weight: bold } #intro { color: teal !important }"
        "</style></head><body>"
        "<p class=lead id=intro style='color: red'>Hello <em>world</em></p>"
        "</body></html>"
    )
    intro = doc.select_one("#intro")
    print(computed_style(intro)["color"], computed_style(intro)["font-weight"])
    print(computed_style(doc.select_one("em"))["color"])

.. testoutput::

    teal bold
    teal

The value is the *computed* value, not the *used* value: turbohtml runs no layout, so lengths and percentages come back
as written -- see :doc:`/explanation/cssom`.

With the string helpers in hand, continue to :doc:`tokenizing` to break whole documents into tokens.
