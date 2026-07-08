/* Serialization primitives shared by every output mode (#document, HTML,
   minify, markdown, text, readability). The escape formatters, the
   start/close-tag writers and the attribute ordering live here as
   `static inline`; the growable buffer they fill is included from buffer.h and
   the markdown/plain-text block predicates from markdown_internal.h, at the
   positions their definitions used to occupy, so a translation unit that
   includes this header sees the same definitions in the same order as before
   the split. */

#ifndef TURBOHTML_SERIALIZE_INTERNAL_H
#define TURBOHTML_SERIALIZE_INTERNAL_H

#include "dom/tree.h"
#include "dom/tree_internal.h" /* struct th_tree, need_text, is_void_atom */

#include "core/ascii.h"  /* is_space, the shared HTML ASCII-whitespace predicate */
#include "core/common.h" /* SWAR lane probes for the serializer's clean-run scan */
#include "core/vec.h"    /* th_grow_cap overflow-safe buffer growth */
#include "data/entity_names.h"

#include <string.h>

#include "serialize/buffer.h" /* the sbuf struct and its reserve/append hot path */

/* The #document attribute sort works in fixed stack buffers: at most this many
   attributes are ordered, each rendered name capped at this many bytes. */
#define MAX_SORTED_ATTRS 64
#define MAX_ATTR_NAME 128

/* The escape policy serialize()/encode() expose through the Formatter enum. */
enum th_formatter {
    TH_FMT_WHATWG,  /* conformant minimal escaping: & < > nbsp in text, & " nbsp in attrs */
    TH_FMT_MINIMAL, /* the three structural characters only, in both contexts */
    TH_FMT_NAMED,   /* HTML named entities for every character that has one */
};

/* Whether a character has a named reference under the NAMED formatter. The ASCII
   ones are exactly & " < >, so the generated table is only consulted past 0x7F. */
static inline int sbuf_named_special(Py_UCS4 character) {
    if (character < 0x80) {
        return character == '&' || character == '"' || character == '<' || character == '>';
    }
    return th_entity_name(character) != NULL;
}

/* The WHATWG/MINIMAL special set tested over a 64-bit word of two UCS-4 code
   points: & always, < > when angle brackets escape (text, or any MINIMAL), " in
   a WHATWG attribute value, and the no-break space WHATWG folds. Each probe sets
   the matching lane's high bit; a nonzero result means a special is in the pair. */
static inline uint64_t sbuf_special_mask(uint64_t word, int escape_angle, int escape_quote, int escape_nbsp) {
    uint64_t mask = swar_haslane32(word, '&');
    if (escape_angle) {
        mask |= swar_haslane32(word, '<') | swar_haslane32(word, '>');
    }
    if (escape_quote) {
        mask |= swar_haslane32(word, '"');
    }
    if (escape_nbsp) {
        mask |= swar_haslane32(word, 0xA0);
    }
    return mask;
}

/* Emit the replacement for a flagged character. */
static inline void sbuf_put_special(sbuf *out, Py_UCS4 character, int formatter) {
    const char *name = formatter == TH_FMT_NAMED ? th_entity_name(character) : NULL;
    if (name != NULL) {
        sbuf_putc(out, '&');
        sbuf_puts(out, name);
        sbuf_putc(out, ';');
    } else if (character == '&') {
        sbuf_puts(out, "&amp;");
    } else if (character == '<') {
        sbuf_puts(out, "&lt;");
    } else if (character == '>') {
        sbuf_puts(out, "&gt;");
    } else if (character == '"') {
        sbuf_puts(out, "&quot;");
    } else {
        sbuf_puts(out, "&nbsp;"); /* the only remaining flagged character is U+00A0 */
    }
}

/* Append NAMED-formatter text: bulk-copy each run with no named reference and
   rewrite the rest. NAMED consults a table past 0x7F, so it stays scalar. */
static inline void sbuf_put_named_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        while (index < len && !sbuf_named_special(text[index])) {
            index++;
        }
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len) {
            sbuf_put_special(out, text[index], TH_FMT_NAMED);
            index++;
        }
    }
}

/* Append text under the WHATWG or MINIMAL formatter, bulk-copying each run with
   nothing to escape and rewriting only the specials between. The tree stores
   text as UCS-4, so the clean-run scan tests two code points per 64-bit word
   with the same SWAR lane probes escape.c uses, hopping over the long unescaped
   spans real documents are made of instead of classifying every character. When
   a pair holds a special, its exact code point is recovered from the lane mask
   over a word built low-lane-first, so the position is independent of byte order
   and of which character class matched. */
static inline void sbuf_put_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len, int in_attr, int formatter) {
    if (formatter == TH_FMT_NAMED) {
        sbuf_put_named_text(out, text, len);
        return;
    }
    int escape_angle = formatter == TH_FMT_MINIMAL || !in_attr;
    int escape_quote = in_attr && formatter == TH_FMT_WHATWG;
    int escape_nbsp = formatter == TH_FMT_WHATWG;
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        Py_ssize_t special = -1;
        while (index + UCS4_LANES <= len) {
            uint64_t word;
            memcpy(&word, &text[index], sizeof(word));
            if (sbuf_special_mask(word, escape_angle, escape_quote, escape_nbsp) != 0) {
                uint64_t ordered = (uint64_t)text[index] | ((uint64_t)text[index + 1] << 32);
                uint64_t omask = sbuf_special_mask(ordered, escape_angle, escape_quote, escape_nbsp);
                special = index + (Py_ssize_t)((omask & 0x80000000ULL) == 0);
                break;
            }
            index += UCS4_LANES;
        }
        if (special < 0 && index < len) {
            /* one code point trails the last full pair; pad with a non-special
               lane so the same mask probe classifies it without a buffer overread */
            uint64_t word = (uint64_t)text[index];
            if (sbuf_special_mask(word, escape_angle, escape_quote, escape_nbsp) != 0) {
                special = index;
            }
        }
        Py_ssize_t stop = special < 0 ? len : special;
        if (stop > start) {
            sbuf_put_run(out, &text[start], stop - start);
        }
        if (special < 0) {
            break;
        }
        sbuf_put_special(out, text[special], formatter);
        index = special + 1;
    }
}

/* Whether an ASCII code point interrupts an XML text run: bit 0 marks the characters
   that break a run in any context -- the structural `& < >`, the carriage return XML
   escapes so a reparse cannot fold it to a newline, and every C0 control the Char
   production forbids (dropped on emit) -- and bit 1 the two the attribute context adds,
   the double quote and the tab/newline XML would otherwise normalize away. One table
   lookup replaces the per-character comparison chain on the hot serialize path. */
static const unsigned char XML_TEXT_STOP[128] = {
    1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

/* Whether a code point cannot appear in XML 1.0 character data, so an XML
   serialization drops it: the C0 controls other than tab/newline/carriage return,
   the UTF-16 surrogate block, and the two noncharacters U+FFFE and U+FFFF. Written
   as arithmetic and single comparisons, not a chained ||, so gcc's branch counting
   matches clang's. */
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

/* Emit one flagged character's XML replacement. */
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
        sbuf_puts(out, "&#13;"); /* the only remaining flagged character is U+000D */
    }
}

/* Whether a code point needs a reference under raw XML serialization. Structural
   `& < >` always escape; a double quote, tab and newline escape only inside an
   attribute value (where XML would otherwise normalize the whitespace away); a
   carriage return escapes everywhere so a reparse cannot fold it to a newline. This
   is Node.serialize(Html(xml=True)), which leaves a non-XML character verbatim. */
static inline int sbuf_xml_special(Py_UCS4 character, int in_attr) {
    if (character == '&' || character == '<' || character == '>' || character == '\r') {
        return 1;
    }
    return in_attr && (character == '"' || character == '\t' || character == '\n');
}

/* Whether a code point interrupts a well-formed XML text run: an ASCII character is
   one table lookup (the attribute context also stops on the bit-1 set), and only the
   rare non-ASCII invalids reach the Char-production check. */
static inline int xml_text_stop(Py_UCS4 character, int in_attr) {
    if (character < 0x80) {
        int mask = in_attr ? 0x3 : 0x1;
        return (XML_TEXT_STOP[character] & mask) != 0;
    }
    return xml_char_invalid(character);
}

/* Append text under XML escaping, bulk-copying each run with nothing to escape and
   rewriting only the specials between. Unlike the HTML formatter, no-break space is
   left verbatim (XML predefines no &nbsp;) and `>` escapes in every context. The raw
   path (Node.serialize) leaves a non-XML character in place; the well-formed path (the
   sanitizer's inner_xml) drops it, so a cleaned fragment always reparses. */
static inline void sbuf_put_xml_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len, int in_attr, int well_formed) {
    Py_ssize_t index = 0;
    if (!well_formed) {
        while (index < len) {
            Py_ssize_t start = index;
            while (index < len && !sbuf_xml_special(text[index], in_attr)) {
                index++;
            }
            if (index > start) {
                sbuf_put_run(out, &text[start], index - start);
            }
            if (index < len) {
                sbuf_put_xml_special(out, text[index]);
                index++;
            }
        }
        return;
    }
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

/* Append a comment's body under XML rules, which forbid the sequence `--` and a
   trailing `-` inside a comment. A space is inserted after any hyphen that would
   otherwise pair with the next one or close the comment, and characters XML cannot
   hold are dropped, so a kept comment always reparses. */
static inline void sbuf_put_xml_comment(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (xml_char_invalid(character)) {
            continue;
        }
        sbuf_putc(out, character);
        if (character != '-') {
            continue;
        }
        int at_end = index + 1 == len;
        if (at_end || text[index + 1] == '-') {
            sbuf_putc(out, ' ');
        }
    }
}

/* An element whose text children serialize literally rather than escaped: the
   WHATWG literal set is style/script/xmp/iframe/noembed/noframes/plaintext.
   noscript carries the rawtext flag for tokenization but is raw-text only when the
   tree was parsed with the scripting flag on; otherwise its content is escaped. */
static inline int is_rawtext_element(const th_node *node, int scripting) {
    /* the caller passes only element nodes, so this skips the node-type check */
    if (node->ns != TH_NS_HTML) {
        return 0; /* foreign elements (svg, mathml) never carry html raw-text content */
    }
    if (node->atom == TH_TAG_SCRIPT || node->atom == TH_TAG_PLAINTEXT) {
        return 1;
    }
    return (node->tag_flags & TH_TAG_RAWTEXT) && (node->atom != TH_TAG_NOSCRIPT || scripting);
}

/* The WHATWG fragment-serialization void set extends the parser's void elements
   with `frame`: it is emitted as a start tag only, never an end tag. `frame` is
   absent from is_void_atom because, unlike a true void element, it is a normal
   open element during parsing (in frameset mode it is inserted childless), so
   widening is_void_atom would wrongly make a stray in-body <frame> swallow no
   following content. */
static inline int is_serialize_void_atom(uint16_t atom) {
    return atom == TH_TAG_FRAME || is_void_atom(atom);
}

/* The doctype name length: build_doctype_text stores "name" optionally followed
   by ` "public" "system"`, but HTML serialization emits only the name. */
static inline Py_ssize_t doctype_name_len(const th_node *node) {
    Py_ssize_t index = 0;
    while (index < node->text_len && node->text[index] != ' ') {
        index++;
    }
    return index;
}

/* A pre/textarea/listing element whose first child is a text node beginning with
   a newline needs an extra leading newline on output: the parser drops one such
   newline when reading, so re-emitting it keeps the round trip faithful. */
static inline int ser_needs_leading_newline(th_tree *tree, th_node *node) {
    if (node->ns != TH_NS_HTML) {
        return 0;
    }
    if (node->atom != TH_TAG_PRE && node->atom != TH_TAG_TEXTAREA && node->atom != TH_TAG_LISTING) {
        return 0;
    }
    /* a text node always carries at least one code point (the builder never
       inserts an empty one), so reading the first character here is safe */
    th_node *first = node->first_child;
    return first != NULL && first->type == TH_NODE_TEXT && need_text(tree, first)[0] == '\n';
}

/* ASCII case-insensitive compare of a UCS-4 attribute value against a lowercase
   literal: the value matches when it has the same length and folds to the same
   bytes (only A-Z fold, the only case mapping the labels here need). */
static inline int ser_value_iequals(const Py_UCS4 *value, Py_ssize_t len, const char *ascii) {
    Py_ssize_t index = 0;
    for (; index < len && ascii[index] != '\0'; index++) {
        Py_UCS4 character = value[index];
        if (character >= 'A' && character <= 'Z') {
            character += 'a' - 'A';
        }
        if (character != (Py_UCS4)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return index == len && ascii[index] == '\0';
}

/* How a <meta> element declares the document encoding, for the meta_charset
   normalizer: 1 a `charset` attribute, 2 an http-equiv="content-type" with a
   `content` attribute, 0 neither (or not an HTML <meta>). */
static inline int ser_meta_charset_kind(th_tree *tree, const th_node *node) {
    if (node->ns != TH_NS_HTML || node->atom != TH_TAG_META) {
        return 0;
    }
    if (th_node_attr_find(tree, (th_node *)node, "charset", 7) >= 0) {
        return 1;
    }
    Py_ssize_t equiv = th_node_attr_find(tree, (th_node *)node, "http-equiv", 10);
    if (equiv >= 0 && th_node_attr_find(tree, (th_node *)node, "content", 7) >= 0 &&
        ser_value_iequals(node->attrs[equiv].value, node->attrs[equiv].value_len, "content-type")) {
        return 2;
    }
    return 0;
}

/* Whether head already declares a charset through a direct <meta> child, so the
   meta_charset option normalizes that one in place instead of injecting another. */
static inline int ser_head_has_charset_meta(th_tree *tree, const th_node *head) {
    for (th_node *child = head->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && ser_meta_charset_kind(tree, child) != 0) {
            return 1;
        }
    }
    return 0;
}

/* Emit a fresh charset declaration, <meta charset="LABEL">. */
static inline void ser_emit_meta_charset(sbuf *out, const th_serialize_opts *opts) {
    sbuf_puts(out, "<meta charset=\"");
    sbuf_put_utf8(out, opts->charset, opts->charset_len);
    sbuf_puts(out, "\">");
}

/* Lexicographic compare of two interned attribute names by bytes, the shorter
   name ordering first on a shared prefix (the html5lib alphabetical-attributes
   order over the stored, parser-lowercased name). */
static inline int ser_name_cmp(const char *left, Py_ssize_t left_len, const char *right, Py_ssize_t right_len) {
    Py_ssize_t min_len = left_len < right_len ? left_len : right_len;
    int order = memcmp(left, right, (size_t)min_len);
    if (order != 0) {
        return order;
    }
    return left_len < right_len ? -1 : left_len > right_len;
}

/* Insertion-sort order[0..count) into ascending attribute-name order. Counts are
   tiny, so the quadratic sort beats any setup an asymptotically faster one needs. */
static inline void ser_sort_order(th_tree *tree, const th_node *node, Py_ssize_t *order, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        order[index] = index;
    }
    for (Py_ssize_t index = 1; index < count; index++) {
        Py_ssize_t key = order[index];
        Py_ssize_t key_len;
        const char *key_name = th_attr_name(tree, node->attrs[key].name_atom, &key_len);
        Py_ssize_t prev = index - 1;
        while (prev >= 0) {
            Py_ssize_t prev_len;
            const char *prev_name = th_attr_name(tree, node->attrs[order[prev]].name_atom, &prev_len);
            if (ser_name_cmp(prev_name, prev_len, key_name, key_len) <= 0) {
                break;
            }
            order[prev + 1] = order[prev];
            prev--;
        }
        order[prev + 1] = key;
    }
}

/* The order to emit node's attributes in: NULL means source order (sorting off, or
   fewer than two attributes), otherwise a filled index array -- the caller's stack
   buffer for up to MAX_SORTED_ATTRS attributes, else a heap block freed through
   ser_attr_order_free. */
static inline Py_ssize_t *ser_attr_order(th_tree *tree, const th_node *node, int sort, Py_ssize_t *stack) {
    if (!sort || node->attr_count < 2) {
        return NULL;
    }
    Py_ssize_t *order =
        node->attr_count <= MAX_SORTED_ATTRS ? stack : PyMem_Malloc((size_t)node->attr_count * sizeof(Py_ssize_t));
    if (order == NULL) { /* GCOVR_EXCL_BR_LINE: only the heap path can fail, and OOM is unforceable */
        return NULL;     /* GCOVR_EXCL_LINE: degrade to source order on allocation failure */
    }
    ser_sort_order(tree, node, order, node->attr_count);
    return order;
}

static inline void ser_attr_order_free(Py_ssize_t *order, const Py_ssize_t *stack) {
    if (order != NULL && order != stack) {
        PyMem_Free(order);
    }
}

/* Under meta_charset, rewrite a charset meta's declaration to the target label:
   charset="LABEL" (kind 1) or content="text/html; charset=LABEL" (kind 2). Returns
   1 when it emitted the attribute, 0 when the caller should emit the stored value.
   The element is an HTML <meta>, whose attribute names the parser has lowercased,
   so a byte compare against the lowercase spelling is exact. */
static inline int ser_put_charset_value(sbuf *out, const th_serialize_opts *opts, int kind, const char *name,
                                        Py_ssize_t name_len) {
    if (kind == 1 && name_len == 7 && memcmp(name, "charset", 7) == 0) {
        sbuf_puts(out, "=\"");
        sbuf_put_utf8(out, opts->charset, opts->charset_len);
        sbuf_putc(out, '"');
        return 1;
    }
    if (kind == 2 && name_len == 7 && memcmp(name, "content", 7) == 0) {
        sbuf_puts(out, "=\"text/html; charset=");
        sbuf_put_utf8(out, opts->charset, opts->charset_len);
        sbuf_putc(out, '"');
        return 1;
    }
    return 0;
}

/* When meta_charset is on and node is a <head> that declares no charset of its own,
   emit the <meta charset> as its first child so the output names the encoding. The
   atom guard means this is a no-op for every element but the document head. */
static inline void ser_inject_head_meta(sbuf *out, th_tree *tree, const th_node *node, const th_serialize_opts *opts) {
    if (opts->inject_meta && node->ns == TH_NS_HTML && node->atom == TH_TAG_HEAD &&
        !ser_head_has_charset_meta(tree, node)) {
        ser_emit_meta_charset(out, opts);
    }
}

/* Which namespace declarations ser_xml_ns_decls emitted for a start tag, so the
   attribute writer drops a stored duplicate of exactly those (a second xmlns on the
   same tag is ill-formed) while keeping every other xmlns:prefix a caller declared. */
typedef struct {
    int default_ns; /* the foreign-root xmlns="..." was written */
    int xlink;      /* the xmlns:xlink prefix binding was written */
} xml_ns_emitted;

/* Emit the namespace declarations an XML start tag needs to stay well-formed: the
   default namespace on the root of a foreign (SVG/MathML) subtree -- an element
   whose parent is in a different namespace -- and the xlink prefix on any element
   carrying an `xlink:`-prefixed attribute (a redundant redeclaration on a nested
   element is still well-formed, so no ancestor tracking is needed). */
static inline xml_ns_emitted ser_xml_ns_decls(sbuf *out, th_tree *tree, const th_node *node) {
    xml_ns_emitted emitted = {0, 0};
    if (node->ns != TH_NS_HTML && (node->parent == NULL || node->parent->ns != node->ns)) {
        sbuf_puts(out, node->ns == TH_NS_SVG ? " xmlns=\"http://www.w3.org/2000/svg\""
                                             : " xmlns=\"http://www.w3.org/1998/Math/MathML\"");
        emitted.default_ns = 1;
    }
    for (Py_ssize_t position = 0; position < node->attr_count; position++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[position].name_atom, &name_len);
        if (name_len >= 6 && memcmp(name, "xlink:", 6) == 0) {
            sbuf_puts(out, " xmlns:xlink=\"http://www.w3.org/1999/xlink\"");
            emitted.xlink = 1;
            break;
        }
    }
    return emitted;
}

/* Whether an attribute's name can be written into an XML start tag: a stored
   namespace declaration ser_xml_ns_decls already emitted for this tag is dropped
   because a second copy would make the tag ill-formed (but any other xmlns:prefix a
   caller declared is kept), and a name carrying a character XML forbids in a Name --
   the tag-soup `=`, `/`, `<`, quotes and spaces the HTML parser keeps but XML cannot
   -- is dropped so the tag stays well-formed. ASCII is checked against the
   NameStart/NameChar table; a byte >= 0x80 is accepted wholesale rather than decode
   UTF-8 to consult the full Unicode Name ranges, matching the datatype validator. The
   table (bit 0 NameStart, bit 1 NameChar) makes gcc and clang count the same
   branches, which a chained comparison over the name literals would not. */
static inline int xml_attr_name_writable(const char *name, Py_ssize_t name_len, xml_ns_emitted emitted) {
    static const unsigned char NAME_FLAGS[128] = {
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 0, 0, 0, 0, 0,
        0, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 3,
        0, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 0,
    };
    int is_default_decl = name_len == 5 && memcmp(name, "xmlns", 5) == 0;
    if (is_default_decl && emitted.default_ns) {
        return 0;
    }
    int is_xlink_decl = name_len == 11 && memcmp(name, "xmlns:xlink", 11) == 0;
    if (is_xlink_decl && emitted.xlink) {
        return 0;
    }
    unsigned char first = (unsigned char)name[0];
    if (first < 0x80 && (NAME_FLAGS[first] & 0x1) == 0) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < name_len; index++) {
        unsigned char character = (unsigned char)name[index];
        if (character < 0x80 && (NAME_FLAGS[character] & 0x2) == 0) {
            return 0;
        }
    }
    return 1;
}

/* Write an element's start tag up to but not including its closing delimiter,
   "<tag attrs...": attributes in source order, or in name order when opts asks;
   values double-quoted and escaped under the formatter (or the XML rules when
   opts->xml), with a charset meta's declaration normalized when meta_charset is on.
   The caller appends ">" (or "/>" for an empty XML element) so it can choose the
   self-closing form after inspecting the children. */
static inline void ser_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_serialize_opts *opts) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);
    xml_ns_emitted emitted = {0, 0};
    if (opts->xml) {
        emitted = ser_xml_ns_decls(out, tree, node);
    }
    Py_ssize_t stack_order[MAX_SORTED_ATTRS];
    Py_ssize_t *order = ser_attr_order(tree, node, opts->sort_attributes, stack_order);
    int charset_kind = opts->inject_meta && !opts->xml ? ser_meta_charset_kind(tree, node) : 0;
    for (Py_ssize_t position = 0; position < node->attr_count; position++) {
        th_node_attr *attr = &node->attrs[order != NULL ? order[position] : position];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        if (opts->well_formed && !xml_attr_name_writable(name, name_len, emitted)) {
            continue;
        }
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, name, name_len);
        if (ser_put_charset_value(out, opts, charset_kind, name, name_len)) {
            continue;
        }
        sbuf_puts(out, "=\"");
        if (opts->xml) {
            sbuf_put_xml_text(out, attr->value, attr->value_len, 1, opts->well_formed);
        } else {
            sbuf_put_text(out, attr->value, attr->value_len, 1, opts->formatter);
        }
        sbuf_putc(out, '"');
    }
    ser_attr_order_free(order, stack_order);
}

static inline void ser_close_tag(sbuf *out, th_node *node) {
    sbuf_puts(out, "</");
    sbuf_put_ucs4(out, node->text, node->text_len);
    sbuf_putc(out, '>');
}

/* Hand a filled sbuf back to the caller, normalizing an empty result to a real
   zero-length allocation so NULL unambiguously means failure. */
static inline Py_UCS4 *sbuf_finish(sbuf *out, Py_ssize_t *out_len) {
    if (out->failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(out->data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *out_len = out->len;
    if (out->data == NULL) {
        out->data = PyMem_Malloc(1); /* GCOVR_EXCL_BR_LINE: empty output, allocation cannot be forced to fail */
    }
    return out->data;
}

/* When serializing a whole parsed document, the output length tracks the input
   length closely, so reserve it up front. That turns the buffer's geometric
   doubling (about ten reallocs and ~2x the output in memmoves for a 235 kB page)
   into a single allocation. A subtree serialize keeps the default growth, since
   the input length would wildly over-reserve. */
static inline void sbuf_presize_for_root(sbuf *out, th_tree *tree, th_node *node) {
    if (node == th_tree_document(tree)) {
        /* reserving the input length is a no-op for an empty or programmatic
           tree (length 0), so no separate guard is needed */
        sbuf_reserve(out, tree->length);
    }
}

/* True only for the foreign attribute names the spec puts in a namespace; those
   serialize with a space (prefix localname). Plain "xmlns" has no prefix in its
   stored name but still belongs to the xmlns namespace, so the renderer prepends
   one. An arbitrary xml:/xlink: name not in the table keeps its literal colon. */
static inline int foreign_attr_namespaced(const char *lower, Py_ssize_t len) {
    static const char *const NAMESPACED[] = {
        "xlink:actuate", "xlink:arcrole", "xlink:href", "xlink:role", "xlink:show",  "xlink:title",
        "xlink:type",    "xml:lang",      "xml:space",  "xmlns",      "xmlns:xlink",
    };
    for (size_t index = 0; index < sizeof(NAMESPACED) / sizeof(NAMESPACED[0]); index++) {
        if ((Py_ssize_t)strlen(NAMESPACED[index]) == len && memcmp(NAMESPACED[index], lower, (size_t)len) == 0) {
            return 1;
        }
    }
    return 0;
}

#include "serialize/markdown_internal.h" /* the markdown/plain-text block predicates */

#endif /* TURBOHTML_SERIALIZE_INTERNAL_H */
