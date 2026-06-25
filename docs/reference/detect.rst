########
 Detect
########

.. module:: turbohtml.detect

Guess the character encoding of a byte stream with no HTML parser in the call path -- a ``bytes -> encoding`` surface
that stands in for `chardet <https://github.com/chardet/chardet>`_, `charset-normalizer
<https://github.com/jawah/charset_normalizer>`_, and ``cchardet``. It reuses the same WHATWG content-detection engine
that backs ``parse(detect_encoding=True)``: an explicit declaration wins first (a byte-order mark, then a ``<meta
charset>`` prescan), and only a declaration-less stream falls through to the chardetng-style heuristic, which resolves
UTF-8 and ISO-2022-JP structurally and otherwise runs the single-byte and CJK candidates in a strict-max competition
that defaults to windows-1252. See :doc:`/explanation/detection` for the pipeline and how the confidence is calibrated.

.. autofunction:: detect

.. autofunction:: detect_all

.. autoclass:: EncodingMatch
    :members:

*************
 Incremental
*************

:class:`Detector` mirrors chardet's ``UniversalDetector``: feed the stream in chunks and resolve on
:meth:`~Detector.close`. The engine scores the whole stream at once, so the buffered bytes are detected together; unlike
the stateless functions, a detector is stateful and not safe to share across threads.

.. autoclass:: Detector
    :members: feed, close, reset, result

***************
 Configuration
***************

All three entry points take one immutable :class:`Detection` config -- a confidence floor, a language hint, and encoding
allow/deny sets -- so the same tuning applies across the surface. :meth:`Detection.chardet` reproduces chardet's default
confidence threshold.

.. autoclass:: Detection
    :members: chardet
