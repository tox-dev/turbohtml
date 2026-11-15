:class:`~turbohtml.validate.XMLSchema` now raises :class:`ValueError` for an XSD schema whose ``xs:group`` references
nest 400 levels or deeper, instead of crashing the interpreter during validation.
