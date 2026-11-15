Stop nested selector lists, ``:has()`` and ``:nth-child(of S)`` from multiplying match time with each nesting level, so
matching stays linear in the stylesheet and the document depth.
