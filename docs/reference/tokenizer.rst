###########
 Tokenizer
###########

.. currentmodule:: turbohtml

The low-level WHATWG tokenization surface, below tree construction. :func:`tokenize` runs a whole string at once;
:class:`Tokenizer` streams chunks. Both yield :class:`Token` objects, whose :class:`TokenType` selects which attributes
are meaningful.

.. autofunction:: tokenize

.. autoclass:: Tokenizer
    :members:

.. autoclass:: Token
    :members:

.. autoclass:: TokenType
    :members:
    :undoc-members:
