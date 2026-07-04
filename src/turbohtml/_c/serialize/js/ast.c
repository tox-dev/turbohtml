/* The arena AST shared by the JavaScript parser and printer: growable tables of
   nodes, symbols and scopes (freed in one shot with the program, like the document
   tree's arena), plus jm_dump, the canonical S-expression the parser tests diff
   against the way the XPath front end does. */

#include "serialize/js/internal.h"

#include <string.h>

/* ----------------------------------------------------------- growable tables */

static int jm_grow_nodes(jm_program *prog) {
    if (prog->node_count < prog->node_cap) {
        return 0;
    }
    int32_t cap = prog->node_cap ? prog->node_cap * 2 : 64;
    jm_node *grown = jm_realloc(prog->nodes, (size_t)cap * sizeof(jm_node));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    prog->nodes = grown;
    prog->node_cap = cap;
    return 0;
}

int32_t jm_node_new(jm_program *prog, jm_kind kind) {
    if (jm_grow_nodes(prog) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        prog->failed = 1;          /* GCOVR_EXCL_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    int32_t index = prog->node_count++;
    jm_node *node = &prog->nodes[index];
    node->kind = (uint8_t)kind;
    node->decl = 0;
    node->op = 0;
    node->flags = 0;
    node->sym = -1;
    node->a = node->b = node->c = node->d = -1;
    node->next = -1;
    node->str = NULL;
    node->str_len = 0;
    return index;
}

int32_t jm_scope_new(jm_program *prog, int32_t parent, uint8_t kind) {
    if (prog->scope_count >= prog->scope_cap) {
        int32_t cap = prog->scope_cap ? prog->scope_cap * 2 : 16;
        jm_scope *grown = jm_realloc(prog->scopes, (size_t)cap * sizeof(jm_scope));
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            prog->failed = 1; /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        prog->scopes = grown;
        prog->scope_cap = cap;
    }
    int32_t index = prog->scope_count++;
    jm_scope *scope = &prog->scopes[index];
    scope->parent = parent;
    scope->kind = kind;
    scope->poisoned = 0;
    scope->first_sym = -1;
    scope->first_child = -1;
    scope->next_sibling = -1;
    scope->first_stmt = -1;
    if (parent >= 0) {
        scope->next_sibling = prog->scopes[parent].first_child;
        prog->scopes[parent].first_child = index;
    }
    return index;
}

int32_t jm_sym_new(jm_program *prog, const Py_UCS4 *name, Py_ssize_t name_len, int32_t scope, uint8_t decl) {
    if (prog->sym_count >= prog->sym_cap) {
        int32_t cap = prog->sym_cap ? prog->sym_cap * 2 : 32;
        jm_sym *grown = jm_realloc(prog->syms, (size_t)cap * sizeof(jm_sym));
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            prog->failed = 1; /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        prog->syms = grown;
        prog->sym_cap = cap;
    }
    int32_t index = prog->sym_count++;
    jm_sym *sym = &prog->syms[index];
    sym->name = name;
    sym->name_len = name_len;
    sym->scope = scope;
    sym->scope_next = prog->scopes[scope].first_sym;
    prog->scopes[scope].first_sym = index;
    sym->decl = decl;
    sym->uses = 0;
    sym->slot = -1;
    sym->mangled = NULL;
    sym->mangled_len = 0;
    sym->refs = 0;
    sym->writes = 0;
    sym->ref_node = -1;
    sym->ref_scope = -1;
    sym->ref_prop = -1;
    sym->min_ref = INT32_MAX;
    sym->decl_node = -1;
    return index;
}

const Py_UCS4 *jm_program_own(jm_program *prog, const Py_UCS4 *buf, Py_ssize_t len) {
    if (prog->owned_count == prog->owned_cap) {
        int32_t cap = prog->owned_cap ? prog->owned_cap * 2 : 16;
        Py_UCS4 **grown = jm_realloc(prog->owned, (size_t)cap * sizeof(Py_UCS4 *));
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            prog->failed = 1; /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        prog->owned = grown;
        prog->owned_cap = cap;
    }
    Py_UCS4 *copy = jm_malloc((size_t)len * sizeof(Py_UCS4));
    if (copy == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        prog->failed = 1; /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    memcpy(copy, buf, (size_t)len * sizeof(Py_UCS4));
    prog->owned[prog->owned_count++] = copy;
    return copy;
}

/* ----------------------------------------------------------- string/identifier values */

/* The value of one hex digit. The lexer only forms a \x / \u escape over hex digits in valid input,
   so a non-hex code point here is malformed input outside the minifier's valid-JS contract; it
   decodes to a bounded (harmless) value rather than reading past the lexeme. */
static Py_UCS4 jm_hex_digit(Py_UCS4 ch) {
    return ch >= 'a' ? ch - ('a' - 10) : ch >= 'A' ? ch - ('A' - 10) : ch - '0';
}

const Py_UCS4 *jm_ident_value(jm_program *prog, const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_len) {
    Py_ssize_t escape = 0;
    while (escape < len && src[escape] != '\\') {
        escape++;
    }
    if (escape == len) { /* the common escape-free name: keep the zero-copy source span */
        *out_len = len;
        return src;
    }
    Py_UCS4 *buf = jm_malloc((size_t)len * sizeof(Py_UCS4)); /* decoding only ever shortens */
    if (buf == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        *out_len = len; /* GCOVR_EXCL_LINE */
        return src;     /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t write = 0;
    for (Py_ssize_t read = 0; read < len;) {
        Py_UCS4 ch = src[read++];
        if (ch != '\\') { /* an identifier escape is only ever \uXXXX or \u{...} */
            buf[write++] = ch;
            continue;
        }
        read++; /* the `u` */
        Py_UCS4 value = 0;
        /* GCOVR_EXCL_BR_START: the `read <` guards only stop a read past a truncated \u at the lexeme
           end -- malformed input outside the valid-JS contract, with no defined value to assert */
        if (read < len && src[read] == '{') {
            for (read++; read < len && src[read] != '}'; read++) {
                value = (value << 4) | jm_hex_digit(src[read]);
            }
            /* GCOVR_EXCL_BR_STOP */
            read++; /* the closing } (present in valid input) */
        } else {
            for (int digit = 0; digit < 4; digit++) {
                if (read >= len) { /* GCOVR_EXCL_BR_LINE: a truncated \uXXXX is malformed input only */
                    break;         /* GCOVR_EXCL_LINE */
                }
                value = (value << 4) | jm_hex_digit(src[read++]);
            }
        }
        buf[write++] = value;
    }
    const Py_UCS4 *owned = jm_program_own(prog, buf, write);
    jm_free(buf);
    if (owned == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        *out_len = len;  /* GCOVR_EXCL_LINE */
        return src;      /* GCOVR_EXCL_LINE */
    }
    *out_len = write;
    return owned;
}

Py_ssize_t jm_str_decode(const Py_UCS4 *lexeme, Py_ssize_t len, Py_UCS4 *out) {
    const Py_UCS4 *inner = lexeme + 1;
    Py_ssize_t inner_len = len - 2;
    Py_ssize_t write = 0;
    for (Py_ssize_t read = 0; read < inner_len;) {
        Py_UCS4 ch = inner[read++];
        if (ch != '\\') {
            out[write++] = ch;
            continue;
        }
        Py_UCS4 esc = inner[read++]; /* a trailing backslash reads the closing quote here, staying in bounds */
        switch (esc) {
        case 'n':
            out[write++] = 0x0A;
            break;
        case 't':
            out[write++] = 0x09;
            break;
        case 'r':
            out[write++] = 0x0D;
            break;
        case 'b':
            out[write++] = 0x08;
            break;
        case 'f':
            out[write++] = 0x0C;
            break;
        case 'v':
            out[write++] = 0x0B;
            break;
        case 'x': {
            Py_UCS4 value = 0;
            for (int digit = 0; digit < 2; digit++) {
                if (read >= inner_len) { /* GCOVR_EXCL_BR_LINE: a truncated \xHH is malformed input only */
                    break;               /* GCOVR_EXCL_LINE */
                }
                value = (value << 4) | jm_hex_digit(inner[read++]);
            }
            out[write++] = value;
            break;
        }
        case 'u': {
            Py_UCS4 value = 0;
            /* GCOVR_EXCL_BR_START: the `read <` guards only stop a read past a truncated \u at the lexeme
               end -- malformed input outside the valid-JS contract, with no defined value to assert */
            if (read < inner_len && inner[read] == '{') {
                for (read++; read < inner_len && inner[read] != '}'; read++) {
                    value = (value << 4) | jm_hex_digit(inner[read]);
                }
                /* GCOVR_EXCL_BR_STOP */
                read++; /* the closing } (present in valid input) */
            } else {
                for (int digit = 0; digit < 4; digit++) {
                    if (read >= inner_len) { /* GCOVR_EXCL_BR_LINE: a truncated \uXXXX is malformed input only */
                        break;               /* GCOVR_EXCL_LINE */
                    }
                    value = (value << 4) | jm_hex_digit(inner[read++]);
                }
            }
            out[write++] = value;
            break;
        }
        case '0':
        case '1':
        case '2':
        case '3':
        case '4':
        case '5':
        case '6':
        case '7': {
            /* a legacy octal escape: one to three octal digits, at most two once the first is 4-7
               (ECMA-262 Annex B.1.2), so a following digit can never silently extend it */
            Py_UCS4 value = esc - '0';
            int more = esc <= '3' ? 2 : 1;
            for (int digit = 0; digit < more && read < inner_len && inner[read] >= '0' && inner[read] <= '7'; digit++) {
                value = value * 8 + (inner[read++] - '0');
            }
            out[write++] = value;
            break;
        }
        case '\r': /* a `\`+<CR><LF> (or `\`+<CR>) LineContinuation contributes nothing */
            if (read < inner_len && inner[read] == '\n') {
                read++;
            }
            break;
        case '\n': /* an LF / LINE SEPARATOR / PARAGRAPH SEPARATOR LineContinuation contributes nothing */
        case 0x2028:
        case 0x2029:
            break;
        default:
            out[write++] = esc; /* \\ \' \" \` and any other escaped code point is itself */
            break;
        }
    }
    return write;
}

Py_ssize_t jm_str_encode(const Py_UCS4 *value, Py_ssize_t len, Py_UCS4 quote, Py_UCS4 *out) {
    static const char hex[] = "0123456789abcdef";
    Py_ssize_t write = 0;
    for (Py_ssize_t read = 0; read < len; read++) {
        Py_UCS4 ch = value[read];
        if (ch == quote || ch == '\\') {
            out[write++] = '\\';
            out[write++] = ch;
        } else if (ch == 0x0A) {
            out[write++] = '\\';
            out[write++] = 'n';
        } else if (ch == 0x0D) {
            out[write++] = '\\';
            out[write++] = 'r';
        } else if (ch == 0x09) {
            out[write++] = '\\';
            out[write++] = 't';
        } else if (ch == 0x08) {
            out[write++] = '\\';
            out[write++] = 'b';
        } else if (ch == 0x0C) {
            out[write++] = '\\';
            out[write++] = 'f';
        } else if (ch == 0x0B) {
            out[write++] = '\\';
            out[write++] = 'v';
        } else if (ch == 0x2028 || ch == 0x2029) { /* a line terminator stays escaped for a pre-ES2019 reader */
            out[write++] = '\\';
            out[write++] = 'u';
            out[write++] = '2';
            out[write++] = '0';
            out[write++] = '2';
            out[write++] = ch == 0x2028 ? '8' : '9';
        } else if (ch < 0x20) { /* any other control takes a fixed-length \xHH: it cannot absorb a digit */
            out[write++] = '\\';
            out[write++] = 'x';
            out[write++] = (Py_UCS4)(unsigned char)hex[(ch >> 4) & 0xF];
            out[write++] = (Py_UCS4)(unsigned char)hex[ch & 0xF];
        } else {
            out[write++] = ch;
        }
    }
    return write;
}

void jm_program_free(jm_program *prog) {
    if (prog == NULL) { /* GCOVR_EXCL_BR_LINE: callers always pass a live program */
        return;         /* GCOVR_EXCL_LINE */
    }
    for (int32_t index = 0; index < prog->sym_count; index++) {
        jm_free(prog->syms[index].mangled);
    }
    for (int32_t index = 0; index < prog->owned_count; index++) {
        jm_free(prog->owned[index]);
    }
    jm_free(prog->owned);
    jm_free(prog->comments);
    jm_free(prog->nodes);
    jm_free(prog->syms);
    jm_free(prog->scopes);
    jm_free(prog);
}

/* ----------------------------------------------------------- dump buffer */

typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} jm_sb;

static void sb_reserve(jm_sb *out, Py_ssize_t extra) {
    if (out->len + extra <= out->cap) {
        return;
    }
    Py_ssize_t cap = out->cap ? out->cap : 256;
    while (cap < out->len + extra) {
        cap *= 2;
    }
    Py_UCS4 *grown = jm_realloc(out->data, (size_t)cap * sizeof(Py_UCS4));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        out->failed = 1; /* GCOVR_EXCL_LINE */
        return;          /* GCOVR_EXCL_LINE */
    }
    out->data = grown;
    out->cap = cap;
}

static void sb_ascii(jm_sb *out, const char *str) {
    Py_ssize_t add = (Py_ssize_t)strlen(str);
    sb_reserve(out, add);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;        /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < add; index++) {
        out->data[out->len++] = (Py_UCS4)(unsigned char)str[index];
    }
}

static void sb_run(jm_sb *out, const Py_UCS4 *text, Py_ssize_t add) {
    sb_reserve(out, add);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return;        /* GCOVR_EXCL_LINE */
    }
    memcpy(out->data + out->len, text, (size_t)add * sizeof(Py_UCS4));
    out->len += add;
}

/* ----------------------------------------------------------- S-expression dump */

static const char *jm_kind_name(uint8_t kind);
static void dump_node(jm_sb *out, const jm_program *prog, int32_t index);

/* Dump a -1-terminated sibling chain wrapped in the given label, e.g. (body s1 s2). */
static void dump_chain(jm_sb *out, const jm_program *prog, const char *label, int32_t first) {
    sb_ascii(out, "(");
    sb_ascii(out, label);
    for (int32_t index = first; index >= 0; index = prog->nodes[index].next) {
        sb_ascii(out, " ");
        dump_node(out, prog, index);
    }
    sb_ascii(out, ")");
}

static void dump_child(jm_sb *out, const jm_program *prog, const char *label, int32_t index) {
    sb_ascii(out, "(");
    sb_ascii(out, label);
    if (index >= 0) {
        sb_ascii(out, " ");
        dump_node(out, prog, index);
    }
    sb_ascii(out, ")");
}

static void dump_node(jm_sb *out, const jm_program *prog, int32_t index) {
    /* dump_chain and dump_child both guard their links, and the root is always valid, so
       index is never negative here */
    const jm_node *node = &prog->nodes[index];
    sb_ascii(out, "(");
    sb_ascii(out, jm_kind_name(node->kind));
    if (node->str != NULL) {
        sb_ascii(out, " '");
        sb_run(out, node->str, node->str_len);
        sb_ascii(out, "'");
    }
    if (node->op != 0) {
        sb_ascii(out, " ");
        sb_ascii(out, jm_tok_name((jm_tok)node->op));
    }
    switch (node->kind) {
    case JN_PROGRAM:
    case JN_BLOCK:
        dump_chain(out, prog, "body", node->a);
        break;
    case JN_VAR: {
        const char *kw = node->decl == 0 ? "var" : node->decl == 1 ? "let" : "const";
        sb_ascii(out, " ");
        sb_ascii(out, kw);
        dump_chain(out, prog, "decls", node->a);
        break;
    }
    case JN_DECLR:
        dump_child(out, prog, "id", node->a);
        dump_child(out, prog, "init", node->b);
        break;
    case JN_IF:
        dump_child(out, prog, "test", node->a);
        dump_child(out, prog, "then", node->b);
        dump_child(out, prog, "else", node->c);
        break;
    case JN_FOR:
        dump_child(out, prog, "init", node->a);
        dump_child(out, prog, "test", node->b);
        dump_child(out, prog, "update", node->c);
        dump_child(out, prog, "body", node->d);
        break;
    case JN_FORIN:
    case JN_FOROF:
        dump_child(out, prog, "left", node->a);
        dump_child(out, prog, "right", node->b);
        dump_child(out, prog, "body", node->c);
        break;
    case JN_WHILE:
    case JN_WITH:
        dump_child(out, prog, "a", node->a);
        dump_child(out, prog, "b", node->b);
        break;
    case JN_DOWHILE:
        dump_child(out, prog, "body", node->a);
        dump_child(out, prog, "test", node->b);
        break;
    case JN_SWITCH:
        dump_child(out, prog, "disc", node->a);
        dump_chain(out, prog, "cases", node->b);
        break;
    case JN_CASE:
        dump_child(out, prog, "test", node->a);
        dump_chain(out, prog, "body", node->b);
        break;
    case JN_TRY:
        dump_child(out, prog, "block", node->a);
        dump_child(out, prog, "param", node->b);
        dump_child(out, prog, "catch", node->c);
        dump_child(out, prog, "finally", node->d);
        break;
    case JN_FUNC:
    case JN_ARROW:
        dump_chain(out, prog, "params", node->a);
        dump_child(out, prog, "body", node->b);
        break;
    case JN_CLASS:
        dump_child(out, prog, "super", node->a);
        dump_chain(out, prog, "members", node->b);
        break;
    case JN_MEMBER:
    case JN_PROP:
        dump_child(out, prog, "key", node->a);
        dump_child(out, prog, "value", node->b);
        break;
    case JN_LABEL:
        dump_child(out, prog, "stmt", node->a);
        break;
    case JN_EXPR_STMT:
    case JN_RETURN:
    case JN_THROW:
    case JN_UNARY:
    case JN_UPDATE:
    case JN_SPREAD:
    case JN_AWAIT:
    case JN_YIELD:
        dump_child(out, prog, "arg", node->a);
        break;
    case JN_MEMBER_EXPR:
        dump_child(out, prog, "obj", node->a);
        dump_child(out, prog, "prop", node->b);
        break;
    case JN_CALL:
    case JN_NEW:
        dump_child(out, prog, "callee", node->a);
        dump_chain(out, prog, "args", node->b);
        break;
    case JN_BINARY:
    case JN_LOGICAL:
    case JN_ASSIGN:
        dump_child(out, prog, "l", node->a);
        dump_child(out, prog, "r", node->b);
        break;
    case JN_COND:
        dump_child(out, prog, "test", node->a);
        dump_child(out, prog, "then", node->b);
        dump_child(out, prog, "else", node->c);
        break;
    case JN_SEQ:
        dump_chain(out, prog, "exprs", node->a);
        break;
    case JN_ARRAY:
        dump_chain(out, prog, "elems", node->a);
        break;
    case JN_OBJECT:
        dump_chain(out, prog, "props", node->a);
        break;
    case JN_TEMPLATE:
        dump_chain(out, prog, "parts", node->a);
        break;
    case JN_TAGGED:
        dump_child(out, prog, "tag", node->a);
        dump_child(out, prog, "quasi", node->b);
        break;
    default:
        break; /* leaf kinds (idents, literals, empty, debugger, holes) print only their head */
    }
    sb_ascii(out, ")");
}

Py_UCS4 *jm_dump(const jm_program *prog, Py_ssize_t *out_len) {
    jm_sb out = {NULL, 0, 0, 0};
    dump_node(&out, prog, prog->root);
    if (out.failed) {      /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        jm_free(out.data); /* GCOVR_EXCL_LINE */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    *out_len = out.len;
    return out.data;
}

static const char *jm_kind_name(uint8_t kind) {
    switch ((jm_kind)kind) { /* GCOVR_EXCL_BR_LINE: exhaustive over jm_kind; the default is unreachable */
    case JN_PROGRAM:
        return "program";
    case JN_BLOCK:
        return "block";
    case JN_EMPTY:
        return "empty";
    case JN_EXPR_STMT:
        return "expr";
    case JN_VAR:
        return "var";
    case JN_DECLR:
        return "declr";
    case JN_IF:
        return "if";
    case JN_FOR:
        return "for";
    case JN_FORIN:
        return "forin";
    case JN_FOROF:
        return "forof";
    case JN_WHILE:
        return "while";
    case JN_DOWHILE:
        return "dowhile";
    case JN_RETURN:
        return "return";
    case JN_THROW:
        return "throw";
    case JN_BREAK:
        return "break";
    case JN_CONTINUE:
        return "continue";
    case JN_SWITCH:
        return "switch";
    case JN_CASE:
        return "case";
    case JN_TRY:
        return "try";
    case JN_FUNC:
        return "func";
    case JN_CLASS:
        return "class";
    case JN_MEMBER:
        return "member";
    case JN_LABEL:
        return "label";
    case JN_WITH:
        return "with";
    case JN_DEBUGGER:
        return "debugger";
    case JN_IDENT:
        return "id";
    case JN_NUM:
        return "num";
    case JN_STRING:
        return "str";
    case JN_REGEX:
        return "regex";
    case JN_BIGINT:
        return "bigint";
    case JN_TEMPLATE:
        return "template";
    case JN_QUASI:
        return "quasi";
    case JN_TAGGED:
        return "tagged";
    case JN_ARRAY:
        return "array";
    case JN_HOLE:
        return "hole";
    case JN_OBJECT:
        return "object";
    case JN_PROP:
        return "prop";
    case JN_SPREAD:
        return "spread";
    case JN_MEMBER_EXPR:
        return "memberexpr";
    case JN_CALL:
        return "call";
    case JN_NEW:
        return "new";
    case JN_UNARY:
        return "unary";
    case JN_UPDATE:
        return "update";
    case JN_BINARY:
        return "binary";
    case JN_LOGICAL:
        return "logical";
    case JN_ASSIGN:
        return "assign";
    case JN_COND:
        return "cond";
    case JN_SEQ:
        return "seq";
    case JN_ARROW:
        return "arrow";
    case JN_YIELD:
        return "yield";
    case JN_AWAIT:
        return "await";
    }
    return "?"; /* GCOVR_EXCL_LINE: the switch is exhaustive over jm_kind */
}
