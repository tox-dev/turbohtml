/* The lossless serializer to_source() drives: it re-emits the verbatim source
   bytes of every element start tag, end tag, and text run the parse left untouched,
   so a round trip over an unmodified tree reproduces the input -- author quoting,
   tag-name case, character-reference spelling, and insignificant whitespace intact.
   Only a node a mutation touched is reserialized: an element whose attributes
   changed rebuilds its start tag, an edited text run re-escapes, and an inserted
   element (which carries no source location) serializes canonically, while its
   untouched siblings still copy their original span. This is the tree-based
   counterpart to the streaming rewriter, built on the per-element spans a parse
   with source locations records. */

#include "serialize/internal.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <string.h>

/* The three arms mirror the input's storage kind. */
void sbuf_put_source(sbuf *out, th_tree *tree, Py_ssize_t start, Py_ssize_t end) {
    Py_ssize_t len = end - start;
    sbuf_reserve(out, len);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    Py_UCS4 *dst = out->data + out->len;
    if (tree->kind == PyUnicode_4BYTE_KIND) {
        memcpy(dst, (const Py_UCS4 *)tree->data + start, (size_t)len * sizeof(Py_UCS4));
    } else if (tree->kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *bytes = (const uint8_t *)tree->data + start;
        for (Py_ssize_t index = 0; index < len; index++) {
            dst[index] = bytes[index];
        }
    } else {
        const uint16_t *units = (const uint16_t *)tree->data + start;
        for (Py_ssize_t index = 0; index < len; index++) {
            dst[index] = units[index];
        }
    }
    out->len += len;
}

/* The WHATWG-conformant escaping the reserialized parts fall back to. */
static const th_serialize_opts lossless_opts = {TH_FMT_WHATWG, 0,    0,    NULL, 0,    0, 0,   NULL, 0,
                                                NULL,          NULL, NULL, NULL, NULL, 0, NULL};

/* Whether the source code points [start, end) hold a "<" or "&", which element content reads back as markup or a
   character reference. A span is whatever slice of the input the tokenizer left untouched, not only data-state text: a
   CDATA section at an integration point, raw-text or <plaintext> content, and a stray "<" all stay spans. */
static int source_has_markup(const th_tree *tree, Py_ssize_t start, Py_ssize_t end) {
    if (tree->kind == PyUnicode_1BYTE_KIND) {
        const Py_UCS1 *bytes = (const Py_UCS1 *)tree->data + start;
        Py_ssize_t length = end - start;
        return memchr(bytes, '<', length) != NULL || memchr(bytes, '&', length) != NULL;
    }
    for (Py_ssize_t index = start; index < end; index++) {
        Py_UCS4 character = PyUnicode_READ(tree->kind, tree->data, index);
        if (character == '<' || character == '&') {
            return 1;
        }
    }
    return 0;
}

/* Emit a text node: its verbatim source span while it is still the zero-copy slice
   the parse left and that slice reads back as the same text, or the WHATWG escaping
   of its current code points -- for a span that holds markup characters, and once a
   read realized the text or an edit replaced it. */
static void lossless_put_text(sbuf *out, th_tree *tree, th_node *node) {
    if (text_is_span(node)) {
        Py_ssize_t offset = text_span_offset(node);
        if (!source_has_markup(tree, offset, offset + node->text_len)) {
            sbuf_put_source(out, tree, offset, offset + node->text_len);
            return;
        }
        need_text(tree, node);
    }
    if (node->text == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: a lazy text OOM, now or earlier */
        out->failed = 1;                            /* GCOVR_EXCL_LINE: allocation failure */
    } else { /* GCOVR_EXCL_LINE: llvm-cov assigns this line to the allocation-failure edge */
        sbuf_put_text(out, node->text, node->text_len, 0, TH_FMT_WHATWG);
    }
}

/* Whether node, a Text or CDATA node below the walk root, sits in a raw-text element: the walk reaches one only after
   the DOM API gave that element a markup child. */
static int lossless_in_rawtext(th_tree *tree, const th_node *node, const th_node *root) {
    return node != root && node->parent->type == TH_NODE_ELEMENT && is_rawtext_element(node->parent, tree->scripting);
}

/* Emit a raw-text element's child: its verbatim source span while it is still the
   zero-copy slice the parse left, else its current code points literally
   (raw-text content is never escaped, and a CR a caller wrote there is folded to LF, as sbuf_put_rawtext does). */
static void lossless_put_rawtext(sbuf *out, th_tree *tree, th_node *node) {
    if (text_is_span(node)) {
        Py_ssize_t offset = text_span_offset(node);
        sbuf_put_source(out, tree, offset, offset + node->text_len);
    } else {
        sbuf_put_rawtext(out, node->text, node->text_len);
    }
}

/* What the walk carries from one step to the next: the formatting elements open around the cursor that block
   end-tag omission (lossless_formatting_blocks), and whether a parsed <plaintext> has been written, after which
   every end tag would reparse as its text (LOSSLESS_AFTER_PLAINTEXT), or whether the walk is below rawtext, a raw-text
   element with a markup child, whose content starts at rawtext_content (LOSSLESS_IN_RAWTEXT). Both live in state so
   that lossless_close_tag tests one field for either. */
enum { LOSSLESS_AFTER_PLAINTEXT = 1, LOSSLESS_IN_RAWTEXT = 2 };
typedef struct {
    int formatting;
    int state;
    th_node *rawtext;
    Py_ssize_t rawtext_content;
} lossless_walk;

/* Whether node is a formatting element that blocks leaving out an end tag around it, as every formatting element
   does in the minifier. One whose end tag the source spelled, and which is written back from the source, does not:
   it is closed in the output where it was closed in the input, so nothing is left for a reparse to reconstruct
   across the gap, and <li><a href=x>y</a><li> stays as written. */
static int lossless_formatting_blocks(th_tree *tree, th_node *node) {
    if (node->type != TH_NODE_ELEMENT || !(node->tag_flags & TH_TAG_FORMATTING)) {
        return 0;
    }
    const th_src_loc *loc = th_node_source_location(tree, node);
    return loc == NULL || loc->end_dirty || !loc->has_end_tag;
}

/* Whether the source closed a foreign element with the "/>" of its own start tag. */
static int lossless_self_closed(const th_node *node, const th_src_loc *loc) {
    return node->ns != TH_NS_HTML && loc->self_closing;
}

/* Write an element's start tag: its verbatim source bytes when the parse recorded a
   location the mutation API has not since dirtied, else a canonical rebuild from the
   current attributes. A self-closed foreign element given children since is rebuilt
   too, as its "/>" would close it ahead of them. */
static void lossless_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_src_loc *loc) {
    if (loc != NULL && !loc->start_dirty && !(lossless_self_closed(node, loc) && node->first_child != NULL)) {
        sbuf_put_source(out, tree, loc->start_tag.start_offset, loc->start_tag.end_offset);
    } else {
        ser_open_tag(out, tree, node, &lossless_opts);
        sbuf_putc(out, '>');
    }
}

/* Whether an element the source never closed with an end tag still ends where the tree says once none is written.
   What closed it in the source need not be written again in its place: the token may have left no node (the <body>
   that breaks out of an <svg>, a second <select>), the parser may have moved it (foster parenting), or an edit may
   have removed it. So the answer comes from the tree as it stands: a foreign element ends without an end tag only
   through the "/>" of the start tag still written for it, an HTML element only where the optional-tag rule lets its
   end tag go, which needs a parent to supply what follows (a detached root has none). trailing says a blocking
   formatting element lies on node's last-child path. */
static int lossless_end_tag_implied(th_tree *tree, th_node *node, const th_src_loc *loc, const lossless_walk *walk,
                                    int trailing) {
    if (node->ns != TH_NS_HTML) {
        return lossless_self_closed(node, loc) && !loc->start_dirty && node->first_child == NULL;
    }
    return node->parent != NULL &&
           ser_end_tag_omissible(tree, node, 0, walk->formatting, trailing, th_tree_quirks(tree));
}

/* Write an element's end tag: its verbatim source bytes when the source closed it,
   nothing when the source left it implicitly closed and a reparse closes it at the
   same place, and a canonical close tag otherwise, as for a synthetic or inserted
   element the parse never located or a source-closed one renamed since. A parsed
   <plaintext> runs to the end of the input, so it and its ancestors get none. */
/* lossless_close_tag's rare start, taken while walk->state is set: whether no end tag goes out at all, as after a
   parsed <plaintext>. Leaving the raw-text element the walk entered for its markup child, it first adds the end tag
   a double-escaped script needs, now that all of its content is written. */
TH_NOINLINE static int lossless_close_held(sbuf *out, th_node *node, lossless_walk *walk) {
    if (walk->state & LOSSLESS_AFTER_PLAINTEXT) {
        return 1;
    }
    if (node == walk->rawtext) {
        walk->state = 0;
        ser_end_script_double_escape(out, node, walk->rawtext_content, 0);
    }
    return 0;
}

static void lossless_close_tag(sbuf *out, th_tree *tree, th_node *node, const th_src_loc *loc, lossless_walk *walk,
                               int trailing) {
    if (walk->state != 0 && lossless_close_held(out, node, walk)) {
        return;
    }
    int located = loc != NULL && !loc->end_dirty;
    if (located && loc->has_end_tag) {
        sbuf_put_source(out, tree, loc->end_tag.start_offset, loc->end_tag.end_offset);
    } else if (located && node->ns == TH_NS_HTML && node->atom == TH_TAG_PLAINTEXT) {
        walk->state |= LOSSLESS_AFTER_PLAINTEXT;
    } else if (!located || !lossless_end_tag_implied(tree, node, loc, walk, trailing)) {
        ser_close_tag(out, node);
    }
}

/* Write a raw-text element's text children and return its first markup child, which only the DOM API inserts and the
   walk then takes over, or NULL after writing the end tag. Below another raw-text element's markup child the end tag
   is text, and that element's end tag decides. */
TH_NOINLINE static th_node *lossless_put_rawtext_element(sbuf *out, th_tree *tree, th_node *node, const th_src_loc *loc,
                                                         lossless_walk *walk) {
    Py_ssize_t content = out->len;
    th_node *child = node->first_child;
    for (; child != NULL && (child->type == TH_NODE_TEXT || child->type == TH_NODE_CDATA);
         child = child->next_sibling) {
        lossless_put_rawtext(out, tree, child);
    }
    if (child != NULL) {
        if (walk->state == 0) {
            walk->state = LOSSLESS_IN_RAWTEXT;
            walk->rawtext = node;
            walk->rawtext_content = content;
        }
        return child;
    }
    if (walk->state == 0) {
        ser_end_script_double_escape(out, node, content, 1);
    }
    lossless_close_tag(out, tree, node, loc, walk, 0);
    return NULL;
}

/* Emit one node and return the next the walk rooted at root visits, or NULL once the
   subtree is done. Iterative -- descending through first_child, ascending through
   parent pointers -- so a tree of any depth serializes without one C stack frame per
   level, the same shape serialize_compact_step uses. */
static th_node *lossless_step(sbuf *out, th_tree *tree, th_node *node, th_node *root, lossless_walk *walk) {
    th_node *descend = NULL;
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT: {
        const th_src_loc *loc = th_node_source_location(tree, node);
        lossless_open_tag(out, tree, node, loc);
        if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
            break; /* void elements have no children or end tag */
        }
        if (ser_needs_leading_newline(tree, node)) {
            sbuf_putc(out, '\n'); /* re-emit the newline pre/textarea/listing dropped on read */
        }
        if (is_rawtext_element(node, tree->scripting)) {
            descend = lossless_put_rawtext_element(out, tree, node, loc, walk);
            break;
        }
        if (node->first_child != NULL) {
            walk->formatting += lossless_formatting_blocks(tree, node);
            descend = node->first_child;
        } else {
            lossless_close_tag(out, tree, node, loc, walk, 0);
        }
        break;
    }
    case TH_NODE_TEXT:
        /* the parent decides for a span too: its source bytes are literal in a raw-text element, escaped elsewhere */
        if (lossless_in_rawtext(tree, node, root)) {
            lossless_put_rawtext(out, tree, node);
        } else {
            lossless_put_text(out, tree, node);
        }
        break;
    case TH_NODE_COMMENT:
        sbuf_puts(out, "<!--");
        sbuf_put_comment(out, node->text, node->text_len);
        sbuf_puts(out, "-->");
        break;
    case TH_NODE_DOCTYPE:
        /* the name alone would move a quirks-mode document to no-quirks, where a <table> closes the open <p> */
        ser_put_html_doctype(out, tree, node);
        break;
    case TH_NODE_PI:
        sbuf_put_html_pi(out, node->text, node->text_len);
        break;
    case TH_NODE_CDATA:
        if (lossless_in_rawtext(tree, node, root)) {
            sbuf_put_ucs4(out, node->text, node->text_len);
        } else if (ucs4_has_gt(node->text, node->text_len)) {
            /* a CDATA section is a Text node, so its escaped text is the one HTML form that holds a ">" */
            sbuf_put_text(out, node->text, node->text_len, 0, TH_FMT_WHATWG);
        } else {
            sbuf_puts(out, "<![CDATA[");
            sbuf_put_ucs4(out, node->text, node->text_len);
            sbuf_puts(out, "]]>");
        }
        break;
    case TH_NODE_CONTENT:
    case TH_NODE_DOCUMENT:
        descend = node->first_child; /* a transparent container emits only its children */
        break;
    }
    if (descend != NULL) {
        return descend;
    }
    /* ascend toward the root, closing each element whose children are done; the climb passes last children only,
       so whether a blocking formatting element lies on an element's last-child path is carried up with it */
    int trailing = 0;
    while (node != root) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        trailing |= lossless_formatting_blocks(tree, node);
        node = node->parent;
        if (node->type == TH_NODE_ELEMENT) {
            walk->formatting -= lossless_formatting_blocks(tree, node);
            lossless_close_tag(out, tree, node, th_node_source_location(tree, node), walk, trailing);
        }
    }
    return NULL;
}

Py_UCS4 *th_node_serialize_source(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    sbuf_presize_for_root(&out, tree, node);
    lossless_walk walk = {0, 0, NULL, 0};
    /* a subtree gets the end tags the whole document's walk writes for it, so the count starts above the root */
    for (th_node *up = node->parent; up != NULL; up = up->parent) {
        walk.formatting += lossless_formatting_blocks(tree, up);
    }
    th_node *cursor = node;
    while (cursor != NULL) {
        cursor = lossless_step(&out, tree, cursor, node, &walk);
    }
    return sbuf_finish(&out, out_len);
}
