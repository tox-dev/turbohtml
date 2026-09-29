/* The XML 1.0 Name productions, shared by the XML parser and by XSLT's check on computed names. A header rather
   than exported functions: callers of these helpers outside tokenizer/xml.c changed how LTO inlines the parser's text
   loop and cost 0.3% on parse-xml-text-references. */

#ifndef TURBOHTML_XML_NAMES_H
#define TURBOHTML_XML_NAMES_H

#include <Python.h>

typedef struct {
    Py_UCS4 lo;
    Py_UCS4 hi;
} cp_range;

/* NameStartChar (XML 1.0 [4]) as inclusive code-point ranges, common ASCII first for the
   early exit; the exact list -- not the "any code point >= U+0080" approximation -- so a
   combining mark, a middle dot or the other punctuation the spec omits is rejected. */
static const cp_range NAME_START_RANGES[] = {
    {'a', 'z'},       {'A', 'Z'},       {'_', '_'},       {':', ':'},         {0xC0, 0xD6},     {0xD8, 0xF6},
    {0xF8, 0x2FF},    {0x370, 0x37D},   {0x37F, 0x1FFF},  {0x200C, 0x200D},   {0x2070, 0x218F}, {0x2C00, 0x2FEF},
    {0x3001, 0xD7FF}, {0xF900, 0xFDCF}, {0xFDF0, 0xFFFD}, {0x10000, 0xEFFFF},
};

/* NameChar (XML 1.0 [4a]): every NameStartChar plus the digits, '-', '.' and the
   combining/extender ranges the production adds. */
static const cp_range NAME_CHAR_RANGES[] = {
    {'a', 'z'},       {'A', 'Z'},       {'0', '9'},         {'_', '_'},       {':', ':'},       {'-', '-'},
    {'.', '.'},       {0xB7, 0xB7},     {0xC0, 0xD6},       {0xD8, 0xF6},     {0xF8, 0x2FF},    {0x300, 0x37D},
    {0x37F, 0x1FFF},  {0x200C, 0x200D}, {0x203F, 0x2040},   {0x2070, 0x218F}, {0x2C00, 0x2FEF}, {0x3001, 0xD7FF},
    {0xF900, 0xFDCF}, {0xFDF0, 0xFFFD}, {0x10000, 0xEFFFF},
};

static int in_ranges(Py_UCS4 ch, const cp_range *ranges, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        if (ch >= ranges[index].lo && ch <= ranges[index].hi) {
            return 1;
        }
    }
    return 0;
}

/* ASCII (U+0000..U+007F) fast path: real XML names are almost all ASCII, so a table lookup skips the
   range scan on the hot per-character path. Bit 0 marks a NameStartChar, bit 1 a NameChar (every
   NameStartChar is also a NameChar); the values are the ASCII subset of the ranges above. */
#define XML_NAME_START_FLAG 0x1
#define XML_NAME_CHAR_FLAG 0x2
static const unsigned char ASCII_NAME_FLAGS[128] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 0, 0, 0, 0, 0,
    0, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 3,
    0, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 0,
};

static int is_name_start(Py_UCS4 ch) {
    if (ch < 0x80) {
        return (ASCII_NAME_FLAGS[ch] & XML_NAME_START_FLAG) != 0;
    }
    return in_ranges(ch, NAME_START_RANGES, (Py_ssize_t)(sizeof(NAME_START_RANGES) / sizeof(cp_range)));
}

static int is_name_char(Py_UCS4 ch) {
    if (ch < 0x80) {
        return (ASCII_NAME_FLAGS[ch] & XML_NAME_CHAR_FLAG) != 0;
    }
    return in_ranges(ch, NAME_CHAR_RANGES, (Py_ssize_t)(sizeof(NAME_CHAR_RANGES) / sizeof(cp_range)));
}

#endif /* TURBOHTML_XML_NAMES_H */
