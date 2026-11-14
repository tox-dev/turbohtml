``Node.encode`` with ``Html(meta_charset=True)`` no longer declares a codec that no WHATWG encoding label names, such as
``iso2022_jp_2``, ``hz``, or UTF-16, which browsers could not read back; it raises ``ValueError`` instead.
