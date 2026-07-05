/* WHATWG encoding sniffing for parse(bytes), #included into dom/node.c (the
   decode delegates to a CPython codec). Covers the BOM, the byte-order-mark
   override, and the "prescan a byte stream to determine its encoding" algorithm
   restricted to the first 1024 bytes, plus a label table mapping the full set of
   WHATWG encoding labels to a canonical name and a Python codec. A label outside
   the table is treated as unsupported, which the spec says to ignore, so the
   sniff falls through to the next step. */

#ifndef TURBOHTML_ENCODING_H
#define TURBOHTML_ENCODING_H

#include "core/ascii.h"

#include <string.h>

typedef struct {
    const char *label;     /* lowercased WHATWG label or alias */
    const char *canonical; /* the name Document.encoding reports */
    const char *codec;     /* the Python codec used to decode */
} th_encoding_entry;

/* The HTML "ASCII whitespace" bytes, as a table so the test is one branchless
   lookup. CR is included because byte input is not preprocessed here. */
static const unsigned char th_space_byte[256] = {
    ['\t'] = 1, ['\n'] = 1, ['\f'] = 1, ['\r'] = 1, [' '] = 1,
};

static int is_attr_space(unsigned char ch) {
    return th_space_byte[ch];
}

/* Every label in the WHATWG Encoding Standard's index, grouped by the encoding it
   names. canonical is the name Document.encoding reports; codec is the CPython
   codec used to decode. The legacy single-byte labels the spec forces onto
   windows-1252 (iso-8859-1, latin1, ascii) resolve here, as do the Turkish
   (iso-8859-9), Thai (iso-8859-11), and legacy CJK/Cyrillic aliases that older
   pages still declare. A label outside this set is unsupported. */
static const th_encoding_entry th_encoding_table[] = {
    {"unicode-1-1-utf-8", "UTF-8", "utf-8"},
    {"unicode11utf8", "UTF-8", "utf-8"},
    {"unicode20utf8", "UTF-8", "utf-8"},
    {"utf-8", "UTF-8", "utf-8"},
    {"utf8", "UTF-8", "utf-8"},
    {"x-unicode20utf8", "UTF-8", "utf-8"},
    {"866", "IBM866", "cp866"},
    {"cp866", "IBM866", "cp866"},
    {"csibm866", "IBM866", "cp866"},
    {"ibm866", "IBM866", "cp866"},
    {"csisolatin2", "ISO-8859-2", "iso-8859-2"},
    {"iso-8859-2", "ISO-8859-2", "iso-8859-2"},
    {"iso-ir-101", "ISO-8859-2", "iso-8859-2"},
    {"iso8859-2", "ISO-8859-2", "iso-8859-2"},
    {"iso88592", "ISO-8859-2", "iso-8859-2"},
    {"iso_8859-2", "ISO-8859-2", "iso-8859-2"},
    {"iso_8859-2:1987", "ISO-8859-2", "iso-8859-2"},
    {"l2", "ISO-8859-2", "iso-8859-2"},
    {"latin2", "ISO-8859-2", "iso-8859-2"},
    {"csisolatin3", "ISO-8859-3", "iso-8859-3"},
    {"iso-8859-3", "ISO-8859-3", "iso-8859-3"},
    {"iso-ir-109", "ISO-8859-3", "iso-8859-3"},
    {"iso8859-3", "ISO-8859-3", "iso-8859-3"},
    {"iso88593", "ISO-8859-3", "iso-8859-3"},
    {"iso_8859-3", "ISO-8859-3", "iso-8859-3"},
    {"iso_8859-3:1988", "ISO-8859-3", "iso-8859-3"},
    {"l3", "ISO-8859-3", "iso-8859-3"},
    {"latin3", "ISO-8859-3", "iso-8859-3"},
    {"csisolatin4", "ISO-8859-4", "iso-8859-4"},
    {"iso-8859-4", "ISO-8859-4", "iso-8859-4"},
    {"iso-ir-110", "ISO-8859-4", "iso-8859-4"},
    {"iso8859-4", "ISO-8859-4", "iso-8859-4"},
    {"iso88594", "ISO-8859-4", "iso-8859-4"},
    {"iso_8859-4", "ISO-8859-4", "iso-8859-4"},
    {"iso_8859-4:1988", "ISO-8859-4", "iso-8859-4"},
    {"l4", "ISO-8859-4", "iso-8859-4"},
    {"latin4", "ISO-8859-4", "iso-8859-4"},
    {"csisolatincyrillic", "ISO-8859-5", "iso-8859-5"},
    {"cyrillic", "ISO-8859-5", "iso-8859-5"},
    {"iso-8859-5", "ISO-8859-5", "iso-8859-5"},
    {"iso-ir-144", "ISO-8859-5", "iso-8859-5"},
    {"iso8859-5", "ISO-8859-5", "iso-8859-5"},
    {"iso88595", "ISO-8859-5", "iso-8859-5"},
    {"iso_8859-5", "ISO-8859-5", "iso-8859-5"},
    {"iso_8859-5:1988", "ISO-8859-5", "iso-8859-5"},
    {"arabic", "ISO-8859-6", "iso-8859-6"},
    {"asmo-708", "ISO-8859-6", "iso-8859-6"},
    {"csiso88596e", "ISO-8859-6", "iso-8859-6"},
    {"csiso88596i", "ISO-8859-6", "iso-8859-6"},
    {"csisolatinarabic", "ISO-8859-6", "iso-8859-6"},
    {"ecma-114", "ISO-8859-6", "iso-8859-6"},
    {"iso-8859-6", "ISO-8859-6", "iso-8859-6"},
    {"iso-8859-6-e", "ISO-8859-6", "iso-8859-6"},
    {"iso-8859-6-i", "ISO-8859-6", "iso-8859-6"},
    {"iso-ir-127", "ISO-8859-6", "iso-8859-6"},
    {"iso8859-6", "ISO-8859-6", "iso-8859-6"},
    {"iso88596", "ISO-8859-6", "iso-8859-6"},
    {"iso_8859-6", "ISO-8859-6", "iso-8859-6"},
    {"iso_8859-6:1987", "ISO-8859-6", "iso-8859-6"},
    {"csisolatingreek", "ISO-8859-7", "iso-8859-7"},
    {"ecma-118", "ISO-8859-7", "iso-8859-7"},
    {"elot_928", "ISO-8859-7", "iso-8859-7"},
    {"greek", "ISO-8859-7", "iso-8859-7"},
    {"greek8", "ISO-8859-7", "iso-8859-7"},
    {"iso-8859-7", "ISO-8859-7", "iso-8859-7"},
    {"iso-ir-126", "ISO-8859-7", "iso-8859-7"},
    {"iso8859-7", "ISO-8859-7", "iso-8859-7"},
    {"iso88597", "ISO-8859-7", "iso-8859-7"},
    {"iso_8859-7", "ISO-8859-7", "iso-8859-7"},
    {"iso_8859-7:1987", "ISO-8859-7", "iso-8859-7"},
    {"sun_eu_greek", "ISO-8859-7", "iso-8859-7"},
    {"csiso88598e", "ISO-8859-8", "iso-8859-8"},
    {"csisolatinhebrew", "ISO-8859-8", "iso-8859-8"},
    {"hebrew", "ISO-8859-8", "iso-8859-8"},
    {"iso-8859-8", "ISO-8859-8", "iso-8859-8"},
    {"iso-8859-8-e", "ISO-8859-8", "iso-8859-8"},
    {"iso-ir-138", "ISO-8859-8", "iso-8859-8"},
    {"iso8859-8", "ISO-8859-8", "iso-8859-8"},
    {"iso88598", "ISO-8859-8", "iso-8859-8"},
    {"iso_8859-8", "ISO-8859-8", "iso-8859-8"},
    {"iso_8859-8:1988", "ISO-8859-8", "iso-8859-8"},
    {"visual", "ISO-8859-8", "iso-8859-8"},
    {"csiso88598i", "ISO-8859-8-I", "iso-8859-8"},
    {"iso-8859-8-i", "ISO-8859-8-I", "iso-8859-8"},
    {"logical", "ISO-8859-8-I", "iso-8859-8"},
    {"csisolatin6", "ISO-8859-10", "iso-8859-10"},
    {"iso-8859-10", "ISO-8859-10", "iso-8859-10"},
    {"iso-ir-157", "ISO-8859-10", "iso-8859-10"},
    {"iso8859-10", "ISO-8859-10", "iso-8859-10"},
    {"iso885910", "ISO-8859-10", "iso-8859-10"},
    {"l6", "ISO-8859-10", "iso-8859-10"},
    {"latin6", "ISO-8859-10", "iso-8859-10"},
    {"iso-8859-13", "ISO-8859-13", "iso-8859-13"},
    {"iso8859-13", "ISO-8859-13", "iso-8859-13"},
    {"iso885913", "ISO-8859-13", "iso-8859-13"},
    {"iso-8859-14", "ISO-8859-14", "iso-8859-14"},
    {"iso8859-14", "ISO-8859-14", "iso-8859-14"},
    {"iso885914", "ISO-8859-14", "iso-8859-14"},
    {"csisolatin9", "ISO-8859-15", "iso-8859-15"},
    {"iso-8859-15", "ISO-8859-15", "iso-8859-15"},
    {"iso8859-15", "ISO-8859-15", "iso-8859-15"},
    {"iso885915", "ISO-8859-15", "iso-8859-15"},
    {"iso_8859-15", "ISO-8859-15", "iso-8859-15"},
    {"l9", "ISO-8859-15", "iso-8859-15"},
    {"iso-8859-16", "ISO-8859-16", "iso-8859-16"},
    {"cskoi8r", "KOI8-R", "koi8-r"},
    {"koi", "KOI8-R", "koi8-r"},
    {"koi8", "KOI8-R", "koi8-r"},
    {"koi8-r", "KOI8-R", "koi8-r"},
    {"koi8_r", "KOI8-R", "koi8-r"},
    {"koi8-ru", "KOI8-U", "koi8-u"},
    {"koi8-u", "KOI8-U", "koi8-u"},
    {"csmacintosh", "macintosh", "mac-roman"},
    {"mac", "macintosh", "mac-roman"},
    {"macintosh", "macintosh", "mac-roman"},
    {"x-mac-roman", "macintosh", "mac-roman"},
    {"dos-874", "windows-874", "cp874"},
    {"iso-8859-11", "windows-874", "cp874"},
    {"iso8859-11", "windows-874", "cp874"},
    {"iso885911", "windows-874", "cp874"},
    {"tis-620", "windows-874", "cp874"},
    {"windows-874", "windows-874", "cp874"},
    {"cp1250", "windows-1250", "cp1250"},
    {"windows-1250", "windows-1250", "cp1250"},
    {"x-cp1250", "windows-1250", "cp1250"},
    {"cp1251", "windows-1251", "cp1251"},
    {"windows-1251", "windows-1251", "cp1251"},
    {"x-cp1251", "windows-1251", "cp1251"},
    {"ansi_x3.4-1968", "windows-1252", "cp1252"},
    {"ascii", "windows-1252", "cp1252"},
    {"cp1252", "windows-1252", "cp1252"},
    {"cp819", "windows-1252", "cp1252"},
    {"csisolatin1", "windows-1252", "cp1252"},
    {"ibm819", "windows-1252", "cp1252"},
    {"iso-8859-1", "windows-1252", "cp1252"},
    {"iso-ir-100", "windows-1252", "cp1252"},
    {"iso8859-1", "windows-1252", "cp1252"},
    {"iso88591", "windows-1252", "cp1252"},
    {"iso_8859-1", "windows-1252", "cp1252"},
    {"iso_8859-1:1987", "windows-1252", "cp1252"},
    {"l1", "windows-1252", "cp1252"},
    {"latin1", "windows-1252", "cp1252"},
    {"us-ascii", "windows-1252", "cp1252"},
    {"windows-1252", "windows-1252", "cp1252"},
    {"x-cp1252", "windows-1252", "cp1252"},
    {"cp1253", "windows-1253", "cp1253"},
    {"windows-1253", "windows-1253", "cp1253"},
    {"x-cp1253", "windows-1253", "cp1253"},
    {"cp1254", "windows-1254", "cp1254"},
    {"csisolatin5", "windows-1254", "cp1254"},
    {"iso-8859-9", "windows-1254", "cp1254"},
    {"iso-ir-148", "windows-1254", "cp1254"},
    {"iso8859-9", "windows-1254", "cp1254"},
    {"iso88599", "windows-1254", "cp1254"},
    {"iso_8859-9", "windows-1254", "cp1254"},
    {"iso_8859-9:1989", "windows-1254", "cp1254"},
    {"l5", "windows-1254", "cp1254"},
    {"latin5", "windows-1254", "cp1254"},
    {"windows-1254", "windows-1254", "cp1254"},
    {"x-cp1254", "windows-1254", "cp1254"},
    {"cp1255", "windows-1255", "cp1255"},
    {"windows-1255", "windows-1255", "cp1255"},
    {"x-cp1255", "windows-1255", "cp1255"},
    {"cp1256", "windows-1256", "cp1256"},
    {"windows-1256", "windows-1256", "cp1256"},
    {"x-cp1256", "windows-1256", "cp1256"},
    {"cp1257", "windows-1257", "cp1257"},
    {"windows-1257", "windows-1257", "cp1257"},
    {"x-cp1257", "windows-1257", "cp1257"},
    {"cp1258", "windows-1258", "cp1258"},
    {"windows-1258", "windows-1258", "cp1258"},
    {"x-cp1258", "windows-1258", "cp1258"},
    {"x-mac-cyrillic", "x-mac-cyrillic", "mac-cyrillic"},
    {"x-mac-ukrainian", "x-mac-cyrillic", "mac-cyrillic"},
    {"chinese", "GBK", "gb18030"},
    {"csgb2312", "GBK", "gb18030"},
    {"csiso58gb231280", "GBK", "gb18030"},
    {"gb2312", "GBK", "gb18030"},
    {"gb_2312", "GBK", "gb18030"},
    {"gb_2312-80", "GBK", "gb18030"},
    {"gbk", "GBK", "gb18030"},
    {"iso-ir-58", "GBK", "gb18030"},
    {"x-gbk", "GBK", "gb18030"},
    {"gb18030", "gb18030", "gb18030"},
    {"big5", "Big5", "big5"},
    {"big5-hkscs", "Big5", "big5"},
    {"cn-big5", "Big5", "big5"},
    {"csbig5", "Big5", "big5"},
    {"x-x-big5", "Big5", "big5"},
    {"cseucpkdfmtjapanese", "EUC-JP", "euc_jp"},
    {"euc-jp", "EUC-JP", "euc_jp"},
    {"x-euc-jp", "EUC-JP", "euc_jp"},
    {"csiso2022jp", "ISO-2022-JP", "iso-2022-jp"},
    {"iso-2022-jp", "ISO-2022-JP", "iso-2022-jp"},
    {"csshiftjis", "Shift_JIS", "shift_jis"},
    {"ms932", "Shift_JIS", "shift_jis"},
    {"ms_kanji", "Shift_JIS", "shift_jis"},
    {"shift-jis", "Shift_JIS", "shift_jis"},
    {"shift_jis", "Shift_JIS", "shift_jis"},
    {"sjis", "Shift_JIS", "shift_jis"},
    {"windows-31j", "Shift_JIS", "shift_jis"},
    {"x-sjis", "Shift_JIS", "shift_jis"},
    {"cseuckr", "EUC-KR", "euc_kr"},
    {"csksc56011987", "EUC-KR", "euc_kr"},
    {"euc-kr", "EUC-KR", "euc_kr"},
    {"iso-ir-149", "EUC-KR", "euc_kr"},
    {"korean", "EUC-KR", "euc_kr"},
    {"ks_c_5601-1987", "EUC-KR", "euc_kr"},
    {"ks_c_5601-1989", "EUC-KR", "euc_kr"},
    {"ksc5601", "EUC-KR", "euc_kr"},
    {"ksc_5601", "EUC-KR", "euc_kr"},
    {"windows-949", "EUC-KR", "euc_kr"},
    {"csiso2022kr", "replacement", "replacement"},
    {"hz-gb-2312", "replacement", "replacement"},
    {"iso-2022-cn", "replacement", "replacement"},
    {"iso-2022-cn-ext", "replacement", "replacement"},
    {"iso-2022-kr", "replacement", "replacement"},
    {"replacement", "replacement", "replacement"},
    {"unicodefffe", "UTF-16BE", "utf-16-be"},
    {"utf-16be", "UTF-16BE", "utf-16-be"},
    {"csunicode", "UTF-16LE", "utf-16-le"},
    {"iso-10646-ucs-2", "UTF-16LE", "utf-16-le"},
    {"ucs-2", "UTF-16LE", "utf-16-le"},
    {"unicode", "UTF-16LE", "utf-16-le"},
    {"unicodefeff", "UTF-16LE", "utf-16-le"},
    {"utf-16", "UTF-16LE", "utf-16-le"},
    {"utf-16le", "UTF-16LE", "utf-16-le"},
    {"x-user-defined", "x-user-defined", "x-user-defined"},
};

/* Resolve a label (any case, surrounding ASCII whitespace allowed) to its table
   entry, or NULL when the label is not one we support. */
static const th_encoding_entry *th_encoding_lookup(const char *label, Py_ssize_t len) {
    char lowered[64];
    while (len > 0 && is_attr_space((unsigned char)*label)) {
        label++;
        len--;
    }
    while (len > 0 && is_attr_space((unsigned char)label[len - 1])) {
        len--;
    }
    if (len == 0 || len >= (Py_ssize_t)sizeof(lowered)) {
        return NULL;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        unsigned char ch = (unsigned char)label[index];
        lowered[index] = (char)lower_ascii(ch);
    }
    lowered[len] = '\0';
    for (size_t index = 0; index < sizeof(th_encoding_table) / sizeof(th_encoding_table[0]); index++) {
        if (strcmp(th_encoding_table[index].label, lowered) == 0) {
            return &th_encoding_table[index];
        }
    }
    return NULL;
}

/* A byte-order mark at the start of buf: its entry and the byte count to drop, or
   0 bytes (and NULL entry) when there is none. */
static Py_ssize_t th_encoding_bom(const unsigned char *buf, Py_ssize_t len, const th_encoding_entry **entry) {
    *entry = NULL;
    if (len >= 3 && buf[0] == 0xEF && buf[1] == 0xBB && buf[2] == 0xBF) {
        *entry = th_encoding_lookup("utf-8", 5);
        return 3;
    }
    if (len >= 2 && buf[0] == 0xFE && buf[1] == 0xFF) {
        *entry = th_encoding_lookup("utf-16be", 8);
        return 2;
    }
    if (len >= 2 && buf[0] == 0xFF && buf[1] == 0xFE) {
        *entry = th_encoding_lookup("utf-16le", 8);
        return 2;
    }
    return 0;
}

/* One byte-order-mark signature for the standalone detector: a prefix and the label
   to report. */
typedef struct {
    const char *label;
    unsigned char prefix[4];
    int length;
} th_detect_bom_sig;

/* Ordered so a UTF-32 mark is tested before the UTF-16 mark it starts with:
   FF FE 00 00 (UTF-32LE) must win over FF FE (UTF-16LE). The array+loop keeps the
   match a single memcmp per row rather than a chain of &&-guarded byte compares. */
static const th_detect_bom_sig th_detect_bom_table[] = {
    {"UTF-32LE", {0xFF, 0xFE, 0x00, 0x00}, 4},  {"UTF-32BE", {0x00, 0x00, 0xFE, 0xFF}, 4},
    {"UTF-8-SIG", {0xEF, 0xBB, 0xBF, 0x00}, 3}, {"UTF-16BE", {0xFE, 0xFF, 0x00, 0x00}, 2},
    {"UTF-16LE", {0xFF, 0xFE, 0x00, 0x00}, 2},
};

/* The label the standalone turbohtml.detect surface reports for a leading byte-order
   mark, or NULL when there is none. It spells a UTF-8 mark as UTF-8-SIG (so a caller
   knows to strip it) and recognizes the UTF-32 marks, neither of which the spec-locked
   parse-time th_encoding_bom above emits -- the WHATWG sniff has no UTF-8-SIG label and
   treats FF FE 00 00 as UTF-16LE, and that path stays byte-for-byte unchanged. */
static const char *th_detect_bom(const unsigned char *buf, Py_ssize_t len) {
    for (size_t index = 0; index < sizeof(th_detect_bom_table) / sizeof(th_detect_bom_table[0]); index++) {
        const th_detect_bom_sig *sig = &th_detect_bom_table[index];
        if (len >= sig->length && memcmp(buf, sig->prefix, (size_t)sig->length) == 0) {
            return sig->label;
        }
    }
    return NULL;
}

/* WHATWG "get an attribute" over [*pos, end): on success fills name/value (each
   lowercased, NUL-terminated, truncated to its buffer) and returns 1. Returns 0
   when the element's attributes are exhausted or the input ends mid-attribute (an
   unterminated name, unquoted value, or quoted value), matching html5lib, so a
   truncated meta does not yield an encoding. */
static int prescan_attribute(const unsigned char *buf, Py_ssize_t *pos, Py_ssize_t end, char *name, size_t name_cap,
                             char *value, size_t value_cap) {
    Py_ssize_t at = *pos;
    while (at < end && (is_attr_space(buf[at]) || buf[at] == '/')) {
        at++;
    }
    if (at >= end || buf[at] == '>') {
        *pos = at;
        return 0;
    }
    size_t name_len = 0;
    int has_value = 0;
    while (1) {
        if (at >= end) { /* the name ran to the end of input */
            *pos = at;
            return 0;
        }
        unsigned char ch = buf[at];
        if (ch == '=' && name_len > 0) {
            at++;
            has_value = 1;
            break;
        }
        if (is_attr_space(ch)) {
            while (at < end && is_attr_space(buf[at])) {
                at++;
            }
            if (at < end && buf[at] == '=') {
                at++;
                has_value = 1;
            }
            break;
        }
        if (ch == '/' || ch == '>') { /* a bare attribute */
            break;
        }
        if (name_len < name_cap - 1) {
            name[name_len++] = (char)lower_ascii(ch);
        }
        at++;
    }
    name[name_len] = '\0'; /* the append guard keeps name_len below name_cap */
    value[0] = '\0';
    if (!has_value) {
        *pos = at;
        return 1;
    }
    while (at < end && is_attr_space(buf[at])) {
        at++;
    }
    size_t value_len = 0;
    if (at < end && (buf[at] == '"' || buf[at] == '\'')) {
        unsigned char quote = buf[at++];
        while (at < end && buf[at] != quote) {
            unsigned char ch = buf[at++];
            if (value_len < value_cap - 1) {
                value[value_len++] = (char)lower_ascii(ch);
            }
        }
        if (at >= end) { /* unterminated quoted value */
            *pos = at;
            return 0;
        }
        at++;
    } else if (at < end && buf[at] == '>') {
        *pos = at;
        return 1;
    } else {
        while (at < end && !is_attr_space(buf[at]) && buf[at] != '>') {
            unsigned char ch = buf[at++];
            if (value_len < value_cap - 1) {
                value[value_len++] = (char)lower_ascii(ch);
            }
        }
        if (at >= end) { /* unquoted value ran to the end of input */
            *pos = at;
            return 0;
        }
    }
    value[value_len] = '\0'; /* the append guard keeps value_len below value_cap */
    *pos = at;
    return 1;
}

/* WHATWG "extract a character encoding from a meta element": the charset= token
   inside a Content-Type value. A quoted value with no closing quote yields no
   encoding. Returns the label (into out) or NULL. */
static const char *prescan_charset_in_content(const char *content, char *out, size_t out_cap) {
    const char *cursor = content;
    while ((cursor = strstr(cursor, "charset")) != NULL) {
        const char *after = cursor + 7;
        while (is_attr_space((unsigned char)*after)) {
            after++;
        }
        if (*after != '=') {
            cursor = after;
            continue;
        }
        after++;
        while (is_attr_space((unsigned char)*after)) {
            after++;
        }
        size_t out_len = 0;
        if (*after == '"' || *after == '\'') {
            char quote = *after++;
            while (*after != '\0' && *after != quote) {
                if (out_len < out_cap - 1) {
                    out[out_len++] = *after;
                }
                after++;
            }
            if (*after != quote) { /* no closing quote */
                return NULL;
            }
        } else {
            while (*after != '\0' && *after != ';' && !is_attr_space((unsigned char)*after)) {
                if (out_len < out_cap - 1) {
                    out[out_len++] = *after;
                }
                after++;
            }
        }
        out[out_len] = '\0';
        return out_len > 0 ? out : NULL;
    }
    return NULL;
}

/* Prescan the input for a usable <meta> encoding. Per the WHATWG "prescan a byte
   stream to determine its encoding" algorithm only the first 1024 bytes are examined,
   so a <meta> whose end reaches past that is not honored. A utf-16 label in this meta
   context is forced to utf-8. Returns the table entry, or NULL when none is found. */
static const th_encoding_entry *th_encoding_prescan(const unsigned char *buf, Py_ssize_t len) {
    Py_ssize_t pos = 0;
    char name[32];
    char value[128];
    if (len > 1024) {
        len = 1024;
    }
    while (pos < len) {
        if (pos + 4 <= len && memcmp(buf + pos, "<!--", 4) == 0) {
            pos += 4;
            while (pos + 3 <= len && memcmp(buf + pos, "-->", 3) != 0) {
                pos++;
            }
            pos += 3;
        } else if (pos + 6 <= len && buf[pos] == '<' && (buf[pos + 1] | 32) == 'm' && (buf[pos + 2] | 32) == 'e' &&
                   (buf[pos + 3] | 32) == 't' && (buf[pos + 4] | 32) == 'a' &&
                   (is_attr_space(buf[pos + 5]) || buf[pos + 5] == '/')) {
            pos += 5;
            int got_pragma = 0;
            int need_pragma = -1; /* -1 unset, 0 from a charset attr, 1 from a content attr */
            char label[64] = {0};
            while (prescan_attribute(buf, &pos, len, name, sizeof(name), value, sizeof(value))) {
                if (strcmp(name, "http-equiv") == 0) {
                    got_pragma = strcmp(value, "content-type") == 0;
                } else if (strcmp(name, "content") == 0 && label[0] == '\0') {
                    if (prescan_charset_in_content(value, label, sizeof(label)) != NULL) {
                        need_pragma = 1;
                    }
                } else if (strcmp(name, "charset") == 0) {
                    size_t copy = strlen(value);
                    copy = copy < sizeof(label) - 1 ? copy : sizeof(label) - 1;
                    memcpy(label, value, copy);
                    label[copy] = '\0';
                    need_pragma = 0;
                }
            }
            if (need_pragma == 0 || (need_pragma == 1 && got_pragma)) {
                const th_encoding_entry *entry = th_encoding_lookup(label, (Py_ssize_t)strlen(label));
                if (entry != NULL) {
                    /* WHATWG prescan overrides: a UTF-16 label means UTF-8, and an
                       x-user-defined label means windows-1252 (HTML §13.2.3.2) */
                    if (strcmp(entry->canonical, "UTF-16LE") == 0 || strcmp(entry->canonical, "UTF-16BE") == 0) {
                        entry = th_encoding_lookup("utf-8", 5);
                    } else if (strcmp(entry->canonical, "x-user-defined") == 0) {
                        entry = th_encoding_lookup("windows-1252", 12);
                    }
                    return entry;
                }
            }
        } else if (pos + 2 <= len && buf[pos] == '<' &&
                   (is_ascii_alpha(buf[pos + 1]) ||
                    (buf[pos + 1] == '/' && pos + 2 < len && is_ascii_alpha(buf[pos + 2])))) {
            /* a start or end tag: skip the tag name, then consume its attributes
               so a '>' inside a quoted value does not terminate the tag early */
            pos += buf[pos + 1] == '/' ? 2 : 1;
            while (pos < len && !is_attr_space(buf[pos]) && buf[pos] != '>' && buf[pos] != '/') {
                pos++;
            }
            while (prescan_attribute(buf, &pos, len, name, sizeof(name), value, sizeof(value))) {
                /* attributes of an unrelated tag are discarded */
            }
            pos += pos < len ? 1 : 0;
        } else if (pos + 2 <= len && buf[pos] == '<' && (buf[pos + 1] == '!' || buf[pos + 1] == '?')) {
            pos += 2;
            while (pos < len && buf[pos] != '>') {
                pos++;
            }
            pos += pos < len ? 1 : 0;
        } else {
            pos++;
        }
    }
    return NULL;
}

#endif /* TURBOHTML_ENCODING_H */
