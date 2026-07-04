##########################
 Escape and unescape text
##########################

Escape text for HTML output and reverse it with :func:`turbohtml.escape` and :func:`turbohtml.unescape` -- the standard
library's behavior, byte for byte, several times faster.

***************************************
 Escape untrusted text for HTML output
***************************************

When you interpolate user-supplied text into HTML, escape it first so it cannot break out of its context:

.. testcode::

    import turbohtml

    comment = '<script>alert("xss")</script>'
    print(f"<p>{turbohtml.escape(comment)}</p>")

.. testoutput::

    <p>&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;</p>

************************************************
 Escape for a text node without touching quotes
************************************************

Inside element text (not an attribute) the quote characters are safe, so pass ``quote=False`` to leave them untouched
and keep the output smaller:

.. testcode::

    print(turbohtml.escape('He said "hi" & left', quote=False))

.. testoutput::

    He said "hi" &amp; left

****************************************
 Build safe HTML strings for a template
****************************************

When you assemble HTML from a mix of trusted markup and untrusted values, use :mod:`turbohtml.migration.markupsafe`.
Wrapping a value in :class:`~turbohtml.migration.markupsafe.Markup` declares it safe; combining it with plain text
escapes that text, so a forgotten escape cannot inject markup. It is a drop-in for `markupsafe
<https://markupsafe.palletsprojects.com>`_, so a `Jinja2 <https://jinja.palletsprojects.com>`_ project migrates by
changing the import:

.. testcode::

    from turbohtml.migration.markupsafe import Markup, escape

    user = "<script>alert(1)</script>"
    row = Markup("<li>{}</li>").format(user)
    print(row)
    print(Markup(", ").join(["<b>", escape("a & b")]))

.. testoutput::

    <li>&lt;script&gt;alert(1)&lt;/script&gt;</li>
    &lt;b&gt;, a &amp; b

**********************************
 Decode HTML character references
**********************************

Convert named and numeric references from scraped or stored HTML back into text:

.. testcode::

    print(turbohtml.unescape("&pound;10 &copy; &#127881;"))

.. testoutput::

    £10 © 🎉

Unescaping follows the HTML5 rules, including longest-match for references that omit the trailing semicolon:

.. testcode::

    print(turbohtml.unescape("&notit;"))

.. testoutput::

    ¬it;
