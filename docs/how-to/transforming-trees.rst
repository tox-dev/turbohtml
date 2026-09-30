###############################
 Clean a subtree before export
###############################

Use DOM transformations when a later traversal must read cleaned text. If you only need smaller HTML output, use
:doc:`minifying` instead. The :doc:`/tutorials/cleaning` tutorial works through a fragment from start to finish.

**************************************
 Preserve the original while cleaning
**************************************

Start with ``sanitize_node`` when you need a sanitized copy. Use the relaxed policy to retain paragraph markup. Retain
the return value: later steps receive that copy, while the original remains available to the caller.

.. testcode::

    from typing import Final

    from turbohtml import parse_fragment
    from turbohtml.clean import Policy, Sanitizer, collapse_whitespace_node, transform_node

    original: Final = parse_fragment("<p>Hello   <b>world</b></p><!--editor-->")
    sanitizer: Final = Sanitizer(Policy.relaxed())
    cleaned: Final = transform_node(original, sanitizer.sanitize_node, collapse_whitespace_node)
    print(cleaned.inner_xml)
    print(original.inner_html)

.. testoutput::

    <p>Hello <b>world</b></p>
    <p>Hello   <b>world</b></p><!--editor-->

``collapse_whitespace_node``, ``strip_comments_node``, and ``linkify_node`` mutate their input. To clean an existing
tree in place, omit the copying stage. A later custom step can introduce markup that the sanitizer would reject; choose
the stage order to match your trust boundary.

******************************
 Add a configured custom step
******************************

Use a function, bound method, or callable object that accepts a ``Node``. Bind extra arguments with
``functools.partial``. A step may return a replacement root or ``None`` to keep the current root:

.. testcode::

    from functools import partial
    from turbohtml import Node
    from turbohtml.clean import strip_comments_node


    def replace_label(node: Node, *, label: str) -> None:
        for element in node.find_all("b"):
            element.text = label


    result: Final = transform_node(
        original,
        partial(replace_label, label="reader"),
        strip_comments_node,
        collapse_whitespace_node,
    )
    print(result.inner_html)

.. testoutput::

    <p>Hello <b>reader</b></p>

A step receives the root once; perform any descendant traversal inside that step. Exceptions stop the sequence and leave
earlier mutations in place. See :doc:`/explanation/tree-transformations` for ownership and execution details.

********************************
 Encode or stream just children
********************************

Pass ``inner=True`` to keep the context element out of the output while retaining its serialization rules. Consume
``serialize_iter`` to write chunks without joining a whole output string:

.. testcode::

    from io import StringIO

    paragraph: Final = result.find_all("p")[0]
    print(paragraph.encode(inner=True))
    output: Final = StringIO()
    for chunk in paragraph.serialize_iter(inner=True):
        _ = output.write(chunk)
    print(output.getvalue())

.. testoutput::

    b'Hello <b>reader</b>'
    Hello <b>reader</b>

``serialize`` and ``encode`` accept compact, indented, or minified output. ``serialize_iter`` accepts compact and
indented output; it rejects ``Minify``. Use ``inner_xml`` when the consumer requires well-formed XML fragments.

Finish or discard a ``serialize_iter`` stream before you edit the tree. An edit such as ``append`` or ``extract``, or
one that detaches the node the stream resumes at, makes the next chunk and each later one raise :exc:`RuntimeError`.
