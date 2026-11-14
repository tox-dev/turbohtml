``Node.encode`` with ``Html(meta_charset=True)`` raises ``ValueError`` when the output would declare a codec that no
WHATWG encoding label names, such as ``iso2022_jp_2``, ``hz``, or UTF-16. Encode without ``meta_charset`` when the page
needs no declaration, or choose a codec the Encoding Standard names.
