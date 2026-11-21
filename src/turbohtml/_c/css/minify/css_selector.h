#ifndef TURBOHTML_CSS_SELECTOR_H
#define TURBOHTML_CSS_SELECTOR_H

#include "core/hash_limits.h"

/* The four pseudo-elements CSS Pseudo-Elements 4 §2 lets a selector spell with one colon (the legacy form) as well as
   two, with identical specificity and match; `::before` -> `:before` etc. saves a byte. */
static int css_is_legacy_pseudo_element(const css_token *token) {
    static const char *const names[] = {"before", "after", "first-line", "first-letter"};
    if (token->kind != CSS_IDENT) {
        return 0;
    }
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        if (css_run_ieq(token->text, token->text_len, names[index])) {
            return 1;
        }
    }
    return 0;
}

/* The index of the first token at or after index in [index, end) that is not a comment. */
static Py_ssize_t css_skip_comments(const token_vec *vec, Py_ssize_t index, Py_ssize_t end) {
    while (index < end && vec->items[index].kind == CSS_COMMENT) {
        index++;
    }
    return index;
}

/* The index of the legacy pseudo-element name that the second colon at or after index (in [index, end)) introduces, or
   -1. Comments make no token (CSS Syntax 3 §4.3.2), so one between the colons or before the name still spells it. Out
   of line: only a `::` or a comment after a `:` reaches it. */
CSS_NOINLINE static Py_ssize_t css_legacy_pseudo_name(const token_vec *vec, Py_ssize_t index, Py_ssize_t end) {
    Py_ssize_t second = css_skip_comments(vec, index, end);
    if (second == end || vec->items[second].kind != CSS_DELIM || vec->items[second].delim != ':') {
        return -1;
    }
    Py_ssize_t name = css_skip_comments(vec, second + 1, end);
    return name < end && css_is_legacy_pseudo_element(&vec->items[name]) ? name : -1;
}

/* Whether the `*` at index is followed, past any comments, by a hash, class, attribute or pseudo-class: the universal
   then adds nothing to its compound, so it is dropped. A comment between them makes no token, so it must not hide the
   token after it. */
static int css_universal_precedes_part(const token_vec *vec, Py_ssize_t index, Py_ssize_t end) {
    Py_ssize_t next = css_skip_comments(vec, index + 1, end);
    if (next == end) {
        return 0;
    }
    const css_token *token = &vec->items[next];
    return token->kind == CSS_HASH ||
           (token->kind == CSS_DELIM && (token->delim == '.' || token->delim == '[' || token->delim == ':'));
}

/* Whether the colon at index directly follows another colon, comments aside. A `::` it starts is part of a longer run,
   which no pseudo-element spells, so the legacy shortening must leave it as written. */
static int css_colon_before(const token_vec *vec, Py_ssize_t start, Py_ssize_t index) {
    while (index > start) {
        const css_token *previous = &vec->items[--index];
        if (previous->kind == CSS_COMMENT) {
            continue;
        }
        return previous->kind == CSS_DELIM && previous->delim == ':';
    }
    return 0;
}

/* Comments make no token; a namespace bar cannot start an unqualified universal (Selectors 4 §5.2). */
static int css_universal_starts_compound(const token_vec *vec, Py_ssize_t start, Py_ssize_t index) {
    int whitespace = 0;
    while (index > start) {
        const css_token *previous = &vec->items[--index];
        if (previous->kind == CSS_COMMENT) {
            continue;
        }
        if (previous->kind == CSS_WS) {
            whitespace = 1;
            continue;
        }
        if (previous->kind == CSS_DELIM && previous->delim == '|') {
            return 0;
        }
        return whitespace || (previous->kind == CSS_DELIM && (previous->delim == '>' || previous->delim == '+' ||
                                                              previous->delim == '~' || previous->delim == ','));
    }
    return 1;
}

/* Selectors 4 §6.1 permits a string value after a matcher, but requires an identifier as the name. */
static int css_attribute_value_position(const token_vec *vec, Py_ssize_t index) {
    const css_token *previous;
    do {
        previous = &vec->items[--index];
    } while (previous->kind == CSS_WS || previous->kind == CSS_COMMENT);
    return previous->kind == CSS_DELIM && previous->delim == '=';
}

/* A `+` before an unsigned number reads back as one signed number, so the space that closes a hex escape before it
   stays. */
static int css_plus_before_number(const token_vec *vec, Py_ssize_t index, Py_ssize_t end) {
    Py_ssize_t next = index + 1;
    while (next < end && (vec->items[next].kind == CSS_WS || vec->items[next].kind == CSS_COMMENT)) {
        next++;
    }
    if (next == end || vec->items[next].kind != CSS_NUM) {
        return 0;
    }
    css_char first = vec->items[next].text[0];
    return css_is_digit(first) || first == '.';
}

/* Whether the combinator at index keeps the space closing a hex escape that ends out. Kept out of line: only input
   whose token_vec.escapes is set reaches it. */
CSS_NOINLINE static int css_combinator_keeps_hex_close(const token_vec *vec, Py_ssize_t index, Py_ssize_t end,
                                                       const css_buf *out) {
    return vec->items[index].delim == '+' && css_plus_before_number(vec, index, end) && out->len > 0 &&
           css_ends_in_hex_close(out->data, out->len);
}

static int css_is_dashes_name(const css_token *token) {
    return token->kind == CSS_IDENT && token->text_len >= 2 && token->text[0] == '-' && token->text[1] == '-';
}

/* Whether the token at index, written straight after out where the input separates the two, spells a `<!--` or `-->`.
   A browser reads either as one token (CSS Syntax 3 §4.3.1), so the selector would change, and at the start of a rule
   it would be skipped, leaving the rest to match on its own. Kept out of line: only a dropped space or comment reaches
   it. */
CSS_NOINLINE static int css_selector_spells_comment_delim(const token_vec *vec, Py_ssize_t index, Py_ssize_t end,
                                                          const css_buf *out) {
    const css_token *token = &vec->items[index];
    /* a space or comment is pending only after a token this selector wrote, so out is not empty */
    css_char last = out->data[out->len - 1];
    css_char before = out->len > 1 ? out->data[out->len - 2] : 0;
    if (token->kind == CSS_DELIM && token->delim == '>') {
        return last == '-' && before == '-';
    }
    if (css_is_dashes_name(token)) {
        return last == '!' && before == '<';
    }
    return token->kind == CSS_DELIM && token->delim == '!' && last == '<' && index + 1 < end &&
           css_is_dashes_name(&vec->items[index + 1]);
}

/* Minify a selector token run [start, end) into out. */
static void css_minify_selector(token_vec *vec, Py_ssize_t start, Py_ssize_t end, int keyframe, css_buf *out) {
    int pending_ws = 0;
    int comment_gap = 0;
    int last_is_ident = 0;
    int attr_depth = 0;
    Py_ssize_t paren_depth = 0;
    Py_ssize_t base = out->len;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            comment_gap = 1;
            continue;
        }
        /* the caller skips leading whitespace and comments, so pending_ws and comment_gap are only set after a token
           has been emitted: out is non-empty whenever either is pending, unless a failed growth dropped that token */
        if (*out->oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (token->kind == CSS_DELIM && token->delim == '[') {
            attr_depth++;
        }
        if (token->kind == CSS_DELIM && token->delim == ']' && attr_depth > 0) {
            attr_depth--;
        }
        if (token->kind == CSS_DELIM && attr_depth == 0) {
            if (token->delim == '(') {
                paren_depth++;
            } else if (token->delim == ')' && paren_depth > 0) {
                paren_depth--;
            }
        }
        if (token->kind == CSS_DELIM && attr_depth == 0 &&
            (token->delim == '>' || token->delim == '+' || token->delim == '~' || token->delim == ',')) {
            if ((pending_ws || comment_gap) && token->delim == '>' &&
                css_selector_spells_comment_delim(vec, index, end, out)) {
                cbuf_putc(out, ' ');
            } else if (!vec->escapes || !css_combinator_keeps_hex_close(vec, index, end, out)) {
                css_rtrim_above(out, base);
            }
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && attr_depth > 0 &&
            (token->delim == '=' || token->delim == '~' || token->delim == '^' || token->delim == '$' ||
             token->delim == '*' || token->delim == '|')) {
            css_rtrim_above(out, base);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        if (pending_ws) {
            css_char last = out->data[out->len - 1];
            int blocked = last == '>' || last == '+' || last == '~' || last == ',' || last == '[' || last == '(';
            /* the space before an opening `[` separates it from the previous compound (`* [x]` is a descendant
               combinator, not the `*=` operator), so it uses the general rules; attr_depth is already raised for this
               `[`, and attribute selectors do not nest, so an opening bracket is the only token at a fresh attr level
             */
            int opening_bracket = token->kind == CSS_DELIM && token->delim == '[';
            if (attr_depth > 0 && !opening_bracket &&
                (last == '[' || last == '=' || last == '~' || last == '^' || last == '$' || last == '*' ||
                 last == '|')) {
                blocked = 1;
            }
            /* a delimiter a backslash escapes ends a name, so the space after it is a descendant combinator */
            if (!blocked || (vec->escapes && css_escapes(out->data, out->len - 1))) {
                cbuf_putc(out, ' ');
            }
        } else if (comment_gap && (css_would_merge_after(vec->escapes, out->data, out->len, last_is_ident, token->text,
                                                         token->text_len) ||
                                   css_selector_spells_comment_delim(vec, index, end, out))) {
            /* whitespace here would be a descendant combinator, so only an empty comment keeps the tokens apart */
            cbuf_puts(out, "/**/");
        }
        pending_ws = 0;
        comment_gap = 0;
        last_is_ident = token->kind == CSS_IDENT;
        if (token->kind == CSS_DELIM && token->delim == ':' && attr_depth == 0 && index + 1 < end &&
            (vec->items[index + 1].kind == CSS_COMMENT ||
             (vec->items[index + 1].kind == CSS_DELIM && vec->items[index + 1].delim == ':')) &&
            !css_colon_before(vec, start, index)) {
            Py_ssize_t name = css_legacy_pseudo_name(vec, index + 1, end);
            if (name >= 0) {
                cbuf_putc(out, ':'); /* legacy pseudo-element: emit one colon, drop the second; the name renders next */
                index = name - 1;
                continue;
            }
        }
        if (token->kind == CSS_DELIM && token->delim == '*' && css_universal_precedes_part(vec, index, end) &&
            css_universal_starts_compound(vec, start, index) && paren_depth == 0) {
            /* An explicit universal inside a function can constrain the default namespace (Selectors 4 §4.2). */
            continue;
        }
        /* '#' before an ident always merges into a single HASH token, so the prior char is never a bare '#' here */
        if (keyframe && token->kind == CSS_IDENT && css_run_ieq(token->text, token->text_len, "from")) {
            cbuf_puts(out, "0%"); /* CSS Animations 1: the keyframe selector "from" is equivalent to 0% (and shorter) */
        } else if (token->kind == CSS_STR && attr_depth > 0 && css_attribute_value_position(vec, index)) {
            /* Selectors 4 §6.1: an attribute-selector value is an <ident-token> or a <string>; the quotes drop only
               when the inner text is a valid <ident-token> (so [x="123"] keeps its quotes -- 123 is not an ident). */
            const css_char *inner = token->text + 1;
            Py_ssize_t inner_len = token->text_len - 2;
            if (css_is_ident_string(inner, inner_len)) {
                cbuf_put_run(out, inner, inner_len);
            } else {
                cbuf_put_run(out, token->text, token->text_len);
            }
        } else {
            if (token->kind == CSS_NUM) {
                cbuf_put_run(out, token->text, token->text_len);
                cbuf_put_run(out, (token->text + token->text_len), token->unit_len);
            } else {
                cbuf_put_run(out, token->text, token->text_len);
            }
        }
    }
    css_rtrim_above(out, base);
}

typedef struct {
    Py_ssize_t prop_off;
    Py_ssize_t prop_len;
    Py_ssize_t val_off;
    Py_ssize_t val_len;
    int important;
    int nested; /* a nested rule or at-rule kept verbatim (prop holds the whole text, val unused) */
    int at_statement;
    int dropped;
} css_decl;

typedef struct {
    css_decl *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int *oom;
    int check_repeats;
} decl_vec;

static int css_decl_is_wide_keyword(const css_buf *pool, const css_decl *decl) {
    const css_char *value = pool->data + decl->val_off;
    Py_ssize_t len = decl->val_len;
    return css_run_ieq(value, len, "inherit") || css_run_ieq(value, len, "initial") ||
           css_run_ieq(value, len, "unset") || css_run_ieq(value, len, "revert") ||
           css_run_ieq(value, len, "revert-layer");
}

static void decl_vec_push(decl_vec *vec, css_decl decl) {
    if (vec->len == vec->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(vec->len + 1), (size_t)vec->cap, 16, sizeof(css_decl), &cap, &bytes);
        if (!grew) {       /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            *vec->oom = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;        /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        css_decl *grown = css_realloc(vec->items, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            *vec->oom = 1;   /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        vec->items = grown;
        vec->cap = (Py_ssize_t)cap;
    }
    vec->items[vec->len++] = decl;
}

/* Whether a property name starts with `--`, making it a custom property. css_spell_names has decoded the name, so the
   bytes tell (`\2d-X` reads `--X`). The name runs through the declaration's colon, so it holds two bytes at least. */
static int css_name_is_custom(const css_char *name) {
    return name[0] == '-' && name[1] == '-';
}

/* Whether the value tokens [start, end) call a substitution function: var(), env(), attr() or if(). Like var() (CSS
   Variables 1 §3), each one leaves the value unchecked until substitution, so a browser keeps the declaration. */
CSS_NOINLINE static int css_value_substitutes(const token_vec *vec, Py_ssize_t start, Py_ssize_t end) {
    for (Py_ssize_t index = start; index + 1 < end; index++) {
        const css_token *token = &vec->items[index];
        if (token->kind == CSS_IDENT && vec->items[index + 1].kind == CSS_DELIM && vec->items[index + 1].delim == '(' &&
            (css_run_ieq(token->text, token->text_len, "var") || css_run_ieq(token->text, token->text_len, "env") ||
             css_run_ieq(token->text, token->text_len, "attr") || css_run_ieq(token->text, token->text_len, "if"))) {
            return 1;
        }
    }
    return 0;
}

/* Build a declaration from a segment [start, end). Returns 1 if a declaration was produced. A string cut short or a
   bad url drops the declaration. A `\` before a newline is a delimiter: a custom property's value and a value that
   calls a substitution function keep it, written as it stands with its newline, and any other declaration is dropped
   as a browser drops it. */
static CSS_FORCEINLINE int css_make_declaration(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end,
                                                comp_vec *scratch, css_decl *decl, int preserve_name) {
    int backslash = 0;
    if (vec->bad && css_holds_bad(vec, start, end)) {
        if (css_holds_cut(vec, start, end)) {
            return 0;
        }
        backslash = 1;
    }
    Py_ssize_t colon = -1;
    for (Py_ssize_t index = start; index < end; index++) {
        if (vec->items[index].kind == CSS_DELIM && vec->items[index].delim == ':') {
            colon = index;
            break;
        }
    }
    if (colon < 0) {
        return 0;
    }
    /* the property name is the text before the colon, with surrounding whitespace trimmed */
    /* the caller (css_parse_declarations) skips leading whitespace/comments, so start is the first property token */
    Py_ssize_t prop_start = start;
    Py_ssize_t prop_end = colon;
    while (prop_end > prop_start && vec->items[prop_end - 1].kind == CSS_WS) {
        prop_end--;
    }
    /* a name that opens a block before the colon, as in `(:)`, is no declaration (CSS Syntax 3 §5.4.5): a browser drops
       it, so the first pass must too. A single ident opens no block and skips the scan. */
    int bare_name = prop_end == prop_start + 1 && vec->items[prop_start].kind == CSS_IDENT;
    if (prop_start >= prop_end || (!bare_name && css_leaves_block_open(vec, prop_start, colon))) {
        return 0;
    }
    /* the raw property text spans from the first to the last non-ws token; build it for the --* check and interning */
    const css_token *first = &vec->items[prop_start];
    int is_custom = first->kind == CSS_IDENT && css_name_is_custom(first->text);
    if (backslash &&
        (css_holds_bad(vec, start, colon) || (!is_custom && !css_value_substitutes(vec, colon + 1, end)))) {
        return 0;
    }
    Py_ssize_t prop_off = pool->len;
    for (Py_ssize_t index = prop_start; index < prop_end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_NUM) {
            for (Py_ssize_t pos = 0; pos < token->text_len; pos++) {
                cbuf_putc(pool, css_lower(token->text[pos]));
            }
            for (Py_ssize_t pos = 0; pos < token->unit_len; pos++) {
                cbuf_putc(pool, css_lower((token->text + token->text_len)[pos]));
            }
        } else if (token->kind != CSS_WS) {
            /* custom-property names and font-feature aliases are case-sensitive, so preserve their spelling. */
            if (is_custom || preserve_name) {
                cbuf_put_run(pool, token->text, token->text_len);
            } else {
                for (Py_ssize_t pos = 0; pos < token->text_len; pos++) {
                    cbuf_putc(pool, css_lower(token->text[pos]));
                }
            }
        } else if (vec->items[index - 1].kind == CSS_DELIM && vec->items[index - 1].delim == '<') {
            /* the tokenizer turns a comment that would join `</` into a space; drop it and keep the declaration so the
               output's `</style` safety net (css.c) can return the source unchanged */
        } else {
            /* whitespace joining two tokens: the name is not one ident, so drop the invalid declaration */
            pool->len = prop_off;
            return 0;
        }
    }
    Py_ssize_t prop_len = pool->len - prop_off;

    /* !important is significant only as the value's trailing "!" "important" pair (CSS Syntax 3 §5.4.4): scan back over
       trailing whitespace/comments to the last two significant tokens. A "!important" anywhere else -- mid-value, or
       nested inside a function (where the last token is the ")") -- is ordinary value text and must be kept. */
    Py_ssize_t value_start = colon + 1;
    Py_ssize_t value_end = end;
    int important = 0;
    Py_ssize_t last = value_end - 1;
    while (last >= value_start && (vec->items[last].kind == CSS_WS || vec->items[last].kind == CSS_COMMENT)) {
        last--;
    }
    if (last > value_start && vec->items[last].kind == CSS_IDENT &&
        css_run_ieq(vec->items[last].text, vec->items[last].text_len, "important")) {
        Py_ssize_t bang = last - 1;
        while (bang > value_start && (vec->items[bang].kind == CSS_WS || vec->items[bang].kind == CSS_COMMENT)) {
            bang--;
        }
        /* in a declaration left open at the end of the input, a block still open before the "!" holds the pair, so it
           is block content, not the priority */
        if (vec->items[bang].kind == CSS_DELIM && vec->items[bang].delim == '!' &&
            !(end == vec->len && css_leaves_block_open(vec, value_start, bang))) {
            important = 1;
            value_end = bang;
            if (!is_custom) {
                last = bang - 1;
                while (last >= value_start &&
                       (vec->items[last].kind == CSS_WS || vec->items[last].kind == CSS_COMMENT)) {
                    last--;
                }
            }
        }
    }

    if (!is_custom && last < value_start) {
        pool->len = prop_off;
        return 0;
    }

    css_buf value = {NULL, 0, 0, pool->oom};
    css_minify_value(pool, vec, value_start, value_end, pool->data + prop_off, prop_len, is_custom || backslash,
                     scratch, &value);
    if (!is_custom && value.len == 0) {
        cbuf_free(&value);
        pool->len = prop_off;
        return 0;
    }
    Py_ssize_t val_off = pool_run(pool, value.data, value.len);
    Py_ssize_t val_len = value.len;
    cbuf_free(&value);

    decl->prop_off = prop_off;
    decl->prop_len = prop_len;
    decl->val_off = val_off;
    decl->val_len = val_len;
    decl->important = important;
    decl->nested = 0;
    decl->dropped = 0;
    return 1;
}

/* The space-separated set of properties a shorthand fully overrides (including itself), or NULL when prop is not a
   shorthand. Resolving this once per declaration -- instead of rescanning the table for every pair -- keeps dedup off
   the O(n^2 * table) path that a rule with hundreds of custom properties would otherwise hit. */
static const char CSS_BACKGROUND_LONGHANDS[] =
    "background background-image background-color background-position background-size background-repeat "
    "background-origin background-clip background-attachment";

static const char *css_longhand_list(const css_char *prop, Py_ssize_t prop_len) {
    static const char *const shorthands[] = {
        "background",  "font",         "border",        "border-width",  "border-style", "border-color",
        "border-top",  "border-right", "border-bottom", "border-left",   "margin",       "padding",
        "column-rule", "animation",    "columns",       "flex",          "flex-flow",    "grid",
        "grid-area",   "grid-row",     "grid-column",   "grid-template", "list-style",   "offset",
        "outline",     "overflow",     "place-content", "place-items",   "place-self",   "text-decoration",
        "transition"};
    // NOLINTBEGIN(bugprone-suspicious-missing-comma): each entry is one shorthand's longhand list as a single
    // space-separated string; clang-format wraps the long ones across lines, which is string concatenation, not a
    // missing comma between array elements
    static const char *const longhands[] = {
        CSS_BACKGROUND_LONGHANDS,
        "font font-style font-variant font-weight font-stretch font-size font-family line-height",
        "border border-width border-top-width border-right-width border-bottom-width border-left-width border-style "
        "border-top-style border-right-style border-bottom-style border-left-style border-color border-top-color "
        "border-right-color border-bottom-color border-left-color",
        "border-width border-top-width border-right-width border-bottom-width border-left-width",
        "border-style border-top-style border-right-style border-bottom-style border-left-style",
        "border-color border-top-color border-right-color border-bottom-color border-left-color",
        "border-top border-top-width border-top-style border-top-color",
        "border-right border-right-width border-right-style border-right-color",
        "border-bottom border-bottom-width border-bottom-style border-bottom-color",
        "border-left border-left-width border-left-style border-left-color",
        "margin margin-top margin-right margin-bottom margin-left",
        "padding padding-top padding-right padding-bottom padding-left",
        "column-rule column-rule-width column-rule-style column-rule-color",
        "animation animation-name animation-duration animation-timing-function animation-delay "
        "animation-iteration-count animation-direction animation-fill-mode animation-play-state",
        "columns column-width column-count",
        "flex flex-basis flex-grow flex-shrink",
        "flex-flow flex-direction flex-wrap",
        "grid grid-template-rows grid-template-columns grid-template-areas grid-auto-rows grid-auto-columns "
        "grid-auto-flow",
        "grid-area grid-row-start grid-column-start grid-row-end grid-column-end",
        "grid-row grid-row-start grid-row-end",
        "grid-column grid-column-start grid-column-end",
        "grid-template grid-template-rows grid-template-columns grid-template-areas",
        "list-style list-style-image list-style-position list-style-type",
        "offset offset-position offset-path offset-distance offset-anchor offset-rotate",
        "outline outline-width outline-style outline-color",
        "overflow overflow-x overflow-y",
        "place-content align-content justify-content",
        "place-items align-items justify-items",
        "place-self align-self justify-self",
        "text-decoration text-decoration-color text-decoration-line text-decoration-thickness",
        "transition transition-property transition-duration transition-timing-function transition-delay"};
    // NOLINTEND(bugprone-suspicious-missing-comma)
    /* a custom property and any name without a hyphen-or-known-shorthand shape is never a shorthand */
    if (prop_len < 4) {
        return NULL;
    }
    /* Every shorthand literal is lower-case, so its first byte is the lower-cased first byte of a matching property.
       Gating on it skips the css_run_ieq call for the majority of names that share no initial letter with any
       shorthand -- the common case for a declaration list that is mostly longhands and custom properties. */
    css_char first = css_lower(prop[0]);
    for (size_t index = 0; index < sizeof(shorthands) / sizeof(shorthands[0]); index++) {
        if ((css_char)(unsigned char)shorthands[index][0] == first && css_run_ieq(prop, prop_len, shorthands[index])) {
            return longhands[index];
        }
    }
    return NULL;
}

static int css_prop_in_list(const css_char *prop, Py_ssize_t prop_len, const char *list) {
    while (*list) {
        const char *word = list;
        while (*list && *list != ' ') {
            list++;
        }
        if ((Py_ssize_t)(list - word) == prop_len) {
            int same = 1;
            for (Py_ssize_t pos = 0; pos < prop_len; pos++) {
                if (prop[pos] != (css_char)(unsigned char)word[pos]) {
                    same = 0;
                    break;
                }
            }
            if (same) {
                return 1;
            }
        }
        while (*list == ' ') {
            list++;
        }
    }
    return 0;
}

static int css_decl_value_eq(const css_buf *pool, const css_decl *left, const css_decl *right) {
    return left->val_len == right->val_len && memcmp(pool->data + left->val_off, pool->data + right->val_off,
                                                     (size_t)left->val_len * sizeof(css_char)) == 0;
}

/* A later shorthand only replaces a longhand if its value parses; CSS-wide keywords are the subset we know parse. */
static void css_dedup_pairwise(css_buf *pool, decl_vec *decls) {
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *later = &decls->items[index];
        const css_char *prop = pool->data + later->prop_off;
        Py_ssize_t prop_len = later->prop_len;
        const char *list = css_decl_is_wide_keyword(pool, later)
                               ? css_longhand_list(pool->data + later->prop_off, later->prop_len)
                               : NULL;
        for (Py_ssize_t earlier = 0; earlier < index; earlier++) {
            css_decl *prev = &decls->items[earlier];
            if (prev->dropped) {
                continue;
            }
            const css_char *prior = pool->data + prev->prop_off;
            int same_name = prev->prop_len == prop_len;
            for (Py_ssize_t pos = 0; same_name && pos < prop_len; pos++) {
                same_name = prop[pos] == prior[pos];
            }
            int same_value = same_name && css_decl_value_eq(pool, prev, later);
            if (same_name && !same_value) {
                decls->check_repeats = 1;
            }
            if (prev->important != later->important) {
                continue;
            }
            int overrides = same_name ? same_value : (list != NULL && css_prop_in_list(prior, prev->prop_len, list));
            if (overrides) {
                prev->dropped = 1;
            }
        }
    }
}

/* A property -> most-recent-active-declaration entry, keyed by (lowercased name, importance). */
typedef struct {
    Py_ssize_t prop_off;
    Py_ssize_t prop_len;
    int important;
    Py_ssize_t index;
    int used;
} dedup_entry;

/* FNV-1a over a run, the hash the dedup and rule tables start on. The stylesheet picks its property names and rules,
   and FNV-1a is fixed, so each table watches its walks and, once one passes TH_FAST_PROBE_LIMIT, starts over under
   the per-process key (core/hash.h) when keyed is set. The standalone harness has no interpreter to draw a key from
   and stays on FNV-1a. A css_char is one byte, so the run and C-string forms agree on equal text either way. */
static uint32_t css_hash_run(const css_char *text, Py_ssize_t len, int keyed) {
#ifndef CSS_MINIFY_STANDALONE
    if (keyed) {
        return th_name_hash(text, len);
    }
#else
    (void)keyed;
#endif
    uint32_t hash = 2166136261u;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        hash = (hash ^ (uint32_t)(text[pos] & 0xFF)) * 16777619u;
    }
    return hash;
}

static uint32_t css_hash_cstr(const char *text, Py_ssize_t len, int keyed) {
#ifndef CSS_MINIFY_STANDALONE
    if (keyed) {
        return th_name_hash(text, len);
    }
#else
    (void)keyed;
#endif
    uint32_t hash = 2166136261u;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        hash = (hash ^ (uint32_t)(unsigned char)text[pos]) * 16777619u;
    }
    return hash;
}

/* 1 when a walk from home to slot on FNV-1a went past TH_FAST_PROBE_LIMIT, so the table starts over keyed. */
static int css_walked_far(Py_ssize_t home, Py_ssize_t slot, uint32_t mask, int keyed) {
    return !keyed && (uint32_t)((size_t)(slot - home) & mask) > TH_FAST_PROBE_LIMIT;
}

/* Find the slot for a code-point key (a property name from the pool): the matching entry, or the empty slot to fill;
   -1 when the walk to it went past TH_FAST_PROBE_LIMIT on FNV-1a. */
static Py_ssize_t dedup_slot_run(dedup_entry *table, uint32_t mask, const css_buf *pool, const css_char *key,
                                 Py_ssize_t key_len, int important, int keyed) {
    const Py_ssize_t home = css_hash_run(key, key_len, keyed) & mask;
    Py_ssize_t slot = home;
    while (table[slot].used) {
        if (table[slot].important == important && table[slot].prop_len == key_len &&
            memcmp(pool->data + table[slot].prop_off, key, (size_t)key_len * sizeof(css_char)) == 0) {
            break;
        }
        TH_WORK_STEP();
        slot = (slot + 1) & mask;
    }
    return css_walked_far(home, slot, mask, keyed) ? -1 : slot;
}

/* Find the slot for an ASCII key (a longhand name from the shorthand table), as dedup_slot_run. */
static Py_ssize_t dedup_slot_cstr(dedup_entry *table, uint32_t mask, const css_buf *pool, const char *key,
                                  Py_ssize_t key_len, int important, int keyed) {
    const Py_ssize_t home = css_hash_cstr(key, key_len, keyed) & mask;
    Py_ssize_t slot = home;
    while (table[slot].used) {
        if (table[slot].important == important && table[slot].prop_len == key_len) {
            int same = 1;
            for (Py_ssize_t pos = 0; pos < key_len; pos++) {
                if (pool->data[table[slot].prop_off + pos] != (css_char)(unsigned char)key[pos]) {
                    same = 0;
                    break;
                }
            }
            if (same) {
                break;
            }
        }
        TH_WORK_STEP();
        slot = (slot + 1) & mask;
    }
    return css_walked_far(home, slot, mask, keyed) ? -1 : slot;
}

/* One pass of css_dedup over an empty table. 1 when a walk on FNV-1a went past TH_FAST_PROBE_LIMIT, which stops the
   pass. A pass marks a declaration dropped only when that holds whatever was dropped before, so the keyed pass that
   follows ends with the marks a single pass would have made. */
static int css_dedup_fill(css_buf *pool, decl_vec *decls, dedup_entry *table, uint32_t mask, int keyed) {
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *later = &decls->items[index];
        const css_char *prop = pool->data + later->prop_off;
        Py_ssize_t prop_len = later->prop_len;
        int important = later->important;
        const char *list = css_decl_is_wide_keyword(pool, later)
                               ? css_longhand_list(pool->data + later->prop_off, later->prop_len)
                               : NULL;
        Py_ssize_t home;
        if (list == NULL) {
            home = dedup_slot_run(table, mask, pool, prop, prop_len, important, keyed);
            if (home < 0) {
                return 1;
            }
            /* a same-name duplicate is dropped only when its value is identical (see css_dedup_pairwise) */
            if (table[home].used && !decls->items[table[home].index].dropped &&
                css_decl_value_eq(pool, &decls->items[table[home].index], later)) {
                decls->items[table[home].index].dropped = 1;
            }
        } else {
            /* The first word is the shorthand's own name; CSS-wide values reset every remaining longhand. */
            const char *cursor = list;
            int own_name = 1;
            while (*cursor) {
                const char *word = cursor;
                while (*cursor && *cursor != ' ') {
                    cursor++;
                }
                Py_ssize_t slot = dedup_slot_cstr(table, mask, pool, word, cursor - word, important, keyed);
                if (slot < 0) {
                    return 1;
                }
                if (table[slot].used && !decls->items[table[slot].index].dropped &&
                    (!own_name || css_decl_value_eq(pool, &decls->items[table[slot].index], later))) {
                    decls->items[table[slot].index].dropped = 1;
                }
                while (*cursor == ' ') {
                    cursor++;
                }
                own_name = 0;
            }
            home = dedup_slot_run(table, mask, pool, prop, prop_len, important, keyed);
        }
        table[home].prop_off = later->prop_off;
        table[home].prop_len = prop_len;
        table[home].important = important;
        table[home].index = index;
        table[home].used = 1;
    }
    return 0;
}

/* Drop earlier declarations a later one fully overrides. Pairwise for a small rule; for a large one (a framework
   :root with hundreds of custom properties) a hash of property -> most-recent declaration makes it linear, since each
   later declaration overrides only its own name and -- when it is a shorthand -- the few names in its longhand list. */
#define CSS_DEDUP_STACK 512
static void css_dedup(css_buf *pool, decl_vec *decls) {
    if (decls->len <= 32) {
        css_dedup_pairwise(pool, decls);
        return;
    }
    decls->check_repeats = 1;
    Py_ssize_t cap = 64;
    while (cap < decls->len * 2) {
        cap *= 2;
    }
    /* keep the hash table on the stack for the common rule (up to 256 declarations); only a framework-sized :root
       spills to the heap */
    dedup_entry stack_table[CSS_DEDUP_STACK];
    dedup_entry *table = stack_table;
    if (cap > CSS_DEDUP_STACK) {
        /* GCOVR_EXCL_START: needs a 32-bit build and a rule of over 100 million declarations */
        if ((size_t)cap > SIZE_MAX / sizeof(dedup_entry)) {
            *pool->oom = 1;
            return;
        }
        /* GCOVR_EXCL_STOP */
        table = css_malloc((size_t)cap * sizeof(dedup_entry));
        if (table == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            *pool->oom = 1;  /* GCOVR_EXCL_LINE: allocation-failure path */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    memset(table, 0, (size_t)cap * sizeof(dedup_entry));
    uint32_t mask = (uint32_t)(cap - 1);
    if (css_dedup_fill(pool, decls, table, mask, 0)) {
        memset(table, 0, (size_t)cap * sizeof(dedup_entry));
        css_dedup_fill(pool, decls, table, mask, 1);
    }
    if (table != stack_table) {
        css_free(table);
    }
    css_decl active[32];
    Py_ssize_t positions[32];
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        if (!decls->items[index].dropped) {
            if (count == 32) {
                return;
            }
            positions[count] = index;
            active[count++] = decls->items[index];
        }
    }
    /* The hash remembers only the latest value for a property; a small remaining list can still contain older equals.
     */
    decl_vec remaining = {active, count, count, pool->oom, 0};
    css_dedup_pairwise(pool, &remaining);
    for (Py_ssize_t index = 0; index < count; index++) {
        decls->items[positions[index]].dropped = active[index].dropped;
    }
}

#endif /* TURBOHTML_CSS_SELECTOR_H */
