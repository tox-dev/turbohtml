/* The public entry point: parse a script and print it minified. A parse error is
   reported to the caller (errbuf) rather than swallowed, so an unminifiable or
   malformed script fails loudly instead of passing through unchanged. The HTML
   inline-<script> path adds its own fallback so a single bad script cannot break
   document serialization; the standalone minify_js() surfaces the error. */

#include "js/internal.h"
#include "js/minify.h"

Py_UCS4 *th_js_minify(const Py_UCS4 *src, Py_ssize_t len, int fold, int mangle, int module, Py_ssize_t *out_len,
                      char *errbuf, size_t errlen) {
    jm_program *prog = jm_parse(src, len, module, errbuf, errlen);
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
        jm_analysis *analysis = NULL;
        /* GCOVR_EXCL_BR_START: the tree only shrinks, so it converges well before the backstop cap */
        for (int pass = 0; pass < 12; pass++) {
            /* GCOVR_EXCL_BR_STOP */
            int status = jm_compress(prog, &analysis);
            if (status <= 0) {
                settled = 1; /* nothing left to do, or a with/eval poisoned resolution, or an allocation failed */
                break;
            }
            /* a status of 2 left the analysis exact for what the passes read, so a fold of the functions the rewrites
               touched that changes nothing settles the tree without another round */
            int folded = fold && (status == 2 ? jm_fold_touched(prog) : jm_fold(prog));
            if (status == 2 && !folded) {
                settled = 1;
                break;
            }
            jm_analysis_free(analysis);
            analysis = NULL;
            settled = fold && !folded;
        }
        jm_mangle(prog, analysis); /* assign short names once the shape has settled */
        /* a last fold over a pathological unsettled exit, and over the `undefined` reads a shadowing
           binding kept the first fold from touching: the mangle resolution now proves them global */
        if (fold && (!settled || prog->shadows_undefined)) { /* GCOVR_EXCL_BR_LINE: only the cap exits unsettled */
            jm_fold(prog);
        }
    }
    /* a pass that ran out of memory left its rewrite unfinished, so printing would return partial output */
    Py_UCS4 *out = prog->failed ? NULL : jm_print(prog, out_len); /* GCOVR_EXCL_BR_LINE: allocation failure */
    jm_program_free(prog);
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        errbuf[0] = '\0'; /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE */
    return out;
}
