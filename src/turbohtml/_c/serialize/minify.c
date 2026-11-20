/* Shrinks a parsed document to the smallest equivalent HTML -- dropping the
   optional tags, quotes, and whitespace the spec lets us fold without changing
   what the bytes reparse to.

   Minification is a serialization mode over the already-conformant parse tree:
   the four transforms below only ever drop or fold what the WHATWG parser
   reconstructs on the way back in, so the minified bytes reparse to the same
   tree. That round-trip equivalence is the correctness gate, enforced by the
   idempotence test over the conformance corpus. It reuses the shared sbuf
   buffer, the escape helpers, ser_close_tag, is_rawtext_element,
   ser_needs_leading_newline and doctype_name_len from serialize/internal.h. */

#include "core/ascii.h"
#include "serialize/internal.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"
#include "css/minify/css.h"
#include "js/minify.h"
#include "tokenizer/charref.h"

#include <string.h>

/* pre/textarea/listing keep their text verbatim: a collapse inside them would
   change rendered content. Raw-text elements (script/style/...) are emitted as
   leaves by the walker, so they need no entry here. */
static int mini_is_preserve_atom(const th_node *node) {
    return node->ns == TH_NS_HTML &&
           (node->atom == TH_TAG_PRE || node->atom == TH_TAG_TEXTAREA || node->atom == TH_TAG_LISTING);
}

/* The whitespace lanes of a 2-code-point word, folded into the same SWAR probe the
   serializer uses for escapable characters so the collapse scan hops clean runs. CR is
   left to sbuf_special_mask, which already stops the hop on it. */
static inline uint64_t mini_ws_mask(uint64_t word) {
    return swar_haslane32(word, ' ') | swar_haslane32(word, '\t') | swar_haslane32(word, '\n') |
           swar_haslane32(word, '\f');
}

/* Whether a code point in collapsed text needs a look before it is written: the structural characters (text never
   escapes the double quote) and, under the NAMED formatter, any character with a reference. U+00A0 reparses as
   itself, so only the NAMED formatter writes it as a reference. */
static int mini_text_special(Py_UCS4 character, int formatter) {
    if (formatter == TH_FMT_NAMED) {
        return sbuf_named_special(character);
    }
    return character == '&' || character == '<' || character == '>';
}

/* Whether a bare '&' at text[index] reparses as a literal '&'. A '#' after it starts a numeric reference, and a run of
   name characters starts a named one when the table holds the run, the run with its ';', or a prefix of at least two
   characters, the tokenizer's longest match. At the end of the text the next node could extend the name, so the '&' is
   escaped there unless the caller writes a delimiter next, which only ends the run. */
static int mini_amp_literal(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len, int delimited) {
    Py_ssize_t cursor = index + 1;
    if (cursor < len && text[cursor] == '#') {
        return 0;
    }
    char ascii[HTML5_MAX_NAME_LEN + 1];
    int name_len = 0;
    while (cursor < len && name_len < HTML5_MAX_NAME_LEN &&
           (is_ascii_alpha(text[cursor]) || is_ascii_digit(text[cursor]))) {
        ascii[name_len++] = (char)text[cursor++];
    }
    if (cursor == len && !delimited) {
        return 0;
    }
    if (name_len == 0) {
        return 1;
    }
    if (cursor < len && text[cursor] == ';') {
        ascii[name_len] = ';';
        if (charref_find_entity(ascii, name_len + 1) != NULL) {
            return 0;
        }
    }
    for (int prefix = name_len; prefix >= 2; prefix--) {
        if (charref_find_entity(ascii, prefix) != NULL) {
            return 0;
        }
    }
    return 1;
}

/* Write a text special in the shortest form that reparses to the same character: '>' and U+00A0 never need a
   reference in text, '&' only when it would start one, and '<' only before a character that opens markup or at the end
   of the text, where the next node is not known. The NAMED formatter keeps its references. */
static void mini_put_text_special(sbuf *out, const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len, int formatter) {
    Py_UCS4 character = text[index];
    int escape = character == '\r';
    if (character == '&') {
        escape = !mini_amp_literal(text, index, len, 0);
    } else if (character == '<') {
        Py_UCS4 next = index + 1 < len ? text[index + 1] : '/';
        escape = is_ascii_alpha(next) || next == '!' || next == '/' || next == '?';
    }
    if (escape || formatter == TH_FMT_NAMED) {
        sbuf_put_special(out, character, formatter);
    } else {
        sbuf_putc(out, character);
    }
}

/* Write text under the minifying layout without folding whitespace: copy the clean runs and give each special its
   shortest form. */
static void mini_put_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len, int formatter) {
    if (formatter == TH_FMT_NAMED) {
        sbuf_put_text(out, text, len, 0, formatter);
        return;
    }
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t special = sbuf_find_special(text, index, len, 0, 0);
        if (special > index) {
            sbuf_put_run(out, &text[index], special - index);
        }
        if (special == len) {
            break;
        }
        mini_put_text_special(out, text, special, len, formatter);
        index = special + 1;
    }
}

/* Collapse each run of ASCII whitespace to a single space, escaping the runs
   between under the formatter. A single pass bulk-copies the clean spans (hopped
   with the SWAR whitespace+special probe for the WHATWG/MINIMAL formatters) and
   rewrites only the whitespace and the escapable characters, so collapsed prose
   costs about what the plain serializer's one-shot escape does rather than a
   separate call per word. Folding to one space (rather than deleting) keeps the
   transform idempotent: a single space reparses to a single-space text node in the
   same place, so the tree is unchanged. */
static void mini_put_collapsed_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len, int formatter,
                                    int *last_was_space) {
    Py_ssize_t index = 0;
    while (index < len) {
        if (is_space(text[index])) {
            if (!*last_was_space) {
                /* a stripped comment can leave two whitespace text nodes adjacent; suppressing
                   the second's leading space keeps the fold idempotent, since the reparse merges
                   them into one node that folds to a single space */
                sbuf_putc(out, ' ');
                *last_was_space = 1;
            }
            do {
                index++;
            } while (index < len && is_space(text[index]));
            continue;
        }
        Py_ssize_t start = index;
        if (formatter != TH_FMT_NAMED) {
            while (index + UCS4_LANES <= len) {
                uint64_t word;
                memcpy(&word, &text[index], sizeof(word));
                if ((sbuf_special_mask(word) | mini_ws_mask(word)) != 0) {
                    break;
                }
                index += UCS4_LANES;
            }
        }
        while (index < len && !is_space(text[index]) && !mini_text_special(text[index], formatter)) {
            index++;
        }
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len && !is_space(text[index])) {
            mini_put_text_special(out, text, index, len, formatter);
            index++;
        }
        *last_was_space = 0;
    }
}

/* Whether the NAMED formatter writes any character of value as a reference, which then keeps the quoted form. */
static int mini_has_named_reference(const Py_UCS4 *value, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (sbuf_named_special(value[index])) {
            return 1;
        }
    }
    return 0;
}

enum {
    MINI_ATTR_QUOTE = 1,  /* the value cannot go unquoted */
    MINI_ATTR_ESCAPE = 2, /* the writer looks at it: it may need a reference */
    MINI_ATTR_DOUBLE = 4,
    MINI_ATTR_SINGLE = 8,
};

/* How each ASCII character bears on writing an attribute value; every other code point has no bearing. */
static const uint8_t MINI_ATTR_CLASS[128] = {
    ['\t'] = MINI_ATTR_QUOTE,
    ['\n'] = MINI_ATTR_QUOTE,
    ['\f'] = MINI_ATTR_QUOTE,
    ['\r'] = MINI_ATTR_QUOTE | MINI_ATTR_ESCAPE,
    [' '] = MINI_ATTR_QUOTE,
    ['"'] = MINI_ATTR_QUOTE | MINI_ATTR_DOUBLE,
    ['\''] = MINI_ATTR_QUOTE | MINI_ATTR_SINGLE,
    ['`'] = MINI_ATTR_QUOTE,
    ['='] = MINI_ATTR_QUOTE,
    ['<'] = MINI_ATTR_QUOTE | MINI_ATTR_ESCAPE,
    ['>'] = MINI_ATTR_QUOTE | MINI_ATTR_ESCAPE,
    ['&'] = MINI_ATTR_ESCAPE,
};

static uint8_t mini_attr_class(Py_UCS4 character) {
    return character < 128 ? MINI_ATTR_CLASS[character] : 0;
}

/* Write a non-empty attribute value in its shortest form that reparses to the same value. Without ASCII whitespace or
   any of " ' ` = < > it goes unquoted: the unquoted value state ends only at whitespace or '>', so a trailing '/' stays
   part of the value. Otherwise it takes whichever quote it holds fewer of, and only that quote needs a reference.
   '&' is escaped only where it would start a reference; the delimiter written after the value ends any name run at
   its end. '<' and '>' keep their references: a reparse through foreign content or <noscript> can read the tag as
   raw text, where they would become markup. */
static void mini_put_attr_value(sbuf *out, const Py_UCS4 *value, Py_ssize_t len) {
    unsigned seen = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        seen |= mini_attr_class(value[index]);
    }
    Py_UCS4 quote = 0;
    unsigned escaped = MINI_ATTR_ESCAPE;
    if (seen & MINI_ATTR_QUOTE) {
        int single = (seen & MINI_ATTR_DOUBLE) != 0;
        if (single && (seen & MINI_ATTR_SINGLE)) { /* only a value holding both quotes needs them counted */
            Py_ssize_t balance = 0;
            for (Py_ssize_t index = 0; index < len; index++) {
                balance += (value[index] == '"') - (value[index] == '\'');
            }
            single = balance > 0;
        }
        quote = single ? '\'' : '"';
        escaped |= single ? MINI_ATTR_SINGLE : MINI_ATTR_DOUBLE;
    }
    sbuf_putc(out, '=');
    if (quote != 0) {
        sbuf_putc(out, quote);
    }
    if ((seen & escaped) == 0) {
        sbuf_put_run(out, value, len);
    } else {
        Py_ssize_t start = 0;
        for (Py_ssize_t index = 0; index < len; index++) {
            Py_UCS4 character = value[index];
            if ((mini_attr_class(character) & escaped) == 0 ||
                (character == '&' && mini_amp_literal(value, index, len, 1))) {
                continue;
            }
            sbuf_put_run(out, &value[start], index - start);
            start = index + 1;
            sbuf_puts(out, character == '&'    ? "&amp;"
                           : character == '\r' ? "&#13;"
                           : character == '<'  ? "&lt;"
                           : character == '>'  ? "&gt;"
                           : character == '"'  ? "&quot;"
                                               : "&#39;");
        }
        sbuf_put_run(out, &value[start], len - start);
    }
    if (quote != 0) {
        sbuf_putc(out, quote);
    }
}

/* Encode text[0..len) as UTF-8 into a freshly PyMem-allocated buffer (caller frees); *out_len
   receives the byte count. The CSS engine works over UTF-8 bytes while the tree stores code
   points, so the <style>/style= paths transcode through here. Tree text is well-formed code
   points (the parser folds surrogates to U+FFFD), so the four UTF-8 lengths cover every input. */
static unsigned char *mini_ucs4_to_utf8(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t *out_len) {
    unsigned char *buf = PyMem_Malloc((size_t)(len * 4 + 1));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (character < 0x80) {
            buf[at++] = (unsigned char)character;
        } else if (character < 0x800) {
            buf[at++] = (unsigned char)(0xC0 | (character >> 6));
            buf[at++] = (unsigned char)(0x80 | (character & 0x3F));
        } else if (character < 0x10000) {
            buf[at++] = (unsigned char)(0xE0 | (character >> 12));
            buf[at++] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
            buf[at++] = (unsigned char)(0x80 | (character & 0x3F));
        } else {
            buf[at++] = (unsigned char)(0xF0 | (character >> 18));
            buf[at++] = (unsigned char)(0x80 | ((character >> 12) & 0x3F));
            buf[at++] = (unsigned char)(0x80 | ((character >> 6) & 0x3F));
            buf[at++] = (unsigned char)(0x80 | (character & 0x3F));
        }
    }
    *out_len = at;
    return buf;
}

/* Minify src[0..len) through the shipped CSS engine, returning its UTF-8 output in a freshly
   PyMem-allocated buffer (caller frees) with the byte length in *out_len. Input that minifies
   to nothing yields a zero-length buffer (*out_len 0), which the callers treat as empty; NULL
   with a nonzero *out_len reports an allocation failure.
   inline_mode 1 selects the style="" declaration-list grammar over the full-stylesheet one.
   baseline (a CSSMinify year, 0 for the most portable output) bounds how new the emitted syntax
   may be. The engine is value-safe and idempotent, so the emitted CSS reparses to the same
   cascade and re-minifying is a fixpoint. */
static unsigned char *mini_css_bytes(const Py_UCS4 *src, Py_ssize_t len, int inline_mode, int baseline,
                                     Py_ssize_t *out_len) {
    Py_ssize_t utf8_len;
    unsigned char *utf8 = mini_ucs4_to_utf8(src, len, &utf8_len);
    if (utf8 == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        *out_len = 1;   /* GCOVR_EXCL_LINE: the CSS engine's own failure convention */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    unsigned char *result = th_minify_css_bytes(utf8, utf8_len, inline_mode, baseline, out_len);
    PyMem_Free(utf8);
    return result;
}

/* Minify a style="" declaration list, returning its value as a freshly PyMem-allocated code-point
   buffer (caller frees) with the length in *out_len, or NULL when the declarations minify to
   nothing (the caller then renders the empty value). An allocation failure marks out failed and
   also returns NULL with an empty value. */
static Py_UCS4 *mini_style_attr_css(sbuf *out, const Py_UCS4 *value, Py_ssize_t len, int baseline,
                                    Py_ssize_t *out_len) {
    Py_ssize_t css_len;
    unsigned char *result = mini_css_bytes(value, len, 1, baseline, &css_len);
    *out_len = 0;
    if (result == NULL && css_len != 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1;                  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (css_len == 0) {
        PyMem_Free(result); /* the declarations minified to nothing: the caller renders an empty value */
        return NULL;
    }
    sbuf decoded = {NULL, 0, 0, 0, 0, NULL};
    sbuf_put_utf8(&decoded, (const char *)result, css_len);
    PyMem_Free(result);
    Py_UCS4 *minified = sbuf_finish(&decoded, out_len);
    out->failed |= minified == NULL;
    return minified;
}

/* Write an element's start tag, dropping redundant attribute quotes and writing a
   valueless/empty attribute as just its name when unquoting is enabled. Attribute
   order and charset normalization come from the shared output options (a normalized
   charset value always keeps its quotes). A style="" value is minified when minify_css
   is set, before the quote decision so the shorter value can also shed its quotes. */
/* Whether a clone's attributes are left out. A first copy of a value set keeps them, so the cost is only summed for a
   repeat; out of line, so an element the source wrote never reaches it. */
static TH_NOINLINE int ser_clone_attrs_refused(sbuf *out, Py_ssize_t start, th_tree *tree, const th_node *node) {
    int added = ser_clone_first(out, node);
    if (added != 0) {
        return added < 0;
    }
    return !ser_clone_fits(out, start, ser_clone_attrs_cost(tree, node));
}

static void mini_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_serialize_opts *opts, int unquote,
                          int minify_css, int css_baseline) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);
    Py_ssize_t stack_order[MAX_SORTED_ATTRS];
    Py_ssize_t *order = ser_attr_order(tree, node, opts->sort_attributes, stack_order);
    int charset_kind = opts->inject_meta ? ser_meta_charset_kind(tree, node) : 0;
    Py_ssize_t clone_start = ser_clone_start(out, node);
    Py_ssize_t attr_count = node->attr_count;
    if (clone_start >= 0 && ser_clone_attrs_refused(out, clone_start, tree, node)) {
        attr_count = 0;
    }
    for (Py_ssize_t position = 0; position < attr_count; position++) {
        th_node_attr *attr = &node->attrs[order != NULL ? order[position] : position];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        TH_WORK_STEPS_ADD(name_len + attr->value_len);
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, name, name_len);
        if (ser_put_charset_value(out, opts, charset_kind, name, name_len)) {
            continue;
        }
        const Py_UCS4 *value = attr->value;
        Py_ssize_t value_len = attr->value_len;
        Py_UCS4 *minified = NULL;
        if (minify_css && value_len > 0 && name_len == 5 && memcmp(name, "style", 5) == 0) {
            /* NULL with value_len 0 when the declarations minified to empty, rendered as an empty value below */
            value = minified = mini_style_attr_css(out, value, value_len, css_baseline, &value_len);
        }
        if (unquote && value_len == 0) {
            if (position + 1 < node->attr_count) {
                Py_ssize_t next_len;
                const th_node_attr *next = &node->attrs[order != NULL ? order[position + 1] : position + 1];
                /* after a bare name a leading "=" would open its value, so the empty value is written out */
                if (th_attr_name(tree, next->name_atom, &next_len)[0] == '=') {
                    sbuf_puts(out, "=\"\"");
                }
            }
            continue; /* an empty value reparses identically as a bare attribute name */
        }
        if (unquote && (opts->formatter != TH_FMT_NAMED || !mini_has_named_reference(value, value_len))) {
            mini_put_attr_value(out, value, value_len);
        } else {
            sbuf_puts(out, "=\"");
            sbuf_put_text(out, value, value_len, 1, opts->formatter);
            sbuf_putc(out, '"');
        }
        PyMem_Free(minified);
    }
    ser_clone_settle(out, clone_start);
    ser_attr_order_free(order, stack_order);
    sbuf_putc(out, '>');
}

/* The next sibling that counts as content for a tag-omission "immediately
   followed by" / "no more content" test: a stripped comment is invisible to the
   reparse, so it is skipped when comment stripping is on. */
static th_node *mini_next_content(th_node *node, int strip_comments) {
    th_node *sibling = node->next_sibling;
    while (sibling != NULL && strip_comments && sibling->type == TH_NODE_COMMENT) {
        sibling = sibling->next_sibling;
    }
    return sibling;
}

/* The first child that counts as content, skipping leading stripped comments so
   the start-tag omission test sees what the reparse will. */
static th_node *mini_first_content(th_node *node, int strip_comments) {
    th_node *child = node->first_child;
    while (child != NULL && strip_comments && child->type == TH_NODE_COMMENT) {
        child = child->next_sibling;
    }
    return child;
}

/* Whether node's rightmost descendant path runs through a formatting element
   (a, b, i, ...). Such an element was open when the parser closed node and is
   reconstructed as a following sibling, so omitting node's end tag would let the
   reparse fold that reconstruction back inside node. The walker also blocks
   omission while a formatting element is open as an ancestor; together they keep
   tag omission away from every adoption-agency reconstruction. The walk costs the
   depth below node, so serialize_minify runs it only under a node it does not
   descend into and carries the answer up its own ascent from there. */
static int mini_trailing_formatting(const th_node *node) {
    for (const th_node *child = node->last_child; child != NULL; child = child->last_child) {
        TH_WORK_STEP();
        if (child->type == TH_NODE_ELEMENT && (child->tag_flags & TH_TAG_FORMATTING)) {
            return 1;
        }
    }
    return 0;
}

/* The elements a </p> may be omitted before (WHATWG § 13.1.2.4). */
static int mini_is_p_follow(uint16_t atom) {
    switch (atom) {
    case TH_TAG_ADDRESS:
    case TH_TAG_ARTICLE:
    case TH_TAG_ASIDE:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_DETAILS:
    case TH_TAG_DIV:
    case TH_TAG_DL:
    case TH_TAG_FIELDSET:
    case TH_TAG_FIGCAPTION:
    case TH_TAG_FIGURE:
    case TH_TAG_FOOTER:
    case TH_TAG_FORM:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_HEADER:
    case TH_TAG_HGROUP:
    case TH_TAG_HR:
    case TH_TAG_MAIN:
    case TH_TAG_MENU:
    case TH_TAG_NAV:
    case TH_TAG_OL:
    case TH_TAG_P:
    case TH_TAG_PRE:
    case TH_TAG_SECTION:
    case TH_TAG_TABLE:
    case TH_TAG_UL:
        return 1;
    default:
        return 0;
    }
}

/* Whether the parent's end tag, on reparse, closes a p, li or dd left open inside
   it: the end tags that "generate implied end tags" before popping (WHATWG
   13.2.6.4.7), plus a template's content, which its </template> closes whole. Any
   other end tag (span, label, a, ...) walks the open stack, stops at the special
   p/li/dd and is ignored, so the child would swallow the parent's later siblings.
   The spec's own exclusions for </p> (a, audio, del, ins, map, noscript, video,
   custom elements) all fall outside this set. */
static int mini_end_closes_child(const th_node *parent) {
    if (parent->type != TH_NODE_ELEMENT) {
        return 1;
    }
    if (parent->ns != TH_NS_HTML) {
        return 0; /* a foreign parent's end tag is an ordinary in-body end tag */
    }
    switch (parent->atom) {
    case TH_TAG_ADDRESS:
    case TH_TAG_APPLET:
    case TH_TAG_ARTICLE:
    case TH_TAG_ASIDE:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_BODY:
    case TH_TAG_BUTTON:
    case TH_TAG_CAPTION:
    case TH_TAG_CENTER:
    case TH_TAG_DD:
    case TH_TAG_DETAILS:
    case TH_TAG_DIALOG:
    case TH_TAG_DIR:
    case TH_TAG_DIV:
    case TH_TAG_DL:
    case TH_TAG_DT:
    case TH_TAG_FIELDSET:
    case TH_TAG_FIGCAPTION:
    case TH_TAG_FIGURE:
    case TH_TAG_FOOTER:
    case TH_TAG_FORM:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_HEADER:
    case TH_TAG_HGROUP:
    case TH_TAG_HTML:
    case TH_TAG_LI:
    case TH_TAG_LISTING:
    case TH_TAG_MAIN:
    case TH_TAG_MARQUEE:
    case TH_TAG_MENU:
    case TH_TAG_NAV:
    case TH_TAG_OBJECT:
    case TH_TAG_OL:
    case TH_TAG_PRE:
    case TH_TAG_SEARCH:
    case TH_TAG_SECTION:
    case TH_TAG_SUMMARY:
    case TH_TAG_TD:
    case TH_TAG_TH:
    case TH_TAG_UL:
        return 1;
    default:
        return 0;
    }
}

/* An li or dd ending an li, dd or dt parent keeps its end tag: the parent's end tag may be
   omitted too, and the next item's start tag stops at the open child, the first special
   element other than address, div and p (WHATWG 13.2.6.4.7), and nests inside it. */
static int mini_end_closes_item(const th_node *parent) {
    uint16_t atom = parent->type == TH_NODE_ELEMENT ? parent->atom : TH_TAG_UNKNOWN;
    return atom != TH_TAG_LI && atom != TH_TAG_DD && atom != TH_TAG_DT && mini_end_closes_child(parent);
}

/* Whether a following sibling begins with ASCII whitespace (used by the head/
   body/caption "not followed by whitespace" omission tests). */
static int mini_starts_with_ws(th_tree *tree, th_node *node) {
    if (node->type != TH_NODE_TEXT || node->text_len == 0) {
        return 0;
    }
    const Py_UCS4 *text = need_text(tree, node);
    return text != NULL && is_space(text[0]); /* GCOVR_EXCL_BR_LINE: NULL is a lazy text OOM the walk reports */
}

static int mini_is_comment_like(th_node *node) {
    return node->type == TH_NODE_COMMENT || node->type == TH_NODE_PI;
}

/* Whether node lies within an html <atom> ancestor the parser would have "in scope",
   walking the parent chain the way has_in_scope walks the open stack: an html scoping
   element or any foreign ancestor bounds the search. rt/rp only imply-close a preceding
   sibling inside a ruby, optgroup only inside a select; outside that scope a following
   rt/rp/optgroup start tag reparents into the element instead, so its end tag must stay.
   A detached serialization root can end the ancestor chain without a document. The climb
   stops after the depth a reparse can reach and answers "not in scope", which keeps the
   end tag: a run of siblings under ancestors nested deeper than any parser builds would
   otherwise climb all of them once per sibling. */
static int mini_in_scope(const th_node *node, uint16_t atom) {
    const th_node *up = node->parent;
    for (int climbed = 0; climbed < TH_MAX_TREE_DEPTH && up != NULL && up->type == TH_NODE_ELEMENT; climbed++) {
        TH_WORK_STEP();
        if (up->ns == TH_NS_HTML && up->atom == atom) {
            return 1;
        }
        if (up->ns != TH_NS_HTML || (up->tag_flags & TH_TAG_SCOPING)) {
            return 0;
        }
        up = up->parent;
    }
    return 0;
}

/* Whether the parent's end tag pops an rt, rp, optgroup or option left open as its last child.
   An html parent's end tag does, since none of the four is special. A foreign parent's end
   tag stops at its integration point and is ignored under an html child. An rt, rp or option
   parent's end tag closes a same-named child instead (rp > rp), and may be omitted before a
   start tag that pops only the current option. An optgroup parent's end tag ignores any child
   but an option. */
static int mini_end_passes_child(const th_node *node) {
    const th_node *parent = node->parent;
    if (parent->type != TH_NODE_ELEMENT) {
        return 1;
    }
    if (parent->ns != TH_NS_HTML) {
        return 0;
    }
    switch (parent->atom) {
    case TH_TAG_RT:
    case TH_TAG_RP:
    case TH_TAG_OPTION:
        return 0;
    case TH_TAG_OPTGROUP:
        return node->atom == TH_TAG_OPTION;
    default:
        return 1;
    }
}

/* The WHATWG optional-tag rule for node's end tag, evaluated against next (the next
   content sibling, stripped comments already skipped). The formatting-reconstruction
   guard is applied separately, in ser_end_tag_omissible.
   "No more content in the parent" is next == NULL; for the special p it also needs a
   parent whose end tag closes it on reparse (mini_end_closes_child), for li and dd
   one that is not itself an item (mini_end_closes_item), and for rt, rp, optgroup and
   option one whose end tag gets past them to its own element (mini_end_passes_child). */
static int mini_end_tag_rule(th_tree *tree, th_node *node, th_node *next, int quirks) {
    int last = next == NULL;
    uint16_t na =
        (next != NULL && next->type == TH_NODE_ELEMENT && next->ns == TH_NS_HTML) ? next->atom : TH_TAG_UNKNOWN;
    switch (node->atom) {
    case TH_TAG_LI:
        return na == TH_TAG_LI || (last && mini_end_closes_item(node->parent));
    case TH_TAG_DT:
        return na == TH_TAG_DT || na == TH_TAG_DD;
    case TH_TAG_DD:
        return na == TH_TAG_DD || na == TH_TAG_DT || (last && mini_end_closes_item(node->parent));
    case TH_TAG_RT:
    case TH_TAG_RP:
        return ((na == TH_TAG_RT || na == TH_TAG_RP) && mini_in_scope(node, TH_TAG_RUBY)) ||
               (last && mini_end_passes_child(node));
    case TH_TAG_OPTGROUP:
        return (na == TH_TAG_OPTGROUP && mini_in_scope(node, TH_TAG_SELECT)) || (last && mini_end_passes_child(node));
    case TH_TAG_OPTION:
        return na == TH_TAG_OPTION || na == TH_TAG_OPTGROUP || (last && mini_end_passes_child(node));
    case TH_TAG_THEAD:
        return na == TH_TAG_TBODY || na == TH_TAG_TFOOT;
    case TH_TAG_TBODY:
        return na == TH_TAG_TBODY || na == TH_TAG_TFOOT || last;
    case TH_TAG_TFOOT:
        return last;
    case TH_TAG_TR:
        return na == TH_TAG_TR || last;
    case TH_TAG_TD:
    case TH_TAG_TH:
        return na == TH_TAG_TD || na == TH_TAG_TH || last;
    case TH_TAG_P:
        /* a quirks-mode <table> nests inside an open <p> instead of closing it */
        return (mini_is_p_follow(na) && !(quirks && na == TH_TAG_TABLE)) ||
               (last && mini_end_closes_child(node->parent));
    case TH_TAG_HTML:
    case TH_TAG_BODY:
        return next == NULL || !mini_is_comment_like(next);
    case TH_TAG_HEAD:
        return next == NULL || (!mini_is_comment_like(next) && !mini_starts_with_ws(tree, next));
    default:
        return 0;
    }
}

/* Whether node's end tag may be omitted: the WHATWG rule, blocked whenever a
   formatting element is open as an ancestor (formatting_depth), lies on node's
   trailing path (trailing_formatting), or is the following sibling, since the
   reparse would then reconstruct it across the boundary. Shared with the lossless
   serializer, which asks it before leaving out an end tag the source did not spell. */
int ser_end_tag_omissible(th_tree *tree, th_node *node, int strip_comments, int formatting_depth,
                          int trailing_formatting, int quirks) {
    /* the rule only fires when next is empty or a specific non-formatting element, so a
       reconstructed formatting element can only be reached through the trailing path */
    if (node->ns != TH_NS_HTML || formatting_depth > 0 || trailing_formatting) {
        return 0;
    }
    return mini_end_tag_rule(tree, node, mini_next_content(node, strip_comments), quirks);
}

/* Whether node's start tag may be omitted. An element carrying attributes never
   qualifies (the omitted tag would lose them); only html/head/body have a rule, so
   everything else rejects before the first-content lookup. */
static int mini_omit_start_tag(th_tree *tree, th_node *node, int strip_comments) {
    if (node->ns != TH_NS_HTML || node->attr_count != 0) {
        return 0;
    }
    if (node->atom != TH_TAG_HTML && node->atom != TH_TAG_HEAD && node->atom != TH_TAG_BODY) {
        return 0;
    }
    th_node *first = mini_first_content(node, strip_comments);
    if (node->atom == TH_TAG_HTML) {
        return first == NULL || first->type != TH_NODE_COMMENT;
    }
    if (node->atom == TH_TAG_HEAD) {
        return first == NULL || first->type == TH_NODE_ELEMENT;
    }
    /* body: omit when empty or when the first thing is not whitespace, a comment, or an element the "after head"
       insertion mode would put in the head. The WHATWG rule names meta, noscript, link, script, style and template;
       a body can also start with the base, basefont, bgsound, noframes or title the rule leaves out. */
    if (first == NULL) {
        return 1;
    }
    if (mini_is_comment_like(first) || mini_starts_with_ws(tree, first)) {
        return 0;
    }
    if (first->type != TH_NODE_ELEMENT || first->ns != TH_NS_HTML) {
        return 1;
    }
    switch (first->atom) {
    case TH_TAG_BASE:
    case TH_TAG_BASEFONT:
    case TH_TAG_BGSOUND:
    case TH_TAG_LINK:
    case TH_TAG_META:
    case TH_TAG_NOFRAMES:
    case TH_TAG_NOSCRIPT:
    case TH_TAG_SCRIPT:
    case TH_TAG_STYLE:
    case TH_TAG_TEMPLATE:
    case TH_TAG_TITLE:
        return 0;
    default:
        return 1;
    }
}

/* Whether a <script>'s type marks it as JavaScript the minifier may rewrite: 2 for "module", 1 for
   absent, empty, or a WHATWG JavaScript MIME type essence (a classic script), 0 otherwise. Any other type
   (application/json, importmap, a text/html template, ...) is data, not script, and must
   pass through untouched, so it is never handed to the JS minifier. A type padded with
   whitespace is treated conservatively as non-JS -- rare, and verbatim is always safe. */
static int script_is_js(th_tree *tree, th_node *node) {
    Py_ssize_t type_index = th_node_attr_find(tree, node, "type", 4);
    if (type_index < 0) {
        return 1; /* no type attribute: a classic script */
    }
    const Py_UCS4 *value = node->attrs[type_index].value;
    Py_ssize_t len = node->attrs[type_index].value_len;
    if (len == 0) {
        return 1; /* type="": a classic script */
    }
    static const char *const js_types[] = {
        "module",
        "text/javascript",
        "application/javascript",
        "text/ecmascript",
        "application/ecmascript",
        "application/x-ecmascript",
        "application/x-javascript",
        "text/javascript1.0",
        "text/javascript1.1",
        "text/javascript1.2",
        "text/javascript1.3",
        "text/javascript1.4",
        "text/javascript1.5",
        "text/jscript",
        "text/livescript",
        "text/x-ecmascript",
        "text/x-javascript",
    };
    for (size_t index = 0; index < sizeof(js_types) / sizeof(js_types[0]); index++) {
        if (ser_value_iequals(value, len, js_types[index])) {
            return index == 0 ? 2 : 1;
        }
    }
    return 0;
}

/* `</script` ends the element and `<!--` enters the escaped state, where a later `<script` hides the end tag; a source
   can carry either inside an escaped `<!--` section whose markers the minifier strips. `</noscript` ends an enclosing
   <noscript>: a scripting browser reads that element as raw text up to the first one, where the scripting-off parse
   built this <script> as its child. */
static int js_embeds_in_script(const Py_UCS4 *js, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (js[index] == '<' && (starts_with_ascii_ci(js + index, len - index, "</script") ||
                                 starts_with_ascii_ci(js + index, len - index, "</noscript") ||
                                 starts_with_ascii_ci(js + index, len - index, "<!--"))) {
            return 0;
        }
    }
    return 1;
}

/* Returns 0, leaving the caller to emit the content verbatim, for a non-JS or empty script, one the JS minifier cannot
   parse, or one whose minified form would not reparse in place. */
static int mini_emit_script_js(sbuf *out, th_tree *tree, th_node *node, const th_minify_opts *opts) {
    if (node->atom != TH_TAG_SCRIPT) {
        return 0; /* style/textarea/title and other raw-text elements are never JavaScript */
    }
    int kind = script_is_js(tree, node);
    if (!kind) {
        return 0;
    }
    Py_ssize_t total = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        total += child->text_len;
    }
    if (total == 0) {
        return 0; /* an empty <script>: the verbatim path emits nothing either */
    }
    Py_UCS4 *src = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
    if (src == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return 1;        /* GCOVR_EXCL_LINE: no verbatim copy stands in for the minified script */
    }
    Py_ssize_t pos = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        const Py_UCS4 *text = need_text(tree, child);
        if (text == NULL && child->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            PyMem_Free(src);                       /* GCOVR_EXCL_LINE: allocation failure */
            out->failed = 1;                       /* GCOVR_EXCL_LINE: allocation failure */
            return 1;                              /* GCOVR_EXCL_LINE: allocation failure */
        }
        memcpy(src + pos, text, (size_t)child->text_len * sizeof(Py_UCS4));
        pos += child->text_len;
    }
    Py_ssize_t out_len;
    char err[160]; /* empty after a NULL result only on allocation failure */
    Py_UCS4 *result =
        th_js_minify(src, total, opts->minify_js_fold, opts->minify_js_mangle, kind == 2, &out_len, err, sizeof(err));
    PyMem_Free(src);
    if (result == NULL) {
        out->failed |= err[0] == '\0'; /* GCOVR_EXCL_BR_LINE: an empty message is an allocation failure */
        return out->failed;            /* a parse error emits the script verbatim */
    }
    int embeds = js_embeds_in_script(result, out_len);
    if (embeds) {
        sbuf_put_ucs4(out, result, out_len);
    }
    PyMem_Free(result);
    return embeds;
}

/* Emit a <style>'s CSS content minified, returning 1 when it did. Returns 0 -- leaving the
   caller to emit the content verbatim -- for a non-style raw-text element or an empty <style>,
   so those cost nothing. A parsed <style> body can hold no </style close sequence (the parser
   would have ended the element there), and the CSS engine returns its input unchanged if its
   output would spell </style at all, so the minified stylesheet stays inside the element. The
   engine knows nothing of an enclosing <noscript>, which a scripting browser reads as raw text up to
   the first `</noscript`, where the scripting-off parse built this <style> as its child, so a minified
   stylesheet that spells one gives way to the verbatim content here. Kept out of line: only a
   <style> under minify_css reaches it, and inlined it grows the serializer's walk. */
static TH_NOINLINE int mini_emit_style_css(sbuf *out, th_tree *tree, th_node *node, int baseline) {
    if (node->atom != TH_TAG_STYLE) {
        return 0; /* script/textarea/title and other raw-text elements are never CSS */
    }
    Py_ssize_t total = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        total += child->text_len;
    }
    if (total == 0) {
        return 0; /* an empty <style>: the verbatim path emits nothing either */
    }
    Py_UCS4 *src = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
    if (src == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return 1;        /* GCOVR_EXCL_LINE: no verbatim copy stands in for the minified stylesheet */
    }
    Py_ssize_t pos = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        const Py_UCS4 *text = need_text(tree, child);
        if (text == NULL && child->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            PyMem_Free(src);                       /* GCOVR_EXCL_LINE: allocation failure */
            out->failed = 1;                       /* GCOVR_EXCL_LINE: allocation failure */
            return 1;                              /* GCOVR_EXCL_LINE: allocation failure */
        }
        memcpy(src + pos, text, (size_t)child->text_len * sizeof(Py_UCS4));
        pos += child->text_len;
    }
    Py_ssize_t css_len;
    unsigned char *result = mini_css_bytes(src, total, 0, baseline, &css_len);
    PyMem_Free(src);
    if (result == NULL && css_len != 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1;                  /* GCOVR_EXCL_LINE: allocation-failure path */
        return 1;                         /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t start = out->len;
    sbuf_put_utf8(out, (const char *)result, css_len); /* css_len 0 (a whitespace-only stylesheet) writes nothing */
    PyMem_Free(result);
    for (Py_ssize_t index = start; index < out->len; index++) {
        if (out->data[index] == '<' && starts_with_ascii_ci(out->data + index, out->len - index, "</noscript")) {
            out->len = start;
            return 0;
        }
    }
    return 1;
}

/* Whether the minified bytes reparse in quirks mode: the source tree was parsed in
   it (ser_put_html_doctype keeps it so), or a document's doctype is quirky or missing.
   An API-built tree defaults to no-quirks, but its doctype decides the reparse. */
static int mini_reparses_quirks(th_tree *tree, th_node *root) {
    if (th_tree_quirks(tree)) {
        return 1;
    }
    if (root->type != TH_NODE_DOCUMENT) {
        return 0;
    }
    for (th_node *child = root->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_DOCTYPE) {
            return th_doctype_is_quirky(child);
        }
    }
    return 1;
}

/* Whether an HTML doctype can spell the identifier. The tokenizer ends the doctype
   at a `>` inside a literal and has no escape for it, and a literal holding both
   quotes has no delimiter left. parse() stores neither, but parse_xml stores the
   first: a SystemLiteral takes any character except its own quote. */
static int mini_doctype_id_fits(const Py_UCS4 *id, Py_ssize_t len) {
    int has_double = 0;
    int has_single = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (id[index] == '>') {
            return 0;
        }
        has_double |= id[index] == '"';
        has_single |= id[index] == '\'';
    }
    return !(has_double && has_single);
}

/* Write one quoted doctype identifier, leaving it unclosed when abrupt: a `>`
   inside the literal ends the doctype and forces quirks mode. An identifier that
   fits never holds both quotes, so the other quote fits one holding `"`. */
static void mini_emit_doctype_id(sbuf *out, const Py_UCS4 *id, Py_ssize_t len, int abrupt) {
    Py_UCS4 quote = '"';
    for (Py_ssize_t index = 0; index < len; index++) {
        if (id[index] == '"') {
            quote = '\'';
        }
    }
    sbuf_putc(out, ' ');
    sbuf_putc(out, quote);
    sbuf_put_ucs4(out, id, len);
    if (!abrupt) {
        sbuf_putc(out, quote);
    }
}

/* Write the doctype with its public and system identifiers. The fragment
   serialization keeps only the name, but the identifiers pick the document mode:
   dropping a quirky one reparses the page in no-quirks mode, where a <table> closes
   the open <p> the quirks parse nested it in. A doctype whose malformed source
   forced quirks mode gets the same effect from the shortest malformed spelling:
   its last identifier left unclosed, or a stray character after the name. An
   identifier HTML cannot spell would end early and leave its tail to reparse as
   markup, so its doctype keeps only the name, and the stray character when the
   page reparses in quirks mode. Shared with the lossless serializer, whose output
   must reparse in the mode the source did. */
void ser_put_html_doctype(sbuf *out, th_tree *tree, th_node *node) {
    int quirky = th_doctype_is_quirky(node);
    int force = th_tree_quirks(tree) && !quirky;
    sbuf_puts(out, "<!DOCTYPE ");
    sbuf_put_ucs4(out, node->text, doctype_name_len(node));
    const Py_UCS4 *public_id;
    const Py_UCS4 *system_id;
    Py_ssize_t public_len;
    Py_ssize_t system_len;
    if (!th_node_doctype_ids(node, &public_id, &public_len, &system_id, &system_len)) {
        sbuf_puts(out, force ? " x>" : ">");
        return;
    }
    if (!mini_doctype_id_fits(public_id, public_len) || !mini_doctype_id_fits(system_id, system_len)) {
        sbuf_puts(out, force || quirky ? " x>" : ">");
        return;
    }
    int has_system = (node->tag_flags & TH_DOCTYPE_HAS_SYSTEM) != 0;
    if (node->tag_flags & TH_DOCTYPE_HAS_PUBLIC) {
        sbuf_puts(out, " PUBLIC");
        mini_emit_doctype_id(out, public_id, public_len, force && !has_system);
    } else {
        sbuf_puts(out, " SYSTEM");
    }
    if (has_system) {
        mini_emit_doctype_id(out, system_id, system_len, force);
    }
    sbuf_putc(out, '>');
}

/* The span the parse recorded for a noscript element. The parse records one for every noscript element it builds, in
   the order of their node sequence, so the search lands on it. */
static const th_noscript_span *mini_noscript_span(const th_minify_source *source, const th_node *node) {
    Py_ssize_t low = 0;
    Py_ssize_t high = source->span_count - 1;
    while (low < high) {
        Py_ssize_t mid = low + (high - low) / 2;
        if (source->spans[mid].seq < node->seq) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    return &source->spans[low];
}

/* Minified outer serialization. The walk is iterative like serialize_compact,
   descending through first_child and ascending through parent pointers, with a
   preserve counter so text inside pre/textarea/listing skips whitespace
   collapsing. */
static void serialize_minify(sbuf *out, th_tree *tree, th_node *root, const th_minify_opts *opts,
                             const th_serialize_opts *st) {
    th_node *node = root;
    int preserve = 0;
    int formatting = 0;
    int last_was_space =
        0; /* whether the last byte emitted is a folded space, so a space across a stripped comment is dropped */
    int quirks = mini_reparses_quirks(tree, root);
    /* The parser reads everything after a <plaintext> start tag as its text, so a
       parsed plaintext element ends the document: its end tag, and every ancestor's
       after it, would reparse as literal text. EOF closes them all instead. */
    int after_plaintext = 0;
    /* a document minify copies noscript content, and the source from a cut onward, as the source spelled it */
    const th_minify_source *source = opts->source;
    while (1) {
        TH_WORK_STEP();
        th_node *descend = NULL;
        if (TH_UNLIKELY(source != NULL) && node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML &&
            node->atom == TH_TAG_NOSCRIPT) {
            const th_noscript_span *span = mini_noscript_span(source, node);
            last_was_space = 0;
            if (!span->stable) {
                /* the caller passes a source only when the first unstable noscript is a cut, and the walk meets that
                   one before any element the source starts after it */
                sbuf_put_source(out, tree, span->start, tree->length);
                break;
            }
            /* the scripting-on parse reads this content as raw text, so it is copied as the source spelled it */
            mini_open_tag(out, tree, node, st, opts->unquote_attributes, opts->minify_css, opts->minify_css_baseline);
            sbuf_put_source(out, tree, span->content, span->end);
            ser_close_tag(out, node);
            goto walked;
        }
        switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
        case TH_NODE_ELEMENT:
            last_was_space = 0;
            /* An outer call requests the root's tags even when HTML permits omission;
               inner scope retains the context without emitting those tags. */
            if (!(st->inner && node == root) &&
                !(opts->omit_optional_tags && node != root && mini_omit_start_tag(tree, node, opts->strip_comments))) {
                mini_open_tag(out, tree, node, st, opts->unquote_attributes, opts->minify_css,
                              opts->minify_css_baseline);
            }
            ser_inject_head_meta(out, tree, node, st);
            if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
                break; /* void elements have no children or end tag */
            }
            if (!(st->inner && node == root) && ser_leading_newline_at(tree, node, opts->strip_comments)) {
                sbuf_putc(out, '\n');
            }
            if (is_rawtext_element(node, tree->scripting)) {
                Py_ssize_t content = out->len;
                /* the JS and CSS minifiers parse text alone, so a markup child keeps the content verbatim */
                if (rawtext_markup_child(node) != NULL) {
                    ser_put_rawtext_markup(out, tree, node, node->first_child, st, opts->strip_comments);
                } else if (!(opts->minify_js && mini_emit_script_js(out, tree, node, opts)) &&
                           !(opts->minify_css && mini_emit_style_css(out, tree, node, opts->minify_css_baseline))) {
                    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
                        sbuf_put_rawtext_node(out, tree, child);
                    }
                }
                if (node->atom == TH_TAG_PLAINTEXT) {
                    after_plaintext = 1;
                } else if (!(st->inner && node == root)) {
                    ser_end_script_double_escape(out, node, content, 0);
                    ser_close_tag(out, node);
                }
                break;
            }
            if (node->first_child != NULL) {
                if (mini_is_preserve_atom(node)) {
                    preserve++;
                }
                if (node->tag_flags & TH_TAG_FORMATTING) {
                    formatting++;
                }
                descend = node->first_child;
            } else if (!(st->inner && node == root) &&
                       !(opts->omit_optional_tags && node != root &&
                         ser_end_tag_omissible(tree, node, opts->strip_comments, formatting, 0, quirks))) {
                ser_close_tag(out, node);
            }
            break;
        case TH_NODE_CDATA:
            if (!ucs4_has_gt(node->text, node->text_len)) {
                last_was_space = 0;
                sbuf_puts(out, "<![CDATA[");
                sbuf_put_ucs4(out, node->text, node->text_len);
                sbuf_puts(out, "]]>");
                break;
            }
            /* a CDATA section is a Text node, so its escaped text is the one HTML form that holds a ">" */
            TH_FALLTHROUGH;
        case TH_NODE_TEXT:
            if (need_text(tree, node) == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                out->failed = 1;                                       /* GCOVR_EXCL_LINE: allocation failure */
                return;                                                /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (opts->collapse_whitespace && preserve == 0) {
                mini_put_collapsed_text(out, node->text, node->text_len, st->formatter, &last_was_space);
            } else {
                mini_put_text(out, node->text, node->text_len, st->formatter);
                last_was_space = 0;
            }
            break;
        case TH_NODE_COMMENT:
            if (!opts->strip_comments) {
                sbuf_puts(out, "<!--");
                sbuf_put_comment(out, node->text, node->text_len);
                sbuf_puts(out, "-->");
                last_was_space = 0;
            }
            break; /* a stripped comment emits nothing, so last_was_space carries across it */
        case TH_NODE_DOCTYPE:
            last_was_space = 0;
            ser_put_html_doctype(out, tree, node);
            break;
        case TH_NODE_PI:
            last_was_space = 0;
            sbuf_put_html_pi(out, node->text, node->text_len);
            break;
        case TH_NODE_CONTENT:
        case TH_NODE_DOCUMENT:
            descend = node->first_child; /* a transparent container emits only its children */
            break;
        }
    walked:
        if (descend != NULL) {
            node = descend;
            continue;
        }
        /* The ascent climbs through last children, so each ancestor's trailing path is the walk below the node it
           starts at plus the nodes it leaves. Walking from every ancestor is quadratic on a tree the parser did not
           build, since nothing caps its depth. */
        int trailing_formatting = node->last_child != NULL && mini_trailing_formatting(node);
        while (node != root) {
            /* a stripped comment is invisible to the reparse, so the walk steps over it instead of
               resetting the trailing state */
            th_node *sibling = mini_next_content(node, opts->strip_comments);
            if (sibling != NULL) {
                node = sibling;
                break;
            }
            trailing_formatting |= node->type == TH_NODE_ELEMENT && (node->tag_flags & TH_TAG_FORMATTING);
            node = node->parent;
            if (node->type == TH_NODE_ELEMENT) {
                if (mini_is_preserve_atom(node)) {
                    preserve--;
                }
                if (node->tag_flags & TH_TAG_FORMATTING) {
                    formatting--;
                }
                if (!after_plaintext && !(st->inner && node == root) &&
                    !(opts->omit_optional_tags && node != root &&
                      ser_end_tag_omissible(tree, node, opts->strip_comments, formatting, trailing_formatting,
                                            quirks))) {
                    ser_close_tag(out, node);
                    last_was_space = 0;
                }
            }
        }
        if (node == root) {
            break;
        }
    }
}

Py_UCS4 *th_node_minify(th_tree *tree, th_node *node, const th_minify_opts *minify, const th_serialize_opts *opts,
                        Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    th_serialize_opts context = *opts;
    th_xml_unwrap unwrap = {.spend = {0, 0, TH_XML_NS_BASE}};
    context.xml_unwrap = &unwrap; /* markup inside a raw-text element goes through the XML walk under xml */
    sbuf_presize_for_markup(&out, tree, node, opts->formatter);
    if (!opts->inner || node->type == TH_NODE_ELEMENT || node->type == TH_NODE_DOCUMENT ||
        node->type == TH_NODE_CONTENT) {
        serialize_minify(&out, tree, node, minify, &context);
    }
    th_xml_unwrap_free(&unwrap);
    return sbuf_finish(&out, out_len);
}
