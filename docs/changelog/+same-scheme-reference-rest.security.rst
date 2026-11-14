A same-scheme reference with an encoded dot segment now resolves as a path under the base URL when its remainder has a
colon in the first path segment, as the WHATWG URL standard specifies. Such references no longer use memory that grows
quadratically with their length in ``Document.base_url()``, ``meta_refresh()`` and link and structured-data extraction.
