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
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
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
    /* GCOVR_EXCL_START: a WHATWG-conformant parse never yields a PI (folded to a
       comment) or a CDATA section (folded to text), so the #document dumper, which
       only serves parsed trees, never reaches these. */
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
    case TH_NODE_DOCUMENT: /* GCOVR_EXCL_LINE: the document node is the serialization root, never a line itself */
        break;             /* GCOVR_EXCL_LINE: same -- the document node is never reached as a child */
    }
    sbuf_putc(out, '\n');
    /* attributes: each on its own deeper line, output in lexicographic name
       order (the html5lib #document format sorts them). Only elements have
       attributes; a text node's attr_count field holds a span offset. */
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
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
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
    case TH_NODE_TEXT:
        if (opts->xml) {
            sbuf_put_xml_text(out, need_text(tree, node), node->text_len, 0, opts->well_formed);
        } else {
            sbuf_put_text(out, need_text(tree, node), node->text_len, 0, opts->formatter);
        }
        break;
    case TH_NODE_COMMENT:
        sbuf_puts(out, "<!--");
        if (opts->well_formed) {
            sbuf_put_xml_comment(out, node->text, node->text_len);
        } else {
            sbuf_put_ucs4(out, node->text, node->text_len);
        }
        sbuf_puts(out, "-->");
        break;
    case TH_NODE_DOCTYPE:
        sbuf_puts(out, "<!DOCTYPE ");
        sbuf_put_ucs4(out, node->text, doctype_name_len(node));
        sbuf_putc(out, '>');
        break;
    case TH_NODE_PI:
        sbuf_puts(out, "<?");
        sbuf_put_ucs4(out, node->text, node->text_len);
        /* XML closes a PI with "?>"; the HTML serialization has no PI syntax and ends the
           bogus-comment form at ">". */
        sbuf_puts(out, opts->xml ? "?>" : ">");
        break;
    case TH_NODE_CDATA:
        sbuf_puts(out, "<![CDATA[");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, "]]>");
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
        if (node->type == TH_NODE_ELEMENT) {
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
    sbuf_putc(out, '\n');
    for (int level = 0; level < depth; level++) {
        sbuf_put_ucs4(out, opts->indent, opts->indent_len);
    }
}

/* Emit one node under the pretty layout and return the next node the walk rooted at
   root visits, or NULL once the subtree is done; *depth carries the current
   indentation level across the walk (and, so serialize_iter can suspend the walk,
   across chunks). A node is written at the current position with no leading
   whitespace; a parent emits the newline and indent before each child, so the root
   starts at column zero. Raw-text and whitespace-significant elements
   (script/style/pre/textarea/listing) keep their content verbatim, since reflowing
   it would change meaning. */
static th_node *serialize_pretty_step(sbuf *out, th_tree *tree, th_node *node, th_node *root, const ser_opts *opts,
                                      int *depth) {
    th_node *descend = NULL;
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_ELEMENT: {
        ser_open_tag(out, tree, node, opts->out);
        if (opts->out->xml) {
            /* XML pretty form: an empty element self-closes on its line, a parent
               opens then lays each child out one level deeper (the ascend closes it) */
            if (node->first_child == NULL) {
                sbuf_puts(out, "/>");
            } else {
                sbuf_putc(out, '>');
                ser_newline_indent(out, opts, *depth + 1);
                descend = node->first_child;
            }
            break;
        }
        sbuf_putc(out, '>');
        if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
            break;
        }
        int raw = is_rawtext_element(node, tree->scripting);
        int preserve = raw || ser_needs_leading_newline(tree, node) ||
                       (node->ns == TH_NS_HTML &&
                        (node->atom == TH_TAG_PRE || node->atom == TH_TAG_TEXTAREA || node->atom == TH_TAG_LISTING));
        if (preserve) {
            /* a whitespace-significant element keeps its content verbatim: its
               children serialize compactly (itself iterative), so the pretty
               walk treats it as a leaf and never recurses through it */
            if (ser_needs_leading_newline(tree, node)) {
                sbuf_putc(out, '\n');
            }
            for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
                if (raw && child->type == TH_NODE_TEXT) { /* GCOVR_EXCL_BR_LINE */
                    sbuf_put_ucs4(out, need_text(tree, child), child->text_len);
                } else {
                    serialize_compact(out, tree, child, opts->out);
                }
            }
            ser_close_tag(out, node);
            break;
        }
        /* meta_charset injects the declaration as head's first child, on its own
           indented line (head is never a preserve/raw element, so this is reached) */
        int inject = opts->out->inject_meta && node->ns == TH_NS_HTML && node->atom == TH_TAG_HEAD &&
                     !ser_head_has_charset_meta(tree, node);
        if (node->first_child == NULL && !inject) {
            ser_close_tag(out, node);
            break;
        }
        if (inject) {
            ser_newline_indent(out, opts, *depth + 1);
            ser_emit_meta_charset(out, opts->out);
        }
        if (node->first_child == NULL) {
            ser_newline_indent(out, opts, *depth);
            ser_close_tag(out, node);
            break;
        }
        ser_newline_indent(out, opts, *depth + 1);
        descend = node->first_child;
        break;
    }
    case TH_NODE_TEXT:
        if (opts->out->xml) {
            sbuf_put_xml_text(out, need_text(tree, node), node->text_len, 0, opts->out->well_formed);
        } else {
            sbuf_put_text(out, need_text(tree, node), node->text_len, 0, opts->out->formatter);
        }
        break;
    case TH_NODE_COMMENT:
        /* the pretty layout is a Node.serialize option, never the sanitizer's compact inner_xml,
           so well_formed is off here and a comment stays on the raw XML path */
        sbuf_puts(out, "<!--");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, "-->");
        break;
    case TH_NODE_DOCTYPE:
        sbuf_puts(out, "<!DOCTYPE ");
        sbuf_put_ucs4(out, node->text, doctype_name_len(node));
        sbuf_putc(out, '>');
        break;
    case TH_NODE_PI:
        sbuf_puts(out, "<?");
        sbuf_put_ucs4(out, node->text, node->text_len);
        /* XML closes a PI with "?>"; the HTML serialization has no PI syntax and ends the
           bogus-comment form at ">". */
        sbuf_puts(out, opts->out->xml ? "?>" : ">");
        break;
    case TH_NODE_CDATA:
        sbuf_puts(out, "<![CDATA[");
        sbuf_put_ucs4(out, node->text, node->text_len);
        sbuf_puts(out, "]]>");
        break;
    case TH_NODE_CONTENT:
    case TH_NODE_DOCUMENT:
        /* a transparent container lays its children out at its own depth */
        descend = node->first_child;
        break;
    }
    if (descend != NULL) {
        if (node->type == TH_NODE_ELEMENT) {
            *depth += 1; /* an element indents its children one level deeper */
        }
        return descend;
    }
    /* ascend toward the root, closing each element once its children are done */
    while (node != root) {
        if (node->next_sibling != NULL) {
            ser_newline_indent(out, opts, *depth);
            return node->next_sibling;
        }
        th_node *parent = node->parent;
        if (parent->type == TH_NODE_ELEMENT) {
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

static void collect_text(sbuf *out, th_tree *tree, th_node *node, int depth) {
    if (depth >= TH_MAX_WALK_DEPTH) {
        /* Backstop against a tree built past the parser's depth cap through the
           mutation API: stop collecting rather than overflow the C stack. A parsed
           tree never reaches this depth. */
        return;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            sbuf_put_ucs4(out, need_text(tree, child), child->text_len);
        } else if (child->type == TH_NODE_ELEMENT || child->type == TH_NODE_CONTENT) {
            collect_text(out, tree, child, depth + 1);
        }
    }
}

Py_UCS4 *th_node_text(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    if (node->type == TH_NODE_TEXT) {
        sbuf_put_ucs4(&out, need_text(tree, node), node->text_len);
    } else {
        collect_text(&out, tree, node, 0);
    }
    return sbuf_finish(&out, out_len);
}

/* Copy every descendant Text node's code points of node into buf at pos, realizing
   zero-copy spans on the way; the caller sizes buf to the subtree's text length.
   The find(text=) C scan reuses one buffer across candidates so no per-node str is
   built; a Text or Content child of a content fragment is descended like an element. */
static Py_ssize_t collect_text_into(th_tree *tree, th_node *node, Py_UCS4 *buf, Py_ssize_t pos) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            memcpy(buf + pos, need_text(tree, child), (size_t)child->text_len * sizeof(Py_UCS4));
            pos += child->text_len;
        } else if (child->type == TH_NODE_ELEMENT || child->type == TH_NODE_CONTENT) {
            pos = collect_text_into(tree, child, buf, pos);
        }
    }
    return pos;
}

void th_node_collect_text(th_tree *tree, th_node *node, Py_UCS4 *buf) {
    collect_text_into(tree, node, buf, 0);
}

/* The WHATWG-conformant defaults the html/inner_html accessors serialize under:
   minimal escaping, source attribute order, no charset injection. */
static const th_serialize_opts ser_default_opts = {TH_FMT_WHATWG, 0, 0, NULL, 0, 0, 0};

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
static const th_serialize_opts ser_xml_opts = {TH_FMT_WHATWG, 0, 0, NULL, 0, 1, 1};

Py_UCS4 *th_node_inner_xml(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        serialize_compact(&out, tree, child, &ser_xml_opts);
    }
    return sbuf_finish(&out, out_len);
}

Py_UCS4 *th_node_serialize(th_tree *tree, th_node *node, const th_serialize_opts *opts, const Py_UCS4 *indent,
                           Py_ssize_t indent_len, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0};
    sbuf_presize_for_root(&out, tree, node);
    if (indent == NULL) {
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
    if (indent == NULL) {
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
