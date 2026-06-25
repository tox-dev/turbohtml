####################################
 Encoding detection: how it guesses
####################################

:func:`turbohtml.detect.detect` answers a narrower question than :doc:`parsing`: given only bytes, which encoding do
they decode under? It reuses the exact engine that backs ``parse(detect_encoding=True)``, so a standalone guess and a
parse agree, but it stops at the encoding rather than continuing into the tokenizer and tree builder.

********************
 The decision order
********************

The pipeline is a browser's, in priority order, and the first step that produces an answer wins:

1. **Byte-order mark.** A leading UTF-8, UTF-16BE, or UTF-16LE BOM is decisive; it reports confidence ``1.0``.
2. **Declaration.** A ``<meta charset>`` (or ``<meta http-equiv>``) in the first bytes is an explicit author statement,
   also ``1.0``.
3. **Structure.** A stream with no declaration that is valid multi-byte UTF-8, or a 7-bit escape-driven ISO-2022-JP
   stream, is resolved by structure rather than guessed; pure ASCII reports ``ascii``.
4. **Heuristic.** Everything else runs the `chardetng <https://github.com/hsivonen/chardetng>`_-style competition: each
   single-byte and CJK candidate accumulates a character-pair frequency score and is disqualified outright by a single
   decode error, and the highest surviving score wins. With no positive evidence the field defaults to windows-1252,
   exactly as the WHATWG sniffing algorithm does.

Because the declaration steps come first, ``detect`` never contradicts a page that says what it is; only a
declaration-less stream is ever guessed.

*************************
 Confidence and language
*************************

chardetng emits a raw integer score, not a probability, so :class:`~turbohtml.detect.EncodingMatch` calibrates one. A
declaration or pure ASCII reports ``1.0``; a structural UTF-8 / ISO-2022-JP match reports ``0.99``; a heuristic guess
maps its score onto ``0.5..0.99`` -- larger evidence is closer to certain, and the windows-1252 default with no evidence
sits at the ``0.5`` midpoint -- and is capped below structural certainty so a guess never claims more than the structure
can prove. The ``language`` is the script's dominant natural language the legacy encoding implies (a Cyrillic code page
reports ``"Russian"``, a CJK page its script's language); the Unicode transports and the Latin scripts shared across
languages report ``None``.

See :doc:`/reference/detect` for the API and :doc:`parsing` for what happens after the bytes become a ``str``.
