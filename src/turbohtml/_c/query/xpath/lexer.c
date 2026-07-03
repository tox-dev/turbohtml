/* Turns an XPath expression string into the token stream the parser consumes, carrying
   XPath's context-sensitive disambiguation of operator names and the '*' wildcard. */

#include "core/common.h"
#include "query/xpath/internal.h"
#include "query/xpath/xpath.h"

static int xp_is_name_start(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || ch == '_' || ch >= 0x80;
}

static int xp_is_name_char(Py_UCS4 ch) {
    return xp_is_name_start(ch) || (ch >= '0' && ch <= '9') || ch == '-' || ch == '.';
}

int xp_name_eq(const lexer *lx, const char *kw) {
    Py_ssize_t index = 0;
    for (; kw[index] != '\0'; index++) {
        if (index >= lx->tlen || lx->tstart[index] != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return index == lx->tlen;
}

/* Whether the just-produced token means the next `*`/NCName sits in operator
   position (XPath's special tokenization rule). */
static int xp_op_follows(tok_kind kind) {
    switch (kind) {
    case TK_AT:
    case TK_DOLLAR:
    case TK_COLONCOLON:
    case TK_LPAREN:
    case TK_LBRACK:
    case TK_COMMA:
    case TK_SLASH:
    case TK_DSLASH:
    case TK_PIPE:
    case TK_PLUS:
    case TK_MINUS:
    case TK_STAR:
    case TK_EQ:
    case TK_NE:
    case TK_LT:
    case TK_LE:
    case TK_GT:
    case TK_GE:
    case TK_AND:
    case TK_OR:
    case TK_DIV:
    case TK_MOD:
        return 0; /* after these, the next token is in value (non-operator) position */
    default:
        return 1; /* after a name/number/literal/) /] /. /.. the next is operator position */
    }
}

void lex_next(lexer *lx) {
    while (lx->pos < lx->len && xp_is_space(lx->src[lx->pos])) {
        lx->pos++;
    }
    int prev_op = lx->op_context;
    lx->tokpos = lx->pos;
    if (lx->pos >= lx->len) {
        lx->kind = TK_EOF;
        return;
    }
    Py_UCS4 ch = lx->src[lx->pos];
    Py_UCS4 peek = lx->pos + 1 < lx->len ? lx->src[lx->pos + 1] : 0;
    switch (ch) {
    case '/':
        lx->pos += peek == '/' ? 2 : 1;
        lx->kind = peek == '/' ? TK_DSLASH : TK_SLASH;
        break;
    case '[':
        lx->pos++;
        lx->kind = TK_LBRACK;
        break;
    case ']':
        lx->pos++;
        lx->kind = TK_RBRACK;
        break;
    case '(':
        lx->pos++;
        lx->kind = TK_LPAREN;
        break;
    case ')':
        lx->pos++;
        lx->kind = TK_RPAREN;
        break;
    case '@':
        lx->pos++;
        lx->kind = TK_AT;
        break;
    case '$':
        lx->pos++;
        lx->kind = TK_DOLLAR;
        break;
    case ',':
        lx->pos++;
        lx->kind = TK_COMMA;
        break;
    case '|':
        lx->pos++;
        lx->kind = TK_PIPE;
        break;
    case '+':
        lx->pos++;
        lx->kind = TK_PLUS;
        break;
    case '-':
        lx->pos++;
        lx->kind = TK_MINUS;
        break;
    case '*':
        lx->pos++;
        lx->kind = TK_STAR;
        break;
    case '=':
        lx->pos++;
        lx->kind = TK_EQ;
        break;
    case '!':
        if (peek == '=') {
            lx->pos += 2;
            lx->kind = TK_NE;
        } else {
            lx->error = 1;
            lx->kind = TK_EOF;
        }
        break;
    case '<':
        lx->pos += peek == '=' ? 2 : 1;
        lx->kind = peek == '=' ? TK_LE : TK_LT;
        break;
    case '>':
        lx->pos += peek == '=' ? 2 : 1;
        lx->kind = peek == '=' ? TK_GE : TK_GT;
        break;
    case ':':
        if (peek == ':') {
            lx->pos += 2;
            lx->kind = TK_COLONCOLON;
        } else {
            lx->error = 1;
            lx->kind = TK_EOF;
        }
        break;
    case '"':
    case '\'': {
        Py_ssize_t start = ++lx->pos;
        while (lx->pos < lx->len && lx->src[lx->pos] != ch) {
            lx->pos++;
        }
        if (lx->pos >= lx->len) {
            lx->error = 1;
            lx->kind = TK_EOF;
            break;
        }
        lx->tstart = lx->src + start;
        lx->tlen = lx->pos - start;
        lx->pos++; /* closing quote */
        lx->kind = TK_LITERAL;
        break;
    }
    case '.':
        if (peek == '.') {
            lx->pos += 2;
            lx->kind = TK_DOTDOT;
        } else if (peek >= '0' && peek <= '9') {
            goto number;
        } else {
            lx->pos++;
            lx->kind = TK_DOT;
        }
        break;
    default:
        if (ch >= '0' && ch <= '9') {
            goto number;
        }
        if (xp_is_name_start(ch)) {
            Py_ssize_t start = lx->pos;
            while (lx->pos < lx->len && xp_is_name_char(lx->src[lx->pos])) {
                lx->pos++;
            }
            lx->tprefix = 0;
            /* a prefixed QName "prefix:local"; the '::' axis separator is excluded */
            if (lx->pos + 1 < lx->len && lx->src[lx->pos] == ':' && lx->src[lx->pos + 1] != ':' &&
                xp_is_name_start(lx->src[lx->pos + 1])) {
                lx->tprefix = lx->pos - start;
                lx->pos++; /* the ':' */
                while (lx->pos < lx->len && xp_is_name_char(lx->src[lx->pos])) {
                    lx->pos++;
                }
            }
            lx->tstart = lx->src + start;
            lx->tlen = lx->pos - start;
            lx->kind = TK_NAME;
            if (prev_op) {
                if (xp_name_eq(lx, "and")) {
                    lx->kind = TK_AND;
                } else if (xp_name_eq(lx, "or")) {
                    lx->kind = TK_OR;
                } else if (xp_name_eq(lx, "div")) {
                    lx->kind = TK_DIV;
                } else if (xp_name_eq(lx, "mod")) {
                    lx->kind = TK_MOD;
                }
            }
        } else {
            lx->error = 1;
            lx->kind = TK_EOF;
        }
        break;
    number: {
        Py_ssize_t start = lx->pos;
        while (lx->pos < lx->len && lx->src[lx->pos] >= '0' && lx->src[lx->pos] <= '9') {
            lx->pos++;
        }
        if (lx->pos < lx->len && lx->src[lx->pos] == '.') {
            lx->pos++;
            while (lx->pos < lx->len && lx->src[lx->pos] >= '0' && lx->src[lx->pos] <= '9') {
                lx->pos++;
            }
        }
        double value = 0.0;
        double frac = 0.0;
        double scale = 1.0;
        int after_dot = 0;
        for (Py_ssize_t index = start; index < lx->pos; index++) {
            Py_UCS4 ch = lx->src[index];
            if (ch == '.') {
                after_dot = 1;
            } else if (!after_dot) {
                value = value * 10.0 + (ch - '0');
            } else {
                scale *= 10.0;
                frac += (ch - '0') / scale;
            }
        }
        lx->num = value + frac;
        lx->kind = TK_NUM;
        break;
    }
    }
    lx->op_context = xp_op_follows(lx->kind);
}
