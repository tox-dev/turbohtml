#########
 Convert
#########

.. module:: turbohtml.convert

Translate a CSS selector into the XPath 1.0 expression that selects the same elements. turbohtml runs both a CSS engine
and an XPath engine in one process, so the translation needs no second library: it is a successor to
`cssselect <https://github.com/scrapy/cssselect>`_, the package ``lxml``/``parsel``/``pyquery`` use for exactly this
step. The emitted XPath uses HTML semantics -- element and attribute names lower-cased, the HTML case-insensitive
attribute set compared case-insensitively -- so it matches turbohtml's own CSS engine and runs unchanged through
:meth:`turbohtml.Node.xpath`.

.. autofunction:: css_to_xpath

.. autoclass:: Translator
    :members: css_to_xpath

.. autodata:: HTMLTranslator

.. autoexception:: SelectorSyntaxError

.. autoexception:: SelectorError

The translatable subset is what XPath 1.0 expresses faithfully: type, universal, class, id, every attribute operator
(``=``, ``~=``, ``|=``, ``^=``, ``$=``, ``*=``), the descendant, child, adjacent-sibling, and general-sibling
combinators, and the structural pseudo-classes -- ``:root``, ``:empty``, ``:first-child`` / ``:last-child`` /
``:only-child``, their ``-of-type`` counterparts, the four ``:nth-*`` families, and a compound ``:not()``. A selector
the engine parses but cannot translate (a relational ``:has()``, an input-state pseudo-class such as ``:checked``,
``:lang()``, or an ``*-of-type`` with no concrete element type) raises :class:`SelectorSyntaxError`.
