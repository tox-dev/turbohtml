/* Parser-test driver for the lexer. It runs the token stream and renders it as a
   canonical dump the unit tests diff against, the way query/xpath exposes xp_dump.

   Because the lexer deliberately defers the regex-vs-division and template-vs-block
   decisions to the parser, this driver stands in for the parser with the classic
   "is a value expected here?" heuristic (regex is allowed only where an operand may
   start) and a template-nesting stack. That heuristic is an approximation - the
   production path in later phases drives the same rescan entry points from the real
   grammar - but it is exact for the constructs the tests cover and it exercises the
   lexer's regex and template branches.

   Dump format: tokens space-separated; a value-bearing token as KIND:lexeme, a
   punctuator as KIND; a token with a line terminator before it is prefixed with *. */

#include "js/internal.h"
#include "js/minify.h"

#include "core/common.h"

#include <string.h>

static PyObject *minify_js_impl(PyObject *arg, int fold, int mangle, int passthrough) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "source must be a str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    Py_UCS4 *src = PyUnicode_AsUCS4Copy(arg);
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t out_len = 0;
    char err[160];
    Py_UCS4 *out = th_js_minify(src, len, fold, mangle, &out_len, err, sizeof(err));
    PyMem_Free(src);
    if (out == NULL) {
        if (err[0] != '\0') { /* GCOVR_EXCL_BR_LINE: the empty-message case is an allocation failure */
            if (passthrough) {
                /* lenient mode: an unparsable script passes through unchanged, the leniency the
                   inline-<script> path already has via th_js_minify's errlen==0 opt-out */
                return Py_NewRef(arg);
            }
            PyErr_SetString(PyExc_ValueError, err);
            return NULL;
        }
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, out, out_len);
    PyMem_Free(out);
    return result;
}

/* The single minify seam, exposed as _minify_js(source, fold, mangle, on_error); the public
   turbohtml.minify_js() wrapper and the HTML inline-<script> path both drive it. on_error
   "passthrough" returns the source unchanged on a parse failure instead of raising. */
PyObject *turbohtml_minify_js(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *source = NULL;
    int fold = 1;
    int mangle = 1;
    const char *on_error = NULL;
    if (!PyArg_ParseTuple(args, "Opps:_minify_js", &source, &fold, &mangle, &on_error)) {
        return NULL;
    }
    int passthrough;
    if (strcmp(on_error, "raise") == 0) {
        passthrough = 0;
    } else if (strcmp(on_error, "passthrough") == 0) {
        passthrough = 1;
    } else {
        PyErr_Format(PyExc_ValueError, "on_error must be 'raise' or 'passthrough', not '%s'", on_error);
        return NULL;
    }
    return minify_js_impl(source, fold, mangle, passthrough);
}

typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} dbuf;

static void dbuf_reserve(dbuf *out, Py_ssize_t extra) {
    if (out->len + extra <= out->cap) {
        return;
    }
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)(out->len + extra), (size_t)out->cap, 256, sizeof(Py_UCS4), &cap, &bytes);
    if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS4 *grown = PyMem_Realloc(out->data, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    out->data = grown;
    out->cap = (Py_ssize_t)cap;
}

static void dbuf_ascii(dbuf *out, const char *str) {
    Py_ssize_t add = (Py_ssize_t)strlen(str);
    dbuf_reserve(out, add);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;        /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < add; index++) {
        out->data[out->len++] = (Py_UCS4)(unsigned char)str[index];
    }
}

static void dbuf_run(dbuf *out, const Py_UCS4 *text, Py_ssize_t add) {
    dbuf_reserve(out, add);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;        /* GCOVR_EXCL_LINE */
    }
    memcpy(out->data + out->len, text, (size_t)add * sizeof(Py_UCS4));
    out->len += add;
}

/* A value-bearing token carries its lexeme; a punctuator is named by kind alone.
   An error token carries the offending span so a failed lex is diagnosable. */
static int jm_tok_has_text(jm_tok kind) {
    switch (kind) {
    case JT_ERROR:
    case JT_IDENT:
    case JT_PRIVATE:
    case JT_NUM:
    case JT_BIGINT:
    case JT_STRING:
    case JT_REGEX:
    case JT_TEMPLATE:
    case JT_TEMPLATE_HEAD:
    case JT_TEMPLATE_MIDDLE:
    case JT_TEMPLATE_TAIL:
        return 1;
    default:
        return 0;
    }
}

/* The value-expecting keywords: after one of these a `/` begins a regex, not
   division. Reserved words that are themselves values (this/true/false/null/super)
   are deliberately absent - a `/` after them is division. */
static int jm_keyword_expects_value(const jm_lexer *lx) {
    static const char *const words[] = {"return", "typeof", "delete", "void",  "instanceof", "in",    "of",      "new",
                                        "do",     "else",   "yield",  "await", "case",       "throw", "default", NULL};
    for (int index = 0; words[index] != NULL; index++) {
        if (jm_text_eq(lx, words[index])) {
            return 1;
        }
    }
    return 0;
}

/* Whether, after the current token, the next `/` would open a regex literal. */
static int jm_value_follows(const jm_lexer *lx) {
    switch (lx->kind) {
    case JT_IDENT:
        return jm_keyword_expects_value(lx);
    case JT_PRIVATE:
    case JT_NUM:
    case JT_BIGINT:
    case JT_STRING:
    case JT_REGEX:
    case JT_TEMPLATE:
    case JT_TEMPLATE_TAIL:
    case JT_RPAREN:
    case JT_RBRACK:
    case JT_RBRACE:
        return 0; /* an operand just ended: the next `/` is division */
    default:
        return 1; /* after any operator or opening bracket a value is expected */
    }
}

Py_UCS4 *jm_lex_dump(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_len) {
    jm_lexer lx;
    jm_lex_init(&lx, src, len);
    dbuf out = {NULL, 0, 0, 0};

    int expect_value = 1;
    int level = 0; /* open-brace depth, with `${` counting as a brace */
    int tmpl[64];  /* brace depth at which each open substitution's `}` sits */
    int tmpl_top = 0;
    int first = 1;

    Py_ssize_t guard = -1;
    for (;;) {
        jm_lex_next(&lx);
        if (lx.pos == guard && lx.kind != JT_EOF) { /* GCOVR_EXCL_START: guards an internal
            invariant -- every scan advances or reaches EOF. No input can stall a correct
            lexer, so this is unreachable from a test; it exists to turn a future scanner
            bug into a visible STUCK marker rather than an out-of-memory hang. */
            dbuf_ascii(&out, " STUCK");
            break;
        } /* GCOVR_EXCL_STOP */
        guard = lx.pos;
        if ((lx.kind == JT_DIV || lx.kind == JT_DIV_ASSIGN) && expect_value) {
            jm_lex_rescan_regex(&lx);
        } else if (lx.kind == JT_LBRACE) {
            level++;
        } else if (lx.kind == JT_RBRACE) {
            if (tmpl_top > 0 && level == tmpl[tmpl_top - 1]) {
                jm_lex_rescan_template(&lx);
                if (lx.kind == JT_TEMPLATE_TAIL) {
                    tmpl_top--;
                    level--;
                }
            } else {
                level--;
            }
        } else if (lx.kind == JT_TEMPLATE_HEAD) {
            level++;
            if (tmpl_top < (int)(sizeof(tmpl) / sizeof(tmpl[0]))) {
                tmpl[tmpl_top++] = level;
            }
        }

        if (!first) {
            dbuf_ascii(&out, " ");
        }
        first = 0;
        if (lx.newline_before) {
            dbuf_ascii(&out, "*");
        }
        dbuf_ascii(&out, jm_tok_name(lx.kind));
        if (jm_tok_has_text(lx.kind)) {
            dbuf_ascii(&out, ":");
            dbuf_run(&out, lx.text, lx.text_len);
        }

        if (lx.kind == JT_EOF || lx.kind == JT_ERROR) {
            break;
        }
        expect_value = jm_value_follows(&lx);
    }

    if (out.failed) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        PyMem_Free(out.data); /* GCOVR_EXCL_LINE */
        return NULL;          /* GCOVR_EXCL_LINE */
    }
    *out_len = out.len;
    return out.data;
}

PyObject *turbohtml_minify_js_parse(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "source must be a str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    Py_UCS4 *src = PyUnicode_AsUCS4Copy(arg);
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    char err[128];
    jm_program *prog = jm_parse(src, len, err, sizeof(err));
    if (prog == NULL) {
        PyMem_Free(src);
        /* GCOVR_EXCL_BR_LINE: the empty-message fallback below is an allocation failure */
        PyErr_SetString(PyExc_ValueError, err[0] ? err : "out of memory"); /* GCOVR_EXCL_BR_LINE */
        return NULL;
    }
    Py_ssize_t dlen = 0;
    Py_UCS4 *dump = jm_dump(prog, &dlen);
    jm_program_free(prog);
    PyMem_Free(src);
    if (dump == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, dump, dlen);
    PyMem_Free(dump);
    return result;
}

PyObject *turbohtml_minify_js_tokens(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "source must be a str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    Py_UCS4 *src = PyUnicode_AsUCS4Copy(arg);
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t dlen = 0;
    Py_UCS4 *dump = jm_lex_dump(src, len, &dlen);
    PyMem_Free(src);
    if (dump == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, dump, dlen);
    PyMem_Free(dump);
    return result;
}
