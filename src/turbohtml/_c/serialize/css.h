/* Public seam of the CSS minifier's allocator-agnostic core. The tokenizer, grammar and
   value engine stay behind css_tokenize.h and friends; only th_minify_css_bytes crosses
   into the rest of the module, so the HTML minifier can reuse the shipped minify_css
   engine over <style> bodies and style="" values without pulling in the token headers. */

#ifndef TURBOHTML_SERIALIZE_CSS_H
#define TURBOHTML_SERIALIZE_CSS_H

#include "core/common.h"

/* Minify the UTF-8 CSS view[0..length) into a freshly allocated UTF-8 buffer (free with
   PyMem_Free); *out_len receives its byte length. inline_mode 1 parses a bare declaration
   list (a style="" value), 0 a full stylesheet (a <style> body). baseline bounds how new
   the output syntax may be (0 targets every browser). Empty output returns NULL with
   *out_len 0. The engine is value-safe: the output reparses to the same cascade as the
   input, and re-minifying is a fixpoint. The view type is css_char (unsigned char). */
unsigned char *th_minify_css_bytes(const unsigned char *view, Py_ssize_t length, int inline_mode, int baseline,
                                   Py_ssize_t *out_len);

#endif /* TURBOHTML_SERIALIZE_CSS_H */
