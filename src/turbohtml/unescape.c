/* HTML unescaping.

   unescape() makes a single pass over the input, resolving numeric and named
   character references per the HTML5 rules. Named references use binary search
   over the generated table, with longest-prefix matching for references that
   omit the trailing semicolon; numeric references apply the spec's correction
   tables. The output is built into a Py_UCS4 buffer because unescape never
   lengthens the text, so the input length is a safe upper bound. */

#include "turbohtml.h"

#include <stdint.h>

#include "charref.h"

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

static inline void emit(Py_UCS4 *out, Py_ssize_t *count, Py_UCS4 *maxchar, Py_UCS4 character) {
    out[*count] = character;
    if (character > *maxchar) {
        *maxchar = character;
    }
    (*count)++;
}

/* Returns the number of input characters consumed (including '&'), or 0 when no
   reference matches so the caller can emit a literal '&' and move on. */
static Py_ssize_t parse_charref(int kind, const void *data, Py_ssize_t length, Py_ssize_t amp_index, Py_UCS4 *out,
                                Py_ssize_t *count, Py_UCS4 *maxchar) {
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
            emit(out, count, maxchar, replacement);
        } else if ((num >= 0xD800 && num <= 0xDFFF) || num > 0x10FFFF) {
            emit(out, count, maxchar, 0xFFFD);
        } else if (charref_is_invalid_codepoint(num)) {
            /* the spec maps these to the empty string, so emit nothing */
        } else {
            emit(out, count, maxchar, num);
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
        emit(out, count, maxchar, '&');
        for (int index = 0; index < name_len; index++) {
            emit(out, count, maxchar, name_chars[index]);
        }
        if (semicolon) {
            emit(out, count, maxchar, ';');
        }
        return cursor - amp_index;
    }

    emit(out, count, maxchar, entity->cp0);
    if (entity->cp1) {
        emit(out, count, maxchar, entity->cp1);
    }
    for (int index = match_len; index < token_len; index++) {
        emit(out, count, maxchar, (index < name_len) ? name_chars[index] : (Py_UCS4)';');
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

    if (PyUnicode_FindChar(arg, '&', 0, length, 1) < 0) {
        return Py_NewRef(arg); /* without a '&' there is nothing to resolve; keep the original object */
    }

    /* reached only after finding '&', so length >= 1; unescape never lengthens
       the text, so length code points is a safe upper bound for the output */
    Py_UCS4 *out =
        PyMem_New(Py_UCS4, length); /* GCOVR_EXCL_BR_LINE: size-overflow guard unreachable for valid lengths */
    if (out == NULL) {              /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = 0;
    Py_UCS4 maxchar = 0;
    Py_ssize_t pos = 0;
    while (pos < length) {
        Py_UCS4 character = PyUnicode_READ(kind, data, pos);
        if (character != '&') {
            emit(out, &count, &maxchar, character);
            pos++;
            continue;
        }
        Py_ssize_t consumed = parse_charref(kind, data, length, pos, out, &count, &maxchar);
        if (consumed == 0) {
            emit(out, &count, &maxchar, '&');
            pos++;
        } else {
            pos += consumed;
        }
    }

    PyObject *result = PyUnicode_New(count, maxchar);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(out);  /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    int result_kind = PyUnicode_KIND(result);
    void *result_data = PyUnicode_DATA(result);
    for (Py_ssize_t index = 0; index < count; index++) {
        PyUnicode_WRITE(result_kind, result_data, index, out[index]);
    }
    PyMem_Free(out);
    return result;
}
