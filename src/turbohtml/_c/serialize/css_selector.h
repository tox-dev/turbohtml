#ifndef TURBOHTML_CSS_SELECTOR_H
#define TURBOHTML_CSS_SELECTOR_H

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

/* Minify a selector token run [start, end) into out. */
static void css_minify_selector(token_vec *vec, Py_ssize_t start, Py_ssize_t end, int keyframe, css_buf *out) {
    int pending_ws = 0;
    int attr_depth = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '[') {
            attr_depth++;
        }
        if (token->kind == CSS_DELIM && token->delim == ']' && attr_depth > 0) {
            attr_depth--;
        }
        if (token->kind == CSS_DELIM && attr_depth == 0 &&
            (token->delim == '>' || token->delim == '+' || token->delim == '~' || token->delim == ',')) {
            css_rtrim(out);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && attr_depth > 0 &&
            (token->delim == '=' || token->delim == '~' || token->delim == '^' || token->delim == '$' ||
             token->delim == '*' || token->delim == '|')) {
            css_rtrim(out);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        /* the caller skips leading whitespace, so pending_ws is only set after a token has been emitted: out is
           non-empty whenever a deferred descendant space is pending */
        if (pending_ws) {
            css_char last = out->data[out->len - 1];
            int blocked = last == '>' || last == '+' || last == '~' || last == ',' || last == '[' || last == '(';
            if (attr_depth > 0 && (last == '[' || last == '=' || last == '~' || last == '^' || last == '$' ||
                                   last == '*' || last == '|')) {
                blocked = 1;
            }
            if (!blocked) {
                cbuf_putc(out, ' ');
            }
        }
        pending_ws = 0;
        if (token->kind == CSS_DELIM && token->delim == ':' && attr_depth == 0 && index + 2 < end &&
            vec->items[index + 1].kind == CSS_DELIM && vec->items[index + 1].delim == ':' &&
            css_is_legacy_pseudo_element(&vec->items[index + 2])) {
            cbuf_putc(out, ':'); /* legacy pseudo-element: emit one colon, drop the second; the name renders next */
            index++;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '*' && index + 1 < end &&
            (vec->items[index + 1].kind == CSS_HASH ||
             (vec->items[index + 1].kind == CSS_DELIM &&
              (vec->items[index + 1].delim == '.' || vec->items[index + 1].delim == '[' ||
               vec->items[index + 1].delim == ':')))) {
            /* a `*` only reaches here at attr-depth 0; inside [...] the `*=` operator is consumed above. A universal
               `*` glued to a subclass/pseudo is redundant (Selectors 4 §5.2): *:hover -> :hover */
            continue;
        }
        /* '#' before an ident always merges into a single HASH token, so the prior char is never a bare '#' here */
        int after_combinator = out->len > 0 && (out->data[out->len - 1] == '.' || out->data[out->len - 1] == ':');
        int is_custom = token->text_len >= 2 && token->text[0] == '-' && token->text[1] == '-';
        if (keyframe && token->kind == CSS_IDENT && css_run_ieq(token->text, token->text_len, "from")) {
            cbuf_puts(out, "0%"); /* CSS Animations 1: the keyframe selector "from" is equivalent to 0% (and shorter) */
        } else if (token->kind == CSS_IDENT && attr_depth == 0 && !after_combinator && !is_custom) {
            for (Py_ssize_t pos = 0; pos < token->text_len; pos++) {
                cbuf_putc(out, css_lower(token->text[pos]));
            }
        } else if (token->kind == CSS_STR && attr_depth > 0) {
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
    css_rtrim(out);
}

typedef struct {
    Py_ssize_t prop_off;
    Py_ssize_t prop_len;
    Py_ssize_t val_off;
    Py_ssize_t val_len;
    int important;
    int nested; /* a nested rule or at-rule kept verbatim (prop holds the whole text, val unused) */
    int dropped;
} css_decl;

typedef struct {
    css_decl *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} decl_vec;

static void decl_vec_push(decl_vec *vec, css_decl decl) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : 16;
        css_decl *grown = css_realloc(vec->items, (size_t)cap * sizeof(css_decl));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE */
            return;          /* GCOVR_EXCL_LINE */
        }
        vec->items = grown;
        vec->cap = cap;
    }
    vec->items[vec->len++] = decl;
}

/* Build a declaration from a segment [start, end). Returns 1 if a declaration was produced. */
static int css_make_declaration(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, comp_vec *scratch,
                                css_decl *decl) {
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
    if (prop_start >= prop_end) {
        return 0;
    }
    /* the raw property text spans from the first to the last non-ws token; build it for the --* check and interning */
    int is_custom = vec->items[prop_start].kind == CSS_IDENT && vec->items[prop_start].text_len >= 2 &&
                    vec->items[prop_start].text[0] == '-' && vec->items[prop_start].text[1] == '-';
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
            /* custom-property names are case-sensitive (CSS Variables 1 §2: "--foo" and "--FOO" are distinct), so a
               --* name is kept verbatim; every other property name is ASCII case-insensitive and lower-cased. */
            for (Py_ssize_t pos = 0; pos < token->text_len; pos++) {
                cbuf_putc(pool, is_custom ? token->text[pos] : css_lower(token->text[pos]));
            }
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
        if (vec->items[bang].kind == CSS_DELIM && vec->items[bang].delim == '!') {
            important = 1;
            value_end = bang;
        }
    }

    css_buf value = {NULL, 0, 0, 0};
    css_minify_value(pool, vec, value_start, value_end, pool->data + prop_off, prop_len, is_custom, scratch, &value);
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
        "background background-image background-position background-size background-repeat background-origin "
        "background-clip background-attachment background-color",
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
    for (size_t index = 0; index < sizeof(shorthands) / sizeof(shorthands[0]); index++) {
        if (css_run_ieq(prop, prop_len, shorthands[index])) {
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

/* The pairwise dedup, fine for the typical small rule. Per CSS Cascade last-wins only among declarations that parse:
   a later same-name declaration with a *different* value may be a progressive-enhancement fallback a browser keeps
   when it cannot parse the later value, so only an identical same-name duplicate is dropped. A longhand covered by a
   later shorthand (a different name in its longhand list) is a strict subset and stays droppable. */
static void css_dedup_pairwise(css_buf *pool, decl_vec *decls) {
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *later = &decls->items[index];
        if (later->nested) {
            continue;
        }
        const css_char *prop = pool->data + later->prop_off;
        Py_ssize_t prop_len = later->prop_len;
        const char *list = css_longhand_list(prop, prop_len);
        for (Py_ssize_t earlier = 0; earlier < index; earlier++) {
            css_decl *prev = &decls->items[earlier];
            if (prev->nested || prev->dropped || prev->important != later->important) {
                continue;
            }
            const css_char *prior = pool->data + prev->prop_off;
            int same_name = prev->prop_len == prop_len;
            for (Py_ssize_t pos = 0; same_name && pos < prop_len; pos++) {
                same_name = prop[pos] == prior[pos];
            }
            int overrides = same_name ? css_decl_value_eq(pool, prev, later)
                                      : (list != NULL && css_prop_in_list(prior, prev->prop_len, list));
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

static uint32_t css_hash_run(const css_char *text, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        hash = (hash ^ (uint32_t)(text[pos] & 0xFF)) * 16777619u;
    }
    return hash;
}

static uint32_t css_hash_cstr(const char *text, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        hash = (hash ^ (uint32_t)(unsigned char)text[pos]) * 16777619u;
    }
    return hash;
}

/* Find the slot for a code-point key (a property name from the pool): the matching entry, or the empty slot to fill. */
static Py_ssize_t dedup_slot_run(dedup_entry *table, uint32_t mask, const css_buf *pool, const css_char *key,
                                 Py_ssize_t key_len, int important) {
    Py_ssize_t slot = css_hash_run(key, key_len) & mask;
    while (table[slot].used) {
        if (table[slot].important == important && table[slot].prop_len == key_len &&
            memcmp(pool->data + table[slot].prop_off, key, (size_t)key_len * sizeof(css_char)) == 0) {
            break;
        }
        slot = (slot + 1) & mask;
    }
    return slot;
}

/* Find the slot for an ASCII key (a longhand name from the shorthand table). */
static Py_ssize_t dedup_slot_cstr(dedup_entry *table, uint32_t mask, const css_buf *pool, const char *key,
                                  Py_ssize_t key_len, int important) {
    Py_ssize_t slot = css_hash_cstr(key, key_len) & mask;
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
        slot = (slot + 1) & mask;
    }
    return slot;
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
    Py_ssize_t cap = 64;
    while (cap < decls->len * 2) {
        cap *= 2;
    }
    /* keep the hash table on the stack for the common rule (up to 256 declarations); only a framework-sized :root
       spills to the heap */
    dedup_entry stack_table[CSS_DEDUP_STACK];
    dedup_entry *table = stack_table;
    if (cap > CSS_DEDUP_STACK) {
        table = css_malloc((size_t)cap * sizeof(dedup_entry));
        if (table == NULL) {                 /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            css_dedup_pairwise(pool, decls); /* GCOVR_EXCL_LINE */
            return;                          /* GCOVR_EXCL_LINE */
        }
    }
    memset(table, 0, (size_t)cap * sizeof(dedup_entry));
    uint32_t mask = (uint32_t)(cap - 1);
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *later = &decls->items[index];
        if (later->nested) {
            continue;
        }
        const css_char *prop = pool->data + later->prop_off;
        Py_ssize_t prop_len = later->prop_len;
        int important = later->important;
        const char *list = css_longhand_list(prop, prop_len);
        if (list == NULL) {
            Py_ssize_t slot = dedup_slot_run(table, mask, pool, prop, prop_len, important);
            /* a same-name duplicate is dropped only when its value is identical (see css_dedup_pairwise) */
            if (table[slot].used && !decls->items[table[slot].index].dropped &&
                css_decl_value_eq(pool, &decls->items[table[slot].index], later)) {
                decls->items[table[slot].index].dropped = 1;
            }
        } else {
            /* every longhand list begins with the shorthand's own name; that first word is a same-name duplicate
               (value-equality gated), the rest are strict-subset longhands the shorthand always resets */
            const char *cursor = list;
            int own_name = 1;
            while (*cursor) {
                const char *word = cursor;
                while (*cursor && *cursor != ' ') {
                    cursor++;
                }
                Py_ssize_t slot = dedup_slot_cstr(table, mask, pool, word, cursor - word, important);
                if (table[slot].used && !decls->items[table[slot].index].dropped &&
                    (!own_name || css_decl_value_eq(pool, &decls->items[table[slot].index], later))) {
                    decls->items[table[slot].index].dropped = 1;
                }
                while (*cursor == ' ') {
                    cursor++;
                }
                own_name = 0;
            }
        }
        Py_ssize_t home = dedup_slot_run(table, mask, pool, prop, prop_len, important);
        table[home].prop_off = later->prop_off;
        table[home].prop_len = prop_len;
        table[home].important = important;
        table[home].index = index;
        table[home].used = 1;
    }
    if (table != stack_table) {
        css_free(table);
    }
}

#endif /* TURBOHTML_CSS_SELECTOR_H */
