/* Public seam of the JavaScript minifier. The rest of the module sees only these
   entry points; the lexer, parser, AST, optimizer and printer stay behind
   js/internal.h. The minifier compiles a script to an arena AST, runs the
   size-reducing passes, and prints it back as code points - independent of the
   HTML tree, so it serves both the standalone minify_js() and the inline-<script>
   path of the HTML minifier. */

#ifndef TURBOHTML_SERIALIZE_JS_MINIFY_H
#define TURBOHTML_SERIALIZE_JS_MINIFY_H

#include "js/jstypes.h"

/* Minify the script src[0..len) into a freshly PyMem-allocated code-point buffer;
   *out_len receives its length. fold runs the constant-folding / dead-code pass and
   mangle the identifier-renaming pass; whitespace, comment and number minification is
   unconditional. Returns NULL on a parse error (a NUL-terminated message, including the
   byte offset, is written into errbuf - capacity errlen) or on allocation failure
   (errbuf left empty). Failing loudly rather than echoing the input back keeps an
   unminifiable script from passing silently; the HTML inline path layers its own
   fallback on top so one bad <script> never breaks serialization. */
Py_UCS4 *th_js_minify(const Py_UCS4 *src, Py_ssize_t len, int fold, int mangle, Py_ssize_t *out_len, char *errbuf,
                      size_t errlen);

/* Parser-test hook: render the token stream of src[0..len) as a canonical,
   newline-separated dump (PyMem-allocated code points; *out_len receives the
   length). Drives the lexer with a minimal operand/operator position tracker so the
   regex- and template-rescan paths are exercised the way the real parser will use
   them. NULL on allocation failure. */
Py_UCS4 *jm_lex_dump(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_len);

#endif /* TURBOHTML_SERIALIZE_JS_MINIFY_H */
