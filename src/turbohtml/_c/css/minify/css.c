/* Aggressive, value-safe CSS minification. Every transform preserves the computed value per the CSS specifications
   (Syntax 3, Values 4, Color 4, Selectors 4, and the shorthand modules), so the output parses to the same cascade as
   the input. The contract is spec conformance: each rewrite cites the spec section that establishes its equivalence,
   and the minifier emits nothing a spec does not permit.

   Two entry points: th_minify_css for a full stylesheet (rules, at-rules, nesting) and th_minify_css_inline for a bare
   declaration list as in a style= attribute. The pipeline is: a zero-copy tokenizer that points its tokens straight
   into the source code-point buffer and hops whitespace with the shared SWAR lane probe; a recursive-descent grammar
   over those tokens; and a value engine that shortens numbers, dimensions and colors and folds a handful of
   shorthands.

   Rendered value components are interned into a per-call code-point pool; a component refers to its text by
   (offset, length) into that pool so the pool can grow without invalidating components.

   The engine core (tokenizer, grammar, value engine, th_minify_css_bytes) touches no CPython runtime: it allocates
   through the css_malloc macros and writes to css_buf. The CPython binding (the PyObject entry points) is compiled
   only into the extension, behind CSS_MINIFY_STANDALONE, so a pure-C harness can run the core under
   AddressSanitizer/LeakSanitizer and libFuzzer. */

#include "core/common.h"
#include "data/css_colors.h"

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "css/minify/css_tokenize.h"
#include "css/minify/css_value.h"
#include "css/minify/css_calc.h"
#include "css/minify/css_render.h"
#include "css/minify/css_shorthand.h"
#include "css/minify/css_selector.h"
#include "css/minify/css_grammar.h"
#include "css/minify/css.h"

/* The allocator-agnostic core: minify a code-point view into a freshly allocated buffer (free with css_free). The
   harness and the CPython binding both call this; it touches no CPython runtime. */
css_char *th_minify_css_bytes(const css_char *view, Py_ssize_t length, int inline_mode, int baseline,
                              Py_ssize_t *out_len) {
    token_vec tokens = {NULL, 0, 0, 0};
    /* presize from the input: tokens average a few code points each and the output never exceeds the input, so one
       allocation up front avoids the geometric realloc churn (and its repeated copies) on a large stylesheet */
    Py_ssize_t token_guess = length / 4 < 64 ? 64 : length / 4;
    css_token *token_store = css_malloc((size_t)token_guess * sizeof(css_token));
    if (token_store != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        tokens.items = token_store;
        tokens.cap = token_guess;
    }
    css_tokenize(view, length, &tokens);
    css_buf pool = {NULL, 0, 0, 0};
    css_buf out = {NULL, 0, 0, 0};
    /* the pool holds the value scratch plus every interned selector and body, so it runs to roughly twice the input */
    cbuf_reserve(&pool, length * 2);
    cbuf_reserve(&out, length);
    cursor cur = {&tokens, 0, baseline};
    if (inline_mode) {
        decl_vec decls = {NULL, 0, 0, 0};
        css_parse_declarations(&pool, &cur, &decls);
        css_render_declarations(&pool, &decls, baseline, &out);
        css_free(decls.items);
    } else {
        css_parse_rules(&pool, &cur, 1, 0, &out);
    }
    css_free(tokens.items);
    cbuf_free(&pool);
    *out_len = out.len;
    return out.data;
}

#ifndef CSS_MINIFY_STANDALONE
static PyObject *css_minify_entry(PyObject *args, int inline_mode) {
    PyObject *source = NULL;
    int baseline = 0;
    if (!PyArg_ParseTuple(args, inline_mode ? "Oi:_minify_css_inline" : "Oi:_minify_css", &source, &baseline)) {
        return NULL;
    }
    if (!PyUnicode_Check(source)) {
        PyErr_SetString(PyExc_TypeError, "argument must be str");
        return NULL;
    }
    /* the str's UTF-8 view is cached and, for an ASCII str, aliases its storage (no copy); a str with a lone
       surrogate has no UTF-8 form and is rejected with the encode error */
    Py_ssize_t length = 0;
    const char *utf8 = PyUnicode_AsUTF8AndSize(source, &length);
    if (utf8 == NULL) {
        return NULL;
    }
    Py_ssize_t out_len = 0;
    css_char *data = th_minify_css_bytes((const css_char *)utf8, length, inline_mode, baseline, &out_len);
    if (data == NULL && out_len != 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory();        /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyUnicode_DecodeUTF8((const char *)data, out_len, "strict");
    PyMem_Free(data);
    return result;
}

PyObject *turbohtml_minify_css(PyObject *Py_UNUSED(module), PyObject *args) {
    return css_minify_entry(args, 0);
}

PyObject *turbohtml_minify_css_inline(PyObject *Py_UNUSED(module), PyObject *args) {
    return css_minify_entry(args, 1);
}
#endif /* !CSS_MINIFY_STANDALONE */
