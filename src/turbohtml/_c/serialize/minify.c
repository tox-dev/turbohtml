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
#include "serialize/js/minify.h"

#include <string.h>

/* pre/textarea/listing keep their text verbatim: a collapse inside them would
   change rendered content. Raw-text elements (script/style/...) are emitted as
   leaves by the walker, so they need no entry here. */
static int mini_is_preserve_atom(const th_node *node) {
    return node->ns == TH_NS_HTML &&
           (node->atom == TH_TAG_PRE || node->atom == TH_TAG_TEXTAREA || node->atom == TH_TAG_LISTING);
}

/* The whitespace lanes of a 2-code-point word, folded into the same SWAR probe the
   serializer uses for escapable characters so the collapse scan hops clean runs. */
static inline uint64_t mini_ws_mask(uint64_t word) {
    return swar_haslane32(word, ' ') | swar_haslane32(word, '\t') | swar_haslane32(word, '\n') |
           swar_haslane32(word, '\f') | swar_haslane32(word, '\r');
}

/* Whether a code point must be escaped when it appears in collapsed text: the
   structural characters (text never escapes the double quote), the no-break space
   under WHATWG, and -- under the NAMED formatter -- any character with a reference. */
static int mini_text_special(Py_UCS4 character, int formatter, int escape_nbsp) {
    if (formatter == TH_FMT_NAMED) {
        return sbuf_named_special(character);
    }
    return character == '&' || character == '<' || character == '>' || (escape_nbsp && character == 0xA0);
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
    int escape_nbsp = formatter == TH_FMT_WHATWG;
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
                if ((sbuf_special_mask(word, 1, 0, escape_nbsp) | mini_ws_mask(word)) != 0) {
                    break;
                }
                index += UCS4_LANES;
            }
        }
        while (index < len && !is_space(text[index]) && !mini_text_special(text[index], formatter, escape_nbsp)) {
            index++;
        }
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len && !is_space(text[index])) {
            sbuf_put_special(out, text[index], formatter);
            index++;
        }
        *last_was_space = 0;
    }
}

/* An attribute value may drop its quotes when it is non-empty, contains no ASCII
   whitespace and none of the characters that would end or re-open the value
   (" ' ` = < >), no ampersand (which could start a character reference), and does
   not end in a slash (which would merge with a following >). Anything else keeps
   the quoted form. The caller (mini_open_tag) writes an empty value as a bare name
   before reaching here, so len is always >= 1 and value[len - 1] is in bounds. */
static int mini_attr_unquotable(const Py_UCS4 *value, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = value[index];
        if (is_space(character) || character == '"' || character == '\'' || character == '`' || character == '=' ||
            character == '<' || character == '>' || character == '&') {
            return 0;
        }
    }
    return value[len - 1] != '/';
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
   to nothing yields a zero-length buffer (*out_len 0), which the callers treat as empty.
   inline_mode 1 selects the style="" declaration-list grammar over the full-stylesheet one.
   baseline (a CSSMinify year, 0 for the most portable output) bounds how new the emitted syntax
   may be. The engine is value-safe and idempotent, so the emitted CSS reparses to the same
   cascade and re-minifying is a fixpoint. */
static unsigned char *mini_css_bytes(const Py_UCS4 *src, Py_ssize_t len, int inline_mode, int baseline,
                                     Py_ssize_t *out_len) {
    Py_ssize_t utf8_len;
    unsigned char *utf8 = mini_ucs4_to_utf8(src, len, &utf8_len);
    if (utf8 == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        *out_len = 0;   /* GCOVR_EXCL_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    unsigned char *result = th_minify_css_bytes(utf8, utf8_len, inline_mode, baseline, out_len);
    PyMem_Free(utf8);
    return result;
}

/* Minify a style="" declaration list, returning its value as a freshly PyMem-allocated code-point
   buffer (caller frees) with the length in *out_len, or NULL when the declarations minify to
   nothing (the caller then renders the empty value). */
static Py_UCS4 *mini_style_attr_css(const Py_UCS4 *value, Py_ssize_t len, int baseline, Py_ssize_t *out_len) {
    Py_ssize_t css_len;
    unsigned char *result = mini_css_bytes(value, len, 1, baseline, &css_len);
    if (css_len == 0) {
        PyMem_Free(result); /* the declarations minified to nothing: the caller renders an empty value */
        *out_len = 0;
        return NULL;
    }
    sbuf decoded = {NULL, 0, 0, 0};
    sbuf_put_utf8(&decoded, (const char *)result, css_len);
    PyMem_Free(result);
    return sbuf_finish(&decoded, out_len);
}

/* Write an element's start tag, dropping redundant attribute quotes and writing a
   valueless/empty attribute as just its name when unquoting is enabled. Attribute
   order and charset normalization come from the shared output options (a normalized
   charset value always keeps its quotes). A style="" value is minified when minify_css
   is set, before the quote decision so the shorter value can also shed its quotes. */
static void mini_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_serialize_opts *opts, int unquote,
                          int minify_css, int css_baseline) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);
    Py_ssize_t stack_order[MAX_SORTED_ATTRS];
    Py_ssize_t *order = ser_attr_order(tree, node, opts->sort_attributes, stack_order);
    int charset_kind = opts->inject_meta ? ser_meta_charset_kind(tree, node) : 0;
    for (Py_ssize_t position = 0; position < node->attr_count; position++) {
        th_node_attr *attr = &node->attrs[order != NULL ? order[position] : position];
        sbuf_putc(out, ' ');
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        sbuf_put_utf8(out, name, name_len);
        if (ser_put_charset_value(out, opts, charset_kind, name, name_len)) {
            continue;
        }
        const Py_UCS4 *value = attr->value;
        Py_ssize_t value_len = attr->value_len;
        Py_UCS4 *minified = NULL;
        if (minify_css && value_len > 0 && name_len == 5 && memcmp(name, "style", 5) == 0) {
            /* NULL with value_len 0 when the declarations minified to empty, rendered as an empty value below */
            value = minified = mini_style_attr_css(value, value_len, css_baseline, &value_len);
        }
        if (unquote && value_len == 0) {
            continue; /* an empty value reparses identically as a bare attribute name */
        }
        if (unquote && mini_attr_unquotable(value, value_len)) {
            sbuf_putc(out, '=');
            sbuf_put_run(out, value, value_len); /* unquotable means no character needs escaping */
        } else {
            sbuf_puts(out, "=\"");
            sbuf_put_text(out, value, value_len, 1, opts->formatter);
            sbuf_putc(out, '"');
        }
        PyMem_Free(minified);
    }
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
   tag omission away from every adoption-agency reconstruction. */
static int mini_trailing_formatting(const th_node *node) {
    for (const th_node *child = node->last_child; child != NULL; child = child->last_child) {
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

/* A </p> may not be omitted at the end of its parent when that parent is one of
   these (its content model would make the reparsed <p> close differently), nor
   when the parent is an autonomous custom element (an unknown HTML tag). */
static int mini_p_parent_excluded(uint16_t atom) {
    switch (atom) {
    case TH_TAG_A:
    case TH_TAG_AUDIO:
    case TH_TAG_DEL:
    case TH_TAG_INS:
    case TH_TAG_MAP:
    case TH_TAG_NOSCRIPT:
    case TH_TAG_VIDEO:
    case TH_TAG_UNKNOWN:
        return 1;
    default:
        return 0;
    }
}

/* Whether a following sibling begins with ASCII whitespace (used by the head/
   body/caption "not followed by whitespace" omission tests). */
static int mini_starts_with_ws(th_tree *tree, th_node *node) {
    return node->type == TH_NODE_TEXT && node->text_len > 0 && is_space(need_text(tree, node)[0]);
}

/* Whether node lies within an html <atom> ancestor the parser would have "in scope",
   walking the parent chain the way has_in_scope walks the open stack: an html scoping
   element or any foreign ancestor bounds the search. rt/rp only imply-close a preceding
   sibling inside a ruby, optgroup only inside a select; outside that scope a following
   rt/rp/optgroup start tag reparents into the element instead, so its end tag must stay.
   node itself is never a boundary (it is one of rt/rp/optgroup), so the walk starts at
   its parent, which the caller's node != root guard keeps non-NULL. */
static int mini_in_scope(const th_node *node, uint16_t atom) {
    for (const th_node *up = node->parent; up->type == TH_NODE_ELEMENT; up = up->parent) {
        if (up->ns == TH_NS_HTML && up->atom == atom) {
            return 1;
        }
        if (up->ns != TH_NS_HTML || (up->tag_flags & TH_TAG_SCOPING)) {
            return 0;
        }
    }
    return 0;
}

/* The WHATWG optional-tag rule for node's end tag, evaluated against next (the next
   content sibling, stripped comments already skipped). This is the cheap test; the
   formatting-reconstruction guard is applied separately so its rightmost-path walk
   only runs for an element the rule already deems omittable, not every element.
   "No more content in the parent" is just next == NULL: the parent's own close (or
   its omission) reconstructs the element, so dropping the end tag stays round-trip
   safe even when the parent is a document or template content node. */
static int mini_end_tag_rule(th_tree *tree, th_node *node, th_node *next) {
    int last = next == NULL;
    uint16_t na =
        (next != NULL && next->type == TH_NODE_ELEMENT && next->ns == TH_NS_HTML) ? next->atom : TH_TAG_UNKNOWN;
    switch (node->atom) {
    case TH_TAG_LI:
        return na == TH_TAG_LI || last;
    case TH_TAG_DT:
        return na == TH_TAG_DT || na == TH_TAG_DD;
    case TH_TAG_DD:
        return na == TH_TAG_DD || na == TH_TAG_DT || last;
    case TH_TAG_RT:
    case TH_TAG_RP:
        return ((na == TH_TAG_RT || na == TH_TAG_RP) && mini_in_scope(node, TH_TAG_RUBY)) || last;
    case TH_TAG_OPTGROUP:
        return (na == TH_TAG_OPTGROUP && mini_in_scope(node, TH_TAG_SELECT)) || last;
    case TH_TAG_OPTION:
        return na == TH_TAG_OPTION || na == TH_TAG_OPTGROUP || last;
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
        return mini_is_p_follow(na) ||
               (last && node->parent->ns == TH_NS_HTML && !mini_p_parent_excluded(node->parent->atom));
    case TH_TAG_HTML:
    case TH_TAG_BODY:
        return next == NULL || next->type != TH_NODE_COMMENT; /* not immediately followed by a comment */
    case TH_TAG_HEAD:
        return next == NULL || (next->type != TH_NODE_COMMENT && !mini_starts_with_ws(tree, next));
    default:
        return 0;
    }
}

/* Whether node's end tag may be omitted: the WHATWG rule, blocked whenever a
   formatting element is open as an ancestor (formatting_depth), lies on node's
   trailing path, or is the following sibling, since the reparse would then
   reconstruct it across the boundary. The expensive trailing-path walk is deferred
   until the cheap rule has already accepted the element. */
static int mini_omit_end_tag(th_tree *tree, th_node *node, const th_minify_opts *opts, int formatting_depth) {
    if (node->ns != TH_NS_HTML || formatting_depth > 0) {
        return 0;
    }
    th_node *next = mini_next_content(node, opts->strip_comments);
    if (!mini_end_tag_rule(tree, node, next)) {
        return 0;
    }
    /* the rule only fires when next is empty or a specific non-formatting element, so a
       reconstructed formatting element can only be reached through the trailing path */
    return !mini_trailing_formatting(node);
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
    /* body: omit when empty or when the first thing is not whitespace, a comment, or
       one of the elements the WHATWG rule keeps the body tag before */
    if (first == NULL) {
        return 1;
    }
    if (first->type == TH_NODE_COMMENT || mini_starts_with_ws(tree, first)) {
        return 0;
    }
    return !(first->type == TH_NODE_ELEMENT &&
             (first->atom == TH_TAG_META || first->atom == TH_TAG_LINK || first->atom == TH_TAG_SCRIPT ||
              first->atom == TH_TAG_STYLE || first->atom == TH_TAG_TEMPLATE));
}

/* Whether a <script>'s type marks it as JavaScript the minifier may rewrite: absent,
   empty, "module", or a WHATWG JavaScript MIME type essence. Any other type
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
            return 1;
        }
    }
    return 0;
}

/* Emit a <script>'s JavaScript content minified, returning 1 when it did. Returns 0 --
   leaving the caller to emit the content verbatim -- for a non-JS script, an empty one, or
   a script the JS minifier cannot parse, so one bad <script> never breaks serialization. */
static int mini_emit_script_js(sbuf *out, th_tree *tree, th_node *node, const th_minify_opts *opts) {
    if (node->atom != TH_TAG_SCRIPT) {
        return 0; /* style/textarea/title and other raw-text elements are never JavaScript */
    }
    if (!script_is_js(tree, node)) {
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
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 0;      /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t pos = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        memcpy(src + pos, need_text(tree, child), (size_t)child->text_len * sizeof(Py_UCS4));
        pos += child->text_len;
    }
    /* errlen 0: the HTML path discards the message and falls back to verbatim instead */
    Py_ssize_t out_len;
    Py_UCS4 *result = th_js_minify(src, total, opts->minify_js_fold, opts->minify_js_mangle, &out_len, NULL, 0);
    PyMem_Free(src);
    if (result == NULL) {
        return 0; /* a parse error (or allocation failure): emit the script verbatim */
    }
    sbuf_put_ucs4(out, result, out_len);
    PyMem_Free(result);
    return 1;
}

/* Emit a <style>'s CSS content minified, returning 1 when it did. Returns 0 -- leaving the
   caller to emit the content verbatim -- for a non-style raw-text element or an empty <style>,
   so those cost nothing. A parsed <style> body can hold no </style close sequence (the parser
   would have ended the element there), and the CSS engine never synthesizes one, so the
   minified stylesheet stays inside the element and reparses to the same raw-text node. */
static int mini_emit_style_css(sbuf *out, th_tree *tree, th_node *node, int baseline) {
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
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 0;      /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t pos = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        memcpy(src + pos, need_text(tree, child), (size_t)child->text_len * sizeof(Py_UCS4));
        pos += child->text_len;
    }
    Py_ssize_t css_len;
    unsigned char *result = mini_css_bytes(src, total, 0, baseline, &css_len);
    PyMem_Free(src);
    sbuf_put_utf8(out, (const char *)result, css_len); /* css_len 0 (a whitespace-only stylesheet) writes nothing */
    PyMem_Free(result);
    return 1;
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
    while (1) {
        th_node *descend = NULL;
        switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
        case TH_NODE_ELEMENT:
            last_was_space = 0;
            /* the serialization root is what the caller asked to serialize, so its own tags
               always render (matching the plain serializer); only inner tags may be omitted */
            if (!(opts->omit_optional_tags && node != root && mini_omit_start_tag(tree, node, opts->strip_comments))) {
                mini_open_tag(out, tree, node, st, opts->unquote_attributes, opts->minify_css,
                              opts->minify_css_baseline);
            }
            ser_inject_head_meta(out, tree, node, st);
            if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
                break; /* void elements have no children or end tag */
            }
            if (ser_needs_leading_newline(tree, node)) {
                sbuf_putc(out, '\n');
            }
            if (is_rawtext_element(node, tree->scripting)) {
                int emitted = 0;
                if (opts->minify_js) {
                    emitted = mini_emit_script_js(out, tree, node, opts);
                }
                if (!emitted && opts->minify_css) {
                    emitted = mini_emit_style_css(out, tree, node, opts->minify_css_baseline);
                }
                if (!emitted) {
                    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
                        sbuf_put_ucs4(out, need_text(tree, child), child->text_len);
                    }
                }
                ser_close_tag(out, node); /* a raw-text element's end tag is never one the optional-tag rules omit */
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
            } else if (!(opts->omit_optional_tags && node != root && mini_omit_end_tag(tree, node, opts, formatting))) {
                ser_close_tag(out, node);
            }
            break;
        case TH_NODE_TEXT:
            if (opts->collapse_whitespace && preserve == 0) {
                mini_put_collapsed_text(out, need_text(tree, node), node->text_len, st->formatter, &last_was_space);
            } else {
                sbuf_put_text(out, need_text(tree, node), node->text_len, 0, st->formatter);
                last_was_space = 0;
            }
            break;
        case TH_NODE_COMMENT:
            if (!opts->strip_comments) {
                sbuf_puts(out, "<!--");
                sbuf_put_ucs4(out, node->text, node->text_len);
                sbuf_puts(out, "-->");
                last_was_space = 0;
            }
            break; /* a stripped comment emits nothing, so last_was_space carries across it */
        case TH_NODE_DOCTYPE:
            last_was_space = 0;
            sbuf_puts(out, "<!DOCTYPE ");
            sbuf_put_ucs4(out, node->text, doctype_name_len(node));
            sbuf_putc(out, '>');
            break;
        /* GCOVR_EXCL_START: a WHATWG-conformant parse folds a PI to a comment and
           a CDATA section to text, so the minifier, which only serves parsed
           trees, never reaches these node types. */
        case TH_NODE_PI:
            sbuf_puts(out, "<?");
            sbuf_put_ucs4(out, node->text, node->text_len);
            sbuf_putc(out, '>');
            break;
        case TH_NODE_CDATA:
            sbuf_puts(out, "<![CDATA[");
            sbuf_put_ucs4(out, node->text, node->text_len);
            sbuf_puts(out, "]]>");
            break;
        /* GCOVR_EXCL_STOP */
        case TH_NODE_CONTENT:
        case TH_NODE_DOCUMENT:
            descend = node->first_child; /* a transparent container emits only its children */
            break;
        }
        if (descend != NULL) {
            node = descend;
            continue;
        }
        while (node != root) {
            if (node->next_sibling != NULL) {
                node = node->next_sibling;
                break;
            }
            node = node->parent;
            if (node->type == TH_NODE_ELEMENT) {
                if (mini_is_preserve_atom(node)) {
                    preserve--;
                }
                if (node->tag_flags & TH_TAG_FORMATTING) {
                    formatting--;
                }
                if (!(opts->omit_optional_tags && node != root && mini_omit_end_tag(tree, node, opts, formatting))) {
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
    sbuf out = {NULL, 0, 0, 0};
    sbuf_presize_for_root(&out, tree, node);
    serialize_minify(&out, tree, node, minify, opts);
    return sbuf_finish(&out, out_len);
}
