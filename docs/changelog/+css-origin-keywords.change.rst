``minify_css()`` writes ``transform-origin`` and ``perspective-origin`` keywords as offsets, drops a zero z offset,
writes a centered position as ``top`` or ``bottom``, and drops ``ease`` from a ``transition`` layer that names its
property first. animate.css comes out 212 bytes smaller and foundation.css 121 bytes.
