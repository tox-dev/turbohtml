########
 Detect
########

.. module:: turbohtml.detect

Detect the character encoding of bytes without parsing them, a successor to ``chardet.detect``, ``cchardet.detect``, and
``charset_normalizer.from_bytes``. The pipeline is the one :func:`turbohtml.parse` runs for ``bytes`` input -- the
WHATWG sniff (byte-order mark, then a ``<meta>`` prescan), the content detector ported from Firefox's `chardetng
<https://github.com/hsivonen/chardetng>`_, then the spec's windows-1252 fallback -- so a standalone detection and
``parse(data, detect_encoding=True)`` always agree (:doc:`how it decides </explanation/parsing>`).

.. autofunction:: detect

.. autofunction:: detect_all

.. autoclass:: EncodingMatch

.. autoclass:: Detection
    :members: chardet

.. autoclass:: Detector
    :members: feed, close, reset, result

    .. autoattribute:: done
        :no-value:
