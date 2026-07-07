/* Strict XML 1.0 parsing mode: a hand-written recursive-descent well-formedness
   parser that builds the same arena-backed th_node tree the HTML tree builder
   produces, so every downstream consumer (navigation, serialization, XPath)
   works unchanged. It shares the tree's arena, node allocator and interned atoms
   through dom/tree_internal.h and creates no PyObjects.

   Unlike the WHATWG path it applies XML productions rather than HTML quirks:
   names are case-sensitive, `<x/>` self-closes any element, CDATA sections and
   processing instructions become their own nodes, only the five predefined plus
   numeric references resolve, and a namespace prefix must be declared. The first
   well-formedness violation is recorded on the tree's error sink and the parse
   stops; the caller raises. There is no silent recovery. */

#include "dom/tree.h"
#include "dom/tree_internal.h" /* arena, node_new, node_append, copy_input_span */

#include <string.h>

static int xml_is_space(Py_UCS4 ch) {
    return ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r';
}

/* XML NameStartChar, approximated: a letter, '_' or ':' (the qualified-name
   separator), or any code point past ASCII. The exact spec ranges exclude a few
   punctuation blocks; a name that starts with a digit, '-' or '.' -- the cases
   real input mistakes -- is still rejected. */
static int is_name_start(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || ch == '_' || ch == ':' || ch >= 0x80;
}

static int is_name_char(Py_UCS4 ch) {
    return is_name_start(ch) || (ch >= '0' && ch <= '9') || ch == '-' || ch == '.';
}

/* A code point is an XML Char when it is TAB/LF/CR or a non-control scalar value
   outside the surrogate range; the numeric-reference guard rejects everything else. */
static int is_xml_char(long ch) {
    return ch == 0x9 || ch == 0xA || ch == 0xD || (ch >= 0x20 && ch <= 0xD7FF) || (ch >= 0xE000 && ch <= 0xFFFD) ||
           (ch >= 0x10000 && ch <= 0x10FFFF);
}

/* One in-scope namespace prefix declared by an ancestor's xmlns:prefix attribute,
   kept as arena-owned code points and popped when its declaring element closes. */
typedef struct {
    Py_UCS4 *prefix;
    Py_ssize_t prefix_len;
    Py_ssize_t depth;
} xml_nsdecl;

typedef struct {
    th_tree *tree;
    int kind;
    const void *data;
    Py_ssize_t length;
    Py_ssize_t pos;
    th_node *document;
    th_node **stack; /* open elements */
    Py_ssize_t stack_len, stack_cap;
    xml_nsdecl *ns; /* in-scope prefix declarations */
    Py_ssize_t ns_len, ns_cap;
    Py_ssize_t *attr_spans; /* flattened (start, end) name ranges of the current tag's attributes */
    Py_ssize_t attr_spans_len, attr_spans_cap;
    Py_UCS4 *scratch; /* reusable buffer for entity-expanded text/attribute runs */
    Py_ssize_t scratch_len, scratch_cap;
    Py_UCS4 *names; /* reusable buffer widening a tag name to code points */
    Py_ssize_t names_cap;
    char *u8; /* reusable buffer holding an attribute name as UTF-8 */
    Py_ssize_t u8_cap;
    th_node *root;    /* the single root element once opened */
    int root_closed;  /* the root element has been closed */
    int have_doctype; /* a doctype has been consumed */
    int error;        /* a well-formedness error has been recorded */
} xml_parser;

static Py_UCS4 cp(const xml_parser *parser, Py_ssize_t index) {
    if (parser->kind == PyUnicode_1BYTE_KIND) {
        return ((const uint8_t *)parser->data)[index];
    }
    if (parser->kind == PyUnicode_2BYTE_KIND) {
        return ((const uint16_t *)parser->data)[index];
    }
    return ((const Py_UCS4 *)parser->data)[index];
}

/* Record the first well-formedness error: resolve its 1-based line / 0-based
   column by scanning to `at`, push it, and latch so the parse unwinds. */
static void record(xml_parser *parser, const char *code, Py_ssize_t at) {
    Py_ssize_t line = 1;
    Py_ssize_t column = 0;
    for (Py_ssize_t index = 0; index < at; index++) {
        if (cp(parser, index) == '\n') {
            line++;
            column = 0;
        } else {
            column++;
        }
    }
    th_error_sink_push(&parser->tree->errors, code, line, column);
    parser->error = 1;
}

static int scratch_push(xml_parser *parser, Py_UCS4 ch) {
    if (parser->scratch_len == parser->scratch_cap) {
        Py_ssize_t cap = parser->scratch_cap ? parser->scratch_cap * 2 : 64;
        Py_UCS4 *grown = PyMem_Realloc(parser->scratch, (size_t)cap * sizeof(Py_UCS4));
        if (grown == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            parser->tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->scratch = grown;
        parser->scratch_cap = cap;
    }
    parser->scratch[parser->scratch_len++] = ch;
    return 0;
}

/* Widen the input range [start, start+len) into the reusable name buffer. */
static Py_UCS4 *widen(xml_parser *parser, Py_ssize_t start, Py_ssize_t len) {
    if (len > parser->names_cap) {
        Py_UCS4 *grown = PyMem_Realloc(parser->names, (size_t)len * sizeof(Py_UCS4));
        if (grown == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            parser->tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->names = grown;
        parser->names_cap = len;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        parser->names[index] = cp(parser, start + index);
    }
    return parser->names;
}

/* Encode the input range [start, start+len) as UTF-8 into the reusable buffer;
 *out_len receives the byte count. NULL on allocation failure. */
static const char *encode_utf8(xml_parser *parser, Py_ssize_t start, Py_ssize_t len, Py_ssize_t *out_len) {
    Py_ssize_t needed = len * 4;
    if (needed > parser->u8_cap) {
        char *grown = PyMem_Realloc(parser->u8, (size_t)needed);
        if (grown == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            parser->tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->u8 = grown;
        parser->u8_cap = needed;
    }
    Py_ssize_t out = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = cp(parser, start + index);
        if (ch < 0x80) {
            parser->u8[out++] = (char)ch;
        } else if (ch < 0x800) {
            parser->u8[out++] = (char)(0xC0 | (ch >> 6));
            parser->u8[out++] = (char)(0x80 | (ch & 0x3F));
        } else if (ch < 0x10000) {
            parser->u8[out++] = (char)(0xE0 | (ch >> 12));
            parser->u8[out++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            parser->u8[out++] = (char)(0x80 | (ch & 0x3F));
        } else {
            parser->u8[out++] = (char)(0xF0 | (ch >> 18));
            parser->u8[out++] = (char)(0x80 | ((ch >> 12) & 0x3F));
            parser->u8[out++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            parser->u8[out++] = (char)(0x80 | (ch & 0x3F));
        }
    }
    *out_len = out;
    return parser->u8;
}

/* Whether the input at `pos` begins with the ASCII literal `text`. */
static int starts_with(const xml_parser *parser, Py_ssize_t pos, const char *text) {
    for (Py_ssize_t index = 0; text[index] != '\0'; index++) {
        if (pos + index >= parser->length || cp(parser, pos + index) != (Py_UCS4)text[index]) {
            return 0;
        }
    }
    return 1;
}

static void skip_space(xml_parser *parser) {
    while (parser->pos < parser->length && xml_is_space(cp(parser, parser->pos))) {
        parser->pos++;
    }
}

/* Whether the name range [start, end) equals the ASCII literal `text`. */
static int name_equals(const xml_parser *parser, Py_ssize_t start, Py_ssize_t end, const char *text) {
    Py_ssize_t len = 0;
    while (text[len] != '\0') {
        len++;
    }
    if (end - start != len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (cp(parser, start + index) != (Py_UCS4)text[index]) {
            return 0;
        }
    }
    return 1;
}

/* Read a Name starting at `pos`, leaving `pos` just past it and returning its end
   offset; records xml-invalid-name and returns -1 on a bad start character. */
static Py_ssize_t read_name(xml_parser *parser) {
    Py_ssize_t start = parser->pos;
    if (start >= parser->length || !is_name_start(cp(parser, start))) {
        record(parser, "xml-invalid-name", start);
        return -1;
    }
    parser->pos++;
    while (parser->pos < parser->length && is_name_char(cp(parser, parser->pos))) {
        parser->pos++;
    }
    return parser->pos;
}

/* Resolve the reference at `pos` (on '&') to one code point, advancing past ';'.
   Returns the code point, or (Py_UCS4)-1 with an error recorded. */
static Py_UCS4 read_reference(xml_parser *parser) {
    Py_ssize_t amp = parser->pos;
    parser->pos++; /* past '&' */
    if (parser->pos < parser->length && cp(parser, parser->pos) == '#') {
        parser->pos++;
        int hex = parser->pos < parser->length && (cp(parser, parser->pos) == 'x' || cp(parser, parser->pos) == 'X');
        if (hex) {
            parser->pos++;
        }
        long value = 0;
        Py_ssize_t digits = 0;
        while (parser->pos < parser->length && cp(parser, parser->pos) != ';') {
            Py_UCS4 ch = cp(parser, parser->pos);
            int digit;
            if (ch >= '0' && ch <= '9') {
                digit = (int)(ch - '0');
            } else if (hex && ch >= 'a' && ch <= 'f') {
                digit = (int)(ch - 'a') + 10;
            } else if (hex && ch >= 'A' && ch <= 'F') {
                digit = (int)(ch - 'A') + 10;
            } else {
                record(parser, "xml-invalid-char-ref", amp);
                return (Py_UCS4)-1;
            }
            value = value * (hex ? 16 : 10) + digit;
            if (value > 0x10FFFF) {
                value = 0x110000; /* clamp so a huge run cannot overflow before the range check */
            }
            digits++;
            parser->pos++;
        }
        if (parser->pos >= parser->length) {
            record(parser, "xml-unterminated-reference", amp);
            return (Py_UCS4)-1;
        }
        if (digits == 0 || !is_xml_char(value)) {
            record(parser, "xml-invalid-char-ref", amp);
            return (Py_UCS4)-1;
        }
        parser->pos++; /* past ';' */
        return (Py_UCS4)value;
    }
    Py_ssize_t start = parser->pos;
    while (parser->pos < parser->length && cp(parser, parser->pos) != ';') {
        parser->pos++;
    }
    if (parser->pos >= parser->length) {
        record(parser, "xml-unterminated-reference", amp);
        return (Py_UCS4)-1;
    }
    Py_ssize_t end = parser->pos;
    parser->pos++; /* past ';' */
    if (name_equals(parser, start, end, "amp")) {
        return '&';
    }
    if (name_equals(parser, start, end, "lt")) {
        return '<';
    }
    if (name_equals(parser, start, end, "gt")) {
        return '>';
    }
    if (name_equals(parser, start, end, "quot")) {
        return '"';
    }
    if (name_equals(parser, start, end, "apos")) {
        return '\'';
    }
    record(parser, "xml-undefined-entity", amp);
    return (Py_UCS4)-1;
}

static th_node *current(const xml_parser *parser) {
    return parser->stack_len > 0 ? parser->stack[parser->stack_len - 1] : parser->document;
}

static int push_open(xml_parser *parser, th_node *element) {
    if (parser->stack_len == parser->stack_cap) {
        Py_ssize_t cap = parser->stack_cap ? parser->stack_cap * 2 : 16;
        th_node **grown = PyMem_Realloc(parser->stack, (size_t)cap * sizeof(th_node *));
        if (grown == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            parser->tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->stack = grown;
        parser->stack_cap = cap;
    }
    parser->stack[parser->stack_len++] = element;
    return 0;
}

/* Declare a namespace prefix (arena-owned copy) in scope at `depth`. */
static int declare_prefix(xml_parser *parser, Py_ssize_t start, Py_ssize_t len, Py_ssize_t depth) {
    if (parser->ns_len == parser->ns_cap) {
        Py_ssize_t cap = parser->ns_cap ? parser->ns_cap * 2 : 8;
        xml_nsdecl *grown = PyMem_Realloc(parser->ns, (size_t)cap * sizeof(xml_nsdecl));
        if (grown == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            parser->tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->ns = grown;
        parser->ns_cap = cap;
    }
    Py_UCS4 *owned = copy_input_span(parser->tree, start, len);
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    parser->ns[parser->ns_len].prefix = owned;
    parser->ns[parser->ns_len].prefix_len = len;
    parser->ns[parser->ns_len].depth = depth;
    parser->ns_len++;
    return 0;
}

static void pop_prefixes(xml_parser *parser, Py_ssize_t depth) {
    while (parser->ns_len > 0 && parser->ns[parser->ns_len - 1].depth >= depth) {
        parser->ns_len--;
    }
}

/* Whether the prefix range [start, end) is the reserved "xml" or an in-scope
   declaration; the source position `at` locates an undeclared-prefix error. */
static int prefix_in_scope(xml_parser *parser, Py_ssize_t start, Py_ssize_t end, Py_ssize_t at) {
    if (name_equals(parser, start, end, "xml")) {
        return 1;
    }
    Py_ssize_t len = end - start;
    for (Py_ssize_t index = parser->ns_len - 1; index >= 0; index--) {
        if (parser->ns[index].prefix_len != len) {
            continue;
        }
        int same = 1;
        for (Py_ssize_t offset = 0; offset < len; offset++) {
            if (parser->ns[index].prefix[offset] != cp(parser, start + offset)) {
                same = 0;
                break;
            }
        }
        if (same) {
            return 1;
        }
    }
    record(parser, "xml-undeclared-namespace", at);
    return 0;
}

/* Validate the prefix of the qualified name in [start, end): a leading prefix
   before ':' must be declared. Returns 0, or -1 with an error recorded. */
static int check_qname_prefix(xml_parser *parser, Py_ssize_t start, Py_ssize_t end) {
    for (Py_ssize_t index = start; index < end; index++) {
        if (cp(parser, index) == ':') {
            if (!prefix_in_scope(parser, start, index, start)) {
                return -1;
            }
            return 0;
        }
    }
    return 0;
}

/* Consume a text run of character data into `current`. Records ']]>' in content
   and any reference error; a top-level run outside the root must be whitespace. */
static int consume_text(xml_parser *parser) {
    Py_ssize_t start = parser->pos;
    Py_ssize_t scan = start;
    int needs_build = 0;
    while (scan < parser->length && cp(parser, scan) != '<') {
        Py_UCS4 ch = cp(parser, scan);
        if (ch < 0x20 && ch != '\t' && ch != '\n' && ch != '\r') {
            record(parser, "xml-invalid-char", scan);
            return -1;
        }
        if (ch == '&' || ch == '\r') {
            needs_build = 1;
        }
        if (ch == ']' && starts_with(parser, scan, "]]>")) {
            record(parser, "xml-cdata-close-in-content", scan);
            return -1;
        }
        scan++;
    }
    if (parser->stack_len == 0) {
        for (Py_ssize_t index = start; index < scan; index++) {
            if (!xml_is_space(cp(parser, index))) {
                record(parser, "xml-content-outside-root", index);
                return -1;
            }
        }
        parser->pos = scan;
        return 0; /* prolog/epilog whitespace is not part of the tree */
    }
    if (!needs_build) {
        th_node *text = node_new(parser->tree, TH_NODE_TEXT);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        text->attr_count = start; /* zero-copy span: input[start .. start+text_len] */
        text->text_len = scan - start;
        node_append(current(parser), text);
        parser->pos = scan;
        return 0;
    }
    parser->scratch_len = 0;
    while (parser->pos < scan) {
        Py_UCS4 ch = cp(parser, parser->pos);
        if (ch == '&') {
            Py_UCS4 resolved = read_reference(parser);
            if (parser->error) {
                return -1;
            }
            if (scratch_push(parser, resolved) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure unreachable */
                return -1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            continue;
        }
        if (ch == '\r') { /* line-ending normalization: CR and CRLF fold to LF */
            if (parser->pos + 1 < parser->length && cp(parser, parser->pos + 1) == '\n') {
                parser->pos++;
            }
            ch = '\n';
        }
        if (scratch_push(parser, ch) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure unreachable */
            return -1;                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->pos++;
    }
    th_node *text = th_tree_make_data_node(parser->tree, TH_NODE_TEXT, parser->scratch, parser->scratch_len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node_append(current(parser), text);
    return 0;
}

static int consume_comment(xml_parser *parser) {
    Py_ssize_t open = parser->pos;
    parser->pos += 4; /* past "<!--" */
    Py_ssize_t start = parser->pos;
    while (parser->pos < parser->length) {
        if (starts_with(parser, parser->pos, "--")) {
            if (!starts_with(parser, parser->pos, "-->")) {
                record(parser, "xml-double-hyphen-in-comment", parser->pos);
                return -1;
            }
            Py_UCS4 *data = widen(parser, start, parser->pos - start);
            if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            th_node *node = th_tree_make_data_node(parser->tree, TH_NODE_COMMENT, data, parser->pos - start);
            if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node_append(current(parser), node);
            parser->pos += 3; /* past "-->" */
            return 0;
        }
        parser->pos++;
    }
    record(parser, "xml-unterminated-comment", open);
    return -1;
}

static int consume_cdata(xml_parser *parser) {
    Py_ssize_t open = parser->pos;
    if (parser->stack_len == 0) {
        record(parser, "xml-cdata-outside-root", open);
        return -1;
    }
    parser->pos += 9; /* past "<![CDATA[" */
    Py_ssize_t start = parser->pos;
    while (parser->pos < parser->length) {
        if (starts_with(parser, parser->pos, "]]>")) {
            Py_UCS4 *data = widen(parser, start, parser->pos - start);
            if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            th_node *node = th_tree_make_data_node(parser->tree, TH_NODE_CDATA, data, parser->pos - start);
            if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node_append(current(parser), node);
            parser->pos += 3; /* past "]]>" */
            return 0;
        }
        parser->pos++;
    }
    record(parser, "xml-unterminated-cdata", open);
    return -1;
}

static int consume_pi(xml_parser *parser) {
    Py_ssize_t open = parser->pos;
    parser->pos += 2; /* past "<?" */
    Py_ssize_t target_start = parser->pos;
    Py_ssize_t target_end = read_name(parser);
    if (target_end < 0) {
        return -1;
    }
    int is_xml_decl = name_equals(parser, target_start, target_end, "xml");
    if (is_xml_decl && open != 0) { /* the XML declaration is only the very first construct */
        record(parser, "xml-reserved-pi-target", target_start);
        return -1;
    }
    Py_ssize_t data_start = parser->pos;
    while (parser->pos < parser->length && !starts_with(parser, parser->pos, "?>")) {
        parser->pos++;
    }
    if (parser->pos >= parser->length) {
        record(parser, "xml-unterminated-pi", open);
        return -1;
    }
    Py_ssize_t data_end = parser->pos;
    parser->pos += 2; /* past "?>" */
    if (is_xml_decl) {
        return 0; /* the declaration configures the parse; it is not a tree node */
    }
    while (data_start < data_end && xml_is_space(cp(parser, data_start))) {
        data_start++; /* the target/data separator is not part of the data */
    }
    Py_UCS4 *target = widen(parser, target_start, target_end - target_start);
    if (target == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t target_len = target_end - target_start;
    /* target and data must widen through separate buffers: copy the target out first */
    parser->scratch_len = 0;
    for (Py_ssize_t index = 0; index < target_len; index++) {
        if (scratch_push(parser, target[index]) < 0) { /* GCOVR_EXCL_BR_LINE: allocation unreachable */
            return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_UCS4 *data = widen(parser, data_start, data_end - data_start);
    if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *node = th_tree_make_pi(parser->tree, parser->scratch, parser->scratch_len, data, data_end - data_start);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node_append(current(parser), node);
    return 0;
}

/* Skip a <!DOCTYPE ...> declaration, matching a bracketed internal subset so a '>'
   inside it does not end the declaration early. The root name becomes a Doctype
   node; DTD-declared entities are not honored. */
static int consume_doctype(xml_parser *parser) {
    Py_ssize_t open = parser->pos;
    if (parser->stack_len > 0 || parser->root != NULL || parser->have_doctype) {
        record(parser, "xml-doctype-outside-prolog", open);
        return -1;
    }
    parser->pos += 9; /* past "<!DOCTYPE" */
    skip_space(parser);
    Py_ssize_t name_start = parser->pos;
    Py_ssize_t name_end = read_name(parser);
    if (name_end < 0) {
        return -1;
    }
    int depth = 0;
    while (parser->pos < parser->length) {
        Py_UCS4 ch = cp(parser, parser->pos);
        if (ch == '[') {
            depth++;
        } else if (ch == ']') {
            depth--;
        } else if (ch == '>' && depth == 0) {
            Py_UCS4 *name = widen(parser, name_start, name_end - name_start);
            if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            th_node *node = th_tree_make_data_node(parser->tree, TH_NODE_DOCTYPE, name, name_end - name_start);
            if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node_append(parser->document, node);
            parser->have_doctype = 1;
            parser->pos++; /* past '>' */
            return 0;
        }
        parser->pos++;
    }
    record(parser, "xml-unterminated-doctype", open);
    return -1;
}

static int consume_end_tag(xml_parser *parser) {
    Py_ssize_t open = parser->pos;
    parser->pos += 2; /* past "</" */
    Py_ssize_t name_start = parser->pos;
    Py_ssize_t name_end = read_name(parser);
    if (name_end < 0) {
        return -1;
    }
    skip_space(parser);
    if (parser->pos >= parser->length || cp(parser, parser->pos) != '>') {
        record(parser, "xml-unterminated-end-tag", open);
        return -1;
    }
    parser->pos++; /* past '>' */
    if (parser->stack_len == 0) {
        record(parser, "xml-unexpected-end-tag", name_start);
        return -1;
    }
    th_node *element = parser->stack[parser->stack_len - 1];
    Py_ssize_t name_len = name_end - name_start;
    int matches = element->text_len == name_len;
    for (Py_ssize_t index = 0; matches && index < name_len; index++) {
        if (element->text[index] != cp(parser, name_start + index)) {
            matches = 0;
        }
    }
    if (!matches) {
        record(parser, "xml-mismatched-tag", name_start);
        return -1;
    }
    element->tag_flags |= TH_ELEM_CLOSED_BY_END_TAG;
    pop_prefixes(parser, parser->stack_len - 1);
    parser->stack_len--;
    if (parser->stack_len == 0) {
        parser->root_closed = 1;
    }
    return 0;
}

/* Parse one attribute onto `element`, tracking any xmlns declaration at `depth`.
   Advances past the attribute; returns 0, or -1 with an error recorded. */
static int consume_attribute(xml_parser *parser, th_node *element, Py_ssize_t depth) {
    Py_ssize_t name_start = parser->pos;
    Py_ssize_t name_end = read_name(parser);
    if (name_end < 0) {
        return -1;
    }
    skip_space(parser);
    if (parser->pos >= parser->length || cp(parser, parser->pos) != '=') {
        record(parser, "xml-expected-equals", parser->pos < parser->length ? parser->pos : name_start);
        return -1;
    }
    parser->pos++; /* past '=' */
    skip_space(parser);
    if (parser->pos >= parser->length || (cp(parser, parser->pos) != '"' && cp(parser, parser->pos) != '\'')) {
        record(parser, "xml-unquoted-attribute", parser->pos < parser->length ? parser->pos : name_start);
        return -1;
    }
    Py_UCS4 quote = cp(parser, parser->pos);
    parser->pos++; /* past the opening quote */
    parser->scratch_len = 0;
    while (parser->pos < parser->length && cp(parser, parser->pos) != quote) {
        Py_UCS4 ch = cp(parser, parser->pos);
        if (ch == '<') {
            record(parser, "xml-lt-in-attribute", parser->pos);
            return -1;
        }
        if (ch == '&') {
            Py_UCS4 resolved = read_reference(parser);
            if (parser->error) {
                return -1;
            }
            if (scratch_push(parser, resolved) < 0) { /* GCOVR_EXCL_BR_LINE: allocation unreachable */
                return -1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            continue;
        }
        if (ch == '\t' || ch == '\n' || ch == '\r') {
            ch = ' '; /* attribute-value normalization folds literal whitespace to space */
        }
        if (scratch_push(parser, ch) < 0) { /* GCOVR_EXCL_BR_LINE: allocation unreachable */
            return -1;                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->pos++;
    }
    if (parser->pos >= parser->length) {
        record(parser, "xml-unterminated-attribute", name_start);
        return -1;
    }
    parser->pos++; /* past the closing quote */
    Py_ssize_t u8_len;
    const char *name = encode_utf8(parser, name_start, name_end - name_start, &u8_len);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (th_node_attr_find(parser->tree, element, name, u8_len) >= 0) {
        record(parser, "xml-duplicate-attribute", name_start);
        return -1;
    }
    int stored = th_node_attr_set(parser->tree, element, name, u8_len, parser->scratch, parser->scratch_len, 1);
    if (stored < 0) { /* GCOVR_EXCL_BR_LINE: th_node_attr_set only fails on allocation */
        return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (parser->attr_spans_len == parser->attr_spans_cap) {
        Py_ssize_t cap = parser->attr_spans_cap ? parser->attr_spans_cap * 2 : 16;
        Py_ssize_t *grown = PyMem_Realloc(parser->attr_spans, (size_t)cap * sizeof(Py_ssize_t));
        if (grown == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            parser->tree->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->attr_spans = grown;
        parser->attr_spans_cap = cap;
    }
    parser->attr_spans[parser->attr_spans_len++] = name_start;
    parser->attr_spans[parser->attr_spans_len++] = name_end;
    if (starts_with(parser, name_start, "xmlns:") && name_end - name_start > 6) {
        /* declare_prefix only fails on allocation */
        if (declare_prefix(parser, name_start + 6, name_end - (name_start + 6), depth) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

static int consume_start_tag(xml_parser *parser) {
    Py_ssize_t lt = parser->pos;
    if (parser->root_closed) {
        record(parser, "xml-content-outside-root", lt);
        return -1;
    }
    if (parser->root != NULL && parser->stack_len == 0) {
        record(parser, "xml-multiple-roots", lt);
        return -1;
    }
    parser->pos++; /* past '<' */
    Py_ssize_t name_start = parser->pos;
    Py_ssize_t name_end = read_name(parser);
    if (name_end < 0) {
        return -1;
    }
    Py_UCS4 *tag = widen(parser, name_start, name_end - name_start);
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *element = th_tree_make_element(parser->tree, tag, name_end - name_start, TH_TAG_UNKNOWN, 0);
    if (element == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t depth = parser->stack_len;
    parser->attr_spans_len = 0;
    int self_closing = 0;
    for (;;) {
        int had_space = parser->pos < parser->length && xml_is_space(cp(parser, parser->pos));
        skip_space(parser);
        if (parser->pos >= parser->length) {
            record(parser, "xml-unclosed-start-tag", lt);
            return -1;
        }
        Py_UCS4 ch = cp(parser, parser->pos);
        if (ch == '>') {
            parser->pos++;
            break;
        }
        if (ch == '/') {
            if (!starts_with(parser, parser->pos, "/>")) {
                record(parser, "xml-unclosed-start-tag", parser->pos);
                return -1;
            }
            self_closing = 1;
            parser->pos += 2;
            break;
        }
        if (!had_space) { /* attributes must be separated by whitespace */
            record(parser, "xml-unclosed-start-tag", parser->pos);
            return -1;
        }
        if (consume_attribute(parser, element, depth) < 0) {
            return -1;
        }
    }
    /* declarations are now in scope: validate the element and its attribute prefixes */
    if (check_qname_prefix(parser, name_start, name_end) < 0) {
        return -1;
    }
    for (Py_ssize_t index = 0; index < parser->attr_spans_len; index += 2) {
        Py_ssize_t attr_start = parser->attr_spans[index];
        Py_ssize_t attr_end = parser->attr_spans[index + 1];
        if (starts_with(parser, attr_start, "xmlns:") || name_equals(parser, attr_start, attr_end, "xmlns")) {
            continue; /* the declaration itself, not a prefix use */
        }
        if (check_qname_prefix(parser, attr_start, attr_end) < 0) {
            return -1;
        }
    }
    node_append(current(parser), element);
    if (parser->stack_len == 0) {
        parser->root = element;
    }
    if (self_closing) {
        pop_prefixes(parser, depth);
        return 0;
    }
    return push_open(parser, element);
}

/* Dispatch the construct at a '<'. */
static int consume_markup(xml_parser *parser) {
    if (starts_with(parser, parser->pos, "<!--")) {
        return consume_comment(parser);
    }
    if (starts_with(parser, parser->pos, "<![CDATA[")) {
        return consume_cdata(parser);
    }
    if (starts_with(parser, parser->pos, "<!DOCTYPE")) {
        return consume_doctype(parser);
    }
    if (parser->pos + 1 < parser->length && cp(parser, parser->pos + 1) == '!') {
        record(parser, "xml-unterminated-markup", parser->pos);
        return -1;
    }
    if (parser->pos + 1 < parser->length && cp(parser, parser->pos + 1) == '?') {
        return consume_pi(parser);
    }
    if (parser->pos + 1 < parser->length && cp(parser, parser->pos + 1) == '/') {
        return consume_end_tag(parser);
    }
    return consume_start_tag(parser);
}

th_tree *th_tree_parse_xml(int kind, const void *data, Py_ssize_t length) {
    th_tree *tree = th_tree_new();
    if (tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tree->can_span = 1; /* text nodes may span into the borrowed input */
    tree->kind = kind;
    tree->data = data;
    tree->length = length;
    th_node *document = node_new(tree, TH_NODE_DOCUMENT);
    if (document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    tree->document = document;

    xml_parser parser;
    memset(&parser, 0, sizeof(parser));
    parser.tree = tree;
    parser.kind = kind;
    parser.data = data;
    parser.length = length;
    parser.document = document;

    while (parser.pos < length) { /* a well-formedness error returns -1 and breaks */
        int failed = cp(&parser, parser.pos) == '<' ? consume_markup(&parser) : consume_text(&parser);
        if (failed < 0) {
            break;
        }
    }
    if (!parser.error) { /* an allocation failure leaves parser.error 0; the failed tree is discarded below */
        if (parser.stack_len > 0) {
            record(&parser, "xml-premature-eof", length);
        } else if (parser.root == NULL) {
            record(&parser, "xml-no-root-element", length);
        }
    }
    PyMem_Free(parser.stack);
    PyMem_Free(parser.ns);
    PyMem_Free(parser.attr_spans);
    PyMem_Free(parser.scratch);
    PyMem_Free(parser.names);
    PyMem_Free(parser.u8);
    if (tree->failed) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree;
}
