A ``:has()`` whose argument is a single compound selector matched as a descendant no longer takes time quadratic in the
nesting depth of a tree built by :func:`turbohtml.parse_xml`, by copying a tree, or through the mutation API.
