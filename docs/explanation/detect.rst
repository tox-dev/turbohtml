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

*********************
 Detection is opt-in
*********************

:func:`turbohtml.parse` assumes a ``str`` is already decoded and never sniffs it; that is the common case and it stays
free of the scan. You reach for detection at the byte boundary. Pass ``bytes`` to :func:`turbohtml.parse` and it runs
the pipeline above to decode them; call :func:`turbohtml.detect.detect` on their own to learn the encoding without
building a tree, the job a standalone ``chardet.detect`` did. Keeping it explicit means the fast path never pays for a
scan it does not need, and the guessing only happens where the input lacks a label. The :doc:`/how-to/encoding` guide
shows both calls.
