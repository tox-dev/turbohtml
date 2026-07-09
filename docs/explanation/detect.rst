####################
 Encoding detection
####################

Bytes off the network rarely say what they are. A response header may lie or go missing, a ``<meta charset>`` may
disagree with the transfer encoding, and a file on disk carries no label at all. A browser resolves this every time it
loads a page, and :func:`turbohtml.detect.detect` answers the same question with the same pipeline the parser runs, so
the encoding it picks is the one a browser would decode with.

**********************
 Why a chardetng port
**********************

The detector is a C port of Firefox's `chardetng <https://github.com/hsivonen/chardetng>`_, the encoding scanner Gecko
ships. Porting a shipping browser detector rather than inventing a scoring model keeps the result aligned with what real
pages already render as: chardetng is tuned against the corpus of legacy-encoded pages the web serves, and its verdicts
are the ones users have been reading for years. The alternative detectors turbohtml replaces -- `chardet
<https://chardet.readthedocs.io/>`_ and `charset-normalizer <https://charset-normalizer.readthedocs.io/>`_ -- each carry
their own heuristics; matching a browser instead means one fewer way for a page to decode differently in your pipeline
than in the reader's tab.

********************
 The sniff pipeline
********************

Detection runs in the order the `WHATWG encoding standard <https://encoding.spec.whatwg.org/>`_ and the HTML parser lay
out, most authoritative signal first. A byte-order mark decides on its own. Failing that, an explicit label -- a
transport charset the caller passes, or a ``<meta charset>`` found by a bounded prescan of the first bytes -- is honored
when it names a known encoding. Only when nothing declares the encoding does the statistical scorer run: chardetng
weighs the byte sequences against per-language, per-encoding models and returns the highest-scoring candidate, with a
tie broken toward the encoding a page in that language most likely used. UTF-8 is confirmed by structure, since valid
multi-byte UTF-8 is distinctive enough to recognize outright.

********************************
 A name is a label, not a codec
********************************

:attr:`EncodingMatch.encoding <turbohtml.detect.EncodingMatch>` names an encoding in the WHATWG standard's index.
:mod:`python:codecs` also has names, and where the two spell an encoding alike they often mean different encodings. The
standard's ``koi8-u`` is KOI8-RU, so ``0xAE`` is ``ў``; CPython's is KOI8-U, where the same byte is a box-drawing
character. Its ``Big5`` index is a superset of CPython's, its ``EUC-KR`` is windows-949, its ``Shift_JIS`` is
windows-31j, and its ``gb18030`` is the 2005 revision. An unassigned byte between ``0x80`` and ``0x9F`` is a C1 control
in every single-byte index, where CPython raises. ``x-mac-cyrillic`` and ``replacement`` are not CPython codecs at all.

Nor is the disagreement only in the tables. The standard's decoders push an ASCII trail byte back onto the stream, so
Big5 ``81 41`` decodes to U+FFFD followed by ``A``, and consume a non-ASCII one, so ``81 FF`` is a single U+FFFD. No
:mod:`python:codecs` error handler reproduces that. turbohtml therefore decodes every legacy encoding from the
standard's own index tables rather than delegating, which is what lets :doc:`the detector </reference/detect>` promise
that the encoding it picks decodes the way a browser's would. UTF-8 and UTF-16 still delegate, because CPython's
decoders for those do match the standard.

So ``bytes.decode(match.encoding)`` is a mistake, and :attr:`EncodingMatch.codec <turbohtml.detect.EncodingMatch>`
exists to prevent it: it names a ``whatwg-*`` codec :mod:`turbohtml.detect` registers over the same decoders the parser
runs.

*********************
 Detection is opt-in
*********************

:func:`turbohtml.parse` assumes a ``str`` is already decoded and never sniffs it; that is the common case and it stays
free of the scan. You reach for detection at the byte boundary. Pass ``bytes`` to :func:`turbohtml.parse` and it runs
the pipeline above to decode them; call :func:`turbohtml.detect.detect` on their own to learn the encoding without
building a tree, the job a standalone ``chardet.detect`` did. Keeping it explicit means the fast path never pays for a
scan it does not need, and the guessing only happens where the input lacks a label. The :doc:`/how-to/encoding` guide
shows both calls.
