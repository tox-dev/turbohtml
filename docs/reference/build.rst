#######
 Build
#######

.. module:: turbohtml.build

Construct HTML trees in code, the way ``lxml.builder.E`` does. The builder is a thin layer over
:class:`~turbohtml.Element` and :meth:`~turbohtml.Node.serialize`: ``E.<tag>(attrs, *children)`` builds a real element,
folds a leading mapping into its attributes, and appends each remaining argument as a child -- a string becomes a
:class:`~turbohtml.Text` node, and any node is appended as-is. The result is an ordinary turbohtml tree, so it queries,
edits, and serializes like a parsed one.

.. autodata:: E
    :no-value:

.. autoclass:: ElementMaker
    :members:

.. autofunction:: document
