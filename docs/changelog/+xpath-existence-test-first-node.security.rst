An XPath location path that is only tested for emptiness, as a predicate, an operand of ``and`` or ``or``, an argument
of ``not()`` or ``boolean()``, or the ``test`` of ``xsl:if`` or ``xsl:when``, now stops at the first node it selects.
Before, each candidate collected the whole axis, so evaluation time grew quadratically with the length of a sibling
list.
