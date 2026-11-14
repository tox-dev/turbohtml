Parsing a ``select`` whose subtree holds many misnested formatting elements, with ``option`` elements between them, no longer
takes time quadratic in the number of nodes under that ``select``. The parser refills the select's ``selectedcontent``
cache only when a moved subtree holds an enabled ``selectedcontent`` element.
