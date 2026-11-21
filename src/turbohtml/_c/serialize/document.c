/* Renders a parsed tree back to HTML -- the round-trippable document/fragment
   markup, the html5lib "#document" debug dump, and the indented pretty form,
   under the WHATWG, minimal, or named-entity escape policy the caller picks. */

#include "serialize/internal.h"

#include "core/work_steps.h"
#include "dom/tree.h"
#include "dom/tree_internal.h"
#include "tokenizer/xml_names.h"

#include <string.h>

static int xml_wide_name_wellformed(const Py_UCS4 *name, Py_ssize_t len) {
    if (len == 0 || !is_name_start(name[0])) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < len; index++) {
        if (!is_name_char(name[index])) {
            return 0;
        }
    }
    return 1;
}

static int xml_element_name_wellformed(const Py_UCS4 *name, Py_ssize_t len) {
    if (!xml_wide_name_wellformed(name, len)) {
        return 0;
    }
    int colon = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (name[index] == ':') {
            if (index == 0 || index == len - 1 || colon) {
                return 0;
            }
            colon = 1;
        }
    }
    return 1;
}

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
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    /* a fragment serializes the context root's children; a document serializes
       the document node's children */
    th_node *top = tree->fragment_root != NULL ? tree->fragment_root : tree->document;
    for (th_node *child = top->first_child; child != NULL; child = child->next_sibling) {
        serialize_node(&out, tree, child, 0);
    }
    th_clone_seen_free(out.clone_seen);
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

/* The raw-text helpers stay out of line: a page holds few raw-text elements, and keeping their loops out of
   serialize_compact_step leaves the common walk's code, and the instructions it runs per node, as they were. */
#if defined(_MSC_VER)
#define SER_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define SER_NOINLINE __attribute__((noinline))
#else
#define SER_NOINLINE
#endif

int th_xml_element_name_wellformed(const Py_UCS4 *name, Py_ssize_t len) {
    return xml_element_name_wellformed(name, len);
}

SER_NOINLINE int th_xml_attr_name_wellformed_utf8(const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = 0;
    int colon = 0;
    while (index < name_len) {
        int first = index == 0;
        unsigned char lead = (unsigned char)name[index];
        Py_UCS4 character;
        if (lead < 0x80) {
            character = lead;
            index++;
        } else if (lead < 0xE0) {
            character = (Py_UCS4)(lead & 0x1F) << 6 | ((unsigned char)name[index + 1] & 0x3F);
            index += 2;
        } else if (lead < 0xF0) {
            character = (Py_UCS4)(lead & 0x0F) << 12 | ((unsigned char)(name[index + 1] & 0x3F)) << 6 |
                        ((unsigned char)name[index + 2] & 0x3F);
            index += 3;
        } else {
            character = (Py_UCS4)(lead & 0x07) << 18 | ((unsigned char)(name[index + 1] & 0x3F)) << 12 |
                        ((unsigned char)(name[index + 2] & 0x3F)) << 6 | ((unsigned char)name[index + 3] & 0x3F);
            index += 4;
        }
        if (!(first ? is_name_start(character) : is_name_char(character))) {
            return 0;
        }
        if (character == ':') {
            if (index == name_len || colon) {
                return 0;
            }
            colon = 1;
        }
    }
    return 1;
}

void th_xml_unwrap_free(th_xml_unwrap *unwrap) {
    PyMem_Free(unwrap->frames);
    PyMem_Free(unwrap->decls);
}

static Py_ssize_t th_clone_seen_slot(const uint64_t *slots, Py_ssize_t cap, uint64_t key) {
    Py_ssize_t index = (Py_ssize_t)(th_key_mix(key) & (uint64_t)(cap - 1));
    while (slots[index] != 0 && slots[index] != key) {
        index = (index + 1) & (cap - 1);
    }
    return index;
}

int th_clone_seen_add(struct th_clone_seen **seen_slot, uint32_t values_seq) {
    if (*seen_slot == NULL) {
        *seen_slot = PyMem_Calloc(1, sizeof(struct th_clone_seen));
        if (*seen_slot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    struct th_clone_seen *seen = *seen_slot;
    if ((seen->len + 1) * 2 > seen->cap) {
        Py_ssize_t cap = seen->cap == 0 ? 8 : seen->cap * 2;
        uint64_t *slots = PyMem_Calloc((size_t)cap, sizeof(uint64_t));
        if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < seen->cap; index++) {
            if (seen->slots[index] != 0) {
                slots[th_clone_seen_slot(slots, cap, seen->slots[index])] = seen->slots[index];
            }
        }
        PyMem_Free(seen->slots);
        seen->slots = slots;
        seen->cap = cap;
    }
    uint64_t key = (uint64_t)values_seq + 1;
    Py_ssize_t index = th_clone_seen_slot(seen->slots, seen->cap, key);
    if (seen->slots[index] == key) {
        return 0;
    }
    seen->slots[index] = key;
    seen->len++;
    return 1;
}

/* Whether a clone whose attributes cost cost characters writes all of them, charged as ser_clone_settle charges them.
   The first clone an output meets for a value set writes them free: the tree holds that set once, so the cost stays
   linear in the tree's own memory. A later clone of the same set fits while the allowance and the output fund it, less
   the clone attributes already written, which count once as output and once as spent. */

void th_clone_seen_free(struct th_clone_seen *seen) {
    if (seen != NULL) {
        PyMem_Free(seen->slots);
        PyMem_Free(seen);
    }
}

/* Enter element, whose tag the XML output leaves out, before its children are written in its place. Returns -1 on
   allocation failure. */
static int ser_xml_unwrap_push(th_tree *tree, th_xml_unwrap *unwrap, const th_node *element) {
    const th_xml_unwrap_frame *outer = ser_xml_unwrapped(unwrap, element->parent);
    th_xml_unwrap_frame frame = {element, outer != NULL ? outer->anchor : element->parent,
                                 outer != NULL ? outer->chain : unwrap->decls_len, unwrap->decls_len};
    th_xml_unwrap_frame *frames = ser_reserve(unwrap->frames, unwrap->len, &unwrap->cap, sizeof(*frames));
    if (frames == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    unwrap->frames = frames;
    for (Py_ssize_t position = 0; position < element->attr_count; position++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, element->attrs[position].name_atom, &name_len);
        if (!ser_xml_ns_name(name, name_len)) {
            continue;
        }
        const th_node_attr **decls = ser_reserve(unwrap->decls, unwrap->decls_len, &unwrap->decls_cap, sizeof(*decls));
        if (decls == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        unwrap->decls = decls;
        unwrap->decls[unwrap->decls_len++] = &element->attrs[position];
    }
    unwrap->frames[unwrap->len++] = frame;
    return 0;
}

static void ser_xml_unwrap_pop(th_xml_unwrap *unwrap) {
    unwrap->decls_len = unwrap->frames[--unwrap->len].own;
}

/* Whether the walk is inside element, a left-out element whose children are done, so it leaves it in place of an end
   tag. Only XML output leaves elements out. */
static int ser_xml_unwrap_leave(const th_serialize_opts *opts, const th_node *element) {
    if (!opts->xml || ser_xml_unwrapped(opts->xml_unwrap, element) == NULL) {
        return 0;
    }
    ser_xml_unwrap_pop(opts->xml_unwrap);
    return 1;
}

typedef struct {
    const char *name;
    Py_ssize_t name_len;
    const th_node_attr *attr;
    Py_ssize_t position; /* in the left-out run, outermost first */
} ser_xml_run_decl;

/* By name, the innermost of a name first: it is the one in effect where the children stand. */
static int ser_xml_run_decl_cmp(const void *left, const void *right) {
    const ser_xml_run_decl *first = left;
    const ser_xml_run_decl *second = right;
    Py_ssize_t common = first->name_len < second->name_len ? first->name_len : second->name_len;
    int order = memcmp(first->name, second->name, (size_t)common);
    if (order == 0) {
        order = (first->name_len > second->name_len) - (first->name_len < second->name_len);
    }
    return order != 0 ? order : (first->position < second->position) - (first->position > second->position);
}

/* The first of decls[0..count) named name, or count when none is. */
static Py_ssize_t ser_xml_run_decl_find(const ser_xml_run_decl *decls, Py_ssize_t count, const char *name,
                                        Py_ssize_t name_len) {
    ser_xml_run_decl key = {name, name_len, NULL, PY_SSIZE_T_MAX};
    Py_ssize_t low = 0;
    Py_ssize_t high = count;
    while (low < high) {
        Py_ssize_t middle = low + (high - low) / 2;
        if (ser_xml_run_decl_cmp(&decls[middle], &key) < 0) {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    return low < count && decls[low].name_len == name_len && memcmp(decls[low].name, name, (size_t)name_len) == 0
               ? low
               : count;
}

#define SER_XML_RUN_DECLS 16

/* Write onto node, a child of a left-out run, the namespace declarations the run carries from unwrap->decls[chain]
   on: the innermost of each name, unless node declares that name itself or the start tag already binds it. The copies
   are charged to unwrap->spend, since one left-out element above many children would multiply them; past the
   allowance the serialization fails with ValueError rather than write names whose prefixes nothing binds. */
SER_NOINLINE void ser_xml_unwrapped_ns_decls(sbuf *out, th_tree *tree, const th_node *node, xml_ns_emitted emitted,
                                             int injected_xlink, th_xml_unwrap *unwrap, Py_ssize_t chain) {
    if (out->failed) {
        return;
    }
    Py_ssize_t count = unwrap->decls_len - chain;
    Py_ssize_t cost = 0;
    for (Py_ssize_t index = chain; index < unwrap->decls_len; index++) {
        Py_ssize_t name_len;
        th_attr_name(tree, unwrap->decls[index]->name_atom, &name_len);
        cost += name_len + unwrap->decls[index]->value_len + 4;
    }
    th_xml_ns_spend *spend = &unwrap->spend;
    if (!xml_ns_copy_funded(spend->copied, spend->flushed + out->len, cost, spend->base)) {
        PyErr_Format(PyExc_ValueError,
                     "the namespace declarations on elements left out for their names exceed the allowance of %zd "
                     "characters plus %d per character of other output",
                     spend->base, TH_XML_NS_PER_CHAR);
        out->failed = 1;
        return;
    }
    spend->copied += cost;
    ser_xml_run_decl stack_decls[SER_XML_RUN_DECLS];
    ser_xml_run_decl *decls =
        count <= SER_XML_RUN_DECLS ? stack_decls : PyMem_Malloc((size_t)count * sizeof(ser_xml_run_decl));
    if (decls == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t kept = 0;
    for (Py_ssize_t index = chain; index < unwrap->decls_len; index++) {
        const th_node_attr *attr = unwrap->decls[index];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        if ((name_len == 5 && emitted.default_ns) ||
            (injected_xlink && name_len == 11 && memcmp(name, "xmlns:xlink", 11) == 0) ||
            !ser_xml_ns_binding_valid(name, name_len, attr->value, attr->value_len)) {
            continue;
        }
        decls[kept++] = (ser_xml_run_decl){name, name_len, attr, index};
    }
    qsort(decls, (size_t)kept, sizeof(ser_xml_run_decl), ser_xml_run_decl_cmp);
    for (Py_ssize_t position = 0; position < node->attr_count; position++) {
        const th_node_attr *attr = &node->attrs[position];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        if (!ser_xml_ns_name(name, name_len) || !xml_attr_name_writable(name, name_len, emitted) ||
            !ser_xml_ns_binding_valid(name, name_len, attr->value, attr->value_len)) {
            continue;
        }
        Py_ssize_t found = ser_xml_run_decl_find(decls, kept, name, name_len);
        if (found < kept) {
            decls[found].attr = NULL; /* node's own declaration is written with its attributes */
        }
    }
    for (Py_ssize_t index = 0; index < kept; index++) {
        if (decls[index].attr == NULL ||
            (index > 0 && decls[index].name_len == decls[index - 1].name_len &&
             memcmp(decls[index].name, decls[index - 1].name, (size_t)decls[index].name_len) == 0)) {
            continue;
        }
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, decls[index].name, decls[index].name_len);
        sbuf_puts(out, "=\"");
        sbuf_put_xml_text(out, decls[index].attr->value, decls[index].attr->value_len, 1);
        sbuf_putc(out, '"');
    }
    if (decls != stack_decls) {
        PyMem_Free(decls);
    }
}

/* A raw-text element's text children go out literally; the first markup child hands the rest to the markup walk. */
SER_NOINLINE static void ser_put_rawtext(sbuf *out, th_tree *tree, th_node *element, const th_serialize_opts *opts) {
    for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_TEXT && child->type != TH_NODE_CDATA) {
            ser_put_rawtext_markup(out, tree, element, child, opts, 0);
            return;
        }
        sbuf_put_rawtext_node(out, tree, child);
    }
}

/* Whether text[at..len) spells "script", in any case, and then a character that ends a tag name. A CR is one: a
   re-parse reads the LF it normalizes to. */
static int ser_script_name_at(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t at) {
    if (len - at <= 6 || !starts_with_ascii_ci(text + at, 6, "script")) {
        return 0;
    }
    Py_UCS4 after = text[at + 6];
    return is_space(after) || after == '/' || after == '>';
}

/* Whether a re-parse of out[content..] as script content ends in the script data double escaped state. */
static int ser_script_double_escaped(const sbuf *out, Py_ssize_t content) {
    enum { SCRIPT_DATA, SCRIPT_ESCAPED, SCRIPT_DOUBLE_ESCAPED } state = SCRIPT_DATA;
    int dashes = 0; /* the "-" run just read, counted up to the two a ">" needs to end the escape */
    for (Py_ssize_t index = content; index < out->len; index++) {
        if (state == SCRIPT_DATA) {
            /* nearly all script text stays in this state: jump from one "<" to the next */
            index = sbuf_find_special(out->data, index, out->len, 0, 0);
            if (index == out->len) {
                break;
            }
            if (out->data[index] == '<' && starts_with_ascii_ci(out->data + index, out->len - index, "<!--")) {
                state = SCRIPT_ESCAPED;
                dashes = 2; /* so "<!-->" and "<!--->" end the escape they start */
                index += 3;
            }
            continue;
        }
        Py_UCS4 character = out->data[index];
        if (character == '-') {
            dashes += dashes < 2;
        } else if (character == '>' && dashes == 2) {
            state = SCRIPT_DATA;
        } else {
            dashes = 0;
            if (character == '<') {
                int closing = index + 1 < out->len && out->data[index + 1] == '/';
                if (ser_script_name_at(out->data, out->len, index + 1 + closing)) {
                    /* an end tag met while only escaped is the element's own: what follows it is not script text */
                    state = closing ? SCRIPT_ESCAPED : SCRIPT_DOUBLE_ESCAPED;
                    index += 6 + closing;
                }
            }
        }
    }
    return state == SCRIPT_DOUBLE_ESCAPED;
}

SER_NOINLINE void ser_script_end_scan(sbuf *out, th_node *element, Py_ssize_t content, th_node *alone) {
    if (ser_script_double_escaped(out, content)) {
        ser_close_tag(out, element);
    } else if (alone != NULL) {
        /* a later serialization writes the same text: skip the scan until a write to the text clears this */
        alone->script_text_closes = 1;
    }
}

/* HTML doctypes can contain characters XML forbids, so discard them in XML output. */
static void ser_put_doctype_id(sbuf *out, const Py_UCS4 *id, Py_ssize_t len) {
    Py_UCS4 quote = '"';
    int has_invalid = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (id[index] == '"') {
            quote = '\'';
        }
        has_invalid |= xml_char_invalid(id[index]);
    }
    sbuf_putc(out, quote);
    if (has_invalid) {
        Py_ssize_t start = 0;
        for (Py_ssize_t index = 0; index < len; index++) {
            if (xml_char_invalid(id[index])) {
                sbuf_put_ucs4(out, id + start, index - start);
                start = index + 1;
            }
        }
        sbuf_put_ucs4(out, id + start, len - start);
    } else {
        sbuf_put_ucs4(out, id, len);
    }
    sbuf_putc(out, quote);
}

static int xml_pubid_valid(const Py_UCS4 *id, Py_ssize_t len) {
    static const char pubid[] =
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \r\n-'()+,./:=?;!*#@$_%";
    for (Py_ssize_t index = 0; index < len; index++) {
        if (id[index] >= 128 || memchr(pubid, (int)id[index], sizeof(pubid) - 1) == NULL) {
            return 0;
        }
    }
    return 1;
}

/* XML serialization of a DocumentType node (DOM-Parsing), with its public and system
   identifiers. A name that is not a well-formed XML Name (empty, or carrying a character
   XML forbids) has no XML form, so the whole doctype is dropped rather than written as tag
   soup that parse_xml would reject (#1031). */
static void ser_put_xml_doctype(sbuf *out, th_node *node) {
    Py_ssize_t name_len = doctype_name_len(node);
    if (!xml_wide_name_wellformed(node->text, name_len)) {
        return;
    }
    sbuf_puts(out, "<!DOCTYPE ");
    sbuf_put_ucs4(out, node->text, name_len);
    const Py_UCS4 *public_id;
    const Py_UCS4 *system_id;
    Py_ssize_t public_len;
    Py_ssize_t system_len;
    if (th_node_doctype_ids(node, &public_id, &public_len, &system_id, &system_len)) {
        if (public_len > 0 && xml_pubid_valid(public_id, public_len)) {
            /* XML requires a system literal after a public one, so both are always written */
            sbuf_puts(out, " PUBLIC ");
            sbuf_putc(out, '"');
            sbuf_put_ucs4(out, public_id, public_len);
            sbuf_putc(out, '"');
            sbuf_putc(out, ' ');
            ser_put_doctype_id(out, system_id, system_len);
        } else if (public_len == 0 || (node->tag_flags & TH_DOCTYPE_HAS_SYSTEM)) {
            sbuf_puts(out, " SYSTEM ");
            ser_put_doctype_id(out, system_id, system_len);
        }
    }
    sbuf_putc(out, '>');
}

static int xml_has_visible_child(th_tree *tree, th_node *parent) {
    th_node *node = parent->first_child;
    while (node != NULL) {
        int transparent =
            node->type == TH_NODE_CONTENT || (node->type == TH_NODE_ELEMENT && node->atom == TH_TAG_UNKNOWN &&
                                              !xml_element_name_wellformed(node->text, node->text_len));
        int dropped = node->type == TH_NODE_PI && (node->tag_flags & TH_PI_XML_INVALID);
        if (node->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(tree, node);
            if (text == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text allocation failure */
                return -1;                            /* GCOVR_EXCL_LINE: lazy text allocation failure */
            }
            dropped = 1;
            for (Py_ssize_t index = 0; index < node->text_len; index++) {
                if (!xml_char_invalid(text[index])) {
                    dropped = 0;
                    break;
                }
            }
        }
        if (!transparent && !dropped) {
            return 1;
        }
        if (transparent && node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node->next_sibling == NULL) {
            node = node->parent;
            if (node == parent) {
                return 0;
            }
        }
        node = node->next_sibling;
    }
    return 0;
}

static int table_cell_needs_wrap(th_node *node, th_node *root) {
    if (node->ns != TH_NS_HTML || (node->atom != TH_TAG_TD && node->atom != TH_TAG_TH)) {
        return 0;
    }
    for (th_node *parent = node->parent; parent != root; parent = parent->parent) {
        if (parent->atom == TH_TAG_TABLE) {
            return 0;
        }
    }
    return 1;
}

/* Markdown's raw HTML is copied out of a table cell with each run of spaces and line feeds collapsed to one space,
   which is harmless for flowing text but rewrites the text of a pre, textarea or listing, where whitespace draws as
   written */
static int ser_markdown_keeps_whitespace(const th_serialize_opts *opts, th_node *node, th_node *root) {
    if (opts->markdown != TH_MARKDOWN_HTML_INLINE && opts->markdown != TH_MARKDOWN_HTML_CELL) {
        return 0;
    }
    for (th_node *parent = node->parent; parent != NULL; parent = parent == root ? NULL : parent->parent) {
        if (parent->ns == TH_NS_HTML &&
            (parent->atom == TH_TAG_PRE || parent->atom == TH_TAG_TEXTAREA || parent->atom == TH_TAG_LISTING)) {
            return 1;
        }
    }
    return 0;
}

/* Preformatted text with each line feed and each space in a run written as a reference, which the collapse leaves be */
static void ser_put_markdown_preformatted(sbuf *out, const Py_UCS4 *text, Py_ssize_t len, int formatter) {
    for (Py_ssize_t index = 0; index < len; index++) {
        int space_run = text[index] == ' ' &&
                        ((index > 0 && text[index - 1] == ' ') || (index + 1 < len && text[index + 1] == ' '));
        if (space_run || text[index] == '\n') {
            sbuf_puts(out, space_run ? "&#32;" : "&#10;");
        } else {
            sbuf_put_text(out, text + index, 1, 0, formatter);
        }
    }
}

/* Emit one node under the compact (WHATWG fragment) layout and return the next node
   the walk rooted at root visits, or NULL once the subtree is fully written. Split
   out of serialize_compact so serialize_iter can resume the walk one node at a time
   between chunks: the one-shot wrapper below loops it to exhaustion and the streaming
   driver stops it at a chunk boundary, so both paths emit byte-identical markup. The
   walk is iterative -- descending through first_child, ascending through parent
   pointers -- so a tree of any depth serializes without one C stack frame per level. */
static th_node *serialize_compact_step(sbuf *out, th_tree *tree, th_node *node, th_node *root,
                                       const th_serialize_opts *opts, const th_cell_wrap *cell_wrap) {
    th_node *descend = NULL;
    if (opts->inner && node == root) {
        if (!opts->xml && node->type == TH_NODE_ELEMENT) {
            if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
                return NULL;
            }
            ser_inject_head_meta(out, tree, node, opts);
            if (is_rawtext_element(node, tree->scripting)) {
                ser_put_rawtext(out, tree, node, opts);
                return NULL;
            }
        }
        return node->first_child;
    }
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_ELEMENT:
        if (node->atom == TH_TAG_UNKNOWN && opts->xml && !xml_element_name_wellformed(node->text, node->text_len)) {
            /* XML has no spelling for the name, so the children take the element's place */
            if (node->first_child == NULL) {
                break;
            }
            if (ser_xml_unwrap_push(tree, opts->xml_unwrap, node) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
                out->failed = 1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            descend = node->first_child;
            break;
        }
        ser_open_tag(out, tree, node, opts);
        if (opts->xml) {
            /* XML syntax: every empty element self-closes, no void/raw-text special
               casing, so a childless element ends the tag and a parent descends. A
               template's content fragment is transparent, so an empty one self-closes and
               round-trips as <template/> the way parse_xml reads it back (#1031). */
            int visible = xml_has_visible_child(tree, node);
            if (visible < 0) {   /* GCOVR_EXCL_BR_LINE: lazy text allocation failure */
                out->failed = 1; /* GCOVR_EXCL_LINE */
                return NULL;     /* GCOVR_EXCL_LINE */
            }
            if (!visible) {
                sbuf_puts(out, "/>");
            } else {
                sbuf_putc(out, '>');
                descend = node->first_child;
            }
            break;
        }
        sbuf_putc(out, '>');
        ser_inject_head_meta(out, tree, node, opts);
        int wrapped_cell = cell_wrap != NULL && table_cell_needs_wrap(node, root);
        if (wrapped_cell) {
            sbuf_put_run(out, cell_wrap->open, cell_wrap->open_len);
        }
        if (node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
            break; /* void elements have no children or end tag */
        }
        if (ser_needs_leading_newline(tree, node)) {
            if (ser_markdown_keeps_whitespace(opts, node->first_child, root)) {
                sbuf_puts(out, "&#10;");
            } else {
                sbuf_putc(out, '\n');
            }
        }
        if (is_rawtext_element(node, tree->scripting)) {
            if (opts->markdown != TH_MARKDOWN_HTML_INLINE) {
                Py_ssize_t content = out->len;
                ser_put_rawtext(out, tree, node, opts);
                /* nested in another raw-text element this end tag is text, and the outer element's end tag decides */
                if (!opts->in_rawtext) {
                    ser_end_script_double_escape(out, node, content, 1);
                }
            }
            ser_close_tag(out, node);
            break;
        }
        if (node->first_child != NULL) {
            descend = node->first_child;
        } else {
            if (wrapped_cell) {
                sbuf_put_run(out, cell_wrap->close, cell_wrap->close_len);
            }
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
        if (need_text(tree, node) == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            out->failed = 1;                                       /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;                                           /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (opts->clone_budget != NULL) {
            *opts->clone_budget += node->text_len; /* raw HTML text funds clone attributes as Markdown prose does */
        }
        if (opts->xml) {
            sbuf_put_xml_text(out, node->text, node->text_len, 0);
        } else if (ser_markdown_keeps_whitespace(opts, node, root)) {
            ser_put_markdown_preformatted(out, node->text, node->text_len, opts->formatter);
        } else {
            sbuf_put_text(out, node->text, node->text_len, 0, opts->formatter);
        }
        break;
    case TH_NODE_COMMENT:
        /* CommonMark's inline comment rule does not match data ending in "-", so the comment would show as text */
        if (opts->markdown == TH_MARKDOWN_HTML_INLINE || opts->markdown == TH_MARKDOWN_HTML_CELL) {
            break;
        }
        sbuf_puts(out, "<!--");
        if (opts->xml) {
            /* HTML parsers read XHTML output too, the sanitizer's inner_xml among them */
            sbuf_put_comment_start(out, node->text, node->text_len);
            sbuf_put_xml_comment(out, node->text, node->text_len);
        } else {
            sbuf_put_comment(out, node->text, node->text_len);
        }
        sbuf_puts(out, "-->");
        break;
    case TH_NODE_DOCTYPE:
        if (opts->xml) {
            ser_put_xml_doctype(out, node);
            break;
        }
        sbuf_puts(out, "<!DOCTYPE ");
        sbuf_put_ucs4(out, node->text, doctype_name_len(node));
        sbuf_putc(out, '>');
        break;
    case TH_NODE_PI:
        if (opts->markdown == TH_MARKDOWN_HTML_INLINE) {
            break;
        }
        /* XML closes a PI with "?>"; the HTML serialization has no PI syntax and ends the bogus-comment form at ">". */
        if (opts->xml) {
            if (node->tag_flags & TH_PI_XML_INVALID) { /* GCOVR_EXCL_BR_LINE: invalid PIs are filtered above */
                break;                                 /* GCOVR_EXCL_LINE */
            }
            sbuf_puts(out, "<?");
            sbuf_put_ucs4(out, node->text, node->attr_count + 1);
            Py_ssize_t data_start = node->attr_count + 1;
            while (data_start < node->text_len) {
                Py_UCS4 ch = node->text[data_start];
                if (!xml_char_invalid(ch) && ch != ' ' && ch != '\t' && ch != '\n' && ch != '\r') {
                    break;
                }
                data_start++;
            }
            sbuf_put_xml_pi_data(out, node->text + data_start, node->text_len - data_start);
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
            if (ser_xml_unwrap_leave(opts, node)) {
                continue;
            }
            if (cell_wrap != NULL && table_cell_needs_wrap(node, root)) {
                sbuf_put_run(out, cell_wrap->close, cell_wrap->close_len);
            }
            ser_close_tag(out, node);
        }
    }
    return NULL;
}

static void serialize_compact(sbuf *out, th_tree *tree, th_node *root, const th_serialize_opts *opts) {
    th_node *node = root;
    while (node != NULL) {
        node = serialize_compact_step(out, tree, node, root, opts, NULL);
    }
}

/* The next node of a walk inside element after node is done, closing each element left behind but not element
   itself: the ascent serialize_compact_step makes under inner. */
static th_node *ser_markup_next(sbuf *out, th_node *node, const th_node *element) {
    while (node != element) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
        if (node != element) {
            ser_close_tag(out, node);
        }
    }
    return NULL;
}

/* Walks the content with the common step and takes over the nodes it would get wrong: a text child of a raw-text
   element, written literally, and a raw-text element holding markup, opened and descended into here so the step never
   calls back and the recursion stops at one level however deep they nest. */
SER_NOINLINE void ser_put_rawtext_markup(sbuf *out, th_tree *tree, th_node *element, th_node *from,
                                         const th_serialize_opts *opts, int strip_comments) {
    th_serialize_opts content = *opts;
    content.inner = 1;
    content.in_rawtext = 1;
    th_node *node = from;
    while (node != NULL) {
        if ((node->type == TH_NODE_TEXT || node->type == TH_NODE_CDATA) &&
            is_rawtext_element(node->parent, tree->scripting)) {
            sbuf_put_rawtext_node(out, tree, node);
            node = ser_markup_next(out, node, element);
        } else if (node->type == TH_NODE_COMMENT && strip_comments) {
            node = ser_markup_next(out, node, element);
        } else if (node->type == TH_NODE_ELEMENT && is_rawtext_element(node, tree->scripting) &&
                   rawtext_markup_child(node) != NULL) {
            ser_open_tag(out, tree, node, &content);
            sbuf_putc(out, '>');
            node = node->first_child;
        } else {
            node = serialize_compact_step(out, tree, node, element, &content, NULL);
        }
    }
}

/* Indentation context for the pretty form: the output options plus the per-level
   unit the layout's Indent supplies. */
typedef struct {
    const th_serialize_opts *out;
    const Py_UCS4 *indent;
    Py_ssize_t indent_len;
} ser_opts;

/* Lines past this depth keep its prefix: parse_xml nests without limit, and a prefix that grew with the depth would
   make the output quadratic in the input. */
#define TH_MAX_PRETTY_INDENT_LEVELS 64

static void ser_newline_indent(sbuf *out, const ser_opts *opts, int depth) {
    if (depth == 0 || opts->indent_len == 0) {
        sbuf_putc(out, '\n');
        return;
    }
    int levels = depth < TH_MAX_PRETTY_INDENT_LEVELS ? depth : TH_MAX_PRETTY_INDENT_LEVELS;
    const Py_ssize_t room = (PY_SSIZE_T_MAX - out->len - 1) / opts->indent_len;
    if (levels > room) { /* GCOVR_EXCL_BR_LINE: allocation size overflow */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation size overflow */
        return;          /* GCOVR_EXCL_LINE: allocation size overflow */
    }
    Py_ssize_t length = levels * opts->indent_len;
    TH_WORK_STEPS_ADD(length);
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
    if (text == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        return 0;                             /* GCOVR_EXCL_LINE: the run that writes this node fails */
    }
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
            if (text == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                out->failed = 1;                      /* GCOVR_EXCL_LINE: allocation failure */
                return last;                          /* GCOVR_EXCL_LINE: allocation failure */
            }
            Py_ssize_t begin = 0;
            Py_ssize_t end = node->text_len;
            while (node == start && is_space(text[begin])) {
                begin++;
            }
            while (node == visible && is_space(text[end - 1])) {
                end--;
            }
            if (opts->xml) {
                sbuf_put_xml_text(out, text + begin, end - begin, 0);
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
               is_rawtext_element(node, tree->scripting) || pretty_layout(tree, node) == PRETTY_INLINE ||
               (node->atom == TH_TAG_UNKNOWN && opts->out->xml &&
                !xml_element_name_wellformed(node->text, node->text_len))) {
        /* a leaf, a raw-text or whitespace-significant element, inline content, or an element
           XML leaves out: written verbatim, and treated as a leaf by the walk */
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
        const Py_UCS4 *text = need_text(tree, node);
        if (text == NULL) {  /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            PyMem_Free(out); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        memcpy(out, text, (size_t)len * sizeof(Py_UCS4));
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

static void collect_text(sbuf *out, th_tree *tree, th_node *root) {
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT) {
            sbuf_put_ucs4(out, need_text(tree, node), node->text_len);
        }
    }
}

Py_UCS4 *th_node_text(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    if (node->type == TH_NODE_TEXT) {
        sbuf_put_ucs4(&out, need_text(tree, node), node->text_len);
    } else {
        collect_text(&out, tree, node);
    }
    return sbuf_finish(&out, out_len);
}

Py_UCS4 *th_node_child_text(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            sbuf_put_ucs4(&out, need_text(tree, child), child->text_len);
        }
    }
    return sbuf_finish(&out, out_len);
}

static PyObject *string_from_bits(const Py_UCS4 *data, Py_ssize_t length, Py_UCS4 bits) {
    Py_UCS4 maxchar = bits <= 0x7F ? 0x7F : bits <= 0xFF ? 0xFF : bits <= 0xFFFF ? 0xFFFF : 0x10FFFF;
    PyObject *result = PyUnicode_New(length, th_str_maxchar(maxchar));
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    int kind = PyUnicode_KIND(result);
    if (kind == PyUnicode_1BYTE_KIND) {
        Py_UCS1 *destination = PyUnicode_1BYTE_DATA(result);
        for (Py_ssize_t index = 0; index < length; index++) {
            destination[index] = (Py_UCS1)data[index];
        }
#ifndef PYPY_VERSION
    } else if (kind == PyUnicode_2BYTE_KIND) {
        Py_UCS2 *destination = PyUnicode_2BYTE_DATA(result);
        for (Py_ssize_t index = 0; index < length; index++) {
            destination[index] = (Py_UCS2)data[index];
        }
#endif
    } else {
        memcpy(PyUnicode_4BYTE_DATA(result), data, (size_t)length * sizeof(Py_UCS4));
    }
    return result;
}

PyObject *th_markup_string(const Py_UCS4 *data, Py_ssize_t length) {
    /* CPython reuses its cached Latin-1 characters through this constructor. */
    if (length <= 1) {
        return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, length);
    }
    Py_UCS4 bits = 0;
    Py_ssize_t end = 0;
    /* Check the width in blocks so the inner loop vectorizes and wide output stops the scan early. */
    while (end < length && bits <= 0xFFFF) {
        Py_ssize_t index = end;
        end += Py_MIN(length - end, 256);
        for (; index < end; index++) {
            bits |= data[index];
        }
    }
    return string_from_bits(data, length, bits);
}

const void *th_node_chars(th_tree *tree, th_node *node, int *kind) {
    if (text_is_span(node)) {
        *kind = tree->kind;
        return (const char *)tree->data + text_span_offset(node) * tree->kind;
    }
    *kind = PyUnicode_4BYTE_KIND;
    return node->text;
}

Py_UCS4 th_chars_width_bits_long(const void *data, int kind, Py_ssize_t length) {
    Py_UCS4 limit = kind == PyUnicode_1BYTE_KIND ? 0x7F : kind == PyUnicode_2BYTE_KIND ? 0xFF : 0xFFFF;
    Py_UCS4 bits = 0;
    /* blocks keep the inner loops free of exits, so they vectorize, and a wide character ends the scan early */
    for (Py_ssize_t start = 0; start < length && bits <= limit; start += 512) {
        Py_ssize_t end = Py_MIN(length, start + 512);
        if (kind == PyUnicode_1BYTE_KIND) {
            const Py_UCS1 *chars = (const Py_UCS1 *)data;
            Py_UCS1 block = 0;
            for (Py_ssize_t index = start; index < end; index++) {
                block |= chars[index];
            }
            bits |= block;
        } else if (kind == PyUnicode_2BYTE_KIND) {
            const Py_UCS2 *chars = (const Py_UCS2 *)data;
            Py_UCS2 block = 0;
            for (Py_ssize_t index = start; index < end; index++) {
                block |= chars[index];
            }
            bits |= block;
        } else {
            const Py_UCS4 *chars = (const Py_UCS4 *)data;
            for (Py_ssize_t index = start; index < end; index++) {
                bits |= chars[index];
            }
        }
    }
    return bits;
}

/* The text of a subtree with several Text nodes, from `first` on: a UCS-4 snapshot whose OR picks the output width. */
static PyObject *snapshot_text_string(th_tree *tree, th_node *root, th_node *first) {
    sbuf snapshot = {NULL, 0, 0, 0, 0, NULL};
    Py_UCS4 bits = 0;
    for (th_node *node = first; node != NULL; node = preorder_next(node, root)) {
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
    PyObject *result = string_from_bits(snapshot.data, snapshot.len, bits);
    PyMem_Free(snapshot.data);
    return result;
}

/* A subtree holding a single Text node with characters, the common case of a leaf element or a lone Text node, gets
   its str built straight from where those characters sit: no UCS-4 snapshot, no realized span, and a memcpy whenever
   the storage width is the output width. Several Text nodes take the snapshot walk. */
/* Each pairing of widths gets its own loop over typed pointers, so the compiler vectorizes every one. */
void th_chars_copy(void *destination, int destination_kind, const void *source, int source_kind, Py_ssize_t length) {
    if (destination_kind == source_kind) {
        memcpy(destination, source, (size_t)length * (size_t)source_kind);
    } else if (destination_kind == PyUnicode_1BYTE_KIND && source_kind == PyUnicode_2BYTE_KIND) {
        Py_UCS1 *out = destination;
        const Py_UCS2 *in = source;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = (Py_UCS1)in[index];
        }
    } else if (destination_kind == PyUnicode_1BYTE_KIND) {
        Py_UCS1 *out = destination;
        const Py_UCS4 *in = source;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = (Py_UCS1)in[index];
        }
    } else if (destination_kind == PyUnicode_2BYTE_KIND && source_kind == PyUnicode_1BYTE_KIND) {
        Py_UCS2 *out = destination;
        const Py_UCS1 *in = source;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = (Py_UCS2)in[index];
        }
    } else if (destination_kind == PyUnicode_2BYTE_KIND) {
        Py_UCS2 *out = destination;
        const Py_UCS4 *in = source;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = (Py_UCS2)in[index];
        }
    } else if (source_kind == PyUnicode_1BYTE_KIND) {
        Py_UCS4 *out = destination;
        const Py_UCS1 *in = source;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = (Py_UCS4)in[index];
        }
    } else {
        Py_UCS4 *out = destination;
        const Py_UCS2 *in = source;
        for (Py_ssize_t index = 0; index < length; index++) {
            out[index] = (Py_UCS4)in[index];
        }
    }
}

/* Latin-1 storage copied out as ASCII, checking the bytes in the same pass, so ASCII text costs one copy and no
   separate width scan; a byte past 0x7F swaps the copy for a Latin-1 one. */
static PyObject *ascii_text_string(const Py_UCS1 *characters, Py_ssize_t length) {
    PyObject *result = PyUnicode_New(length, 0x7F);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_UCS1 *destination = PyUnicode_1BYTE_DATA(result);
    for (Py_ssize_t start = 0; start < length; start += 512) {
        Py_ssize_t end = Py_MIN(length, start + 512);
        Py_UCS1 bits = 0;
        for (Py_ssize_t index = start; index < end; index++) {
            destination[index] = characters[index];
            bits |= characters[index];
        }
        if (bits > 0x7F) {
            Py_DECREF(result);
            result = PyUnicode_New(length, 0xFF);
            if (result != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
                memcpy(PyUnicode_1BYTE_DATA(result), characters, (size_t)length);
            }
            return result;
        }
    }
    return result;
}

int th_tree_input_ascii(th_tree *tree, Py_ssize_t reading) {
    if (tree->source_ascii == 0) {
        if (reading < tree->length / 4) {
            return 0;
        }
        tree->source_ascii = th_chars_width_bits(tree->data, PyUnicode_1BYTE_KIND, tree->length) <= 0x7F ? 1 : -1;
    }
    return tree->source_ascii > 0;
}

static PyObject *single_text_string(th_tree *tree, th_node *node) {
    int kind;
    const void *characters = th_node_chars(tree, node, &kind);
    if (kind == PyUnicode_1BYTE_KIND) {
        if (!th_tree_input_ascii(tree, node->text_len)) {
            return ascii_text_string(characters, node->text_len);
        }
        PyObject *result = PyUnicode_New(node->text_len, 0x7F);
        if (result != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            memcpy(PyUnicode_1BYTE_DATA(result), characters, (size_t)node->text_len);
        }
        return result;
    }
    Py_UCS4 bits = th_chars_width_bits(characters, kind, node->text_len);
    Py_UCS4 maxchar = bits <= 0x7F ? 0x7F : bits <= 0xFF ? 0xFF : bits <= 0xFFFF ? 0xFFFF : 0x10FFFF;
    PyObject *result = PyUnicode_New(node->text_len, th_str_maxchar(maxchar));
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_chars_copy(PyUnicode_DATA(result), PyUnicode_KIND(result), characters, kind, node->text_len);
    return result;
}

PyObject *th_node_text_string(th_tree *tree, th_node *root) {
    th_node *first = root->type == TH_NODE_TEXT ? root : root->first_child;
    th_node *only = NULL;
    for (th_node *node = first; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT && node->text_len > 0) {
            if (only != NULL) {
                return snapshot_text_string(tree, root, only);
            }
            only = node;
        }
    }
    return only == NULL ? PyUnicode_New(0, 0x7F) : single_text_string(tree, only);
}

/* Copy every descendant Text node's code points of node into buf at pos, realizing
   zero-copy spans on the way; the caller sizes buf to at least the subtree's text
   length. The find(text=) C scan reuses one buffer across candidates so no per-node
   str is built; a Text or Content child of a content fragment is descended like an
   element. Stores the count of code points copied in out_len and returns 0, or -1
   when realizing a span fails. */
static int collect_text_into(th_tree *tree, th_node *root, Py_UCS4 *buf, Py_ssize_t *out_len) {
    Py_ssize_t pos = 0;
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(tree, node);
            if (text == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                return -1;                            /* GCOVR_EXCL_LINE: allocation failure */
            }
            memcpy(buf + pos, text, (size_t)node->text_len * sizeof(Py_UCS4));
            pos += node->text_len;
        }
    }
    *out_len = pos;
    return 0;
}

int th_node_collect_text(th_tree *tree, th_node *node, Py_UCS4 *buf, Py_ssize_t *out_len) {
    return collect_text_into(tree, node, buf, out_len);
}

/* The WHATWG-conformant defaults the html/inner_html accessors serialize under:
   minimal escaping, source attribute order, no charset injection. */
static const th_serialize_opts ser_default_opts = {TH_FMT_WHATWG, 0,    0,    NULL, 0,    0, 0,   NULL, 0,
                                                   NULL,          NULL, NULL, NULL, NULL, 0, NULL};

Py_UCS4 *th_node_html(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    sbuf_presize_for_root(&out, tree, node);
    serialize_compact(&out, tree, node, &ser_default_opts);
    return sbuf_finish(&out, out_len);
}

/* The html accessor defaults with the CommonMark attribute-name filter, for the HTML Markdown embeds raw. */
Py_UCS4 *th_node_markdown_html(th_tree *tree, th_node *node, th_markdown_html context, Py_ssize_t *clone_budget,
                               Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    const th_serialize_opts opts = {TH_FMT_WHATWG, 0,    0,    NULL, 0, 0,   0, NULL, (int)context, NULL,
                                    clone_budget,  NULL, NULL, NULL, 0, NULL};
    serialize_compact(&out, tree, node, &opts);
    return sbuf_finish(&out, out_len);
}

Py_UCS4 *th_node_markdown_open_tag(th_tree *tree, th_node *node, th_markdown_html context, Py_ssize_t *clone_budget,
                                   Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    const th_serialize_opts opts = {TH_FMT_WHATWG, 0,    0,    NULL, 0, 0,   0, NULL, (int)context, NULL,
                                    clone_budget,  NULL, NULL, NULL, 0, NULL};
    ser_open_tag(&out, tree, node, &opts);
    sbuf_putc(&out, '>');
    return sbuf_finish(&out, out_len);
}

Py_UCS4 *th_node_markdown_table_html(th_tree *tree, th_node *node, const th_cell_wrap *wrap, Py_ssize_t *clone_budget,
                                     Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    const th_serialize_opts opts = {TH_FMT_WHATWG, 0,    0,    NULL, 0, 0,   0, NULL, TH_MARKDOWN_HTML_CELL, NULL,
                                    clone_budget,  NULL, NULL, NULL, 0, NULL};
    th_node *current = node;
    while (current != NULL) {
        current = serialize_compact_step(&out, tree, current, node, &opts, wrap);
    }
    return sbuf_finish(&out, out_len);
}

Py_UCS4 *th_node_inner_html(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    if (node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && is_serialize_void_atom(node->atom)) {
        return sbuf_finish(&out, out_len);
    }
    /* a clonable shadow root sets the bit that marks a raw-text element */
    if (node->type == TH_NODE_ELEMENT && is_rawtext_element(node, tree->scripting)) {
        ser_put_rawtext(&out, tree, node, &ser_default_opts);
    } else {
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            serialize_compact(&out, tree, child, &ser_default_opts);
        }
    }
    return sbuf_finish(&out, out_len);
}

/* The inner_html defaults with XML/XHTML syntax on, the same rules Node.serialize(Html(xml=True)) emits under. */
static const th_serialize_opts ser_xml_opts = {TH_FMT_WHATWG, 0,    0,    NULL, 0,    1, 0,   NULL, 0,
                                               NULL,          NULL, NULL, NULL, NULL, 0, NULL};

/* The children of node as XML. A non-NULL spend has each child of an XML tree carry the namespace declarations it
   inherits, charged to that account; NULL leaves them out. */
static Py_UCS4 *inner_xml(th_tree *tree, th_node *node, th_xml_ns_spend *spend, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    th_serialize_opts opts = ser_xml_opts;
    th_xml_xlink_walk xlink = {NULL, NULL};
    th_xml_ns_scope scope;
    ser_xml_ns_scope_init(&scope, node);
    opts.xml_scope = spend != NULL && th_tree_is_xml(tree) ? &scope : NULL;
    opts.xml_xlink = th_tree_is_xml(tree) ? &xlink : NULL;
    opts.xml_ns_spend = spend;
    th_xml_unwrap unwrap = {.spend = {0, 0, TH_XML_NS_BASE}};
    opts.xml_unwrap = &unwrap;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        serialize_compact(&out, tree, child, &opts);
    }
    th_xml_unwrap_free(&unwrap);
    ser_xml_ns_scope_free(&scope);
    return sbuf_finish(&out, out_len);
}

Py_UCS4 *th_node_inner_xml(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    th_xml_ns_spend spend = {0, 0, TH_XML_NS_BASE};
    return inner_xml(tree, node, &spend, out_len);
}

Py_UCS4 *th_node_inner_xml_construct(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    th_xml_ns_spend spend = {0, 0, TH_XML_NS_FEED_BASE};
    return inner_xml(tree, node, &spend, out_len);
}

Py_UCS4 *th_node_inner_xml_local(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    return inner_xml(tree, node, NULL, out_len);
}

static int inner_preserves_text(th_tree *tree, th_node *root, const th_serialize_opts *opts) {
    return opts->inner && !opts->xml && root->type == TH_NODE_ELEMENT && root->ns == TH_NS_HTML &&
           (is_rawtext_element(root, tree->scripting) || root->atom == TH_TAG_PRE || root->atom == TH_TAG_TEXTAREA ||
            root->atom == TH_TAG_LISTING);
}

Py_UCS4 *th_node_serialize(th_tree *tree, th_node *node, const th_serialize_opts *opts, const Py_UCS4 *indent,
                           Py_ssize_t indent_len, Py_ssize_t *out_len) {
    sbuf out = {NULL, 0, 0, 0, 0, NULL};
    th_serialize_opts context = *opts;
    th_xml_xlink_walk xlink = {NULL, NULL};
    th_xml_ns_spend spend = {0, 0, TH_XML_NS_BASE};
    th_xml_unwrap unwrap = {.spend = {0, 0, TH_XML_NS_BASE}};
    th_xml_ns_scope scope;
    ser_xml_ns_scope_init(&scope, opts->inner ? node : node->parent);
    context.xml_scope = opts->xml && th_tree_is_xml(tree) ? &scope : NULL;
    context.xml_xlink = th_tree_is_xml(tree) ? &xlink : NULL;
    context.xml_ns_spend = opts->inner ? &spend : NULL; /* the one copy a whole subtree carries multiplies nothing */
    context.xml_unwrap = &unwrap;
    opts = &context;
    sbuf_presize_for_markup(&out, tree, node, opts->formatter);
    if (indent == NULL || inner_preserves_text(tree, node, opts)) {
        serialize_compact(&out, tree, node, opts);
    } else {
        ser_opts pretty = {opts, indent, indent_len};
        serialize_pretty(&out, tree, node, &pretty, 0);
    }
    th_xml_unwrap_free(&unwrap);
    ser_xml_ns_scope_free(&scope);
    return sbuf_finish(&out, out_len);
}

/* Emit whole nodes from cursor until the chunk holds at least this many code points;
   stopping only on a per-node boundary keeps each chunk near this size, bar the one
   case a single text node exceeds it (it still emits as one chunk). The output stays
   bounded to roughly one chunk, which is the point: no full-size string is built. */
#define TH_SERIALIZE_CHUNK 8192

/* The tree may be edited between chunks, so each chunk gathers the namespace scope it needs afresh rather than the
   cursor carrying one that points into the tree. A scope larger than a chunk would then cost more to gather than the
   chunk costs to write, every chunk, so a chunk that gathered one runs on until it holds as many code points as the
   gather walked ancestors and attributes. */
static int chunk_has_room(const sbuf *out, const th_xml_ns_scope *scope) {
    return out->len < TH_SERIALIZE_CHUNK || out->len < scope->walked;
}

Py_UCS4 *th_node_serialize_chunk(th_tree *tree, th_node *root, const th_serialize_opts *opts, const Py_UCS4 *indent,
                                 Py_ssize_t indent_len, th_ser_cursor *cursor, Py_ssize_t *out_len) {
    /* the earlier chunks still fund clone attributes, and the value sets they wrote stay written */
    sbuf out = {NULL, 0, 0, 0, cursor->clone_credit, cursor->clone_seen};
    th_serialize_opts context = *opts;
    th_xml_ns_scope scope;
    ser_xml_ns_scope_init(&scope, opts->inner ? root : root->parent);
    context.xml_scope = opts->xml && th_tree_is_xml(tree) ? &scope : NULL;
    context.xml_xlink = th_tree_is_xml(tree) ? &cursor->xlink : NULL; /* kept across chunks, like the resume node */
    context.xml_ns_spend = opts->inner ? &cursor->xml_ns : NULL;
    context.xml_unwrap = &cursor->unwrap;
    opts = &context;
    /* one chunk grows to about TH_SERIALIZE_CHUNK before it is handed off, so size the
       buffer to that up front rather than let the doubling reserve walk 256 -> 8192 on
       every chunk (a node straddling the limit still forces at most one more grow) */
    sbuf_reserve(&out, TH_SERIALIZE_CHUNK);
    if (indent == NULL || inner_preserves_text(tree, root, opts)) {
        while (cursor->node != NULL && chunk_has_room(&out, &scope)) {
            cursor->node = serialize_compact_step(&out, tree, cursor->node, root, opts, NULL);
        }
    } else {
        ser_opts pretty = {opts, indent, indent_len};
        while (cursor->node != NULL && chunk_has_room(&out, &scope)) {
            cursor->node = serialize_pretty_step(&out, tree, cursor->node, root, &pretty, &cursor->depth);
        }
    }
    cursor->xml_ns.flushed += out.len;
    cursor->unwrap.spend.flushed += out.len;
    cursor->clone_credit = out.clone_credit + out.len;
    cursor->clone_seen = out.clone_seen;
    out.clone_seen = NULL;
    ser_xml_ns_scope_free(&scope);
    return sbuf_finish(&out, out_len);
}
