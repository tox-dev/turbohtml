/* markupsafe-compatible HTML escaping that returns a Markup safe-string.

   turbohtml.markup mirrors markupsafe's escape/Markup surface so a project can
   replace `from markupsafe import ...` with `from turbohtml.migration.markupsafe import ...`.
   This escape differs from turbohtml.escape() in the two ways markupsafe's
   contract fixes: it always escapes both quotes, and it writes the numeric
   references markupsafe emits (&#34; / &#39;) rather than the stdlib forms
   (&quot; / &#x27;). The result is a Markup (a str subclass), built here in C so
   the Jinja2 autoescape hot path stays one C call with no Python-level wrapper
   around it: matching markupsafe's output but skipping the per-call escape()
   function frame and Markup() construction that dominate its cost on the small
   strings templates interpolate.

   The Markup type and its operator surface live in turbohtml/markup.py, which
   hands the type to _register_markup() at import time; escape results are
   stamped with it through str's own constructor, bypassing Markup.__new__ since
   the bytes are already escaped and safe. */

#include "core/common.h"

#include "tokenizer/binding.h"

#include <stdint.h>
#include <string.h>

#if defined(_MSC_VER)
#include <intrin.h>
static inline int ms_ctz64(uint64_t value) {
    unsigned long index;
    _BitScanForward64(&index, value);
    return (int)index;
}
#else
#define ms_ctz64(value) __builtin_ctzll(value)
#endif

/* The 1-byte path classifies eight bytes per step with the same has-zero SWAR
   probe escape.c uses; escaping markup is overwhelmingly small ASCII, so a
   portable word scan covers the throughput without the SIMD backends. */
#define MS_BLOCK 8
#define MS_ONES 0x0101010101010101ULL
#define MS_HIGHS 0x8080808080808080ULL

static inline uint64_t ms_hasbyte(uint64_t word, uint8_t byte) {
    uint64_t lanes = word ^ (MS_ONES * byte);
    return (lanes - MS_ONES) & ~lanes & MS_HIGHS;
}

/* Each matching lane's high bit; summed by shifting to the low bit and folding. */
static inline Py_ssize_t ms_count(uint64_t mask) {
    return (Py_ssize_t)(((mask >> 7) * MS_ONES) >> 56);
}

static inline uint64_t ms_special_mask(uint64_t word) {
    return ms_hasbyte(word, '&') | ms_hasbyte(word, '<') | ms_hasbyte(word, '>') | ms_hasbyte(word, '"') |
           ms_hasbyte(word, '\'');
}

/* Growth in characters from escaping one code point: &amp; adds 4, &lt;/&gt;
   add 3, &#34;/&#39; add 4. */
static inline Py_ssize_t ms_extra(Py_UCS4 character) {
    switch (character) {
    case '&':
    case '"':
    case '\'':
        return 4;
    case '<':
    case '>':
        return 3;
    default:
        return 0;
    }
}

static inline Py_ssize_t ms_block_extra(uint64_t word) {
    return 4 * ms_count(ms_hasbyte(word, '&')) + 3 * ms_count(ms_hasbyte(word, '<')) +
           3 * ms_count(ms_hasbyte(word, '>')) + 4 * ms_count(ms_hasbyte(word, '"')) +
           4 * ms_count(ms_hasbyte(word, '\''));
}

static inline Py_ssize_t ms_write(int kind, void *data, Py_ssize_t offset, Py_UCS4 character) {
    const char *replacement = NULL;
    int replacement_len = 0;
    switch (character) {
    case '&':
        replacement = "&amp;";
        replacement_len = 5;
        break;
    case '<':
        replacement = "&lt;";
        replacement_len = 4;
        break;
    case '>':
        replacement = "&gt;";
        replacement_len = 4;
        break;
    case '"':
        replacement = "&#34;";
        replacement_len = 5;
        break;
    case '\'':
        replacement = "&#39;";
        replacement_len = 5;
        break;
    default:
        break;
    }
    if (replacement != NULL) {
        for (int index = 0; index < replacement_len; index++) {
            PyUnicode_WRITE(kind, data, offset + index, (Py_UCS4)replacement[index]);
        }
        return replacement_len;
    }
    PyUnicode_WRITE(kind, data, offset, character);
    return 1;
}

/* How many characters escaping the whole string adds (0 proves a no-op). */
static Py_ssize_t ms_sizing(int kind, const void *data, Py_ssize_t length) {
    Py_ssize_t extra = 0;
    if (kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *input = (const uint8_t *)data;
        Py_ssize_t pos = 0;
        for (; pos + MS_BLOCK <= length; pos += MS_BLOCK) {
            uint64_t word;
            memcpy(&word, input + pos, sizeof(word));
            extra += ms_block_extra(word);
        }
        for (; pos < length; pos++) {
            extra += ms_extra(input[pos]);
        }
    } else if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *input = (const uint16_t *)data;
        for (Py_ssize_t pos = 0; pos < length; pos++) {
            extra += ms_extra(input[pos]);
        }
    } else {
        const uint32_t *input = (const uint32_t *)data;
        for (Py_ssize_t pos = 0; pos < length; pos++) {
            extra += ms_extra(input[pos]);
        }
    }
    return extra;
}

/* Fill out (sized length + extra) with the escaped form of text. */
static void ms_fill(int kind, const void *data, Py_ssize_t length, int out_kind, void *out_data) {
    Py_ssize_t written = 0;
    if (kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *input = (const uint8_t *)data;
        uint8_t *output = (uint8_t *)out_data;
        Py_ssize_t pos = 0;
        for (; pos + MS_BLOCK <= length; pos += MS_BLOCK) {
            uint64_t word;
            memcpy(&word, input + pos, sizeof(word));
            uint64_t mask = ms_special_mask(word);
            if (mask == 0) {
                memcpy(output + written, input + pos, MS_BLOCK);
                written += MS_BLOCK;
                continue;
            }
            /* copy the clean gap before each special wholesale, then rewrite it */
            Py_ssize_t prev = 0;
            do {
                Py_ssize_t index = ms_ctz64(mask) >> 3;
                if (index > prev) {
                    memcpy(output + written, input + pos + prev, (size_t)(index - prev));
                    written += index - prev;
                }
                written += ms_write(out_kind, out_data, written, input[pos + index]);
                mask &= mask - 1;
                prev = index + 1;
            } while (mask != 0);
            if (MS_BLOCK > prev) {
                memcpy(output + written, input + pos + prev, (size_t)(MS_BLOCK - prev));
                written += MS_BLOCK - prev;
            }
        }
        for (; pos < length; pos++) {
            written += ms_write(out_kind, out_data, written, input[pos]);
        }
    } else if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *input = (const uint16_t *)data;
        for (Py_ssize_t pos = 0; pos < length; pos++) {
            written += ms_write(out_kind, out_data, written, input[pos]);
        }
    } else {
        const uint32_t *input = (const uint32_t *)data;
        for (Py_ssize_t pos = 0; pos < length; pos++) {
            written += ms_write(out_kind, out_data, written, input[pos]);
        }
    }
}

/* Make a Markup holding content, through str's constructor so Markup.__new__ is
   skipped: the content is already safe, so its __html__/coercion logic is moot. */
static PyObject *markup_wrap(PyObject *markup_type, PyObject *content) {
    PyObject *args = PyTuple_Pack(1, content);
    if (args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_Type.tp_new((PyTypeObject *)markup_type, args, NULL);
    Py_DECREF(args);
    return result;
}

/* Escape text (a str) with markupsafe's entities and return it as a Markup. */
static PyObject *markup_escape_str(PyObject *markup_type, PyObject *text) {
    int kind = PyUnicode_KIND(text);
    Py_ssize_t length = PyUnicode_GET_LENGTH(text);
    const void *data = PyUnicode_DATA(text);

    Py_ssize_t extra = ms_sizing(kind, data, length);
    if (extra == 0) {
        return markup_wrap(markup_type, text);
    }

    Py_UCS4 maxchar = PyUnicode_MAX_CHAR_VALUE(text);
    PyObject *escaped = PyUnicode_New(length + extra, th_str_maxchar(maxchar));
    if (escaped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    ms_fill(kind, data, length, PyUnicode_KIND(escaped), PyUnicode_DATA(escaped));
    PyObject *result = markup_wrap(markup_type, escaped);
    Py_DECREF(escaped);
    return result;
}

static PyObject *markup_type_or_error(PyObject *module) {
    module_state *state = PyModule_GetState(module);
    if (state->markup_type == NULL) {       /* GCOVR_EXCL_BR_LINE: turbohtml.markup registers the type at import */
        PyErr_SetString(PyExc_RuntimeError, /* GCOVR_EXCL_LINE: registration precedes any call */
                        "turbohtml.markup is not initialized");
        return NULL; /* GCOVR_EXCL_LINE */
    }
    return state->markup_type;
}

/* escape(s): mirror markupsafe.escape's dispatch exactly. An exact str is
   escaped; anything with __html__ is trusted as-is; everything else is
   str-coerced then escaped. */
PyObject *turbohtml_markup_escape(PyObject *module, PyObject *s) {
    PyObject *markup_type = markup_type_or_error(module);
    if (markup_type == NULL) { /* GCOVR_EXCL_BR_LINE: registration precedes any call */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    if (PyUnicode_CheckExact(s)) {
        return markup_escape_str(markup_type, s);
    }
    PyObject *html = PyObject_GetAttrString(s, "__html__");
    if (html != NULL) {
        PyObject *rendered = PyObject_CallNoArgs(html);
        Py_DECREF(html);
        if (rendered == NULL) {
            return NULL;
        }
        PyObject *text = PyObject_Str(rendered);
        Py_DECREF(rendered);
        if (text == NULL) {
            return NULL;
        }
        PyObject *result = markup_wrap(markup_type, text);
        Py_DECREF(text);
        return result;
    }
    PyErr_Clear();
    PyObject *text = PyObject_Str(s);
    if (text == NULL) {
        return NULL;
    }
    PyObject *result = markup_escape_str(markup_type, text);
    Py_DECREF(text);
    return result;
}

/* escape_silent(s): like escape but None becomes the empty Markup. */
PyObject *turbohtml_markup_escape_silent(PyObject *module, PyObject *s) {
    if (s == Py_None) {
        PyObject *markup_type = markup_type_or_error(module);
        if (markup_type == NULL) { /* GCOVR_EXCL_BR_LINE: registration precedes any call */
            return NULL;           /* GCOVR_EXCL_LINE */
        }
        PyObject *empty = PyUnicode_New(0, 127);
        if (empty == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        PyObject *result = markup_wrap(markup_type, empty);
        Py_DECREF(empty);
        return result;
    }
    return turbohtml_markup_escape(module, s);
}

/* soft_str(s): str-coerce only when needed, preserving a Markup so already-safe
   text is not escaped again. */
PyObject *turbohtml_markup_soft_str(PyObject *Py_UNUSED(module), PyObject *s) {
    if (PyUnicode_Check(s)) {
        return Py_NewRef(s);
    }
    return PyObject_Str(s);
}

/* _register_markup(type): turbohtml.markup hands its Markup class here so escape
   results can be stamped with it. */
PyObject *turbohtml_register_markup(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->markup_type, Py_NewRef(type));
    Py_RETURN_NONE;
}
