Microdata extraction now raises :exc:`RecursionError` past fixed limits on items and properties shared through
``itemref``, instead of spending exponential or quadratic time.
