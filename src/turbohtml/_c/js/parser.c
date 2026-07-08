/* Recursive-descent parser for the ECMAScript grammar, building the arena AST.

   It keeps the lexer's single-current-token model (jm_lex_next leaves the current
   token in lx.kind) and resolves the two grammar-driven lexer ambiguities from here:
   a `/` in operand position is re-read as a regex (jm_lex_rescan_regex), and a `}`
   that closes a `${...}` is re-read as a template continuation (jm_lex_rescan_template).
   The arrow-function cover grammar is handled by backtracking - the lexer state is a
   plain value, so a speculative scan can peek for `=>` and rewind.

   Any construct the parser does not handle reports an error; jm_parse then returns
   NULL and the caller emits the original source unchanged, so partial coverage can
   never corrupt a script. */

#include "js/internal.h"

#include <stdio.h>
#include <string.h>

typedef struct {
    jm_lexer lx;
    jm_program *prog;
    int err;
    int32_t depth; /* current parse-recursion depth; a nesting cap stops a stack overflow (#421) */
    char *errbuf;
    size_t errlen;
} P;

/* The recursion/nesting a script may reach before the parser rejects it as too deep, chosen so the
   post-parse fold and print walks (bounded by this AST height) stay well inside the C stack -- even a
   512 KiB secondary-thread stack -- while clearing the deepest generated expressions seen in practice
   (hundreds of operands). The point is to fail cleanly on a pathological input rather than fault. */
enum { JM_MAX_DEPTH = 1000 };

/* Report the first error: the construct that failed, the byte offset, and the offending token slice
   (or end-of-input), so a diagnostic names what and where rather than an offset alone (#434). */
static void fail(P *parser, const char *message) {
    if (parser->err) {
        return;
    }
    parser->err = 1;
    if (parser->errlen == 0) { /* errlen==0 is the no-message opt-out the HTML inline-<script> path uses */
        return;
    }
    Py_ssize_t start = parser->lx.start;
    if (parser->lx.kind == JT_EOF) {
        snprintf(parser->errbuf, parser->errlen, "%s at offset %zd: reached end of input", message, (Py_ssize_t)start);
        return;
    }
    char slice[16];
    Py_ssize_t width = 0;
    for (Py_ssize_t index = start; index < parser->lx.pos && width + 1 < (Py_ssize_t)sizeof(slice); index++) {
        Py_UCS4 ch = parser->lx.src[index];
        slice[width++] = ch >= 0x20 && ch < 0x7f ? (char)ch : '?'; /* keep the message ASCII and one line */
    }
    slice[width] = '\0';
    snprintf(parser->errbuf, parser->errlen, "%s at offset %zd near '%s'", message, (Py_ssize_t)start, slice);
}

/* Enter one level of parse recursion; returns 0 (after flagging a clean error) when the nesting cap
   is reached, so the caller unwinds instead of overflowing the stack. Each enter pairs with a leave. */
static int enter(P *parser) {
    if (parser->depth >= JM_MAX_DEPTH) {
        fail(parser, "nested too deeply");
        return 0;
    }
    parser->depth++;
    return 1;
}

static void leave(P *parser) {
    parser->depth--;
}

/* Advance to the next token; a lexer error becomes a parser error. */
static void advance(P *parser) {
    jm_lex_next(&parser->lx);
    if (parser->lx.kind == JT_ERROR) {
        fail(parser, "lexical error");
    }
}

static int at(const P *parser, jm_tok kind) {
    return parser->lx.kind == kind;
}

static int kw(const P *parser, const char *word) {
    return parser->lx.kind == JT_IDENT && jm_text_eq(&parser->lx, word);
}

static int eat(P *parser, jm_tok kind) {
    if (parser->lx.kind == kind) {
        advance(parser);
        return 1;
    }
    return 0;
}

static void expect(P *parser, jm_tok kind, const char *message) {
    if (!eat(parser, kind)) {
        fail(parser, message);
    }
}

/* A speculative-parse checkpoint. Backtracking must restore the error flag as well
   as the lexer position: a `/` lexed as division during a lookahead can hit a `\`
   and set parser->err via advance(), and that speculative error must not leak into the
   real parse. */
typedef struct {
    jm_lexer lx;
    int err;
} jm_mark;

static jm_mark mark(const P *parser) {
    jm_mark saved = {parser->lx, parser->err};
    return saved;
}

static void reset(P *parser, jm_mark saved) {
    parser->lx = saved.lx;
    parser->err = saved.err;
}

static int32_t parse_stmt(P *parser);
static int32_t parse_stmt_body(P *parser);
static int32_t parse_block(P *parser);
static int32_t parse_assign(P *parser, int no_in);
static int32_t parse_assign_body(P *parser, int no_in);
static int32_t parse_expr(P *parser, int no_in);

/* parse_stmt and parse_assign are the two recursion hubs every nested statement and expression passes
   through, so metering depth at their entry (paired with the operator/chain-loop caps below) bounds the
   whole parse; the _body functions hold the grammar, these thin wrappers hold the depth accounting. */
static int32_t parse_stmt(P *parser) {
    if (!enter(parser)) {
        return -1;
    }
    int32_t result = parse_stmt_body(parser);
    leave(parser);
    return result;
}

static int32_t parse_assign(P *parser, int no_in) {
    if (!enter(parser)) {
        return -1;
    }
    int32_t result = parse_assign_body(parser, no_in);
    leave(parser);
    return result;
}
static int32_t parse_binary(P *parser, int min_prec, int no_in);
static int32_t parse_binary_base(P *parser);
static int32_t parse_unary(P *parser);
static int32_t parse_call_member(P *parser);
static int32_t parse_primary(P *parser);
static int32_t parse_function(P *parser, int is_expr, int is_async);
static int32_t parse_class(P *parser, int is_expr);
static void parse_params(P *parser, int32_t fn);

/* Store a child index into a node's a/b/c/d slot. Written as calls, never a direct
   `prog->nodes[node].slot = parse_x(...)`, because the right-hand parse can append a node
   and grow (realloc) prog->nodes: as a function argument the parse is fully sequenced before
   the slot address is taken, so a moved buffer can never leave the store writing freed memory. */
static void set_a(P *parser, int32_t node, int32_t child) {
    parser->prog->nodes[node].a = child;
}
static void set_b(P *parser, int32_t node, int32_t child) {
    parser->prog->nodes[node].b = child;
}
static void set_c(P *parser, int32_t node, int32_t child) {
    parser->prog->nodes[node].c = child;
}
static void set_d(P *parser, int32_t node, int32_t child) {
    parser->prog->nodes[node].d = child;
}

/* A spread/rest element `...expr`, shared by params, arrays and call arguments. */
static int32_t parse_spread(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_SPREAD);
    advance(parser);
    set_a(parser, node, parse_assign(parser, 0));
    return node;
}

/* Record the current identifier token's text on a node as its decoded StringValue, so an escaped
   spelling (`abc`) binds and resolves as its plain form (ECMA-262 §12.7); every consumer -- the
   scope binder, the folder and the printer -- then sees the one canonical name. */
static void set_ident(P *parser, int32_t node) {
    Py_ssize_t len = 0;
    parser->prog->nodes[node].str = jm_ident_value(parser->prog, parser->lx.text, parser->lx.text_len, &len);
    parser->prog->nodes[node].str_len = len;
}

/* A node with one borrowed-text span (identifier/literal/template chunk). */
static int32_t leaf(P *parser, jm_kind kind) {
    int32_t index = jm_node_new(parser->prog, kind);
    if (index < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;   /* GCOVR_EXCL_LINE */
    }
    if (kind == JN_IDENT) {
        set_ident(parser, index);
    } else {
        parser->prog->nodes[index].str = parser->lx.text;
        parser->prog->nodes[index].str_len = parser->lx.text_len;
    }
    advance(parser);
    return index;
}

/* Consume a statement terminator: an explicit `;`, or an ASI boundary (a `}`, EOF,
   or a preceding line break). The parser is lenient - it never rejects a missing
   semicolon - because the input is already valid and the printer re-inserts them. */
static void semicolon(P *parser) {
    if (eat(parser, JT_SEMI)) {
        return;
    }
    /* ASI: a close brace, end of input, or a newline ends the statement. */
}

static int32_t parse_var(P *parser, int no_in) {
    int32_t node = jm_node_new(parser->prog, JN_VAR);
    if (node < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;  /* GCOVR_EXCL_LINE */
    }
    parser->prog->nodes[node].decl = kw(parser, "const") ? 2 : kw(parser, "let") ? 1 : 0;
    advance(parser); /* var / let / const */
    int32_t tail = -1;
    for (;;) {
        int32_t declr = jm_node_new(parser->prog, JN_DECLR);
        if (declr < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return -1;   /* GCOVR_EXCL_LINE */
        }
        int32_t target = parse_primary(parser); /* an identifier or a destructuring pattern */
        if (parser->err) {
            return -1;
        }
        set_a(parser, declr, target);
        if (eat(parser, JT_ASSIGN)) {
            set_b(parser, declr, parse_assign(parser, no_in));
            if (parser->err) {
                return -1;
            }
        }
        if (tail < 0) {
            set_a(parser, node, declr);
        } else {
            parser->prog->nodes[tail].next = declr;
        }
        tail = declr;
        if (!eat(parser, JT_COMMA)) {
            break;
        }
    }
    return node;
}

static int32_t parse_if(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_IF);
    advance(parser); /* if */
    expect(parser, JT_LPAREN, "expected (");
    set_a(parser, node, parse_expr(parser, 0));
    expect(parser, JT_RPAREN, "expected )");
    if (parser->err) {
        return -1;
    }
    set_b(parser, node, parse_stmt(parser));
    if (parser->err) {
        return -1;
    }
    if (kw(parser, "else")) {
        advance(parser);
        set_c(parser, node, parse_stmt(parser));
    }
    return parser->err ? -1 : node;
}

static int32_t parse_for(P *parser) {
    advance(parser); /* for */
    int is_await = 0;
    if (kw(parser, "await")) {
        is_await = 1;
        advance(parser);
    }
    expect(parser, JT_LPAREN, "expected (");
    if (parser->err) {
        return -1;
    }
    /* The init clause: a declaration, an expression, or empty. `in`/`of` after it
       turns the loop into for-in / for-of. */
    int32_t init = -1;
    if (at(parser, JT_SEMI)) {
        /* empty init */
    } else if (kw(parser, "var") || kw(parser, "let") || kw(parser, "const")) {
        init = parse_var(parser, 1);
    } else {
        init = parse_expr(parser, 1);
    }
    if (parser->err) {
        return -1;
    }
    if (kw(parser, "in") || kw(parser, "of")) {
        int is_of = kw(parser, "of");
        int32_t node = jm_node_new(parser->prog, is_of ? JN_FOROF : JN_FORIN);
        if (is_await) {
            parser->prog->nodes[node].flags |= JN_F_AWAIT;
        }
        advance(parser); /* in / of */
        set_a(parser, node, init);
        set_b(parser, node, is_of ? parse_assign(parser, 0) : parse_expr(parser, 0));
        expect(parser, JT_RPAREN, "expected )");
        if (parser->err) {
            return -1;
        }
        set_c(parser, node, parse_stmt(parser));
        return parser->err ? -1 : node;
    }
    int32_t node = jm_node_new(parser->prog, JN_FOR);
    set_a(parser, node, init);
    expect(parser, JT_SEMI, "expected ;");
    if (!at(parser, JT_SEMI)) {
        set_b(parser, node, parse_expr(parser, 0));
    }
    expect(parser, JT_SEMI, "expected ;");
    if (!at(parser, JT_RPAREN)) {
        set_c(parser, node, parse_expr(parser, 0));
    }
    expect(parser, JT_RPAREN, "expected )");
    if (parser->err) {
        return -1;
    }
    set_d(parser, node, parse_stmt(parser));
    return parser->err ? -1 : node;
}

static int32_t parse_while(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_WHILE);
    advance(parser);
    expect(parser, JT_LPAREN, "expected (");
    set_a(parser, node, parse_expr(parser, 0));
    expect(parser, JT_RPAREN, "expected )");
    if (parser->err) {
        return -1;
    }
    set_b(parser, node, parse_stmt(parser));
    return parser->err ? -1 : node;
}

static int32_t parse_do(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_DOWHILE);
    advance(parser);
    set_a(parser, node, parse_stmt(parser));
    if (parser->err) {
        return -1;
    }
    if (!kw(parser, "while")) {
        fail(parser, "expected while");
        return -1;
    }
    advance(parser);
    expect(parser, JT_LPAREN, "expected (");
    set_b(parser, node, parse_expr(parser, 0));
    expect(parser, JT_RPAREN, "expected )");
    semicolon(parser);
    return parser->err ? -1 : node;
}

static int32_t parse_switch(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_SWITCH);
    advance(parser);
    expect(parser, JT_LPAREN, "expected (");
    set_a(parser, node, parse_expr(parser, 0));
    expect(parser, JT_RPAREN, "expected )");
    expect(parser, JT_LBRACE, "expected {");
    if (parser->err) {
        return -1;
    }
    int32_t tail = -1;
    while (!at(parser, JT_RBRACE) && !at(parser, JT_EOF)) {
        int32_t clause = jm_node_new(parser->prog, JN_CASE);
        if (kw(parser, "case")) {
            advance(parser);
            set_a(parser, clause, parse_expr(parser, 0));
        } else if (kw(parser, "default")) {
            advance(parser);
        } else {
            fail(parser, "expected case or default");
            return -1;
        }
        expect(parser, JT_COLON, "expected :");
        if (parser->err) {
            return -1;
        }
        int32_t stail = -1;
        while (!at(parser, JT_RBRACE) && !kw(parser, "case") && !kw(parser, "default") && !at(parser, JT_EOF)) {
            int32_t stmt = parse_stmt(parser);
            if (parser->err) {
                return -1;
            }
            if (stail < 0) {
                set_b(parser, clause, stmt);
            } else {
                parser->prog->nodes[stail].next = stmt;
            }
            stail = stmt;
        }
        if (tail < 0) {
            set_b(parser, node, clause);
        } else {
            parser->prog->nodes[tail].next = clause;
        }
        tail = clause;
    }
    expect(parser, JT_RBRACE, "expected }");
    return parser->err ? -1 : node;
}

static int32_t parse_try(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_TRY);
    advance(parser);
    set_a(parser, node, parse_block(parser));
    if (parser->err) {
        return -1;
    }
    if (kw(parser, "catch")) {
        advance(parser);
        if (eat(parser, JT_LPAREN)) {
            set_b(parser, node, parse_primary(parser)); /* catch binding */
            expect(parser, JT_RPAREN, "expected )");
        }
        set_c(parser, node, parse_block(parser));
        if (parser->err) {
            return -1;
        }
    }
    if (kw(parser, "finally")) {
        advance(parser);
        set_d(parser, node, parse_block(parser));
    }
    return parser->err ? -1 : node;
}

static int32_t parse_return_throw(P *parser, jm_kind kind) {
    int32_t node = jm_node_new(parser->prog, kind);
    advance(parser);
    /* a restricted production: no argument when a line break or terminator follows */
    if (kind == JN_RETURN &&
        (at(parser, JT_SEMI) || at(parser, JT_RBRACE) || at(parser, JT_EOF) || parser->lx.newline_before)) {
        semicolon(parser);
        return node;
    }
    set_a(parser, node, parse_expr(parser, 0));
    semicolon(parser);
    return parser->err ? -1 : node;
}

static int32_t parse_break_continue(P *parser, jm_kind kind) {
    int32_t node = jm_node_new(parser->prog, kind);
    advance(parser);
    if (at(parser, JT_IDENT) && !parser->lx.newline_before) {
        parser->prog->nodes[node].str = parser->lx.text;
        parser->prog->nodes[node].str_len = parser->lx.text_len;
        advance(parser);
    }
    semicolon(parser);
    return parser->err ? -1 : node;
}

static int32_t parse_block(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_BLOCK);
    expect(parser, JT_LBRACE, "expected {");
    if (parser->err) {
        return -1;
    }
    int32_t tail = -1;
    while (!at(parser, JT_RBRACE) && !at(parser, JT_EOF)) {
        int32_t stmt = parse_stmt(parser);
        if (parser->err) {
            return -1;
        }
        if (tail < 0) {
            set_a(parser, node, stmt);
        } else {
            parser->prog->nodes[tail].next = stmt;
        }
        tail = stmt;
    }
    expect(parser, JT_RBRACE, "expected }");
    return parser->err ? -1 : node;
}

/* A bare `{` is a block; an expression statement never starts with one (an object
   literal in statement position is parenthesized). */
static int32_t parse_stmt_body(P *parser) {
    if (parser->err) { /* GCOVR_EXCL_BR_LINE: callers guard against re-entry on error */
        return -1;     /* GCOVR_EXCL_LINE: callers guard, but keep the recursion safe */
    }
    if (at(parser, JT_LBRACE)) {
        return parse_block(parser);
    }
    if (at(parser, JT_SEMI)) {
        int32_t node = jm_node_new(parser->prog, JN_EMPTY);
        advance(parser);
        return node;
    }
    if (kw(parser, "var") || kw(parser, "let") || kw(parser, "const")) {
        int32_t node = parse_var(parser, 0);
        semicolon(parser);
        return parser->err ? -1 : node;
    }
    if (kw(parser, "if")) {
        return parse_if(parser);
    }
    if (kw(parser, "for")) {
        return parse_for(parser);
    }
    if (kw(parser, "while")) {
        return parse_while(parser);
    }
    if (kw(parser, "do")) {
        return parse_do(parser);
    }
    if (kw(parser, "switch")) {
        return parse_switch(parser);
    }
    if (kw(parser, "try")) {
        return parse_try(parser);
    }
    if (kw(parser, "return")) {
        return parse_return_throw(parser, JN_RETURN);
    }
    if (kw(parser, "throw")) {
        return parse_return_throw(parser, JN_THROW);
    }
    if (kw(parser, "break")) {
        return parse_break_continue(parser, JN_BREAK);
    }
    if (kw(parser, "continue")) {
        return parse_break_continue(parser, JN_CONTINUE);
    }
    if (kw(parser, "function")) {
        return parse_function(parser, 0, 0);
    }
    if (kw(parser, "async")) { /* an async function declaration, distinct from an async expression */
        jm_mark save = mark(parser);
        advance(parser);
        int is_decl = kw(parser, "function") && !parser->lx.newline_before;
        reset(parser, save);
        if (is_decl) {
            return parse_function(parser, 0, 1);
        }
    }
    if (kw(parser, "class")) {
        return parse_class(parser, 0);
    }
    if (kw(parser, "with")) {
        int32_t node = jm_node_new(parser->prog, JN_WITH);
        advance(parser);
        expect(parser, JT_LPAREN, "expected (");
        set_a(parser, node, parse_expr(parser, 0));
        expect(parser, JT_RPAREN, "expected )");
        if (parser->err) {
            return -1;
        }
        set_b(parser, node, parse_stmt(parser));
        return parser->err ? -1 : node;
    }
    if (kw(parser, "debugger")) {
        int32_t node = jm_node_new(parser->prog, JN_DEBUGGER);
        advance(parser);
        semicolon(parser);
        return node;
    }
    if (kw(parser, "import") || kw(parser, "export")) {
        fail(parser, "module syntax unsupported"); /* falls back to the source verbatim */
        return -1;
    }
    /* a labeled statement: IDENT ':' stmt - disambiguated by backtracking */
    if (at(parser, JT_IDENT)) {
        jm_mark save = mark(parser);
        const Py_UCS4 *label = parser->lx.text;
        Py_ssize_t label_len = parser->lx.text_len;
        advance(parser);
        if (at(parser, JT_COLON)) {
            advance(parser);
            int32_t node = jm_node_new(parser->prog, JN_LABEL);
            parser->prog->nodes[node].str = label;
            parser->prog->nodes[node].str_len = label_len;
            set_a(parser, node, parse_stmt(parser));
            return parser->err ? -1 : node;
        }
        reset(parser, save); /* not a label: rewind and parse as an expression statement */
    }
    int32_t node = jm_node_new(parser->prog, JN_EXPR_STMT);
    set_a(parser, node, parse_expr(parser, 0));
    semicolon(parser);
    return parser->err ? -1 : node;
}

/* Binding power of a binary/logical operator in operator position, or 0 if the
   current token is not one. *logical reports whether it is &&/||/??. no_in masks the
   `in` operator while parsing a for-loop init. */
static int binary_prec(P *parser, int no_in, int *logical, int *is_in_or_instanceof) {
    *logical = 0;
    *is_in_or_instanceof = 0;
    switch (parser->lx.kind) {
    case JT_NULLISH:
        *logical = 1;
        return 1;
    case JT_OR:
        *logical = 1;
        return 2;
    case JT_AND:
        *logical = 1;
        return 3;
    case JT_BIT_OR:
        return 4;
    case JT_BIT_XOR:
        return 5;
    case JT_BIT_AND:
        return 6;
    case JT_EQ_EQ:
    case JT_NE:
    case JT_EQ_EQ_EQ:
    case JT_NE_EQ:
        return 7;
    case JT_LT:
    case JT_GT:
    case JT_LE:
    case JT_GE:
        return 8;
    case JT_SHL:
    case JT_SHR:
    case JT_USHR:
        return 9;
    case JT_PLUS:
    case JT_MINUS:
        return 10;
    case JT_STAR:
    case JT_DIV:
    case JT_MOD:
        return 11;
    case JT_POW:
        return 12;
    case JT_IDENT:
        if (jm_text_eq(&parser->lx, "instanceof")) {
            *is_in_or_instanceof = 1;
            return 8;
        }
        if (!no_in && jm_text_eq(&parser->lx, "in")) {
            *is_in_or_instanceof = 1;
            return 8;
        }
        return 0;
    default:
        return 0;
    }
}

static int32_t parse_binary(P *parser, int min_prec, int no_in) {
    int32_t left = parse_unary(parser);
    if (parser->err) {
        return -1;
    }
    int32_t built = 0; /* each loop turn deepens the left-leaning spine by one, so it counts as nesting */
    for (;;) {
        int logical = 0;
        int worded = 0;
        int prec = binary_prec(parser, no_in, &logical, &worded);
        if (prec == 0 || prec < min_prec) {
            break;
        }
        if (!enter(parser)) {
            return -1;
        }
        built++;
        uint16_t op = (uint16_t)parser->lx.kind;
        int32_t word_node = -1;
        if (worded) {
            word_node = jm_node_new(parser->prog, JN_IDENT); /* records instanceof/in text */
            parser->prog->nodes[word_node].str = parser->lx.text;
            parser->prog->nodes[word_node].str_len = parser->lx.text_len;
        }
        advance(parser);
        /* ** is right-associative (same prec for its right operand); others left. */
        int right_min = op == JT_POW ? prec : prec + 1;
        int32_t right = parse_binary(parser, right_min, no_in);
        if (parser->err) {
            return -1;
        }
        int32_t node = jm_node_new(parser->prog, logical ? JN_LOGICAL : JN_BINARY);
        parser->prog->nodes[node].op = op;
        set_a(parser, node, left);
        set_b(parser, node, right);
        if (worded) {
            /* carry the word operator's text so the printer emits instanceof/in */
            set_c(parser, node, word_node);
        }
        left = node;
    }
    parser->depth -= built; /* the spine is built; release its levels so a sibling expression starts fresh */
    return left;
}

static int is_assign_op(jm_tok kind) {
    switch (kind) {
    case JT_ASSIGN:
    case JT_PLUS_ASSIGN:
    case JT_MINUS_ASSIGN:
    case JT_STAR_ASSIGN:
    case JT_DIV_ASSIGN:
    case JT_MOD_ASSIGN:
    case JT_POW_ASSIGN:
    case JT_SHL_ASSIGN:
    case JT_SHR_ASSIGN:
    case JT_USHR_ASSIGN:
    case JT_AND_ASSIGN:
    case JT_OR_ASSIGN:
    case JT_XOR_ASSIGN:
    case JT_LAND_ASSIGN:
    case JT_LOR_ASSIGN:
    case JT_NULLISH_ASSIGN:
        return 1;
    default:
        return 0;
    }
}

static int32_t parse_cond(P *parser, int no_in) {
    int32_t test = parse_binary(parser, 1, no_in);
    if (parser->err) {
        return -1;
    }
    if (!at(parser, JT_QUESTION)) {
        return test;
    }
    advance(parser);
    int32_t node = jm_node_new(parser->prog, JN_COND);
    set_a(parser, node, test);
    set_b(parser, node, parse_assign(parser, 0)); /* the consequent allows `in` */
    expect(parser, JT_COLON, "expected :");
    if (parser->err) {
        return -1;
    }
    set_c(parser, node, parse_assign(parser, no_in));
    return parser->err ? -1 : node;
}

/* Peek whether the next tokens form an arrow head (`x =>` or `( ... ) =>`, each
   optionally prefixed `async`). Restores the lexer before returning. */
static int looks_like_arrow(P *parser) {
    jm_mark save = mark(parser);
    int result = 0;
    if (kw(parser, "async")) {
        advance(parser);
        if (parser->lx.newline_before) {
            reset(parser, save);
            return 0;
        }
    }
    if (at(parser, JT_IDENT)) {
        advance(parser);
        result = at(parser, JT_ARROW);
    } else if (at(parser, JT_LPAREN)) {
        int depth = 0;
        do {
            if (at(parser, JT_LPAREN)) {
                depth++;
            } else if (at(parser, JT_RPAREN)) {
                depth--;
            } else if (at(parser, JT_EOF) || at(parser, JT_ERROR)) {
                reset(parser, save);
                return 0;
            }
            advance(parser);
        } while (depth > 0);
        result = at(parser, JT_ARROW);
    }
    reset(parser, save);
    return result;
}

static int32_t parse_arrow(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_ARROW);
    if (kw(parser, "async")) {
        parser->prog->nodes[node].flags |= JN_F_ASYNC;
        advance(parser);
    }
    if (at(parser, JT_IDENT)) {
        int32_t param = leaf(parser, JN_IDENT);
        set_a(parser, node, param);
    } else {
        advance(parser); /* ( */
        int32_t tail = -1;
        /* no EOF guard: the cover-grammar lookahead already matched complete params and `=>`,
           and an unterminated list makes parse_assign error out below, so the loop always ends */
        while (!at(parser, JT_RPAREN)) {
            int32_t param = at(parser, JT_ELLIPSIS) ? parse_spread(parser) : parse_assign(parser, 0);
            if (parser->err) {
                return -1;
            }
            if (tail < 0) {
                set_a(parser, node, param);
            } else {
                parser->prog->nodes[tail].next = param;
            }
            tail = param;
            if (!eat(parser, JT_COMMA)) {
                break;
            }
        }
        expect(parser, JT_RPAREN, "expected )");
    }
    expect(parser, JT_ARROW, "expected =>");
    if (parser->err) {
        return -1;
    }
    if (at(parser, JT_LBRACE)) {
        set_b(parser, node, parse_block(parser));
    } else {
        parser->prog->nodes[node].flags |= JN_F_EXPRBODY;
        set_b(parser, node, parse_assign(parser, 0));
    }
    return parser->err ? -1 : node;
}

static int32_t parse_assign_body(P *parser, int no_in) {
    if (kw(parser, "yield")) {
        int32_t node = jm_node_new(parser->prog, JN_YIELD);
        advance(parser);
        if (eat(parser, JT_STAR)) {
            parser->prog->nodes[node].flags |= JN_F_DELEGATE;
        }
        if (!at(parser, JT_SEMI) && !at(parser, JT_RPAREN) && !at(parser, JT_RBRACE) && !at(parser, JT_RBRACK) &&
            !at(parser, JT_COMMA) && !at(parser, JT_COLON) && !at(parser, JT_EOF) && !parser->lx.newline_before) {
            set_a(parser, node, parse_assign(parser, no_in));
        }
        return parser->err ? -1 : node;
    }
    if (looks_like_arrow(parser)) {
        return parse_arrow(parser);
    }
    int32_t left = parse_cond(parser, no_in);
    if (parser->err) {
        return -1;
    }
    if (is_assign_op(parser->lx.kind)) {
        uint16_t op = (uint16_t)parser->lx.kind;
        advance(parser);
        int32_t node = jm_node_new(parser->prog, JN_ASSIGN);
        parser->prog->nodes[node].op = op;
        set_a(parser, node, left);
        set_b(parser, node, parse_assign(parser, no_in));
        return parser->err ? -1 : node;
    }
    return left;
}

static int32_t parse_expr(P *parser, int no_in) {
    int32_t first = parse_assign(parser, no_in);
    if (parser->err || !at(parser, JT_COMMA)) {
        return first;
    }
    int32_t node = jm_node_new(parser->prog, JN_SEQ);
    set_a(parser, node, first);
    int32_t tail = first;
    while (eat(parser, JT_COMMA)) {
        int32_t expr = parse_assign(parser, no_in);
        if (parser->err) {
            return -1;
        }
        parser->prog->nodes[tail].next = expr;
        tail = expr;
    }
    return node;
}

static int32_t parse_unary(P *parser) {
    jm_tok kind = parser->lx.kind;
    jm_kind node_kind;
    if (kind == JT_NOT || kind == JT_BIT_NOT || kind == JT_PLUS || kind == JT_MINUS || kw(parser, "typeof") ||
        kw(parser, "void") || kw(parser, "delete")) {
        node_kind = JN_UNARY;
    } else if (kind == JT_INC || kind == JT_DEC) {
        node_kind = JN_UPDATE;
    } else if (kw(parser, "await")) {
        node_kind = JN_AWAIT;
    } else {
        return parse_binary_base(parser); /* no prefix operator: the postfix ++/-- + call/member expression */
    }
    int32_t node = jm_node_new(parser->prog, node_kind);
    if (node_kind == JN_UNARY) {
        parser->prog->nodes[node].op = (uint16_t)kind;
        if (kind == JT_IDENT) {
            parser->prog->nodes[node].str = parser->lx.text; /* typeof / void / delete keyword text */
            parser->prog->nodes[node].str_len = parser->lx.text_len;
        }
    } else if (node_kind == JN_UPDATE) {
        parser->prog->nodes[node].op = (uint16_t)kind;
        parser->prog->nodes[node].flags |= JN_F_PREFIX;
    }
    advance(parser);
    if (!enter(parser)) { /* one guard for the shared prefix recursion (`!!!x`, `typeof void x`, `++x`) */
        return -1;
    }
    set_a(parser, node, parse_unary(parser));
    leave(parser);
    return parser->err ? -1 : node;
}

/* Resolve the call/member/new chain, then an optional postfix update. */
static int32_t parse_binary_base(P *parser) {
    int32_t expr = parse_call_member(parser);
    if (parser->err) {
        return -1;
    }
    if ((at(parser, JT_INC) || at(parser, JT_DEC)) && !parser->lx.newline_before) {
        int32_t node = jm_node_new(parser->prog, JN_UPDATE);
        parser->prog->nodes[node].op = (uint16_t)parser->lx.kind;
        set_a(parser, node, expr);
        advance(parser);
        return node;
    }
    return expr;
}

/* arguments: ( assign , assign , ...spread ) - fills the arg chain of node->b. */
static void parse_args(P *parser, int32_t call) {
    advance(parser); /* ( */
    int32_t tail = -1;
    while (!at(parser, JT_RPAREN) && !at(parser, JT_EOF)) {
        int32_t arg = at(parser, JT_ELLIPSIS) ? parse_spread(parser) : parse_assign(parser, 0);
        if (parser->err) {
            return;
        }
        if (tail < 0) {
            set_b(parser, call, arg);
        } else {
            parser->prog->nodes[tail].next = arg;
        }
        tail = arg;
        if (!eat(parser, JT_COMMA)) {
            break;
        }
    }
    expect(parser, JT_RPAREN, "expected )");
}

/* The constructor of a `new` expression is a MemberExpression: member accesses bind
   to it, but the first `(` is the new's own argument list, not a call on the callee.
   So this walks the dot/bracket chain only, stopping at `(`. */
static int32_t parse_new_callee(P *parser) {
    int32_t expr;
    if (kw(parser, "new")) {
        int32_t node = jm_node_new(parser->prog, JN_NEW);
        advance(parser);
        if (!enter(parser)) { /* `new new new ...` recurses here */
            return -1;
        }
        set_a(parser, node, parse_new_callee(parser));
        leave(parser);
        if (parser->err) {
            return -1;
        }
        if (at(parser, JT_LPAREN)) {
            parse_args(parser, node);
        }
        expr = node;
    } else {
        expr = parse_primary(parser);
    }
    if (parser->err) {
        return -1;
    }
    int32_t built = 0; /* the callee's member chain deepens the spine one node per link */
    for (;;) {
        if (!at(parser, JT_DOT) && !at(parser, JT_LBRACK)) {
            break;
        }
        if (!enter(parser)) {
            return -1;
        }
        built++;
        if (at(parser, JT_DOT)) {
            advance(parser);
            int32_t prop = leaf(parser, JN_IDENT);
            int32_t node = jm_node_new(parser->prog, JN_MEMBER_EXPR);
            set_a(parser, node, expr);
            set_b(parser, node, prop);
            expr = node;
        } else {
            advance(parser);
            int32_t node = jm_node_new(parser->prog, JN_MEMBER_EXPR);
            parser->prog->nodes[node].flags |= JN_F_COMPUTED;
            set_a(parser, node, expr);
            set_b(parser, node, parse_expr(parser, 0));
            expect(parser, JT_RBRACK, "expected ]");
            expr = node;
        }
        if (parser->err) {
            return -1;
        }
    }
    parser->depth -= built;
    return expr;
}

static int32_t parse_call_member(P *parser) {
    int32_t expr;
    int32_t built = 0; /* every call/member/tag link deepens the left-leaning spine by one */
    if (kw(parser, "new")) {
        int32_t node = jm_node_new(parser->prog, JN_NEW);
        advance(parser);
        if (at(parser, JT_DOT)) { /* new.target */
            static const Py_UCS4 new_kw[] = {'n', 'e', 'w', 0};
            advance(parser);
            int32_t target = leaf(parser, JN_IDENT);
            int32_t kwn = jm_node_new(parser->prog, JN_IDENT);
            parser->prog->nodes[kwn].str = new_kw;
            parser->prog->nodes[kwn].str_len = 3;
            expr = jm_node_new(parser->prog, JN_MEMBER_EXPR);
            set_a(parser, expr, kwn);
            set_b(parser, expr, target);
            goto chain; /* allow a following member/call chain */
        }
        set_a(parser, node, parse_new_callee(parser)); /* MemberExpression, no trailing call */
        if (parser->err) {
            return -1;
        }
        /* a new expression takes the immediately following argument list, if any */
        if (at(parser, JT_LPAREN)) {
            parse_args(parser, node);
        }
        expr = node;
    } else {
        expr = parse_primary(parser);
    }
    if (parser->err) {
        return -1;
    }
chain:
    for (;;) {
        if (!at(parser, JT_DOT) && !at(parser, JT_OPT_CHAIN) && !at(parser, JT_LBRACK) && !at(parser, JT_LPAREN) &&
            !at(parser, JT_TEMPLATE) && !at(parser, JT_TEMPLATE_HEAD)) {
            break;
        }
        if (!enter(parser)) {
            return -1;
        }
        built++;
        if (at(parser, JT_DOT)) {
            advance(parser);
            int32_t prop = leaf(parser, JN_IDENT); /* a property name (identifier name) */
            int32_t node = jm_node_new(parser->prog, JN_MEMBER_EXPR);
            set_a(parser, node, expr);
            set_b(parser, node, prop);
            expr = node;
        } else if (at(parser, JT_OPT_CHAIN)) {
            advance(parser);
            int32_t node = jm_node_new(parser->prog, JN_MEMBER_EXPR);
            parser->prog->nodes[node].flags |= JN_F_OPTIONAL;
            set_a(parser, node, expr);
            if (at(parser, JT_LPAREN)) { /* ?.( */
                int32_t call = jm_node_new(parser->prog, JN_CALL);
                parser->prog->nodes[call].flags |= JN_F_OPTIONAL;
                set_a(parser, call, expr);
                parse_args(parser, call);
                expr = call;
            } else if (at(parser, JT_LBRACK)) { /* ?.[ */
                advance(parser);
                parser->prog->nodes[node].flags |= JN_F_COMPUTED;
                set_b(parser, node, parse_expr(parser, 0));
                expect(parser, JT_RBRACK, "expected ]");
                expr = node;
            } else {
                set_b(parser, node, leaf(parser, JN_IDENT));
                expr = node;
            }
        } else if (at(parser, JT_LBRACK)) {
            advance(parser);
            int32_t node = jm_node_new(parser->prog, JN_MEMBER_EXPR);
            parser->prog->nodes[node].flags |= JN_F_COMPUTED;
            set_a(parser, node, expr);
            set_b(parser, node, parse_expr(parser, 0));
            expect(parser, JT_RBRACK, "expected ]");
            expr = node;
        } else if (at(parser, JT_LPAREN)) {
            int32_t node = jm_node_new(parser->prog, JN_CALL);
            set_a(parser, node, expr);
            parse_args(parser, node);
            expr = node;
        } else { /* JT_TEMPLATE / JT_TEMPLATE_HEAD: a tagged template */
            int32_t node = jm_node_new(parser->prog, JN_TAGGED);
            set_a(parser, node, expr);
            set_b(parser, node, parse_primary(parser)); /* the template literal */
            expr = node;
        }
        if (parser->err) {
            return -1;
        }
    }
    parser->depth -= built;
    return expr;
}

static int32_t parse_template(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_TEMPLATE);
    int32_t head = jm_node_new(parser->prog, JN_QUASI);
    parser->prog->nodes[head].str = parser->lx.text;
    parser->prog->nodes[head].str_len = parser->lx.text_len;
    int complete = at(parser, JT_TEMPLATE);
    advance(parser);
    set_a(parser, node, head);
    int32_t tail = head;
    while (!complete) {
        int32_t expr = parse_expr(parser, 0);
        if (parser->err) {
            return -1;
        }
        parser->prog->nodes[tail].next = expr;
        tail = expr;
        if (!at(parser, JT_RBRACE)) {
            fail(parser, "expected } in template");
            return -1;
        }
        jm_lex_rescan_template(&parser->lx);
        if (parser->lx.kind == JT_ERROR) {
            fail(parser, "bad template");
            return -1;
        }
        int32_t chunk = jm_node_new(parser->prog, JN_QUASI);
        parser->prog->nodes[chunk].str = parser->lx.text;
        parser->prog->nodes[chunk].str_len = parser->lx.text_len;
        complete = at(parser, JT_TEMPLATE_TAIL);
        advance(parser);
        parser->prog->nodes[tail].next = chunk;
        tail = chunk;
    }
    return node;
}

static int32_t parse_object(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_OBJECT);
    advance(parser); /* { */
    int32_t tail = -1;
    while (!at(parser, JT_RBRACE) && !at(parser, JT_EOF)) {
        int32_t prop = jm_node_new(parser->prog, JN_PROP);
        if (at(parser, JT_ELLIPSIS)) {
            parser->prog->nodes[prop].kind = JN_SPREAD;
            advance(parser);
            set_a(parser, prop, parse_assign(parser, 0));
        } else {
            /* get/set accessor, or a method, or key: value, or shorthand */
            int is_get = 0, is_set = 0, is_async = 0, is_gen = 0;
            if ((kw(parser, "get") || kw(parser, "set") || kw(parser, "async"))) {
                jm_mark save = mark(parser);
                is_get = kw(parser, "get");
                is_set = kw(parser, "set");
                is_async = kw(parser, "async");
                advance(parser);
                if (at(parser, JT_COLON) || at(parser, JT_COMMA) || at(parser, JT_RBRACE) || at(parser, JT_LPAREN)) {
                    reset(parser, save); /* it was actually a property named get/set/async */
                    is_get = is_set = is_async = 0;
                }
            }
            /* get/set cannot be generators; a method or async method can take `*` */
            if (!is_get && !is_set && eat(parser, JT_STAR)) {
                is_gen = 1;
            }
            /* key */
            if (at(parser, JT_LBRACK)) {
                advance(parser);
                parser->prog->nodes[prop].flags |= JN_F_COMPUTED;
                set_a(parser, prop, parse_assign(parser, 0));
                expect(parser, JT_RBRACK, "expected ]");
            } else if (at(parser, JT_STRING) || at(parser, JT_NUM)) {
                set_a(parser, prop, leaf(parser, at(parser, JT_STRING) ? JN_STRING : JN_NUM));
            } else {
                set_a(parser, prop, leaf(parser, JN_IDENT)); /* identifier or #private key */
            }
            if (parser->err) {
                return -1;
            }
            if (is_get || is_set || is_gen || is_async || at(parser, JT_LPAREN)) {
                /* a method/accessor: its value is a function without the `function` keyword */
                int32_t fn = jm_node_new(parser->prog, JN_FUNC);
                parser->prog->nodes[fn].flags |= JN_F_EXPR;
                if (is_async) {
                    parser->prog->nodes[fn].flags |= JN_F_ASYNC;
                }
                if (is_gen) {
                    parser->prog->nodes[fn].flags |= JN_F_GENERATOR;
                }
                if (is_get) {
                    parser->prog->nodes[prop].flags |= JN_F_GET;
                } else if (is_set) {
                    parser->prog->nodes[prop].flags |= JN_F_SET;
                } else {
                    parser->prog->nodes[prop].flags |= JN_F_METHOD;
                }
                parse_params(parser, fn);
                if (parser->err) {
                    return -1;
                }
                set_b(parser, fn, parse_block(parser));
                set_b(parser, prop, fn);
            } else if (eat(parser, JT_COLON)) {
                set_b(parser, prop, parse_assign(parser, 0));
            } else if (eat(parser, JT_ASSIGN)) {
                /* shorthand with a default - only valid in a destructuring pattern */
                parser->prog->nodes[prop].flags |= JN_F_SHORTHAND;
                int32_t def = jm_node_new(parser->prog, JN_ASSIGN);
                parser->prog->nodes[def].op = JT_ASSIGN;
                set_a(parser, def, parser->prog->nodes[prop].a);
                set_b(parser, def, parse_assign(parser, 0));
                set_b(parser, prop, def);
            } else {
                parser->prog->nodes[prop].flags |= JN_F_SHORTHAND;
            }
        }
        if (parser->err) {
            return -1;
        }
        if (tail < 0) {
            set_a(parser, node, prop);
        } else {
            parser->prog->nodes[tail].next = prop;
        }
        tail = prop;
        if (!eat(parser, JT_COMMA)) {
            break;
        }
    }
    expect(parser, JT_RBRACE, "expected }");
    return parser->err ? -1 : node;
}

static int32_t parse_array(P *parser) {
    int32_t node = jm_node_new(parser->prog, JN_ARRAY);
    advance(parser); /* [ */
    int32_t tail = -1;
    while (!at(parser, JT_RBRACK) && !at(parser, JT_EOF)) {
        int32_t elem;
        if (at(parser, JT_COMMA)) {
            elem = jm_node_new(parser->prog, JN_HOLE);
        } else if (at(parser, JT_ELLIPSIS)) {
            elem = parse_spread(parser);
        } else {
            elem = parse_assign(parser, 0);
        }
        if (parser->err) {
            return -1;
        }
        if (tail < 0) {
            set_a(parser, node, elem);
        } else {
            parser->prog->nodes[tail].next = elem;
        }
        tail = elem;
        if (!eat(parser, JT_COMMA)) {
            break;
        }
    }
    expect(parser, JT_RBRACK, "expected ]");
    return parser->err ? -1 : node;
}

static int32_t parse_primary(P *parser) {
    switch (parser->lx.kind) {
    case JT_NUM:
        return leaf(parser, JN_NUM);
    case JT_STRING:
        return leaf(parser, JN_STRING);
    case JT_BIGINT:
        return leaf(parser, JN_BIGINT);
    case JT_PRIVATE:
        return leaf(parser, JN_IDENT);
    case JT_DIV:
    case JT_DIV_ASSIGN:
        jm_lex_rescan_regex(&parser->lx);
        if (parser->lx.kind == JT_ERROR) {
            fail(parser, "bad regex");
            return -1;
        }
        return leaf(parser, JN_REGEX);
    case JT_TEMPLATE:
    case JT_TEMPLATE_HEAD:
        return parse_template(parser);
    case JT_LBRACK:
        return parse_array(parser);
    case JT_LBRACE:
        return parse_object(parser);
    case JT_LPAREN: {
        advance(parser);
        int32_t expr = parse_expr(parser, 0);
        expect(parser, JT_RPAREN, "expected )");
        if (parser->err) {
            return -1;
        }
        /* parentheses around an optional chain are load-bearing - they end the chain
           so a following access always evaluates - so keep them through the printer. */
        for (int32_t walk = expr; walk >= 0;) {
            const jm_node *node = &parser->prog->nodes[walk];
            if (node->flags & JN_F_OPTIONAL) {
                parser->prog->nodes[expr].flags |= JN_F_PAREN;
                break;
            }
            walk = (node->kind == JN_MEMBER_EXPR || node->kind == JN_CALL) ? node->a : -1;
        }
        return expr;
    }
    case JT_IDENT:
        if (kw(parser, "import")) { /* import.meta / import(); export never reaches expression position */
            fail(parser, "module syntax unsupported");
            return -1;
        }
        if (kw(parser, "function")) {
            return parse_function(parser, 1, 0);
        }
        if (kw(parser, "class")) {
            return parse_class(parser, 1);
        }
        if (kw(parser, "async")) {
            jm_mark save = mark(parser);
            advance(parser);
            if (kw(parser, "function") && !parser->lx.newline_before) {
                return parse_function(parser, 1, 1);
            }
            reset(parser, save);
        }
        return leaf(parser, JN_IDENT);
    default:
        fail(parser, "unexpected token");
        return -1;
    }
}

static void parse_params(P *parser, int32_t fn) {
    expect(parser, JT_LPAREN, "expected (");
    int32_t tail = -1;
    while (!at(parser, JT_RPAREN) && !at(parser, JT_EOF)) {
        int32_t param = at(parser, JT_ELLIPSIS) ? parse_spread(parser) : parse_assign(parser, 0);
        if (parser->err) {
            return;
        }
        if (tail < 0) {
            set_a(parser, fn, param);
        } else {
            parser->prog->nodes[tail].next = param;
        }
        tail = param;
        if (!eat(parser, JT_COMMA)) {
            break;
        }
    }
    expect(parser, JT_RPAREN, "expected )");
}

static int32_t parse_function(P *parser, int is_expr, int is_async) {
    int32_t node = jm_node_new(parser->prog, JN_FUNC);
    if (is_expr) {
        parser->prog->nodes[node].flags |= JN_F_EXPR;
    }
    if (is_async) {
        parser->prog->nodes[node].flags |= JN_F_ASYNC;
        advance(parser); /* async (the function keyword is current) */
    }
    advance(parser); /* function */
    if (eat(parser, JT_STAR)) {
        parser->prog->nodes[node].flags |= JN_F_GENERATOR;
    }
    if (at(parser, JT_IDENT)) {
        set_ident(parser, node);
        advance(parser);
    }
    parse_params(parser, node);
    if (parser->err) {
        return -1;
    }
    set_b(parser, node, parse_block(parser));
    return parser->err ? -1 : node;
}

static int32_t parse_class(P *parser, int is_expr) {
    int32_t node = jm_node_new(parser->prog, JN_CLASS);
    if (is_expr) {
        parser->prog->nodes[node].flags |= JN_F_EXPR;
    }
    advance(parser); /* class */
    if (at(parser, JT_IDENT) && !kw(parser, "extends")) {
        set_ident(parser, node);
        advance(parser);
    }
    if (kw(parser, "extends")) {
        advance(parser);
        set_a(parser, node, parse_call_member(parser));
        if (parser->err) {
            return -1;
        }
    }
    expect(parser, JT_LBRACE, "expected {");
    if (parser->err) {
        return -1;
    }
    int32_t tail = -1;
    while (!at(parser, JT_RBRACE) && !at(parser, JT_EOF)) {
        if (eat(parser, JT_SEMI)) {
            continue; /* a stray semicolon between members */
        }
        int32_t member = jm_node_new(parser->prog, JN_MEMBER);
        int is_static = 0, is_get = 0, is_set = 0, is_async = 0, is_gen = 0;
        if (kw(parser, "static")) {
            jm_mark save = mark(parser);
            advance(parser);
            if (at(parser, JT_LPAREN) || at(parser, JT_ASSIGN) || at(parser, JT_SEMI) || at(parser, JT_RBRACE)) {
                reset(parser, save); /* a field named static */
            } else {
                is_static = 1;
            }
        }
        if (kw(parser, "async")) {
            jm_mark save = mark(parser);
            advance(parser);
            if (at(parser, JT_LPAREN) || at(parser, JT_ASSIGN) || at(parser, JT_SEMI) || parser->lx.newline_before) {
                reset(parser, save);
            } else {
                is_async = 1;
            }
        }
        if (eat(parser, JT_STAR)) {
            is_gen = 1;
        }
        if ((kw(parser, "get") || kw(parser, "set")) && !is_async && !is_gen) {
            jm_mark save = mark(parser);
            int want_get = kw(parser, "get");
            advance(parser);
            if (at(parser, JT_LPAREN) || at(parser, JT_ASSIGN) || at(parser, JT_SEMI) || at(parser, JT_RBRACE)) {
                reset(parser, save); /* a field named get/set */
            } else if (want_get) {
                is_get = 1;
            } else {
                is_set = 1;
            }
        }
        if (is_static) {
            parser->prog->nodes[member].flags |= JN_F_STATIC;
        }
        /* key */
        if (at(parser, JT_LBRACK)) {
            advance(parser);
            parser->prog->nodes[member].flags |= JN_F_COMPUTED;
            set_a(parser, member, parse_assign(parser, 0));
            expect(parser, JT_RBRACK, "expected ]");
        } else if (at(parser, JT_STRING) || at(parser, JT_NUM)) {
            set_a(parser, member, leaf(parser, at(parser, JT_STRING) ? JN_STRING : JN_NUM));
        } else {
            set_a(parser, member, leaf(parser, JN_IDENT)); /* identifier or #private */
        }
        if (parser->err) {
            return -1;
        }
        if (is_get || is_set || is_gen || is_async || at(parser, JT_LPAREN)) {
            int32_t fn = jm_node_new(parser->prog, JN_FUNC);
            parser->prog->nodes[fn].flags |= JN_F_EXPR;
            if (is_async) {
                parser->prog->nodes[fn].flags |= JN_F_ASYNC;
            }
            if (is_gen) {
                parser->prog->nodes[fn].flags |= JN_F_GENERATOR;
            }
            parser->prog->nodes[member].decl = is_get ? 1 : is_set ? 2 : 0; /* 0 method, 1 get, 2 set */
            parse_params(parser, fn);
            if (parser->err) {
                return -1;
            }
            set_b(parser, fn, parse_block(parser));
            set_b(parser, member, fn);
        } else {
            /* a field: optional initializer, then ASI */
            parser->prog->nodes[member].decl = 3; /* field */
            if (eat(parser, JT_ASSIGN)) {
                set_b(parser, member, parse_assign(parser, 0));
            }
            semicolon(parser);
        }
        if (parser->err) {
            return -1;
        }
        if (tail < 0) {
            set_b(parser, node, member);
        } else {
            parser->prog->nodes[tail].next = member;
        }
        tail = member;
    }
    expect(parser, JT_RBRACE, "expected }");
    return parser->err ? -1 : node;
}

jm_program *jm_parse(const Py_UCS4 *src, Py_ssize_t len, char *errbuf, size_t errlen) {
    jm_program *prog = jm_calloc(1, sizeof(jm_program));
    if (prog == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        if (errlen > 0) {     /* GCOVR_EXCL_LINE */
            errbuf[0] = '\0'; /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        return NULL; /* GCOVR_EXCL_LINE */
    }
    prog->src = src;
    prog->src_len = len;

    P parser = {.prog = prog, .err = 0, .errbuf = errbuf, .errlen = errlen};
    if (errlen > 0) { /* errlen==0 is the no-message opt-out the HTML inline-<script> path uses */
        errbuf[0] = '\0';
    }
    jm_lex_init(&parser.lx, src, len);
    parser.lx.sink = prog; /* accrue kept license/banner comments before the first token is read */
    advance(&parser);

    int32_t root = jm_node_new(prog, JN_PROGRAM);
    int32_t tail = -1;
    while (!at(&parser, JT_EOF) && !parser.err) {
        int32_t stmt = parse_stmt(&parser);
        if (parser.err) {
            break;
        }
        if (tail < 0) {
            prog->nodes[root].a = stmt;
        } else {
            prog->nodes[tail].next = stmt;
        }
        tail = stmt;
    }
    prog->root = root;
    prog->comment_count = parser.lx.comment_count; /* commit the run scanned on the real parse path */

    if (parser.err || prog->failed) {     /* GCOVR_EXCL_BR_LINE: prog->failed is only set on allocation failure */
        if (prog->failed && errlen > 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            errbuf[0] = '\0';             /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        jm_program_free(prog);
        return NULL;
    }
    return prog;
}
