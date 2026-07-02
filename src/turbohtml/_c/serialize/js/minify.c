/* The public entry point: parse a script and print it minified. A parse error is
   reported to the caller (errbuf) rather than swallowed, so an unminifiable or
   malformed script fails loudly instead of passing through unchanged. The HTML
   inline-<script> path adds its own fallback so a single bad script cannot break
   document serialization; the standalone minify_js() surfaces the error. */

#include "serialize/js/internal.h"
#include "serialize/js/minify.h"

Py_UCS4 *th_js_minify(const Py_UCS4 *src, Py_ssize_t len, int fold, int mangle, Py_ssize_t *out_len, char *errbuf,
                      size_t errlen) {
    jm_program *prog = jm_parse(src, len, errbuf, errlen);
    if (prog == NULL) {
        return NULL; /* errbuf carries the message; empty on allocation failure */
    }
    if (fold) {
        jm_fold(prog);
    }
    if (mangle) {
        /* Compress and fold to a fixpoint: each compress pass re-resolves the tree, so a binding a
           previous pass exposed as dead or single-use is caught by the next, and the re-fold cleans up
           the empties and folds any literal an inline just uncovered (`const x=42;x*2` -> `42*2`). The
           tree only ever shrinks, so this converges; the cap is a backstop against a pathological loop. */
        int settled = 0;
        /* GCOVR_EXCL_BR_START: the tree only shrinks, so it converges well before the backstop cap */
        for (int pass = 0; pass < 12; pass++) {
            /* GCOVR_EXCL_BR_STOP */
            if (jm_compress(prog) <= 0) {
                settled = 1; /* nothing left to do (or poisoned/failed: the tree is as folded as before) */
                break;
            }
            settled = fold && !jm_fold(prog); /* clean up empties and fold what the compress exposed */
        }
        jm_mangle(prog);        /* assign short names once the shape has settled */
        if (fold && !settled) { /* GCOVR_EXCL_BR_LINE: only the backstop cap exits the loop unsettled */
            jm_fold(prog);      /* GCOVR_EXCL_LINE: a last fold over that pathological exit */
        } /* GCOVR_EXCL_LINE */
    }
    Py_UCS4 *out = jm_print(prog, out_len);
    jm_program_free(prog);
    if (out == NULL && errlen > 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        errbuf[0] = '\0';            /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE */
    return out;
}
