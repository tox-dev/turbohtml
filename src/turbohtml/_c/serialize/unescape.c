/* HTML unescaping.

   unescape() hops between '&' occurrences (memchr for 1-byte text), bulk-copies
   the clean spans in between, and resolves numeric and named character
   references per the HTML5 rules at each stop. Named references use binary
   search over the generated table, with longest-prefix matching for references
   that omit the trailing semicolon; numeric references apply the spec's
   correction tables. The output staging starts at the input's width (so clean
   spans are plain memcpy) and widens to Py_UCS4 only when a reference produces
   a wider character (e.g. "&#127881;" in ASCII text); unescape never lengthens
   the text, so the input length is a safe upper bound for the staging. */

#include "core/common.h"

#include <stdint.h>
#include <string.h>

#include "tokenizer/charref.h"

static inline int is_name_char(Py_UCS4 character) {
    /* the [^\t\n\f <&#;] class from the reference HTML5 charref regex */
    switch (character) {
    case '\t':
    case '\n':
    case '\x0c':
    case ' ':
    case '<':
    case '&':
    case '#':
    case ';':
        return 0;
    default:
        return 1;
    }
}

/* The output is staged in one buffer of length * 4 bytes. One-byte input
   starts in narrow mode, where the staging holds bytes (so clean spans move
   with plain memcpy) and switches to wide Py_UCS4 staging only when a
   reference produces a character above 0xFF; the switch widens the staged
   prefix in place, back to front, which is safe because slot 4 * i never
   overlaps unread byte j <= i. UCS-2 / UCS-4 input starts wide. "seen"
   accumulates an OR of every wide-mode code point instead of an exact
   maximum: PyUnicode_New only bins maxchar at 0x7F / 0xFF / 0xFFFF, OR never
   crosses one of those bins unless a character did, and the OR has no
   per-call branch. Values above 0xFFFF are clamped to 0x10FFFF before use. */
typedef struct {
    uint8_t *narrow; /* the staging buffer seen as bytes; valid while !is_wide */
    Py_UCS4 *wide;   /* the same buffer seen as code points; valid once is_wide */
    Py_ssize_t count;
    Py_UCS4 seen;
    int is_wide;
} sink_t;

static void sink_widen(sink_t *sink) {
    for (Py_ssize_t index = sink->count - 1; index >= 0; index--) {
        Py_UCS4 character = sink->narrow[index]; /* read before the slot is overwritten (index 0 overlaps) */
        sink->wide[index] = character;
        sink->seen |= character;
    }
    sink->is_wide = 1;
}

static inline void emit(sink_t *sink, Py_UCS4 character) {
    if (!sink->is_wide) {
        if (character <= 0xFF) {
            sink->narrow[sink->count++] = (uint8_t)character;
            return;
        }
        sink_widen(sink);
    }
    sink->wide[sink->count] = character;
    sink->seen |= character;
    sink->count++;
}

static Py_ssize_t find_amp(int kind, const void *data, Py_ssize_t from, Py_ssize_t length) {
    if (kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *input = (const uint8_t *)data;
        /* in reference-dense text the next '&' is a handful of characters away,
           so probe inline first; memchr's call cost only pays off on long spans */
        Py_ssize_t probe_end = from + 16 < length ? from + 16 : length;
        for (Py_ssize_t pos = from; pos < probe_end; pos++) {
            if (input[pos] == '&') {
                return pos;
            }
        }
        if (probe_end == length) {
            return -1;
        }
        const uint8_t *hit = memchr(input + probe_end, '&', (size_t)(length - probe_end));
        return hit == NULL ? -1 : (Py_ssize_t)(hit - input);
    }
    if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *input = (const uint16_t *)data;
        Py_ssize_t pos = from;
        while (pos + UCS2_LANES <= length) {
            uint64_t word;
            memcpy(&word, input + pos, sizeof(word));
            if (swar_haslane16(word, '&') != 0) {
                break; /* the scalar loop below pins down which lane it is */
            }
            pos += UCS2_LANES;
        }
        for (; pos < length; pos++) {
            if (input[pos] == '&') {
                return pos;
            }
        }
        return -1;
    }
    const uint32_t *input = (const uint32_t *)data;
    Py_ssize_t pos = from;
    while (pos + UCS4_LANES <= length) {
        uint64_t word;
        memcpy(&word, input + pos, sizeof(word));
        if (swar_haslane32(word, '&') != 0) {
            break; /* the scalar loop below pins down which lane it is */
        }
        pos += UCS4_LANES;
    }
    for (; pos < length; pos++) {
        if (input[pos] == '&') {
            return pos;
        }
    }
    return -1;
}

static void copy_span(int kind, const void *data, Py_ssize_t from, Py_ssize_t to, sink_t *sink) {
    if (kind == PyUnicode_1BYTE_KIND && !sink->is_wide) {
        /* narrow staging matches the input width, so the span is one memcpy */
        memcpy(sink->narrow + sink->count, (const uint8_t *)data + from, (size_t)(to - from));
        sink->count += to - from;
        return;
    }
    Py_UCS4 *dest = sink->wide + sink->count;
    Py_UCS4 bits = 0;
    if (kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *input = (const uint8_t *)data;
        for (Py_ssize_t pos = from; pos < to; pos++) {
            dest[pos - from] = input[pos];
            bits |= input[pos];
        }
    } else if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *input = (const uint16_t *)data;
        for (Py_ssize_t pos = from; pos < to; pos++) {
            dest[pos - from] = input[pos];
            bits |= input[pos];
        }
    } else {
        const uint32_t *input = (const uint32_t *)data;
        for (Py_ssize_t pos = from; pos < to; pos++) {
            dest[pos - from] = input[pos];
            bits |= input[pos];
        }
    }
    sink->count += to - from;
    sink->seen |= bits;
}

/* Returns the number of input characters consumed (including '&'), or 0 when no
   reference matches so the caller can emit a literal '&' and move on. */
static Py_ssize_t parse_charref(int kind, const void *data, Py_ssize_t length, Py_ssize_t amp_index, sink_t *sink) {
    Py_ssize_t pos = amp_index + 1;
    if (pos >= length) {
        return 0;
    }
    Py_UCS4 character = PyUnicode_READ(kind, data, pos);

    if (character == '#') {
        Py_ssize_t cursor = pos + 1;
        int hex = 0;
        if (cursor < length) {
            Py_UCS4 marker = PyUnicode_READ(kind, data, cursor);
            if (marker == 'x' || marker == 'X') {
                hex = 1;
                cursor++;
            }
        }
        Py_UCS4 num = 0;
        int overflow = 0;
        Py_ssize_t first_digit = cursor;
        while (cursor < length) {
            Py_UCS4 digit = PyUnicode_READ(kind, data, cursor);
            if (hex) {
                int value = charref_hex_value(digit);
                if (value < 0) {
                    break;
                }
                num = num * 16 + (Py_UCS4)value;
            } else {
                if (digit < '0' || digit > '9') {
                    break;
                }
                num = num * 10 + (digit - '0');
            }
            if (num > 0x110000) {
                num = 0x110000; /* cap so the > 0x10FFFF branch below fires regardless of further digits */
                overflow = 1;
            }
            cursor++;
        }
        if (cursor == first_digit) {
            return 0; /* "&#" with no digits is not a reference */
        }
        if (cursor < length && PyUnicode_READ(kind, data, cursor) == ';') {
            cursor++;
        }

        Py_UCS4 replacement;
        if (!overflow && charref_find_invalid(num, &replacement)) {
            emit(sink, replacement);
        } else if ((num >= 0xD800 && num <= 0xDFFF) || num > 0x10FFFF) {
            emit(sink, 0xFFFD);
        } else if (charref_is_invalid_codepoint(num)) {
            /* the spec maps these to the empty string, so emit nothing */
        } else {
            emit(sink, num);
        }
        return cursor - amp_index;
    }

    if (!is_name_char(character)) {
        return 0; /* "&;", "& ", "&&" and similar are literal */
    }

    Py_UCS4 name_chars[HTML5_MAX_NAME_LEN];
    char ascii[HTML5_MAX_NAME_LEN + 1];
    int name_len = 0;
    Py_ssize_t cursor = pos;
    while (cursor < length && name_len < HTML5_MAX_NAME_LEN) {
        Py_UCS4 candidate = PyUnicode_READ(kind, data, cursor);
        if (!is_name_char(candidate)) {
            break;
        }
        name_chars[name_len] = candidate;
        ascii[name_len] = (candidate < 128) ? (char)candidate : (char)0x01; /* 0x01 is never a table entry */
        name_len++;
        cursor++;
    }
    int semicolon = 0;
    if (cursor < length && PyUnicode_READ(kind, data, cursor) == ';') {
        ascii[name_len] = ';';
        semicolon = 1;
        cursor++;
    }
    int token_len = name_len + semicolon;

    const html5_entity *entity = charref_find_entity(ascii, token_len);
    int match_len = token_len;
    if (entity == NULL) {
        for (int prefix = token_len - 1; prefix >= 2; prefix--) {
            entity = charref_find_entity(ascii, prefix);
            if (entity != NULL) {
                match_len = prefix;
                break;
            }
        }
    }

    if (entity == NULL) {
        emit(sink, '&');
        for (int index = 0; index < name_len; index++) {
            emit(sink, name_chars[index]);
        }
        if (semicolon) {
            emit(sink, ';');
        }
        return cursor - amp_index;
    }

    emit(sink, entity->cp0);
    if (entity->cp1) {
        emit(sink, entity->cp1);
    }
    for (int index = match_len; index < token_len; index++) {
        emit(sink, (index < name_len) ? name_chars[index] : (Py_UCS4)';');
    }
    return cursor - amp_index;
}

PyObject *turbohtml_unescape(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "unescape() argument must be str");
        return NULL;
    }
    Py_ssize_t length = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);

    Py_ssize_t amp = find_amp(kind, data, 0, length);
    if (amp < 0) {
        return Py_NewRef(arg); /* without a '&' there is nothing to resolve; keep the original object */
    }

    /* reached only after finding '&', so length >= 1; unescape never lengthens
       the text, so length code points is a safe upper bound for the output */
    Py_UCS4 *out =
        PyMem_New(Py_UCS4, length); /* GCOVR_EXCL_BR_LINE: size-overflow guard unreachable for valid lengths */
    if (out == NULL) {              /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory();    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    sink_t sink = {(uint8_t *)out, out, 0, 0, kind != PyUnicode_1BYTE_KIND};
    Py_ssize_t pos = 0;
    while (1) {
        if (amp > pos) {
            copy_span(kind, data, pos, amp, &sink);
        }
        Py_ssize_t consumed = parse_charref(kind, data, length, amp, &sink);
        if (consumed == 0) {
            emit(&sink, '&');
            pos = amp + 1;
        } else {
            pos = amp + consumed;
        }
        if (pos >= length) {
            break;
        }
        /* references often sit back-to-back, so probe before paying for a search call */
        if (PyUnicode_READ(kind, data, pos) == '&') {
            amp = pos;
            continue;
        }
        amp = find_amp(kind, data, pos + 1, length);
        if (amp < 0) {
            copy_span(kind, data, pos, length, &sink);
            break;
        }
    }

    Py_ssize_t count = sink.count;
    Py_UCS4 seen = sink.seen;
    if (!sink.is_wide) {
        for (Py_ssize_t index = 0; index < count; index++) {
            seen |= sink.narrow[index];
        }
    }
    /* the OR accumulator can exceed 0x10FFFF only when an astral character was
       emitted, and everything above 0xFFFF lands in the same PyUnicode_New bin */
    PyObject *result = PyUnicode_New(count, th_str_maxchar(seen > 0xFFFF ? 0x10FFFF : seen));
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(out);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    void *result_data = PyUnicode_DATA(result);
    switch (PyUnicode_KIND(result)) {
    case PyUnicode_1BYTE_KIND:
        /* a 1-byte result implies narrow staging: wide mode is entered only by
           a >0xFF character or by wide input, whose >0xFF characters survive
           (references consume ASCII only), and either forces a wider result */
        memcpy(result_data, sink.narrow, (size_t)count);
        break;
    case PyUnicode_2BYTE_KIND: {
        uint16_t *dest = (uint16_t *)result_data;
        for (Py_ssize_t index = 0; index < count; index++) {
            dest[index] = (uint16_t)sink.wide[index];
        }
        break;
    }
    default:
        memcpy(result_data, sink.wide, (size_t)count * sizeof(Py_UCS4));
        break;
    }
    PyMem_Free(out);
    return result;
}
