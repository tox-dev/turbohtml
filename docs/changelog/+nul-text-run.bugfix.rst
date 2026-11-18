``parse()`` again reports ``unexpected-null-character`` for a NUL inside a long run of plain Latin-1 text on x86-64. It
dropped that error, while a streamed parse of the same string reported it.
