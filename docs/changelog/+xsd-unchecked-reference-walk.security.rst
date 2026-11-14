Follow only the references the XSD cycle check covers when validating: ``XMLSchema.validate`` also walked a
``complexContent`` or ``simpleContent`` nested in an attribute group or a derivation, a type's second content child, and
a group reference inside an ``xs:annotation``, so a circular schema compiled and then overflowed the C stack. An
annotation in an ``xs:choice`` no longer counts as an empty branch.
