Repeatedly assigning ``Text.data``, ``Comment.data`` or the ``etree`` ``text`` and ``tail`` properties now keeps memory
proportional to the current text instead of every value assigned.
