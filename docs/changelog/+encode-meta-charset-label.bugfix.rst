``Node.encode`` with ``Html(meta_charset=True)`` now declares the WHATWG name of the encoding it writes, so
``iso2022_jp`` declares ``ISO-2022-JP`` where it used to declare a codec name browsers ignore. A codec no WHATWG label
names, such as ``iso2022_jp_2`` or ``utf-16``, raises ``ValueError`` under ``meta_charset``.
