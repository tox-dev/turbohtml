Raise ``UnicodeEncodeError`` when ``Node.encode`` would write U+000E, U+000F or U+001B under an ISO-2022 codec such as
``iso-2022-jp``. Those characters switch the state of a reader's decoder, so markup holding one could be read
differently from the markup that was serialized; ``etree.tostring`` writes the three as character references.
