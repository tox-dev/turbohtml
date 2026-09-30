/* Renders a parsed tree back to HTML -- the round-trippable document/fragment
   markup, the html5lib "#document" debug dump, and the indented pretty form,
   under the WHATWG, minimal, or named-entity escape policy the caller picks. */

#include "serialize/internal.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <string.h>

/* Write an attribute's displayed name (the form the #document line uses) into buf:
   namespaced foreign attributes show "prefix localname", everything else is the
   stored name (foreign attribute case adjustments are applied at construction). */
static void render_attr_name(th_tree *tree, const th_node *node, const th_node_attr *attr, char *buf, size_t bufsize) {
    Py_ssize_t name_len;
    const char *name = th_attr_name(tree, attr->name_atom, &name_len);
    int to_space = node->ns != TH_NS_HTML && foreign_attr_namespaced(name, name_len);
    size_t write_index = 0;
    if (to_space && memchr(name, ':', (size_t)name_len) == NULL) {
        /* the only namespaced attribute without a prefix to split on is plain xmlns,
           which belongs to the xmlns namespace and renders as "xmlns xmlns"; the
           6-byte prefix always fits the 128-byte buffer, so no bound check is needed */
        memcpy(buf, "xmlns ", 6);
        write_index = 6;
    }
    for (const char *character = name; *character != '\0' && write_index + 1 < bufsize; character++) {
        buf[write_index++] = (to_space && *character == ':') ? ' ' : *character;
    }
    buf[write_index] = '\0';
}

/* Emit one node's #document line (and, for an element, its sorted attribute
   lines). The children are walked by serialize_node, so this never recurses. */
static void serialize_node_line(sbuf *out, th_tree *tree, th_node *node, int depth) {
    if (node->type == TH_NODE_TEXT) {
        need_text(tree, node); /* realize a zero-copy span before output */
    }
    /* html5lib format: "| " then two spaces per depth level, then the node */
    sbuf_puts(out, "| ");
    for (int index = 0; index < depth; index++) {
        sbuf_puts(out, "  ");
    }
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_DOCTYPE:
        sbuf_puts(out, "<!DOCTYPE ");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_putc(out, '>');
        break;
    case TH_NODE_COMMENT:
        sbuf_puts(out, "<!-- ");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, " -->");
        break;
    case TH_NODE_TEXT:
        sbuf_putc(out, '"');
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_putc(out, '"');
        break;
    case TH_NODE_ELEMENT:
        sbuf_putc(out, '<');
        if (node->ns == TH_NS_SVG) {
            sbuf_puts(out, "svg ");
        } else if (node->ns == TH_NS_MATHML) {
            sbuf_puts(out, "math ");
        }
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_putc(out, '>');
        break;
    case TH_NODE_CONTENT:
        sbuf_puts(out, "content");
        break;
    case TH_NODE_PI:
        sbuf_puts(out, "<?");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, "?>");
        break;
    /* GCOVR_EXCL_START: the HTML parser folds a foreign CDATA section to text, so
       the #document dumper, which serves parsed trees, never reaches this type. */
    case TH_NODE_CDATA:
        sbuf_puts(out, "<![CDATA[");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, "]]>");
        break;
        /* GCOVR_EXCL_STOP */
    case TH_NODE_DOCUMENT: /* GCOVR_EXCL_LINE: the document node is the serialization root, never a line itself */
        break;             /* GCOVR_EXCL_LINE: same -- the document node is never reached as a child */
    }
    sbuf_putc(out, '\n');
    /* attributes: each on its own deeper line, output in lexicographic name
       order (the html5lib #document format sorts them). */
    Py_ssize_t order[MAX_SORTED_ATTRS];
    Py_ssize_t count =
        node->type == TH_NODE_ELEMENT ? (node->attr_count < MAX_SORTED_ATTRS ? node->attr_count : MAX_SORTED_ATTRS) : 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        order[index] = index;
    }
    /* Sort on the displayed name so a namespaced attribute (shown as
       "prefix localname") orders by its space, which precedes a literal colon. */
    char ke_buf[MAX_ATTR_NAME];
    char cmp_buf[MAX_ATTR_NAME];
    for (Py_ssize_t index = 1; index < count; index++) { /* insertion sort; attribute counts are tiny */
        Py_ssize_t key = order[index];
        render_attr_name(tree, node, &node->attrs[key], ke_buf, sizeof(ke_buf));
        Py_ssize_t prev = index - 1;
        while (prev >= 0 && (render_attr_name(tree, node, &node->attrs[order[prev]], cmp_buf, sizeof(cmp_buf)),
                             strcmp(cmp_buf, ke_buf) > 0)) {
            order[prev + 1] = order[prev];
            prev--;
        }
        order[prev + 1] = key;
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        th_node_attr *attr = &node->attrs[order[index]];
        sbuf_puts(out, "| ");
        for (int level = 0; level <= depth; level++) {
            sbuf_puts(out, "  ");
        }
        /* xlink:/xml:/xmlns: serialize with a space; the mixed-case spelling of an
           SVG/MathML attribute is already stored, applied at construction */
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        if (node->ns != TH_NS_HTML && foreign_attr_namespaced(name, name_len)) {
            if (memchr(name, ':', (size_t)name_len) == NULL) {
                sbuf_puts(out, "xmlns "); /* plain xmlns (no prefix to split on) renders with its namespace prefix */
            }
            for (const char *character = name; *character; character++) {
                sbuf_putc(out, *character == ':' ? (Py_UCS4)' ' : (Py_UCS4)*character);
            }
        } else {
            sbuf_put_utf8(out, name, name_len);
        }
        sbuf_puts(out, "=\"");
        sbuf_put_ucs4(out, attr->value, attr->value_len);
        sbuf_putc(out, '"');
        sbuf_putc(out, '\n');
    }
}

/* Dump node and its subtree in the html5lib #document format. The walk is
   iterative (descend through first_child, ascend through parent) so an
   arbitrarily deep tree never overflows the C stack. */
static void serialize_node(sbuf *out, th_tree *tree, th_node *root, int depth0) {
    th_node *node = root;
    int depth = depth0;
    while (1) {
        serialize_node_line(out, tree, node, depth);
        if (node->first_child != NULL) {
            node = node->first_child;
            depth += 1;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
            depth -= 1;
        }
        if (node == root) {
            break;
        }
        node = node->next_sibling;
    }
}

Py_UCS4 *th_tree_serialize(th_tree *tree, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    /* a fragment serializes the context root's children; a document serializes
       the document node's children */
    th_node *top = tree->fragment_root != NULL ? tree->fragment_root : tree->document;
    for (th_node *child = top->first_child; child != NULL; child = child->next_sibling) {
        serialize_node(&out, tree, child, 0);
    }
    if (out.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(out.data); /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    *out_len = out.len;
    if (out.data == NULL) {
        /* an empty tree (whitespace-only input) serializes to nothing; hand
           back a real zero-length allocation so NULL stays unambiguously the
           failure signal for the caller */
        out.data = PyMem_Malloc(1);
        if (out.data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
    }
    return out.data;
}

/* Emit one node under the compact (WHATWG fragment) layout and return the next node
   the walk rooted at root visits, or NULL once the subtree is fully written. Split
   out of serialize_compact so serialize_iter can resume the walk one node at a time
   between chunks: the one-shot wrapper below loops it to exhaustion and the streaming
   driver stops it at a chunk boundary, so both paths emit byte-identical markup. The
   walk is iterative -- descending through first_child, ascending through parent
   pointers -- so a tree of any depth serializes without one C stack frame per level. */
static th_node *serialize_compact_step(sbuf *out, th_tree *tree, th_node *node, th_node *root,
                                       const th_serialize_opts *opts) {
    th_node *descend = NULL;
    if (opts->inner && node == root) {
        if (!opts->xml && node->type == TH_NODE_ELEMENT) {
            if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
                return NULL;
            }
            ser_inject_head_meta(out, tree, node, opts);
        }
        return node->first_child;
    }
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT:
        ser_open_tag(out, tree, node, opts);
        if (opts->xml) {
            /* XML syntax: every empty element self-closes, no void/raw-text special
               casing, so a childless element ends the tag and a parent descends */
            if (node->first_child == NULL) {
                sbuf_puts(out, "/>");
            } else {
                sbuf_putc(out, '>');
                descend = node->first_child;
            }
            break;
        }
        sbuf_putc(out, '>');
        ser_inject_head_meta(out, tree, node, opts);
        if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
            break; /* void elements have no children or end tag */
        }
        if (ser_needs_leading_newline(tree, node)) {
            sbuf_putc(out, '\n');
        }
        if (is_rawtext_element(node, tree->scripting)) {
            /* a rawtext element's children are always text nodes */
            for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
                sbuf_put_ucs4(out, need_text(tree, child), child->text_len);
            }
            ser_close_tag(out, node);
            break;
        }
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            ser_close_tag(out, node); /* an empty element still takes an end tag */
        }
        break;
    case TH_NODE_CDATA:
        if (opts->xml) {
            sbuf_put_xml_cdata(out, node->text, node->text_len);
            break;
        }
        if (!ucs4_has_gt(node->text, node->text_len)) {
            sbuf_puts(out, "<![CDATA[");
            sbuf_put_ucs4(out, node->text, node->text_len);
            sbuf_puts(out, "]]>");
            break;
        }
        /* a CDATA section is a Text node, so its escaped text is the one HTML form that holds a ">" */
        TH_FALLTHROUGH;
    case TH_NODE_TEXT:
        if (opts->inner && !opts->xml && root->type == TH_NODE_ELEMENT && is_rawtext_element(root, tree->scripting) &&
            node->parent == root) {
            sbuf_put_ucs4(out, need_text(tree, node), node->text_len);
        } else if (opts->xml) {
            sbuf_put_xml_text(out, need_text(tree, node), node->text_len, 0, opts->well_formed);
        } else {
            sbuf_put_text(out, need_text(tree, node), node->text_len, 0, opts->formatter);
        }
        break;
    case TH_NODE_COMMENT:
        sbuf_puts(out, "<!--");
        if (opts->xml) {
            /* HTML parsers read XHTML output too, the sanitizer's inner_xml among them */
            sbuf_put_comment_start(out, node->text, node->text_len);
            sbuf_put_xml_comment(out, node->text, node->text_len, opts->well_formed);
        } else {
            sbuf_put_comment(out, node->text, node->text_len);
        }
        sbuf_puts(out, "-->");
        break;
    case TH_NODE_DOCTYPE:
        sbuf_puts(out, "<!DOCTYPE ");
        sbuf_put_ucs4(out, node->text, doctype_name_len(node));
        sbuf_putc(out, '>');
        break;
    case TH_NODE_PI:
        /* XML closes a PI with "?>"; the HTML serialization has no PI syntax and ends the bogus-comment form at ">". */
        if (opts->xml) {
            sbuf_puts(out, "<?");
            sbuf_put_xml_pi_data(out, node->text, node->text_len);
            sbuf_puts(out, "?>");
        } else {
            sbuf_put_html_pi(out, node->text, node->text_len);
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
    /* ascend toward the root, closing each element whose children are done */
    while (node != root) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (node->type == TH_NODE_ELEMENT && !(opts->inner && node == root)) {
            ser_close_tag(out, node);
        }
    }
    return NULL;
}

static void serialize_compact(sbuf *out, th_tree *tree, th_node *root, const th_serialize_opts *opts) {
    th_node *node = root;
    while (node != NULL) {
        node = serialize_compact_step(out, tree, node, root, opts);
    }
}

/* Indentation context for the pretty form: the output options plus the per-level
   unit the layout's Indent supplies. */
typedef struct {
    const th_serialize_opts *out;
    const Py_UCS4 *indent;
    Py_ssize_t indent_len;
} ser_opts;

static void ser_newline_indent(sbuf *out, const ser_opts *opts, int depth) {
    if (depth == 0 || opts->indent_len == 0) {
        sbuf_putc(out, '\n');
        return;
    }
    if (depth > (PY_SSIZE_T_MAX - out->len - 1) / opts->indent_len) { /* GCOVR_EXCL_BR_LINE: allocation size overflow */
        out->failed = 1;                                              /* GCOVR_EXCL_LINE: allocation size overflow */
        return;                                                       /* GCOVR_EXCL_LINE: allocation size overflow */
    }
    Py_ssize_t length = depth * opts->indent_len;
    sbuf_reserve(out, length + 1);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    out->data[out->len++] = '\n';
    Py_UCS4 *indent = out->data + out->len;
    memcpy(indent, opts->indent, (size_t)opts->indent_len * sizeof(Py_UCS4));
    /* Doubling the copied prefix avoids one capacity check and small copy per level. */
    for (Py_ssize_t written = opts->indent_len; written < length;) {
        Py_ssize_t chunk = written < length - written ? written : length - written;
        memcpy(indent + written, indent, (size_t)chunk * sizeof(Py_UCS4));
        written += chunk;
    }
    out->len += length;
}

/* The HTML elements the default CSS lays out as blocks (display block, list-item,
   table parts, or none). Whitespace at a block's edges and between block siblings
   renders nothing, so the pretty layout may add or drop it there and nowhere else. */
static int pretty_is_block_atom(uint16_t atom) {
    switch (atom) {
    case TH_TAG_ADDRESS:
    case TH_TAG_ARTICLE:
    case TH_TAG_ASIDE:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_BODY:
    case TH_TAG_CAPTION:
    case TH_TAG_CENTER:
    case TH_TAG_COL:
    case TH_TAG_COLGROUP:
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
    case TH_TAG_FRAME:
    case TH_TAG_FRAMESET:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_HEAD:
    case TH_TAG_HEADER:
    case TH_TAG_HGROUP:
    case TH_TAG_HR:
    case TH_TAG_HTML:
    case TH_TAG_LEGEND:
    case TH_TAG_LI:
    case TH_TAG_LISTING:
    case TH_TAG_MAIN:
    case TH_TAG_MENU:
    case TH_TAG_NAV:
    case TH_TAG_OL:
    case TH_TAG_P:
    case TH_TAG_PLAINTEXT:
    case TH_TAG_PRE:
    case TH_TAG_SEARCH:
    case TH_TAG_SECTION:
    case TH_TAG_SUMMARY:
    case TH_TAG_TABLE:
    case TH_TAG_TBODY:
    case TH_TAG_TD:
    case TH_TAG_TFOOT:
    case TH_TAG_TH:
    case TH_TAG_THEAD:
    case TH_TAG_TR:
    case TH_TAG_UL:
    case TH_TAG_XMP:
        return 1;
    default:
        return 0;
    }
}

/* How the pretty layout writes a node's children. */
enum {
    PRETTY_INLINE,   /* verbatim on the node's own line: its whitespace may render */
    PRETTY_RUNS,     /* a block container: block children on their own lines, the
                        inline content between them one line per run */
    PRETTY_ELEMENTS, /* element-only content with no rendering rules (XML, SVG,
                        MathML): one child per line */
};

/* An XML tree carries no HTML rendering, and in foreign content only the absence
   of text says whitespace is free to add (the libxml2 rule): any text child keeps
   the element verbatim, so reindenting its own output reproduces it. SVG <text>
   renders the whitespace between its tspans, so it stays verbatim even without. */
static int pretty_layout(th_tree *tree, th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return PRETTY_RUNS; /* the document and a content fragment are block containers */
    }
    if (th_tree_is_xml(tree) || node->ns != TH_NS_HTML) {
        if (node->ns == TH_NS_SVG && ser_value_iequals(node->text, node->text_len, "text")) {
            return PRETTY_INLINE;
        }
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_TEXT) {
                return PRETTY_INLINE;
            }
        }
        return PRETTY_ELEMENTS;
    }
    /* pre and listing are blocks whose whitespace renders */
    return pretty_is_block_atom(node->atom) && node->atom != TH_TAG_PRE && node->atom != TH_TAG_LISTING ? PRETTY_RUNS
                                                                                                        : PRETTY_INLINE;
}

/* Whether child gets a line of its own inside parent. Everything in the document
   node, <html> and <head> does, since none of it renders inline; so does every
   child of an element-only container, which holds no text. In a block container
   only a block-level child does. */
static int pretty_is_block(th_tree *tree, const th_node *parent, const th_node *child) {
    if (child->type == TH_NODE_TEXT) {
        return 0;
    }
    if (parent->type == TH_NODE_DOCUMENT ||
        (parent->type == TH_NODE_ELEMENT && (th_tree_is_xml(tree) || parent->ns != TH_NS_HTML ||
                                             parent->atom == TH_TAG_HTML || parent->atom == TH_TAG_HEAD))) {
        return 1;
    }
    return child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML && pretty_is_block_atom(child->atom);
}

static int pretty_is_blank(th_tree *tree, th_node *node) {
    if (node->type != TH_NODE_TEXT) {
        return 0;
    }
    const Py_UCS4 *text = need_text(tree, node);
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (!is_space(text[index])) {
            return 0;
        }
    }
    return 1;
}

/* The first node from start on that begins a line inside parent: a block child, or
   the first visible node of an inline run. Whitespace-only text between blocks is
   skipped, since the layout writes its own. */
static th_node *pretty_first_item(th_tree *tree, const th_node *parent, th_node *start) {
    while (start != NULL && !pretty_is_block(tree, parent, start) && pretty_is_blank(tree, start)) {
        start = start->next_sibling;
    }
    return start;
}

static int pretty_has_block(th_tree *tree, const th_node *parent) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (pretty_is_block(tree, parent, child)) {
            return 1;
        }
    }
    return 0;
}

/* Write one inline run -- start and the siblings up to the next block child --
   verbatim on one line, trimming the whitespace at its two ends: they touch a
   block boundary, where whitespace renders nothing, and the layout's own newline
   replaces it. Returns the run's last sibling, so the walk resumes after it. */
static th_node *pretty_emit_run(sbuf *out, th_tree *tree, th_node *start, const th_serialize_opts *opts) {
    th_node *last = start;
    th_node *visible = start;
    for (th_node *node = start->next_sibling; node != NULL && !pretty_is_block(tree, start->parent, node);
         node = node->next_sibling) {
        last = node;
        if (!pretty_is_blank(tree, node)) {
            visible = node;
        }
    }
    for (th_node *node = start;; node = node->next_sibling) {
        if (node->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(tree, node);
            Py_ssize_t begin = 0;
            Py_ssize_t end = node->text_len;
            while (node == start && is_space(text[begin])) {
                begin++;
            }
            while (node == visible && is_space(text[end - 1])) {
                end--;
            }
            if (opts->xml) {
                sbuf_put_xml_text(out, text + begin, end - begin, 0, opts->well_formed);
            } else {
                sbuf_put_text(out, text + begin, end - begin, 0, opts->formatter);
            }
        } else {
            serialize_compact(out, tree, node, opts);
        }
        if (node == visible) {
            return last;
        }
    }
}

/* Emit one node under the pretty layout and return the next node the walk rooted at
   root visits, or NULL once the subtree is done; *depth carries the current
   indentation level across the walk (and, so serialize_iter can suspend the walk,
   across chunks). A node is written at the current position with no leading
   whitespace; a parent emits the newline and indent before each child, so the root
   starts at column zero. The layout only adds whitespace where it renders nothing
   (pretty_layout), so the reparsed text reads the same and reindenting the output
   reproduces it. */
static th_node *serialize_pretty_step(sbuf *out, th_tree *tree, th_node *node, th_node *root, const ser_opts *opts,
                                      int *depth) {
    th_serialize_opts leaf_opts = *opts->out;
    leaf_opts.inner = 0;
    if (opts->out->inner && node == root) {
        if (!opts->out->xml && node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML) {
            if (is_serialize_void_atom(node->atom)) {
                return NULL;
            }
            if (opts->out->inject_meta && node->atom == TH_TAG_HEAD && !ser_head_has_charset_meta(tree, node)) {
                ser_emit_meta_charset(out, opts->out);
                if (pretty_first_item(tree, node, node->first_child) != NULL) {
                    ser_newline_indent(out, opts, *depth);
                }
            }
        }
        if (pretty_layout(tree, node) == PRETTY_INLINE) {
            for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
                serialize_compact(out, tree, child, &leaf_opts);
            }
            return NULL;
        }
        return pretty_first_item(tree, node, node->first_child);
    }
    if (node != root && !pretty_is_block(tree, node->parent, node)) {
        node = pretty_emit_run(out, tree, node, &leaf_opts);
    } else if (node->type == TH_NODE_DOCUMENT || node->type == TH_NODE_CONTENT) {
        return pretty_first_item(tree, node, node->first_child);
    } else if (node->type != TH_NODE_ELEMENT || (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) ||
               is_rawtext_element(node, tree->scripting) || pretty_layout(tree, node) == PRETTY_INLINE) {
        /* a leaf, a raw-text or whitespace-significant element, or inline content:
           written verbatim, and treated as a leaf by the walk */
        serialize_compact(out, tree, node, &leaf_opts);
    } else {
        /* meta_charset injects the declaration as head's first child, on its own
           indented line */
        int inject = !opts->out->xml && opts->out->inject_meta && node->ns == TH_NS_HTML && node->atom == TH_TAG_HEAD &&
                     !ser_head_has_charset_meta(tree, node);
        th_node *first = pretty_first_item(tree, node, node->first_child);
        ser_open_tag(out, tree, node, opts->out);
        if (first == NULL && !inject) {
            if (opts->out->xml) {
                sbuf_puts(out, "/>");
            } else {
                sbuf_putc(out, '>');
                ser_close_tag(out, node);
            }
        } else if (!inject && !pretty_has_block(tree, node)) {
            /* one inline run and no block child: the run stays on the tag's line */
            sbuf_putc(out, '>');
            pretty_emit_run(out, tree, first, &leaf_opts);
            ser_close_tag(out, node);
        } else {
            sbuf_putc(out, '>');
            if (inject) {
                ser_newline_indent(out, opts, *depth + 1);
                ser_emit_meta_charset(out, opts->out);
            }
            if (first != NULL) {
                ser_newline_indent(out, opts, *depth + 1);
                *depth += 1; /* an element indents its children one level deeper */
                return first;
            }
            ser_newline_indent(out, opts, *depth);
            ser_close_tag(out, node);
        }
    }
    /* ascend toward the root, closing each element once its children are done */
    while (node != root) {
        th_node *parent = node->parent;
        th_node *next = pretty_first_item(tree, parent, node->next_sibling);
        if (next != NULL) {
            ser_newline_indent(out, opts, *depth);
            return next;
        }
        if (parent->type == TH_NODE_ELEMENT && !(opts->out->inner && parent == root)) {
            *depth -= 1;
            ser_newline_indent(out, opts, *depth);
            ser_close_tag(out, parent);
        }
        node = parent;
    }
    return NULL;
}

static void serialize_pretty(sbuf *out, th_tree *tree, th_node *root, const ser_opts *opts, int depth0) {
    th_node *node = root;
    int depth = depth0;
    while (node != NULL) {
        node = serialize_pretty_step(out, tree, node, root, opts, &depth);
    }
}

th_node *th_tree_document(th_tree *tree) {
    return tree->fragment_root != NULL ? tree->fragment_root : tree->document;
}

Py_UCS4 *th_node_data(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    Py_ssize_t len = node->type == TH_NODE_DOCTYPE ? doctype_name_len(node) : node->text_len;
    *out_len = len;
    Py_UCS4 *out = PyMem_Malloc((len ? len : 1) * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (len) {
        memcpy(out, need_text(tree, node), (size_t)len * sizeof(Py_UCS4));
    }
    return out;
}

/* The doctype's public and system identifiers, sliced out of the stored
   "name \"public\" \"system\"" text. Returns 1 and sets all four out params when
   the doctype carries identifiers, 0 when it is just a name. Either identifier may
   be present but empty (a SYSTEM doctype has no public id, a PUBLIC doctype may
   omit the system id), and build_doctype_text always writes both quoted strings
   together, so the closing quotes are guaranteed and no bounds check is needed.
   node->attr_count records the public id's length so the split holds even when an
   identifier embeds a quote. */
int th_node_doctype_ids(th_node *node, const Py_UCS4 **public_id, Py_ssize_t *public_len, const Py_UCS4 **system_id,
                        Py_ssize_t *system_len) {
    Py_ssize_t name_len = doctype_name_len(node);
    if (name_len >= node->text_len) {
        return 0;
    }
    /* attr_count holds the public id's length, so the two identifiers split
       unambiguously even when either one embeds a quote (a scan for the closing
       `"` would stop early on `SYSTEM 'taco"quote'`). */
    Py_ssize_t pos = name_len + 2; /* skip the space and the opening quote */
    *public_id = &node->text[pos];
    *public_len = node->attr_count;
    pos += node->attr_count + 3; /* skip the closing quote, the separating space, and the next opening quote */
    *system_id = &node->text[pos];
    *system_len = node->text_len - pos - 1; /* drop the trailing closing quote */
    return 1;
}

static th_node *text_preorder_next(th_node *node, th_node *root) {
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node != root && node->next_sibling == NULL) {
        node = node->parent;
    }
    return node == root ? NULL : node->next_sibling;
}

static void collect_text(sbuf *out, th_tree *tree, th_node *root) {
    for (th_node *node = root->first_child; node != NULL; node = text_preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT) {
            sbuf_put_ucs4(out, need_text(tree, node), node->text_len);
        }
    }
}

Py_UCS4 *th_node_text(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    if (node->type == TH_NODE_TEXT) {
        sbuf_put_ucs4(&out, need_text(tree, node), node->text_len);
    } else {
        collect_text(&out, tree, node);
    }
    return sbuf_finish(&out, out_len);
}

PyObject *th_node_text_string(th_tree *tree, th_node *root) {
    sbuf snapshot = {NULL, 0, 0, 0};
    Py_UCS4 bits = 0;
    th_node *first = root->type == TH_NODE_TEXT ? root : root->first_child;
    for (th_node *node = first; node != NULL; node = text_preorder_next(node, root)) {
        if (node->type != TH_NODE_TEXT || node->text_len == 0) {
            continue;
        }
        const Py_UCS4 *text = need_text(tree, node);
        if (text == NULL ||                                   /* GCOVR_EXCL_BR_LINE: allocation failure or overflow */
            node->text_len > PY_SSIZE_T_MAX - snapshot.len) { /* GCOVR_EXCL_BR_LINE: allocation failure or overflow */
            PyMem_Free(snapshot.data);                        /* GCOVR_EXCL_LINE: allocation failure or overflow */
            return PyErr_NoMemory();                          /* GCOVR_EXCL_LINE: allocation failure or overflow */
        }
        sbuf_reserve(&snapshot, node->text_len);
        if (snapshot.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            PyMem_Free(snapshot.data); /* GCOVR_EXCL_LINE: allocation failure */
            return PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation failure */
        }
        Py_UCS4 *destination = snapshot.data + snapshot.len;
        for (Py_ssize_t index = 0; index < node->text_len; index++) {
            Py_UCS4 character = text[index];
            destination[index] = character;
            bits |= character;
        }
        snapshot.len += node->text_len;
    }
    Py_UCS4 maxchar = bits <= 0x7F ? 0x7F : bits <= 0xFF ? 0xFF : bits <= 0xFFFF ? 0xFFFF : 0x10FFFF;
    PyObject *result = PyUnicode_New(snapshot.len, th_str_maxchar(maxchar));
    if (result == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        PyMem_Free(snapshot.data); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;               /* GCOVR_EXCL_LINE: allocation failure */
    }
    int kind = PyUnicode_KIND(result);
    if (kind == PyUnicode_1BYTE_KIND) {
        Py_UCS1 *destination = PyUnicode_1BYTE_DATA(result);
        for (Py_ssize_t index = 0; index < snapshot.len; index++) {
            destination[index] = (Py_UCS1)snapshot.data[index];
        }
#ifndef PYPY_VERSION
    } else if (kind == PyUnicode_2BYTE_KIND) {
        Py_UCS2 *destination = PyUnicode_2BYTE_DATA(result);
        for (Py_ssize_t index = 0; index < snapshot.len; index++) {
            destination[index] = (Py_UCS2)snapshot.data[index];
        }
#endif
    } else {
        memcpy(PyUnicode_4BYTE_DATA(result), snapshot.data, (size_t)snapshot.len * sizeof(Py_UCS4));
    }
    PyMem_Free(snapshot.data);
    return result;
}

/* Copy every descendant Text node's code points of node into buf at pos, realizing
   zero-copy spans on the way; the caller sizes buf to the subtree's text length.
   The find(text=) C scan reuses one buffer across candidates so no per-node str is
   built; a Text or Content child of a content fragment is descended like an element. */
static void collect_text_into(th_tree *tree, th_node *root, Py_UCS4 *buf) {
    Py_ssize_t pos = 0;
    for (th_node *node = root->first_child; node != NULL; node = text_preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT) {
            memcpy(buf + pos, need_text(tree, node), (size_t)node->text_len * sizeof(Py_UCS4));
            pos += node->text_len;
        }
    }
}

void th_node_collect_text(th_tree *tree, th_node *node, Py_UCS4 *buf) {
    collect_text_into(tree, node, buf);
}

/* The WHATWG-conformant defaults the html/inner_html accessors serialize under:
   minimal escaping, source attribute order, no charset injection. */
static const th_serialize_opts ser_default_opts = {TH_FMT_WHATWG, 0, 0, NULL, 0, 0, 0, 0};

Py_UCS4 *th_node_html(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    sbuf_presize_for_root(&out, tree, node);
    serialize_compact(&out, tree, node, &ser_default_opts);
    return sbuf_finish(&out, out_len);
}

Py_UCS4 *th_node_inner_html(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        serialize_compact(&out, tree, child, &ser_default_opts);
    }
    return sbuf_finish(&out, out_len);
}

/* The inner_html defaults with XML/XHTML syntax turned on, plus the well-formed pass:
   empty elements self-close, values follow the XML escaping rules, foreign subtrees
   carry their namespace declarations, and comments plus character data plus attribute
   names are made well-formed. This is the sanitizer's serialization; Node.serialize's
   own Html(xml=True) stays on the raw XML path with well_formed off. */
static const th_serialize_opts ser_xml_opts = {TH_FMT_WHATWG, 0, 0, NULL, 0, 1, 1, 0};

Py_UCS4 *th_node_inner_xml(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        serialize_compact(&out, tree, child, &ser_xml_opts);
    }
    return sbuf_finish(&out, out_len);
}

static int inner_preserves_text(th_tree *tree, th_node *root, const th_serialize_opts *opts) {
    return opts->inner && !opts->xml && root->type == TH_NODE_ELEMENT && root->ns == TH_NS_HTML &&
           (is_rawtext_element(root, tree->scripting) || root->atom == TH_TAG_PRE || root->atom == TH_TAG_TEXTAREA ||
            root->atom == TH_TAG_LISTING);
}

Py_UCS4 *th_node_serialize(th_tree *tree, th_node *node, const th_serialize_opts *opts, const Py_UCS4 *indent,
                           Py_ssize_t indent_len, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    sbuf_presize_for_root(&out, tree, node);
    if (indent == NULL || inner_preserves_text(tree, node, opts)) {
        serialize_compact(&out, tree, node, opts);
    } else {
        ser_opts pretty = {opts, indent, indent_len};
        serialize_pretty(&out, tree, node, &pretty, 0);
    }
    return sbuf_finish(&out, out_len);
}

/* Emit whole nodes from cursor until the chunk holds at least this many code points;
   stopping only on a per-node boundary keeps each chunk near this size, bar the one
   case a single text node exceeds it (it still emits as one chunk). The output stays
   bounded to roughly one chunk, which is the point: no full-size string is built. */
#define TH_SERIALIZE_CHUNK 8192

Py_UCS4 *th_node_serialize_chunk(th_tree *tree, th_node *root, const th_serialize_opts *opts, const Py_UCS4 *indent,
                                 Py_ssize_t indent_len, th_ser_cursor *cursor, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    /* one chunk grows to about TH_SERIALIZE_CHUNK before it is handed off, so size the
       buffer to that up front rather than let the doubling reserve walk 256 -> 8192 on
       every chunk (a node straddling the limit still forces at most one more grow) */
    sbuf_reserve(&out, TH_SERIALIZE_CHUNK);
    if (indent == NULL || inner_preserves_text(tree, root, opts)) {
        while (cursor->node != NULL && out.len < TH_SERIALIZE_CHUNK) {
            cursor->node = serialize_compact_step(&out, tree, cursor->node, root, opts);
        }
    } else {
        ser_opts pretty = {opts, indent, indent_len};
        while (cursor->node != NULL && out.len < TH_SERIALIZE_CHUNK) {
            cursor->node = serialize_pretty_step(&out, tree, cursor->node, root, &pretty, &cursor->depth);
        }
    }
    return sbuf_finish(&out, out_len);
}
