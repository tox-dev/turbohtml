#########################
 The node and tree model
#########################

Every node in a parsed document is one of a small sealed set of types, all sharing the navigation defined on
:class:`~turbohtml.Node`. The purple base carries the shared traversal; the green leaves are the concrete node types a
tree is built from.

.. mermaid::

    graph TD
        Node["Node<br/>(shared navigation)"]
        Node --> Document[Document]
        Node --> Element[Element]
        Node --> Text[Text]
        Node --> Comment[Comment]
        Node --> CData[CData]
        Node --> PI[ProcessingInstruction]
        Node --> Doctype[Doctype]

        classDef base fill:#ede7f6,stroke:#4527a0,color:#311b92
        classDef leaf fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20

        class Node base
        class Document,Element,Text,Comment,CData,PI,Doctype leaf

The node types are a small sealed hierarchy (:class:`~turbohtml.Document`, :class:`~turbohtml.Element`,
:class:`~turbohtml.Text`, :class:`~turbohtml.Comment`, :class:`~turbohtml.Doctype`,
:class:`~turbohtml.ProcessingInstruction`, :class:`~turbohtml.CData`) sharing the navigation defined on
:class:`~turbohtml.Node`. turbohtml models text as real child nodes (the WHATWG DOM shape) rather than the
``.text``/``.tail`` split lxml-style trees use, so a node's children are its text runs and elements interleaved in
document order. Each type sets ``__match_args__``, so structural pattern matching unpacks a node's defining field, and
node equality is identity over the underlying arena node, so two wrappers for the same element compare equal and hash
alike.
