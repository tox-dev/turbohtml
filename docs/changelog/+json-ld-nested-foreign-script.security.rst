Foreign ``<script>`` and ``<style>`` elements inside ``<svg>`` or ``<math>``, and HTML ``<script>`` and ``<style>``
elements that were given element children through the DOM API, now supply only their own text children to
:meth:`~turbohtml.Document.json_ld`, :meth:`~turbohtml.Document.structured_data`, :func:`~turbohtml.extract.dates` and
``computed_style``. Text inside an element child, or inside a nested one, no longer joins the outer block or sheet.
