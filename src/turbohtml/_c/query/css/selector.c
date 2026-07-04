/* The compiled CSS selector engine. Split out of selector.h (spec-tracked in issue #478):
   the matcher and recursive-descent parser live here as one translation unit, and
   selector.h now carries only the shared types plus the entry-point prototypes. */

#include "query/css/selector.h"

/* Record a parse failure with the position and reason for the error message. Set
   unconditionally: an error propagates up through early returns without another
   failure being recorded, so the first (deepest) call keeps its position. */
static void sel_fail(sel_parser *parser, const char *reason) {
    parser->error = 1;
    parser->err_pos = parser->pos;
    parser->err_reason = reason;
}

int sel_is_ident(Py_UCS4 ch) {
    return is_ascii_alpha(ch) || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' || ch >= 0x80;
}

static int sel_is_hex(Py_UCS4 ch) {
    return (ch >= '0' && ch <= '9') || ((ch | 32) >= 'a' && (ch | 32) <= 'f');
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
    if (!sel_is_hex(parser->src[parser->pos])) {
        return parser->src[parser->pos++]; /* the literal escaped character */
    }
    uint32_t value = 0;
    for (int digit = 0; digit < 6 && parser->pos < parser->len && sel_is_hex(parser->src[parser->pos]); digit++) {
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

/* UTF-8 encode a slice and resolve it to a tag atom. */
static uint16_t sel_tag_atom(const Py_UCS4 *name, Py_ssize_t len) {
    char bytes[64];
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < len && at < (Py_ssize_t)sizeof(bytes) - 4; index++) {
        Py_UCS4 ch = name[index];
        if (ch >= 'A' && ch <= 'Z') {
            ch += 32; /* tag names match case-insensitively */
        }
        if (ch < 0x80) {
            bytes[at++] = (char)ch;
        } else {
            bytes[at++] = '\x01'; /* a non-ASCII byte is never in the tag table */
        }
    }
    return th_tag_lookup(bytes, at);
}

static uint32_t sel_attr_atom(th_tree *tree, const Py_UCS4 *name, Py_ssize_t len) {
    char bytes[128];
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < len && at < (Py_ssize_t)sizeof(bytes) - 4; index++) {
        Py_UCS4 ch = name[index];
        if (ch >= 'A' && ch <= 'Z') {
            ch += 32; /* attribute names are lowercased in the tree */
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
    } else if (sel_attr_default_ci(name, name_len)) {
        simple->ci_default = 1; /* the HTML set defaults to case-insensitive without a flag */
    }
    if (parser->pos >= parser->len || parser->src[parser->pos] != ']') {
        sel_fail(parser, "expected ']'");
        return;
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
        simple->tag_atom = sel_tag_atom(simple->name, simple->name_len);
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

/* Read a run of ASCII digits as a non-negative int; *seen reports any digit. */
static int sel_read_int(sel_parser *parser, int *seen) {
    int value = 0;
    *seen = 0;
    while (parser->pos < parser->len && parser->src[parser->pos] >= '0' && parser->src[parser->pos] <= '9') {
        value = value * 10 + (int)(parser->src[parser->pos] - '0');
        parser->pos++;
        *seen = 1;
    }
    return value;
}

/* Parse the An+B microsyntax (CSS Syntax §"The An+B microsyntax") into a/b,
   including the even/odd keywords. pos starts after '(' and stops at the ')'. */
static void sel_parse_anb(sel_parser *parser, int *a, int *b) {
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
    int num = sel_read_int(parser, &seen_a);
    if (parser->pos < parser->len && (parser->src[parser->pos] | 32) == 'n') {
        parser->pos++;
        *a = sign * (seen_a ? num : 1);
        sel_skip_ws(parser);
        if (parser->pos < parser->len && (parser->src[parser->pos] == '+' || parser->src[parser->pos] == '-')) {
            int bsign = parser->src[parser->pos] == '-' ? -1 : 1;
            parser->pos++;
            sel_skip_ws(parser);
            int seen_b;
            int bval = sel_read_int(parser, &seen_b);
            if (!seen_b) {
                sel_fail(parser, "expected a number after the sign in An+B");
                return;
            }
            *b = bsign * bval;
        } else {
            *b = 0;
        }
    } else if (seen_a) {
        *a = 0;
        *b = sign * num;
    } else {
        sel_fail(parser, "expected An+B"); /* a bare sign with no digits and no 'n' is invalid */
        return;
    }
    sel_skip_ws(parser);
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
   like \2a or \* becomes '*'); the decoded slice is the value, split at match time. */
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
        sel_parse_anb(parser, &simple->nth_a, &simple->nth_b);
        if (parser->error) {
            return;
        }
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
            simple->tag_atom = sel_tag_atom(simple->name, simple->name_len);
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
    int grown = *capacity == 0 ? 8 : *capacity * 2;
    void *bigger = PyMem_Realloc(*buffer, (size_t)grown * elem_size);
    if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *buffer = bigger;
    *capacity = grown;
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
            if (grow_failed) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                parser->error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
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
                parser->error = 1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                               /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        compounds[count].simples = simples;
        compounds[count].count = simple_count;
        compounds[count].combinator = combinator;
        count++;
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
            if (grow_failed) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                parser->error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        if (sel_complex_parse(parser, &alts[count], nested, relative) == 0) {
            count++;
        } else if (forgiving) {
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
    sel_free_alts(compiled->alts, compiled->count);
    PyMem_Free(compiled->source);
    PyMem_Free(compiled);
}

/* Set a SelectorSyntaxError naming the selector, the reason the parse failed, and the
   position the failing token starts at (issue #434), e.g.
   invalid CSS selector ":nth-child(foo)": expected An+B at position 11 */
void sel_raise(PyObject *selector_error, PyObject *selector_str, const sel_parser *parser) {
    PyErr_Format(selector_error, "invalid CSS selector \"%U\": %s at position %zd", selector_str, parser->err_reason,
                 parser->err_pos);
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
    sel_parser parser = {compiled->source, 0, PyUnicode_GET_LENGTH(selector_str), tree, 0, 0, 0, "unexpected token"};
    if (sel_parse_alts(&parser, &compiled->alts, &compiled->count, 0, 0, 0) < 0) {
        sel_raise(selector_error, selector_str, &parser);
        PyMem_Free(compiled->source);
        PyMem_Free(compiled);
        return NULL;
    }
    return compiled;
}

/* The functional pseudo-classes recurse into the matcher: :is()/:where() test the
   element against a nested list, and :has() searches for a relative match, so the
   matching primitives they call are forward-declared here. */
static int sel_match_compound(th_node *node, const sel_compound *compound, const sel_ctx *ctx);
static int sel_matches_alts(th_node *node, const sel_complex *alts, int count, const sel_ctx *ctx);
static int sel_has_match(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx);

Py_UCS4 sel_fold(Py_UCS4 ch, int ci) {
    return (ci && ch >= 'A' && ch <= 'Z') ? ch + 32 : ch;
}

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
            return 1;
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
            if (sel_eq(value + start, want_len, want, want_len, ci)) {
                return 1;
            }
        }
        return 0;
    }
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

/* The 1-based position of node among its element siblings (from the end when
   from_end), counting only same-type siblings when of_type. */
static int sel_sibling_index(th_node *node, int from_end, int of_type) {
    int index = 1;
    for (th_node *sibling = from_end ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = from_end ? sibling->next_sibling : sibling->prev_sibling) {
        if (sibling->type == TH_NODE_ELEMENT && (!of_type || sel_same_type(node, sibling))) {
            index++;
        }
    }
    return index;
}

/* The 1-based position of node among its inclusive siblings that match the
   :nth-child(... of S) selector list (from the end when from_end), or 0 when node
   itself does not match S, so a non-matching element is never selected. */
static int sel_nth_of_index(th_node *node, int from_end, const sel_simple *simple, const sel_ctx *ctx) {
    if (!sel_matches_alts(node, simple->sub, simple->sub_count, ctx)) {
        return 0;
    }
    int index = 1;
    for (th_node *sibling = from_end ? node->next_sibling : node->prev_sibling; sibling != NULL;
         sibling = from_end ? sibling->next_sibling : sibling->prev_sibling) {
        if (sibling->type == TH_NODE_ELEMENT && sel_matches_alts(sibling, simple->sub, simple->sub_count, ctx)) {
            index++;
        }
    }
    return index;
}

/* Whether a 1-based index satisfies An+B for some non-negative n. */
static int sel_nth_matches(int a, int b, int index) {
    if (a == 0) {
        return index == b;
    }
    int diff = index - b;
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

/* Whether node sits inside fieldset's first legend, the region a disabled
   fieldset does not disable (HTML "the fieldset element"). */
static int sel_in_first_legend(th_node *fieldset, th_node *node) {
    th_node *legend = NULL;
    /* a direct child with the legend atom is necessarily an HTML legend: foreign
       content only enters through an svg/math subtree, never as a bare child here */
    for (th_node *child = fieldset->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && child->atom == TH_TAG_LEGEND) {
            legend = child;
            break;
        }
    }
    if (legend == NULL) {
        return 0;
    }
    for (th_node *ancestor = node; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor == legend) {
            return 1;
        }
    }
    return 0;
}

/* Whether node is actually disabled: its own disabled attribute, an option whose
   optgroup is disabled, or a control inside a disabled fieldset (HTML "disabled"). */
static int sel_is_disabled(th_node *node) {
    if (!sel_is_disableable(node)) {
        return 0;
    }
    if (sel_has_attr(node, TH_ATTR_DISABLED)) {
        return 1;
    }
    if (node->atom == TH_TAG_OPTION) {
        /* an option always has an element parent (it is never the document root),
           as the :root pseudo-class above also relies on; a parent with the optgroup
           atom is necessarily HTML */
        th_node *parent = node->parent;
        return parent->atom == TH_TAG_OPTGROUP && sel_has_attr(parent, TH_ATTR_DISABLED);
    }
    if (node->atom == TH_TAG_OPTGROUP || node->atom == TH_TAG_FIELDSET) {
        return 0; /* an optgroup/fieldset is disabled only by its own attribute */
    }
    for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
        /* a fieldset-atom ancestor is necessarily HTML; the type guard skips the
           document node that ends the chain */
        if (ancestor->type == TH_NODE_ELEMENT && ancestor->atom == TH_TAG_FIELDSET &&
            sel_has_attr(ancestor, TH_ATTR_DISABLED) && !sel_in_first_legend(ancestor, node)) {
            return 1;
        }
    }
    return 0;
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
static int sel_is_read_write(th_node *node) {
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_INPUT) {
        return sel_is_mutable_input(node) && !sel_has_attr(node, TH_ATTR_READONLY) && !sel_is_disabled(node);
    }
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_TEXTAREA) {
        return !sel_has_attr(node, TH_ATTR_READONLY) && !sel_is_disabled(node);
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
static int sel_is_default(th_node *node) {
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
        return form != NULL && sel_first_submit(form) == node;
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

/* :lang(): the element's language is the nearest lang attribute on it or an
   ancestor; it matches when any comma-separated range in the argument matches. */
static int sel_matches_lang(th_node *node, const sel_simple *simple) {
    const Py_UCS4 *tag = NULL;
    Py_ssize_t tag_len = 0;
    for (th_node *ancestor = node; ancestor != NULL && ancestor->type == TH_NODE_ELEMENT; ancestor = ancestor->parent) {
        const th_node_attr *attr = sel_find_attr(ancestor, TH_ATTR_LANG);
        if (attr != NULL && attr->value != NULL) { /* an empty lang is stored as NULL and skipped */
            tag = attr->value;
            tag_len = attr->value_len;
            break;
        }
    }
    if (tag == NULL) {
        return 0;
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

/* The direction of the first strong-directional character in a span: 2 rtl, else the
   1 ltr default when none is strong. */
static int sel_first_strong_dir(const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        int strong = sel_strong_dir(text[index]);
        if (strong != 0) {
            return strong;
        }
    }
    return 1;
}

/* The direction a dir=auto element resolves to (HTML "the directionality of an
   element"): an input element resolves from its value attribute, every other element
   from its text content. */
static int sel_auto_dir(th_tree *tree, th_node *node) {
    if (node->ns == TH_NS_HTML && node->atom == TH_TAG_INPUT) {
        const th_node_attr *value = sel_find_attr(node, TH_ATTR_VALUE);
        if (value == NULL || value->value == NULL) {
            return 1; /* an empty control value resolves to the ltr default */
        }
        return sel_first_strong_dir(value->value, value->value_len);
    }
    Py_ssize_t len = 0;
    Py_UCS4 *text = th_node_text(tree, node, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int dir = sel_first_strong_dir(text, len);
    PyMem_Free(text);
    return dir;
}

/* The element's resolved direction: 1 ltr, 2 rtl. The nearest dir attribute wins
   (auto and a dir-less bdi resolve from the content's first strong character);
   with none, the document default is ltr (HTML "the dir attribute"). */
static int sel_direction(th_tree *tree, th_node *node) {
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
                return sel_auto_dir(tree, node);
            }
        }
        return 1;
    }
    for (th_node *ancestor = node; ancestor != NULL && ancestor->type == TH_NODE_ELEMENT; ancestor = ancestor->parent) {
        const th_node_attr *attr = sel_find_attr(ancestor, TH_ATTR_DIR);
        if (attr != NULL && attr->value != NULL) {
            if (sel_kw(attr->value, attr->value_len, "ltr")) {
                return 1;
            }
            if (sel_kw(attr->value, attr->value_len, "rtl")) {
                return 2;
            }
            if (sel_kw(attr->value, attr->value_len, "auto")) {
                return sel_auto_dir(tree, ancestor);
            }
            /* an invalid dir value is ignored; keep walking up the ancestor chain */
        } else if (ancestor->ns == TH_NS_HTML && ancestor->atom == TH_TAG_BDI) {
            return sel_auto_dir(tree, ancestor); /* a dir-less bdi defaults to auto */
        }
    }
    return 1;
}

static int sel_match_pseudo(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    switch (simple->pseudo) { /* GCOVR_EXCL_BR_LINE: the parser only stores known pseudo ids */
    case PSEUDO_ROOT:
        /* the root element's parent is the document, never an element (a matched
           node always has a parent, as the '>' combinator above relies on) */
        return node->parent->type != TH_NODE_ELEMENT;
    case PSEUDO_EMPTY:
        return sel_is_empty(node, ctx->tree);
    case PSEUDO_FIRST_CHILD:
        return sel_no_sibling(node, 0, 0);
    case PSEUDO_LAST_CHILD:
        return sel_no_sibling(node, 1, 0);
    case PSEUDO_ONLY_CHILD:
        return sel_no_sibling(node, 0, 0) && sel_no_sibling(node, 1, 0);
    case PSEUDO_FIRST_OF_TYPE:
        return sel_no_sibling(node, 0, 1);
    case PSEUDO_LAST_OF_TYPE:
        return sel_no_sibling(node, 1, 1);
    case PSEUDO_ONLY_OF_TYPE:
        return sel_no_sibling(node, 0, 1) && sel_no_sibling(node, 1, 1);
    case PSEUDO_NTH_CHILD:
        if (simple->sub != NULL) {
            int of_index = sel_nth_of_index(node, 0, simple, ctx);
            return of_index != 0 && sel_nth_matches(simple->nth_a, simple->nth_b, of_index);
        }
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_sibling_index(node, 0, 0));
    case PSEUDO_NTH_LAST_CHILD:
        if (simple->sub != NULL) {
            int of_index = sel_nth_of_index(node, 1, simple, ctx);
            return of_index != 0 && sel_nth_matches(simple->nth_a, simple->nth_b, of_index);
        }
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_sibling_index(node, 1, 0));
    case PSEUDO_NTH_OF_TYPE:
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_sibling_index(node, 0, 1));
    case PSEUDO_NTH_LAST_OF_TYPE:
        return sel_nth_matches(simple->nth_a, simple->nth_b, sel_sibling_index(node, 1, 1));
    /* §6.6 the scoping root: the element the query was rooted at, or, when the root is
       the document (or a fragment), the document element, as :root resolves to */
    case PSEUDO_SCOPE:
        if (ctx->scope->type == TH_NODE_ELEMENT) {
            return node == ctx->scope;
        }
        return node->parent->type != TH_NODE_ELEMENT;
    /* §12 the input pseudo-classes determinable from the static tree */
    case PSEUDO_CHECKED:
        return sel_is_checked(node);
    case PSEUDO_DISABLED:
        return sel_is_disabled(node);
    case PSEUDO_ENABLED:
        return sel_is_disableable(node) && !sel_is_disabled(node);
    case PSEUDO_REQUIRED:
        return sel_is_required_capable(node) && sel_has_attr(node, TH_ATTR_REQUIRED);
    case PSEUDO_OPTIONAL:
        return sel_is_required_capable(node) && !sel_has_attr(node, TH_ATTR_REQUIRED);
    case PSEUDO_READ_ONLY:
        return !sel_is_read_write(node);
    case PSEUDO_READ_WRITE:
        return sel_is_read_write(node);
    case PSEUDO_DEFAULT:
        return sel_is_default(node);
    case PSEUDO_LANG:
        return sel_matches_lang(node, simple);
    case PSEUDO_DIR:
        return sel_direction(ctx->tree, node) == simple->nth_a;
    /* :link/:any-link: an a or area carrying an href (HTML "the :link/:any-link") */
    case PSEUDO_ANY_LINK:
        return (node->atom == TH_TAG_A || node->atom == TH_TAG_AREA) && sel_has_attr(node, TH_ATTR_HREF);
    /* live UA/interaction or navigation state a static tree cannot express */
    case PSEUDO_NEVER:
        return 0;
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

int sel_match_simple(th_node *node, const sel_simple *simple, const sel_ctx *ctx) {
    switch (simple->kind) {
    case '*':
        return 1;
    case ':':
        return sel_match_pseudo(node, simple, ctx);
    case 'e':
        if (simple->tag_atom != TH_TAG_UNKNOWN) {
            return node->atom == simple->tag_atom;
        }
        /* a custom/unknown tag is stored lowercased; type selectors are ASCII case-insensitive
           in HTML, so match it case-insensitively like the builtin-atom path above does */
        return sel_eq(node->text, node->text_len, simple->name, simple->name_len, 1);
    case '#': {
        /* class/ID selectors are case-sensitive in no-quirks mode, ASCII
           case-insensitive in quirks mode (Selectors-4 §6.1/§6.2) */
        const th_node_attr *attr = sel_find_attr(node, TH_ATTR_ID);
        return attr != NULL && attr->value != NULL &&
               sel_eq(attr->value, attr->value_len, simple->name, simple->name_len, ctx->quirks);
    }
    case '.': {
        const th_node_attr *attr = sel_find_attr(node, TH_ATTR_CLASS);
        if (attr == NULL || attr->value == NULL) {
            return 0;
        }
        return contains_ws_token(attr->value, attr->value_len, simple->name, simple->name_len, ctx->quirks);
    }
    default: { /* '[' */
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
        return sel_match_attr_op(simple, value, value_len, ci);
    }
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

static uint16_t sel_compound_known_type_atom(const sel_compound *compound) {
    if (compound->count != 1) {
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

/* node matches compounds[index]; verify the combinators and compounds to its left,
   with backtracking on the descendant and general-sibling axes. anchor is NULL for an
   ordinary selector (the leftmost compound is the end); for a :has() relative selector
   it is the element :has() tests, and the leftmost compound's leading combinator must
   connect to it. The interior-combinator machinery is shared by both. */
static int sel_match_from(th_node *node, const sel_complex *complex, int index, th_node *anchor, const sel_ctx *ctx) {
    if (index == 0) {
        if (anchor == NULL) {
            return 1;
        }
        /* an explicit :scope in the leftmost compound already matched the anchor (the
           scope was rebound to it), so it pins the compound to the anchor: :has(:scope >
           p) equals :has(> p), and the leading combinator adds nothing (issue #431) */
        if (sel_compound_has_scope(&complex->compounds[0])) {
            return 1;
        }
        switch (complex->compounds[0].combinator) {
        case '>':
            return node->parent == anchor;
        case '+':
            return sel_prev_element(node) == anchor;
        case '~':
            for (th_node *prev = sel_prev_element(node); prev != NULL; prev = sel_prev_element(prev)) {
                if (prev == anchor) {
                    return 1;
                }
            }
            return 0;
        default: /* descendant */
            for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
                if (ancestor == anchor) {
                    return 1;
                }
            }
            return 0;
        }
    }
    const sel_compound *target = &complex->compounds[index - 1];
    uint16_t target_atom = sel_compound_known_type_atom(target);
    switch (complex->compounds[index].combinator) {
    case '>': {
        /* a matched node is an element, so it always has a parent (the document
           at the root), which sel_match_compound rejects as a non-element */
        th_node *parent = node->parent;
        if (target_atom != TH_TAG_UNKNOWN) {
            return parent != NULL && parent->type == TH_NODE_ELEMENT && parent->atom == target_atom &&
                   sel_match_from(parent, complex, index - 1, anchor, ctx);
        }
        return sel_match_compound(parent, target, ctx) && sel_match_from(parent, complex, index - 1, anchor, ctx);
    }
    case '+': {
        th_node *prev = sel_prev_element(node);
        if (target_atom != TH_TAG_UNKNOWN) {
            return prev != NULL && prev->atom == target_atom && sel_match_from(prev, complex, index - 1, anchor, ctx);
        }
        return prev != NULL && sel_match_compound(prev, target, ctx) &&
               sel_match_from(prev, complex, index - 1, anchor, ctx);
    }
    case '~':
        if (target_atom != TH_TAG_UNKNOWN) {
            for (th_node *prev = sel_prev_element(node); prev != NULL; prev = sel_prev_element(prev)) {
                if (prev->atom == target_atom && sel_match_from(prev, complex, index - 1, anchor, ctx)) {
                    return 1;
                }
            }
            return 0;
        }
        for (th_node *prev = sel_prev_element(node); prev != NULL; prev = sel_prev_element(prev)) {
            if (sel_match_compound(prev, target, ctx) && sel_match_from(prev, complex, index - 1, anchor, ctx)) {
                return 1;
            }
        }
        return 0;
    default: /* descendant */
        if (target_atom != TH_TAG_UNKNOWN) {
            for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
                if (ancestor->type == TH_NODE_ELEMENT && ancestor->atom == target_atom &&
                    sel_match_from(ancestor, complex, index - 1, anchor, ctx)) {
                    return 1;
                }
            }
            return 0;
        }
        for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            if (sel_match_compound(ancestor, target, ctx) &&
                sel_match_from(ancestor, complex, index - 1, anchor, ctx)) {
                return 1;
            }
        }
        return 0;
    }
}

/* node matches some alternative in alts: it is the subject (rightmost compound) of
   one complex whose combinators to the left verify. The top-level match and the
   :is()/:where() pseudo-classes share this. */
static int sel_matches_alts(th_node *node, const sel_complex *alts, int count, const sel_ctx *ctx) {
    for (int index = 0; index < count; index++) {
        const sel_complex *complex = &alts[index];
        int subject = complex->count - 1;
        if (sel_match_compound(node, &complex->compounds[subject], ctx) &&
            sel_match_from(node, complex, subject, NULL, ctx)) {
            return 1;
        }
    }
    return 0;
}

/* Whether any element in the subtree below node is the subject of rel anchored at
   anchor (the element :has() is testing), checked recursively in document order. */
static int sel_has_subtree(th_node *node, const sel_complex *rel, int subject, th_node *anchor, const sel_ctx *ctx) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if ((sel_match_compound(child, &rel->compounds[subject], ctx) &&
             sel_match_from(child, rel, subject, anchor, ctx)) ||
            sel_has_subtree(child, rel, subject, anchor, ctx)) {
            return 1;
        }
    }
    return 0;
}

/* Whether the anchor element satisfies any :has() relative selector. A relative
   selector reaches the anchor's descendants and, through a leading sibling
   combinator, its following siblings and their subtrees. */
static int sel_has_match(th_node *anchor, const sel_complex *alts, int count, const sel_ctx *ctx) {
    /* inside a :has() relative selector the scope element is the anchor, so a written
       :scope resolves to it rather than the outer query root (Selectors-4 §6.6.2, #431) */
    sel_ctx scoped = {ctx->tree, anchor, ctx->quirks};
    for (int index = 0; index < count; index++) {
        const sel_complex *rel = &alts[index];
        int subject = rel->count - 1;
        if (sel_has_subtree(anchor, rel, subject, anchor, &scoped)) {
            return 1;
        }
        /* only a leading sibling combinator (:has(+ x) / :has(~ x)) can match outside
           the anchor's subtree; a descendant or child relative selector cannot, so its
           following-sibling scan would always fail and is skipped. An explicit leading
           :scope pins compound[0] to the anchor, so the reach is set by the combinator
           after it (:has(:scope + x) equals :has(+ x)) (issue #431) */
        char lead = rel->compounds[0].combinator;
        if (rel->count > 1 && sel_compound_has_scope(&rel->compounds[0])) {
            lead = rel->compounds[1].combinator;
        }
        if (lead != '+' && lead != '~') {
            continue;
        }
        for (th_node *sibling = anchor->next_sibling; sibling != NULL; sibling = sibling->next_sibling) {
            if (sibling->type != TH_NODE_ELEMENT) {
                continue;
            }
            if ((sel_match_compound(sibling, &rel->compounds[subject], &scoped) &&
                 sel_match_from(sibling, rel, subject, anchor, &scoped)) ||
                sel_has_subtree(sibling, rel, subject, anchor, &scoped)) {
                return 1;
            }
        }
    }
    return 0;
}

/* scope is the element :scope matches: the node the query was rooted at. */
int selector_matches(th_node *node, const sel_compiled *compiled, th_node *scope) {
    sel_ctx ctx = {compiled->tree, scope, compiled->quirks};
    return sel_matches_alts(node, compiled->alts, compiled->count, &ctx);
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
