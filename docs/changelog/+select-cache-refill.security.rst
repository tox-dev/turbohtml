Parsing a ``select`` with misnested formatting elements and ``option`` elements between them no longer takes time
quadratic in the number of nodes under that ``select``. When a ``select`` holds more than sixteen enabled
``selectedcontent`` elements, the clones go to the first sixteen targets in the order the select lists them, which can
differ from the order used before.
