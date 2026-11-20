``minify_css()`` writes ``transform-origin`` and ``perspective-origin`` keywords as offsets, drops a zero z offset,
writes a centered position as ``top`` or ``bottom``, drops ``ease`` from a ``transition`` layer that names its property
first, and drops the initial ``outside`` and ``disc`` from ``list-style``. animate.css comes out 212 bytes smaller and
foundation.css 121 bytes.
