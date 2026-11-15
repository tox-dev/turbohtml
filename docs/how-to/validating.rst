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
``"structure"``, ``"datatype"``, or ``"facet"``. A document with more than 1,000 violations lists the first 1,000:

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

***************************************
 Compose a schema across several files
***************************************

A RELAX NG schema can pull in other files with ``include`` (a grammar whose definitions merge into this one) and
``externalRef`` (a pattern used in place). A schema passed as text has no location, so pass its path as ``base_url``;
each ``href`` resolves against it, after any ``xml:base`` on the way down. Set ``include_root`` to the directory the
schema files live in so a reference cannot read anything outside it:

.. testcode::

    import tempfile
    from pathlib import Path
    from turbohtml import parse_xml
    from turbohtml.validate import RelaxNG

    with tempfile.TemporaryDirectory() as folder:
        Path(folder, "line.rng").write_text(
            '<element name="line" xmlns="http://relaxng.org/ns/structure/1.0"><text/></element>', encoding="utf-8"
        )
        main = Path(folder, "note.rng")
        main.write_text(
            '<element name="note" xmlns="http://relaxng.org/ns/structure/1.0">'
            '<oneOrMore><externalRef href="line.rng"/></oneOrMore></element>',
            encoding="utf-8",
        )
        schema = RelaxNG(main.read_text(encoding="utf-8"), base_url=str(main), include_root=folder)
    print(schema.is_valid(parse_xml("<note><line>hi</line></note>")))

.. testoutput::

    True

Compilation reads every referenced file once, so the compiled schema validates without touching the disk again. Without
``base_url``, a schema that references another file raises :class:`ValueError` instead of reading a path relative to the
working directory.
