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

/* The Char production [2]: TAB/LF/CR or a scalar value outside the surrogate range
   and the U+FFFE/U+FFFF noncharacters. Every literal text, attribute, comment, CDATA
   and PI code point is held to it, as is a resolved numeric character reference. */
static int is_xml_char(long ch) {
    return ch == 0x9 || ch == 0xA || ch == 0xD || (ch >= 0x20 && ch <= 0xD7FF) || (ch >= 0xE000 && ch <= 0xFFFD) ||
           (ch >= 0x10000 && ch <= 0x10FFFF);
}

/* One in-scope namespace prefix declared by an ancestor's xmlns:prefix attribute,
   kept as arena-owned code points and popped when its declaring element closes. */
typedef struct {
    Py_UCS4 *prefix;
    Py_ssize_t prefix_len;
    Py_UCS4 *uri; /* the namespace name the prefix binds, for expanded-name comparison */
    Py_ssize_t uri_len;
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

/* The reserved namespace names Namespaces in XML 1.0 binds by definition to the xml and
   xmlns prefixes; neither may be re-bound and the xmlns name may not be bound at all. */
static const char XML_NS_URI[] = "http://www.w3.org/XML/1998/namespace";
static const char XMLNS_NS_URI[] = "http://www.w3.org/2000/xmlns/";

/* Whether the scratch buffer (a parsed, entity-normalized attribute value) equals `text`. */
static int scratch_equals(const xml_parser *parser, const char *text, Py_ssize_t text_len) {
    if (parser->scratch_len != text_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < text_len; index++) {
        if (parser->scratch[index] != (Py_UCS4)text[index]) {
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
        int hex = parser->pos < parser->length && cp(parser, parser->pos) == 'x'; /* [66]: the marker is lowercase */
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
/* Copy a code-point run (an entity-normalized attribute value) into the arena. */
static Py_UCS4 *arena_copy(th_tree *tree, const Py_UCS4 *src, Py_ssize_t len) {
    Py_UCS4 *out = arena_alloc(tree, len * (Py_ssize_t)sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(out, src, (size_t)len * sizeof(Py_UCS4));
    return out;
}

/* Declare a namespace prefix bound to `uri` (arena-owned copies) in scope at `depth`. */
static int declare_prefix(xml_parser *parser, Py_ssize_t start, Py_ssize_t len, const Py_UCS4 *uri, Py_ssize_t uri_len,
                          Py_ssize_t depth) {
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
    Py_UCS4 *owned_uri = arena_copy(parser->tree, uri, uri_len);
    if (owned_uri == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    parser->ns[parser->ns_len].prefix = owned;
    parser->ns[parser->ns_len].prefix_len = len;
    parser->ns[parser->ns_len].uri = owned_uri;
    parser->ns[parser->ns_len].uri_len = uri_len;
    parser->ns[parser->ns_len].depth = depth;
    parser->ns_len++;
    return 0;
}

static void pop_prefixes(xml_parser *parser, Py_ssize_t depth) {
    while (parser->ns_len > 0 && parser->ns[parser->ns_len - 1].depth >= depth) {
        parser->ns_len--;
    }
}

/* The index of the innermost in-scope declaration of prefix [start, start+len), or -1. */
static Py_ssize_t find_prefix(const xml_parser *parser, Py_ssize_t start, Py_ssize_t len) {
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
            return index;
        }
    }
    return -1;
}

/* Whether the prefix range [start, end) is the reserved "xml" or an in-scope
   declaration; the source position `at` locates an undeclared-prefix error. */
static int prefix_in_scope(xml_parser *parser, Py_ssize_t start, Py_ssize_t end, Py_ssize_t at) {
    if (name_equals(parser, start, end, "xml") || find_prefix(parser, start, end - start) >= 0) {
        return 1;
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
        if (!is_xml_char(ch)) {
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
        text_set_span(text, start);
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
        if (!is_xml_char(cp(parser, parser->pos))) {
            record(parser, "xml-invalid-char", parser->pos);
            return -1;
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
        if (!is_xml_char(cp(parser, parser->pos))) {
            record(parser, "xml-invalid-char", parser->pos);
            return -1;
        }
        parser->pos++;
    }
    record(parser, "xml-unterminated-cdata", open);
    return -1;
}

/* Whether the name range [start, end) is "xml" in any case: the reserved PITarget
   ([17]) that only the leading XML declaration, spelled in lowercase, may take. */
static int name_is_xml_ci(const xml_parser *parser, Py_ssize_t start, Py_ssize_t end) {
    static const char lower[] = "xml";
    if (end - start != 3) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < 3; index++) { /* per char, accept either case */
        Py_UCS4 ch = cp(parser, start + index);
        if (ch != (Py_UCS4)lower[index] && ch != (Py_UCS4)(lower[index] - 'a' + 'A')) {
            return 0;
        }
    }
    return 1;
}

/* A VersionNum ([26]) is '1.' followed by one or more digits. */
static int is_version_num(const xml_parser *parser, Py_ssize_t start, Py_ssize_t end) {
    if (end - start < 3 || cp(parser, start) != '1' || cp(parser, start + 1) != '.') {
        return 0;
    }
    for (Py_ssize_t index = start + 2; index < end; index++) {
        Py_UCS4 ch = cp(parser, index);
        if (ch < '0' || ch > '9') {
            return 0;
        }
    }
    return 1;
}

/* An EncName ([81]) is a letter followed by letters, digits, '.', '_' or '-'. */
static int is_enc_name(const xml_parser *parser, Py_ssize_t start, Py_ssize_t end) {
    Py_UCS4 first = start < end ? cp(parser, start) : 0;
    if (!((first >= 'A' && first <= 'Z') || (first >= 'a' && first <= 'z'))) {
        return 0;
    }
    for (Py_ssize_t index = start + 1; index < end; index++) {
        Py_UCS4 ch = cp(parser, index);
        int allowed = (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '.' ||
                      ch == '_' || ch == '-';
        if (!allowed) {
            return 0;
        }
    }
    return 1;
}

/* Read a pseudo-attribute value: Eq ([25], S? '=' S?) then a quoted run, leaving
   [*value_start, *value_end) on the literal and pos past the close quote. */
static int xml_decl_value(xml_parser *parser, Py_ssize_t *value_start, Py_ssize_t *value_end) {
    skip_space(parser);
    if (parser->pos >= parser->length || cp(parser, parser->pos) != '=') {
        record(parser, "xml-malformed-declaration", parser->pos);
        return -1;
    }
    parser->pos++; /* past '=' */
    skip_space(parser);
    if (parser->pos >= parser->length || (cp(parser, parser->pos) != '"' && cp(parser, parser->pos) != '\'')) {
        record(parser, "xml-malformed-declaration", parser->pos);
        return -1;
    }
    Py_UCS4 quote = cp(parser, parser->pos);
    parser->pos++; /* past the opening quote */
    *value_start = parser->pos;
    while (parser->pos < parser->length && cp(parser, parser->pos) != quote) {
        parser->pos++;
    }
    if (parser->pos >= parser->length) {
        record(parser, "xml-malformed-declaration", *value_start);
        return -1;
    }
    *value_end = parser->pos;
    parser->pos++; /* past the closing quote */
    return 0;
}

/* Validate the XML declaration ([23]) after its 'xml' target: VersionInfo, then an
   optional EncodingDecl and SDDecl in that order, then '?>'. It configures the
   parse and is not a tree node. Position is just past "xml"; returns 0 or -1. */
static int consume_xml_decl(xml_parser *parser) {
    Py_ssize_t before_version = parser->pos;
    skip_space(parser);
    if (parser->pos == before_version || !starts_with(parser, parser->pos, "version")) {
        record(parser, "xml-malformed-declaration", parser->pos);
        return -1;
    }
    parser->pos += 7; /* past "version" */
    Py_ssize_t version_start;
    Py_ssize_t version_end;
    if (xml_decl_value(parser, &version_start, &version_end) < 0) {
        return -1;
    }
    if (!is_version_num(parser, version_start, version_end)) {
        record(parser, "xml-malformed-declaration", version_start);
        return -1;
    }
    int seen_encoding = 0;
    int seen_standalone = 0;
    for (;;) {
        Py_ssize_t before_attr = parser->pos;
        skip_space(parser);
        if (starts_with(parser, parser->pos, "?>")) {
            parser->pos += 2; /* past "?>" */
            return 0;
        }
        if (parser->pos == before_attr) { /* pseudo-attributes are separated by S */
            record(parser, "xml-malformed-declaration", parser->pos);
            return -1;
        }
        Py_ssize_t value_start;
        Py_ssize_t value_end;
        if (!seen_encoding && !seen_standalone && starts_with(parser, parser->pos, "encoding")) {
            parser->pos += 8; /* past "encoding" */
            if (xml_decl_value(parser, &value_start, &value_end) < 0) {
                return -1;
            }
            if (!is_enc_name(parser, value_start, value_end)) {
                record(parser, "xml-malformed-declaration", value_start);
                return -1;
            }
            seen_encoding = 1;
        } else if (!seen_standalone && starts_with(parser, parser->pos, "standalone")) {
            parser->pos += 10; /* past "standalone" */
            if (xml_decl_value(parser, &value_start, &value_end) < 0) {
                return -1;
            }
            if (!name_equals(parser, value_start, value_end, "yes") &&
                !name_equals(parser, value_start, value_end, "no")) {
                record(parser, "xml-malformed-declaration", value_start);
                return -1;
            }
            seen_standalone = 1;
        } else {
            record(parser, "xml-malformed-declaration", parser->pos);
            return -1;
        }
    }
}

static int consume_pi(xml_parser *parser) {
    Py_ssize_t open = parser->pos;
    parser->pos += 2; /* past "<?" */
    Py_ssize_t target_start = parser->pos;
    Py_ssize_t target_end = read_name(parser);
    if (target_end < 0) {
        return -1;
    }
    if (name_is_xml_ci(parser, target_start, target_end)) {
        if (open == 0 && name_equals(parser, target_start, target_end, "xml")) {
            return consume_xml_decl(parser); /* the leading, lowercase declaration */
        }
        record(parser, "xml-reserved-pi-target", target_start);
        return -1;
    }
    for (Py_ssize_t index = target_start; index < target_end; index++) { /* a PI target is an NCName: no ':' */
        if (cp(parser, index) == ':') {
            record(parser, "xml-invalid-pi-target", target_start);
            return -1;
        }
    }
    if (parser->pos < parser->length && !starts_with(parser, parser->pos, "?>") &&
        !xml_is_space(cp(parser, parser->pos))) { /* [16]: target and data are S-separated */
        record(parser, "xml-malformed-pi", parser->pos);
        return -1;
    }
    Py_ssize_t data_start = parser->pos;
    while (parser->pos < parser->length && !starts_with(parser, parser->pos, "?>")) {
        if (!is_xml_char(cp(parser, parser->pos))) {
            record(parser, "xml-invalid-char", parser->pos);
            return -1;
        }
        parser->pos++;
    }
    if (parser->pos >= parser->length) {
        record(parser, "xml-unterminated-pi", open);
        return -1;
    }
    Py_ssize_t data_end = parser->pos;
    parser->pos += 2; /* past "?>" */
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

/* Enforce Namespaces in XML 1.0 on an xmlns / xmlns:prefix attribute whose value is in the
   scratch buffer, declaring a well-formed prefix binding. Returns 0 for an ordinary attribute
   or a valid declaration, -1 with an error recorded for a reserved-binding violation. */
static int consume_namespace_decl(xml_parser *parser, Py_ssize_t name_start, Py_ssize_t name_end, Py_ssize_t depth) {
    int is_default = name_equals(parser, name_start, name_end, "xmlns");
    if (!is_default && !starts_with(parser, name_start, "xmlns:")) {
        return 0;
    }
    int binds_xml_uri = scratch_equals(parser, XML_NS_URI, (Py_ssize_t)(sizeof(XML_NS_URI) - 1));
    int binds_xmlns_uri = scratch_equals(parser, XMLNS_NS_URI, (Py_ssize_t)(sizeof(XMLNS_NS_URI) - 1));
    if (is_default) { /* a reserved name may not be bound as the default namespace */
        if (binds_xml_uri || binds_xmlns_uri) {
            record(parser, "xml-reserved-namespace", name_start);
            return -1;
        }
        return 0;
    }
    Py_ssize_t prefix_start = name_start + 6;
    Py_ssize_t prefix_len = name_end - prefix_start;
    if (prefix_len == 0) { /* xmlns:="..." -- an empty prefix is not an NCName */
        record(parser, "xml-invalid-namespace-decl", name_start);
        return -1;
    }
    if (name_equals(parser, prefix_start, name_end, "xmlns")) { /* the xmlns prefix is never declarable */
        record(parser, "xml-reserved-prefix", prefix_start);
        return -1;
    }
    if (name_equals(parser, prefix_start, name_end, "xml")) { /* xml binds only to the xml namespace */
        if (!binds_xml_uri) {
            record(parser, "xml-reserved-prefix", prefix_start);
            return -1;
        }
    } else if (binds_xml_uri) { /* no other prefix may bind the xml namespace */
        record(parser, "xml-reserved-namespace", prefix_start);
        return -1;
    }
    if (binds_xmlns_uri) { /* the xmlns namespace may not be bound to any prefix */
        record(parser, "xml-reserved-namespace", prefix_start);
        return -1;
    }
    int declared = declare_prefix(parser, prefix_start, prefix_len, parser->scratch, parser->scratch_len, depth);
    if (declared < 0) { /* GCOVR_EXCL_BR_LINE: declare_prefix only fails on allocation */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
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
        if (!is_xml_char(ch)) {
            record(parser, "xml-invalid-char", parser->pos);
            return -1;
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
    return consume_namespace_decl(parser, name_start, name_end, depth);
}

/* The in-scope declaration index for the prefix of attribute [start, end), with *colon set to
   the ':' offset; -1 when no namespace applies (unprefixed, the xml prefix, or an xmlns decl).
   A declared prefix is guaranteed in scope: check_qname_prefix has already validated it. */
static Py_ssize_t attr_ns_index(const xml_parser *parser, Py_ssize_t start, Py_ssize_t end, Py_ssize_t *colon) {
    for (Py_ssize_t index = start; index < end; index++) {
        if (cp(parser, index) == ':') {
            *colon = index;
            if (name_equals(parser, start, index, "xml") || name_equals(parser, start, index, "xmlns")) {
                return -1;
            }
            return find_prefix(parser, start, index - start);
        }
    }
    return -1;
}

/* Whether the local names [a, a+len) and [b, b+len) are equal code point for code point. */
static int local_names_equal(const xml_parser *parser, Py_ssize_t a, Py_ssize_t b, Py_ssize_t len) {
    for (Py_ssize_t offset = 0; offset < len; offset++) {
        if (cp(parser, a + offset) != cp(parser, b + offset)) {
            return 0;
        }
    }
    return 1;
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
    /* expanded-name uniqueness: no two attributes may share a local name and namespace URI,
       even through different prefixes bound to the same URI (Namespaces in XML 1.0 5.3) */
    for (Py_ssize_t index = 0; index < parser->attr_spans_len; index += 2) {
        Py_ssize_t colon = 0;
        Py_ssize_t decl = attr_ns_index(parser, parser->attr_spans[index], parser->attr_spans[index + 1], &colon);
        if (decl < 0) {
            continue;
        }
        Py_ssize_t local_start = colon + 1;
        Py_ssize_t local_len = parser->attr_spans[index + 1] - local_start;
        const xml_nsdecl *decl_ns = &parser->ns[decl];
        for (Py_ssize_t other = 0; other < index; other += 2) {
            Py_ssize_t other_colon = 0;
            Py_ssize_t other_decl =
                attr_ns_index(parser, parser->attr_spans[other], parser->attr_spans[other + 1], &other_colon);
            if (other_decl < 0) {
                continue;
            }
            Py_ssize_t other_local_len = parser->attr_spans[other + 1] - (other_colon + 1);
            const xml_nsdecl *other_ns = &parser->ns[other_decl];
            int same_uri = decl_ns->uri_len == other_ns->uri_len &&
                           memcmp(decl_ns->uri, other_ns->uri, (size_t)decl_ns->uri_len * sizeof(Py_UCS4)) == 0;
            if (same_uri && local_len == other_local_len &&
                local_names_equal(parser, local_start, other_colon + 1, local_len)) {
                record(parser, "xml-duplicate-attribute", parser->attr_spans[index]);
                return -1;
            }
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
