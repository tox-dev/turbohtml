A ``select`` mirrors its shown option into at most 16 ``selectedcontent`` elements. The rest keep the content the source
gave them, so a page with many of them no longer builds a quadratic number of DOM nodes.
