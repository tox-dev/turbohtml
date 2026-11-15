``parse(bytes)`` and ``detect()`` no longer cut a ``<meta>`` name or value at a NUL byte, so a label containing one no
longer selects an encoding.
