/* WHATWG encoding sniffing for parse(bytes), #included into dom/node.c. Covers
   the BOM, the byte-order-mark override, and the "prescan a byte stream to
   determine its encoding" algorithm restricted to the first 1024 bytes, plus a
   label table mapping the full set of WHATWG encoding labels to a canonical name
   and the decoder that reads it (encoding/decode.h). A label outside the table is
   treated as unsupported, which the spec says to ignore, so the sniff falls
   through to the next step. */

#ifndef TURBOHTML_ENCODING_H
#define TURBOHTML_ENCODING_H

#include "core/ascii.h"
#include "data/encoding_tables.h"

#include <string.h>

/* The decoder an entry is read with. UTF-8 and the two UTF-16 forms delegate to
   CPython, whose decoders agree with the spec byte for byte, including the
   maximal-subpart error handling; every legacy encoding is decoded natively,
   because no CPython codec implements the WHATWG index or its error resync. */
typedef enum {
    TH_DEC_UTF8,
    TH_DEC_UTF16LE,
    TH_DEC_UTF16BE,
    TH_DEC_SINGLE_BYTE,
    TH_DEC_BIG5,
    TH_DEC_EUC_JP,
    TH_DEC_EUC_KR,
    TH_DEC_GB18030,
    TH_DEC_SHIFT_JIS,
    TH_DEC_ISO_2022_JP,
    TH_DEC_REPLACEMENT,
    TH_DEC_X_USER_DEFINED,
} th_dec_kind;

typedef struct {
    const char *label;     /* lowercased WHATWG label or alias */
    const char *canonical; /* the name Document.encoding reports */
    uint8_t kind;          /* th_dec_kind */
    uint8_t single;        /* the th_sb_index row, when kind is TH_DEC_SINGLE_BYTE */
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
   names. canonical is the name Document.encoding reports; kind and single select
   the decoder. The legacy single-byte labels the spec forces onto windows-1252
   (iso-8859-1, latin1, ascii) resolve here, as do the Turkish (iso-8859-9), Thai
   (iso-8859-11), and legacy CJK/Cyrillic aliases that older pages still declare.
   ISO-8859-8-I shares ISO-8859-8's index: they differ only in bidi handling, which
   decoding does not model. A label outside this set is unsupported. */
static const th_encoding_entry th_encoding_table[] = {
    {"unicode-1-1-utf-8", "UTF-8", TH_DEC_UTF8, 0},
    {"unicode11utf8", "UTF-8", TH_DEC_UTF8, 0},
    {"unicode20utf8", "UTF-8", TH_DEC_UTF8, 0},
    {"utf-8", "UTF-8", TH_DEC_UTF8, 0},
    {"utf8", "UTF-8", TH_DEC_UTF8, 0},
    {"x-unicode20utf8", "UTF-8", TH_DEC_UTF8, 0},
    {"866", "IBM866", TH_DEC_SINGLE_BYTE, TH_SB_IBM866},
    {"cp866", "IBM866", TH_DEC_SINGLE_BYTE, TH_SB_IBM866},
    {"csibm866", "IBM866", TH_DEC_SINGLE_BYTE, TH_SB_IBM866},
    {"ibm866", "IBM866", TH_DEC_SINGLE_BYTE, TH_SB_IBM866},
    {"csisolatin2", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"iso-8859-2", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"iso-ir-101", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"iso8859-2", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"iso88592", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"iso_8859-2", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"iso_8859-2:1987", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"l2", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"latin2", "ISO-8859-2", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_2},
    {"csisolatin3", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"iso-8859-3", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"iso-ir-109", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"iso8859-3", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"iso88593", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"iso_8859-3", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"iso_8859-3:1988", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"l3", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"latin3", "ISO-8859-3", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_3},
    {"csisolatin4", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"iso-8859-4", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"iso-ir-110", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"iso8859-4", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"iso88594", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"iso_8859-4", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"iso_8859-4:1988", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"l4", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"latin4", "ISO-8859-4", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_4},
    {"csisolatincyrillic", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"cyrillic", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"iso-8859-5", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"iso-ir-144", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"iso8859-5", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"iso88595", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"iso_8859-5", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"iso_8859-5:1988", "ISO-8859-5", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_5},
    {"arabic", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"asmo-708", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"csiso88596e", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"csiso88596i", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"csisolatinarabic", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"ecma-114", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso-8859-6", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso-8859-6-e", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso-8859-6-i", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso-ir-127", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso8859-6", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso88596", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso_8859-6", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"iso_8859-6:1987", "ISO-8859-6", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_6},
    {"csisolatingreek", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"ecma-118", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"elot_928", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"greek", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"greek8", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"iso-8859-7", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"iso-ir-126", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"iso8859-7", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"iso88597", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"iso_8859-7", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"iso_8859-7:1987", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"sun_eu_greek", "ISO-8859-7", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_7},
    {"csiso88598e", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"csisolatinhebrew", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"hebrew", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso-8859-8", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso-8859-8-e", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso-ir-138", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso8859-8", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso88598", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso_8859-8", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso_8859-8:1988", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"visual", "ISO-8859-8", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"csiso88598i", "ISO-8859-8-I", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"iso-8859-8-i", "ISO-8859-8-I", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"logical", "ISO-8859-8-I", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_8},
    {"csisolatin6", "ISO-8859-10", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_10},
    {"iso-8859-10", "ISO-8859-10", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_10},
    {"iso-ir-157", "ISO-8859-10", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_10},
    {"iso8859-10", "ISO-8859-10", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_10},
    {"iso885910", "ISO-8859-10", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_10},
    {"l6", "ISO-8859-10", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_10},
    {"latin6", "ISO-8859-10", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_10},
    {"iso-8859-13", "ISO-8859-13", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_13},
    {"iso8859-13", "ISO-8859-13", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_13},
    {"iso885913", "ISO-8859-13", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_13},
    {"iso-8859-14", "ISO-8859-14", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_14},
    {"iso8859-14", "ISO-8859-14", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_14},
    {"iso885914", "ISO-8859-14", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_14},
    {"csisolatin9", "ISO-8859-15", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_15},
    {"iso-8859-15", "ISO-8859-15", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_15},
    {"iso8859-15", "ISO-8859-15", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_15},
    {"iso885915", "ISO-8859-15", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_15},
    {"iso_8859-15", "ISO-8859-15", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_15},
    {"l9", "ISO-8859-15", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_15},
    {"iso-8859-16", "ISO-8859-16", TH_DEC_SINGLE_BYTE, TH_SB_ISO_8859_16},
    {"cskoi8r", "KOI8-R", TH_DEC_SINGLE_BYTE, TH_SB_KOI8_R},
    {"koi", "KOI8-R", TH_DEC_SINGLE_BYTE, TH_SB_KOI8_R},
    {"koi8", "KOI8-R", TH_DEC_SINGLE_BYTE, TH_SB_KOI8_R},
    {"koi8-r", "KOI8-R", TH_DEC_SINGLE_BYTE, TH_SB_KOI8_R},
    {"koi8_r", "KOI8-R", TH_DEC_SINGLE_BYTE, TH_SB_KOI8_R},
    {"koi8-ru", "KOI8-U", TH_DEC_SINGLE_BYTE, TH_SB_KOI8_U},
    {"koi8-u", "KOI8-U", TH_DEC_SINGLE_BYTE, TH_SB_KOI8_U},
    {"csmacintosh", "macintosh", TH_DEC_SINGLE_BYTE, TH_SB_MACINTOSH},
    {"mac", "macintosh", TH_DEC_SINGLE_BYTE, TH_SB_MACINTOSH},
    {"macintosh", "macintosh", TH_DEC_SINGLE_BYTE, TH_SB_MACINTOSH},
    {"x-mac-roman", "macintosh", TH_DEC_SINGLE_BYTE, TH_SB_MACINTOSH},
    {"dos-874", "windows-874", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_874},
    {"iso-8859-11", "windows-874", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_874},
    {"iso8859-11", "windows-874", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_874},
    {"iso885911", "windows-874", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_874},
    {"tis-620", "windows-874", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_874},
    {"windows-874", "windows-874", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_874},
    {"cp1250", "windows-1250", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1250},
    {"windows-1250", "windows-1250", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1250},
    {"x-cp1250", "windows-1250", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1250},
    {"cp1251", "windows-1251", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1251},
    {"windows-1251", "windows-1251", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1251},
    {"x-cp1251", "windows-1251", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1251},
    {"ansi_x3.4-1968", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"ascii", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"cp1252", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"cp819", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"csisolatin1", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"ibm819", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"iso-8859-1", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"iso-ir-100", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"iso8859-1", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"iso88591", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"iso_8859-1", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"iso_8859-1:1987", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"l1", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"latin1", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"us-ascii", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"windows-1252", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"x-cp1252", "windows-1252", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1252},
    {"cp1253", "windows-1253", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1253},
    {"windows-1253", "windows-1253", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1253},
    {"x-cp1253", "windows-1253", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1253},
    {"cp1254", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"csisolatin5", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"iso-8859-9", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"iso-ir-148", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"iso8859-9", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"iso88599", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"iso_8859-9", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"iso_8859-9:1989", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"l5", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"latin5", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"windows-1254", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"x-cp1254", "windows-1254", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1254},
    {"cp1255", "windows-1255", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1255},
    {"windows-1255", "windows-1255", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1255},
    {"x-cp1255", "windows-1255", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1255},
    {"cp1256", "windows-1256", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1256},
    {"windows-1256", "windows-1256", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1256},
    {"x-cp1256", "windows-1256", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1256},
    {"cp1257", "windows-1257", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1257},
    {"windows-1257", "windows-1257", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1257},
    {"x-cp1257", "windows-1257", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1257},
    {"cp1258", "windows-1258", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1258},
    {"windows-1258", "windows-1258", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1258},
    {"x-cp1258", "windows-1258", TH_DEC_SINGLE_BYTE, TH_SB_WINDOWS_1258},
    {"x-mac-cyrillic", "x-mac-cyrillic", TH_DEC_SINGLE_BYTE, TH_SB_X_MAC_CYRILLIC},
    {"x-mac-ukrainian", "x-mac-cyrillic", TH_DEC_SINGLE_BYTE, TH_SB_X_MAC_CYRILLIC},
    {"chinese", "GBK", TH_DEC_GB18030, 0},
    {"csgb2312", "GBK", TH_DEC_GB18030, 0},
    {"csiso58gb231280", "GBK", TH_DEC_GB18030, 0},
    {"gb2312", "GBK", TH_DEC_GB18030, 0},
    {"gb_2312", "GBK", TH_DEC_GB18030, 0},
    {"gb_2312-80", "GBK", TH_DEC_GB18030, 0},
    {"gbk", "GBK", TH_DEC_GB18030, 0},
    {"iso-ir-58", "GBK", TH_DEC_GB18030, 0},
    {"x-gbk", "GBK", TH_DEC_GB18030, 0},
    {"gb18030", "gb18030", TH_DEC_GB18030, 0},
    {"big5", "Big5", TH_DEC_BIG5, 0},
    {"big5-hkscs", "Big5", TH_DEC_BIG5, 0},
    {"cn-big5", "Big5", TH_DEC_BIG5, 0},
    {"csbig5", "Big5", TH_DEC_BIG5, 0},
    {"x-x-big5", "Big5", TH_DEC_BIG5, 0},
    {"cseucpkdfmtjapanese", "EUC-JP", TH_DEC_EUC_JP, 0},
    {"euc-jp", "EUC-JP", TH_DEC_EUC_JP, 0},
    {"x-euc-jp", "EUC-JP", TH_DEC_EUC_JP, 0},
    {"csiso2022jp", "ISO-2022-JP", TH_DEC_ISO_2022_JP, 0},
    {"iso-2022-jp", "ISO-2022-JP", TH_DEC_ISO_2022_JP, 0},
    {"csshiftjis", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"ms932", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"ms_kanji", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"shift-jis", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"shift_jis", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"sjis", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"windows-31j", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"x-sjis", "Shift_JIS", TH_DEC_SHIFT_JIS, 0},
    {"cseuckr", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"csksc56011987", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"euc-kr", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"iso-ir-149", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"korean", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"ks_c_5601-1987", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"ks_c_5601-1989", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"ksc5601", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"ksc_5601", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"windows-949", "EUC-KR", TH_DEC_EUC_KR, 0},
    {"csiso2022kr", "replacement", TH_DEC_REPLACEMENT, 0},
    {"hz-gb-2312", "replacement", TH_DEC_REPLACEMENT, 0},
    {"iso-2022-cn", "replacement", TH_DEC_REPLACEMENT, 0},
    {"iso-2022-cn-ext", "replacement", TH_DEC_REPLACEMENT, 0},
    {"iso-2022-kr", "replacement", TH_DEC_REPLACEMENT, 0},
    {"replacement", "replacement", TH_DEC_REPLACEMENT, 0},
    {"unicodefffe", "UTF-16BE", TH_DEC_UTF16BE, 0},
    {"utf-16be", "UTF-16BE", TH_DEC_UTF16BE, 0},
    {"csunicode", "UTF-16LE", TH_DEC_UTF16LE, 0},
    {"iso-10646-ucs-2", "UTF-16LE", TH_DEC_UTF16LE, 0},
    {"ucs-2", "UTF-16LE", TH_DEC_UTF16LE, 0},
    {"unicode", "UTF-16LE", TH_DEC_UTF16LE, 0},
    {"unicodefeff", "UTF-16LE", TH_DEC_UTF16LE, 0},
    {"utf-16", "UTF-16LE", TH_DEC_UTF16LE, 0},
    {"utf-16le", "UTF-16LE", TH_DEC_UTF16LE, 0},
    {"x-user-defined", "x-user-defined", TH_DEC_X_USER_DEFINED, 0},
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

/* The encoding a <meta> label names, after the two rewrites the spec applies wherever a
   document declares its own encoding (HTML §13.2.3.2, and again in "changing the encoding
   while parsing"): a UTF-16 label cannot be right, because an ASCII-compatible read of the
   bytes is what surfaced it, and x-user-defined means windows-1252. */
static const th_encoding_entry *th_encoding_declared(const th_encoding_entry *entry) {
    if (strcmp(entry->canonical, "UTF-16LE") == 0 || strcmp(entry->canonical, "UTF-16BE") == 0) {
        return th_encoding_lookup("utf-8", 5);
    }
    if (strcmp(entry->canonical, "x-user-defined") == 0) {
        return th_encoding_lookup("windows-1252", 12);
    }
    return entry;
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
            if (value_len < value_cap - 1) { /* GCOVR_EXCL_BR_LINE: value outgrows the 1024-byte window */
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
            if (value_len < value_cap - 1) { /* GCOVR_EXCL_BR_LINE: value outgrows the 1024-byte window */
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
    /* the spec caps nothing but the 1024-byte prescan window, so a value can fill it; a smaller buffer would drop the
       "charset=" of a long content attribute and silently fall back to windows-1252 */
    char value[1025];
    if (len > 1024) {
        len = 1024;
    }
    while (pos < len) {
        if (pos + 4 <= len && memcmp(buf + pos, "<!--", 4) == 0) {
            /* the spec closes the comment at the first '>' preceded by two '-' that comes after the '<', so the two
               dashes of "<!--" may double as the "--" of "-->" and "<!-->" is a complete comment */
            Py_ssize_t at = pos + 2;
            while (at + 3 <= len && memcmp(buf + at, "-->", 3) != 0) {
                at++;
            }
            pos = at + 3;
        } else if (pos + 6 <= len && buf[pos] == '<' && (buf[pos + 1] | 32) == 'm' && (buf[pos + 2] | 32) == 'e' &&
                   (buf[pos + 3] | 32) == 't' && (buf[pos + 4] | 32) == 'a' &&
                   (is_attr_space(buf[pos + 5]) || buf[pos + 5] == '/')) {
            pos += 5;
            int got_pragma = 0;
            int need_pragma = -1; /* -1 unset, 0 from a charset attr, 1 from a content attr */
            char label[64] = {0};
            /* the spec builds an attribute list and skips an attribute whose name is already in it, so the first
               occurrence of each name wins and a repeated charset or http-equiv cannot override it */
            int seen_http_equiv = 0;
            int seen_content = 0;
            int seen_charset = 0;
            while (prescan_attribute(buf, &pos, len, name, sizeof(name), value, sizeof(value))) {
                if (strcmp(name, "http-equiv") == 0 && !seen_http_equiv) {
                    seen_http_equiv = 1;
                    got_pragma = strcmp(value, "content-type") == 0;
                } else if (strcmp(name, "content") == 0 && !seen_content) {
                    seen_content = 1;
                    if (label[0] == '\0' && prescan_charset_in_content(value, label, sizeof(label)) != NULL) {
                        need_pragma = 1;
                    }
                } else if (strcmp(name, "charset") == 0 && !seen_charset) {
                    seen_charset = 1;
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
                    return th_encoding_declared(entry);
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
        } else if (pos + 2 <= len && buf[pos] == '<' &&
                   (buf[pos + 1] == '!' || buf[pos + 1] == '?' || buf[pos + 1] == '/')) {
            /* a bogus comment, including "</" not followed by a letter: skip to the next '>' */
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
