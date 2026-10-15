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

#ifdef CSS_MINIFY_STANDALONE
/* the core needs only CPython's signed size type, so the harness build supplies it without the interpreter headers */
#include <stddef.h>
#include <stdio.h>
typedef ptrdiff_t Py_ssize_t;
#else
#include "core/common.h"
#endif
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

#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION
/* The fuzz build starts the pool at one code point, as core/fuzzing.h starts arrays small, so the pool's growth and a
   failure there run on short inputs. */
#define CSS_POOL_RESERVE(length) ((Py_ssize_t)1)
#else
/* the pool holds the value scratch plus every interned selector and body, so it runs to roughly twice the input */
#define CSS_POOL_RESERVE(length) ((length) * 2)
#endif

static int css_spell_eof(const css_token *last, const css_char *view, Py_ssize_t length, css_buf *spelled);
static int css_spell_names(const token_vec *tokens, const css_char *view, Py_ssize_t length, css_buf *spelled);
static css_char *css_minify_spelled(token_vec *tokens, css_buf *spelled, int inline_mode, int baseline,
                                    Py_ssize_t *out_len);
static int css_spells_style_end(const css_char *text, Py_ssize_t len);

/* The allocator-agnostic core: minify a code-point view into a freshly allocated buffer (free with css_free). The
   harness and the CPython binding both call this; it touches no CPython runtime. */
css_char *th_minify_css_bytes(const css_char *view, Py_ssize_t length, int inline_mode, int baseline,
                              Py_ssize_t *out_len) {
    /* one flag for the whole call: every buffer and vector below points at it, so a failed allocation anywhere,
       including one whose buffer the caller then drops, fails the call */
    int oom = 0;
    token_vec tokens = {.oom = &oom};
    /* presize from the input: tokens average a few code points each and the output never exceeds the input, so one
       allocation up front avoids the geometric realloc churn (and its repeated copies) on a large stylesheet */
    Py_ssize_t token_guess = length / 4 < 64 ? 64 : length / 4;
    tokens.items = css_malloc((size_t)token_guess * sizeof(css_token));
    if (tokens.items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        *out_len = 1;           /* GCOVR_EXCL_LINE: a NULL result with a length reports the failure */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tokens.cap = token_guess;
    css_tokenize(view, length, &tokens);
    if (tokens.len > 0 && (view[length - 1] == '\\' || tokens.items[tokens.len - 1].kind == CSS_STR ||
                           tokens.items[tokens.len - 1].kind == CSS_URL)) {
        css_buf spelled = {NULL, 0, 0, &oom};
        if (css_spell_eof(&tokens.items[tokens.len - 1], view, length, &spelled)) {
            return css_minify_spelled(&tokens, &spelled, inline_mode, baseline, out_len);
        }
    }
    if (tokens.escaped) {
        css_buf spelled = {NULL, 0, 0, &oom};
        if (css_spell_names(&tokens, view, length, &spelled)) {
            return css_minify_spelled(&tokens, &spelled, inline_mode, baseline, out_len);
        }
        cbuf_free(&spelled);
    }
    css_buf pool = {NULL, 0, 0, &oom};
    css_buf out = {NULL, 0, 0, &oom};
    cursor cur = {&tokens, 0, baseline, -1, 0, {NULL, 0, 0, &oom}};
    /* readers index the pool from its start, so the parsers run only on complete tokens and an allocated pool */
    if (!oom && cbuf_reserve(&pool, CSS_POOL_RESERVE(length)) && /* GCOVR_EXCL_BR_LINE: OOM only */
        cbuf_reserve(&out, length)) {                            /* GCOVR_EXCL_BR_LINE: OOM only */
        if (inline_mode) {
            decl_vec decls = {NULL, 0, 0, &oom, 0};
            css_parse_declarations(&pool, &cur, &decls, 0);
            css_render_declarations(&pool, &decls, baseline, &out, CSS_DECL_NORMAL);
            css_free(decls.items);
        } else {
            css_parse_rules(&pool, &cur, 1, 0, 0, &out);
        }
    }
    cbuf_free(&cur.media_next);
    css_free(tokens.items);
    cbuf_free(&pool);
    if (oom) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        cbuf_free(&out); /* GCOVR_EXCL_LINE: allocation-failure path */
        *out_len = 1;    /* GCOVR_EXCL_LINE: a NULL result with a length reports the failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* Dropping a string line continuation, or a comment in a declaration kept as written, can join a `</style` that
       closes an HTML <style>; the input then comes back unchanged. */
    if (css_spells_style_end(out.data, out.len) && !css_spells_style_end(view, length)) {
        out.len = 0;
        cbuf_put_run(&out, view, length);
    }
    *out_len = out.len;
    return out.data;
}

/* The tokens keep, as written, a string or url the input leaves open and a `\` ending the input, so the `}`, `)` or
   quote the output adds would land inside that token or be escaped. Spelled gets the input with that `\` read as CSS
   Syntax 3 reads it (nothing inside a string, §4.3.5; U+FFFD elsewhere, §4.3.7), then the quote and `)` the open token
   needs: consuming a string or url (§4.3.5, §4.3.6) returns the token at the end of input. Returns whether it spelled
   anything. A url's quote state mirrors css_tokenize, which reads a quoted stretch inside any url. */
static int css_spell_eof(const css_token *last, const css_char *view, Py_ssize_t length, css_buf *spelled) {
    const css_char *text = last->text;
    Py_ssize_t len = last->text_len + last->unit_len; /* a dimension's unit follows its number */
    int escape = last->kind != CSS_COMMENT && css_escapes(text, len);
    css_char quote = 0;
    int open_url = last->kind == CSS_URL;
    if (last->kind == CSS_STR && !css_string_closed(text, len)) {
        quote = text[0];
    }
    for (Py_ssize_t pos = 4; open_url && pos < len; pos++) {
        css_char character = text[pos];
        if (character == '\\') {
            pos++;
        } else if (quote != 0) {
            quote = character == quote ? 0 : quote;
        } else if (character == '"' || character == '\'') {
            quote = character;
        } else {
            open_url = character != ')';
        }
    }
    if (!escape && quote == 0 && !open_url) {
        return 0;
    }
    cbuf_put_run(spelled, view, length - escape);
    if (escape && quote == 0) {
        cbuf_puts(spelled, "\xEF\xBF\xBD");
    }
    if (quote != 0) {
        cbuf_putc(spelled, quote);
    }
    if (open_url) {
        cbuf_putc(spelled, ')');
    }
    return 1;
}

static css_char *css_minify_spelled(token_vec *tokens, css_buf *spelled, int inline_mode, int baseline,
                                    Py_ssize_t *out_len) {
    css_free(tokens->items);
    if (*spelled->oom) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        cbuf_free(spelled); /* GCOVR_EXCL_LINE */
        *out_len = 1;       /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    css_char *minified = th_minify_css_bytes(spelled->data, spelled->len, inline_mode, baseline, out_len);
    cbuf_free(spelled);
    return minified;
}

static void css_put_code_point(css_buf *out, uint32_t code_point) {
    if (code_point < 0x80) {
        cbuf_putc(out, (css_char)code_point);
    } else if (code_point < 0x800) {
        cbuf_putc(out, (css_char)(0xC0 | (code_point >> 6)));
        cbuf_putc(out, (css_char)(0x80 | (code_point & 0x3F)));
    } else if (code_point < 0x10000) {
        cbuf_putc(out, (css_char)(0xE0 | (code_point >> 12)));
        cbuf_putc(out, (css_char)(0x80 | ((code_point >> 6) & 0x3F)));
        cbuf_putc(out, (css_char)(0x80 | (code_point & 0x3F)));
    } else {
        cbuf_putc(out, (css_char)(0xF0 | (code_point >> 18)));
        cbuf_putc(out, (css_char)(0x80 | ((code_point >> 12) & 0x3F)));
        cbuf_putc(out, (css_char)(0x80 | ((code_point >> 6) & 0x3F)));
        cbuf_putc(out, (css_char)(0x80 | (code_point & 0x3F)));
    }
}

/* CSS Syntax 3 §4.3.7 replaces zero, surrogates and out-of-range escapes with U+FFFD.
   Backslash-newline is invalid (§4.3.8); preserve that input for the existing parser. */
static int css_decode_name(const css_char *name, Py_ssize_t len, css_buf *decoded) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        css_char byte = name[pos++];
        if (byte != '\\') {
            cbuf_putc(decoded, byte);
            continue;
        }
        byte = name[pos];
        if (byte == '\n' || byte == '\r' || byte == '\f') {
            return 0;
        }
        if (!css_is_hex(byte)) {
            /* a non-ASCII code point's continuation bytes follow as plain bytes */
            if (byte == 0) {
                css_put_code_point(decoded, 0xFFFD);
            } else {
                cbuf_putc(decoded, byte);
            }
            pos++;
            continue;
        }
        uint32_t value = 0;
        for (int digits = 0; digits < 6 && pos < len && css_is_hex(name[pos]); digits++) {
            css_char hex = name[pos++];
            value = value * 16 + (hex <= '9' ? (uint32_t)(hex - '0') : (uint32_t)((hex | 0x20) - 'a' + 10));
        }
        if (pos < len && css_is_ws(name[pos])) {
            pos += name[pos] == '\r' && pos + 1 < len && name[pos + 1] == '\n' ? 2 : 1;
        }
        css_put_code_point(decoded,
                           value == 0 || (value >= 0xD800 && value <= 0xDFFF) || value > 0x10FFFF ? 0xFFFD : value);
    }
    return 1;
}

enum { CSS_SPELL_IDENT, CSS_SPELL_UNIT, CSS_SPELL_HASH };

/* Hex escapes keep control bytes out of HTML style elements; escaping U+FEFF prevents BOM sniffing.
   Preserve token boundaries for CDC and dimension exponents (CSS Syntax 3 §4.3.1 and §4.3.3). */
static void css_serialize_name(const css_char *name, Py_ssize_t len, int mode, int exponent, css_buf *out) {
    int escape_first = 0;
    if (mode != CSS_SPELL_HASH) {
        css_char first = name[0];
        css_char second = len > 1 ? name[1] : 0;
        int name_start = first >= 0x80 || first == '_' || (css_lower(first) >= 'a' && css_lower(first) <= 'z');
        int second_start =
            second >= 0x80 || second == '_' || second == '-' || (css_lower(second) >= 'a' && css_lower(second) <= 'z');
        escape_first = !(name_start || (first == '-' && second_start)) || (len == 2 && first == '-' && second == '-');
        if (mode == CSS_SPELL_UNIT && !exponent && css_lower(first) == 'e' &&
            (css_is_digit(second) || (second == '-' && len > 2 && css_is_digit(name[2])))) {
            escape_first = 2;
        }
    }
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        css_char byte = name[pos];
        uint32_t code_point = byte;
        int byte_order_mark = byte == 0xEF && name[pos + 1] == 0xBB && name[pos + 2] == 0xBF;
        int hex = byte < 0x20 || byte == 0x7F || byte_order_mark || (pos == 0 && escape_first == 2) ||
                  (pos == 0 && escape_first && css_is_digit(byte));
        if (byte_order_mark) {
            code_point = 0xFEFF;
            pos += 2;
        }
        if (hex) {
            char digits[8];
            int written = snprintf(digits, sizeof(digits), "\\%x", (unsigned int)code_point);
            cbuf_put_run(out, (const css_char *)digits, written);
            if (pos + 1 == len || css_is_hex(name[pos + 1])) {
                cbuf_putc(out, ' ');
            }
            continue;
        }
        if ((pos == 0 && escape_first) || (byte < 0x80 && (byte == '\\' || !(css_charmask[byte] & CSS_CM_IDENT)))) {
            cbuf_putc(out, '\\');
        }
        cbuf_putc(out, byte);
    }
}

/* Preserve hash type and raw @charset spelling: encoding sniffing precedes tokenization
   (CSS Syntax 3 §3.2 and §4.3.1). */
static int css_spell_names(const token_vec *tokens, const css_char *view, Py_ssize_t length, css_buf *spelled) {
    css_buf decoded = {NULL, 0, 0, tokens->oom};
    Py_ssize_t copied = 0;
    int changed = 0;
    for (Py_ssize_t index = 0; index < tokens->len; index++) {
        const css_token *token = &tokens->items[index];
        Py_ssize_t prefix = token->kind == CSS_AT || token->kind == CSS_HASH;
        Py_ssize_t name_len = token->text_len - prefix;
        if (token->kind == CSS_NUM) {
            prefix = token->text_len;
            name_len = token->unit_len;
        } else if (token->kind != CSS_IDENT && token->kind != CSS_AT && token->kind != CSS_HASH) {
            continue;
        }
        const css_char *name = token->text + prefix;
        if (name_len == 0 || memchr(name, '\\', (size_t)name_len) == NULL) {
            continue;
        }
        int starts_ident = css_starts_ident(name, 0, name_len);
        decoded.len = 0;
        if ((!starts_ident && token->kind != CSS_HASH) || !css_decode_name(name, name_len, &decoded) ||
            (token->kind == CSS_AT && decoded.len == 7 && memcmp(decoded.data, "charset", 7) == 0)) {
            continue;
        }
        if (*decoded.oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            break;          /* GCOVR_EXCL_LINE */
        }
        int mode = token->kind == CSS_NUM ? CSS_SPELL_UNIT : starts_ident ? CSS_SPELL_IDENT : CSS_SPELL_HASH;
        int exponent = mode == CSS_SPELL_UNIT && (memchr(token->text, 'e', (size_t)token->text_len) != NULL ||
                                                  memchr(token->text, 'E', (size_t)token->text_len) != NULL);
        cbuf_put_run(spelled, view + copied, name - view - copied);
        Py_ssize_t written = spelled->len;
        css_serialize_name(decoded.data, decoded.len, mode, exponent, spelled);
        if (*spelled->oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            break;           /* GCOVR_EXCL_LINE */
        }
        changed |= spelled->len - written != name_len || memcmp(spelled->data + written, name, (size_t)name_len) != 0;
        copied = name - view + name_len;
    }
    cbuf_free(&decoded);
    cbuf_put_run(spelled, view + copied, length - copied);
    return changed || *spelled->oom; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
}

/* The tokenizer lowercases an end tag name, so `</STYLE` counts too; OR-ing 0x20 folds the ASCII letters and no other
 * byte. */
static int css_spells_style_end(const css_char *text, Py_ssize_t len) {
    static const char name[] = "style";
    for (Py_ssize_t index = 0; index + 2 + (Py_ssize_t)(sizeof(name) - 1) <= len; index++) {
        if (text[index] != '<' || text[index + 1] != '/') {
            continue;
        }
        Py_ssize_t matched = 0;
        while (name[matched] != '\0' && (text[index + 2 + matched] | 0x20) == name[matched]) {
            matched++;
        }
        if (name[matched] == '\0') {
            return 1;
        }
    }
    return 0;
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
