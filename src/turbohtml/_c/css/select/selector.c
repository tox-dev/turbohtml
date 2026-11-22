/* The compiled CSS selector engine. Split out of selector.h (spec-tracked in issue #478):
   the matcher and recursive-descent parser live here as one translation unit, and
   selector.h now carries only the shared types plus the entry-point prototypes. */

#include "core/common.h"
#include "core/stack.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "css/select/selector.h"

#include <limits.h>

/* Record a parse failure with the position and reason for the error message. Set
   unconditionally: an error propagates up through early returns without another
   failure being recorded, so the first (deepest) call keeps its position. A NULL
   reason marks an allocation failure. sel_raise reports it as MemoryError, and a
   forgiving :is()/:where() list stops on it, since dropping the arm would match the
   document against a partial list. */
static void sel_fail(sel_parser *parser, const char *reason) {
    parser->error = 1;
    parser->err_pos = parser->pos;
    parser->err_reason = reason;
}

int sel_is_ident(Py_UCS4 ch) {
    return is_ascii_alpha(ch) || is_ascii_digit(ch) || ch == '-' || ch == '_' || ch >= 0x80;
}

/* Skip whitespace and CSS comments (CSS Syntax §4.3.2: a comment is valid anywhere
   whitespace is). An unterminated comment runs to end of input rather than erroring,
   as the spec's consume-comments step treats a file-ending comment as complete. */
static void sel_skip_ws(sel_parser *parser) {
    while (parser->pos < parser->len) {
        if (is_space(parser->src[parser->pos])) {
            parser->pos++;
        } else if (parser->src[parser->pos] == '/' && parser->pos + 1 < parser->len &&
                   parser->src[parser->pos + 1] == '*') {
            parser->pos += 2;
            while (parser->pos + 1 < parser->len &&
                   !(parser->src[parser->pos] == '*' && parser->src[parser->pos + 1] == '/')) {
                parser->pos++;
            }
            parser->pos = parser->pos + 1 < parser->len ? parser->pos + 2 : parser->len;
        } else {
            break;
        }
    }
}

/* Whether the backslash at parser->pos begins a valid escape: a backslash is
   one unless it precedes a CSS newline (LF/CR/FF); at end of input it still
   escapes the U+FFFD replacement character. */
static int sel_starts_escape(const sel_parser *parser) {
    if (parser->pos + 1 >= parser->len) {
        return 1;
    }
    Py_UCS4 next = parser->src[parser->pos + 1];
    return next != '\n' && next != '\r' && next != '\x0c';
}

/* Consume an escaped code point (CSS Syntax 4.3.7); the leading backslash at
   parser->pos is assumed to start a valid escape and is consumed here. */
static Py_UCS4 sel_consume_escape(sel_parser *parser) {
    parser->pos++; /* the backslash */
    if (parser->pos >= parser->len) {
        return 0xFFFD; /* a trailing backslash escapes the replacement character */
    }
    if (!is_ascii_hexdigit(parser->src[parser->pos])) {
        return parser->src[parser->pos++]; /* the literal escaped character */
    }
    uint32_t value = 0;
    for (int digit = 0; digit < 6 && parser->pos < parser->len && is_ascii_hexdigit(parser->src[parser->pos]);
         digit++) {
        Py_UCS4 hex = parser->src[parser->pos++];
        value = value * 16 + (hex <= '9' ? (uint32_t)(hex - '0') : (uint32_t)((hex | 32) - 'a' + 10));
    }
    if (parser->pos < parser->len && is_space(parser->src[parser->pos])) {
        parser->pos++; /* one trailing whitespace closes the hex escape */
    }
    if (value == 0 || value > 0x10FFFF || (value >= 0xD800 && value <= 0xDFFF)) {
        return 0xFFFD; /* null, surrogate, and out-of-range escapes fold to U+FFFD */
    }
    return (Py_UCS4)value;
}

/* Read an identifier run, decoding any CSS escapes in place (a decoded run is
   never longer than its source), and return its slice via *out / *out_len. */
static void sel_ident(sel_parser *parser, const Py_UCS4 **out, Py_ssize_t *out_len) {
    Py_ssize_t start = parser->pos;
    Py_ssize_t write = start;
    /* CSS Syntax §4.3.9 "would start an identifier": a leading '-' begins one only when
       a second '-', a name-start code point, or an escape follows; a lone '-' (or '-'
       before a digit) is not an <ident-token>, so it is not a valid type selector (#375) */
    if (parser->pos < parser->len && parser->src[parser->pos] == '-') {
        Py_UCS4 after = parser->pos + 1 < parser->len ? parser->src[parser->pos + 1] : 0;
        if (after != '-' && after != '\\' && after != '_' && after < 0x80 && !is_ascii_alpha(after)) {
            sel_fail(parser, "expected an identifier");
            *out = parser->src + start;
            *out_len = 0;
            return;
        }
    }
    while (parser->pos < parser->len) {
        if (parser->src[parser->pos] == '\\') {
            if (!sel_starts_escape(parser)) {
                break; /* a backslash before a newline is not part of the identifier */
            }
            parser->src[write++] = sel_consume_escape(parser);
            continue;
        }
        if (!sel_is_ident(parser->src[parser->pos])) {
            break;
        }
        parser->src[write++] = parser->src[parser->pos++];
    }
    *out = parser->src + start;
    *out_len = write - start;
    if (*out_len == 0) {
        sel_fail(parser, "expected an identifier");
    }
}

/* Read a quoted-string value (the opening quote at parser->pos), decoding CSS
   escapes in place like sel_ident. Two rules set a string apart from an
   identifier (CSS Syntax 4.3.5 "consume a string token"): a backslash before a
   newline is a line continuation and is dropped, and a raw U+0000 folds to
   U+FFFD (Syntax 3.3 preprocessing). A decoded run is never longer than its
   source, so it is written back over the source. */
static void sel_string(sel_parser *parser, const Py_UCS4 **out, Py_ssize_t *out_len) {
    Py_UCS4 quote = parser->src[parser->pos++];
    Py_ssize_t start = parser->pos;
    Py_ssize_t write = start;
    while (parser->pos < parser->len && parser->src[parser->pos] != quote) {
        if (parser->src[parser->pos] == '\\') {
            Py_UCS4 next = parser->pos + 1 < parser->len ? parser->src[parser->pos + 1] : 0;
            if (next == '\n' || next == '\x0c') {
                parser->pos += 2; /* line continuation: the escaped newline is dropped */
                continue;
            }
            if (next == '\r') {
                parser->pos += 2;
                if (parser->pos < parser->len && parser->src[parser->pos] == '\n') {
                    parser->pos++; /* a CR LF pair counts as one newline */
                }
                continue;
            }
            parser->src[write++] = sel_consume_escape(parser);
            continue;
        }
        Py_UCS4 ch = parser->src[parser->pos++];
        parser->src[write++] = ch == 0 ? 0xFFFD : ch;
    }
    if (parser->pos >= parser->len) {
        sel_fail(parser, "unterminated string");
        return;
    }
    parser->pos++; /* the closing quote */
    *out = parser->src + start;
    *out_len = write - start;
}

/* UTF-8 encode a slice and resolve it to a tag atom. Folded for an HTML tree's
   lowercased tags, kept verbatim for a case-sensitive XML tree; a NULL tree is the
   CSS-to-XPath translator, which folds. */
static uint16_t sel_tag_atom(th_tree *tree, const Py_UCS4 *name, Py_ssize_t len) {
    char bytes[64];
    Py_ssize_t at = 0;
    int fold = tree == NULL || !th_tree_is_xml(tree);
    for (Py_ssize_t index = 0; index < len && at < (Py_ssize_t)sizeof(bytes) - 4; index++) {
        Py_UCS4 ch = name[index];
        if (fold && ch >= 'A' && ch <= 'Z') {
            ch += 32;
        }
        if (ch < 0x80) {
            bytes[at++] = (char)ch;
        } else {
            bytes[at++] = '\x01'; /* a non-ASCII byte is never in the tag table */
        }
    }
    return th_tag_lookup(bytes, at);
}

/* HTML attribute names are lowercased in the tree, so the selector name folds to match;
   XML keeps case. The caller only resolves an atom when the tree is non-NULL. */
static uint32_t sel_attr_atom(th_tree *tree, const Py_UCS4 *name, Py_ssize_t len) {
    char bytes[128];
    Py_ssize_t at = 0;
    int fold = !th_tree_is_xml(tree);
    for (Py_ssize_t index = 0; index < len && at < (Py_ssize_t)sizeof(bytes) - 4; index++) {
        Py_UCS4 ch = name[index];
        if (fold && ch >= 'A' && ch <= 'Z') {
            ch += 32;
        }
        if (ch < 0x80) {
            bytes[at++] = (char)ch;
        } else if (ch < 0x800) {
            bytes[at++] = (char)(0xC0 | (ch >> 6));
            bytes[at++] = (char)(0x80 | (ch & 0x3F));
        } else if (ch < 0x10000) {
            bytes[at++] = (char)(0xE0 | (ch >> 12));
            bytes[at++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            bytes[at++] = (char)(0x80 | (ch & 0x3F));
        } else {
            bytes[at++] = (char)(0xF0 | (ch >> 18));
            bytes[at++] = (char)(0x80 | ((ch >> 12) & 0x3F));
            bytes[at++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            bytes[at++] = (char)(0x80 | (ch & 0x3F));
        }
    }
    return th_attr_lookup(tree, bytes, at);
}

/* The HTML "case-sensitivity of selectors" attribute set: with no explicit s/i
   flag, an attribute selector on one of these names compares its value ASCII
   case-insensitively on HTML elements (WHATWG HTML, "Case-sensitivity of
   selectors"). Kept sorted for the binary search in sel_attr_default_ci. */
static const char *const SEL_CI_ATTRS[] = {
    "accept",   "accept-charset", "align",    "alink",      "axis",   "bgcolor",  "charset",   "checked",  "clear",
    "codetype", "color",          "compact",  "declare",    "defer",  "dir",      "direction", "disabled", "enctype",
    "face",     "frame",          "hreflang", "http-equiv", "lang",   "language", "link",      "media",    "method",
    "multiple", "nohref",         "noresize", "noshade",    "nowrap", "readonly", "rel",       "rev",      "rules",
    "scope",    "scrolling",      "selected", "shape",      "target", "text",     "type",      "valign",   "valuetype",
    "vlink",
};

/* Whether an attribute name (lowercased) is in the case-insensitive set. */
static int sel_attr_default_ci(const Py_UCS4 *name, Py_ssize_t len) {
    char buf[16]; /* the longest name, accept-charset, is 14 bytes */
    if (len >= (Py_ssize_t)sizeof(buf)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = name[index];
        if (ch >= 'A' && ch <= 'Z') {
            ch += 32;
        }
        if (ch >= 0x80) {
            return 0; /* a non-ASCII name is never in the set */
        }
        buf[index] = (char)ch;
    }
    buf[len] = '\0';
    Py_ssize_t lo = 0;
    Py_ssize_t hi = (Py_ssize_t)(sizeof(SEL_CI_ATTRS) / sizeof(SEL_CI_ATTRS[0]));
    while (lo < hi) {
        Py_ssize_t mid = (lo + hi) / 2;
        int cmp = strcmp(buf, SEL_CI_ATTRS[mid]);
        if (cmp == 0) {
            return 1;
        }
        if (cmp < 0) {
            hi = mid;
        } else {
            lo = mid + 1;
        }
    }
    return 0;
}

Py_UCS4 sel_fold(Py_UCS4 ch, int ci) {
    return ci ? lower_ascii(ch) : ch;
}

static void sel_kmp_prefix(const Py_UCS4 *want, Py_ssize_t want_len, int ci, Py_ssize_t *prefix) {
    prefix[0] = 0;
    for (Py_ssize_t index = 1, matched = 0; index < want_len; index++) {
        while (matched > 0 && sel_fold(want[index], ci) != sel_fold(want[matched], ci)) {
            matched = prefix[matched - 1];
        }
        if (sel_fold(want[index], ci) == sel_fold(want[matched], ci)) {
            matched++;
        }
        prefix[index] = matched;
    }
}

static int sel_substr_overlaps(const Py_UCS4 *want, Py_ssize_t want_len, int ci) {
    if (want_len <= 64) {
        return 0;
    }
    /* GCOVR_EXCL_START: needs a 32-bit build and a selector string of over a billion code points */
    Py_ssize_t *prefix = TH_UNLIKELY((size_t)want_len > SIZE_MAX / sizeof(Py_ssize_t))
                             ? NULL
                             : PyMem_Malloc((size_t)want_len * sizeof(*prefix));
    /* GCOVR_EXCL_STOP */
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 1;         /* GCOVR_EXCL_LINE: retain the bounded matcher when classification runs out of memory */
    }
    sel_kmp_prefix(want, want_len, ci, prefix);
    for (Py_ssize_t index = 64; index < want_len; index++) {
        if (prefix[index] >= 64) {
            PyMem_Free(prefix);
            return 1;
        }
    }
    PyMem_Free(prefix);
    return 0;
}

/* Parse a bracketed attribute selector (the leading '[' already consumed). */
static void sel_attribute(sel_parser *parser, sel_simple *simple) {
    simple->kind = '[';
    simple->op = OP_EXISTS;
    sel_skip_ws(parser);
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    /* an optional namespace prefix (ns|, *|, or a bare |) selects on the local
       attribute name in a namespaceless HTML document, matching how a type selector
       already ignores its namespace prefix (Selectors-4 §6.3, issue #378) */
    if (parser->pos < parser->len && parser->src[parser->pos] == '*') {
        if (parser->pos + 1 >= parser->len || parser->src[parser->pos + 1] != '|') {
            sel_fail(parser, "expected '|' after '*' in the namespace prefix");
            return;
        }
        parser->pos += 2;
        sel_ident(parser, &name, &name_len);
    } else if (parser->pos < parser->len && parser->src[parser->pos] == '|') {
        parser->pos++;
        sel_ident(parser, &name, &name_len);
    } else {
        sel_ident(parser, &name, &name_len);
        /* a '|' that is not the '|=' dash-match operator is a namespace separator;
           drop the prefix already read and take the identifier after it as the name */
        if (!parser->error && parser->pos < parser->len && parser->src[parser->pos] == '|' &&
            (parser->pos + 1 >= parser->len || parser->src[parser->pos + 1] != '=')) {
            parser->pos++;
            sel_ident(parser, &name, &name_len);
        }
    }
    if (parser->error) {
        return;
    }
    /* the attribute name slice feeds the CSS-to-XPath translator, which parses with no
       tree and so cannot resolve (and does not need) the per-tree attribute atom */
    simple->name = name;
    simple->name_len = name_len;
    simple->attr_atom = parser->tree != NULL ? sel_attr_atom(parser->tree, name, name_len) : 0;
    sel_skip_ws(parser);
    Py_UCS4 ch = parser->pos < parser->len ? parser->src[parser->pos] : 0;
    if (ch == ']') {
        parser->pos++;
        return;
    }
    if (ch == '~' || ch == '|' || ch == '^' || ch == '$' || ch == '*') {
        simple->op = ch == '~'   ? OP_INCLUDE
                     : ch == '|' ? OP_DASH
                     : ch == '^' ? OP_PREFIX
                     : ch == '$' ? OP_SUFFIX
                                 : OP_SUBSTR;
        parser->pos++;
        if (parser->pos >= parser->len || parser->src[parser->pos] != '=') {
            sel_fail(parser, "expected '=' after the attribute operator");
            return;
        }
        parser->pos++;
    } else if (ch == '=') {
        simple->op = OP_EQ;
        parser->pos++;
    } else {
        sel_fail(parser, "expected an attribute operator or ']'");
        return;
    }
    sel_skip_ws(parser);
    if (parser->pos < parser->len && (parser->src[parser->pos] == '"' || parser->src[parser->pos] == '\'')) {
        sel_string(parser, &simple->value, &simple->value_len);
        if (parser->error) {
            return;
        }
    } else {
        sel_ident(parser, &simple->value, &simple->value_len);
        if (parser->error) {
            return;
        }
    }
    sel_skip_ws(parser);
    if (parser->pos < parser->len && (parser->src[parser->pos] | 32) == 'i') {
        simple->ci = 1;
        parser->pos++;
        sel_skip_ws(parser);
    } else if (parser->pos < parser->len && (parser->src[parser->pos] | 32) == 's') {
        parser->pos++;
        sel_skip_ws(parser);
    } else if ((parser->tree == NULL || !th_tree_is_xml(parser->tree)) && sel_attr_default_ci(name, name_len)) {
        /* the HTML set defaults to case-insensitive without a flag, only in an HTML document
           (the translator, with no tree, targets one); XML values always compare exactly */
        simple->ci_default = 1;
    }
    if (parser->pos >= parser->len || parser->src[parser->pos] != ']') {
        sel_fail(parser, "expected ']'");
        return;
    }
    if (parser->tree != NULL && simple->op == OP_SUBSTR &&
        sel_substr_overlaps(simple->value, simple->value_len, simple->ci || simple->ci_default)) {
        simple->kind = ':';
        simple->pseudo = PSEUDO_SUBSTR_LONG;
    }
    parser->pos++;
}

/* Parse the local part of a type selector (the part after an optional namespace
   prefix): '*' for the universal selector or an identifier for a tag name. */
static void sel_type_local(sel_parser *parser, sel_simple *simple) {
    if (parser->pos < parser->len && parser->src[parser->pos] == '*') {
        simple->kind = '*';
        parser->pos++;
    } else if (parser->pos < parser->len &&
               (sel_is_ident(parser->src[parser->pos]) || parser->src[parser->pos] == '\\')) {
        simple->kind = 'e';
        sel_ident(parser, &simple->name, &simple->name_len);
        simple->tag_atom = sel_tag_atom(parser->tree, simple->name, simple->name_len);
    } else {
        sel_fail(parser, "expected a type or '*' after the namespace prefix");
    }
}

/* Whether a name slice equals a lowercase ASCII keyword (ASCII case-insensitive). */
static int sel_kw(const Py_UCS4 *name, Py_ssize_t len, const char *kw) {
    if (len != (Py_ssize_t)strlen(kw)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = name[index];
        if (ch >= 'A' && ch <= 'Z') {
            ch += 32;
        }
        if (ch != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return 1;
}

/* Read a run of ASCII digits as a magnitude; *seen reports any digit. A magnitude past INT_MAX stops growing, which
   keeps the value past INT_MAX and so still clamps the same way. */
static int64_t sel_read_int(sel_parser *parser, int *seen) {
    int64_t value = 0;
    *seen = 0;
    while (parser->pos < parser->len && parser->src[parser->pos] >= '0' && parser->src[parser->pos] <= '9') {
        const int digit = (int)(parser->src[parser->pos] - '0');
        value = value > INT_MAX ? value : value * 10 + digit;
        parser->pos++;
        *seen = 1;
    }
    return value;
}

/* A coefficient as browsers read it: its signed value saturated to the 32-bit int range, so -2^31 stays INT_MIN. */
static int64_t sel_clamp_int(int negative, int64_t magnitude) {
    const int64_t value = negative ? -magnitude : magnitude;
    return value < INT_MIN ? INT_MIN : (value > INT_MAX ? INT_MAX : value);
}

/* Parse the An+B microsyntax (CSS Syntax §"The An+B microsyntax") into a/b, including the even/odd keywords. pos
   starts after '(' and stops at the ')'. */
static void sel_parse_anb(sel_parser *parser, int64_t *a, int64_t *b) {
    sel_skip_ws(parser);
    if (parser->pos + 4 <= parser->len && sel_kw(parser->src + parser->pos, 4, "even")) {
        parser->pos += 4;
        *a = 2;
        *b = 0;
        sel_skip_ws(parser);
        return;
    }
    if (parser->pos + 3 <= parser->len && sel_kw(parser->src + parser->pos, 3, "odd")) {
        parser->pos += 3;
        *a = 2;
        *b = 1;
        sel_skip_ws(parser);
        return;
    }
    int sign = 1;
    if (parser->pos < parser->len && (parser->src[parser->pos] == '+' || parser->src[parser->pos] == '-')) {
        sign = parser->src[parser->pos] == '-' ? -1 : 1;
        parser->pos++;
    }
    int seen_a;
    const int64_t num = sel_read_int(parser, &seen_a);
    if (parser->pos < parser->len && (parser->src[parser->pos] | 32) == 'n') {
        parser->pos++;
        *a = seen_a ? sel_clamp_int(sign < 0, num) : sign;
        sel_skip_ws(parser);
        if (parser->pos < parser->len && (parser->src[parser->pos] == '+' || parser->src[parser->pos] == '-')) {
            const int bsign = parser->src[parser->pos] == '-' ? -1 : 1;
            parser->pos++;
            sel_skip_ws(parser);
            int seen_b;
            const int64_t bval = sel_read_int(parser, &seen_b);
            if (!seen_b) {
                sel_fail(parser, "expected a number after the sign in An+B");
                return;
            }
            *b = sel_clamp_int(bsign < 0, bval);
        } else {
            *b = 0;
        }
    } else if (seen_a) {
        *a = 0;
        *b = sel_clamp_int(sign < 0, num);
    } else {
        sel_fail(parser, "expected An+B"); /* a bare sign with no digits and no 'n' is invalid */
        return;
    }
    sel_skip_ws(parser);
}

/* The pair the matcher reads for the An+B = a/b, with both coefficients inside the 32-bit int range. It selects the
   sibling positions in [1, INT_MAX] that the exact integers select. A positive a keeps b when b >= 1 and otherwise
   its residue in [0, a), which the positions congruent to b and not below it reduce to. A negative a whose magnitude
   exceeds INT_MAX keeps at most the position b, since the step can be reached only once. */
static void sel_nth_narrow(int64_t a, int64_t b, int *out_a, int *out_b) {
    int64_t pa = 0;
    int64_t pb = 0;
    if (a == 0) {
        pb = b >= 1 ? b : 0;
    } else if (a > 0) {
        pa = a;
        pb = b >= 1 ? b : ((b % a) + a) % a;
    } else if (b < 1) {
        pa = -a <= INT_MAX ? a : 0;
    } else {
        pa = -a <= INT_MAX ? a : 0;
        pb = b;
    }
    *out_a = (int)pa;
    *out_b = (int)pb;
}

/* Parse the comma-separated selector list inside a :is()/:where()/:has(); defined
   below, after the complex-selector parser it drives. */
int sel_parse_alts(sel_parser *parser, sel_complex **out_alts, int *out_count, int nested, int relative, int forgiving);
void sel_free_alts(sel_complex *alts, int count);

/* The pseudo-classes that depend on live UA/interaction or navigation state a
   parsed tree cannot express. They parse as valid selectors but match nothing,
   so :is()/:not() compositions stay usable instead of failing to compile. */
static const char *const SEL_NEVER_PSEUDOS[] = {
    "hover",
    "focus",
    "focus-within",
    "focus-visible",
    "active",
    "target",
    "target-within",
    "visited",
    /* live media, modal, and user-interaction validity state a static tree cannot
       express: valid selectors that match nothing (issue #432) */
    "modal",
    "fullscreen",
    "picture-in-picture",
    "playing",
    "paused",
    "muted",
    "current",
    "past",
    "future",
    "user-valid",
    "user-invalid",
    "autofill",
    "defined",
};

/* Parse the :dir() argument (pos just after '('): an identifier, mapped to the
   direction code 1 (ltr) or 2 (rtl); any other identifier is valid but matches
   nothing, stored as 0. */
static void sel_parse_dir(sel_parser *parser, sel_simple *simple) {
    sel_skip_ws(parser);
    const Py_UCS4 *arg;
    Py_ssize_t arg_len;
    sel_ident(parser, &arg, &arg_len);
    if (parser->error) {
        return;
    }
    simple->nth_a = sel_kw(arg, arg_len, "ltr") ? 1 : (sel_kw(arg, arg_len, "rtl") ? 2 : 0);
    sel_skip_ws(parser);
}

/* Parse the :lang() argument (pos just after '('): a non-empty comma-separated
   list of language ranges. Escapes are decoded in place (so an escaped wildcard
   like \2a or \* becomes '*'); the decoded slice is the value, split at match time.
   nth_a is 1 when a range is a quoted empty string, which matches an element whose
   language is not tagged (Selectors 4 §11.1), so the matcher need not split for it. */
static void sel_parse_lang(sel_parser *parser, sel_simple *simple) {
    sel_skip_ws(parser);
    Py_ssize_t start = parser->pos;
    Py_ssize_t write = parser->pos;
    while (parser->pos < parser->len && parser->src[parser->pos] != ')') {
        if (parser->src[parser->pos] == '\\') {
            parser->src[write++] = sel_consume_escape(parser);
            continue;
        }
        parser->src[write++] = parser->src[parser->pos++];
    }
    Py_ssize_t end = write;
    while (end > start && is_space(parser->src[end - 1])) {
        end--;
    }
    if (end == start) {
        sel_fail(parser, "expected a language range"); /* :lang() with no range is invalid */
        return;
    }
    simple->value = parser->src + start;
    simple->value_len = end - start;
    Py_ssize_t cursor = start;
    while (cursor < end) {
        Py_ssize_t from = cursor;
        while (cursor < end && parser->src[cursor] != ',') {
            cursor++;
        }
        Py_ssize_t to = cursor++;
        while (from < to && is_space(parser->src[from])) {
            from++;
        }
        while (to > from && is_space(parser->src[to - 1])) {
            to--;
        }
        if (to - from == 2 && (parser->src[from] == '"' || parser->src[from] == '\'') &&
            parser->src[from + 1] == parser->src[from]) {
            simple->nth_a = 1;
        }
    }
}

/* Parse a pseudo-class selector (the leading ':' already consumed). */
static void sel_pseudo(sel_parser *parser, sel_simple *simple) {
    simple->kind = ':';
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    sel_ident(parser, &name, &name_len);
    if (parser->error) {
        return;
    }
    int functional = PSEUDO_NONE; /* an nth-* pseudo-class taking An+B */
    int listy = PSEUDO_NONE;      /* :is()/:where()/:has()/:not() taking a selector list */
    int langdir = PSEUDO_NONE;    /* :lang()/:dir() taking a special argument */
    if (sel_kw(name, name_len, "root")) {
        simple->pseudo = PSEUDO_ROOT;
    } else if (sel_kw(name, name_len, "empty")) {
        simple->pseudo = PSEUDO_EMPTY;
    } else if (sel_kw(name, name_len, "first-child")) {
        simple->pseudo = PSEUDO_FIRST_CHILD;
    } else if (sel_kw(name, name_len, "last-child")) {
        simple->pseudo = PSEUDO_LAST_CHILD;
    } else if (sel_kw(name, name_len, "only-child")) {
        simple->pseudo = PSEUDO_ONLY_CHILD;
    } else if (sel_kw(name, name_len, "first-of-type")) {
        simple->pseudo = PSEUDO_FIRST_OF_TYPE;
    } else if (sel_kw(name, name_len, "last-of-type")) {
        simple->pseudo = PSEUDO_LAST_OF_TYPE;
    } else if (sel_kw(name, name_len, "only-of-type")) {
        simple->pseudo = PSEUDO_ONLY_OF_TYPE;
    } else if (sel_kw(name, name_len, "nth-child")) {
        functional = PSEUDO_NTH_CHILD;
    } else if (sel_kw(name, name_len, "nth-last-child")) {
        functional = PSEUDO_NTH_LAST_CHILD;
    } else if (sel_kw(name, name_len, "nth-of-type")) {
        functional = PSEUDO_NTH_OF_TYPE;
    } else if (sel_kw(name, name_len, "nth-last-of-type")) {
        functional = PSEUDO_NTH_LAST_OF_TYPE;
    } else if (sel_kw(name, name_len, "is")) {
        listy = PSEUDO_IS;
    } else if (sel_kw(name, name_len, "where")) {
        listy = PSEUDO_WHERE;
    } else if (sel_kw(name, name_len, "has")) {
        listy = PSEUDO_HAS;
    } else if (sel_kw(name, name_len, "not")) {
        listy = PSEUDO_NOT;
    } else if (sel_kw(name, name_len, "scope")) {
        simple->pseudo = PSEUDO_SCOPE;
        parser->scoped = 1;
    } else if (sel_kw(name, name_len, "checked")) {
        simple->pseudo = PSEUDO_CHECKED;
    } else if (sel_kw(name, name_len, "disabled")) {
        simple->pseudo = PSEUDO_DISABLED;
    } else if (sel_kw(name, name_len, "enabled")) {
        simple->pseudo = PSEUDO_ENABLED;
    } else if (sel_kw(name, name_len, "required")) {
        simple->pseudo = PSEUDO_REQUIRED;
    } else if (sel_kw(name, name_len, "optional")) {
        simple->pseudo = PSEUDO_OPTIONAL;
    } else if (sel_kw(name, name_len, "read-only")) {
        simple->pseudo = PSEUDO_READ_ONLY;
    } else if (sel_kw(name, name_len, "read-write")) {
        simple->pseudo = PSEUDO_READ_WRITE;
    } else if (sel_kw(name, name_len, "default")) {
        simple->pseudo = PSEUDO_DEFAULT;
    } else if (sel_kw(name, name_len, "lang")) {
        langdir = PSEUDO_LANG;
    } else if (sel_kw(name, name_len, "dir")) {
        langdir = PSEUDO_DIR;
    } else if (sel_kw(name, name_len, "link") || sel_kw(name, name_len, "any-link")) {
        simple->pseudo = PSEUDO_ANY_LINK;
    } else {
        for (size_t index = 0; index < sizeof(SEL_NEVER_PSEUDOS) / sizeof(SEL_NEVER_PSEUDOS[0]); index++) {
            if (sel_kw(name, name_len, SEL_NEVER_PSEUDOS[index])) {
                simple->pseudo = PSEUDO_NEVER;
                break;
            }
        }
        if (simple->pseudo == PSEUDO_NONE) {
            sel_fail(parser, "unknown pseudo-class");
            return;
        }
    }
    if (functional != PSEUDO_NONE) {
        simple->pseudo = functional;
        if (parser->pos >= parser->len || parser->src[parser->pos] != '(') {
            sel_fail(parser, "expected '(' after the pseudo-class");
            return;
        }
        parser->pos++;
        int64_t nth_a = 0;
        int64_t nth_b = 0;
        sel_parse_anb(parser, &nth_a, &nth_b);
        if (parser->error) {
            return;
        }
        sel_nth_narrow(nth_a, nth_b, &simple->nth_a, &simple->nth_b);
        /* the Level-4 'of S' clause filters the sibling list by a selector; it is
           valid only on :nth-child()/:nth-last-child(), not the -of-type variants */
        if (parser->pos < parser->len && sel_is_ident(parser->src[parser->pos])) {
            /* the leading character is an identifier char, so sel_ident reads a
               non-empty run and cannot fail here */
            const Py_UCS4 *kw;
            Py_ssize_t kw_len;
            sel_ident(parser, &kw, &kw_len);
            if (!sel_kw(kw, kw_len, "of") || (functional != PSEUDO_NTH_CHILD && functional != PSEUDO_NTH_LAST_CHILD)) {
                sel_fail(parser, "expected 'of S' or ')'");
                return;
            }
            /* S is a real (non-forgiving) complex-selector-list; this consumes the ')' */
            if (parser->depth >= SEL_MAX_DEPTH) {
                sel_fail(parser, "selector nested too deeply");
                return;
            }
            parser->depth++;
            sel_parse_alts(parser, &simple->sub, &simple->sub_count, 1, 0, 0);
            parser->depth--;
            return;
        }
        if (parser->pos >= parser->len || parser->src[parser->pos] != ')') {
            sel_fail(parser, "expected ')'");
            return;
        }
        parser->pos++;
    } else if (listy != PSEUDO_NONE) {
        simple->pseudo = listy;
        if (parser->pos >= parser->len || parser->src[parser->pos] != '(') {
            sel_fail(parser, "expected '(' after the pseudo-class"); /* :is()/:where()/:has() require a list */
            return;
        }
        parser->pos++;
        /* :is()/:where() take a forgiving selector list (a bad arm is dropped); :has()
           and :not() take a real list, so any bad arm invalidates the whole selector */
        int forgiving = listy == PSEUDO_IS || listy == PSEUDO_WHERE;
        if (parser->depth >= SEL_MAX_DEPTH) {
            sel_fail(parser, "selector nested too deeply");
            return;
        }
        parser->depth++;
        sel_parse_alts(parser, &simple->sub, &simple->sub_count, 1, listy == PSEUDO_HAS, forgiving);
        parser->depth--;
    } else if (langdir != PSEUDO_NONE) {
        simple->pseudo = langdir;
        if (parser->pos >= parser->len || parser->src[parser->pos] != '(') {
            sel_fail(parser, "expected '(' after the pseudo-class"); /* :lang()/:dir() require an argument */
            return;
        }
        parser->pos++;
        if (langdir == PSEUDO_DIR) {
            sel_parse_dir(parser, simple);
        } else {
            sel_parse_lang(parser, simple);
            if (parser->tree != NULL) {
                simple->attr_atom = th_attr_lookup(parser->tree, "xml:lang", 8);
            }
        }
        if (parser->error) {
            return;
        }
        if (parser->pos >= parser->len || parser->src[parser->pos] != ')') {
            sel_fail(parser, "expected ')'");
            return;
        }
        parser->pos++;
    } else if (parser->pos < parser->len && parser->src[parser->pos] == '(') {
        sel_fail(parser, "pseudo-class takes no argument"); /* a non-functional pseudo-class */
        return;
    }
}

/* Parse one simple selector into *simple. */
static void sel_one(sel_parser *parser, sel_simple *simple) {
    simple->tag_atom = TH_TAG_UNKNOWN;
    simple->attr_atom = 0;
    simple->ci = 0;
    simple->ci_default = 0;
    simple->pseudo = PSEUDO_NONE;
    simple->nth_a = 0;
    simple->nth_b = 0;
    simple->name = NULL;
    simple->name_len = 0;
    simple->value = NULL;
    simple->value_len = 0;
    simple->sub = NULL;
    simple->sub_count = 0;
    Py_UCS4 ch = parser->src[parser->pos];
    if (ch == '.' || ch == '#') {
        simple->kind = (char)ch;
        parser->pos++;
        sel_ident(parser, &simple->name, &simple->name_len);
    } else if (ch == ':') {
        parser->pos++;
        sel_pseudo(parser, simple);
    } else if (ch == '[') {
        parser->pos++;
        sel_attribute(parser, simple);
    } else if (ch == '|') {
        /* an empty (no-namespace) prefix; the prefix is ignored in a namespaceless
           HTML document, so |E reduces to E and |* to the universal selector */
        parser->pos++;
        sel_type_local(parser, simple);
    } else if (ch == '*' && parser->pos + 1 < parser->len && parser->src[parser->pos + 1] == '|') {
        /* a '*|' any-namespace prefix; ignored in HTML, so *|E reduces to E */
        parser->pos += 2;
        sel_type_local(parser, simple);
    } else if (ch == '*') {
        simple->kind = '*';
        parser->pos++;
    } else {
        /* an identifier: a tag name, or a namespace prefix when a '|' follows it.
           sel_starts_simple guarantees at least one char, so sel_ident succeeds */
        sel_ident(parser, &simple->name, &simple->name_len);
        if (parser->pos < parser->len && parser->src[parser->pos] == '|') {
            parser->pos++; /* an 'ns|' prefix; ignored in HTML, only the local part selects */
            simple->name = NULL;
            simple->name_len = 0;
            sel_type_local(parser, simple);
        } else {
            simple->kind = 'e';
            simple->tag_atom = sel_tag_atom(parser->tree, simple->name, simple->name_len);
        }
    }
}

static int sel_starts_simple(Py_UCS4 ch) {
    return ch == '*' || ch == '.' || ch == '#' || ch == '[' || ch == ':' || ch == '\\' || ch == '|' || sel_is_ident(ch);
}

/* Grow a parse buffer by doubling (starting at 8). Called only when the buffer is
   full, so one doubling always makes room for the next element. Returns 0, or -1 on an
   allocation failure that leaves the existing buffer intact. The compound/complex/list
   buffers grow this way, so a selector is bounded only by its own length, not a fixed
   compound/simple/arm cap (issue #432). */
static int sel_grow(void **buffer, int *capacity, size_t elem_size) {
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)*capacity + 1, (size_t)*capacity, 8, elem_size, &cap, &bytes);
    if (!grew || cap > INT_MAX) { /* GCOVR_EXCL_BR_LINE: needs a parse buffer of over a billion elements */
        return -1;                /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    void *bigger = PyMem_Realloc(*buffer, bytes);
    if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *buffer = bigger;
    *capacity = (int)cap;
    return 0;
}

void sel_free_alts(sel_complex *alts, int count);

/* Free the simples of a compound (each functional pseudo-class's nested list, then the
   array itself). */
static void free_simples(sel_simple *simples, int count) {
    for (int index = 0; index < count; index++) {
        if (simples[index].sub != NULL) {
            sel_free_alts(simples[index].sub, simples[index].sub_count);
        }
    }
    PyMem_Free(simples);
}

/* Parse a compound (one or more adjacent simples) into a grown, owned array. Returns
   the array with *out_count set, or NULL with parser->error set. */
static sel_simple *sel_compound_parse(sel_parser *parser, int *out_count) {
    sel_simple *simples = NULL;
    int capacity = 0;
    int count = 0;
    while (parser->pos < parser->len && sel_starts_simple(parser->src[parser->pos])) {
        if (count == capacity) {
            /* named so the branch and its coverage marker stay on one line (clang-format
               would otherwise wrap the long comparison and orphan the marker) */
            int grow_failed = sel_grow((void **)&simples, &capacity, sizeof(sel_simple)) < 0;
            if (grow_failed) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                sel_fail(parser, NULL); /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                  /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        simples[count].nest = 0;
        sel_one(parser, &simples[count]);
        if (parser->error) {
            break;
        }
        /* Selectors-4 §4.1: a type/universal selector, if present, is first in a
           compound, so a type after a subclass selector (e.g. [href]p) is invalid (#375) */
        if (count > 0 && (simples[count].kind == 'e' || simples[count].kind == '*')) {
            sel_fail(parser, "the type selector must be first in a compound");
            break;
        }
        count++;
    }
    if (count == 0 && !parser->error) {
        sel_fail(parser, "expected a selector");
    }
    if (parser->error) {
        /* the simple that failed left its own sub NULL (sel_parse_alts cleans up on
           error), so only the ones parsed before it carry a nested list to free */
        free_simples(simples, count);
        return NULL;
    }
    *out_count = count;
    return simples;
}

/* Parse one complex selector (compounds joined by combinators) into *complex,
   allocating its compounds. Returns 0, or -1 with parser->error set. */
static void free_compounds(sel_compound *compounds, int count) {
    for (int index = 0; index < count; index++) {
        free_simples(compounds[index].simples, compounds[index].count);
    }
}

/* Free a comma-separated list of complex selectors and the array holding it (used
   for the top-level compiled selector and every nested :is()/:where()/:has() list). */
void sel_free_alts(sel_complex *alts, int count) {
    for (int index = 0; index < count; index++) {
        free_compounds(alts[index].compounds, alts[index].count);
        PyMem_Free(alts[index].compounds);
    }
    PyMem_Free(alts);
}

static int sel_complex_parse(sel_parser *parser, sel_complex *complex, int nested, int relative) {
    sel_compound *compounds = NULL;
    int capacity = 0;
    int count = 0;
    char combinator = ' ';
    /* a relative selector (the argument of :has()) may open with a combinator, which
       joins its first compound to the anchor element instead of a left-hand compound */
    if (relative) {
        sel_skip_ws(parser);
        Py_UCS4 lead = parser->pos < parser->len ? parser->src[parser->pos] : 0;
        if (lead == '>' || lead == '+' || lead == '~') {
            combinator = (char)lead;
            parser->pos++;
            sel_skip_ws(parser);
        }
    }
    while (1) {
        int simple_count = 0;
        sel_simple *simples = sel_compound_parse(parser, &simple_count);
        if (parser->error) {
            break;
        }
        if (count == capacity) {
            int grow_failed = sel_grow((void **)&compounds, &capacity, sizeof(sel_compound)) < 0;
            if (grow_failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                free_simples(simples, simple_count); /* GCOVR_EXCL_LINE: allocation-failure path */
                sel_fail(parser, NULL);              /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                               /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        compounds[count].simples = simples;
        compounds[count].count = simple_count;
        compounds[count].combinator = combinator;
        count++;
        parser->compounds++;
        int saw_ws = parser->pos < parser->len && is_space(parser->src[parser->pos]);
        sel_skip_ws(parser);
        if (parser->pos >= parser->len) {
            break;
        }
        Py_UCS4 ch = parser->src[parser->pos];
        if (ch == ',' || (nested && ch == ')')) {
            break;
        }
        if (ch == '>' || ch == '+' || ch == '~') {
            combinator = (char)ch;
            parser->pos++;
            sel_skip_ws(parser);
        } else if (saw_ws && sel_starts_simple(ch)) {
            combinator = ' ';
        } else {
            sel_fail(parser, "expected a combinator or the end of the selector");
            break;
        }
    }
    if (parser->error) {
        free_compounds(compounds, count);
        PyMem_Free(compounds);
        return -1;
    }
    complex->compounds = compounds;
    complex->count = count;
    return 0;
}

/* Recover from a failed arm in a forgiving selector list: advance to the next
   top-level ',' or the ')' that closes the list (or the end), skipping balanced
   brackets and quoted strings so a delimiter inside them does not end the arm. */
static void sel_skip_bad_arm(sel_parser *parser) {
    int depth = 0;
    while (parser->pos < parser->len) {
        Py_UCS4 ch = parser->src[parser->pos];
        if (ch == '"' || ch == '\'') {
            Py_UCS4 quote = ch;
            parser->pos++;
            while (parser->pos < parser->len && parser->src[parser->pos] != quote) {
                if (parser->src[parser->pos] == '\\' && parser->pos + 1 < parser->len) {
                    parser->pos++; /* the escaped character cannot close the string */
                }
                parser->pos++;
            }
            if (parser->pos < parser->len) {
                parser->pos++; /* the closing quote */
            }
            continue;
        }
        if (ch == '(' || ch == '[') {
            depth++;
        } else if (ch == ']') {
            if (depth > 0) {
                depth--;
            }
        } else if (ch == ')') {
            if (depth == 0) {
                return; /* the ')' that ends the forgiving list */
            }
            depth--;
        } else if (ch == ',' && depth == 0) {
            return; /* the next arm */
        }
        parser->pos++;
    }
}

/* Parse a comma-separated list of complex selectors into a freshly allocated array.
   nested stops the list at a closing ')' (the :is()/:where()/:has() argument case)
   and requires that ')'; relative lets each complex open with a combinator (:has());
   forgiving drops an arm that fails to parse instead of failing the whole list (the
   :is()/:where() rule), and may leave an empty list (*out_alts NULL) that matches
   nothing. Returns 0 with the out parameters set, or -1 with parser->error set. */
int sel_parse_alts(sel_parser *parser, sel_complex **out_alts, int *out_count, int nested, int relative,
                   int forgiving) {
    sel_complex *alts = NULL;
    int capacity = 0;
    int count = 0;
    while (1) {
        sel_skip_ws(parser);
        if (count == capacity) {
            int grow_failed = sel_grow((void **)&alts, &capacity, sizeof(sel_complex)) < 0;
            if (grow_failed) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                sel_fail(parser, NULL); /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                  /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        alts[count].walks = 0;
        if (sel_complex_parse(parser, &alts[count], nested, relative) == 0) {
            count++;
        } else if (forgiving) {
            if (parser->err_reason == NULL) { /* GCOVR_EXCL_BR_LINE: only an allocation failure has no reason */
                break;                        /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            parser->error = 0; /* drop the unparsable arm and recover to the next one */
            sel_skip_bad_arm(parser);
        } else {
            break;
        }
        sel_skip_ws(parser);
        if (parser->pos >= parser->len) {
            if (nested) {
                sel_fail(parser, "expected ')'"); /* a ':is(...' that never closes its '(' */
            }
            break;
        }
        if (parser->src[parser->pos] == ',') {
            parser->pos++;
            continue;
        }
        /* an arm stops only at a comma, the end, or -- when nested -- the closing
           ')' that ends the argument list; consume it and finish */
        parser->pos++;
        break;
    }
    if (parser->error) {
        for (int index = 0; index < count; index++) {
            free_compounds(alts[index].compounds, alts[index].count);
            PyMem_Free(alts[index].compounds);
        }
        PyMem_Free(alts);
        return -1;
    }
    if (count == 0) {
        PyMem_Free(alts); /* a forgiving list whose arms all dropped grew no arm; free the (empty) buffer */
        *out_alts = NULL; /* matches nothing */
        *out_count = 0;
        return 0;
    }
    *out_alts = alts;
    *out_count = count;
    return 0;
}

void selector_free(sel_compiled *compiled) {
    if (compiled == NULL) { /* GCOVR_EXCL_BR_LINE: only failed stylesheet compilation passes NULL */
        return;             /* GCOVR_EXCL_LINE: allocation-failure cleanup */
    }
    sel_free_alts(compiled->alts, compiled->count);
    PyMem_Free(compiled->source);
    PyMem_Free(compiled);
}

/* Set a SelectorSyntaxError naming the selector, the reason the parse failed, and the
   position the failing token starts at (issue #434), e.g.
   invalid CSS selector ":nth-child(foo)": expected An+B at position 11 */
void sel_raise(PyObject *selector_error, PyObject *selector_str, const sel_parser *parser) {
    if (parser->err_reason == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();             /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyErr_Format(selector_error, "invalid CSS selector \"%U\": %s at position %zd", selector_str, parser->err_reason,
                 parser->err_pos);
}

/* Whether any compound in the list carries a :has(), recursing through the nested
   lists of :is()/:where()/:not()/:has() so a :has() buried inside them still counts.
   The result drives whether a query allocates the :has() subtree memo at all. */
typedef int (*sel_pseudo_test)(int pseudo);

static int sel_alts_have_pseudo(const sel_complex *alts, int count, sel_pseudo_test wanted);

static int sel_compound_has_pseudo(const sel_compound *compound, sel_pseudo_test wanted) {
    for (int index = 0; index < compound->count; index++) {
        const sel_simple *simple = &compound->simples[index];
        if (simple->kind != ':') {
            continue;
        }
        if (wanted(simple->pseudo)) {
            return 1;
        }
        if (simple->sub != NULL && sel_alts_have_pseudo(simple->sub, simple->sub_count, wanted)) {
            return 1;
        }
    }
    return 0;
}

static int sel_alts_have_pseudo(const sel_complex *alts, int count, sel_pseudo_test wanted) {
    for (int index = 0; index < count; index++) {
        const sel_complex *complex = &alts[index];
        for (int compound = 0; compound < complex->count; compound++) {
            if (sel_compound_has_pseudo(&complex->compounds[compound], wanted)) {
                return 1;
            }
        }
    }
    return 0;
}

static int sel_is_has_pseudo(int pseudo) {
    return pseudo == PSEUDO_HAS;
}

static int sel_is_dir_pseudo(int pseudo) {
    return pseudo == PSEUDO_DIR;
}

static int sel_is_legend_pseudo(int pseudo) {
    return pseudo == PSEUDO_DISABLED || pseudo == PSEUDO_ENABLED || pseudo == PSEUDO_READ_ONLY ||
           pseudo == PSEUDO_READ_WRITE;
}

/* How far matching alts walks ancestors (sel_compiled.climbs). A walk comes from a descendant combinator between two
   compounds or leading a relative selector (the argument of :has()), and from a :lang() or :dir(), which reads what an
   ancestor declares. A compound left of a descendant combinator between two compounds is tested at each ancestor that
   combinator's walk passes. */
static int sel_alts_climb(const sel_complex *alts, int count, int relative) {
    int climbs = 0;
    for (int alt = 0; alt < count; alt++) {
        const sel_complex *complex = &alts[alt];
        int walked_from = 0; /* the compounds left of this one are tested along a walk */
        for (int index = 0; index < complex->count; index++) {
            if (complex->compounds[index].combinator == ' ' && (index > 0 || relative)) {
                climbs = 1; /* climbs is 0 or 1 here: the loop below returns 2 for any deeper walk */
                walked_from = index;
            }
        }
        for (int index = 0; index < complex->count; index++) {
            const sel_compound *compound = &complex->compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                int simple_climbs = 0;
                if (simple->kind == ':' && (simple->pseudo == PSEUDO_LANG || simple->pseudo == PSEUDO_DIR)) {
                    simple_climbs = 1;
                } else if (simple->kind == ':' && simple->sub != NULL) {
                    simple_climbs = sel_alts_climb(simple->sub, simple->sub_count, simple->pseudo == PSEUDO_HAS);
                }
                if (simple_climbs > 1 || (simple_climbs == 1 && index < walked_from)) {
                    return 2;
                }
                climbs = climbs > simple_climbs ? climbs : simple_climbs;
            }
        }
    }
    return climbs;
}

/* Whether alts writes :scope outside a :has(), which binds :scope to its own anchor. */
static int sel_alts_write_scope(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        for (int index = 0; index < alts[alt].count; index++) {
            const sel_compound *compound = &alts[alt].compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind == ':' &&
                    (simple->pseudo == PSEUDO_SCOPE || (simple->sub != NULL && simple->pseudo != PSEUDO_HAS &&
                                                        sel_alts_write_scope(simple->sub, simple->sub_count)))) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

/* Whether alts writes :scope in the S of an :nth-last-child() outside a :has(). */
static int sel_alts_scope_ahead(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        for (int index = 0; index < alts[alt].count; index++) {
            const sel_compound *compound = &alts[alt].compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind == ':' && simple->sub != NULL && simple->pseudo != PSEUDO_HAS &&
                    ((simple->pseudo == PSEUDO_NTH_LAST_CHILD &&
                      sel_alts_write_scope(simple->sub, simple->sub_count)) ||
                     sel_alts_scope_ahead(simple->sub, simple->sub_count))) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

/* Whether a :scope in alts, read in a :has() argument, can be tested at the anchor: one in a nested list alternative
   that walks to other elements, or in the S of an 'of S' clause, which reads the other siblings. The argument reaches
   only elements past the anchor, so a :scope tested at the element it reached fails under every anchor. */
static int sel_alts_scope_reaches(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        for (int index = 0; index < alts[alt].count; index++) {
            const sel_compound *compound = &alts[alt].compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind != ':' || simple->sub == NULL || simple->pseudo == PSEUDO_HAS) {
                    continue;
                }
                if (simple->pseudo == PSEUDO_NTH_CHILD || simple->pseudo == PSEUDO_NTH_LAST_CHILD) {
                    if (sel_alts_write_scope(simple->sub, simple->sub_count)) {
                        return 1;
                    }
                    continue;
                }
                for (int sub = 0; sub < simple->sub_count; sub++) {
                    if ((simple->sub[sub].count > 1 && sel_alts_write_scope(&simple->sub[sub], 1)) ||
                        sel_alts_scope_reaches(&simple->sub[sub], 1)) {
                        return 1;
                    }
                }
            }
        }
    }
    return 0;
}

static int sel_compound_writes_scope(sel_compound *compound) {
    return sel_alts_write_scope(&(const sel_complex){compound, 1, 0, 0, 0}, 1);
}

/* Whether complex runs from a lone :scope through + and ~ over compounds without :scope; with universal compounds
   between the two ends, an offset chain (SEL_ALT_SCOPE_OFFSET), or with + alone, a fixed one. */
static int sel_scope_chain(const sel_complex *complex, int offset) {
    const sel_compound *first = &complex->compounds[0];
    if (complex->count < 2 || first->count != 1 || first->simples[0].kind != ':' ||
        first->simples[0].pseudo != PSEUDO_SCOPE) {
        return 0;
    }
    for (int index = 1; index < complex->count; index++) {
        sel_compound *compound = &complex->compounds[index];
        const int universal = compound->count == 1 && compound->simples[0].kind == '*';
        if (compound->combinator != '+' && (!offset || compound->combinator != '~')) {
            return 0;
        }
        if (offset && index < complex->count - 1 ? !universal : sel_compound_writes_scope(compound)) {
            return 0;
        }
    }
    return 1;
}

/* sel_alts_scope_reaches for a :has() argument led by + or ~ that joins its compounds with + and ~ alone, so every
   element it tests is a later sibling of the anchor: 0 when its :scope never reaches them, the compound count of the
   longest SEL_ALT_SCOPE_OFFSET chain when every :scope that does is one tested at those elements, as such a chain
   holds the same at every distance from its compound count on, else SEL_SCOPE_REACH_ANY. */
static int sel_alts_scope_reach(const sel_complex *alts, int count) {
    int reach = 0;
    for (int alt = 0; alt < count; alt++) {
        for (int index = 0; index < alts[alt].count; index++) {
            const sel_compound *compound = &alts[alt].compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind != ':' || simple->sub == NULL || simple->pseudo == PSEUDO_HAS) {
                    continue;
                }
                if (simple->pseudo == PSEUDO_NTH_CHILD || simple->pseudo == PSEUDO_NTH_LAST_CHILD) {
                    if (sel_alts_write_scope(simple->sub, simple->sub_count)) {
                        return SEL_SCOPE_REACH_ANY;
                    }
                    continue;
                }
                for (int sub = 0; sub < simple->sub_count; sub++) {
                    const sel_complex *nested = &simple->sub[sub];
                    int inner = nested->count;
                    if (!(nested->walks & SEL_ALT_SCOPE_OFFSET) && !sel_scope_chain(nested, 0)) {
                        inner = nested->count > 1 && sel_alts_write_scope(nested, 1) ? SEL_SCOPE_REACH_ANY
                                                                                     : sel_alts_scope_reach(nested, 1);
                    }
                    reach = inner > reach ? inner : reach;
                }
            }
        }
    }
    return reach;
}

static int sel_compound_has_scope(const sel_compound *compound);
static int sel_compound_writes_any_scope(const sel_compound *compound);

/* Whether every :scope compound writes is tested among the siblings of the element compound is matched at: a lone
   :scope, or one in a list alternative that joins its compounds with + and ~ alone. A :has() argument reaches such a
   compound through > or a descendant combinator only below the anchor or a sibling of it, where the anchor is never
   among those siblings, so the compound answers the same under every anchor. */
static int sel_compound_scope_confined(const sel_compound *compound) {
    for (int at = 0; at < compound->count; at++) {
        const sel_simple *simple = &compound->simples[at];
        if (simple->kind != ':' || simple->sub == NULL || simple->pseudo == PSEUDO_HAS) {
            continue;
        }
        for (int alt = 0; alt < simple->sub_count; alt++) {
            const sel_complex *nested = &simple->sub[alt];
            for (int index = 0; index < nested->count; index++) {
                if ((index > 0 && nested->compounds[index].combinator != '+' &&
                     nested->compounds[index].combinator != '~' && sel_alts_write_scope(nested, 1)) ||
                    !sel_compound_scope_confined(&nested->compounds[index])) {
                    return 0;
                }
            }
        }
    }
    return 1;
}

/* sel_complex.scope_reach of rel, a :has() argument. */
static int sel_rel_scope_reach(const sel_complex *rel) {
    for (int index = 0; index < rel->count; index++) {
        if (rel->compounds[index].combinator != '+' && rel->compounds[index].combinator != '~') {
            int confined = !(rel->count > 1 && sel_compound_has_scope(&rel->compounds[0]));
            for (int at = 0; at < rel->count && confined; at++) {
                confined = at < index ? !sel_compound_writes_any_scope(&rel->compounds[at])
                                      : sel_compound_scope_confined(&rel->compounds[at]);
            }
            return confined || !sel_alts_scope_reaches(rel, 1) ? 0 : SEL_SCOPE_REACH_ANY;
        }
    }
    return sel_alts_scope_reach(rel, 1);
}

/* Whether simple writes :scope outside a :has(). */
static int sel_simple_writes_scope(const sel_simple *simple) {
    return simple->kind == ':' &&
           (simple->pseudo == PSEUDO_SCOPE || (simple->sub != NULL && simple->pseudo != PSEUDO_HAS &&
                                               sel_alts_write_scope(simple->sub, simple->sub_count)));
}

static int sel_compound_writes_any_scope(const sel_compound *compound) {
    for (int at = 0; at < compound->count; at++) {
        if (sel_simple_writes_scope(&compound->simples[at])) {
            return 1;
        }
    }
    return 0;
}

/* Whether complex is a lone :scope. */
static int sel_lone_scope(const sel_complex *complex) {
    const sel_compound *compound = &complex->compounds[0];
    return complex->count == 1 && compound->count == 1 && compound->simples[0].kind == ':' &&
           compound->simples[0].pseudo == PSEUDO_SCOPE;
}

/* Whether simple is :not(:scope). */
static int sel_simple_not_scope(const sel_simple *simple) {
    return simple->kind == ':' && simple->pseudo == PSEUDO_NOT && simple->sub_count == 1 && sel_lone_scope(simple->sub);
}

/* sel_sweep_chain_kind of a chain whose first compound holds :not(:scope) */
#define SEL_SWEEP_NOT_SCOPE (-2)

/* How sel_deep_has_sweep reads alt, a chain of compounds that writes :scope: the index of the one compound that does,
   as a lone :scope beside simples that do not, with only + and ~ right of it; SEL_SWEEP_NOT_SCOPE when that compound
   leads the chain and holds :not(:scope) instead; -1 for any other chain. */
static int sel_sweep_chain_kind(const sel_complex *alt) {
    int scope = -1;
    for (int index = 0; index < alt->count; index++) {
        if (sel_compound_writes_any_scope(&alt->compounds[index])) {
            if (scope >= 0) {
                return -1;
            }
            scope = index;
        }
    }
    for (int index = scope + 1; index < alt->count; index++) {
        if (alt->compounds[index].combinator != '+' && alt->compounds[index].combinator != '~') {
            return -1;
        }
    }
    const sel_compound *compound = &alt->compounds[scope];
    int lone = 0;
    int negated = 0;
    for (int at = 0; at < compound->count; at++) {
        const sel_simple *simple = &compound->simples[at];
        if (simple->kind == ':' && simple->pseudo == PSEUDO_SCOPE) {
            lone++;
        } else if (sel_simple_not_scope(simple)) {
            negated++;
        } else if (sel_simple_writes_scope(simple)) {
            return -1;
        }
    }
    if (lone + negated != 1) {
        return -1;
    }
    return lone ? scope : scope == 0 ? SEL_SWEEP_NOT_SCOPE : -1;
}

/* Whether every alternative of the S of an 'of S' clause is a lone :scope, or a SEL_ALT_SCOPE_OFFSET chain that ends
   in a universal compound: S then holds by the distance from the anchor alone. */
static int sel_sweep_offsets(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        const sel_compound *last = &alts[alt].compounds[alts[alt].count - 1];
        if (!sel_lone_scope(&alts[alt]) &&
            !(sel_scope_chain(&alts[alt], 1) && last->count == 1 && last->simples[0].kind == '*')) {
            return 0;
        }
    }
    return 1;
}

static int sel_sweeps_alts(const sel_complex *alts, int count);

/* Whether sel_deep_has_sweep reads the :scope compound writes. */
static int sel_sweeps_compound(const sel_compound *compound) {
    for (int at = 0; at < compound->count; at++) {
        const sel_simple *simple = &compound->simples[at];
        if (!sel_simple_writes_scope(simple) || simple->pseudo == PSEUDO_SCOPE) {
            continue;
        }
        if (simple->pseudo == PSEUDO_NTH_CHILD || simple->pseudo == PSEUDO_NTH_LAST_CHILD
                ? !sel_sweep_offsets(simple->sub, simple->sub_count)
                : !sel_sweeps_alts(simple->sub, simple->sub_count)) {
            return 0;
        }
    }
    return 1;
}

static int sel_sweeps_alts(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        if (!sel_alts_write_scope(&alts[alt], 1)) {
            continue;
        }
        if (alts[alt].count == 1 ? !sel_sweeps_compound(&alts[alt].compounds[0])
                                 : sel_sweep_chain_kind(&alts[alt]) == -1) {
            return 0;
        }
    }
    return 1;
}

/* sel_complex.sweep_at of rel, a :has() argument: one compound past a leading :scope writes :scope, the anchor reaches
   it through + and ~ alone, and sel_deep_has_sweep reads what it writes. */
static int sel_rel_sweep_at(const sel_complex *rel) {
    const int pinned = rel->count > 1 && sel_compound_has_scope(&rel->compounds[0]);
    int dependent = -1;
    int below = 0; /* past a > or descendant combinator, where sel_compound_scope_confined holds */
    for (int index = pinned; index < rel->count; index++) {
        below |= rel->compounds[index].combinator != '+' && rel->compounds[index].combinator != '~';
        if (below ? !sel_compound_scope_confined(&rel->compounds[index])
                  : sel_compound_writes_any_scope(&rel->compounds[index])) {
            if (below || dependent >= 0) {
                return 0;
            }
            dependent = index;
        }
    }
    if (dependent < 0) {
        return 0;
    }
    for (int at = 0; pinned && at < rel->compounds[0].count; at++) {
        const sel_simple *simple = &rel->compounds[0].simples[at];
        if (simple->pseudo != PSEUDO_SCOPE && sel_simple_writes_scope(simple)) {
            return 0;
        }
    }
    return sel_sweeps_compound(&rel->compounds[dependent]) ? dependent + 1 : 0;
}

/* What sel_alts_nest counts: marks, the lists it marks (sel_simple.nest); routes, those only the deep matcher keeps the
   answers of on any tree; slots, the 'of S' clauses. scoped: the selector holds a :scope, without which no :has()
   argument reads it (sel_complex.scope_reach, sel_complex.sweep_at) and no alternative takes a mark of
   sel_alt_scope_walks. */
typedef struct {
    int marks;
    int routes;
    int slots;
    int scoped;
} sel_nesting;

static int sel_alts_nest(sel_complex *alts, int count, int asked_again, int in_of, sel_nesting *nesting);

/* How matching simple can walk (SEL_WALKS_UP, SEL_WALKS_WIDE, SEL_WALKS_SIBLINGS), marking the lists inside it
   (sel_alts_nest). asked_again: one match can ask simple about the same element more than once. */
static int sel_simple_nest(sel_simple *simple, int asked_again, int in_of, sel_nesting *nesting) {
    if (simple->kind != ':') {
        return 0;
    }
    switch (simple->pseudo) {
    case PSEUDO_LANG:
    case PSEUDO_DIR:
        return SEL_WALKS_UP;
    case PSEUDO_HAS: {
        /* a :has() argument is tested at every element of the walks of every anchor, and anchors share elements */
        const int walks = sel_alts_nest(simple->sub, simple->sub_count, 1, 0, nesting);
        for (int alt = 0; alt < simple->sub_count; alt++) {
            sel_complex *rel = &simple->sub[alt];
            rel->scope_reach = nesting->scoped ? sel_rel_scope_reach(rel) : 0;
            rel->sweep_at = nesting->scoped ? sel_rel_sweep_at(rel) : 0;
        }
        simple->nest = asked_again;
        nesting->marks += asked_again;
        nesting->routes += asked_again;
        return SEL_WALKS_WIDE | (walks & SEL_WALKS_SIBLINGS);
    }
    case PSEUDO_IS:
    case PSEUDO_WHERE:
    case PSEUDO_NOT: {
        const int marked = nesting->marks;
        int walks = sel_alts_nest(simple->sub, simple->sub_count, asked_again, in_of, nesting);
        /* a list asked again inside this one multiplies the cost of a question by the walk again */
        walks |= nesting->marks != marked ? SEL_WALKS_WIDE : 0;
        simple->nest = !asked_again ? 0 : (walks & SEL_WALKS_WIDE) ? SEL_NEST_ALWAYS : walks != 0 ? SEL_NEST_DEEP : 0;
        nesting->marks += simple->nest != 0;
        nesting->routes += simple->nest == SEL_NEST_ALWAYS;
        return walks;
    }
    case PSEUDO_NTH_CHILD:
    case PSEUDO_NTH_LAST_CHILD:
        if (simple->sub != NULL) {
            /* S is tested at every sibling the count passes; the deep matcher counts every clause in its own slot */
            const int walks = sel_alts_nest(simple->sub, simple->sub_count, 1, 1, nesting);
            simple->nest = ++nesting->slots;
            nesting->marks++;
            nesting->routes += walks != 0 || asked_again;
            return SEL_WALKS_WIDE | (walks & SEL_WALKS_SIBLINGS);
        }
        return in_of ? SEL_WALKS_WIDE : 0;
    case PSEUDO_NTH_OF_TYPE:
    case PSEUDO_NTH_LAST_OF_TYPE:
    case PSEUDO_FIRST_OF_TYPE:
    case PSEUDO_LAST_OF_TYPE:
    case PSEUDO_ONLY_OF_TYPE:
        return in_of ? SEL_WALKS_WIDE : 0;
    default:
        return 0;
    }
}

/* How matching alts can walk (SEL_WALKS_UP, SEL_WALKS_WIDE, SEL_WALKS_SIBLINGS): a combinator, :has(), :lang(), :dir(),
   a nested list that walks, or inside an 'of S' clause (in_of) a sibling position. Sets sel_complex.walks on each
   alternative and sel_simple.nest on each list that walks and that one match asks about the same element more than
   once: one left of a combinator, which the combinator's walk tests at every element it passes, or one inside such a
   list, a :has() or an 'of S' clause. The matcher with no memo pays a walk for every such question, so each nesting
   level multiplied the cost of a match by the tree depth or the sibling count. */
/* The marks of complex that a :scope in it sets: SEL_ALT_SCOPED, SEL_ALT_AHEAD and SEL_ALT_SCOPE_OFFSET. */
static int sel_alt_scope_walks(const sel_complex *complex) {
    return (sel_alts_write_scope(complex, 1) ? SEL_ALT_SCOPED : 0) |
           (sel_alts_scope_ahead(complex, 1) ? SEL_ALT_AHEAD : 0) |
           (sel_scope_chain(complex, 1) ? SEL_ALT_SCOPE_OFFSET : 0);
}

static int sel_alts_nest(sel_complex *alts, int count, int asked_again, int in_of, sel_nesting *nesting) {
    int walks = 0;
    for (int alt = 0; alt < count; alt++) {
        sel_complex *complex = &alts[alt];
        int alt_walks = 0;
        for (int index = 0; index < complex->count; index++) {
            sel_compound *compound = &complex->compounds[index];
            if (compound->combinator == '~') {
                alt_walks |= SEL_WALKS_WIDE | SEL_WALKS_SIBLINGS;
            } else if (index > 0) {
                alt_walks |= SEL_WALKS_UP;
            }
            for (int at = 0; at < compound->count; at++) {
                alt_walks |=
                    sel_simple_nest(&compound->simples[at], asked_again || index < complex->count - 1, in_of, nesting);
            }
        }
        complex->walks = (alt_walks ? SEL_ALT_WALKS : 0) | (alt_walks & SEL_WALKS_SIBLINGS ? SEL_ALT_SIBLINGS : 0) |
                         (nesting->scoped ? sel_alt_scope_walks(complex) : 0);
        walks |= alt_walks;
    }
    return walks;
}

/* The C stack one compound of a chain may take in any matcher family: about three times the most measured with gcc
       -O2 and LTO (a :has() sibling split, 0.33 KiB), leaving room for the frames of unoptimized and other compilers.
     */
enum { SEL_CHAIN_BYTES = 1024 };

#ifndef TH_STACK_FLOOR
/* Where the platform reports no stack bounds, the room assumed left: a 512 KiB thread less the margin other stack
   guards keep and the frames of the caller, as the JavaScript parser assumes. */
enum { SEL_FALLBACK_STACK = 384 * 1024 };
#endif

/* The most compounds a match of alts recurses through. Each combinator step of the matcher goes one call deeper per
   compound, and the list of a functional pseudo-class starts its own chain on top, so the chains add up along the
   nesting. */
static Py_ssize_t sel_alts_chain(const sel_complex *alts, int count) {
    Py_ssize_t longest = 0;
    for (int alt = 0; alt < count; alt++) {
        const sel_complex *complex = &alts[alt];
        Py_ssize_t nested = 0;
        for (int index = 0; index < complex->count; index++) {
            const sel_compound *compound = &complex->compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind == ':' && simple->sub != NULL) {
                    const Py_ssize_t inner = sel_alts_chain(simple->sub, simple->sub_count);
                    nested = inner > nested ? inner : nested;
                }
            }
        }
        const Py_ssize_t chain = complex->count + nested;
        longest = chain > longest ? chain : longest;
    }
    return longest;
}

TH_NOINLINE int sel_stack_check_chain(Py_ssize_t chain) {
#ifdef TH_STACK_FLOOR
    const intptr_t room = (intptr_t)(TH_FRAME_ADDRESS() - th_stack_floor());
#else
    const intptr_t room = SEL_FALLBACK_STACK;
#endif
    if (room / SEL_CHAIN_BYTES >= chain) {
        return 0;
    }
    PyErr_Format(PyExc_RecursionError,
                 "the selector chains %zd compound selectors, more than the C stack this thread has left can match; "
                 "split it, or run it on a thread with a larger stack",
                 chain);
    return -1;
}

/* Whether a :has() sits inside another :has() argument, at any depth. */
static int sel_alts_nest_has(const sel_complex *alts, int count, int inside) {
    for (int alt = 0; alt < count; alt++) {
        const sel_complex *complex = &alts[alt];
        for (int index = 0; index < complex->count; index++) {
            const sel_compound *compound = &complex->compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind == ':' && simple->sub != NULL) {
                    const int has = simple->pseudo == PSEUDO_HAS;
                    if ((has && inside) || sel_alts_nest_has(simple->sub, simple->sub_count, inside || has)) {
                        return 1;
                    }
                }
            }
        }
    }
    return 0;
}

/* Whether a :has() argument leads with a sibling combinator. */
static int sel_alts_lead_sibling(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        const char combinator = alts[alt].compounds[0].combinator;
        if (combinator == '~' || combinator == '+') {
            return 1;
        }
    }
    return 0;
}

/* Whether a :has() led by a sibling combinator sits anywhere in alts. */
static int sel_alts_sibling_has_at(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        for (int index = 0; index < alts[alt].count; index++) {
            const sel_compound *compound = &alts[alt].compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind == ':' && simple->sub != NULL &&
                    ((simple->pseudo == PSEUDO_HAS && sel_alts_lead_sibling(simple->sub, simple->sub_count)) ||
                     sel_alts_sibling_has_at(simple->sub, simple->sub_count))) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

/* Whether a :has() led by a sibling combinator, outside any other :has(), holds another such :has(). The sibling
   table of the non-deep matcher keeps those walks, which the deep matcher does not reproduce. */
static int sel_alts_nest_sibling_has(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        for (int index = 0; index < alts[alt].count; index++) {
            const sel_compound *compound = &alts[alt].compounds[index];
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind != ':' || simple->sub == NULL) {
                    continue;
                }
                if (simple->pseudo == PSEUDO_HAS) {
                    if (sel_alts_lead_sibling(simple->sub, simple->sub_count) &&
                        sel_alts_sibling_has_at(simple->sub, simple->sub_count)) {
                        return 1;
                    }
                } else if (sel_alts_nest_sibling_has(simple->sub, simple->sub_count)) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

/* Whether alts holds a nested list the nesting analysis routes or keeps: a :has() or an 'of S' clause, or a
   :is()/:where()/:not() argument with a combinator or a list of its own. A plain argument walks nothing. A ~
   combinator needs it too, for the SEL_ALT_SIBLINGS mark the cascade routes on. */
static int sel_alts_need_nest(const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        for (int index = 0; index < alts[alt].count; index++) {
            const sel_compound *compound = &alts[alt].compounds[index];
            if (compound->combinator == '~') {
                return 1;
            }
            for (int at = 0; at < compound->count; at++) {
                const sel_simple *simple = &compound->simples[at];
                if (simple->kind != ':' || simple->sub == NULL) {
                    continue;
                }
                if (simple->pseudo == PSEUDO_HAS || simple->pseudo == PSEUDO_NTH_CHILD ||
                    simple->pseudo == PSEUDO_NTH_LAST_CHILD) {
                    return 1;
                }
                for (int argument = 0; argument < simple->sub_count; argument++) {
                    if (simple->sub[argument].count > 1) {
                        return 1;
                    }
                }
                if (sel_alts_need_nest(simple->sub, simple->sub_count)) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

/* Compile a selector string against the tree it will run on. Returns NULL with a
   SelectorSyntaxError set on a syntax error. */
sel_compiled *selector_compile(PyObject *selector_error, th_tree *tree, PyObject *selector_str) {
    sel_compiled *compiled = PyMem_Calloc(1, sizeof(sel_compiled));
    if (compiled == NULL) {              /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return (void *)PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    compiled->source = PyUnicode_AsUCS4Copy(selector_str);
    if (compiled->source == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(compiled);       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    compiled->quirks = th_tree_quirks(tree);
    compiled->tree = tree;
    sel_parser parser = {
        compiled->source, 0, PyUnicode_GET_LENGTH(selector_str), tree, 0, 0, 0, "unexpected token", 0, 0};
    if (sel_parse_alts(&parser, &compiled->alts, &compiled->count, 0, 0, 0) < 0) {
        sel_raise(selector_error, selector_str, &parser);
        PyMem_Free(compiled->source);
        PyMem_Free(compiled);
        return NULL;
    }
    compiled->has_relational = sel_alts_have_pseudo(compiled->alts, compiled->count, sel_is_has_pseudo);
    compiled->reads_legend = sel_alts_have_pseudo(compiled->alts, compiled->count, sel_is_legend_pseudo);
    compiled->reads_dir = sel_alts_have_pseudo(compiled->alts, compiled->count, sel_is_dir_pseudo);
    compiled->climbs = sel_alts_climb(compiled->alts, compiled->count, 0);
    /* the parsed total bounds the chain, so the walk runs only for a selector long enough to reach the check */
    compiled->chain =
        parser.compounds > SEL_CHAIN_CHECK ? sel_alts_chain(compiled->alts, compiled->count) : parser.compounds;
    if (sel_alts_need_nest(compiled->alts, compiled->count)) {
        compiled->nests_has = sel_alts_nest_has(compiled->alts, compiled->count, 0);
        /* the clauses number on from a hash of the selector (sel_simple.nest), so those of selectors compiled apart,
           which one cascade memo keys together, start apart */
        sel_nesting nesting = {.slots = (int)(((uint64_t)(uintptr_t)compiled * 0x9E3779B97F4A7C15ull) >> 34),
                               .scoped = parser.scoped};
        sel_alts_nest(compiled->alts, compiled->count, 0, 0, &nesting);
        if (nesting.routes != 0 && !sel_alts_nest_sibling_has(compiled->alts, compiled->count)) {
            /* only the deep matcher keeps the answers of a list asked again */
            compiled->climbs = SEL_CLIMBS_LISTS;
        }
    }
    return compiled;
}

/* The functional pseudo-classes recurse into the matcher: :is()/:where() test the
   element against a nested list, and :has() searches for a relative match, so the
   matching primitives they call are forward-declared here. */
static int sel_match_compound(th_node *node, const sel_compound *compound, const sel_ctx *ctx);
static int sel_matches_alts(th_node *node, const sel_complex *alts, int count, const sel_ctx *ctx);
static int sel_has_match(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx);
static int sel_memo_get(const sel_memo *memo, const void *key, const th_node *node, int *out);
static void sel_memo_put(sel_memo *memo, const void *key, const th_node *node, int result);

int sel_eq(const Py_UCS4 *left, Py_ssize_t alen, const Py_UCS4 *right, Py_ssize_t blen, int ci) {
    if (alen != blen) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < alen; index++) {
        if (sel_fold(left[index], ci) != sel_fold(right[index], ci)) {
            return 0;
        }
    }
    return 1;
}

/* Whether a whitespace-separated token list contains the wanted value. */
static int contains_ws_token(const Py_UCS4 *value, Py_ssize_t value_len, const Py_UCS4 *want, Py_ssize_t want_len,
                             int ci) {
    Py_ssize_t cursor = 0;
    while (cursor < value_len) {
        while (cursor < value_len && is_space(value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < value_len && !is_space(value[cursor])) {
            cursor++;
        }
        if (cursor > start && sel_eq(value + start, cursor - start, want, want_len, ci)) {
            TH_WORK_STEPS_ADD(cursor);
            return 1;
        }
    }
    TH_WORK_STEPS_ADD(value_len);
    return 0;
}

static int sel_substr_kmp(const Py_UCS4 *value, Py_ssize_t value_len, const Py_UCS4 *want, Py_ssize_t want_len,
                          Py_ssize_t offset, int ci) {
    /* GCOVR_EXCL_START: needs a 32-bit build and a selector string of over a billion code points */
    Py_ssize_t *prefix = TH_UNLIKELY((size_t)want_len > SIZE_MAX / sizeof(Py_ssize_t))
                             ? NULL
                             : PyMem_Malloc((size_t)want_len * sizeof(*prefix));
    /* GCOVR_EXCL_STOP */
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: the caller retains the allocation-free scan */
    }
    sel_kmp_prefix(want, want_len, ci, prefix);
    Py_ssize_t matched = 0;
    for (Py_ssize_t index = offset; index < value_len; index++) {
        TH_WORK_STEP();
        while (matched > 0 && sel_fold(value[index], ci) != sel_fold(want[matched], ci)) {
            matched = prefix[matched - 1];
        }
        if (sel_fold(value[index], ci) == sel_fold(want[matched], ci)) {
            matched++;
            if (matched == want_len) {
                PyMem_Free(prefix);
                return 1;
            }
        }
    }
    PyMem_Free(prefix);
    return 0;
}

static int sel_substr_long(const Py_UCS4 *value, Py_ssize_t value_len, const Py_UCS4 *want, Py_ssize_t want_len,
                           int ci) {
    if (want_len > value_len) {
        return 0;
    }
    if (value_len < 256) {
        for (Py_ssize_t start = 0; start + want_len <= value_len; start++) {
            if (sel_eq(value + start, want_len, want, want_len, ci)) {
                return 1;
            }
        }
        return 0;
    }
    size_t candidates = 64;
    Py_ssize_t last = want_len - 1;
    Py_UCS4 last_ch = sel_fold(want[last], ci);
    for (Py_ssize_t start = 0; start + want_len <= value_len; start++) {
        TH_WORK_STEP();
        if (sel_fold(value[start + last], ci) != last_ch) {
            continue;
        }
        if (sel_eq(value + start, want_len, want, want_len, ci)) {
            return 1;
        }
        if (candidates != SIZE_MAX) { /* GCOVR_EXCL_BR_LINE: SIZE_MAX only after unforceable allocation failure */
            candidates--;
            if (candidates == 0) {
                int found = sel_substr_kmp(value, value_len, want, want_len, start + 1, ci);
                if (found >= 0) { /* GCOVR_EXCL_BR_LINE: negative only on unforceable allocation failure */
                    return found;
                }
                /* GCOVR_EXCL_START: retain the allocation-free scan after an unforceable allocation failure */
                candidates = SIZE_MAX;
            }
            /* GCOVR_EXCL_STOP */
        }
    }
    return 0;
}

static const th_node_attr *sel_find_attr(th_node *node, uint32_t atom) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

/* Whether the attribute value satisfies the simple's operator and value, folding
   case per ci (the explicit flag combined with the HTML default-set decision). */
static int sel_match_attr_op(const sel_simple *simple, const Py_UCS4 *value, Py_ssize_t value_len, int ci) {
    const Py_UCS4 *want = simple->value;
    Py_ssize_t want_len = simple->value_len;
    switch (simple->op) {
    case OP_EXISTS:
        return 1;
    case OP_EQ:
        return sel_eq(value, value_len, want, want_len, ci);
    case OP_PREFIX:
        return want_len > 0 && value_len >= want_len && sel_eq(value, want_len, want, want_len, ci);
    case OP_SUFFIX:
        return want_len > 0 && value_len >= want_len &&
               sel_eq(value + (value_len - want_len), want_len, want, want_len, ci);
    case OP_DASH:
        return sel_eq(value, value_len, want, want_len, ci) ||
               (value_len > want_len && value[want_len] == '-' && sel_eq(value, want_len, want, want_len, ci));
    case OP_INCLUDE: {
        if (want_len == 0) {
            return 0;
        }
        return contains_ws_token(value, value_len, want, want_len, ci);
    }
    default: /* OP_SUBSTR */
        if (want_len == 0) {
            return 0;
        }
        for (Py_ssize_t start = 0; start + want_len <= value_len; start++) {
            TH_WORK_STEP();
            if (sel_eq(value + start, want_len, want, want_len, ci)) {
                return 1;
            }
        }
        return 0;
    }
}

/* A class, ~= or *= test on a long value. Each reads the whole value, and a query asks the
   same one about the same value many times: the compound left of a combinator is tested on an
   ancestor once per candidate below it, and every formatting element the tree builder
   re-creates shares its source's value buffer (node_clone). The answer depends on the simple
   selector, the case folding and the value alone, so the value memo keeps it under the simple
   offset by the folding and the buffer, and the value is read once per selector. */
static TH_NOINLINE int sel_match_long_value(const th_node *node, const sel_simple *simple, const Py_UCS4 *value,
                                            Py_ssize_t value_len, int ci, const sel_ctx *ctx) {
    const void *key = (const char *)simple + (ci != 0);
    int matched;
    if (sel_value_memo_get(ctx->value_memo, key, node, value, &matched)) {
        return matched;
    }
    if (simple->kind == '.') {
        matched = contains_ws_token(value, value_len, simple->name, simple->name_len, ci);
    } else if (simple->kind == ':') { /* PSEUDO_SUBSTR_LONG */
        matched = sel_substr_long(value, value_len, simple->value, simple->value_len, ci);
    } else {
        matched = sel_match_attr_op(simple, value, value_len, ci);
    }
    sel_value_memo_put(ctx->value_memo, key, node, value, matched);
    return matched;
}

/* Two elements share a type when their namespace and tag match; a custom tag
   (no builtin atom) compares by lowercased name so distinct customs stay apart. */
int sel_same_type(const th_node *a, const th_node *b) {
    if (a->ns != b->ns || a->atom != b->atom) {
        return 0;
    }
    if (a->atom != TH_TAG_UNKNOWN) {
        return 1;
    }
    return sel_eq(a->text, a->text_len, b->text, b->text_len, 0);
}

/* Whether node has no element sibling on the chosen side (next when from_end),
   counting only same-type siblings when of_type. Drives the first/last/only
   structural pseudo-classes. */
int sel_no_sibling(th_node *node, int from_end, int of_type) {
    for (th_node *sibling = from_end ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = from_end ? sibling->next_sibling : sibling->prev_sibling) {
        if (sibling->type == TH_NODE_ELEMENT && (!of_type || sel_same_type(node, sibling))) {
            return 0;
        }
    }
    return 1;
}

/* A position walk this short runs without the memo, as the sibling walks of the general
   sibling combinator do (SEL_SIBLING_MEMO_MIN_RUN). */
#define SEL_POSITION_MEMO_MIN_RUN 32

typedef struct {
    const th_node *node;
    size_t order;
} sel_position_item;

/* Group elements by type, in any total order that agrees with sel_same_type, and keep the
   walk order inside a type. */
static int sel_position_cmp(const void *left, const void *right) {
    const sel_position_item *left_item = left;
    const sel_position_item *right_item = right;
    const th_node *left_node = left_item->node;
    const th_node *right_node = right_item->node;
    if (left_node->ns != right_node->ns) {
        return left_node->ns < right_node->ns ? -1 : 1;
    }
    if (left_node->atom != right_node->atom) {
        return left_node->atom < right_node->atom ? -1 : 1;
    }
    if (left_node->atom == TH_TAG_UNKNOWN) {
        if (left_node->text_len != right_node->text_len) {
            return left_node->text_len < right_node->text_len ? -1 : 1;
        }
        for (Py_ssize_t index = 0; index < left_node->text_len; index++) {
            if (left_node->text[index] != right_node->text[index]) {
                return left_node->text[index] < right_node->text[index] ? -1 : 1;
            }
        }
    }
    /* Walk order is the final key, so the siblings of one type keep their order. Branch-free:
       which of the two a qsort passes on the left depends on the libc. */
    return (left_item->order > right_item->order) - (left_item->order < right_item->order);
}

/* Store the position of every element sibling of node in map and return node's own, or 0
   when an allocation fails and the caller keeps its direct walk. Same-type positions come
   from sorting the siblings by type instead of comparing each with all the others, so the
   pass costs n log n however many types the list mixes. */
static Py_ssize_t sel_position_index(th_node_map *map, const th_node *node, int from_end, int of_type) {
    const th_node *start = node;
    for (const th_node *sibling = from_end ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = from_end ? sibling->next_sibling : sibling->prev_sibling) {
        start = sibling;
    }
    size_t count = 0;
    for (const th_node *sibling = start; sibling != NULL;
         sibling = from_end ? sibling->prev_sibling : sibling->next_sibling) {
        count += sibling->type == TH_NODE_ELEMENT;
    }
    /* GCOVR_EXCL_START: needs a 32-bit build and over a billion siblings; the caller keeps its direct walk */
    sel_position_item *items = TH_UNLIKELY(count > SIZE_MAX / sizeof(sel_position_item))
                                   ? NULL
                                   : PyMem_Malloc(count * sizeof(sel_position_item));
    /* GCOVR_EXCL_STOP */
    /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
    if (items == NULL || th_node_map_reserve(map, map->count + count) < 0) {
        PyMem_Free(items);
        return 0;
    }
    /* GCOVR_EXCL_STOP */
    size_t filled = 0;
    for (const th_node *sibling = start; sibling != NULL;
         sibling = from_end ? sibling->prev_sibling : sibling->next_sibling) {
        if (sibling->type == TH_NODE_ELEMENT) {
            items[filled] = (sel_position_item){sibling, filled};
            filled++;
        }
    }
    if (of_type) {
        qsort(items, count, sizeof(sel_position_item), sel_position_cmp);
    }
    Py_ssize_t position = 0;
    for (size_t index = 0; index < count; index++) {
        const int same_run = index > 0 && (!of_type || sel_same_type(items[index - 1].node, items[index].node));
        position = same_run ? position + 1 : 1;
        (void)th_node_map_insert(map, items[index].node, position); /* reserved above, so it cannot fail */
    }
    PyMem_Free(items);
    return th_node_map_find(map, node);
}

/* The memoized sel_sibling_index of node, indexing node's sibling list on a miss, or 0
   without a memo (single-element matching) and when indexing could not allocate. */
static int sel_position(const sel_ctx *ctx, const th_node *node, int from_end, int of_type) {
    if (ctx->position_memo == NULL) {
        return 0;
    }
    th_node_map *map = &ctx->position_memo->index[from_end][of_type];
    const Py_ssize_t position = th_node_map_find(map, node);
    return (int)(position != 0 ? position : sel_position_index(map, node, from_end, of_type));
}

void sel_position_memo_free(sel_position_memo *memo) {
    for (int from_end = 0; from_end < 2; from_end++) {
        for (int of_type = 0; of_type < 2; of_type++) {
            PyMem_Free(memo->index[from_end][of_type].entries);
            memo->index[from_end][of_type] = (th_node_map){0};
        }
    }
}

/* sel_no_sibling for the same-type question. A sibling of another type does not end the
   walk, so among siblings of many types every candidate would walk to the end of the list:
   past SEL_POSITION_MEMO_MIN_RUN siblings the position memo answers instead. */
static int sel_no_type_sibling(th_node *node, int from_end, const sel_ctx *ctx) {
    int walked = 0;
    for (th_node *sibling = from_end ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = from_end ? sibling->next_sibling : sibling->prev_sibling) {
        TH_WORK_STEP();
        if (sibling->type == TH_NODE_ELEMENT && sel_same_type(node, sibling)) {
            return 0;
        }
        if (++walked == SEL_POSITION_MEMO_MIN_RUN) {
            const int position = sel_position(ctx, node, from_end, 1);
            if (position != 0) {
                return position == 1;
            }
        }
    }
    return 1;
}

/* The 1-based position of node among its element siblings (from the end when
   from_end), counting only same-type siblings when of_type. Past
   SEL_POSITION_MEMO_MIN_RUN siblings the position memo answers, so a query that has to
   count for many siblings of one long list walks it once. */
static int sel_sibling_index(th_node *node, int from_end, int of_type, const sel_ctx *ctx) {
    int index = 1;
    int walked = 0;
    for (th_node *sibling = from_end ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = from_end ? sibling->next_sibling : sibling->prev_sibling) {
        TH_WORK_STEP();
        if (sibling->type == TH_NODE_ELEMENT && (!of_type || sel_same_type(node, sibling))) {
            index++;
        }
        if (++walked == SEL_POSITION_MEMO_MIN_RUN) {
            const int position = sel_position(ctx, node, from_end, of_type);
            if (position != 0) {
                return position;
            }
        }
    }
    return index;
}

/* The 1-based position of node among its inclusive siblings that match the
   :nth-child(... of S) selector list (from the end when from_end), or 0 when node
   itself does not match S, so a non-matching element is never selected. */
static int sel_nth_of_index(th_node *node, int from_end, const sel_simple *simple, const sel_ctx *ctx) {
    int index = 1;
    for (th_node *sibling = from_end ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = from_end ? sibling->next_sibling : sibling->prev_sibling) {
        if (sibling->type == TH_NODE_ELEMENT && sel_matches_alts(sibling, simple->sub, simple->sub_count, ctx)) {
            index++;
        }
    }
    return index;
}

/* Recounting sibling positions makes wide-tree traversal quadratic. */
static int sel_nth_index(th_node *node, int direction, int of_type, const sel_simple *simple, const sel_ctx *ctx) {
    if (simple->sub != NULL && !sel_matches_alts(node, simple->sub, simple->sub_count, ctx)) {
        return 0;
    }
    if (ctx->nth_memo != NULL) {
        sel_nth_memo *previous = ctx->nth_memo;
        if (previous->simple == simple && (simple->sub == NULL || previous->scope == ctx->scope) &&
            (!of_type || sel_same_type(previous->node, node))) {
            /* Only a sibling of node's type, or the previous candidate, stops these walks, so
               past SEL_POSITION_MEMO_MIN_RUN siblings sel_sibling_index takes over and answers
               from the position memo; the walks of an "of S" argument have no memo to fall to. */
            const int walk_limit = simple->sub == NULL ? SEL_POSITION_MEMO_MIN_RUN : INT_MAX;
            int walked = 0;
            th_node *before = node->prev_sibling;
            while (before != NULL && before != previous->node &&
                   (before->type != TH_NODE_ELEMENT || (of_type && !sel_same_type(node, before)))) {
                if (++walked == walk_limit) {
                    goto walk;
                }
                before = before->prev_sibling;
            }
            if (previous->node == before) {
                const int index = previous->index + direction;
                ctx->nth_memo->node = node;
                ctx->nth_memo->index = index;
                return index;
            }
            th_node *after = node->next_sibling;
            while (after != NULL && after != previous->node &&
                   (after->type != TH_NODE_ELEMENT || (of_type && !sel_same_type(node, after)))) {
                if (++walked == walk_limit) {
                    goto walk;
                }
                after = after->next_sibling;
            }
            if (previous->node == after) {
                const int index = previous->index - direction;
                ctx->nth_memo->node = node;
                ctx->nth_memo->index = index;
                return index;
            }
            if (previous->node->parent == node->parent) {
                if (previous->node == node) {
                    return previous->index;
                }
                const int previous_index = previous->index;
                int distance = 0;
                for (th_node *sibling = previous->node->next_sibling; sibling != NULL;
                     sibling = sibling->next_sibling) {
                    if (++walked == walk_limit) {
                        goto walk;
                    }
                    if (sibling->type == TH_NODE_ELEMENT && (!of_type || sel_same_type(node, sibling)) &&
                        (simple->sub == NULL || sel_matches_alts(sibling, simple->sub, simple->sub_count, ctx))) {
                        distance++;
                    }
                    if (sibling == node) {
                        const int index = previous_index + (direction < 0 ? -distance : distance);
                        *ctx->nth_memo = (sel_nth_memo){node, ctx->scope, simple, index};
                        return index;
                    }
                }
            }
        }
    }
walk:;
    const int index = simple->sub == NULL ? sel_sibling_index(node, direction < 0, of_type, ctx)
                                          : sel_nth_of_index(node, direction < 0, simple, ctx);
    if (ctx->nth_memo != NULL) {
        *ctx->nth_memo = (sel_nth_memo){node, ctx->scope, simple, index};
    }
    return index;
}

/* Whether a 1-based index satisfies An+B for some non-negative n. sel_nth_narrow leaves b non-negative whenever a is
   not 0, and both operands within INT_MAX, so index - b cannot overflow. */
static int sel_nth_matches(int a, int b, int index) {
    if (a == 0) {
        return index == b;
    }
    const int diff = index - b;
    return diff % a == 0 && diff / a >= 0;
}

/* An element is :empty (Selectors-4 §13.2) when it has no children except,
   optionally, document white space: any element child or a text child holding a
   non-whitespace code point makes it non-empty, while comments, processing
   instructions, and whitespace-only text do not count. Level 3 rejected
   whitespace-only elements; Level 4 changed that, so `<p> </p>` now matches. */
static int sel_is_empty(const th_node *node, th_tree *tree) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT) {
            return 0;
        }
        if (child->type == TH_NODE_TEXT && !th_node_text_is_blank(tree, child)) {
            return 0;
        }
    }
    return 1;
}

/* The §12 input pseudo-classes, determinable from the static tree. */

static int sel_has_attr(th_node *node, uint32_t atom) {
    return sel_find_attr(node, atom) != NULL;
}

/* Whether node's type attribute equals kw (ASCII case-insensitive). */
static int sel_input_type_is(th_node *node, const char *kw) {
    const th_node_attr *attr = sel_find_attr(node, TH_ATTR_TYPE);
    return attr != NULL && attr->value != NULL && sel_kw(attr->value, attr->value_len, kw);
}

/* The disableable HTML form controls :enabled/:disabled apply to. */
static int sel_is_disableable(th_node *node) {
    if (node->ns != TH_NS_HTML) {
        return 0;
    }
    switch (node->atom) {
    case TH_TAG_BUTTON:
    case TH_TAG_INPUT:
    case TH_TAG_SELECT:
    case TH_TAG_TEXTAREA:
    case TH_TAG_OPTGROUP:
    case TH_TAG_OPTION:
    case TH_TAG_FIELDSET:
        return 1;
    default:
        return 0;
    }
}

/* Keys the first-legend and fieldset-climb answers in a memo that also holds :has() and sibling answers,
   which key by their own selector objects. */
static const char SEL_FIRST_LEGEND_KEY = 0;
static const char SEL_FIELDSET_CLIMB_KEY = 0;

/* Whether child, the child of a fieldset that a control is or sits under, is that
   fieldset's first legend, whose contents a disabled fieldset does not disable (HTML "the
   fieldset element"). Every control below a legend asks about it again, so the walk over
   its earlier siblings is memoized: run once per control, a fieldset of n children holding
   n controls would cost n^2 steps. */
static int sel_is_first_legend(th_node *child, sel_memo *memo) {
    /* a direct child with the legend atom is necessarily an HTML legend: foreign
       content only enters through an svg/math subtree, never as a bare child here */
    if (child->atom != TH_TAG_LEGEND) {
        return 0;
    }
    /* the streaming rewriter matches against a bare ancestor chain: its fieldset lists no
       children, so no legend there can be told to be the first and none is exempt */
    if (child->parent->first_child == NULL) {
        return 0;
    }
    int first = 1;
    if (sel_memo_get(memo, &SEL_FIRST_LEGEND_KEY, child, &first)) {
        return first;
    }
    for (th_node *earlier = child->prev_sibling; earlier != NULL; earlier = earlier->prev_sibling) {
        TH_WORK_STEP();
        if (earlier->type == TH_NODE_ELEMENT && earlier->atom == TH_TAG_LEGEND) {
            first = 0;
            break;
        }
    }
    sel_memo_put(memo, &SEL_FIRST_LEGEND_KEY, child, first);
    return first;
}

/* Whether a disabled fieldset above node disables it: one whose first legend does not hold
   the child the climb came through. Past SEL_POSITION_MEMO_MIN_RUN levels the answer is
   memoized for every node the climb passed, so the controls under one deep run of fieldsets
   stop at the first memoized ancestor instead of each climbing to the root. */
static int sel_fieldset_disables(th_node *node, sel_memo *memo) {
    int disabled = 0;
    int climbed = 0;
    th_node *child = node;
    for (th_node *ancestor = node->parent; ancestor != NULL; child = ancestor, ancestor = ancestor->parent) {
        TH_WORK_STEP();
        climbed++;
        /* a fieldset-atom ancestor is necessarily HTML; the type guard skips the
           document node that ends the chain */
        if (ancestor->type == TH_NODE_ELEMENT && ancestor->atom == TH_TAG_FIELDSET &&
            sel_has_attr(ancestor, TH_ATTR_DISABLED) && !sel_is_first_legend(child, memo)) {
            disabled = 1;
            break;
        }
        if (climbed >= SEL_POSITION_MEMO_MIN_RUN && sel_memo_get(memo, &SEL_FIELDSET_CLIMB_KEY, ancestor, &disabled)) {
            break;
        }
    }
    if (climbed > SEL_POSITION_MEMO_MIN_RUN) {
        th_node *passed = node;
        for (int index = 0; index < climbed; index++) {
            sel_memo_put(memo, &SEL_FIELDSET_CLIMB_KEY, passed, disabled);
            passed = passed->parent;
        }
    }
    return disabled;
}

/* Whether node is actually disabled: its own disabled attribute, an option whose
   optgroup is disabled, or a control inside a disabled fieldset (HTML "disabled"). */
static int sel_is_disabled(th_node *node, sel_memo *memo) {
    if (!sel_is_disableable(node)) {
        return 0;
    }
    if (sel_has_attr(node, TH_ATTR_DISABLED)) {
        return 1;
    }
    if (node->atom == TH_TAG_OPTION) {
        /* a parent with the optgroup atom is necessarily HTML; a detached option
           (built, extracted or replaced) has no parent at all */
        th_node *parent = node->parent;
        return parent != NULL && parent->atom == TH_TAG_OPTGROUP && sel_has_attr(parent, TH_ATTR_DISABLED);
    }
    if (node->atom == TH_TAG_OPTGROUP || node->atom == TH_TAG_FIELDSET) {
        return 0; /* an optgroup/fieldset is disabled only by its own attribute */
    }
    return sel_fieldset_disables(node, memo);
}

/* :checked: a checked checkbox/radio input or a selected option. */
static int sel_is_checked(th_node *node) {
    if (node->ns != TH_NS_HTML) {
        return 0;
    }
    if (node->atom == TH_TAG_INPUT) {
        return (sel_input_type_is(node, "checkbox") || sel_input_type_is(node, "radio")) &&
               sel_has_attr(node, TH_ATTR_CHECKED);
    }
    return node->atom == TH_TAG_OPTION && sel_has_attr(node, TH_ATTR_SELECTED);
}

/* :required/:optional apply to these mutable controls. */
static int sel_is_required_capable(th_node *node) {
    return node->ns == TH_NS_HTML &&
           (node->atom == TH_TAG_INPUT || node->atom == TH_TAG_SELECT || node->atom == TH_TAG_TEXTAREA);
}

/* The input types that are mutable text fields, so :read-write rather than
   :read-only (HTML "the input element"). */
static const char *const SEL_MUTABLE_INPUT_TYPES[] = {
    "text", "search", "url", "tel", "email", "password", "number", "date", "datetime-local", "month", "week", "time",
};

static int sel_is_mutable_input(th_node *node) {
    const th_node_attr *attr = sel_find_attr(node, TH_ATTR_TYPE);
    if (attr == NULL || attr->value == NULL) {
        return 1; /* the default type is text, which is mutable */
    }
    for (size_t index = 0; index < sizeof(SEL_MUTABLE_INPUT_TYPES) / sizeof(SEL_MUTABLE_INPUT_TYPES[0]); index++) {
        if (sel_kw(attr->value, attr->value_len, SEL_MUTABLE_INPUT_TYPES[index])) {
            return 1;
        }
    }
    return 0;
}

/* Whether the element is editable through contenteditable (true when the
   attribute is present and empty or "true"; not inherited, matching browsers). */
static int sel_is_contenteditable(th_node *node) {
    const th_node_attr *attr = sel_find_attr(node, TH_ATTR_CONTENTEDITABLE);
    if (attr == NULL) {
        return 0;
    }
    /* an empty value is stored as NULL, so a present-but-empty contenteditable
       (the editable case) is caught by the NULL test */
    return attr->value == NULL || sel_kw(attr->value, attr->value_len, "true");
}

/* :read-write: a mutable text input, a writable textarea, or a contenteditable
   element. Everything else is :read-only. */
static int sel_is_read_write(th_node *node, sel_memo *memo) {
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_INPUT) {
        return sel_is_mutable_input(node) && !sel_has_attr(node, TH_ATTR_READONLY) && !sel_is_disabled(node, memo);
    }
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_TEXTAREA) {
        return !sel_has_attr(node, TH_ATTR_READONLY) && !sel_is_disabled(node, memo);
    }
    return sel_is_contenteditable(node);
}

/* A submit button: an input of type submit/image, or a button whose type is
   submit (the missing-value default). */
static int sel_is_submit_control(th_node *node) {
    if (node->ns != TH_NS_HTML) {
        return 0;
    }
    if (node->atom == TH_TAG_INPUT) {
        return sel_input_type_is(node, "submit") || sel_input_type_is(node, "image");
    }
    if (node->atom == TH_TAG_BUTTON) {
        const th_node_attr *attr = sel_find_attr(node, TH_ATTR_TYPE);
        return attr == NULL || attr->value == NULL || sel_kw(attr->value, attr->value_len, "submit");
    }
    return 0;
}

/* The nearest ancestor form element, or NULL. A form-atom ancestor is necessarily
   HTML (foreign content has no form element); the document node ending the chain
   never carries the form atom, so the walk stops there without a type guard. */
static th_node *sel_form_owner(th_node *node) {
    for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->atom == TH_TAG_FORM) {
            return ancestor;
        }
    }
    return NULL;
}

/* The first submit control under root in tree order, or NULL. */
static th_node *sel_first_submit(th_node *root) {
    for (th_node *child = root->first_child; child != NULL; child = child->next_sibling) {
        TH_WORK_STEP();
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (sel_is_submit_control(child)) {
            return child;
        }
        th_node *deeper = sel_first_submit(child);
        if (deeper != NULL) {
            return deeper;
        }
    }
    return NULL;
}

/* :default: a default-checked checkbox/radio, a default-selected option, or a
   form's first submit button (HTML "the :default pseudo-class"). */
static int sel_is_default(th_node *node, sel_default_memo *memo) {
    if (node->ns != TH_NS_HTML) {
        return 0;
    }
    if (node->atom == TH_TAG_INPUT && (sel_input_type_is(node, "checkbox") || sel_input_type_is(node, "radio"))) {
        return sel_has_attr(node, TH_ATTR_CHECKED);
    }
    if (node->atom == TH_TAG_OPTION) {
        return sel_has_attr(node, TH_ATTR_SELECTED);
    }
    if (sel_is_submit_control(node)) {
        th_node *form = sel_form_owner(node);
        if (form == NULL) {
            return 0;
        }
        if (form->first_child == node) {
            return 1;
        }
        if (memo == NULL) {
            return sel_first_submit(form) == node;
        }
        if (memo->form != form) {
            memo->form = form;
            memo->first = sel_first_submit(form);
        }
        return memo->first == node;
    }
    return 0;
}

/* The end (exclusive) of the '-'-delimited subtag of s beginning at start. */
static Py_ssize_t sel_subtag_end(const Py_UCS4 *s, Py_ssize_t len, Py_ssize_t start) {
    Py_ssize_t end = start;
    while (end < len && s[end] != '-') {
        end++;
    }
    return end;
}

/* Whether a language tag matches a range by RFC 4647 §3.3.2 extended filtering
   (ASCII case-insensitive): a '*' subtag is a wildcard (a leading '*' matches any
   primary tag, an interior '*' is skipped), and a non-matching, non-singleton tag
   subtag is skipped, so a range's subtags need only appear in order. */
static int sel_lang_range_matches(const Py_UCS4 *tag, Py_ssize_t tag_len, const Py_UCS4 *range, Py_ssize_t range_len) {
    if (range_len == 0) {
        return 0;
    }
    Py_ssize_t rend = sel_subtag_end(range, range_len, 0);
    Py_ssize_t tend = sel_subtag_end(tag, tag_len, 0);
    if ((rend != 1 || range[0] != '*') && !sel_eq(tag, tend, range, rend, 1)) {
        return 0; /* the primary subtag must match unless the range's is the wildcard */
    }
    Py_ssize_t rstart = rend + 1;
    Py_ssize_t tstart = tend + 1;
    while (rstart < range_len) {
        rend = sel_subtag_end(range, range_len, rstart);
        Py_ssize_t rlen = rend - rstart;
        if (rlen == 1 && range[rstart] == '*') {
            rstart = rend + 1;
            continue;
        }
        while (1) {
            if (tstart >= tag_len) {
                return 0; /* the range has subtags left but the tag has none */
            }
            tend = sel_subtag_end(tag, tag_len, tstart);
            if (sel_eq(tag + tstart, tend - tstart, range + rstart, rlen, 1)) {
                tstart = tend + 1;
                break;
            }
            if (tend - tstart == 1) {
                return 0; /* a singleton tag subtag cannot be skipped by the implicit wildcard */
            }
            tstart = tend + 1;
        }
        rstart = rend + 1;
    }
    return 1;
}

/* The attribute declaring an element's language (HTML "the lang and xml:lang
   attributes"), or NULL: xml:lang in the XML namespace wins, and only an XML tree or a
   foreign element carries one (the HTML parser leaves xml:lang on an HTML element in
   no namespace); then lang, which counts on an HTML or SVG element of an HTML tree. An
   empty value declares the language unknown, so it stops the walk like any other. */
#if defined(__GNUC__)
#define SEL_LANG_ATTR_INLINE inline __attribute__((always_inline))
#else
#define SEL_LANG_ATTR_INLINE inline
#endif

/* Inlined into both of its callers, so that the deep matcher's call leaves the :lang() walk as tight as before. */
static SEL_LANG_ATTR_INLINE const th_node_attr *sel_lang_attr(th_tree *tree, th_node *node, uint32_t xml_lang_atom) {
    int xml = th_tree_is_xml(tree);
    if (xml || node->ns != TH_NS_HTML) {
        const th_node_attr *attr = sel_find_attr(node, xml_lang_atom);
        if (attr != NULL) {
            return attr;
        }
    }
    if (xml || node->ns == TH_NS_MATHML) {
        return NULL;
    }
    return sel_find_attr(node, TH_ATTR_LANG);
}

/* :lang(): the element's language is the one the nearest self-or-ancestor element
   declares; it matches when any comma-separated range in the argument matches. With
   none, or an empty one, the language is not tagged and only a quoted empty range matches. */
static int sel_matches_lang(th_tree *tree, th_node *node, const sel_simple *simple) {
    const Py_UCS4 *tag = NULL;
    Py_ssize_t tag_len = 0;
    for (th_node *ancestor = node; ancestor != NULL && ancestor->type == TH_NODE_ELEMENT; ancestor = ancestor->parent) {
        TH_WORK_STEP();
        const th_node_attr *attr = sel_lang_attr(tree, ancestor, simple->attr_atom);
        if (attr != NULL) {
            tag = attr->value;
            tag_len = attr->value_len;
            break;
        }
    }
    if (tag_len == 0) {
        return simple->nth_a;
    }
    Py_ssize_t cursor = 0;
    while (cursor < simple->value_len) {
        Py_ssize_t start = cursor;
        while (cursor < simple->value_len && simple->value[cursor] != ',') {
            cursor++;
        }
        Py_ssize_t end = cursor;
        if (cursor < simple->value_len) {
            cursor++; /* step over the comma */
        }
        while (start < end && is_space(simple->value[start])) {
            start++;
        }
        while (end > start && is_space(simple->value[end - 1])) {
            end--;
        }
        if (end - start >= 2 && (simple->value[start] == '"' || simple->value[start] == '\'') &&
            simple->value[end - 1] == simple->value[start]) {
            start++;
            end--;
        }
        if (sel_lang_range_matches(tag, tag_len, simple->value + start, end - start)) {
            return 1;
        }
    }
    return 0;
}

/* The directional class of a code point used to resolve dir=auto: 2 RTL, 1 LTR,
   0 neutral. An approximation of the Unicode bidi rule that covers the common RTL
   blocks (Hebrew through Arabic Extended-A, and the Hebrew/Arabic presentation
   forms); rarer RTL scripts resolve as if neutral. */
static int sel_strong_dir(Py_UCS4 ch) {
    if ((ch >= 0x0590 && ch <= 0x08FF) || (ch >= 0xFB1D && ch <= 0xFEFF)) {
        return 2;
    }
    if (is_ascii_alpha(ch) || ch >= 0x00C0) {
        return 1;
    }
    return 0;
}

/* The direction of the first strong-directional character in a span: 1 ltr, 2 rtl, 0
   when none is strong. */
static int sel_first_strong_dir(const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        TH_WORK_STEP();
        int strong = sel_strong_dir(text[index]);
        if (strong != 0) {
            return strong;
        }
    }
    return 0;
}

/* Key a node's first strong direction in a sel_memo; no selector object shares either
   address. A memo bit holds two answers, so ltr and "none strong" add the none key, and
   write it before the rtl key every read starts from: a table that stops growing
   between the two writes then reads as unknown, never as ltr. */
static const char sel_dir_rtl_key = 0;
static const char sel_dir_none_key = 0;

/* The first strong direction remembered for node: 1 ltr, 2 rtl, 0 none, -1 unknown. */
static int sel_dir_memo_get(const sel_memo *memo, const th_node *node) {
    int rtl = 0;
    if (!sel_memo_get(memo, &sel_dir_rtl_key, node, &rtl)) {
        return -1;
    }
    if (rtl) {
        return 2;
    }
    int none = 0;
    sel_memo_get(memo, &sel_dir_none_key, node, &none);
    return none ? 0 : 1;
}

static void sel_dir_memo_put(sel_memo *memo, const th_node *node, int dir) {
    if (dir != 2) {
        sel_memo_put(memo, &sel_dir_none_key, node, dir == 0);
    }
    sel_memo_put(memo, &sel_dir_rtl_key, node, dir == 2);
}

/* The first strong direction of root's descendant text in document order (the text
   th_node_text collects): 1 ltr, 2 rtl, 0 none. The walk records the answer of every
   node with children it enters: the strong character answers for each open ancestor up
   to root, and a node left without one holds none. A later walk from an ancestor skips
   a subtree known to hold none and stops at one known to hold a strong character, so no
   node is entered twice and the walks of one table stay linear in the tree, nested
   dir=auto elements included. */
static int sel_subtree_dir(th_tree *tree, th_node *root, sel_memo *memo) {
    th_node *node = root->first_child;
    if (node == NULL) {
        return 0;
    }
    for (;;) {
        TH_WORK_STEP();
        int dir = 0;
        if (node->type == TH_NODE_TEXT) {
            if (node->text_len > 0) {
                const Py_UCS4 *text = th_node_realize_text(tree, node);
                if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return 1;       /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                dir = sel_first_strong_dir(text, node->text_len);
            }
        } else if (node->first_child != NULL) {
            dir = sel_dir_memo_get(memo, node);
            if (dir < 0) {
                node = node->first_child;
                continue;
            }
        }
        if (dir != 0) {
            for (th_node *open = node->parent; open != root; open = open->parent) {
                sel_dir_memo_put(memo, open, dir);
            }
            sel_dir_memo_put(memo, root, dir);
            return dir;
        }
        while (node->next_sibling == NULL) {
            node = node->parent;
            sel_dir_memo_put(memo, node, 0);
            if (node == root) {
                return 0;
            }
        }
        node = node->next_sibling;
    }
}

/* The legend table of a context built without one: a single test (selector_matches,
   rewrite rules) of a selector without a :disabled-family pseudo-class, or a driver that
   needs no table of its own. Only this per-thread table outlives such a context, so the
   matcher binds it on the first dir=auto read rather than every caller setting up and
   freeing one; binding empties what the previous context left. */
static _Thread_local sel_memo sel_single_test_memo;

/* The direction a dir=auto element resolves to (HTML "the directionality of an
   element"): an input element resolves from its value attribute, every other element
   from its text content, ltr when none of it is strong. Every descendant without a dir of
   its own resolves through the same element, and a nested dir=auto element reads part of
   the same text, so the answers come from sel_subtree_dir over the legend table. */
static int sel_auto_dir(th_node *node, const sel_ctx *ctx) {
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_INPUT) {
        const th_node_attr *value = sel_find_attr(node, TH_ATTR_VALUE);
        if (value == NULL || value->value == NULL) {
            return 1; /* an empty control value resolves to the ltr default */
        }
        return sel_first_strong_dir(value->value, value->value_len) == 2 ? 2 : 1;
    }
    sel_memo *memo = ctx->legend_memo;
    if (memo == NULL) {
        memo = &sel_single_test_memo;
        sel_memo_free(memo);
        /* every context is a writable local of its driver or of the matcher copying it */
        ((sel_ctx *)ctx)->legend_memo = memo;
    }
    int dir = sel_dir_memo_get(memo, node);
    if (dir < 0) {
        dir = sel_subtree_dir(ctx->tree, node, memo);
    }
    return dir == 2 ? 2 : 1;
}

/* The element's resolved direction: 1 ltr, 2 rtl. The nearest dir attribute wins
   (auto and a dir-less bdi resolve from the content's first strong character);
   with none, the document default is ltr (HTML "the dir attribute"). Kept out of line so
   sel_match_simple, which every selector runs, does not carry the :dir() walk. */
static TH_NOINLINE int sel_direction(th_node *node, const sel_ctx *ctx) {
    /* a telephone input's directionality is its own dir, never inherited, and is ltr
       unless dir explicitly overrides it (HTML "the directionality", issue #374) */
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_INPUT && sel_input_type_is(node, "tel")) {
        const th_node_attr *attr = sel_find_attr(node, TH_ATTR_DIR);
        if (attr != NULL && attr->value != NULL) {
            if (sel_kw(attr->value, attr->value_len, "ltr")) {
                return 1;
            }
            if (sel_kw(attr->value, attr->value_len, "rtl")) {
                return 2;
            }
            if (sel_kw(attr->value, attr->value_len, "auto")) {
                return sel_auto_dir(node, ctx);
            }
        }
        return 1;
    }
    for (th_node *ancestor = node; ancestor != NULL && ancestor->type == TH_NODE_ELEMENT; ancestor = ancestor->parent) {
        TH_WORK_STEP();
        const th_node_attr *attr = sel_find_attr(ancestor, TH_ATTR_DIR);
        if (attr != NULL && attr->value != NULL) {
            if (sel_kw(attr->value, attr->value_len, "ltr")) {
                return 1;
            }
            if (sel_kw(attr->value, attr->value_len, "rtl")) {
                return 2;
            }
            if (sel_kw(attr->value, attr->value_len, "auto")) {
                return sel_auto_dir(ancestor, ctx);
            }
            /* an invalid dir value is ignored; keep walking up the ancestor chain */
        } else if (ancestor->ns == TH_NS_HTML && ancestor->atom == TH_TAG_BDI) {
            return sel_auto_dir(ancestor, ctx); /* a dir-less bdi defaults to auto */
        }
    }
    return 1;
}

static int sel_match_substr_attr(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    if (simple->attr_atom == UINT32_MAX) {
        return 0;
    }
    const th_node_attr *attr = sel_find_attr(node, simple->attr_atom);
    if (attr == NULL) {
        return 0;
    }
    const Py_UCS4 *value = attr->value != NULL ? attr->value : simple->value;
    Py_ssize_t value_len = attr->value != NULL ? attr->value_len : 0;
    int ci = simple->ci || (simple->ci_default && node->ns == TH_NS_HTML);
    if (value_len >= SEL_VALUE_MEMO_MIN) {
        return sel_match_long_value(node, simple, value, value_len, ci, ctx);
    }
    return sel_substr_long(value, value_len, simple->value, simple->value_len, ci);
}

static int sel_deep_match(th_node *node, const sel_simple *simple, const sel_ctx *ctx);
static int sel_deep_match_lists(th_node *node, const sel_simple *simple, const sel_ctx *ctx);

static int sel_match_pseudo(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    switch (simple->pseudo) { /* GCOVR_EXCL_BR_LINE: the parser only stores known pseudo ids */
    case PSEUDO_ROOT:
        /* the root element's parent is the document, never an element; the top of a
           detached subtree (a built, extracted or replaced element) has no parent and
           is no root */
        return node->parent != NULL && node->parent->type != TH_NODE_ELEMENT;
    case PSEUDO_EMPTY:
        return sel_is_empty(node, ctx->tree);
    case PSEUDO_FIRST_CHILD:
        return sel_no_sibling(node, 0, 0);
    case PSEUDO_LAST_CHILD:
        return sel_no_sibling(node, 1, 0);
    case PSEUDO_ONLY_CHILD:
        return sel_no_sibling(node, 0, 0) && sel_no_sibling(node, 1, 0);
    case PSEUDO_FIRST_OF_TYPE:
        return sel_no_type_sibling(node, 0, ctx);
    case PSEUDO_LAST_OF_TYPE:
        return sel_no_type_sibling(node, 1, ctx);
    case PSEUDO_ONLY_OF_TYPE:
        return sel_no_type_sibling(node, 0, ctx) && sel_no_type_sibling(node, 1, ctx);
    case PSEUDO_NTH_CHILD:
        if (simple->sub != NULL) {
            int of_index = sel_nth_index(node, 1, 0, simple, ctx);
            return of_index != 0 && sel_nth_matches(simple->nth_a, simple->nth_b, of_index);
        }
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_nth_index(node, 1, 0, simple, ctx));
    case PSEUDO_NTH_LAST_CHILD:
        if (simple->sub != NULL) {
            int of_index = sel_nth_index(node, -1, 0, simple, ctx);
            return of_index != 0 && sel_nth_matches(simple->nth_a, simple->nth_b, of_index);
        }
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_nth_index(node, -1, 0, simple, ctx));
    case PSEUDO_NTH_OF_TYPE:
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_nth_index(node, 1, 1, simple, ctx));
    case PSEUDO_NTH_LAST_OF_TYPE:
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_nth_index(node, -1, 1, simple, ctx));
    /* §6.6 the scoping root: the element the query was rooted at, or, when the root is
       the document (or a fragment), the document element, as :root resolves to */
    case PSEUDO_SCOPE:
        if (ctx->scope->type == TH_NODE_ELEMENT) {
            return node == ctx->scope;
        }
        /* a non-element scope inside a detached subtree (closest() from its text) has
           no document element above it */
        return node->parent != NULL && node->parent->type != TH_NODE_ELEMENT;
    /* §12 the input pseudo-classes determinable from the static tree */
    case PSEUDO_CHECKED:
        return sel_is_checked(node);
    case PSEUDO_DISABLED:
        return sel_is_disabled(node, ctx->legend_memo);
    case PSEUDO_ENABLED:
        return sel_is_disableable(node) && !sel_is_disabled(node, ctx->legend_memo);
    case PSEUDO_REQUIRED:
        return sel_is_required_capable(node) && sel_has_attr(node, TH_ATTR_REQUIRED);
    case PSEUDO_OPTIONAL:
        return sel_is_required_capable(node) && !sel_has_attr(node, TH_ATTR_REQUIRED);
    case PSEUDO_READ_ONLY:
        return !sel_is_read_write(node, ctx->legend_memo);
    case PSEUDO_READ_WRITE:
        return sel_is_read_write(node, ctx->legend_memo);
    case PSEUDO_DEFAULT:
        return sel_is_default(node, ctx->default_memo);
    case PSEUDO_LANG:
        return sel_matches_lang(ctx->tree, node, simple);
    case PSEUDO_DIR:
        return sel_direction(node, ctx) == simple->nth_a;
    /* :link/:any-link: an a or area carrying an href (HTML "the :link/:any-link") */
    case PSEUDO_ANY_LINK:
        return (node->atom == TH_TAG_A || node->atom == TH_TAG_AREA) && sel_has_attr(node, TH_ATTR_HREF);
    /* live UA/interaction or navigation state a static tree cannot express */
    case PSEUDO_NEVER:
        return 0;
    case PSEUDO_SUBSTR_LONG:
        return sel_match_substr_attr(node, simple, ctx);
    case PSEUDO_DEEP:
        return sel_deep_match(node, simple, ctx);
    case PSEUDO_DEEP_LISTS:
        return sel_deep_match_lists(node, simple, ctx);
    /* the functional pseudo-classes: :is()/:where() match the element against the
       nested list; :not() is its negation; :has() searches for a relative match
       anchored at it */
    case PSEUDO_IS:
    case PSEUDO_WHERE:
        return sel_matches_alts(node, simple->sub, simple->sub_count, ctx);
    case PSEUDO_NOT:
        return !sel_matches_alts(node, simple->sub, simple->sub_count, ctx);
    default: /* PSEUDO_HAS */
        return sel_has_match(node, simple->sub, simple->sub_count, ctx);
    }
}

/* Out of line, so the :has() loop that LTO inlines sel_match_simple into keeps its register allocation
   when unrelated code changes size. */
static TH_NOINLINE int sel_match_attr_arm(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    if (simple->kind == '.') {
        const th_node_attr *attr = sel_find_attr(node, TH_ATTR_CLASS);
        if (attr == NULL || attr->value == NULL) {
            return 0;
        }
        if (attr->value_len >= SEL_VALUE_MEMO_MIN) {
            return sel_match_long_value(node, simple, attr->value, attr->value_len, ctx->quirks, ctx);
        }
        return contains_ws_token(attr->value, attr->value_len, simple->name, simple->name_len, ctx->quirks);
    }
    if (simple->attr_atom == UINT32_MAX) {
        return 0; /* no element in the tree carries this name */
    }
    const th_node_attr *attr = sel_find_attr(node, simple->attr_atom);
    if (attr == NULL) {
        return 0;
    }
    const Py_UCS4 *value = attr->value != NULL ? attr->value : simple->value;
    Py_ssize_t value_len = attr->value != NULL ? attr->value_len : 0;
    /* the HTML case-insensitive set applies only to HTML-namespace elements */
    int ci = simple->ci || (simple->ci_default && node->ns == TH_NS_HTML);
    /* the other operators compare at most the selector's own value */
    if (value_len >= SEL_VALUE_MEMO_MIN && (simple->op == OP_INCLUDE || simple->op == OP_SUBSTR)) {
        return sel_match_long_value(node, simple, value, value_len, ci, ctx);
    }
    return sel_match_attr_op(simple, value, value_len, ci);
}

int sel_match_simple(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    switch (simple->kind) {
    case '*':
        return 1;
    case ':':
        return sel_match_pseudo(node, simple, ctx);
    case 'e':
        if (simple->tag_atom != TH_TAG_UNKNOWN && node->atom == simple->tag_atom) {
            return 1;
        }
        /* An XML element keeps its literal spelling under TH_TAG_UNKNOWN even when the name
           looks like a builtin, so a builtin-atom selector falls back to a spelling compare;
           an HTML element carrying a different builtin atom does not match. */
        if (node->atom != TH_TAG_UNKNOWN) {
            return 0;
        }
        return sel_eq(node->text, node->text_len, simple->name, simple->name_len, !th_tree_is_xml(ctx->tree));
    case '#': {
        /* class/ID selectors are case-sensitive in no-quirks mode, ASCII
           case-insensitive in quirks mode (Selectors-4 §6.1/§6.2) */
        const th_node_attr *attr = sel_find_attr(node, TH_ATTR_ID);
        return attr != NULL && attr->value != NULL &&
               sel_eq(attr->value, attr->value_len, simple->name, simple->name_len, ctx->quirks);
    }
    case '.':
    default:
        return sel_match_attr_arm(node, simple, ctx);
    }
}

static int sel_match_compound(th_node *node, const sel_compound *compound, const sel_ctx *ctx) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    for (int index = 0; index < compound->count; index++) {
        if (!sel_match_simple(node, &compound->simples[index], ctx)) {
            return 0;
        }
    }
    return 1;
}

static uint16_t sel_compound_known_type_atom(const sel_compound *compound, th_tree *tree) {
    /* an XML element carries TH_TAG_UNKNOWN whatever its name, so only an HTML tree can compare atoms */
    if (compound->count != 1 || th_tree_is_xml(tree)) {
        return TH_TAG_UNKNOWN;
    }
    const sel_simple *simple = &compound->simples[0];
    return simple->kind == 'e' ? simple->tag_atom : TH_TAG_UNKNOWN;
}

static th_node *sel_prev_element(th_node *node) {
    for (th_node *sibling = node->prev_sibling; sibling != NULL; sibling = sibling->prev_sibling) {
        if (sibling->type == TH_NODE_ELEMENT) {
            return sibling;
        }
    }
    return NULL;
}

/* Whether a compound writes :scope explicitly (a relative selector's leftmost compound
   may, e.g. :has(:scope > p)). Inside :has() the scope is rebound to the anchor, so
   such a :scope pins that compound to the anchor itself. */
static int sel_compound_has_scope(const sel_compound *compound) {
    for (int index = 0; index < compound->count; index++) {
        if (compound->simples[index].kind == ':' && compound->simples[index].pseudo == PSEUDO_SCOPE) {
            return 1;
        }
    }
    return 0;
}

/* How matching compounds[0..index] at a node ended, after Blink's SelectorChecker::MatchStatus
   (https://github.com/chromium/chromium/blob/98efee7338d7c5a17952aab8887d8212fc35fb0c/third_party/blink/renderer/core/css/selector_checker.cc#L1097-L1179).
   A failure no earlier sibling or higher ancestor can fix ends the combinator walks to its
   right instead of letting them retry the next sibling or ancestor. Miss and hit are 0 and
   1, so a comparison is a status. */
enum sel_status {
    SEL_FAILS_LOCALLY = 0,  /* this node fails; an earlier sibling or higher ancestor may still match */
    SEL_MATCHES = 1,        /* this node matches */
    SEL_FAILS_ALL_SIBLINGS, /* this node and every earlier sibling fail */
    SEL_FAILS_COMPLETELY,   /* this node, its earlier siblings, its ancestors and theirs all fail */
};

/* A sibling walk this short runs without the memo: Blink's nth-index cache starts past 32
   siblings
   (https://github.com/chromium/chromium/blob/98efee7338d7c5a17952aab8887d8212fc35fb0c/third_party/blink/renderer/core/dom/nth_index_cache.cc#L54),
   so the sibling runs of ordinary markup never pay for a hash probe. */
#define SEL_SIBLING_MEMO_MIN_RUN 32

static enum sel_status sel_match_anchor(th_node *node, const sel_compound *compound, th_node *anchor) {
    /* an explicit :scope in the leftmost compound already matched the anchor (the
       scope was rebound to it), so it pins the compound to the anchor: :has(:scope >
       p) equals :has(> p), and the leading combinator adds nothing (issue #431) */
    if (sel_compound_has_scope(compound)) {
        return SEL_MATCHES;
    }
    switch (compound->combinator) {
    case '>':
        return node->parent == anchor;
    case '+':
        return sel_prev_element(node) == anchor;
    case '~':
        for (th_node *prev = sel_prev_element(node); prev != NULL; prev = sel_prev_element(prev)) {
            TH_WORK_STEP();
            if (prev == anchor) {
                return SEL_MATCHES;
            }
        }
        return SEL_FAILS_LOCALLY;
    default: /* descendant */
        for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            TH_WORK_STEP();
            if (ancestor == anchor) {
                return SEL_MATCHES;
            }
        }
        return SEL_FAILS_LOCALLY;
    }
}

static enum sel_status sel_match_left(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                      const sel_ctx *ctx);

/* node matches compounds[index]; verify the combinators and compounds to its left. The
   leftmost compound of an ordinary selector ends the chain, so a one-compound selector
   and the last step of every chain return here without the frame of the combinator
   walks. */
static inline enum sel_status sel_match_from(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                             const sel_ctx *ctx) {
    if (index == 0) {
        return anchor == NULL ? SEL_MATCHES : sel_match_anchor(node, &complex->compounds[0], anchor);
    }
    return sel_match_left(node, complex, index, anchor, ctx);
}

/* The general sibling combinator to the left of compounds[index]: some earlier element
   sibling of node matches compounds[index - 1] and everything left of it. Whether such a
   sibling exists at or before a node depends only on that node, so past the first
   SEL_SIBLING_MEMO_MIN_RUN siblings the walk stops at the first one whose answer the
   sibling memo holds, and it stores its own answer under node's nearest earlier sibling.
   A query walking the siblings in document order then visits each one a bounded number of
   times. Reusing the last walk avoids the hash lookup in document order. Inside :has(),
   the answer depends on the anchor, so the memo stays out. Memo bookkeeping stays out
   of line to limit saved registers in other combinators. */
static TH_NOINLINE enum sel_status sel_match_sibling_walk(th_node *node, const sel_complex *complex, int index,
                                                          th_node *anchor, const sel_ctx *ctx, int skip_nearest) {
    const sel_compound *target = &complex->compounds[index - 1];
    const uint16_t target_atom = sel_compound_known_type_atom(target, ctx->tree);
    th_node *nearest = sel_prev_element(node);
    th_node *prev = skip_nearest ? sel_prev_element(nearest) : nearest;
    int walked = skip_nearest;
    sel_sibling_memo *siblings = anchor == NULL ? ctx->sibling_memo : NULL;
    sel_memo *memo = siblings != NULL ? &siblings->table : NULL;
    sel_sibling_record *last = siblings != NULL ? &siblings->last_walks[(unsigned)index % SEL_SIBLING_RECORDS] : NULL;
    if (last != NULL && last->node == node && last->key == target) {
        return (enum sel_status)last->status;
    }
    enum sel_status status = SEL_FAILS_ALL_SIBLINGS;
    for (; prev != NULL; prev = sel_prev_element(prev), walked++) {
        TH_WORK_STEP();
        int cached;
        if (memo != NULL && walked >= SEL_SIBLING_MEMO_MIN_RUN && sel_memo_get(memo, target, prev, &cached)) {
            status = cached ? SEL_MATCHES : SEL_FAILS_ALL_SIBLINGS;
            break;
        }
        if (target_atom != TH_TAG_UNKNOWN ? prev->atom == target_atom : sel_match_compound(prev, target, ctx)) {
            const enum sel_status left = sel_match_from(prev, complex, index - 1, anchor, ctx);
            if (left != SEL_FAILS_LOCALLY) {
                status = left;
                break;
            }
        }
        if (last != NULL && last->node == prev && last->key == target) {
            status = (enum sel_status)last->status;
            break;
        }
    }
    if (memo != NULL) {
        if (walked >= SEL_SIBLING_MEMO_MIN_RUN) {
            sel_memo_put(memo, target, nearest, status == SEL_MATCHES);
        }
        *last = (sel_sibling_record){target, node, status};
    }
    return status;
}

static enum sel_status sel_match_earlier_sibling(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                                 const sel_ctx *ctx) {
    const sel_compound *target = &complex->compounds[index - 1];
    const uint16_t target_atom = sel_compound_known_type_atom(target, ctx->tree);
    th_node *nearest = sel_prev_element(node);
    if (nearest != NULL && target_atom != TH_TAG_UNKNOWN && nearest->atom == target_atom) {
        const enum sel_status left = sel_match_from(nearest, complex, index - 1, anchor, ctx);
        if (left != SEL_FAILS_LOCALLY) {
            return left;
        }
        return sel_match_sibling_walk(node, complex, index, anchor, ctx, 1);
    }
    return sel_match_sibling_walk(node, complex, index, anchor, ctx, 0);
}

/* Leftmost relative compounds connect to their anchor separately, keeping them out
   of the interior-combinator frame. */
static enum sel_status sel_match_left(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                      const sel_ctx *ctx) {
    const sel_compound *target = &complex->compounds[index - 1];
    uint16_t target_atom = sel_compound_known_type_atom(target, ctx->tree);
    switch (complex->compounds[index].combinator) {
    case '>': {
        /* the root element's parent is the document, which sel_match_compound rejects
           as a non-element; the top of a detached subtree (a built, extracted or
           replaced element) has no parent at all */
        th_node *parent = node->parent;
        if (parent == NULL) {
            return SEL_FAILS_ALL_SIBLINGS;
        }
        enum sel_status status = SEL_FAILS_LOCALLY;
        if (target_atom != TH_TAG_UNKNOWN ? parent->type == TH_NODE_ELEMENT && parent->atom == target_atom
                                          : sel_match_compound(parent, target, ctx)) {
            status = sel_match_from(parent, complex, index - 1, anchor, ctx);
        }
        /* every earlier sibling has the same parent, so it fails the same way */
        return status == SEL_FAILS_LOCALLY ? SEL_FAILS_ALL_SIBLINGS : status;
    }
    case '+': {
        th_node *prev = sel_prev_element(node);
        if (prev == NULL) {
            return SEL_FAILS_ALL_SIBLINGS;
        }
        if (target_atom != TH_TAG_UNKNOWN ? prev->atom != target_atom : !sel_match_compound(prev, target, ctx)) {
            return SEL_FAILS_LOCALLY;
        }
        return sel_match_from(prev, complex, index - 1, anchor, ctx);
    }
    case '~':
        return sel_match_earlier_sibling(node, complex, index, anchor, ctx);
    default: /* descendant */
        if (target_atom != TH_TAG_UNKNOWN) {
            for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
                TH_WORK_STEP();
                if (ancestor->type == TH_NODE_ELEMENT && ancestor->atom == target_atom) {
                    const enum sel_status status = sel_match_from(ancestor, complex, index - 1, anchor, ctx);
                    if (status == SEL_MATCHES || status == SEL_FAILS_COMPLETELY) {
                        return status;
                    }
                }
            }
            return SEL_FAILS_COMPLETELY;
        }
        for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            TH_WORK_STEP();
            if (sel_match_compound(ancestor, target, ctx)) {
                const enum sel_status status = sel_match_from(ancestor, complex, index - 1, anchor, ctx);
                if (status == SEL_MATCHES || status == SEL_FAILS_COMPLETELY) {
                    return status;
                }
            }
        }
        return SEL_FAILS_COMPLETELY;
    }
}

/* node matches some alternative in alts: it is the subject (rightmost compound) of
   one complex whose combinators to the left verify. The top-level match and the
   :is()/:where() pseudo-classes share this. */
int selector_matches_alt(th_node *node, const sel_complex *complex, const sel_ctx *ctx) {
    int subject = complex->count - 1;
    return sel_match_compound(node, &complex->compounds[subject], ctx) &&
           sel_match_from(node, complex, subject, NULL, ctx) == SEL_MATCHES;
}

static int sel_matches_alts(th_node *node, const sel_complex *alts, int count, const sel_ctx *ctx) {
    for (int index = 0; index < count; index++) {
        if (selector_matches_alt(node, &alts[index], ctx)) {
            return 1;
        }
    }
    return 0;
}

/* Hash the (key, node) pair to a slot. The node part is its creation sequence, as in the node maps,
   not its address: some spacings of the nodes in memory put every home slot of an address hash in
   one probe run, so the cost of a query hung on where the allocator placed the tree. The key rotates
   the node's slot because a node collects one entry per key it is probed with; hashing the node
   alone would put all of them in one probe run. */
static size_t sel_memo_hash(const void *key, uint32_t seq, size_t mask) {
    return (th_hash_slot(th_fibonacci_hash(seq), mask + 1) +
            (size_t)(((uintptr_t)key >> 4) * 0x9E3779B97F4A7C15u >> 32)) &
           mask;
}

/* The rest of a probe that walked TH_FAST_RUN_LIMIT taken slots one by one. A document picks
   which sequences its deep elements get, so it can give them home slots in one run; from here
   the walk takes the keyed stride the node maps take, which the document cannot predict, and an
   odd stride reaches every slot. Out of line so the inlined lookup keeps the cost of a plain
   linear probe. */
static TH_NOINLINE size_t sel_memo_walk(const sel_memo *memo, const void *key, const void *node, uint32_t seq,
                                        size_t idx) {
    const size_t step = th_node_probe_step(seq);
    for (;;) {
        TH_WORK_STEP();
        const sel_memo_slot *slot = &memo->slots[idx];
        if ((slot->key == key && slot->node == node) || slot->key == NULL) {
            return idx;
        }
        idx = (idx + step) & memo->mask;
    }
}

/* Linear-probe from the pair's home slot to the slot holding the (key, node) pair or, if
   absent, the first empty slot it would occupy (the table is never full, so an empty slot
   always exists). Shared by get and insert so one probe loop carries both. seq only picks the
   home slot: a node, or a value buffer, has one seq for as long as its entries live. */
static size_t sel_memo_find(const sel_memo *memo, const void *key, const void *node, uint32_t seq) {
    size_t idx = sel_memo_hash(key, seq, memo->mask);
    for (size_t walked = 0; walked < (size_t)TH_FAST_RUN_LIMIT; walked++) {
        TH_WORK_STEP();
        const sel_memo_slot *slot = &memo->slots[idx];
        /* Key first: one argument spans many nodes, so the existing tests reach every probe edge. */
        if (slot->key == key && slot->node == node) {
            return idx;
        }
        if (slot->key == NULL) {
            return idx;
        }
        idx = (idx + 1) & memo->mask;
    }
    return sel_memo_walk(memo, key, node, seq, idx);
}

/* Look up (key, node, seq); on a hit store the cached result in *out and return 1. */
static int sel_memo_lookup(const sel_memo *memo, const void *key, const void *node, uint32_t seq, int *out) {
    if (memo->slots == NULL) {
        return 0;
    }
    const sel_memo_slot *slot = &memo->slots[sel_memo_find(memo, key, node, seq)];
    if (slot->key == NULL) {
        return 0;
    }
    *out = slot->result;
    return 1;
}

static int sel_memo_get(const sel_memo *memo, const void *key, const th_node *node, int *out) {
    return sel_memo_lookup(memo, key, node, node->seq, out);
}

static void sel_memo_insert(sel_memo *memo, const void *key, const void *node, uint32_t seq, unsigned char result) {
    sel_memo_slot *slot = &memo->slots[sel_memo_find(memo, key, node, seq)];
    memo->count += slot->key == NULL;
    *slot = (sel_memo_slot){key, node, seq, result};
}

/* Grow (or first-allocate) the table to the next power of two and rehash. Past limit, or on
   allocation failure, the table is dropped for good and memo->failed sends the matcher to the
   direct walk: restarting would refill and drop the table on every walk. */
static void sel_memo_grow(sel_memo *memo) {
    if (memo->failed) {
        return;
    }
    size_t new_cap = memo->slots == NULL ? 64 : (memo->mask + 1) * 2;
    if (memo->limit != 0 && new_cap > memo->limit) {
        sel_memo_free(memo);
        memo->failed = 1;
        return;
    }
    sel_memo_slot *slots = PyMem_Calloc(new_cap, sizeof(sel_memo_slot));
    if (slots == NULL) {  /* GCOVR_EXCL_BR_LINE: a calloc failure cannot be forced from a test */
        memo->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    sel_memo_slot *old_slots = memo->slots;
    size_t old_cap = memo->slots == NULL ? 0 : memo->mask + 1;
    memo->slots = slots;
    memo->mask = new_cap - 1;
    memo->count = 0;
    for (size_t idx = 0; idx < old_cap; idx++) {
        if (old_slots[idx].key != NULL) {
            sel_memo_insert(memo, old_slots[idx].key, old_slots[idx].node, old_slots[idx].seq, old_slots[idx].result);
        }
    }
    PyMem_Free(old_slots);
}

/* Cache a (key, node, seq) answer, growing past a 0.75 load factor. */
static void sel_memo_store(sel_memo *memo, const void *key, const void *node, uint32_t seq, int result) {
    if ((memo->count + 1) * 4 > (memo->mask + 1) * 3) {
        sel_memo_grow(memo);
        if (memo->failed) {
            return;
        }
    }
    sel_memo_insert(memo, key, node, seq, (unsigned char)(result != 0));
}

static void sel_memo_put(sel_memo *memo, const void *key, const th_node *node, int result) {
    sel_memo_store(memo, key, node, node->seq, result);
}

int sel_value_memo_get(const sel_memo *memo, const void *key, const th_node *node, const Py_UCS4 *value, int *out) {
    return sel_memo_lookup(memo, key, value, th_node_values_seq(node), out);
}

void sel_value_memo_put(sel_memo *memo, const void *key, const th_node *node, const Py_UCS4 *value, int result) {
    sel_memo_store(memo, key, value, th_node_values_seq(node), result);
}

void sel_memo_free(sel_memo *memo) {
    PyMem_Free(memo->slots);
    memo->slots = NULL;
    memo->mask = 0;
    memo->count = 0;
}

/* Whether the complex selector writes :scope anywhere (directly or inside a nested
   functional pseudo). A written :scope binds to the scope element, so an answer that
   holds for one scope cannot be reused for another: a :has() argument that writes it
   keeps the direct per-anchor walk, and a driver that rebinds scope per candidate keeps
   the sibling memo out. */
static int sel_rel_uses_scope(const sel_complex *rel);

static int sel_compound_uses_scope(const sel_compound *compound) {
    for (int index = 0; index < compound->count; index++) {
        const sel_simple *simple = &compound->simples[index];
        if (simple->kind != ':') {
            continue;
        }
        if (simple->pseudo == PSEUDO_SCOPE) {
            return 1;
        }
        if (simple->sub != NULL) {
            for (int alt = 0; alt < simple->sub_count; alt++) {
                if (sel_rel_uses_scope(&simple->sub[alt])) {
                    return 1;
                }
            }
        }
    }
    return 0;
}

static int sel_rel_uses_scope(const sel_complex *rel) {
    for (int index = 0; index < rel->count; index++) {
        if (sel_compound_uses_scope(&rel->compounds[index])) {
            return 1;
        }
    }
    return 0;
}

/* The memo only earns its keep where nesting is deep enough that candidate anchors'
   subtrees overlap heavily -- there the re-walk factor (how many nested anchors re-scan
   a node) dwarfs a hash probe. Shallower than this the direct walk wins, so a candidate
   at descent `descent` below the tested anchor consults/populates the memo only past
   this many levels. Real markup nests ~8-18 deep (the benchmark pages top out at 18), so
   its :has() subtrees never touch the hash and cost exactly what the pre-memo walk did,
   while a deep nested chain -- the O(n^2) trigger -- collapses to amortized O(1) per
   candidate. Each anchor pays at most this many un-memoized levels before it reaches a
   populated deeper node, bounding its work at O(depth) total. */
#define SEL_HAS_MEMO_MIN_DESCENT TH_HAS_MEMO_DEPTH

static th_node *sel_first_element_child(th_node *node) {
    th_node *child = node->first_child;
    while (child != NULL && child->type != TH_NODE_ELEMENT) {
        child = child->next_sibling;
    }
    return child;
}

static th_node *sel_next_element_sibling(th_node *node) {
    th_node *sibling = node->next_sibling;
    while (sibling != NULL && sibling->type != TH_NODE_ELEMENT) {
        sibling = sibling->next_sibling;
    }
    return sibling;
}

static void sel_has_mark_ancestors(th_node *node, th_node *root, int descent, const sel_complex *rel,
                                   const sel_ctx *ctx) {
    th_node *ancestor = node->parent;
    for (int depth = descent - 1;; depth--) {
        if (depth >= SEL_HAS_MEMO_MIN_DESCENT) {
            sel_memo_put(ctx->has_memo, rel, ancestor, 1);
        }
        if (ancestor == root) {
            return;
        }
        ancestor = ancestor->parent;
    }
}

/* Whether node's subtree (its strict descendants) holds an element matching comp, the
   lone compound of a descendant :has() argument such as :has(a). The result is a pure
   function of the subtree, so it is memoized per (rel, node): each node's children are
   scanned once across the whole query, turning the per-anchor O(subtree) re-walk that
   made nested :has() quadratic into an amortized O(1) memo hit. descent is how far node
   sits below the anchor; the memo is consulted only past SEL_HAS_MEMO_MIN_DESCENT so a
   shallow match costs no more than the plain recursion (which this reduces to when there
   is no memo, the memo can no longer grow, or the descent is still shallow). */
static int sel_has_desc(th_node *node, const sel_complex *rel, const sel_compound *comp, const sel_ctx *ctx,
                        int descent) {
    th_node *current = node;
    int current_descent = descent;
    for (;;) {
        TH_WORK_STEP();
        th_node *child = sel_first_element_child(current);
        if (child != NULL) {
            current = child;
            current_descent++;
        entered:
            if (current_descent >= SEL_HAS_MEMO_MIN_DESCENT) {
                int cached;
                if (sel_memo_get(ctx->has_memo, rel, current, &cached)) {
                    if (cached) {
                        sel_has_mark_ancestors(current, node, current_descent, rel, ctx);
                        return 1;
                    }
                    goto complete;
                }
            }
            if (sel_match_compound(current, comp, ctx)) {
                sel_has_mark_ancestors(current, node, current_descent, rel, ctx);
                return 1;
            }
            continue;
        }
    complete:
        if (current_descent >= SEL_HAS_MEMO_MIN_DESCENT) {
            sel_memo_put(ctx->has_memo, rel, current, 0);
        }
        while (current != node) {
            th_node *sibling = sel_next_element_sibling(current);
            if (sibling != NULL) {
                current = sibling;
                goto entered;
            }
            current = current->parent;
            current_descent--;
            if (current != node && current_descent >= SEL_HAS_MEMO_MIN_DESCENT) {
                sel_memo_put(ctx->has_memo, rel, current, 0);
            }
        }
        return 0;
    }
}

/* Whether rel reaches its subject through child and sibling combinators alone, from compound `first` on (a :scope that
   pins compounds[0] to the anchor leaves the lead to compounds[1]). Only a descendant combinator can put the subject
   deeper than rel->count levels below the anchor, so the walk for any other argument stops there. */
static int sel_rel_levels_bounded(const sel_complex *rel, int first) {
    for (int index = first; index < rel->count; index++) {
        if (rel->compounds[index].combinator == ' ') {
            return 0;
        }
    }
    return 1;
}

/* sel_has_subtree for a rel bounded by sel_rel_levels_bounded: the walk visits the elements `levels` levels below node
   and no deeper, so each child recurses only while a level remains. */
static int sel_has_levels(th_node *node, const sel_complex *rel, int subject, th_node *anchor, const sel_ctx *ctx,
                          int levels) {
    for (th_node *current = sel_first_element_child(node); current != NULL;
         current = sel_next_element_sibling(current)) {
        TH_WORK_STEP();
        if (sel_match_compound(current, &rel->compounds[subject], ctx) &&
            sel_match_from(current, rel, subject, anchor, ctx) == SEL_MATCHES) {
            return 1;
        }
        if (levels > 1 && sel_has_levels(current, rel, subject, anchor, ctx, levels - 1)) {
            return 1;
        }
    }
    return 0;
}

/* levels is 0 for a rel with a descendant combinator, which can reach any depth. */
static int sel_has_subtree(th_node *node, const sel_complex *rel, int subject, th_node *anchor, const sel_ctx *ctx,
                           int levels) {
    if (levels != 0) {
        return sel_has_levels(node, rel, subject, anchor, ctx, levels);
    }
    th_node *current = sel_first_element_child(node);
    while (current != NULL) {
        TH_WORK_STEP();
        if (sel_match_compound(current, &rel->compounds[subject], ctx) &&
            sel_match_from(current, rel, subject, anchor, ctx) == SEL_MATCHES) {
            return 1;
        }
        th_node *child = sel_first_element_child(current);
        if (child != NULL) {
            current = child;
            continue;
        }
        for (;;) {
            th_node *sibling = sel_next_element_sibling(current);
            if (sibling != NULL) {
                current = sibling;
                break;
            }
            current = current->parent;
            if (current == node) {
                return 0;
            }
        }
    }
    return 0;
}

/* Whether a compound holds a nested selector list, whose match can walk siblings of its own. */
static int sel_compound_nests(const sel_compound *compound) {
    for (int index = 0; index < compound->count; index++) {
        if (compound->simples[index].sub != NULL) {
            return 1;
        }
    }
    return 0;
}

/* :has(~ x R) or :has(+ x R) for a rel without :scope: an element sibling y after the
   anchor (for +, the next one) matches x and y:has(R) holds, R being the rest of rel with
   its own leading combinator. The match can only reach y's level from a sibling, so the
   anchor's own subtree never needs a scan. Whether such a y exists at or after a node
   depends only on that node, so for ~, past the first min_run siblings the walk stops at
   the first one whose answer the sibling memo holds, and it stores its answer under each
   sibling it passed from there: a query testing anchors in document order visits each
   sibling a bounded number of times. */
static int sel_has_later_sibling(th_node *anchor, const sel_complex *rel, const sel_ctx *ctx) {
    const char lead = rel->compounds[0].combinator;
    const sel_complex rest = {rel->compounds + 1, rel->count - 1, 0, 0, 0};
    /* rest lives in this frame, so it must not key the :has() memo; the sibling memo keys a
       relative selector by its first compound instead, which outlives the frame. Without
       :scope in rel, nothing below depends on the scope, so the sibling memo carries over. */
    sel_ctx scoped = *ctx;
    scoped.scope = anchor;
    scoped.has_memo = NULL;
    sel_memo *memo = lead == '~' ? &ctx->sibling_memo->table : NULL;
    const uint16_t target_atom = sel_compound_known_type_atom(&rel->compounds[0], ctx->tree);
    /* a sibling left unmemoized repeats the walk of rest or of a nested list, and each link multiplies that walk,
       so a test with more to verify memoizes from its second sibling, and a nested list from its first */
    const int min_run = sel_compound_nests(&rel->compounds[0]) ? 0 : rel->count > 1 ? 1 : SEL_SIBLING_MEMO_MIN_RUN;
    th_node *memo_from = NULL;
    int found = 0;
    int walked = 0;
    th_node *sibling = sel_next_element_sibling(anchor);
    for (; sibling != NULL; sibling = sel_next_element_sibling(sibling), walked++) {
        TH_WORK_STEP();
        if (memo != NULL && walked >= min_run) {
            if (walked == min_run) {
                memo_from = sibling;
            }
            if (sel_memo_get(memo, rel->compounds, sibling, &found)) {
                break;
            }
        }
        if ((target_atom != TH_TAG_UNKNOWN ? sibling->atom == target_atom
                                           : sel_match_compound(sibling, &rel->compounds[0], &scoped)) &&
            (rest.count == 0 || sel_has_match(sibling, &rest, 1, &scoped))) {
            found = 1;
            break;
        }
        if (lead == '+') {
            break;
        }
    }
    if (memo_from != NULL) {
        for (th_node *passed = memo_from; passed != sibling; passed = sel_next_element_sibling(passed)) {
            sel_memo_put(memo, rel->compounds, passed, found);
        }
    }
    return found;
}

/* Whether the anchor element satisfies any :has() relative selector. A relative
   selector reaches the anchor's descendants and, through a leading sibling
   combinator, its following siblings and their subtrees. */
static int sel_has_match(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx) {
    /* inside a :has() relative selector the scope element is the anchor, so a written
       :scope resolves to it rather than the outer query root (Selectors-4 §6.6.2, #431),
       and sibling answers memoized under the outer scope do not carry over */
    sel_ctx scoped = *ctx;
    scoped.scope = anchor;
    scoped.sibling_memo = NULL;
    for (int index = 0; index < count; index++) {
        const sel_complex *rel = &alts[index];
        int subject = rel->count - 1;
        char lead_combinator = rel->compounds[0].combinator;
        if (rel->count == 1 && (lead_combinator == '>' || lead_combinator == '+' || lead_combinator == '~')) {
            if (lead_combinator == '~' && ctx->sibling_memo != NULL && !sel_rel_uses_scope(rel)) {
                if (sel_has_later_sibling(anchor, rel, ctx)) {
                    return 1;
                }
                continue;
            }
            th_node *candidate =
                lead_combinator == '>' ? sel_first_element_child(anchor) : sel_next_element_sibling(anchor);
            if (candidate == NULL) {
                continue;
            }
            const uint16_t target_atom = sel_compound_known_type_atom(&rel->compounds[0], ctx->tree);
            for (; candidate != NULL; candidate = sel_next_element_sibling(candidate)) {
                TH_WORK_STEP();
                if (target_atom != TH_TAG_UNKNOWN ? candidate->atom == target_atom
                                                  : sel_match_compound(candidate, &rel->compounds[0], &scoped)) {
                    return 1;
                }
                if (lead_combinator == '+') {
                    break;
                }
            }
            continue;
        }
        /* Reuse anchor-independent subtree results for descendant compounds without :scope. */
        if (scoped.has_memo != NULL && rel->count == 1 && !sel_rel_uses_scope(rel)) {
            if (sel_has_desc(anchor, rel, &rel->compounds[0], &scoped, 0)) {
                return 1;
            }
            continue;
        }
        /* only a leading sibling combinator (:has(+ x) / :has(~ x)) can match outside
           the anchor's subtree, and then only there: a descendant or child relative
           selector scans the subtree, a sibling one the following siblings. An explicit
           leading :scope pins compound[0] to the anchor, so the reach is set by the
           combinator after it (:has(:scope + x) equals :has(+ x)) (issue #431) */
        const int pinned = rel->count > 1 && sel_compound_has_scope(&rel->compounds[0]);
        const char lead = rel->compounds[pinned].combinator;
        const int levels = rel->count > 1 && sel_rel_levels_bounded(rel, pinned) ? rel->count : 0;
        if (lead != '+' && lead != '~') {
            if (sel_has_subtree(anchor, rel, subject, anchor, &scoped, levels)) {
                return 1;
            }
            continue;
        }
        /* a single test has no memo, and without one testing each later sibling y on its own
           would re-walk y's siblings for every y */
        if ((lead == '+' || ctx->sibling_memo != NULL) && !sel_rel_uses_scope(rel)) {
            if (sel_has_later_sibling(anchor, rel, ctx)) {
                return 1;
            }
            continue;
        }
        for (th_node *sibling = anchor->next_sibling; sibling != NULL; sibling = sibling->next_sibling) {
            TH_WORK_STEP();
            if (sibling->type != TH_NODE_ELEMENT) {
                continue;
            }
            if ((sel_match_compound(sibling, &rel->compounds[subject], &scoped) &&
                 sel_match_from(sibling, rel, subject, anchor, &scoped) == SEL_MATCHES) ||
                sel_has_subtree(sibling, rel, subject, anchor, &scoped, levels)) {
                return 1;
            }
        }
    }
    return 0;
}

/* Each page covers SEL_DEEP_PAGE_NODES consecutive seqs at two bits each. */
#define SEL_DEEP_PAGE_SHIFT 11
#define SEL_DEEP_PAGE_NODES (1 << SEL_DEEP_PAGE_SHIFT)
#define SEL_DEEP_PAGE_BYTES (SEL_DEEP_PAGE_NODES / 4)

static size_t sel_deep_key_home(const void *key, size_t mask) {
    return (size_t)(((uint64_t)(uintptr_t)key * 0x9E3779B97F4A7C15ull) >> 32) & mask;
}

static sel_deep_key *sel_deep_key_find(sel_deep_memo *memo, const void *key) {
    if (memo->last->key == key) {
        return memo->last;
    }
    for (size_t index = sel_deep_key_home(key, memo->mask);; index = (index + 1) & memo->mask) {
        sel_deep_key *slot = &memo->keys[index];
        if (slot->key == key) {
            return memo->last = slot;
        }
        if (slot->key == NULL) {
            return NULL;
        }
    }
}

/* Grows the key table to twice its size, or sets it up. The table is sized by the selector's keys, so it does not
   count toward the limit. */
static int sel_deep_key_grow(sel_deep_memo *memo) {
    const size_t capacity = memo->keys == NULL ? 16 : (memo->mask + 1) * 2;
    sel_deep_key *keys = PyMem_Calloc(capacity, sizeof(sel_deep_key));
    if (keys == NULL) { /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
        return 0;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    const size_t old_capacity = memo->keys == NULL ? 0 : memo->mask + 1;
    sel_deep_key *old = memo->keys;
    memo->keys = keys;
    memo->last = NULL;
    memo->mask = capacity - 1;
    for (size_t index = 0; index < old_capacity; index++) {
        if (old[index].key != NULL) {
            size_t home = sel_deep_key_home(old[index].key, memo->mask);
            while (keys[home].key != NULL) {
                home = (home + 1) & memo->mask;
            }
            keys[home] = old[index];
        }
    }
    PyMem_Free(old);
    return 1;
}

static sel_deep_key *sel_deep_key_slot(sel_deep_memo *memo, const void *key) {
    if (memo->keys == NULL || (memo->count + 1) * 4 > (memo->mask + 1) * 3) {
        if (!sel_deep_key_grow(memo)) { /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    size_t index = sel_deep_key_home(key, memo->mask);
    while (memo->keys[index].key != NULL && memo->keys[index].key != key) {
        index = (index + 1) & memo->mask;
    }
    sel_deep_key *slot = &memo->keys[index];
    if (slot->key == NULL) {
        slot->key = key;
        memo->count++;
    }
    memo->last = slot;
    return slot;
}

static void sel_deep_store_free(sel_deep_memo *memo) {
    if (memo->keys != NULL) {
        for (size_t index = 0; index <= memo->mask; index++) {
            sel_deep_key *slot = &memo->keys[index];
            if (slot->key != NULL) {
                for (size_t block = 0; block < slot->pages_len; block++) {
                    PyMem_Free(slot->pages[block]);
                }
                PyMem_Free(slot->pages);
            }
        }
        PyMem_Free(memo->keys);
    }
    PyMem_Free(memo->runs);
    memo->runs = NULL;
    memo->runs_mask = 0;
    memo->runs_count = 0;
    memo->keys = NULL;
    memo->last = NULL;
    memo->mask = 0;
    memo->count = 0;
    memo->bytes = 0;
}

static void sel_deep_fail(sel_deep_memo *memo) {
    sel_deep_store_free(memo);
    memo->failed = 1;
}

static int sel_deep_reserve(sel_deep_memo *memo, size_t bytes) {
    if (memo->bytes + bytes > memo->limit) {
        return 0;
    }
    memo->bytes += bytes;
    return 1;
}

static int sel_deep_get(sel_deep_memo *memo, const void *key, const th_node *node, int *out) {
    if (memo->keys == NULL) {
        return 0;
    }
    const sel_deep_key *slot = sel_deep_key_find(memo, key);
    if (slot == NULL) {
        return 0;
    }
    const size_t block = node->seq >> SEL_DEEP_PAGE_SHIFT;
    if (block >= slot->pages_len || slot->pages[block] == NULL) {
        return 0;
    }
    const unsigned state =
        (slot->pages[block][(node->seq & (SEL_DEEP_PAGE_NODES - 1)) >> 2] >> ((node->seq & 3) * 2)) & 3u;
    if (state == 0) {
        return 0;
    }
    *out = state == 2;
    return 1;
}

/* Adds the page of block to slot, which has none there, or fails the memo when the limit or memory stops it. */
static int sel_deep_page(sel_deep_memo *memo, sel_deep_key *slot, size_t block) {
    const size_t length = block < slot->pages_len ? slot->pages_len : block + 1;
    const size_t grown = length - slot->pages_len;
    /* GCOVR_EXCL_START: needs a 32-bit build and a node sequence past SIZE_MAX bytes of page table */
    if (length > (SIZE_MAX - SEL_DEEP_PAGE_BYTES) / sizeof(unsigned char *)) {
        sel_deep_fail(memo);
        return 0;
    }
    /* GCOVR_EXCL_STOP */
    if (!sel_deep_reserve(memo, grown * sizeof(unsigned char *) + SEL_DEEP_PAGE_BYTES)) {
        sel_deep_fail(memo);
        return 0;
    }
    if (grown > 0) {
        unsigned char **pages = PyMem_Realloc(slot->pages, length * sizeof(unsigned char *));
        if (pages == NULL) {     /* GCOVR_EXCL_LINE: allocation-failure path */
            sel_deep_fail(memo); /* GCOVR_EXCL_LINE */
            return 0;            /* GCOVR_EXCL_LINE */
        }
        memset(pages + slot->pages_len, 0, grown * sizeof(unsigned char *));
        slot->pages = pages;
        slot->pages_len = length;
    }
    slot->pages[block] = PyMem_Calloc(1, SEL_DEEP_PAGE_BYTES);
    if (slot->pages[block] == NULL) { /* GCOVR_EXCL_LINE: allocation-failure path */
        sel_deep_fail(memo);          /* GCOVR_EXCL_LINE */
        return 0;                     /* GCOVR_EXCL_LINE */
    }
    return 1;
}

static void sel_deep_put(sel_deep_memo *memo, const void *key, const th_node *node, int result) {
    if (memo->failed) {
        return;
    }
    sel_deep_key *slot = memo->last != NULL && memo->last->key == key ? memo->last : sel_deep_key_slot(memo, key);
    if (slot == NULL) {      /* GCOVR_EXCL_LINE: allocation-failure path */
        sel_deep_fail(memo); /* GCOVR_EXCL_LINE */
        return;              /* GCOVR_EXCL_LINE */
    }
    const size_t block = node->seq >> SEL_DEEP_PAGE_SHIFT;
    if (block >= slot->pages_len || slot->pages[block] == NULL) {
        if (!sel_deep_page(memo, slot, block)) {
            return;
        }
    }
    unsigned char *byte = &slot->pages[block][(node->seq & (SEL_DEEP_PAGE_NODES - 1)) >> 2];
    const unsigned shift = (node->seq & 3) * 2;
    *byte = (unsigned char)((*byte & ~(3u << shift)) | ((result ? 2u : 1u) << shift));
}

/* How many siblings a walk passes before it adds a record of its run (sel_deep_run) when none exists. A walk looks one
   up from SEL_SIBLING_MEMO_MIN_RUN on, but a record of 24 bytes added only past this many keeps the records under a
   fifth of a byte per sibling walked. */
#define SEL_DEEP_RUN_MIN 128

/* A bound past every position: nothing known on that side yet. */
#define SEL_DEEP_RUN_END INT_MAX

static size_t sel_deep_run_home(const void *key, const th_node *parent, size_t mask) {
    return (size_t)((((uint64_t)(uintptr_t)key ^ ((uint64_t)(uintptr_t)parent << 7)) * 0x9E3779B97F4A7C15ull) >> 32) &
           mask;
}

/* Grows the record table to twice its size, or sets it up. Past limit it drops every record and doubles how far a walk
   goes before it keeps one: a record needs a run longer than that, so the records then fit in fewer bytes, and a walk
   without one passes fewer siblings than that. */
static int sel_deep_run_grow(sel_deep_memo *table) {
    const size_t capacity = table->runs == NULL ? 16 : (table->runs_mask + 1) * 2;
    sel_deep_run *runs =
        capacity <= table->limit / sizeof(sel_deep_run) ? PyMem_Calloc(capacity, sizeof(sel_deep_run)) : NULL;
    if (runs == NULL) {
        PyMem_Free(table->runs);
        table->runs = NULL;
        table->runs_mask = 0;
        table->runs_count = 0;
        table->runs_doubled++;
        return 0;
    }
    for (size_t index = 0; table->runs != NULL && index <= table->runs_mask; index++) {
        if (table->runs[index].key != NULL) {
            size_t home = sel_deep_run_home(table->runs[index].key, table->runs[index].parent, capacity - 1);
            while (runs[home].key != NULL) {
                home = (home + 1) & (capacity - 1);
            }
            runs[home] = table->runs[index];
        }
    }
    PyMem_Free(table->runs);
    table->runs = runs;
    table->runs_mask = capacity - 1;
    return 1;
}

static sel_deep_run *sel_deep_run_find(const sel_deep_memo *table, const void *key, const th_node *parent) {
    if (table->runs == NULL) {
        return NULL;
    }
    for (size_t index = sel_deep_run_home(key, parent, table->runs_mask);; index = (index + 1) & table->runs_mask) {
        sel_deep_run *run = &table->runs[index];
        if (run->key == key && run->parent == parent) {
            return run;
        }
        if (run->key == NULL) {
            return NULL;
        }
    }
}

/* The record of key over the children of parent, added when missing with nothing known yet, or NULL when the table
   could not keep it. rising picks the bounds of a backward walk. */
static sel_deep_run *sel_deep_run_of(sel_deep_memo *table, const void *key, const th_node *parent, int rising) {
    sel_deep_run *run = sel_deep_run_find(table, key, parent);
    if (run != NULL) {
        return run;
    }
    if ((table->runs == NULL || (table->runs_count + 1) * 4 > (table->runs_mask + 1) * 3) &&
        !sel_deep_run_grow(table)) {
        return NULL;
    }
    size_t index = sel_deep_run_home(key, parent, table->runs_mask);
    while (table->runs[index].key != NULL) {
        index = (index + 1) & table->runs_mask;
    }
    run = &table->runs[index];
    *run = (sel_deep_run){key, parent, rising ? 0 : SEL_DEEP_RUN_END, rising ? SEL_DEEP_RUN_END : 0};
    table->runs_count++;
    return run;
}

/* The matcher of a query's stand-in (sel_deep_query): the matcher above, with the answers of its ancestor walks kept
   in memo. A walk this short runs without it, so a tree that nests no deeper than ordinary markup never pays for a
   hash probe. */
#define SEL_ANCESTOR_MEMO_MIN_RUN 32

static int sel_deep_alts(th_node *node, const sel_complex *alts, int count, const sel_ctx *ctx, sel_deep_memo *memo);
static int sel_deep_has(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx, sel_deep_memo *memo);

/* The kept tables of memo, allocated on first use, or NULL when that fails: the caller then takes the direct walk.
 */
static sel_deep_kept *sel_deep_kept_of(sel_deep_memo *memo) {
    if (memo->kept == NULL) {
        memo->kept = PyMem_Calloc(1, sizeof(sel_deep_kept));
        if (memo->kept != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            memo->kept->lists.limit = memo->limit;
            memo->kept->scoped.limit = memo->limit;
            memo->kept->subject_scope = memo->subject_scope;
        }
    }
    return memo->kept;
}

/* Empties a table of answers the scope has rebound past, so the answers it held no longer apply. */
static void sel_deep_clear(sel_deep_memo *table) {
    sel_deep_store_free(table);
    table->failed = 0;
}

/* The table of the answers that depend on the scope element, emptied when the scope is no longer the one they hold
   for: the cascade and Matcher.filter rebind it per element. */
static sel_deep_memo *sel_deep_scoped(sel_deep_kept *kept, const th_node *scope) {
    if (kept->scoped_for != scope) {
        sel_deep_clear(&kept->scoped);
        kept->scoped_for = scope;
    }
    return &kept->scoped;
}

/* The table that keeps the answers of a walk for compounds[0..index - 1] of complex past its first steps: memo, or
   scoped where those compounds write :scope and the driver may rebind the scope (it passes a sibling memo only
   while the scope stays put). NULL when no table can keep them. */
static sel_deep_memo *sel_deep_walk_table(const sel_complex *complex, int index, const sel_ctx *ctx,
                                          sel_deep_memo *memo) {
    if (ctx->sibling_memo != NULL || !sel_rel_uses_scope(&(const sel_complex){complex->compounds, index, 0, 0, 0})) {
        return memo;
    }
    sel_deep_kept *kept = sel_deep_kept_of(memo);
    if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (kept->subject_scope && !(complex->walks & SEL_ALT_AHEAD)) {
        return memo;
    }
    return sel_deep_scoped(kept, ctx->scope);
}

/* sel_deep_walk_table for a sibling walk, which inside :has() keeps its answers in the scoped table bound to the
   anchor: the anchor bounds the walk and binds :scope, and sel_deep_has sets the enclosing answers aside. */
static sel_deep_memo *sel_deep_sibling_table(const sel_complex *complex, int index, th_node *anchor, const sel_ctx *ctx,
                                             sel_deep_memo *memo) {
    if (anchor == NULL) {
        return sel_deep_walk_table(complex, index, ctx, memo);
    }
    sel_deep_kept *kept = sel_deep_kept_of(memo);
    if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    return sel_deep_scoped(kept, anchor);
}

/* Whether the deep matcher keeps the answers of the :is(), :where() or :not() simple: one asked again whose walks only
   climb (SEL_NEST_DEEP) costs no more than the tree nests per question, so on a tree known to nest less than
   SEL_HAS_MEMO_MIN_DESCENT deep the direct walk is cheaper than the table. */
static int sel_deep_keeps(const sel_simple *simple, const sel_ctx *ctx) {
    return simple->nest == SEL_NEST_ALWAYS ||
           (simple->nest == SEL_NEST_DEEP && th_tree_nest_bound(ctx->tree) < SEL_HAS_MEMO_MIN_DESCENT);
}

/* A nested list one match asks again (sel_simple.nest): the answer of each alternative that walks is kept in lists,
   or in scoped for one that writes :scope, so each walk runs once per element however many enclosing walks ask. */
static int sel_deep_list(th_node *node, const sel_simple *simple, const sel_ctx *ctx, sel_deep_memo *memo) {
    sel_deep_kept *kept = sel_deep_kept_of(memo);
    if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return sel_deep_alts(node, simple->sub, simple->sub_count, ctx, memo); /* GCOVR_EXCL_LINE: allocation failure */
    }
    for (int index = 0; index < simple->sub_count; index++) {
        const sel_complex *alt = &simple->sub[index];
        int matched;
        if (!(alt->walks & SEL_ALT_WALKS)) {
            matched = sel_deep_matches_alt(node, alt, ctx, memo);
        } else if ((alt->walks & SEL_ALT_SCOPED) &&
                   (!kept->subject_scope || node == ctx->scope || (alt->walks & SEL_ALT_AHEAD))) {
            if (!sel_deep_get(sel_deep_scoped(kept, ctx->scope), alt, node, &matched)) {
                matched = sel_deep_matches_alt(node, alt, ctx, memo);
                sel_deep_put(sel_deep_scoped(kept, ctx->scope), alt, node, matched);
            }
        } else if (!sel_deep_get(&kept->lists, alt, node, &matched)) {
            matched = sel_deep_matches_alt(node, alt, ctx, memo);
            sel_deep_put(&kept->lists, alt, node, matched);
        }
        if (matched) {
            return 1;
        }
    }
    return 0;
}

/* A :has() one match asks again (sel_simple.nest). It binds :scope to its anchor, so its answer holds under any
   scope. */
static int sel_deep_has_once(th_node *node, const sel_simple *simple, const sel_ctx *ctx, sel_deep_memo *memo) {
    sel_deep_kept *kept = sel_deep_kept_of(memo);
    if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return sel_deep_has(node, simple->sub, simple->sub_count, ctx, memo); /* GCOVR_EXCL_LINE: allocation failure */
    }
    int found;
    if (!sel_deep_get(&kept->lists, simple, node, &found)) {
        found = sel_deep_has(node, simple->sub, simple->sub_count, ctx, memo);
        sel_deep_put(&kept->lists, simple, node, found);
    }
    return found;
}

/* The slot of slots, a table of capacity counts, that holds the counts of the 'of S' clause simple, or the empty one
   they go in. A clause's number (sel_simple.nest) is its home, so the clauses of one selector take consecutive slots.
 */
static size_t sel_deep_counts_slot(sel_deep_counts *const *slots, int capacity, const sel_simple *simple) {
    const size_t mask = (size_t)capacity - 1;
    size_t index = (size_t)(unsigned)simple->nest & mask;
    while (slots[index] != NULL && slots[index]->owner != simple) {
        index = (index + 1) & mask;
    }
    return index;
}

/* The counts of the 'of S' clause simple, added empty on its first use, or NULL when allocation fails. They are keyed
   by the clause, as the cascade's memo serves the clauses of every rule. */
static sel_deep_counts *sel_deep_counts_of(sel_deep_kept *kept, const sel_simple *simple) {
    if (kept->counts != NULL) {
        sel_deep_counts *found = kept->counts[sel_deep_counts_slot(kept->counts, kept->count_slots, simple)];
        if (found != NULL) {
            return found;
        }
    }
    if ((kept->count_used + 1) * 4 > kept->count_slots * 3) {
        const int capacity = kept->count_slots == 0 ? 8 : kept->count_slots * 2;
        sel_deep_counts **grown = PyMem_Calloc((size_t)capacity, sizeof(sel_deep_counts *));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        for (int slot = 0; slot < kept->count_slots; slot++) {
            if (kept->counts[slot] != NULL) {
                grown[sel_deep_counts_slot(grown, capacity, kept->counts[slot]->owner)] = kept->counts[slot];
            }
        }
        PyMem_Free(kept->counts);
        kept->counts = grown;
        kept->count_slots = capacity;
    }
    sel_deep_counts *counts = PyMem_Calloc(1, sizeof(sel_deep_counts));
    if (counts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    counts->owner = simple;
    kept->counts[sel_deep_counts_slot(kept->counts, kept->count_slots, simple)] = counts;
    kept->count_used++;
    return counts;
}

/* The index :nth-child(An+B of S) or :nth-last-child(An+B of S) gives node, for a clause one match asks about many
   siblings (sel_simple.nest): one plus the count of the element siblings before node (after it, for
   :nth-last-child()) that match S. Each sibling's index is kept in the clause's counts, filled from the nearest
   sibling that has one, so a sibling list costs one pass per clause in whatever order its siblings are asked. A
   position inside S reads the position memo, which a single test's context lacks. */
static int sel_deep_count(th_node *node, int direction, const sel_simple *simple, const sel_ctx *ctx,
                          sel_deep_memo *memo) {
    sel_deep_kept *kept = sel_deep_kept_of(memo);
    sel_deep_counts *counts = kept != NULL ? sel_deep_counts_of(kept, simple) : NULL; /* GCOVR_EXCL_BR_LINE */
    if (counts == NULL) {                                          /* GCOVR_EXCL_BR_LINE: allocation failure */
        return sel_nth_of_index(node, direction < 0, simple, ctx); /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (counts->scope != ctx->scope && (!kept->subject_scope || simple->pseudo != PSEUDO_NTH_CHILD) &&
        sel_alts_write_scope(simple->sub, simple->sub_count)) {
        PyMem_Free(counts->map.entries);
        counts->map = (th_node_map){0};
        counts->scope = ctx->scope;
    }
    sel_ctx counting = *ctx;
    if (counting.position_memo == NULL) {
        counting.position_memo = &kept->positions;
    }
    Py_ssize_t index = th_node_map_find(&counts->map, node);
    if (index != 0) {
        return (int)index;
    }
    th_node *from = node;
    th_node *known = NULL;
    for (th_node *sibling = direction < 0 ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = direction < 0 ? sibling->next_sibling : sibling->prev_sibling) {
        TH_WORK_STEP();
        if (sibling->type == TH_NODE_ELEMENT) {
            index = th_node_map_find(&counts->map, sibling);
            if (index != 0) {
                known = sibling;
                break;
            }
            from = sibling;
        }
    }
    /* :nth-last-child() reads S after node, where a match whose scope is its subject reaches the scope */
    const int subject_scope = kept->subject_scope;
    kept->subject_scope = subject_scope && direction > 0;
    index = known != NULL ? index + sel_deep_list(known, simple, &counting, memo) : 1;
    for (th_node *sibling = from;; sibling = direction < 0 ? sibling->prev_sibling : sibling->next_sibling) {
        TH_WORK_STEP();
        if (sibling->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (th_node_map_insert(&counts->map, sibling, index) < 0) {     /* GCOVR_EXCL_BR_LINE */
            index = sel_nth_of_index(node, direction < 0, simple, ctx); /* GCOVR_EXCL_LINE */
            break;                                                      /* GCOVR_EXCL_LINE */
        }
        if (sibling == node) {
            break;
        }
        index += sel_deep_list(sibling, simple, &counting, memo);
    }
    kept->subject_scope = subject_scope;
    return (int)index;
}
static int sel_deep_alts(th_node *node, const sel_complex *alts, int count, const sel_ctx *ctx, sel_deep_memo *memo);
static int sel_deep_has(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx, sel_deep_memo *memo);
static enum sel_status sel_deep_left(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                     const sel_ctx *ctx, sel_deep_memo *memo);

/* Whether node declares the language a :lang() or the direction a :dir() reads, so its descendants that declare
   none answer as it does: an element with a lang or xml:lang, or one with a valid dir or a bdi without one. */
static int sel_deep_declares(th_node *node, const sel_simple *simple, th_tree *tree) {
    if (simple->pseudo == PSEUDO_LANG) {
        return sel_lang_attr(tree, node, simple->attr_atom) != NULL;
    }
    const th_node_attr *attr = sel_find_attr(node, TH_ATTR_DIR);
    if (attr != NULL && attr->value != NULL) {
        return sel_kw(attr->value, attr->value_len, "ltr") || sel_kw(attr->value, attr->value_len, "rtl") ||
               sel_kw(attr->value, attr->value_len, "auto");
    }
    return node->ns == TH_NS_HTML && node->atom == TH_TAG_BDI;
}

/* :lang() or :dir() at node: the nearest self-or-ancestor element that declares one answers, or, with none, the
   document default (an untagged language, ltr), which nth_a holds for both. An element between them answers as node
   does, so past the first SEL_ANCESTOR_MEMO_MIN_RUN levels the walk stops at the first one whose answer the memo holds
   and stores its answer under each one it passed. A telephone input never inherits its direction. */
static int sel_deep_inherited(th_node *node, const sel_simple *simple, const sel_ctx *ctx, sel_deep_memo *memo) {
    if (simple->pseudo == PSEUDO_DIR && node->ns == TH_NS_HTML && node->atom == TH_TAG_INPUT &&
        sel_input_type_is(node, "tel")) {
        return sel_match_simple(node, simple, ctx);
    }
    int found = simple->nth_a == 1;
    th_node *memo_from = NULL;
    th_node *ancestor = node;
    for (int walked = 0; ancestor != NULL && ancestor->type == TH_NODE_ELEMENT; ancestor = ancestor->parent, walked++) {
        TH_WORK_STEP();
        if (walked >= SEL_ANCESTOR_MEMO_MIN_RUN) {
            if (memo_from == NULL) {
                memo_from = ancestor;
            }
            if (sel_deep_get(memo, simple, ancestor, &found)) {
                break;
            }
        }
        if (sel_deep_declares(ancestor, simple, ctx->tree)) {
            found = sel_match_simple(ancestor, simple, ctx);
            break;
        }
    }
    if (memo_from != NULL) {
        for (th_node *passed = memo_from; passed != ancestor; passed = passed->parent) {
            sel_deep_put(memo, simple, passed, found);
        }
    }
    return found;
}

/* :nth-child(An+B of S) and :nth-last-child(An+B of S): sel_nth_index reading S through this matcher, with the
   count kept per clause (sel_deep_count). */
static int sel_deep_nth(th_node *node, int direction, const sel_simple *simple, const sel_ctx *ctx,
                        sel_deep_memo *memo) {
    return sel_deep_list(node, simple, ctx, memo) ? sel_deep_count(node, direction, simple, ctx, memo) : 0;
}

/* sel_match_simple for the simples whose answer climbs, or holds a selector list that may. */
static int sel_deep_simple(th_node *node, const sel_simple *simple, const sel_ctx *ctx, sel_deep_memo *memo) {
    if (simple->kind == ':') {
        switch (simple->pseudo) {
        case PSEUDO_LANG:
        case PSEUDO_DIR:
            return sel_deep_inherited(node, simple, ctx, memo);
        case PSEUDO_IS:
        case PSEUDO_WHERE:
            return sel_deep_keeps(simple, ctx) ? sel_deep_list(node, simple, ctx, memo)
                                               : sel_deep_alts(node, simple->sub, simple->sub_count, ctx, memo);
        case PSEUDO_NOT:
            return sel_deep_keeps(simple, ctx) ? !sel_deep_list(node, simple, ctx, memo)
                                               : !sel_deep_alts(node, simple->sub, simple->sub_count, ctx, memo);
        case PSEUDO_HAS:
            return simple->nest != 0 ? sel_deep_has_once(node, simple, ctx, memo)
                                     : sel_deep_has(node, simple->sub, simple->sub_count, ctx, memo);
        case PSEUDO_NTH_CHILD:
        case PSEUDO_NTH_LAST_CHILD:
            if (simple->sub != NULL) {
                const int index = sel_deep_nth(node, simple->pseudo == PSEUDO_NTH_CHILD ? 1 : -1, simple, ctx, memo);
                return index != 0 && sel_nth_matches(simple->nth_a, simple->nth_b, index);
            }
            break;
        default:
            break;
        }
    }
    return sel_match_simple(node, simple, ctx);
}

/* Charge one compound test to memo's allowance: 0 once it is spent. */
static int sel_deep_spend(sel_deep_memo *memo) {
    if (++memo->spent > memo->allowance) {
        memo->over = 1;
    }
    return !memo->over;
}

static int sel_deep_compound(th_node *node, const sel_compound *compound, const sel_ctx *ctx, sel_deep_memo *memo) {
    if (node->type != TH_NODE_ELEMENT || (memo->walking != 0 && !sel_deep_spend(memo))) {
        return 0;
    }
    for (int index = 0; index < compound->count; index++) {
        if (!sel_deep_simple(node, &compound->simples[index], ctx, memo)) {
            return 0;
        }
    }
    return 1;
}

/* Whether node matches target, comparing atoms when target is a lone builtin type selector of an HTML tree. */
static int sel_deep_target(th_node *node, const sel_compound *target, uint16_t target_atom, const sel_ctx *ctx,
                           sel_deep_memo *memo) {
    if (target_atom != TH_TAG_UNKNOWN) {
        return node->type == TH_NODE_ELEMENT && node->atom == target_atom;
    }
    return sel_deep_compound(node, target, ctx, memo);
}

static enum sel_status sel_deep_from(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                     const sel_ctx *ctx, sel_deep_memo *memo) {
    if (index == 0) {
        return anchor == NULL ? SEL_MATCHES : sel_match_anchor(node, &complex->compounds[0], anchor);
    }
    return sel_deep_left(node, complex, index, anchor, ctx, memo);
}

/* node's position among its element siblings, 1 for the first, or 0 when indexing could not allocate. */
static int sel_deep_position(const th_node *node, const sel_ctx *ctx, sel_deep_memo *memo) {
    sel_ctx indexed = *ctx;
    if (indexed.position_memo == NULL) {
        sel_deep_kept *kept = sel_deep_kept_of(memo);
        if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 0;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        indexed.position_memo = &kept->positions;
    }
    return sel_position(&indexed, node, 0, 0);
}

/* The record a walk of key over the children of parent stops on once it has passed *from siblings: an existing one,
   or a new one past the table's threshold (SEL_DEEP_RUN_MIN, raised past limit). NULL while the walk has to go on to
   that threshold (raising *from) or when the table cannot keep one. */
static sel_deep_run *sel_deep_run_take(sel_deep_memo *table, const void *key, const th_node *parent, int rising,
                                       int *from) {
    if (table == NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    sel_deep_run *run = sel_deep_run_find(table, key, parent);
    const int keep_from = SEL_DEEP_RUN_MIN << table->runs_doubled;
    if (run != NULL || *from >= keep_from) {
        return run != NULL ? run : sel_deep_run_of(table, key, parent, rising);
    }
    *from = keep_from;
    return NULL;
}

/* sel_match_sibling_walk through this matcher. Past the first SEL_SIBLING_MEMO_MIN_RUN siblings a walk the driver's
   memo does not serve keeps a record of its key over the run (sel_deep_run): it stops where the record decides, and
   moves a bound past each sibling it passed, so the walks of one run pass each sibling a bounded number of times
   whatever order they come in. */
static enum sel_status sel_deep_siblings(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                         const sel_ctx *ctx, sel_deep_memo *memo) {
    const sel_compound *target = &complex->compounds[index - 1];
    const uint16_t target_atom = sel_compound_known_type_atom(target, ctx->tree);
    th_node *nearest = sel_prev_element(node);
    sel_sibling_memo *siblings = anchor == NULL ? ctx->sibling_memo : NULL;
    sel_sibling_record *last = siblings != NULL ? &siblings->last_walks[(unsigned)index % SEL_SIBLING_RECORDS] : NULL;
    if (last != NULL && last->node == node && last->key == target) {
        return (enum sel_status)last->status;
    }
    enum sel_status status = SEL_FAILS_ALL_SIBLINGS;
    int walked = 0;
    int from = siblings == NULL ? SEL_SIBLING_MEMO_MIN_RUN : INT_MAX;
    int position = 0; /* prev's, once the walk keeps a record */
    int subject = 0;
    int below = 0; /* no match sits before this position */
    th_node *prev = nearest;
    for (; prev != NULL; prev = sel_prev_element(prev), walked++, position--) {
        TH_WORK_STEP();
        if (walked == from) {
            const sel_deep_run *run = sel_deep_run_take(sel_deep_sibling_table(complex, index, anchor, ctx, memo),
                                                        target, node->parent, 1, &from);
            position = run != NULL ? sel_deep_position(prev, ctx, memo) : 0;
            if (position != 0) {
                subject = position + walked + 1;
                below = run->no;
                if (position >= run->yes - 1) {
                    status = SEL_MATCHES;
                    break;
                }
            }
        }
        int cached;
        if (subject != 0 && position < below) {
            break;
        }
        if (siblings != NULL && walked >= SEL_SIBLING_MEMO_MIN_RUN &&
            sel_memo_get(&siblings->table, target, prev, &cached)) {
            status = cached ? SEL_MATCHES : SEL_FAILS_ALL_SIBLINGS;
            break;
        }
        if (sel_deep_target(prev, target, target_atom, ctx, memo)) {
            const enum sel_status left = sel_deep_from(prev, complex, index - 1, anchor, ctx, memo);
            if (left != SEL_FAILS_LOCALLY) {
                status = left;
                break;
            }
        }
        if (last != NULL && last->node == prev && last->key == target) {
            status = (enum sel_status)last->status;
            break;
        }
    }
    if (siblings != NULL) {
        if (walked >= SEL_SIBLING_MEMO_MIN_RUN) {
            sel_memo_put(&siblings->table, target, nearest, status == SEL_MATCHES);
        }
        *last = (sel_sibling_record){target, node, status};
    } else if (subject != 0) {
        /* the walk's own questions can rebind the scoped table, or drop the records past its limit */
        sel_deep_memo *table = sel_deep_sibling_table(complex, index, anchor, ctx, memo);
        if (table == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return status;   /* GCOVR_EXCL_LINE: allocation failure */
        }
        sel_deep_run *run = sel_deep_run_find(table, target, node->parent);
        if (run == NULL) {
            return status;
        }
        if (status == SEL_MATCHES) {
            run->yes = position + 1 < run->yes ? position + 1 : run->yes;
        } else {
            run->no = subject > run->no ? subject : run->no;
        }
    }
    return status;
}

/* The descendant combinator to the left of compounds[index]: an ancestor of node matches compounds[index - 1] and
   everything left of it. Whether such an ancestor exists at or above a node depends only on that node, so past the
   first SEL_ANCESTOR_MEMO_MIN_RUN ancestors the walk stops at the first one whose answer the memo holds and stores
   its answer under each one it passed: a query visits each ancestor a bounded number of times. Inside :has() the
   answer depends on the anchor, so the walk keeps off the memo; where compounds[0..index - 1] write :scope it
   depends on the scope too, so the walk keeps off it unless the context holds a sibling memo, which a driver passes
   only while the scope stays put. */
static enum sel_status sel_deep_ancestors(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                          const sel_ctx *ctx, sel_deep_memo *memo) {
    const sel_compound *target = &complex->compounds[index - 1];
    const uint16_t target_atom = sel_compound_known_type_atom(target, ctx->tree);
    enum sel_status status = SEL_FAILS_COMPLETELY;
    th_node *memo_from = NULL;
    sel_deep_memo *table = memo;
    th_node *ancestor = node->parent;
    for (int walked = 0; ancestor != NULL; ancestor = ancestor->parent, walked++) {
        TH_WORK_STEP();
        if (walked == SEL_ANCESTOR_MEMO_MIN_RUN && anchor == NULL) {
            table = sel_deep_walk_table(complex, index, ctx, memo);
            memo_from = table != NULL ? ancestor : NULL; /* GCOVR_EXCL_BR_LINE: NULL only on allocation failure */
        }
        int cached;
        if (memo_from != NULL && sel_deep_get(table, target, ancestor, &cached)) {
            status = cached ? SEL_MATCHES : SEL_FAILS_COMPLETELY;
            break;
        }
        if (sel_deep_target(ancestor, target, target_atom, ctx, memo)) {
            const enum sel_status left = sel_deep_from(ancestor, complex, index - 1, anchor, ctx, memo);
            if (left == SEL_MATCHES || left == SEL_FAILS_COMPLETELY) {
                status = left;
                break;
            }
        }
    }
    if (memo_from != NULL) {
        if (table != memo) {
            /* the walk's own questions can rebind the scoped table */
            table = sel_deep_walk_table(complex, index, ctx, memo);
        }
        for (th_node *passed = memo_from; passed != ancestor; passed = passed->parent) {
            sel_deep_put(table, target, passed, status == SEL_MATCHES);
        }
    }
    return status;
}

/* sel_match_left through this matcher. */
static enum sel_status sel_deep_left(th_node *node, const sel_complex *complex, int index, th_node *anchor,
                                     const sel_ctx *ctx, sel_deep_memo *memo) {
    const sel_compound *target = &complex->compounds[index - 1];
    const uint16_t target_atom = sel_compound_known_type_atom(target, ctx->tree);
    switch (complex->compounds[index].combinator) {
    case '>': {
        th_node *parent = node->parent;
        if (parent == NULL) {
            return SEL_FAILS_ALL_SIBLINGS;
        }
        enum sel_status status = SEL_FAILS_LOCALLY;
        if (sel_deep_target(parent, target, target_atom, ctx, memo)) {
            status = sel_deep_from(parent, complex, index - 1, anchor, ctx, memo);
        }
        return status == SEL_FAILS_LOCALLY ? SEL_FAILS_ALL_SIBLINGS : status;
    }
    case '+': {
        th_node *prev = sel_prev_element(node);
        if (prev == NULL) {
            return SEL_FAILS_ALL_SIBLINGS;
        }
        if (!sel_deep_target(prev, target, target_atom, ctx, memo)) {
            return SEL_FAILS_LOCALLY;
        }
        return sel_deep_from(prev, complex, index - 1, anchor, ctx, memo);
    }
    case '~':
        return sel_deep_siblings(node, complex, index, anchor, ctx, memo);
    default: /* descendant */
        return sel_deep_ancestors(node, complex, index, anchor, ctx, memo);
    }
}

/* A SEL_ALT_SCOPE_OFFSET chain at node: its element siblings from the scope element on, compared by position rather
   than walked. Only an element among node's earlier siblings can match :scope for it, and a document or a text node
   as the scope never has one. -1 when the positions could not be indexed, and the matcher walks. */
static int sel_deep_scope_offset(th_node *node, const sel_complex *complex, const sel_ctx *ctx, sel_deep_memo *memo) {
    if (node->parent != ctx->scope->parent) {
        return 0;
    }
    const int scope_at = sel_deep_position(ctx->scope, ctx, memo);
    const int node_at = sel_deep_position(node, ctx, memo);
    if (scope_at == 0 || node_at == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                       /* GCOVR_EXCL_LINE: allocation failure */
    }
    int exact = 1;
    for (int index = 1; index < complex->count; index++) {
        exact &= complex->compounds[index].combinator == '+';
    }
    const int distance = node_at - scope_at;
    return (exact ? distance == complex->count - 1 : distance >= complex->count - 1) &&
           sel_deep_compound(node, &complex->compounds[complex->count - 1], ctx, memo);
}

int sel_deep_matches_alt(th_node *node, const sel_complex *complex, const sel_ctx *ctx, sel_deep_memo *memo) {
    /* -1 without a scope offset, or on allocation failure */
    const int offset = complex->walks & SEL_ALT_SCOPE_OFFSET ? sel_deep_scope_offset(node, complex, ctx, memo) : -1;
    if (offset >= 0) {
        return offset;
    }
    const int subject = complex->count - 1;
    return sel_deep_compound(node, &complex->compounds[subject], ctx, memo) &&
           sel_deep_from(node, complex, subject, NULL, ctx, memo) == SEL_MATCHES;
}

static int sel_deep_alts(th_node *node, const sel_complex *alts, int count, const sel_ctx *ctx, sel_deep_memo *memo) {
    for (int index = 0; index < count; index++) {
        if (sel_deep_matches_alt(node, &alts[index], ctx, memo)) {
            return 1;
        }
    }
    return 0;
}

/* The memo key of the :has() answers about compounds[index] of rel and the compounds right of it. The combinator's
   address rather than the compound's keeps these apart from the answers of the ancestor walks, which key by
   compound. */
static const void *sel_deep_has_key(const sel_complex *rel, int index) {
    return &rel->compounds[index].combinator;
}

static int sel_deep_has_rel(th_node *node, const sel_complex *rel, int index, const sel_ctx *ctx, sel_deep_memo *memo);

/* Whether node matches compounds[index] of rel, a :has() argument without :scope, and the compounds right of it
   hang off node in turn: rel read left to right, so that every answer is a fact of the tree and the memo keeps it
   for every anchor. -1 once the memo has failed, since without it each node would rescan its subtree; the anchor
   then takes the direct walk. */
static int sel_deep_has_step(th_node *node, const sel_complex *rel, int index, const sel_ctx *ctx,
                             sel_deep_memo *memo) {
    if (!sel_deep_compound(node, &rel->compounds[index], ctx, memo)) {
        return 0;
    }
    if (index + 1 == rel->count) {
        return 1;
    }
    return memo->failed ? -1 : sel_deep_has_rel(node, rel, index + 1, ctx, memo);
}

/* Whether node's subtree (its strict descendants) holds an element that passes sel_deep_has_step at index: the
   descendant walk of sel_has_desc, with every answer past SEL_HAS_MEMO_MIN_DESCENT levels below the anchor kept in
   memo under key. */
static int sel_deep_has_desc(th_node *node, const sel_complex *rel, int index, const sel_ctx *ctx, sel_deep_memo *memo,
                             int descent) {
    const void *key = sel_deep_has_key(rel, index);
    th_node *current = node;
    int current_descent = descent;
    int found = 0;
    for (;;) {
        TH_WORK_STEP();
        th_node *child = sel_first_element_child(current);
        if (child != NULL) {
            current = child;
            current_descent++;
        entered:
            if (current_descent >= SEL_HAS_MEMO_MIN_DESCENT && sel_deep_get(memo, key, current, &found)) {
                if (found) {
                    break;
                }
                goto complete;
            }
            found = sel_deep_has_step(current, rel, index, ctx, memo);
            if (found != 0) {
                break;
            }
            continue;
        }
    complete:
        /* node itself was never tested, so its subtree's answer is not its own */
        if (current != node && current_descent >= SEL_HAS_MEMO_MIN_DESCENT) {
            sel_deep_put(memo, key, current, 0);
        }
        while (current != node) {
            th_node *sibling = sel_next_element_sibling(current);
            if (sibling != NULL) {
                current = sibling;
                goto entered;
            }
            current = current->parent;
            current_descent--;
            if (current != node && current_descent >= SEL_HAS_MEMO_MIN_DESCENT) {
                sel_deep_put(memo, key, current, 0);
            }
        }
        return 0;
    }
    /* a failed memo stores nothing more, so marking after a -1 is harmless */
    for (th_node *ancestor = current->parent;; ancestor = ancestor->parent) {
        if (--current_descent >= SEL_HAS_MEMO_MIN_DESCENT) {
            sel_deep_put(memo, key, ancestor, 1);
        }
        if (ancestor == node) {
            return found;
        }
    }
}

/* Whether first or an element sibling after it passes sel_deep_has_step at index. Whether one does at or after a
   sibling depends only on that sibling, so past the first SEL_SIBLING_MEMO_MIN_RUN siblings the walk keeps a record
   of the run (sel_deep_run), stops where it decides and moves a bound past the siblings it passed: the anchors of a
   long sibling run pass each sibling a bounded number of times. */
static int sel_deep_has_from(th_node *first, const th_node *parent, const sel_complex *rel, int index,
                             const sel_ctx *ctx, sel_deep_memo *memo) {
    const void *key = sel_deep_has_key(rel, index);
    int from = SEL_SIBLING_MEMO_MIN_RUN;
    int position = 0; /* the sibling's, once the walk keeps a record */
    int start = 0;
    int above = SEL_DEEP_RUN_END; /* no sibling passes from this position on */
    int found = 0;
    int walked = 0;
    for (th_node *sibling = first; sibling != NULL; sibling = sel_next_element_sibling(sibling), walked++, position++) {
        TH_WORK_STEP();
        if (walked == from) {
            const sel_deep_run *run = sel_deep_run_take(memo, key, parent, 0, &from);
            position = run != NULL ? sel_deep_position(sibling, ctx, memo) : 0;
            if (position != 0) {
                start = position - walked;
                above = run->no;
                if (position <= run->yes) {
                    return 1;
                }
            }
        }
        if (position >= above || (found = sel_deep_has_step(sibling, rel, index, ctx, memo)) != 0) {
            break;
        }
    }
    /* a failed memo (-1) is not asked again: sel_deep_has_anchor walks directly from then on. The walk went on past
       the record's yes, and no walk under the same key runs inside it, so a found position raises yes. */
    sel_deep_run *run = start != 0 ? sel_deep_run_of(memo, key, parent, 0) : NULL;
    if (run != NULL && found) {
        run->yes = position;
    } else if (run != NULL) {
        run->no = start < run->no ? start : run->no;
    }
    return found;
}

/* Whether a later element sibling of node passes sel_deep_has_step at index. In an argument whose :scope reaches the
   elements it tests (sel_complex.scope_reach), an element that many siblings past the anchor or more answers the same
   under every anchor, so the walk asks the shared records from there; the answers before it hold for this anchor
   alone and go under each sibling passed in its own table, so each is found once per anchor. */
static int sel_deep_has_later(th_node *node, const sel_complex *rel, int index, const sel_ctx *ctx,
                              sel_deep_memo *memo) {
    th_node *sibling = sel_next_element_sibling(node);
    if (rel->scope_reach == 0 || sibling == NULL || (rel->sweep_at != 0 && index >= rel->sweep_at)) {
        return sel_deep_has_from(sibling, node->parent, rel, index, ctx, memo);
    }
    const int anchor_at = sel_deep_position(ctx->scope, ctx, memo);
    int at = sel_deep_position(sibling, ctx, memo);
    sel_deep_kept *kept = sel_deep_kept_of(memo);
    /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
    if (anchor_at == 0 || at == 0 || kept == NULL) {
        for (; sibling != NULL; sibling = sel_next_element_sibling(sibling)) {
            const int found = sel_deep_has_step(sibling, rel, index, ctx, memo);
            if (found != 0) {
                return found;
            }
        }
        return 0;
    }
    /* GCOVR_EXCL_STOP */
    const void *key = sel_deep_has_key(rel, index);
    th_node *first = sibling;
    int found = 0;
    int known = 0;
    for (; sibling != NULL && at - anchor_at < rel->scope_reach; sibling = sel_next_element_sibling(sibling), at++) {
        TH_WORK_STEP();
        known = sel_deep_get(sel_deep_scoped(kept, ctx->scope), key, sibling, &found);
        if (known || (found = sel_deep_has_step(sibling, rel, index, ctx, memo)) != 0) {
            break;
        }
    }
    if (!known && found == 0 && sibling != NULL) {
        found = sel_deep_has_from(sibling, node->parent, rel, index, ctx, memo);
    }
    for (th_node *passed = first; passed != sibling; passed = sel_next_element_sibling(passed)) {
        sel_deep_put(sel_deep_scoped(kept, ctx->scope), key, passed, found);
    }
    return found;
}

/* Whether an element that compounds[index]'s combinator relates to node passes sel_deep_has_step at index: the
   compounds from index on, read as a :has() argument of node. */
static int sel_deep_has_rel(th_node *node, const sel_complex *rel, int index, const sel_ctx *ctx, sel_deep_memo *memo) {
    switch (rel->compounds[index].combinator) {
    case '>':
        for (th_node *child = sel_first_element_child(node); child != NULL; child = sel_next_element_sibling(child)) {
            TH_WORK_STEP();
            const int found = sel_deep_has_step(child, rel, index, ctx, memo);
            if (found != 0) {
                return found;
            }
        }
        return 0;
    case '+': {
        th_node *next = sel_next_element_sibling(node);
        return next != NULL ? sel_deep_has_step(next, rel, index, ctx, memo) : 0;
    }
    case '~':
        return sel_deep_has_later(node, rel, index, ctx, memo);
    default: /* descendant; past the anchor every node is deep enough for the memo */
        return sel_deep_has_desc(node, rel, index, ctx, memo, index == 0 ? 0 : SEL_HAS_MEMO_MIN_DESCENT);
    }
}

/* sel_has_subtree through this matcher. */
static int sel_deep_has_subtree(th_node *node, const sel_complex *rel, int subject, th_node *anchor, const sel_ctx *ctx,
                                sel_deep_memo *memo) {
    th_node *current = sel_first_element_child(node);
    while (current != NULL) {
        TH_WORK_STEP();
        if (sel_deep_compound(current, &rel->compounds[subject], ctx, memo) &&
            sel_deep_from(current, rel, subject, anchor, ctx, memo) == SEL_MATCHES) {
            return 1;
        }
        th_node *child = sel_first_element_child(current);
        if (child != NULL) {
            current = child;
            continue;
        }
        for (;;) {
            th_node *sibling = sel_next_element_sibling(current);
            if (sibling != NULL) {
                current = sibling;
                break;
            }
            current = current->parent;
            if (current == node) {
                return 0;
            }
        }
    }
    return 0;
}

/* sel_deep_has_sweep keeps a set of anchor positions as at most this many spans: past it, it gives up on the run */
#define SEL_SWEEP_SPANS 64
/* the (predicates, modulus) pairs its spans may name over one run, each with a count array */
#define SEL_SWEEP_KEYS 8
/* the predicates on the anchor its chains may name (sel_sweep_q), one bit each */
#define SEL_SWEEP_QS 32

/* The anchor positions lo..hi (1-based within the run) that leave rem modulo mod and, when co_mod is not 0, do not
   leave co_rem modulo co_mod, at which the predicates of need hold and those of deny fail. */
typedef struct {
    int lo;
    int hi;
    int mod;
    int rem;
    int co_mod;
    int co_rem;
    unsigned need;
    unsigned deny;
} sel_span;

/* What a chain's left part ending at one compound starts from: the two latest distinct positions its first compound
   can take in a match ending at the current sibling (first), and in one ending at or before it (prefix); 0 for none. */
typedef struct {
    int first[2];
    int prefix[2];
} sel_sweep_cell;

/* A chain the sweep reads at every sibling: from scope (the compound pinned to the anchor, -1 for the anchor ahead of
   a :has() argument) through the off compounds joined to it by +, and on through compounds from..to-1. point: the +
   run reaches the last compound, so the anchor sits off places back. not_scope: compound 0 holds :not(:scope) and
   the chain matches unless its first compound can only be the anchor. any_last: the last compound is tested apart. */
typedef struct {
    const sel_complex *complex;
    int scope;
    int from;
    int to;
    int off;
    int point;
    int not_scope;
    int any_last;
    int q;
    sel_sweep_cell *cells;
} sel_sweep_chain;

/* A predicate on the anchor: the compound scope at it (with its left part), and the block compounds right of it at
   the siblings after it. */
typedef struct {
    const sel_complex *complex;
    int scope;
    int block;
} sel_sweep_q;

typedef struct {
    unsigned need;
    unsigned deny;
    int mod;
    int owner; /* the first key of the same predicates, which sums them all */
    int *counts;
} sel_sweep_key;

/* sel_deep_has_sweep's plan marks, one per simple of a compound that writes :scope and one per alternative of a list
   that does, read back in the order the plan wrote them */
#define SEL_SWEEP_STATIC 0
#define SEL_SWEEP_SCOPE 1
#define SEL_SWEEP_NTH 2
#define SEL_SWEEP_LIST 3
#define SEL_SWEEP_CHAIN 4 /* plus the chain's index */

typedef struct {
    const sel_ctx *ctx;
    sel_deep_memo *memo;
    th_node **nodes; /* the run, nodes[1..n] */
    int n;
    sel_span *pool;
    int used;
    int cap;
    int *marks;
    int mark_count;
    int mark_cap;
    int cursor;
    sel_sweep_chain *chains;
    int chain_count;
    sel_sweep_q qs[SEL_SWEEP_QS];
    int q_count;
    sel_sweep_key keys[SEL_SWEEP_KEYS];
    int key_count;
    int failed;
} sel_sweep;

static int64_t sel_sweep_mod(int64_t value, int64_t mod) {
    const int64_t rem = value % mod;
    return rem < 0 ? rem + mod : rem;
}

/* The least value from lo on that leaves rem modulo mod. */
static int64_t sel_sweep_align(int64_t lo, int64_t mod, int64_t rem) {
    return lo + sel_sweep_mod(rem - lo, mod);
}

/* The values that leave rem1 modulo mod1 and rem2 modulo mod2, as *rem modulo *mod; 0 when none does. The moduli stay
   below 2^31, so every product fits. */
static int sel_sweep_crt(int64_t mod1, int64_t rem1, int64_t mod2, int64_t rem2, int64_t *mod, int64_t *rem) {
    int64_t gcd = mod1;
    int64_t other = mod2;
    int64_t inverse = 1;
    int64_t next = 0;
    while (other != 0) {
        const int64_t quotient = gcd / other;
        const int64_t rest = gcd - quotient * other;
        gcd = other;
        other = rest;
        const int64_t step = inverse - quotient * next;
        inverse = next;
        next = step;
    }
    if ((rem2 - rem1) % gcd != 0) {
        return 0;
    }
    const int64_t period = mod2 / gcd;
    const int64_t times =
        sel_sweep_mod(sel_sweep_mod((rem2 - rem1) / gcd, period) * sel_sweep_mod(inverse, period), period);
    *mod = mod1 / gcd * mod2;
    *rem = sel_sweep_mod(rem1 + mod1 * times, *mod);
    return 1;
}

static void sel_sweep_push(sel_sweep *sweep, int start, const sel_span *span) {
    if (sweep->used - start >= SEL_SWEEP_SPANS) {
        sweep->failed = 1;
        return;
    }
    if (sweep->used == sweep->cap) {
        const size_t cap = sweep->cap == 0 ? SEL_SWEEP_SPANS : 2 * (size_t)sweep->cap;
        /* GCOVR_EXCL_START: needs a 32-bit build and over 134 million spans in one sweep */
        if (cap > INT_MAX || cap > SIZE_MAX / sizeof(sel_span)) {
            sweep->failed = 1;
            return;
        }
        /* GCOVR_EXCL_STOP */
        sel_span *pool = PyMem_Realloc(sweep->pool, cap * sizeof(sel_span));
        if (pool == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            sweep->failed = 1; /* GCOVR_EXCL_LINE: allocation failure */
            return;            /* GCOVR_EXCL_LINE: allocation failure */
        }
        sweep->pool = pool;
        sweep->cap = (int)cap;
    }
    sweep->pool[sweep->used++] = *span;
}

/* Adds to the set from start the anchors lo..hi that leave rem modulo mod and none of the co_count residues of cos,
   under need and deny: clipped to the first anchor (every caller ends hi within the run), a progression with at most
   one anchor in range made that anchor, and a residue it excludes kept as the span's co_mod when it fits, split off
   when it leaves one anchor, and spelled out as the progression's other residues when a second one comes. */
static void sel_sweep_settle(sel_sweep *sweep, int start, int64_t lo, int64_t hi, int64_t mod, int64_t rem,
                             const int64_t (*cos)[2], int co_count, unsigned need, unsigned deny) {
    lo = lo < 1 ? 1 : lo;
    if (lo > hi) {
        return;
    }
    rem = sel_sweep_mod(rem, mod);
    if (mod > hi - lo) {
        lo = sel_sweep_align(lo, mod, rem);
        if (lo > hi) {
            return;
        }
        hi = lo;
        mod = 1;
        rem = 0;
    }
    int64_t kept[2][2];
    int kept_count = 0;
    for (int co = 0; co < co_count; co++) {
        int64_t both;
        int64_t at;
        if (!sel_sweep_crt(mod, rem, cos[co][0], cos[co][1], &both, &at)) {
            continue;
        }
        if (both == mod) {
            return;
        }
        if (both > hi - lo) {
            const int64_t only = sel_sweep_align(lo, both, at);
            if (only <= hi) {
                sel_sweep_settle(sweep, start, lo, only - 1, mod, rem, cos + co + 1, co_count - co - 1, need, deny);
                sel_sweep_settle(sweep, start, only + 1, hi, mod, rem, cos + co + 1, co_count - co - 1, need, deny);
                return;
            }
            continue;
        }
        if (kept_count == 1 && kept[0][0] == both && kept[0][1] == at) {
            continue;
        }
        if (kept_count == 1) {
            /* a progression leaving both of two residues out: the other residues of the second's modulus */
            if (both / mod - 1 > SEL_SWEEP_SPANS) {
                sweep->failed = 1;
                return;
            }
            for (int64_t residue = rem; residue < both; residue += mod) {
                if (residue != at) {
                    sel_sweep_settle(sweep, start, lo, hi, both, residue, kept, 1, need, deny);
                }
            }
            return;
        }
        kept[0][0] = both;
        kept[0][1] = at;
        kept_count = 1;
    }
    const sel_span span = {.lo = (int)lo,
                           .hi = (int)hi,
                           .mod = (int)mod,
                           .rem = (int)rem,
                           .co_mod = kept_count ? (int)kept[0][0] : 0,
                           .co_rem = kept_count ? (int)kept[0][1] : 0,
                           .need = need,
                           .deny = deny};
    sel_sweep_push(sweep, start, &span);
}

static void sel_sweep_range(sel_sweep *sweep, int start, int64_t lo, int64_t hi, int64_t mod, int64_t rem,
                            unsigned need, unsigned deny) {
    sel_sweep_settle(sweep, start, lo, hi, mod, rem, NULL, 0, need, deny);
}

/* Replaces the sets [a, b) and [b, used) with their intersection at a. */
static void sel_sweep_meet(sel_sweep *sweep, int a, int b) {
    const int c = sweep->used;
    for (int left = a; left < b; left++) {
        for (int right = b; right < c; right++) {
            const sel_span p = sweep->pool[left];
            const sel_span q = sweep->pool[right];
            int64_t mod;
            int64_t rem;
            if (!sel_sweep_crt(p.mod, p.rem, q.mod, q.rem, &mod, &rem)) {
                continue;
            }
            int64_t cos[2][2];
            int co_count = 0;
            if (p.co_mod != 0) {
                cos[co_count][0] = p.co_mod;
                cos[co_count++][1] = p.co_rem;
            }
            if (q.co_mod != 0) {
                cos[co_count][0] = q.co_mod;
                cos[co_count++][1] = q.co_rem;
            }
            sel_sweep_settle(sweep, c, p.lo > q.lo ? p.lo : q.lo, p.hi < q.hi ? p.hi : q.hi, mod, rem, cos, co_count,
                             p.need | q.need, p.deny | q.deny);
        }
    }
    memmove(sweep->pool + a, sweep->pool + c, (size_t)(sweep->used - c) * sizeof(sel_span));
    sweep->used = a + (sweep->used - c);
}

/* Replaces the set from start with its complement within the run. */
static void sel_sweep_negate(sel_sweep *sweep, int start) {
    const int end = sweep->used;
    sel_sweep_range(sweep, end, 1, sweep->n, 1, 0, 0, 0);
    for (int index = start; index < end; index++) {
        const sel_span span = sweep->pool[index];
        const int pieces = sweep->used;
        sel_sweep_range(sweep, pieces, 1, span.lo - 1, 1, 0, 0, 0);
        sel_sweep_range(sweep, pieces, span.hi + 1, sweep->n, 1, 0, 0, 0);
        if (span.mod > 1) {
            const int64_t co[1][2] = {{span.mod, span.rem}};
            sel_sweep_settle(sweep, pieces, 1, sweep->n, 1, 0, co, 1, 0, 0);
        }
        if (span.co_mod != 0) {
            sel_sweep_range(sweep, pieces, 1, sweep->n, span.co_mod, span.co_rem, 0, 0);
        }
        for (int bit = 0; bit < SEL_SWEEP_QS; bit++) {
            if (span.need & (1u << bit)) {
                sel_sweep_range(sweep, pieces, 1, sweep->n, 1, 0, 0, 1u << bit);
            }
            if (span.deny & (1u << bit)) {
                sel_sweep_range(sweep, pieces, 1, sweep->n, 1, 0, 1u << bit, 0);
            }
        }
        sel_sweep_meet(sweep, end, pieces);
    }
    memmove(sweep->pool + start, sweep->pool + end, (size_t)(sweep->used - end) * sizeof(sel_span));
    sweep->used = start + (sweep->used - end);
}

static void sel_sweep_mark(sel_sweep *sweep, int mark) {
    if (sweep->mark_count == sweep->mark_cap) {
        const size_t cap = sweep->mark_cap == 0 ? 64 : 2 * (size_t)sweep->mark_cap;
        /* GCOVR_EXCL_START: needs a 32-bit build and over 536 million marks in one sweep */
        if (cap > INT_MAX || cap > SIZE_MAX / sizeof(int)) {
            sweep->failed = 1;
            return;
        }
        /* GCOVR_EXCL_STOP */
        int *marks = PyMem_Realloc(sweep->marks, cap * sizeof(int));
        if (marks == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            sweep->failed = 1; /* GCOVR_EXCL_LINE: allocation failure */
            return;            /* GCOVR_EXCL_LINE: allocation failure */
        }
        sweep->marks = marks;
        sweep->mark_cap = (int)cap;
    }
    sweep->marks[sweep->mark_count++] = mark;
}

/* Plans a chain of complex up to compound to: scope is the compound pinned to the anchor (-1 for none), or
   SEL_SWEEP_NOT_SCOPE. */
static void sel_sweep_plan_chain(sel_sweep *sweep, const sel_complex *complex, int scope, int to, int any_last) {
    /* GCOVR_EXCL_START: needs a 32-bit build and over 107 million chains in one sweep */
    sel_sweep_chain *chains =
        TH_UNLIKELY((size_t)sweep->chain_count + 1 > SIZE_MAX / sizeof(sel_sweep_chain))
            ? NULL
            : PyMem_Realloc(sweep->chains, (size_t)(sweep->chain_count + 1) * sizeof(sel_sweep_chain));
    /* GCOVR_EXCL_STOP */
    if (chains == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        sweep->failed = 1; /* GCOVR_EXCL_LINE: allocation failure */
        return;            /* GCOVR_EXCL_LINE: allocation failure */
    }
    sweep->chains = chains;
    sel_sweep_chain *chain = &chains[sweep->chain_count++];
    *chain = (sel_sweep_chain){.complex = complex, .scope = scope, .to = to, .any_last = any_last, .q = -1};
    if (scope == SEL_SWEEP_NOT_SCOPE) {
        chain->scope = -1;
        chain->not_scope = 1;
    } else {
        while (scope + 1 + chain->off < to && complex->compounds[scope + 1 + chain->off].combinator == '+') {
            chain->off++;
        }
        chain->point = scope + chain->off == to - 1;
        chain->from = scope + 1 + chain->off;
        const int block = chain->point && any_last ? chain->off - 1 : chain->off;
        if (block > 0 || scope > 0 || (scope == 0 && complex->compounds[0].count > 1)) {
            if (sweep->q_count == SEL_SWEEP_QS) {
                sweep->failed = 1;
                return;
            }
            sweep->qs[sweep->q_count] = (sel_sweep_q){complex, scope, block};
            chain->q = sweep->q_count++;
        }
    }
    if (chain->from < chain->to) {
        chain->cells = PyMem_Calloc((size_t)(chain->to - chain->from), sizeof(sel_sweep_cell));
        sweep->failed |= chain->cells == NULL;
    }
}

static void sel_sweep_plan_alts(sel_sweep *sweep, const sel_complex *alts, int count);

static void sel_sweep_plan_compound(sel_sweep *sweep, const sel_compound *compound) {
    for (int at = 0; at < compound->count; at++) {
        const sel_simple *simple = &compound->simples[at];
        if (!sel_simple_writes_scope(simple)) {
            sel_sweep_mark(sweep, SEL_SWEEP_STATIC);
        } else if (simple->pseudo == PSEUDO_SCOPE) {
            sel_sweep_mark(sweep, SEL_SWEEP_SCOPE);
        } else if (simple->pseudo == PSEUDO_NTH_CHILD || simple->pseudo == PSEUDO_NTH_LAST_CHILD) {
            sel_sweep_mark(sweep, SEL_SWEEP_NTH);
        } else {
            sel_sweep_mark(sweep, SEL_SWEEP_LIST);
            sel_sweep_plan_alts(sweep, simple->sub, simple->sub_count);
        }
    }
}

static void sel_sweep_plan_alts(sel_sweep *sweep, const sel_complex *alts, int count) {
    for (int alt = 0; alt < count; alt++) {
        if (!sel_alts_write_scope(&alts[alt], 1)) {
            sel_sweep_mark(sweep, SEL_SWEEP_STATIC);
        } else if (alts[alt].count == 1) {
            sel_sweep_mark(sweep, SEL_SWEEP_LIST);
            sel_sweep_plan_compound(sweep, &alts[alt].compounds[0]);
        } else {
            sel_sweep_mark(sweep, SEL_SWEEP_CHAIN + sweep->chain_count);
            sel_sweep_plan_chain(sweep, &alts[alt], sel_sweep_chain_kind(&alts[alt]), alts[alt].count, 0);
        }
    }
}

/* Whether node matches compound, leaving out a :not(:scope) when skip. */
static int sel_sweep_matches(sel_sweep *sweep, th_node *node, const sel_compound *compound, int skip) {
    for (int at = 0; at < compound->count; at++) {
        const sel_simple *simple = &compound->simples[at];
        if (!(skip && sel_simple_not_scope(simple)) && !sel_deep_simple(node, simple, sweep->ctx, sweep->memo)) {
            return 0;
        }
    }
    return 1;
}

/* Moves every chain's cells on to the sibling at position. */
static void sel_sweep_advance(sel_sweep *sweep, int position) {
    th_node *node = sweep->nodes[position];
    for (int index = 0; index < sweep->chain_count; index++) {
        const sel_sweep_chain *chain = &sweep->chains[index];
        for (int at = chain->to - 1; at >= chain->from; at--) {
            sel_sweep_cell *cell = &chain->cells[at - chain->from];
            int first[2] = {0, 0};
            if ((chain->any_last && at == chain->to - 1) ||
                sel_sweep_matches(sweep, node, &chain->complex->compounds[at], chain->not_scope && at == 0)) {
                if (at == chain->from) {
                    first[0] = position;
                } else {
                    const sel_sweep_cell *left = cell - 1;
                    const int *from = chain->complex->compounds[at].combinator == '+' ? left->first : left->prefix;
                    first[0] = from[0];
                    first[1] = from[1];
                }
            }
            cell->first[0] = first[0];
            cell->first[1] = first[1];
            /* a cell's starts never fall from one sibling to the next, and a match that keeps the latest start keeps
               the one before it too, so the newest pair leads the prefix */
            if (first[0] > cell->prefix[0]) {
                cell->prefix[1] = first[1] > cell->prefix[0] ? first[1] : cell->prefix[0];
                cell->prefix[0] = first[0];
            }
        }
    }
}

/* Adds the anchors under which chain matches at the sibling at position. */
static void sel_sweep_chain_spans(sel_sweep *sweep, int start, const sel_sweep_chain *chain, int position) {
    const unsigned need = chain->q >= 0 ? 1u << chain->q : 0;
    if (chain->point) {
        sel_sweep_range(sweep, start, position - chain->off, position - chain->off, 1, 0, need, 0);
        return;
    }
    const int *first = chain->cells[chain->to - 1 - chain->from].first;
    if (chain->not_scope) {
        if (first[1] != 0) {
            sel_sweep_range(sweep, start, 1, sweep->n, 1, 0, 0, 0);
        } else if (first[0] != 0) {
            sel_sweep_range(sweep, start, 1, first[0] - 1, 1, 0, 0, 0);
            sel_sweep_range(sweep, start, first[0] + 1, sweep->n, 1, 0, 0, 0);
        }
        return;
    }
    if (first[0] != 0) {
        sel_sweep_range(sweep, start, 1, (int64_t)first[0] - chain->off - 1, 1, 0, need, 0);
    }
}

/* Adds the anchors under which the sibling at position matches an :nth-child() or :nth-last-child() whose S
   (sel_sweep_offsets) holds at the siblings whose distance from the anchor is one of the exact ones, or tail or more.
 */
static void sel_sweep_nth(sel_sweep *sweep, int start, const sel_simple *simple, int position) {
    int exact[SEL_SWEEP_SPANS];
    int exact_count = 0;
    int tail = INT_MAX;
    for (int alt = 0; alt < simple->sub_count; alt++) {
        const sel_complex *complex = &simple->sub[alt];
        int fixed = 1;
        for (int index = 1; index < complex->count; index++) {
            fixed &= complex->compounds[index].combinator == '+';
        }
        if (!fixed) {
            tail = complex->count - 1 < tail ? complex->count - 1 : tail;
        }
    }
    for (int alt = 0; alt < simple->sub_count; alt++) {
        const sel_complex *complex = &simple->sub[alt];
        int fixed = 1;
        for (int index = 1; index < complex->count; index++) {
            fixed &= complex->compounds[index].combinator == '+';
        }
        const int distance = complex->count - 1;
        if (!fixed || distance >= tail) {
            continue;
        }
        int at = exact_count;
        while (at > 0 && exact[at - 1] > distance) {
            at--;
        }
        if (at > 0 && exact[at - 1] == distance) {
            continue;
        }
        if (exact_count == SEL_SWEEP_SPANS) {
            sweep->failed = 1;
            return;
        }
        memmove(exact + at + 1, exact + at, (size_t)(exact_count - at) * sizeof(int));
        exact[at] = distance;
        exact_count++;
    }
    const int64_t a = simple->nth_a;
    const int64_t b = simple->nth_b;
    if (simple->pseudo == PSEUDO_NTH_CHILD) {
        for (int index = 0; index < exact_count; index++) {
            if (sel_nth_matches(simple->nth_a, simple->nth_b, index + 1)) {
                sel_sweep_range(sweep, start, position - exact[index], position - exact[index], 1, 0, 0, 0);
            }
        }
        if (tail == INT_MAX) {
            return;
        }
        /* from tail on the index runs exact_count + 1, exact_count + 2, ...: the anchor sits base - index back */
        const int64_t least = exact_count + 1;
        const int64_t base = (int64_t)position - tail + 1 + exact_count;
        if (a > 0) {
            sel_sweep_range(sweep, start, 1, base - sel_sweep_align(least > b ? least : b, a, b), a, base - b, 0, 0);
        } else if (a == 0) {
            if (b >= least) {
                sel_sweep_range(sweep, start, base - b, base - b, 1, 0, 0, 0);
            }
        } else {
            const int64_t low = sel_sweep_align(least, -a, b);
            if (low <= b) {
                sel_sweep_range(sweep, start, base - b, base - low, -a, base - b, 0, 0);
            }
        }
        return;
    }
    const int after = sweep->n - position;
    for (int index = 0; index < exact_count; index++) {
        int count = 0;
        for (int other = index; other < exact_count && exact[other] <= (int64_t)exact[index] + after; other++) {
            count++;
        }
        if (tail != INT_MAX && (int64_t)exact[index] + after >= tail) {
            count += exact[index] + after - tail + 1;
        }
        if (sel_nth_matches(simple->nth_a, simple->nth_b, count)) {
            sel_sweep_range(sweep, start, position - exact[index], position - exact[index], 1, 0, 0, 0);
        }
    }
    if (tail != INT_MAX && sel_nth_matches(simple->nth_a, simple->nth_b, after + 1)) {
        sel_sweep_range(sweep, start, 1, (int64_t)position - tail, 1, 0, 0, 0);
    }
}

static void sel_sweep_alts(sel_sweep *sweep, th_node *node, int position, const sel_complex *alts, int count);

/* Adds the anchors under which node, the sibling at position, matches compound. */
static void sel_sweep_compound(sel_sweep *sweep, th_node *node, int position, const sel_compound *compound) {
    const int start = sweep->used;
    sel_sweep_range(sweep, start, 1, sweep->n, 1, 0, 0, 0);
    for (int at = 0; at < compound->count; at++) {
        const sel_simple *simple = &compound->simples[at];
        const int mark = sweep->marks[sweep->cursor++];
        const int mid = sweep->used;
        if (mark == SEL_SWEEP_STATIC) {
            if (mid > start && !sel_deep_simple(node, simple, sweep->ctx, sweep->memo)) {
                sweep->used = start;
            }
            continue;
        }
        if (mark == SEL_SWEEP_SCOPE) {
            sel_sweep_range(sweep, mid, position, position, 1, 0, 0, 0);
        } else if (mark == SEL_SWEEP_NTH) {
            sel_sweep_nth(sweep, mid, simple, position);
        } else {
            sel_sweep_alts(sweep, node, position, simple->sub, simple->sub_count);
            if (simple->pseudo == PSEUDO_NOT) {
                sel_sweep_negate(sweep, mid);
            }
        }
        sel_sweep_meet(sweep, start, mid);
    }
}

static void sel_sweep_alts(sel_sweep *sweep, th_node *node, int position, const sel_complex *alts, int count) {
    const int start = sweep->used;
    for (int alt = 0; alt < count; alt++) {
        const int mark = sweep->marks[sweep->cursor++];
        if (mark == SEL_SWEEP_STATIC) {
            if (sel_deep_matches_alt(node, &alts[alt], sweep->ctx, sweep->memo)) {
                sel_sweep_range(sweep, start, 1, sweep->n, 1, 0, 0, 0);
            }
        } else if (mark == SEL_SWEEP_LIST) {
            const int mid = sweep->used;
            sel_sweep_compound(sweep, node, position, &alts[alt].compounds[0]);
            if (sweep->used - start > SEL_SWEEP_SPANS) {
                sweep->failed = 1;
                sweep->used = mid;
            }
        } else {
            sel_sweep_chain_spans(sweep, start, &sweep->chains[mark - SEL_SWEEP_CHAIN], position);
        }
    }
}

/* Counts the anchors of span into the count arrays (sel_sweep_key) of its predicates and modulus, weight times. */
static void sel_sweep_count(sel_sweep *sweep, unsigned need, unsigned deny, int lo, int hi, int mod, int rem,
                            int weight) {
    /* sel_sweep_settle leaves every span and co residue at least one anchor */
    const int first = (int)sel_sweep_align(lo, mod, rem);
    const int last = hi - (int)sel_sweep_mod((int64_t)hi - rem, mod);
    mod = first == last ? 1 : mod;
    int index = 0;
    int owner = -1;
    for (; index < sweep->key_count; index++) {
        const sel_sweep_key *other = &sweep->keys[index];
        if (other->need == need && other->deny == deny) {
            owner = owner < 0 ? index : owner;
            if (other->mod == mod) {
                break;
            }
        }
    }
    if (index == sweep->key_count) {
        if (index == SEL_SWEEP_KEYS) {
            sweep->failed = 1;
            return;
        }
        int *counts = PyMem_Calloc((size_t)sweep->n + 2, sizeof(int));
        if (counts == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            sweep->failed = 1; /* GCOVR_EXCL_LINE: allocation failure */
            return;            /* GCOVR_EXCL_LINE: allocation failure */
        }
        sweep->keys[sweep->key_count++] = (sel_sweep_key){need, deny, mod, owner < 0 ? index : owner, counts};
    }
    int *counts = sweep->keys[index].counts;
    counts[first] += weight;
    if ((int64_t)last + mod <= sweep->n) {
        counts[last + mod] -= weight;
    }
}

/* Whether the predicate q holds with the anchor at position. */
static int sel_sweep_q_holds(sel_sweep *sweep, const sel_sweep_q *q, int position) {
    const sel_compound *compounds = q->complex->compounds;
    if (q->scope >= 0) {
        th_node *node = sweep->nodes[position];
        for (int at = 0; at < compounds[q->scope].count; at++) {
            const sel_simple *simple = &compounds[q->scope].simples[at];
            if (!(simple->kind == ':' && simple->pseudo == PSEUDO_SCOPE) &&
                !sel_deep_simple(node, simple, sweep->ctx, sweep->memo)) {
                return 0;
            }
        }
        if (q->scope > 0 && sel_deep_from(node, q->complex, q->scope, NULL, sweep->ctx, sweep->memo) != SEL_MATCHES) {
            return 0;
        }
    }
    for (int at = 1; at <= q->block; at++) {
        /* the spans that name q leave room for its block before the sibling they were read at */
        if (!sel_deep_compound(sweep->nodes[position + at], &compounds[q->scope + at], sweep->ctx, sweep->memo)) {
            return 0;
        }
    }
    return 1;
}

static void sel_sweep_free(sel_sweep *sweep) {
    for (int index = 0; index < sweep->chain_count; index++) {
        PyMem_Free(sweep->chains[index].cells);
    }
    for (int index = 0; index < sweep->key_count; index++) {
        PyMem_Free(sweep->keys[index].counts);
    }
    PyMem_Free(sweep->chains);
    PyMem_Free(sweep->marks);
    PyMem_Free(sweep->pool);
    PyMem_Free(sweep->nodes);
}

/* Reads rel (sel_complex.sweep_at) at every element sibling of parent, and keeps its answer for every one of them as
   the anchor under key: the anchors each sibling matches for form a few spans of positions (sel_span), counted into
   arrays and summed once, so the run costs time linear in its length rather than in its square. Returns anchor's
   answer, or -1 when it gave up. */
static int sel_sweep_run(sel_sweep *sweep, th_node *anchor, const sel_complex *rel, const void *key) {
    th_node *parent = anchor->parent;
    for (th_node *child = sel_first_element_child(parent); child != NULL; child = sel_next_element_sibling(child)) {
        sweep->n++;
    }
    /* GCOVR_EXCL_START: needs a 32-bit build and a parent with over 536 million element children */
    sweep->nodes = TH_UNLIKELY((size_t)sweep->n + 1 > SIZE_MAX / sizeof(th_node *))
                       ? NULL
                       : PyMem_Malloc(((size_t)sweep->n + 1) * sizeof(th_node *));
    /* GCOVR_EXCL_STOP */
    if (sweep->nodes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;              /* GCOVR_EXCL_LINE: allocation failure */
    }
    int position = 0;
    for (th_node *child = sel_first_element_child(parent); child != NULL; child = sel_next_element_sibling(child)) {
        sweep->nodes[++position] = child;
    }
    const int dependent = rel->sweep_at - 1;
    const int pinned = rel->count > 1 && sel_compound_has_scope(&rel->compounds[0]);
    sel_sweep_plan_chain(sweep, rel, pinned ? 0 : -1, dependent + 1, 1);
    sel_sweep_plan_compound(sweep, &rel->compounds[dependent]);
    for (position = 1; position <= sweep->n && !sweep->failed; position++) {
        TH_WORK_STEP();
        sel_sweep_advance(sweep, position);
        th_node *node = sweep->nodes[position];
        const int after =
            dependent + 1 == rel->count ? 1 : sel_deep_has_rel(node, rel, dependent + 1, sweep->ctx, sweep->memo);
        /* a memo that failed (-1) leaves the rest of the run unknown */
        sweep->failed = after < 0;
        if (after <= 0) {
            continue;
        }
        sweep->used = 0;
        sweep->cursor = 0;
        sel_sweep_chain_spans(sweep, 0, &sweep->chains[0], position);
        const int mid = sweep->used;
        sel_sweep_compound(sweep, node, position, &rel->compounds[dependent]);
        sel_sweep_meet(sweep, 0, mid);
        for (int index = 0; index < sweep->used; index++) {
            const sel_span *span = &sweep->pool[index];
            sel_sweep_count(sweep, span->need, span->deny, span->lo, span->hi, span->mod, span->rem, 1);
            if (span->co_mod != 0) {
                sel_sweep_count(sweep, span->need, span->deny, span->lo, span->hi, span->co_mod, span->co_rem, -1);
            }
        }
    }
    if (sweep->failed) {
        return -1;
    }
    for (int index = 0; index < sweep->key_count; index++) {
        int *counts = sweep->keys[index].counts;
        for (position = 1 + sweep->keys[index].mod; position <= sweep->n; position++) {
            counts[position] += counts[position - sweep->keys[index].mod];
        }
    }
    for (int index = 0; index < sweep->key_count; index++) {
        const sel_sweep_key *part = &sweep->keys[index];
        if (part->owner != index) {
            for (position = 1; position <= sweep->n; position++) {
                sweep->keys[part->owner].counts[position] += part->counts[position];
            }
        }
    }
    int answer = 0;
    for (position = 1; position <= sweep->n; position++) {
        int found = 0;
        for (int index = 0; index < sweep->key_count && !found; index++) {
            const sel_sweep_key *owner = &sweep->keys[index];
            if (owner->owner != index || owner->counts[position] <= 0) {
                continue;
            }
            found = 1;
            for (int bit = 0; bit < sweep->q_count && found; bit++) {
                if ((owner->need | owner->deny) & (1u << bit)) {
                    found = sel_sweep_q_holds(sweep, &sweep->qs[bit], position) == (int)((owner->need >> bit) & 1u);
                }
            }
        }
        sel_deep_put(sweep->memo, key, sweep->nodes[position], found);
        answer = sweep->nodes[position] == anchor ? found : answer;
    }
    return answer;
}

/* Whether a later element sibling of anchor matches rel, an argument whose :scope sel_complex.sweep_at reads: the
   first anchor of a long sibling run sweeps the run (sel_sweep_run) and the memo keeps every anchor's answer. A
   short run, or one the sweep gave up on, takes the direct walk (-1). */
static int sel_deep_has_sweep(th_node *anchor, const sel_complex *rel, const sel_ctx *ctx, sel_deep_memo *memo) {
    const void *key = &rel->sweep_at;
    int found = 0;
    if (sel_deep_get(memo, key, anchor, &found)) {
        return found;
    }
    int later = 0;
    for (th_node *sibling = sel_next_element_sibling(anchor); sibling != NULL && later < SEL_SIBLING_MEMO_MIN_RUN;
         sibling = sel_next_element_sibling(sibling)) {
        later++;
    }
    if (later < SEL_SIBLING_MEMO_MIN_RUN || sel_deep_get(memo, &rel->scope_reach, anchor->parent, &found)) {
        return -1;
    }
    sel_sweep sweep = {.ctx = ctx, .memo = memo};
    found = sel_sweep_run(&sweep, anchor, rel, key);
    sel_sweep_free(&sweep);
    if (found < 0) {
        sel_deep_put(memo, &rel->scope_reach, anchor->parent, 1);
    }
    return found;
}

/* Whether rel, a :has() argument whose leftmost combinator past a pinning :scope is lead, holds for anchor, walking
   every element it may reach without a memo. Its cost grows with the elements past the anchor, so a page whose rules
   walk this way from every element of a long run takes time quadratic in the run. So sel_deep_compound charges each
   compound tested while one runs, those of its nested walks included, to memo's allowance. */
static int sel_deep_has_direct(th_node *anchor, const sel_complex *rel, char lead, const sel_ctx *ctx,
                               sel_deep_memo *memo) {
    const int subject = rel->count - 1;
    if (lead != '+' && lead != '~') {
        return sel_deep_has_subtree(anchor, rel, subject, anchor, ctx, memo);
    }
    for (th_node *sibling = sel_next_element_sibling(anchor); sibling != NULL && !memo->over;
         sibling = sel_next_element_sibling(sibling)) {
        TH_WORK_STEP();
        if ((sel_deep_compound(sibling, &rel->compounds[subject], ctx, memo) &&
             sel_deep_from(sibling, rel, subject, anchor, ctx, memo) == SEL_MATCHES) ||
            sel_deep_has_subtree(sibling, rel, subject, anchor, ctx, memo)) {
            return 1;
        }
    }
    return 0;
}

/* sel_has_match through this matcher. An argument whose :scope never meets the anchor (sel_alts_scope_reaches) reads
   left to right, its answers facts of the tree the memo keeps for every anchor; a :scope that pins the leftmost
   compound to the anchor leaves the rest to read that way. One whose :scope sel_deep_has_sweep reads answers for every
   anchor of a long sibling run at once. Any other argument, or one that meets a memo that failed, takes the direct
   walk from the anchor. */
static int sel_deep_has_anchor(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx,
                               sel_deep_memo *memo) {
    if (memo->over) {
        return 0;
    }
    sel_ctx scoped = *ctx;
    scoped.scope = anchor;
    scoped.sibling_memo = NULL;
    for (int index = 0; index < count; index++) {
        const sel_complex *rel = &alts[index];
        const int pinned = rel->count > 1 && sel_compound_has_scope(&rel->compounds[0]);
        const int shared = pinned ? !sel_alts_scope_reaches(
                                        &(const sel_complex){rel->compounds + pinned, rel->count - pinned, 0, 0, 0}, 1)
                                  : rel->scope_reach < SEL_SCOPE_REACH_ANY;
        if (!memo->failed && !shared && rel->sweep_at != 0) {
            const int found = sel_deep_has_sweep(anchor, rel, &scoped, memo);
            if (found > 0) {
                return 1;
            }
            if (found == 0) {
                continue;
            }
        }
        if (!memo->failed && shared) {
            int found = 0;
            if (!pinned) {
                found = sel_deep_has_rel(anchor, rel, 0, &scoped, memo);
            } else if (sel_deep_compound(anchor, &rel->compounds[0], &scoped, memo)) {
                found = sel_deep_has_rel(anchor, rel, 1, &scoped, memo);
            }
            if (found > 0) {
                return 1;
            }
            if (found == 0) {
                continue;
            }
        }
        memo->walking++;
        const int found = sel_deep_has_direct(anchor, rel, rel->compounds[pinned].combinator, &scoped, memo);
        memo->walking--;
        if (found) {
            return 1;
        }
    }
    return 0;
}

/* :has() binds :scope to the anchor, so while it runs the answers kept for the enclosing scope are set aside rather
   than emptied. */
static int sel_deep_has(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx, sel_deep_memo *memo) {
    sel_deep_kept *kept = memo->kept;
    if (kept == NULL) {
        return sel_deep_has_anchor(anchor, alts, count, ctx, memo);
    }
    const int subject_scope = kept->subject_scope;
    kept->subject_scope = 0;
    if (kept->scoped.count == 0) {
        const int found = sel_deep_has_anchor(anchor, alts, count, ctx, memo);
        kept->subject_scope = subject_scope;
        return found;
    }
    const sel_deep_memo outer = kept->scoped;
    const th_node *outer_for = kept->scoped_for;
    kept->scoped = (sel_deep_memo){.limit = outer.limit};
    kept->scoped_for = anchor;
    const int found = sel_deep_has_anchor(anchor, alts, count, ctx, memo);
    sel_deep_store_free(&kept->scoped);
    kept->scoped = outer;
    kept->scoped_for = outer_for;
    kept->subject_scope = subject_scope;
    return found;
}

/* PSEUDO_DEEP: the simple is the one of the query's stand-in, which holds the memo. */
static TH_NOINLINE int sel_deep_match(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    sel_deep_query *deep = (sel_deep_query *)((const char *)simple - offsetof(sel_deep_query, simple));
    return sel_deep_alts(node, simple->sub, simple->sub_count, ctx, &deep->memo);
}

/* PSEUDO_DEEP_LISTS: PSEUDO_DEEP for a selector that keeps the answers of nested lists (SEL_CLIMBS_LISTS), which
   tell apart the answers that hold under any scope by whether the subject is the scope. */
static TH_NOINLINE int sel_deep_match_lists(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    sel_deep_query *deep = (sel_deep_query *)((const char *)simple - offsetof(sel_deep_query, simple));
    sel_deep_kept *kept = sel_deep_kept_of(&deep->memo);
    if (kept != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        kept->subject_scope = node == ctx->scope;
    }
    return sel_deep_alts(node, simple->sub, simple->sub_count, ctx, &deep->memo);
}

/* Out of line, so a query that frees a stand-in keeps the size it had before the stand-in kept more tables. */
TH_NOINLINE void sel_deep_memo_free(sel_deep_memo *memo) {
    sel_deep_store_free(memo);
    sel_deep_kept *kept = memo->kept;
    if (kept == NULL) {
        return;
    }
    sel_deep_store_free(&kept->lists);
    sel_deep_store_free(&kept->scoped);
    for (int slot = 0; slot < kept->count_slots; slot++) {
        if (kept->counts[slot] != NULL) {
            PyMem_Free(kept->counts[slot]->map.entries);
            PyMem_Free(kept->counts[slot]);
        }
    }
    PyMem_Free(kept->counts);
    sel_position_memo_free(&kept->positions);
    PyMem_Free(kept);
    memo->kept = NULL;
}

/* The most bytes of pages a query's memo holds: 256 MiB, about 1e9 answers. Past it the matcher walks directly. */
#define SEL_DEEP_MEMO_LIMIT ((size_t)256 << 20)

TH_NOINLINE sel_compiled *sel_deep_wrap(sel_compiled *compiled, sel_deep_query *deep) {
    deep->memo = (sel_deep_memo){
        .limit = SEL_DEEP_MEMO_LIMIT, .failed = th_tree_seq_wrapped(compiled->tree), .allowance = SIZE_MAX};
    deep->simple = (sel_simple){.kind = ':',
                                .pseudo = compiled->climbs == SEL_CLIMBS_LISTS ? PSEUDO_DEEP_LISTS : PSEUDO_DEEP,
                                .sub = compiled->alts,
                                .sub_count = compiled->count};
    deep->compound = (sel_compound){&deep->simple, 1, ' '};
    deep->complex = (sel_complex){&deep->compound, 1, 0, 0, 0};
    deep->compiled = *compiled;
    deep->compiled.alts = &deep->complex;
    deep->compiled.count = 1;
    return &deep->compiled;
}

/* scope is the element :scope matches: the node the query was rooted at. A single
   test builds a throwaway context with no :has() memo (nothing to amortize over), but a
   selector that nests a :has() gets a sibling table, since each nested :has() repeats its
   sibling walks at every level without one. A selector that reads a :disabled-family pseudo-class gets
   a legend memo, since one :has() or sibling walk tests many controls under one fieldset.
   Both tables live for this one test, so scope is fixed for every fact they hold. */
static TH_NOINLINE int sel_matches_with_tables(th_node *node, const sel_compiled *compiled, th_node *scope,
                                               sel_memo *value_memo) {
    sel_memo legend_memo = {0};
    sel_sibling_memo siblings = {0};
    sel_ctx ctx = {compiled->tree,
                   scope,
                   compiled->quirks,
                   NULL,
                   NULL,
                   NULL,
                   compiled->nests_has ? &siblings : NULL,
                   compiled->reads_legend ? &legend_memo : NULL,
                   NULL,
                   value_memo};
    const int matched = sel_matches_alts(node, compiled->alts, compiled->count, &ctx);
    sel_memo_free(&legend_memo);
    sel_memo_free(&siblings.table);
    return matched;
}

/* A single test still gets a table: a :has() walk or the shared values of re-created formatting elements read one
   value many times even then. Out of line, so a caller's loop keeps selector_matches inlined. */
static TH_NOINLINE int selector_matches_once(th_node *node, const sel_compiled *compiled, th_node *scope) {
    sel_memo own = {0};
    const int matched = selector_matches(node, compiled, scope, &own);
    sel_memo_free(&own);
    return matched;
}

/* value_memo is the caller's table for long attribute values, or NULL for one that lives as long as the test. */
int selector_matches(th_node *node, const sel_compiled *compiled, th_node *scope, sel_memo *value_memo) {
    if (value_memo == NULL) {
        return selector_matches_once(node, compiled, scope);
    }
    if (compiled->reads_legend || compiled->nests_has) {
        return sel_matches_with_tables(node, compiled, scope, value_memo);
    }
    sel_ctx ctx = {compiled->tree, scope, compiled->quirks, NULL, NULL, NULL, NULL, NULL, NULL, value_memo};
    return sel_matches_alts(node, compiled->alts, compiled->count, &ctx);
}

/* Out of line, so a single test that needs no stand-in keeps the call it made before. */
TH_NOINLINE int selector_matches_deep(th_node *node, sel_compiled *compiled, th_node *scope) {
    sel_deep_query deep;
    const int matched = selector_matches(node, sel_deep_wrap(compiled, &deep), scope, NULL);
    sel_deep_leave(&deep);
    return matched;
}

/* Match against a caller-owned context, so a driver looping over many candidates can
   share one :has() subtree memo (ctx->has_memo) across the whole walk. */
int selector_matches_c(th_node *node, const sel_compiled *compiled, const sel_ctx *ctx) {
    return sel_matches_alts(node, compiled->alts, compiled->count, ctx);
}

/* Whether a query over this compiled selector should build the :has() subtree memo: the
   selector must contain a :has(), and the tree must not be known to nest less than
   SEL_HAS_MEMO_MIN_DESCENT deep -- only a tree that may nest that deep lets a candidate
   anchor's subtree re-walk (the O(n^2) source) reach far enough to memoize. On a shallow
   parsed page the direct walk already wins, so the driver leaves ctx->has_memo NULL and the
   matcher runs the exact pre-memo path with no added per-node cost. */
int selector_uses_has_memo(const sel_compiled *compiled) {
    return compiled->has_relational && th_tree_nest_bound(compiled->tree) < SEL_HAS_MEMO_MIN_DESCENT;
}

int selector_uses_scope(const sel_compiled *compiled) {
    int uses_scope = 0;
    for (int alt = 0; alt < compiled->count; alt++) {
        uses_scope |= sel_rel_uses_scope(&compiled->alts[alt]);
    }
    return uses_scope;
}

/* The lone simple selector of a selector that is one group, one compound, one
   simple (such as "a", ".x", or "#id"), or NULL otherwise. The caller can then
   test each element with sel_match_simple directly, skipping the group and
   combinator machinery. */
const sel_simple *sel_single_simple(const sel_compiled *compiled) {
    if (compiled->count == 1 && compiled->alts[0].count == 1 && compiled->alts[0].compounds[0].count == 1) {
        return &compiled->alts[0].compounds[0].simples[0];
    }
    return NULL;
}
