``Element.form_data()`` now allocates the field name once per ``<select multiple>`` instead of once per selected option.
Before, the memory of the result grew with the product of the name length and the number of selected options.
