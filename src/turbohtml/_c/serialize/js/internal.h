/* Shared internal contract for the JavaScript-minifier translation units: the
   token kinds, the lexer state, and the helpers the lexer/parser/printer call
   across their split-file boundaries. The pipeline mirrors the XPath engine
   (query/xpath/{lexer,parser,ast}.c): a lexer turns code points into tokens, a
   recursive-descent parser builds a flat arena AST, and a printer walks it back
   out into the shared serializer buffer. All symbols carry the jm_ (JS-minify)
   prefix; only th_js_minify in js/minify.h crosses into the rest of the module.

   This header grows by phase. Phase 1 lands the lexer surface only. */

#ifndef TURBOHTML_SERIALIZE_JS_INTERNAL_H
#define TURBOHTML_SERIALIZE_JS_INTERNAL_H

#include "serialize/js/jstypes.h"

/* ----------------------------------------------------------------- tokens */

/* ECMAScript token kinds. The lexer never decides whether a `/` is division or a
   regular-expression literal (that needs grammar position): it always reports
   JT_DIV / JT_DIV_ASSIGN, and the parser calls jm_lex_rescan_regex when a value is
   expected. The same rewind trick re-reads a `}` as a template continuation. */
typedef enum {
    JT_EOF,
    JT_ERROR,

    /* value-bearing: text/text_len span the lexeme in the source */
    JT_IDENT,           /* identifier or keyword (the parser classifies keywords) */
    JT_PRIVATE,         /* #name - a private class member */
    JT_NUM,             /* numeric literal */
    JT_BIGINT,          /* numeric literal with the trailing n */
    JT_STRING,          /* '...' or "..." */
    JT_REGEX,           /* /body/flags - only ever produced by jm_lex_rescan_regex */
    JT_TEMPLATE,        /* `...` with no substitution */
    JT_TEMPLATE_HEAD,   /* `...${ */
    JT_TEMPLATE_MIDDLE, /* }...${ - only ever produced by jm_lex_rescan_template */
    JT_TEMPLATE_TAIL,   /* }...` - only ever produced by jm_lex_rescan_template */

    /* punctuators and operators */
    JT_LBRACE,
    JT_RBRACE,
    JT_LPAREN,
    JT_RPAREN,
    JT_LBRACK,
    JT_RBRACK,
    JT_DOT,
    JT_ELLIPSIS, /* ... */
    JT_SEMI,
    JT_COMMA,
    JT_ARROW,     /* => */
    JT_OPT_CHAIN, /* ?. */
    JT_QUESTION,
    JT_COLON,
    JT_LT,
    JT_GT,
    JT_LE,
    JT_GE,
    JT_EQ_EQ,    /* == */
    JT_NE,       /* != */
    JT_EQ_EQ_EQ, /* === */
    JT_NE_EQ,    /* !== */
    JT_PLUS,
    JT_MINUS,
    JT_STAR,
    JT_DIV,
    JT_MOD,
    JT_POW,  /* ** */
    JT_INC,  /* ++ */
    JT_DEC,  /* -- */
    JT_SHL,  /* << */
    JT_SHR,  /* >> */
    JT_USHR, /* >>> */
    JT_BIT_AND,
    JT_BIT_OR,
    JT_BIT_XOR,
    JT_NOT,     /* ! */
    JT_BIT_NOT, /* ~ */
    JT_AND,     /* && */
    JT_OR,      /* || */
    JT_NULLISH, /* ?? */
    JT_ASSIGN,
    JT_PLUS_ASSIGN,
    JT_MINUS_ASSIGN,
    JT_STAR_ASSIGN,
    JT_DIV_ASSIGN,
    JT_MOD_ASSIGN,
    JT_POW_ASSIGN,
    JT_SHL_ASSIGN,
    JT_SHR_ASSIGN,
    JT_USHR_ASSIGN,
    JT_AND_ASSIGN,     /* &= */
    JT_OR_ASSIGN,      /* |= */
    JT_XOR_ASSIGN,     /* ^= */
    JT_LAND_ASSIGN,    /* &&= */
    JT_LOR_ASSIGN,     /* ||= */
    JT_NULLISH_ASSIGN, /* ??= */
} jm_tok;

/* ----------------------------------------------------------------- lexer */

typedef struct {
    const Py_UCS4 *src;
    Py_ssize_t len;
    Py_ssize_t pos;   /* scan cursor: one past the current token */
    Py_ssize_t start; /* where the current token began (for rescan/lexeme) */

    jm_tok kind;
    const Py_UCS4 *text; /* lexeme of a value-bearing token (into src) */
    Py_ssize_t text_len;
    int newline_before; /* a line terminator (or a block comment with one) preceded this token */

    int error; /* a lexical error was hit; kind is JT_ERROR */
} jm_lexer;

/* Initialize a lexer over src[0..len). Does not read the first token; call
   jm_lex_next. The caller owns src for the lexer's lifetime; the lexer itself
   holds no owned memory (template nesting is tracked by the parser's recursion). */
void jm_lex_init(jm_lexer *lx, const Py_UCS4 *src, Py_ssize_t len);

/* Advance past the current token, leaving the next one in lx->kind. Treats `/` as
   division and `}` as a closing brace; the parser overrides those with the rescan
   entry points below before consuming the token. */
void jm_lex_next(jm_lexer *lx);

/* Re-read the current JT_DIV / JT_DIV_ASSIGN token as a JT_REGEX literal, rewinding
   to its `/`. The parser calls this when a value (not an operator) is expected. */
void jm_lex_rescan_regex(jm_lexer *lx);

/* Re-read the current JT_RBRACE token as a template continuation, rewinding to its
   `}`. The parser calls this after parsing a `${...}` substitution expression. The
   result is JT_TEMPLATE_MIDDLE (another `${`) or JT_TEMPLATE_TAIL (the closing `` ` ``). */
void jm_lex_rescan_template(jm_lexer *lx);

/* Whether the lexer's current value-bearing token text equals the ASCII keyword. */
int jm_text_eq(const jm_lexer *lx, const char *keyword);

/* A short, stable name for a token kind, used by the parser-test dump hook. */
const char *jm_tok_name(jm_tok kind);

/* ----------------------------------------------------------------- AST */

/* The node kinds of the arena AST. One flat array of jm_node holds the whole tree
   (statements and expressions share the array); children are int32 indices into it,
   -1 for "none". A node's role for its index fields (a/b/c/d) and its str/num
   payload is documented per kind. Identifiers, literals and template chunks borrow
   their text from the source buffer (zero copy), which outlives the minify call. */
typedef enum {
    JN_PROGRAM, /* a = first statement (chained by next) */
    JN_BLOCK,   /* a = first statement */
    JN_EMPTY,
    JN_EXPR_STMT, /* a = expression */
    JN_VAR,       /* decl = 0 var / 1 let / 2 const; a = first JN_DECLR */
    JN_DECLR,     /* a = target (ident or pattern); b = init or -1 */
    JN_IF,        /* a = test; b = consequent; c = alternate or -1 */
    JN_FOR,       /* a = init or -1; b = test or -1; c = update or -1; d = body */
    JN_FORIN,     /* a = left; b = right; c = body */
    JN_FOROF,     /* a = left; b = right; c = body; flags JN_F_AWAIT */
    JN_WHILE,     /* a = test; b = body */
    JN_DOWHILE,   /* a = body; b = test */
    JN_RETURN,    /* a = argument or -1 */
    JN_THROW,     /* a = argument */
    JN_BREAK,     /* str = label or NULL */
    JN_CONTINUE,  /* str = label or NULL */
    JN_SWITCH,    /* a = discriminant; b = first JN_CASE */
    JN_CASE,      /* a = test or -1 (default); b = first statement */
    JN_TRY,       /* a = block; b = catch param or -1; c = catch block or -1; d = finally or -1 */
    JN_FUNC,      /* str = name or NULL; a = first param; b = body block; flags async/generator/expr */
    JN_CLASS,     /* str = name or NULL; a = superclass or -1; b = first JN_MEMBER */
    JN_MEMBER,    /* class member: a = key; b = value; decl = member kind; flags static/computed */
    JN_LABEL,     /* str = label; a = statement */
    JN_WITH,      /* a = object; b = body */
    JN_DEBUGGER,

    JN_IDENT,    /* str = name; sym = symbol id (>=0) for a resolvable binding/reference, else -1 */
    JN_NUM,      /* str = lexeme */
    JN_STRING,   /* str = lexeme including quotes */
    JN_REGEX,    /* str = lexeme */
    JN_BIGINT,   /* str = lexeme including the trailing n */
    JN_TEMPLATE, /* a = first child: JN_QUASI and expression nodes alternating */
    JN_QUASI,    /* str = the raw chunk lexeme including its ` ${ } delimiters */
    JN_TAGGED,   /* a = tag expression; b = JN_TEMPLATE */
    JN_ARRAY,    /* a = first element (JN_HOLE marks an elision; JN_SPREAD a spread) */
    JN_HOLE,
    JN_OBJECT,      /* a = first JN_PROP */
    JN_PROP,        /* a = key; b = value; decl = prop kind; flags shorthand/computed/method/get/set */
    JN_SPREAD,      /* a = argument */
    JN_MEMBER_EXPR, /* a = object; b = property; flags computed/optional */
    JN_CALL,        /* a = callee; b = first argument; flags optional */
    JN_NEW,         /* a = callee; b = first argument */
    JN_UNARY,       /* op = operator token; a = argument */
    JN_UPDATE,      /* op = ++/--; a = argument; flags prefix */
    JN_BINARY,      /* op; a = left; b = right */
    JN_LOGICAL,     /* op = && / || / ??; a = left; b = right */
    JN_ASSIGN,      /* op = = or a compound assign; a = target; b = value */
    JN_COND,        /* a = test; b = consequent; c = alternate */
    JN_SEQ,         /* a = first expression (chained by next) - the comma operator */
    JN_ARROW,       /* a = first param; b = body (expression or JN_BLOCK); flags async/exprbody */
    JN_YIELD,       /* a = argument or -1; flags delegate */
    JN_AWAIT,       /* a = argument */
} jm_kind;

enum {
    JN_F_ASYNC = 1 << 0,
    JN_F_GENERATOR = 1 << 1,
    JN_F_EXPR = 1 << 2,      /* JN_FUNC/JN_CLASS used as an expression, not a declaration */
    JN_F_STATIC = 1 << 3,    /* class member */
    JN_F_COMPUTED = 1 << 4,  /* [key] member / property */
    JN_F_OPTIONAL = 1 << 5,  /* ?. member or call */
    JN_F_PREFIX = 1 << 6,    /* prefix ++/-- */
    JN_F_SHORTHAND = 1 << 7, /* { x } object shorthand */
    JN_F_METHOD = 1 << 8,    /* object/class method (value is a function printed without `function`) */
    JN_F_GET = 1 << 9,
    JN_F_SET = 1 << 10,
    JN_F_AWAIT = 1 << 11,    /* for-await-of */
    JN_F_EXPRBODY = 1 << 12, /* arrow with an expression body */
    JN_F_DELEGATE = 1 << 13, /* yield* */
    JN_F_PAREN = 1 << 14,    /* a parenthesized optional chain whose parens are load-bearing:
                                `(a?.b).c` breaks the short-circuit that `a?.b.c` keeps */
};

typedef struct {
    uint8_t kind; /* jm_kind */
    uint8_t decl; /* var-decl kind / class-member kind / property kind */
    uint16_t op;  /* jm_tok for operator nodes */
    uint16_t flags;
    int32_t sym; /* JN_IDENT symbol id, else -1 */
    int32_t a, b, c, d;
    int32_t next; /* sibling chain (-1 none) */
    const Py_UCS4 *str;
    Py_ssize_t str_len;
} jm_node;

/* ----------------------------------------------------------------- scope/symbols */

/* A lexical binding. name borrows the source; resolved follows references to their
   declaration after the whole program is parsed (a reference's symbol points at the
   nearest enclosing declaration once scopes close). uses counts references so the
   mangler can spend the shortest names on the hottest bindings. mangled is the
   renamed code points the printer emits, or NULL to keep name. */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    int32_t scope;      /* declaring scope id */
    int32_t scope_next; /* next symbol declared in the same scope, or -1 */
    uint8_t decl;       /* 0 var / 1 let / 2 const / 3 param / 4 function / 5 catch / 6 class */
    uint32_t uses;
    int32_t slot;     /* rename slot: bindings sharing a slot take the same short name */
    Py_UCS4 *mangled; /* assigned short name (owned), or NULL to keep the original */
    Py_ssize_t mangled_len;
    /* single-use inlining and dead-binding elimination: refs counts read references and writes counts
       assignment/update/for-target references (a binding read once and never written has refs == 1,
       writes == 0); ref_node is the one read's node when refs == 1; decl_node is the single-declarator
       statement (var/let/const or function) the binding is declared by */
    int32_t refs;
    int32_t writes;
    int32_t ref_node;
    int32_t ref_scope; /* the scope the one read sits in; a function inlines only into its own scope */
    int32_t decl_node;
    int32_t ref_prop; /* the `{ x }` property node when a read is a shorthand: the read doubles as
                         the key, so an inline must first give the property an explicit key */
    int32_t min_ref;  /* the lowest read node index: parse order is textual order, so a read below
                         the binding's own declarator index runs before it initializes */
} jm_sym;

/* A scope node in the lexical scope tree. kind distinguishes a function/block/catch/
   with scope (var hoisting stops at a function scope; with/eval poison renaming). */
typedef struct {
    int32_t parent;
    uint8_t kind;         /* 0 block / 1 function / 2 catch / 3 with */
    int poisoned;         /* a with or a direct eval makes renaming unsafe in this subtree */
    int32_t first_sym;    /* head of the chain of symbols declared here (jm_sym.scope == this) */
    int32_t first_child;  /* head of the child-scope list, for an O(scopes) tree walk */
    int32_t next_sibling; /* next scope with the same parent */
    int32_t first_stmt;   /* a function scope's first body statement: nothing executes before it,
                             so a var literal declared there dominates every read (-1 elsewhere) */
} jm_scope;

/* ----------------------------------------------------------------- program */

/* A parsed program: the node arena, the symbol and scope tables, the root node, and
   the borrowed source. Owned by one minify call and freed with jm_program_free. */
typedef struct {
    jm_node *nodes;
    int32_t node_count;
    int32_t node_cap;
    int32_t root;

    jm_sym *syms;
    int32_t sym_count;
    int32_t sym_cap;

    jm_scope *scopes;
    int32_t scope_count;
    int32_t scope_cap;

    const Py_UCS4 *src;
    Py_ssize_t src_len;

    /* lexemes the fold pass synthesizes (a folded `1+2` -> `3`): the parser's nodes borrow the
       source, but a folded literal has no source span, so the program owns and frees these. */
    Py_UCS4 **owned;
    int32_t owned_count;
    int32_t owned_cap;
    int failed; /* allocation failure */
} jm_program;

/* Copy len code points into a program-owned buffer (freed with the program) and return it, or NULL
   on allocation failure. Used by the fold pass for a literal it synthesizes. */
const Py_UCS4 *jm_program_own(jm_program *prog, const Py_UCS4 *buf, Py_ssize_t len);

/* Parse src[0..len) into a program. Returns NULL on a syntax error (a NUL-terminated
   message is written into errbuf, capacity errlen) or allocation failure (errbuf
   empty). The source must outlive the program (nodes borrow its code points). */
jm_program *jm_parse(const Py_UCS4 *src, Py_ssize_t len, char *errbuf, size_t errlen);

void jm_program_free(jm_program *prog);

/* Resolve every identifier to its binding and rename local bindings to short names,
   in place (sets jm_sym.mangled, which the printer emits). Safe by construction: a
   binding is only renamed when its whole subtree is statically resolvable and the new
   name cannot capture a free name or shadow an enclosing binding that is referenced.
   A no-op (leaves names unchanged) on allocation failure. */
/* Peephole-fold the AST into shorter equivalent forms (e.g. true -> !0) in place,
   before mangling. Value-exact; a no-op on allocation failure. */
int jm_fold(jm_program *prog); /* 1 when any transform fired: the tree needs another look */

/* Run one compression pass: resolve every binding, then drop dead ones, inline single-use values, and
   collapse assignment temporaries. Returns 1 if the tree changed (run again), 0 at a fixpoint, -1 when
   with/eval or an allocation makes it unusable. Interleave with jm_fold until it returns <= 0. */
int jm_compress(jm_program *prog);

void jm_mangle(jm_program *prog);

/* Append a scope (parent, kind 0 block / 1 function / 2 catch / 3 with) or a symbol
   declared in scope; return the index, or -1 on OOM. Used by the mangler. */
int32_t jm_scope_new(jm_program *prog, int32_t parent, uint8_t kind);
int32_t jm_sym_new(jm_program *prog, const Py_UCS4 *name, Py_ssize_t name_len, int32_t scope, uint8_t decl);

/* Append a node of the given kind, returning its index or -1 on OOM. All fields but
   kind are zeroed except a/b/c/d/next/sym which are set to -1. */
int32_t jm_node_new(jm_program *prog, jm_kind kind);

/* Render the AST as a canonical S-expression (code points; *out_len receives the
   length), the form the parser tests diff against. PyMem-allocated; NULL on OOM. */
Py_UCS4 *jm_dump(const jm_program *prog, Py_ssize_t *out_len);

/* Print the program as minified code into a freshly PyMem-allocated buffer
   (*out_len receives the length). NULL on OOM. Phase 3: whitespace/semicolons. */
Py_UCS4 *jm_print(const jm_program *prog, Py_ssize_t *out_len);

#endif /* TURBOHTML_SERIALIZE_JS_INTERNAL_H */
