:meth:`~turbohtml.Element.form_data` and :attr:`~turbohtml.Element.field_value` no longer rescan an ``optgroup``'s or
``fieldset``'s attributes for every ``option`` or ``legend``, which made large pages quadratic.
