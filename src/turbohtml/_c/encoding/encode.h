/* The WHATWG encoders for the labels a meta_charset declaration names, #included into dom/document.c after
   encoding/decode.h. Each encoder inverts its decoder: a code point is written with a byte sequence that the index
   decodes back to that code point, so the bytes read back as the same text under the declared label, and a code point
   no sequence decodes to is refused. The index tables are decode-side only, so each reverse map is built from the
   decoder's own tables on first use and kept for the life of the process, one per label and plane. */

#ifndef TURBOHTML_ENCODING_ENCODE_H
#define TURBOHTML_ENCODING_ENCODE_H

#include "encoding/decode.h"

#define TH_REVERSE_SIZE 65536

/* Store point's sequence in table unless the point is unmapped (0), lies outside plane, or an earlier sequence already
   holds it. The first sequence in the index wins, and each stored sequence decodes to the point it is stored under. */
static void reverse_put(uint32_t *table, Py_UCS4 plane, Py_UCS4 point, uint32_t packed) {
    if (point != 0 && point >> 16 == plane && table[point & 0xFFFF] == 0) {
        table[point & 0xFFFF] = packed;
    }
}

/* The four-byte GB18030 sequence for a pointer of the four-byte space, packed big-endian. */
static uint32_t gb18030_four(uint32_t pointer) {
    return (0x81 + pointer / 12600) << 24 | (0x30 + pointer / 1260 % 10) << 16 | (0x81 + pointer / 10 % 126) << 8 |
           (0x30 + pointer % 10);
}

static void reverse_fill(const th_encoding_entry *entry, Py_UCS4 plane, uint32_t *table) {
    switch (entry->kind) { /* GCOVR_EXCL_BR_LINE: the UTF, replacement and user-defined kinds never reach here */
    case TH_DEC_SINGLE_BYTE:
        for (Py_UCS4 byte = 0x80; byte <= 0xFF; byte++) {
            Py_UCS4 point = th_sb_index[entry->single][byte];
            if (point != 0xFFFD) { /* the index marks a byte it leaves undefined as U+FFFD, which no byte encodes */
                reverse_put(table, plane, point, byte);
            }
        }
        break;
    case TH_DEC_BIG5:
        for (int index = 0; index < TH_ENTRIES(th_big5_low); index++) {
            int pointer = index + TH_BIG5_FIRST;
            Py_UCS4 point = th_big5_low[index];
            if (pointer == TH_BIG5_COMBINING_MACRON_CAPITAL || pointer == TH_BIG5_COMBINING_CARON_CAPITAL ||
                pointer == TH_BIG5_COMBINING_MACRON_SMALL || pointer == TH_BIG5_COMBINING_CARON_SMALL) {
                continue; /* the decoder yields these as a base letter and a mark, never as one code point */
            }
            if ((th_big5_astral[index >> 3] >> (index & 7)) & 1) {
                point |= TH_BIG5_PLANE;
            }
            int row = pointer / 157;
            int column = pointer % 157;
            reverse_put(table, plane, point, (0x81 + row) << 8 | (column + (column < 63 ? 0x40 : 0x62)));
        }
        break;
    case TH_DEC_EUC_KR:
        for (int index = 0; index < TH_ENTRIES(th_euc_kr); index++) {
            reverse_put(table, plane, th_euc_kr[index], (0x81 + index / 190) << 8 | (0x41 + index % 190));
        }
        break;
    case TH_DEC_GB18030:
        reverse_put(table, plane, 0x20AC, 0x80);
        for (int index = 0; index < TH_ENTRIES(th_gb18030); index++) {
            int column = index % 190;
            reverse_put(table, plane, th_gb18030[index],
                        (0x81 + index / 190) << 8 | (column + (column < 63 ? 0x40 : 0x41)));
        }
        for (uint32_t pointer = 0; pointer <= 39419; pointer++) {
            Py_UCS4 point = 0;
            th_dec_gb18030_range(pointer, &point);
            reverse_put(table, plane, point, gb18030_four(pointer));
        }
        break;
    case TH_DEC_EUC_JP:
        for (int index = 0; index < 94 * 94; index++) {
            reverse_put(table, plane, th_jis0208[index], (0xA1 + index / 94) << 8 | (0xA1 + index % 94));
        }
        for (int index = 0; index < 94 * 94; index++) {
            reverse_put(table, plane, th_jis0212[index], 0x8F0000 | (0xA1 + index / 94) << 8 | (0xA1 + index % 94));
        }
        for (Py_UCS4 byte = 0xA1; byte <= 0xDF; byte++) {
            reverse_put(table, plane, 0xFF61 + byte - 0xA1, 0x8E00 | byte);
        }
        break;
    case TH_DEC_SHIFT_JIS:
        reverse_put(table, plane, 0x80, 0x80);
        for (int index = 0; index < TH_ENTRIES(th_jis0208); index++) {
            /* the decoder computes the private-use run, so the index's entries there never decode */
            Py_UCS4 point = 8836 <= index && index <= 10715 ? 0xE000 + (index - 8836) : th_jis0208[index];
            int row = index / 188;
            int column = index % 188;
            reverse_put(table, plane, point,
                        (row + (row < 31 ? 0x81 : 0xC1)) << 8 | (column + (column < 63 ? 0x40 : 0x41)));
        }
        for (Py_UCS4 byte = 0xA1; byte <= 0xDF; byte++) {
            reverse_put(table, plane, 0xFF61 + byte - 0xA1, byte);
        }
        break;
    case TH_DEC_ISO_2022_JP:
        for (int index = 0; index < 94 * 94; index++) {
            reverse_put(table, plane, th_jis0208[index], (0x21 + index / 94) << 8 | (0x21 + index % 94));
        }
        break;
    default:   /* GCOVR_EXCL_LINE: unreachable, see the switch */
        break; /* GCOVR_EXCL_LINE */
    }
}

/* The reverse map for entry's index at plane, built on first use and kept in the process for the rest of its life, so
   the map and the bytes it points at outlive the call; NULL with an exception set on allocation failure. Keyed by the
   decoder kind and the single-byte row, so labels that share an index (GBK and GB18030) share a map. */
static const uint32_t *reverse_table(const th_encoding_entry *entry, Py_UCS4 plane) {
    static PyObject *tables = NULL;
    if (tables == NULL) {
        tables = PyDict_New();
        if (tables == NULL) { /* GCOVR_EXCL_LINE: dict allocation cannot be forced from a test */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
    }
    PyObject *key = PyLong_FromLong(((long)entry->kind << 8 | entry->single) << 5 | (long)plane);
    if (key == NULL) { /* GCOVR_EXCL_LINE: integer allocation cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    PyObject *table = PyDict_GetItemWithError(tables, key);
    if (table == NULL) {
        if (PyErr_Occurred()) { /* GCOVR_EXCL_LINE: a hash of a small int cannot fail */
            Py_DECREF(key);     /* GCOVR_EXCL_LINE */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
        PyObject *built = PyBytes_FromStringAndSize(NULL, TH_REVERSE_SIZE * (Py_ssize_t)sizeof(uint32_t));
        if (built == NULL) { /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(key);  /* GCOVR_EXCL_LINE */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        memset(PyBytes_AS_STRING(built), 0, TH_REVERSE_SIZE * sizeof(uint32_t));
        reverse_fill(entry, plane, (uint32_t *)PyBytes_AS_STRING(built));
        table = PyDict_SetDefault(tables, key, built);
        Py_DECREF(built);
        if (table == NULL) { /* GCOVR_EXCL_LINE: the insert fails only on allocation */
            Py_DECREF(key);  /* GCOVR_EXCL_LINE */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(key);
    return (const uint32_t *)PyBytes_AS_STRING(table);
}

/* Refuse point at index of the serialized text: the error names the character, its code point and its position. */
static PyObject *refuse_point(Py_ssize_t index, Py_UCS4 point, const char *encoding, const char *label) {
    PyObject *character = PyUnicode_FromOrdinal((int)point);
    if (character == NULL) { /* GCOVR_EXCL_LINE: a code point the str holds always converts */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    /* PyUnicode_FromFormat takes %X only from 3.12 on, so the code point is formatted here */
    char hex[9];
    snprintf(hex, sizeof hex, "%04X", (unsigned)point);
    PyErr_Format(PyExc_ValueError, "meta_charset cannot declare %s for %s: %R (U+%s) at position %zd has no byte in %s",
                 label, encoding, character, hex, index, label);
    Py_DECREF(character);
    return NULL;
}

/* Write the escape that makes intermediate and final the designation the decoder reads next, and return its length. */
static Py_ssize_t iso_escape(unsigned char *out, unsigned char intermediate, unsigned char final) {
    out[0] = 0x1B;
    out[1] = intermediate;
    out[2] = final;
    return 3;
}

/* Write packed's sequence, lead byte first, and return its length: every multi-byte sequence leads with a byte of 0x81
   or above, so the length follows from the packed value's magnitude. */
static Py_ssize_t write_packed(unsigned char *out, uint32_t packed) {
    Py_ssize_t length = packed > 0xFFFFFF ? 4 : packed > 0xFFFF ? 3 : packed > 0xFF ? 2 : 1;
    for (Py_ssize_t index = 0; index < length; index++) {
        out[index] = (unsigned char)(packed >> (8 * (length - 1 - index)));
    }
    return length;
}

static uint32_t stateless_packed(const th_encoding_entry *entry, const uint32_t *bmp, const uint32_t *astral,
                                 Py_UCS4 point) {
    if (point < 0x10000) {
        return bmp[point];
    }
    if (entry->kind == TH_DEC_GB18030) { /* the four-byte form maps every code point above U+FFFF in one linear run */
        return gb18030_four(189000 + (point - 0x10000));
    }
    if (point >> 16 == 2 && astral != NULL) {
        return astral[point & 0xFFFF];
    }
    return 0;
}

/* Encode text with an encoding whose sequences never change state: one lookup per code point, four bytes at most. */
static PyObject *encode_stateless(const th_encoding_entry *entry, PyObject *text, const char *encoding,
                                  const char *label) {
    const uint32_t *bmp = reverse_table(entry, 0);
    /* GCOVR_EXCL_START: the table allocations fail only on memory exhaustion */
    if (bmp == NULL) {
        return NULL;
    }
    const uint32_t *astral = entry->kind == TH_DEC_BIG5 ? reverse_table(entry, 2) : NULL;
    if (entry->kind == TH_DEC_BIG5 && astral == NULL) {
        return NULL;
    }
    /* GCOVR_EXCL_STOP */
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    unsigned char *buffer = PyMem_Malloc((size_t)len * 4 + 1);
    if (buffer == NULL) {        /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t used = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 point = PyUnicode_READ_CHAR(text, index);
        if (point < 0x80) {
            buffer[used++] = (unsigned char)point;
            continue;
        }
        uint32_t packed = stateless_packed(entry, bmp, astral, point);
        if (packed == 0) {
            PyMem_Free(buffer);
            return refuse_point(index, point, encoding, label);
        }
        used += write_packed(buffer + used, packed);
    }
    PyObject *encoded = PyBytes_FromStringAndSize((const char *)buffer, used);
    PyMem_Free(buffer);
    return encoded;
}

/* ISO-2022-JP switches between ASCII, Roman (U+00A5 and U+203E) and JIS X 0208, each switch an escape that the decoder
   reads before the character after it, so an escape never stands alone. U+000E, U+000F and U+001B are refused: the
   decoder reads them as the shift codes and escapes the text was not meant to carry. Only a declaring document reaches
   here, and its markup ends with a closing tag, so no switch is left open at the end. */
static PyObject *encode_iso2022_jp(const th_encoding_entry *entry, PyObject *text, const char *encoding,
                                   const char *label) {
    const uint32_t *jis = reverse_table(entry, 0);
    if (jis == NULL) { /* GCOVR_EXCL_LINE: the table allocation fails only on memory exhaustion */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    unsigned char *buffer = PyMem_Malloc((size_t)len * 5 + 3);
    if (buffer == NULL) {        /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    enum { ASCII_MODE, ROMAN_MODE, JIS0208_MODE } mode = ASCII_MODE;
    Py_ssize_t used = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 point = PyUnicode_READ_CHAR(text, index);
        if (point == 0x0E || point == 0x0F || point == 0x1B) {
            PyMem_Free(buffer);
            return refuse_point(index, point, encoding, label);
        }
        if (point < 0x80) {
            if (mode != ASCII_MODE) {
                used += iso_escape(buffer + used, '(', 'B');
                mode = ASCII_MODE;
            }
            buffer[used++] = (unsigned char)point;
        } else if (point == 0xA5 || point == 0x203E) {
            if (mode != ROMAN_MODE) {
                used += iso_escape(buffer + used, '(', 'J');
                mode = ROMAN_MODE;
            }
            buffer[used++] = point == 0xA5 ? 0x5C : 0x7E;
        } else {
            uint32_t packed = point < 0x10000 ? jis[point] : 0;
            if (packed == 0) {
                PyMem_Free(buffer);
                return refuse_point(index, point, encoding, label);
            }
            if (mode != JIS0208_MODE) {
                used += iso_escape(buffer + used, '$', 'B');
                mode = JIS0208_MODE;
            }
            buffer[used++] = (unsigned char)(packed >> 8);
            buffer[used++] = (unsigned char)packed;
        }
    }
    PyObject *encoded = PyBytes_FromStringAndSize((const char *)buffer, used);
    PyMem_Free(buffer);
    return encoded;
}

/* Encode text for a meta_charset declaration of label: the bytes the label's WHATWG encoder writes, so they read back
   as text under the label. encoding is the codec the caller asked for and is named in the error, as is a code point
   the label has no byte for, with its position. */
PyObject *declared_encode(const char *label, PyObject *text, const char *encoding) {
    const th_encoding_entry *entry = th_encoding_lookup(label, (Py_ssize_t)strlen(label));
    if (entry->kind == TH_DEC_ISO_2022_JP) {
        return encode_iso2022_jp(entry, text, encoding, label);
    }
    return encode_stateless(entry, text, encoding, label);
}

#endif /* TURBOHTML_ENCODING_ENCODE_H */
