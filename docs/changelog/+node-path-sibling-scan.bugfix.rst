Taking ``css_path()`` or ``xpath_path()`` of every element now takes time linear in the document, and anchors added by
``linkify_node()`` appear in later ``find_all()`` and ``select()`` results.
