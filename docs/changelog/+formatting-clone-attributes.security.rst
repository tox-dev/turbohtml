Parsing a formatting element with many attributes that the parser reopens many times now uses memory linear in the
input; past a bound, reopened copies carry no attributes.
