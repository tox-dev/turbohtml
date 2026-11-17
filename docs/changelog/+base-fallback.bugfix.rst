``base_url()`` now normalizes its ``fallback`` as the URL standard parses it, both when the document has no
``<base href>`` and when a ``<base href>`` resolves against it, so ``http:site.example`` reads as
``http://site.example/`` and ``HTTP://Site.Example`` as ``http://site.example/``. A fallback that does not
parse is still returned as written.
