/* Turns a JavaScript source string into the token stream the parser consumes.

   The one structural rule the lexer cannot resolve on its own is whether a `/`
   begins a regular-expression literal or is the division operator: that depends on
   the grammar position only the parser knows. So `/` always lexes to JT_DIV /
   JT_DIV_ASSIGN and a `}` to JT_RBRACE, and the parser rewinds and re-reads them as
   a regex (jm_lex_rescan_regex) or a template continuation (jm_lex_rescan_template)
   when its position calls for it - the same disambiguation the XPath lexer does for
   `*` and the operator names. Identifier/whitespace classification follows the XPath
   convention of treating every code point >= 0x80 as an identifier character, which
   is a safe over-approximation for the valid scripts a minifier is handed (all JS
   operators and punctuation are ASCII). */

#include "core/ascii.h"
#include "js/internal.h"

#include <string.h>

/* The hot scanners classify every code point of the source, so the ASCII range (where
   nearly all of a script's bytes live) is resolved with a single table load and mask
   instead of a chain of range compares; code points >= 0x80 fall back to the Unicode
   rules below. CC_ID is the identifier-start set, CC_ID|CC_DIGIT the identifier-part. */
enum { CC_ID = 1, CC_DIGIT = 2, CC_WS = 4, CC_LT = 8, CC_HEX = 16 };

static const uint8_t jm_cc[128] = {
    0, 0,  0,  0,  0,  0,  0,  0, 0, 4, 8, 4, 4, 8, 0, 0, 0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0, 0, 0, 0, 0, 0,
    4, 0,  0,  0,  1,  0,  0,  0, 0, 0, 0, 0, 0, 0, 0, 0, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 0, 0, 0, 0, 0, 0,
    0, 17, 17, 17, 17, 17, 17, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1, 0, 0, 0, 0, 1,
    0, 17, 17, 17, 17, 17, 17, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1, 0, 0, 0, 0, 0,
};

/* Line terminators per ECMAScript: LF, CR, LINE SEPARATOR, PARAGRAPH SEPARATOR. */
static int jm_is_line_term(Py_UCS4 ch) {
    if (ch < 0x80) {
        return jm_cc[ch] & CC_LT;
    }
    return ch == 0x2028 || ch == 0x2029;
}

/* White space that separates tokens without ending a line: the ASCII set, the
   no-break space, the byte-order mark, and the Unicode Space_Separator (Zs) code
   points. These all sit at or above 0x80 (bar the ASCII ones), so the identifier
   over-approximation below must exclude them or `a b` would lex as one name. */
static int jm_is_ws(Py_UCS4 ch) {
    if (ch < 0x80) {
        return jm_cc[ch] & CC_WS;
    }
    return ch == 0xA0 || ch == 0xFEFF || ch == 0x1680 || (ch >= 0x2000 && ch <= 0x200A) || ch == 0x202F ||
           ch == 0x205F || ch == 0x3000;
}

/* The valid scripts a minifier is handed never put an operator outside ASCII, so
   any code point >= 0x80 is taken as an identifier character - except the white
   space and line terminators above 0x80, which must keep separating tokens. */
static int jm_is_id_start(Py_UCS4 ch) {
    if (ch < 0x80) {
        return jm_cc[ch] & CC_ID;
    }
    return !jm_is_ws(ch) && !jm_is_line_term(ch);
}

static int jm_is_id_part(Py_UCS4 ch) {
    if (ch < 0x80) {
        return jm_cc[ch] & (CC_ID | CC_DIGIT);
    }
    return jm_is_id_start(ch); /* for a code point >= 0x80, identifier-part == identifier-start */
}

void jm_lex_init(jm_lexer *lx, const Py_UCS4 *src, Py_ssize_t len) {
    lx->src = src;
    lx->len = len;
    lx->pos = 0;
    lx->start = 0;
    lx->kind = JT_EOF;
    lx->text = src;
    lx->text_len = 0;
    lx->newline_before = 0;
    lx->sink = NULL;
    lx->comment_count = 0;
    lx->error = 0;
}

int jm_text_eq(const jm_lexer *lx, const char *keyword) {
    Py_ssize_t index = 0;
    for (; keyword[index] != '\0'; index++) {
        if (index >= lx->text_len || lx->text[index] != (Py_UCS4)(unsigned char)keyword[index]) {
            return 0;
        }
    }
    return index == lx->text_len;
}

/* Whether body[0..body_len) contains needle (an ASCII literal) as a substring. */
static int jm_body_has(const Py_UCS4 *body, Py_ssize_t body_len, const char *needle) {
    Py_ssize_t needle_len = (Py_ssize_t)strlen(needle);
    for (Py_ssize_t index = 0; index + needle_len <= body_len; index++) {
        Py_ssize_t match = 0;
        while (match < needle_len && body[index + match] == (Py_UCS4)(unsigned char)needle[match]) {
            match++;
        }
        if (match == needle_len) {
            return 1;
        }
    }
    return 0;
}

/* Whether a closed block comment (text spans the whole comment including both delimiters, so len >= 4 and
   text[2] is the first body code point) is a license/banner comment kept through minification: a body that
   opens with `!` -- the bang marker the CSS minifier keeps byte-exact -- or an @license / @preserve
   annotation anywhere in the body. */
static int jm_comment_is_kept(const Py_UCS4 *text, Py_ssize_t len) {
    if (text[2] == '!') {
        return 1;
    }
    const Py_UCS4 *body = text + 2;
    Py_ssize_t body_len = len - 4; /* strip the two-code-point delimiters at each end */
    return jm_body_has(body, body_len, "@license") || jm_body_has(body, body_len, "@preserve");
}

/* Record a kept comment into the program sink so the printer can re-emit it. Discards ordinary comments
   and does nothing on the token-dump path (sink == NULL). The count lives on the lexer so a speculative
   backtrack rewinds it, and a re-scan overwrites the same slots -- the committed run is left correct. */
static void jm_capture_comment(jm_lexer *lx, const Py_UCS4 *text, Py_ssize_t len) {
    if (lx->sink == NULL || !jm_comment_is_kept(text, len)) {
        return;
    }
    struct jm_program *prog = lx->sink;
    if (lx->comment_count == prog->comment_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)prog->comment_cap + 1, (size_t)prog->comment_cap, 4, sizeof(jm_comment), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            prog->failed = 1; /* GCOVR_EXCL_LINE */
            return;           /* GCOVR_EXCL_LINE */
        }
        jm_comment *grown = jm_realloc(prog->comments, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            prog->failed = 1; /* GCOVR_EXCL_LINE */
            return;           /* GCOVR_EXCL_LINE */
        }
        prog->comments = grown;
        prog->comment_cap = (int32_t)cap;
    }
    prog->comments[lx->comment_count].text = text;
    prog->comments[lx->comment_count].len = len;
    lx->comment_count++;
}

/* Consume white space and comments, recording in lx->newline_before whether any
   line terminator was crossed (a block comment counts when it spans one): the
   parser turns that flag into automatic-semicolon-insertion decisions. */
static void jm_skip_trivia(jm_lexer *lx) {
    lx->newline_before = 0;
    while (lx->pos < lx->len) {
        Py_UCS4 ch = lx->src[lx->pos];
        if (jm_is_line_term(ch)) {
            lx->newline_before = 1;
            lx->pos++;
        } else if (jm_is_ws(ch)) {
            lx->pos++;
        } else if (ch == '/' && lx->pos + 1 < lx->len && lx->src[lx->pos + 1] == '/') {
            lx->pos += 2;
            while (lx->pos < lx->len && !jm_is_line_term(lx->src[lx->pos])) {
                lx->pos++;
            }
        } else if (ch == '/' && lx->pos + 1 < lx->len && lx->src[lx->pos + 1] == '*') {
            Py_ssize_t comment_start = lx->pos;
            lx->pos += 2;
            while (lx->pos < lx->len &&
                   !(lx->src[lx->pos] == '*' && lx->pos + 1 < lx->len && lx->src[lx->pos + 1] == '/')) {
                if (jm_is_line_term(lx->src[lx->pos])) {
                    lx->newline_before = 1;
                }
                lx->pos++;
            }
            if (lx->pos < lx->len) {
                lx->pos += 2; /* past the closing star-slash */
                jm_capture_comment(lx, lx->src + comment_start, lx->pos - comment_start);
            } else {
                lx->error = 1; /* unterminated block comment */
                return;
            }
        } else if (ch == '#' && lx->pos == 0 && lx->pos + 1 < lx->len && lx->src[lx->pos + 1] == '!') {
            while (lx->pos < lx->len && !jm_is_line_term(lx->src[lx->pos])) {
                lx->pos++;
            }
        } else {
            return;
        }
    }
}

/* Finish a value-bearing token: record its lexeme span and kind. */
static void jm_emit(jm_lexer *lx, jm_tok kind) {
    lx->kind = kind;
    lx->text = lx->src + lx->start;
    lx->text_len = lx->pos - lx->start;
}

static void jm_fail(jm_lexer *lx) {
    lx->error = 1;
    lx->kind = JT_ERROR;
    lx->text = lx->src + lx->start;
    lx->text_len = lx->pos - lx->start;
}

static void jm_scan_ident(jm_lexer *lx) {
    while (lx->pos < lx->len) {
        Py_UCS4 ch = lx->src[lx->pos];
        if (jm_is_id_part(ch)) {
            lx->pos++;
        } else if (ch == '\\' && lx->pos + 1 < lx->len && lx->src[lx->pos + 1] == 'u') {
            lx->pos += 2;
            if (lx->pos < lx->len && lx->src[lx->pos] == '{') { /* a \u{...} braced escape */
                while (lx->pos < lx->len && lx->src[lx->pos] != '}') {
                    lx->pos++;
                }
                if (lx->pos < lx->len) {
                    lx->pos++;
                }
            }
            /* a \uXXXX escape's four hex digits are identifier-part and consumed above */
        } else {
            break;
        }
    }
    jm_emit(lx, JT_IDENT);
}

/* Consume a run of digits in the given radix, allowing the `_` separator between
   them. Returns the number of digits seen (separators excluded). */
static Py_ssize_t jm_scan_digits(jm_lexer *lx, int hex) {
    Py_ssize_t digits = 0;
    while (lx->pos < lx->len) {
        Py_UCS4 ch = lx->src[lx->pos];
        if (hex ? is_ascii_hexdigit(ch) : is_ascii_digit(ch)) {
            digits++;
            lx->pos++;
        } else if (ch == '_') {
            lx->pos++;
        } else {
            break;
        }
    }
    return digits;
}

static void jm_scan_number(jm_lexer *lx) {
    Py_UCS4 ch = lx->src[lx->start];
    if (ch == '0' && lx->pos + 1 < lx->len) {
        Py_UCS4 radix = lx->src[lx->start + 1];
        if (radix == 'x' || radix == 'X' || radix == 'o' || radix == 'O' || radix == 'b' || radix == 'B') {
            lx->pos += 2;
            jm_scan_digits(lx, radix == 'x' || radix == 'X');
            if (lx->pos < lx->len && lx->src[lx->pos] == 'n') {
                lx->pos++;
                jm_emit(lx, JT_BIGINT);
                return;
            }
            jm_emit(lx, JT_NUM);
            return;
        }
    }
    /* a legacy octal (010) or non-octal decimal (08) integer: a leading 0 then a
       decimal digit. These take no fraction or exponent, so a following `.` is a
       member access, not a decimal point. */
    if (ch == '0' && lx->pos + 1 < lx->len && is_ascii_digit(lx->src[lx->start + 1])) {
        jm_scan_digits(lx, 0);
        jm_emit(lx, JT_NUM);
        return;
    }
    int is_int = 1;
    if (ch == '.') {
        is_int = 0;
        lx->pos++; /* consume the leading dot before its fractional digits */
        jm_scan_digits(lx, 0);
    } else {
        jm_scan_digits(lx, 0);
        if (lx->pos < lx->len && lx->src[lx->pos] == '.') {
            is_int = 0;
            lx->pos++;
            jm_scan_digits(lx, 0);
        }
    }
    if (lx->pos < lx->len && (lx->src[lx->pos] == 'e' || lx->src[lx->pos] == 'E')) {
        is_int = 0;
        lx->pos++;
        if (lx->pos < lx->len && (lx->src[lx->pos] == '+' || lx->src[lx->pos] == '-')) {
            lx->pos++;
        }
        jm_scan_digits(lx, 0);
    }
    if (is_int && lx->pos < lx->len && lx->src[lx->pos] == 'n') {
        lx->pos++;
        jm_emit(lx, JT_BIGINT);
        return;
    }
    jm_emit(lx, JT_NUM);
}

static void jm_scan_string(jm_lexer *lx, Py_UCS4 quote) {
    lx->pos++; /* opening quote */
    while (lx->pos < lx->len) {
        Py_UCS4 ch = lx->src[lx->pos];
        if (ch == quote) {
            lx->pos++;
            jm_emit(lx, JT_STRING);
            return;
        }
        if (ch == '\\') {
            /* a `\`+<CR><LF> LineContinuation is one unit (ECMA-262 §12.3): consume all three so
               the trailing LF is not left to read as an unescaped newline. Every other escape (and
               a lone LF/CR/LS/PS continuation) is two code points and opaque here. */
            if (lx->pos + 2 < lx->len && lx->src[lx->pos + 1] == '\r' && lx->src[lx->pos + 2] == '\n') {
                lx->pos += 3;
            } else {
                lx->pos += 2;
            }
            continue;
        }
        if (jm_is_line_term(ch)) {
            break; /* an unescaped newline is illegal in a string literal */
        }
        lx->pos++;
    }
    jm_fail(lx);
}

/* Scan a template body from lx->pos (just past a `` ` `` or a `}`) to the next `` ` ``
   or `${`. is_head selects the token names for a body that opens with `` ` ``
   (JT_TEMPLATE / JT_TEMPLATE_HEAD) versus one that resumes after a `}`
   (JT_TEMPLATE_TAIL / JT_TEMPLATE_MIDDLE). */
static void jm_scan_template_body(jm_lexer *lx, int is_head) {
    while (lx->pos < lx->len) {
        Py_UCS4 ch = lx->src[lx->pos];
        if (ch == '`') {
            lx->pos++;
            jm_emit(lx, is_head ? JT_TEMPLATE : JT_TEMPLATE_TAIL);
            return;
        }
        if (ch == '\\') {
            lx->pos += 2;
            continue;
        }
        if (ch == '$' && lx->pos + 1 < lx->len && lx->src[lx->pos + 1] == '{') {
            lx->pos += 2;
            jm_emit(lx, is_head ? JT_TEMPLATE_HEAD : JT_TEMPLATE_MIDDLE);
            return;
        }
        lx->pos++;
    }
    jm_fail(lx);
}

/* Read a one-, two- or three-byte operator starting at lx->pos. Each arm peeks the
   following code points only as far as the longest operator that begins with the
   current one, so e.g. `>` walks `>` -> `>>` -> `>>>` -> `>>>=`. */
static void jm_scan_punct(jm_lexer *lx) {
    Py_UCS4 ch = lx->src[lx->pos];
    Py_UCS4 next_ch = lx->pos + 1 < lx->len ? lx->src[lx->pos + 1] : 0;
    Py_UCS4 next2_ch = lx->pos + 2 < lx->len ? lx->src[lx->pos + 2] : 0;
    switch (ch) {
    case '{':
        lx->pos++;
        lx->kind = JT_LBRACE;
        return;
    case '}':
        lx->pos++;
        lx->kind = JT_RBRACE;
        return;
    case '(':
        lx->pos++;
        lx->kind = JT_LPAREN;
        return;
    case ')':
        lx->pos++;
        lx->kind = JT_RPAREN;
        return;
    case '[':
        lx->pos++;
        lx->kind = JT_LBRACK;
        return;
    case ']':
        lx->pos++;
        lx->kind = JT_RBRACK;
        return;
    case ';':
        lx->pos++;
        lx->kind = JT_SEMI;
        return;
    case ',':
        lx->pos++;
        lx->kind = JT_COMMA;
        return;
    case ':':
        lx->pos++;
        lx->kind = JT_COLON;
        return;
    case '~':
        lx->pos++;
        lx->kind = JT_BIT_NOT;
        return;
    case '.':
        if (next_ch == '.' && next2_ch == '.') {
            lx->pos += 3;
            lx->kind = JT_ELLIPSIS;
            return;
        }
        lx->pos++;
        lx->kind = JT_DOT;
        return;
    case '?':
        if (next_ch == '?') {
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_NULLISH_ASSIGN;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_NULLISH;
            return;
        }
        if (next_ch == '.' && !is_ascii_digit(next2_ch)) {
            lx->pos += 2;
            lx->kind = JT_OPT_CHAIN;
            return;
        }
        lx->pos++;
        lx->kind = JT_QUESTION;
        return;
    case '<':
        if (next_ch == '<') {
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_SHL_ASSIGN;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_SHL;
            return;
        }
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_LE;
            return;
        }
        lx->pos++;
        lx->kind = JT_LT;
        return;
    case '>':
        if (next_ch == '>') {
            if (next2_ch == '>') {
                if (lx->pos + 3 < lx->len && lx->src[lx->pos + 3] == '=') {
                    lx->pos += 4;
                    lx->kind = JT_USHR_ASSIGN;
                    return;
                }
                lx->pos += 3;
                lx->kind = JT_USHR;
                return;
            }
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_SHR_ASSIGN;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_SHR;
            return;
        }
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_GE;
            return;
        }
        lx->pos++;
        lx->kind = JT_GT;
        return;
    case '=':
        if (next_ch == '=') {
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_EQ_EQ_EQ;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_EQ_EQ;
            return;
        }
        if (next_ch == '>') {
            lx->pos += 2;
            lx->kind = JT_ARROW;
            return;
        }
        lx->pos++;
        lx->kind = JT_ASSIGN;
        return;
    case '!':
        if (next_ch == '=') {
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_NE_EQ;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_NE;
            return;
        }
        lx->pos++;
        lx->kind = JT_NOT;
        return;
    case '+':
        if (next_ch == '+') {
            lx->pos += 2;
            lx->kind = JT_INC;
            return;
        }
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_PLUS_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_PLUS;
        return;
    case '-':
        if (next_ch == '-') {
            lx->pos += 2;
            lx->kind = JT_DEC;
            return;
        }
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_MINUS_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_MINUS;
        return;
    case '*':
        if (next_ch == '*') {
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_POW_ASSIGN;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_POW;
            return;
        }
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_STAR_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_STAR;
        return;
    case '/':
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_DIV_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_DIV;
        return;
    case '%':
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_MOD_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_MOD;
        return;
    case '^':
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_XOR_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_BIT_XOR;
        return;
    case '&':
        if (next_ch == '&') {
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_LAND_ASSIGN;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_AND;
            return;
        }
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_AND_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_BIT_AND;
        return;
    case '|':
        if (next_ch == '|') {
            if (next2_ch == '=') {
                lx->pos += 3;
                lx->kind = JT_LOR_ASSIGN;
                return;
            }
            lx->pos += 2;
            lx->kind = JT_OR;
            return;
        }
        if (next_ch == '=') {
            lx->pos += 2;
            lx->kind = JT_OR_ASSIGN;
            return;
        }
        lx->pos++;
        lx->kind = JT_BIT_OR;
        return;
    default:
        lx->pos++;
        jm_fail(lx);
        return; /* report the stray byte as the error lexeme */
    }
}

void jm_lex_next(jm_lexer *lx) {
    jm_skip_trivia(lx);
    if (lx->error) {
        jm_fail(lx);
        return;
    }
    lx->start = lx->pos;
    if (lx->pos >= lx->len) {
        lx->kind = JT_EOF;
        lx->text = lx->src + lx->pos;
        lx->text_len = 0;
        return;
    }
    Py_UCS4 ch = lx->src[lx->pos];
    if (jm_is_id_start(ch) || (ch == '\\' && lx->pos + 1 < lx->len && lx->src[lx->pos + 1] == 'u')) {
        jm_scan_ident(lx);
    } else if (is_ascii_digit(ch) || (ch == '.' && lx->pos + 1 < lx->len && is_ascii_digit(lx->src[lx->pos + 1]))) {
        jm_scan_number(lx);
    } else if (ch == '"' || ch == '\'') {
        jm_scan_string(lx, ch);
    } else if (ch == '`') {
        lx->pos++;
        jm_scan_template_body(lx, 1);
    } else if (ch == '#') {
        lx->pos++;
        jm_scan_ident(lx);
        lx->kind = JT_PRIVATE;
        lx->text = lx->src + lx->start;
        lx->text_len = lx->pos - lx->start;
    } else {
        jm_scan_punct(lx);
    }
}

void jm_lex_rescan_regex(jm_lexer *lx) {
    lx->pos = lx->start + 1; /* past the `/` */
    int in_class = 0;
    while (lx->pos < lx->len) {
        Py_UCS4 ch = lx->src[lx->pos];
        if (jm_is_line_term(ch)) {
            break; /* a regex literal may not span a line */
        }
        if (ch == '\\' && lx->pos + 1 < lx->len) {
            lx->pos += 2;
            continue;
        }
        if (ch == '[') {
            in_class = 1;
        } else if (ch == ']') {
            in_class = 0;
        } else if (ch == '/' && !in_class) {
            lx->pos++;
            while (lx->pos < lx->len && jm_is_id_part(lx->src[lx->pos])) {
                lx->pos++; /* flags */
            }
            jm_emit(lx, JT_REGEX);
            return;
        }
        lx->pos++;
    }
    jm_fail(lx);
}

void jm_lex_rescan_template(jm_lexer *lx) {
    lx->pos = lx->start + 1; /* past the `}` */
    jm_scan_template_body(lx, 0);
}

const char *jm_tok_name(jm_tok kind) {
    switch (kind) { /* GCOVR_EXCL_BR_LINE: exhaustive over jm_tok; the default is unreachable */
    case JT_EOF:
        return "EOF";
    case JT_ERROR:
        return "ERROR";
    case JT_IDENT:
        return "IDENT";
    case JT_PRIVATE:
        return "PRIVATE";
    case JT_NUM:
        return "NUM";
    case JT_BIGINT:
        return "BIGINT";
    case JT_STRING:
        return "STRING";
    case JT_REGEX:
        return "REGEX";
    case JT_TEMPLATE:
        return "TEMPLATE";
    case JT_TEMPLATE_HEAD:
        return "TEMPLATE_HEAD";
    case JT_TEMPLATE_MIDDLE:
        return "TEMPLATE_MIDDLE";
    case JT_TEMPLATE_TAIL:
        return "TEMPLATE_TAIL";
    case JT_LBRACE:
        return "LBRACE";
    case JT_RBRACE:
        return "RBRACE";
    case JT_LPAREN:
        return "LPAREN";
    case JT_RPAREN:
        return "RPAREN";
    case JT_LBRACK:
        return "LBRACK";
    case JT_RBRACK:
        return "RBRACK";
    case JT_DOT:
        return "DOT";
    case JT_ELLIPSIS:
        return "ELLIPSIS";
    case JT_SEMI:
        return "SEMI";
    case JT_COMMA:
        return "COMMA";
    case JT_ARROW:
        return "ARROW";
    case JT_OPT_CHAIN:
        return "OPT_CHAIN";
    case JT_QUESTION:
        return "QUESTION";
    case JT_COLON:
        return "COLON";
    case JT_LT:
        return "LT";
    case JT_GT:
        return "GT";
    case JT_LE:
        return "LE";
    case JT_GE:
        return "GE";
    case JT_EQ_EQ:
        return "EQ_EQ";
    case JT_NE:
        return "NE";
    case JT_EQ_EQ_EQ:
        return "EQ_EQ_EQ";
    case JT_NE_EQ:
        return "NE_EQ";
    case JT_PLUS:
        return "PLUS";
    case JT_MINUS:
        return "MINUS";
    case JT_STAR:
        return "STAR";
    case JT_DIV:
        return "DIV";
    case JT_MOD:
        return "MOD";
    case JT_POW:
        return "POW";
    case JT_INC:
        return "INC";
    case JT_DEC:
        return "DEC";
    case JT_SHL:
        return "SHL";
    case JT_SHR:
        return "SHR";
    case JT_USHR:
        return "USHR";
    case JT_BIT_AND:
        return "BIT_AND";
    case JT_BIT_OR:
        return "BIT_OR";
    case JT_BIT_XOR:
        return "BIT_XOR";
    case JT_NOT:
        return "NOT";
    case JT_BIT_NOT:
        return "BIT_NOT";
    case JT_AND:
        return "AND";
    case JT_OR:
        return "OR";
    case JT_NULLISH:
        return "NULLISH";
    case JT_ASSIGN:
        return "ASSIGN";
    case JT_PLUS_ASSIGN:
        return "PLUS_ASSIGN";
    case JT_MINUS_ASSIGN:
        return "MINUS_ASSIGN";
    case JT_STAR_ASSIGN:
        return "STAR_ASSIGN";
    case JT_DIV_ASSIGN:
        return "DIV_ASSIGN";
    case JT_MOD_ASSIGN:
        return "MOD_ASSIGN";
    case JT_POW_ASSIGN:
        return "POW_ASSIGN";
    case JT_SHL_ASSIGN:
        return "SHL_ASSIGN";
    case JT_SHR_ASSIGN:
        return "SHR_ASSIGN";
    case JT_USHR_ASSIGN:
        return "USHR_ASSIGN";
    case JT_AND_ASSIGN:
        return "AND_ASSIGN";
    case JT_OR_ASSIGN:
        return "OR_ASSIGN";
    case JT_XOR_ASSIGN:
        return "XOR_ASSIGN";
    case JT_LAND_ASSIGN:
        return "LAND_ASSIGN";
    case JT_LOR_ASSIGN:
        return "LOR_ASSIGN";
    case JT_NULLISH_ASSIGN:
        return "NULLISH_ASSIGN";
    }
    return "?"; /* GCOVR_EXCL_LINE: the switch is exhaustive over jm_tok */
}
