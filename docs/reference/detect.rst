########
 Detect
########

.. module:: turbohtml.detect

Detect the character encoding of bytes without parsing them, a successor to ``chardet.detect``, ``cchardet.detect``, and
``charset_normalizer.from_bytes``. The pipeline is the one :func:`turbohtml.parse` runs for ``bytes`` input -- the
WHATWG sniff (byte-order mark, then a ``<meta>`` prescan), a content detector that scores byte-pair frequencies against
per-encoding character-class models, then the spec's windows-1252 fallback -- so a standalone detection and
``parse(data, detect_encoding=True)`` always agree (:doc:`how it decides </explanation/parsing>`).

.. autofunction:: detect

.. autofunction:: detect_all

.. autoclass:: EncodingMatch

.. autoclass:: Detection
    :members: chardet

.. autoclass:: EncodingDetector
    :members: feed, close, reset, result

    .. autoattribute:: done
        :no-value:

Detect the natural language of text, a successor to ``whatlang``, ``resiliparse.parse.lang``, and trafilatura's language
filter. :func:`detect_language` finds the dominant Unicode script and ranks the languages sharing it by a
character-trigram model, reading the visible text rather than an ``<html lang>`` attribute.

.. autofunction:: detect_language

.. autoclass:: LanguageMatch

.. autoclass:: LanguageDetection

Normalize text to a Unicode normalization form (UAX #15), a C successor to :func:`unicodedata.normalize` and
:func:`unicodedata.is_normalized`. :func:`normalize` runs all four forms -- NFC, NFD, NFKC, NFKD -- over the pinned
tables generated from the interpreter's own ``unicodedata``, so the two agree exactly; a quick check returns
already-normalized text untouched.

.. autofunction:: normalize

.. autofunction:: is_normalized
