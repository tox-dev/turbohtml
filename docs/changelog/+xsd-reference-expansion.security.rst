:class:`~turbohtml.validate.XMLSchema` now raises :class:`ValueError` for an XSD schema whose references expand to more
than 20,000 particles and attributes, instead of exponential validation time and memory.
