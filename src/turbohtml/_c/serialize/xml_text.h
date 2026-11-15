#ifndef TURBOHTML_SERIALIZE_XML_TEXT_H
#define TURBOHTML_SERIALIZE_XML_TEXT_H

#include "serialize/buffer.h"

/* XML 1.0 excludes these code points even when a DOM mutation inserts them after parsing. */
static inline int xml_char_invalid(Py_UCS4 character) {
    if (character < 0x20) {
        Py_UCS4 allowed = (1u << '\t') | (1u << '\n') | (1u << '\r');
        return ((allowed >> character) & 1u) == 0;
    }
    if (character < 0xD800) {
        return 0;
    }
    if (character <= 0xDFFF) {
        return 1;
    }
    if (character == 0xFFFE) {
        return 1;
    }
    return character == 0xFFFF;
}

/* Bit 0 stops text runs; bit 1 also stops attribute runs. */
static const unsigned char XML_TEXT_STOP[128] = {
    1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

static inline void sbuf_put_xml_special(sbuf *out, Py_UCS4 character) {
    if (character == '&') {
        sbuf_puts(out, "&amp;");
    } else if (character == '<') {
        sbuf_puts(out, "&lt;");
    } else if (character == '>') {
        sbuf_puts(out, "&gt;");
    } else if (character == '"') {
        sbuf_puts(out, "&quot;");
    } else if (character == '\t') {
        sbuf_puts(out, "&#9;");
    } else if (character == '\n') {
        sbuf_puts(out, "&#10;");
    } else {
        sbuf_puts(out, "&#13;");
    }
}

static inline int xml_text_stop(Py_UCS4 character, int in_attr) {
    if (character < 0x80) {
        int mask = in_attr ? 0x3 : 0x1;
        return (XML_TEXT_STOP[character] & mask) != 0;
    }
    return xml_char_invalid(character);
}

/* Whether a c14n text run stops at a character: the markup characters and the controls XML cannot hold lie below
   U+0040, so one bit mask covers them (tab and newline stay literal), and the code points XML forbids above it are the
   surrogates and noncharacters. Most text is below U+0040 or above it with no test beyond the first compare. */
static inline int c14n_text_stop(Py_UCS4 character) {
    if (character < 0x40) {
        const uint64_t stops = 0xFFFFF9FFULL | (1ULL << '&') | (1ULL << '<') | (1ULL << '>');
        return (int)((stops >> character) & 1u);
    }
    return character >= 0xD800 && xml_char_invalid(character);
}

/* Copy clean runs and drop characters XML 1.0 cannot hold. */
static inline void sbuf_put_xml_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len, int in_attr) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        while (index < len && !xml_text_stop(text[index], in_attr)) {
            index++;
        }
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len) {
            if (!xml_char_invalid(text[index])) {
                sbuf_put_xml_special(out, text[index]);
            }
            index++;
        }
    }
}

/* Whether a copy of about `cost` code points fits the allowance of `base` plus TH_XML_NS_PER_CHAR per code point of
   other output (dom/tree.h), `copied` of the `written` so far being copies already. Dividing instead of multiplying
   keeps a huge output from wrapping. */
static inline int xml_ns_copy_funded(Py_ssize_t copied, Py_ssize_t written, Py_ssize_t cost, Py_ssize_t base) {
    return (copied + cost - base) / TH_XML_NS_PER_CHAR <= written - copied;
}

#endif /* TURBOHTML_SERIALIZE_XML_TEXT_H */
