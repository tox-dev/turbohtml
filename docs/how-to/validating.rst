################################
 Validate against an XML schema
################################

.. currentmodule:: turbohtml.validate

Parse the XML with :func:`turbohtml.parse_xml`, compile the schema once, then validate. :class:`XMLSchema` takes XSD 1.0
text (or a parsed schema document) and :class:`RelaxNG` takes RELAX NG XML syntax; both expose the same
:meth:`~XMLSchema.validate`, :meth:`~XMLSchema.is_valid`, and :meth:`~XMLSchema.assert_valid` surface.

*****************
 Get every error
*****************

:meth:`~XMLSchema.validate` returns a :class:`ValidationResult`. It is truthy when the document is valid, and its
``errors`` tuple holds one :class:`ValidationError` per violation -- each with a ``message``, the ``/root/child``
document ``path`` that located it, a ``line`` (``0`` when the source carries no positions), and a coarse ``type`` of
``"structure"``, ``"datatype"``, or ``"facet"``:

.. testcode::

    from turbohtml import parse_xml
    from turbohtml.validate import XMLSchema

    schema = XMLSchema(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="book"><xs:complexType>'
        '<xs:sequence><xs:element name="title" type="xs:string"/></xs:sequence>'
        '<xs:attribute name="isbn" type="xs:string" use="required"/>'
        "</xs:complexType></xs:element></xs:schema>"
    )
    result = schema.validate(parse_xml("<book><title>One</title></book>"))
    print(result.valid)
    for error in result.errors:
        print(error.type, error.path, "--", error.message)

.. testoutput::

    False
    structure /book -- required attribute 'isbn' is missing

***********
 Fail fast
***********

When you only care whether the document conforms, :meth:`~XMLSchema.is_valid` returns the bool, and
:meth:`~XMLSchema.assert_valid` raises :class:`SchemaValidationError` (carrying the errors) on the first invalid
document, so it drops into a pipeline that expects an exception:

.. testcode::

    from turbohtml import parse_xml
    from turbohtml.validate import RelaxNG, SchemaValidationError

    schema = RelaxNG(
        '<element name="note" xmlns="http://relaxng.org/ns/structure/1.0">'
        '<oneOrMore><element name="line"><text/></element></oneOrMore></element>'
    )
    print(schema.is_valid(parse_xml("<note><line>hi</line></note>")))
    try:
        schema.assert_valid(parse_xml("<note/>"))
    except SchemaValidationError as error:
        print("rejected:", error.errors[0].path)

.. testoutput::

    True
    rejected: /note
