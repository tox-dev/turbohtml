Keep the root element on the parser's stack of open elements, which malformed markup could empty, and stop an SVG or
MathML ``template`` from counting as an open HTML template for ``html``, ``body`` and ``form`` tags. Fragment parsing in
an SVG or MathML context no longer takes the context element for an HTML element of the same name.
