``Node.encode`` with ``Html(meta_charset=True)`` now declares the WHATWG name of the encoding it writes, so
``iso2022_jp`` declares ``ISO-2022-JP`` where it used to declare a codec name browsers ignore. UTF-8 is declared as
``utf-8``, the spelling ``serialize()`` uses.
