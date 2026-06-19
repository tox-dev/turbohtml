/* Content-based character encoding detection (issue #182), #included into
   tree_type.c after encoding.h.

   This is a strictly WHATWG-subordinate fallback: it runs only after the spec
   path (a BOM, the encoding argument, then the <meta> prescan) has yielded no
   encoding, and only when the caller opts in with detect_encoding=True. The spec
   result always wins, so conformance is unaffected; without the opt-in the
   fallback stays windows-1252 exactly as before.

   The design follows Firefox's chardetng (https://github.com/hsivonen/chardetng,
   MIT/Apache-2.0, Copyright Mozilla Foundation): negative matching (a single
   decode error or C1 control disqualifies a candidate encoding) combined with
   character-pair frequency scoring for the single-byte encodings. This first
   phase implements the UTF-8 candidate -- the one encoding that structure alone
   detects reliably -- and the dispatch the later single-byte and CJK phases plug
   into. */

/* Validate a byte run as well-formed UTF-8, setting *has_non_ascii when it holds
   at least one multi-byte sequence. Returns 1 for valid UTF-8, 0 on the first
   malformed, overlong, surrogate, or truncated sequence -- a single error
   disqualifies UTF-8, as in chardetng. Pure ASCII is valid UTF-8 but leaves
   *has_non_ascii 0, since ASCII decodes identically under the windows-1252
   fallback and carries no evidence either way. */
static int th_detect_is_utf8(const unsigned char *buf, Py_ssize_t len, int *has_non_ascii) {
    *has_non_ascii = 0;
    Py_ssize_t index = 0;
    while (index < len) {
        unsigned char lead = buf[index];
        if (lead < 0x80) {
            index++;
            continue;
        }
        *has_non_ascii = 1;
        Py_ssize_t trailing;
        unsigned char first_low;
        unsigned char first_high;
        /* the first continuation byte carries the lower bound that rejects an overlong
           form and the upper bound that rejects surrogates / values above U+10FFFF; the
           rest of the continuation bytes are the full 0x80..0xBF */
        if (lead >= 0xC2 && lead <= 0xDF) {
            trailing = 1;
            first_low = 0x80;
            first_high = 0xBF;
        } else if (lead >= 0xE0 && lead <= 0xEF) {
            trailing = 2;
            first_low = lead == 0xE0 ? 0xA0 : 0x80;  /* E0: no overlong 80..9F */
            first_high = lead == 0xED ? 0x9F : 0xBF; /* ED: no surrogate A0..BF */
        } else if (lead >= 0xF0 && lead <= 0xF4) {
            trailing = 3;
            first_low = lead == 0xF0 ? 0x90 : 0x80;  /* F0: no overlong 80..8F */
            first_high = lead == 0xF4 ? 0x8F : 0xBF; /* F4: nothing above U+10FFFF */
        } else {
            return 0; /* C0/C1, F5..FF, or a stray continuation byte as lead */
        }
        if (index + trailing >= len) {
            return 0; /* truncated trailing sequence */
        }
        if (buf[index + 1] < first_low || buf[index + 1] > first_high) {
            return 0;
        }
        for (Py_ssize_t offset = 2; offset <= trailing; offset++) {
            if (buf[index + offset] < 0x80 || buf[index + offset] > 0xBF) {
                return 0;
            }
        }
        index += trailing + 1;
    }
    return 1;
}

/* Guess the encoding of a declaration-less byte stream, or NULL when no candidate
   is confident enough (the caller then keeps the windows-1252 fallback). Phase 1
   resolves only UTF-8; the single-byte and CJK candidates land in later phases. */
static const th_encoding_entry *th_encoding_detect(const unsigned char *buf, Py_ssize_t len) {
    int has_non_ascii;
    if (th_detect_is_utf8(buf, len, &has_non_ascii) && has_non_ascii) {
        return th_encoding_lookup("utf-8", 5);
    }
    return NULL;
}
