#ifndef TURBOHTML_CSS_GRAMMAR_H
#define TURBOHTML_CSS_GRAMMAR_H

static int css_decl_is_wide_keyword(const css_buf *pool, const css_decl *decl) {
    const css_char *value = pool->data + decl->val_off;
    Py_ssize_t len = decl->val_len;
    return css_run_ieq(value, len, "inherit") || css_run_ieq(value, len, "initial") ||
           css_run_ieq(value, len, "unset") || css_run_ieq(value, len, "revert") ||
           css_run_ieq(value, len, "revert-layer");
}

/* A value carrying a custom-property substitution cannot be hoisted into a shorthand: var()/env() expand at the
   shorthand level, where a multi-token or guaranteed-invalid expansion would change which sub-properties are set. */
static int css_decl_has_substitution(const css_buf *pool, const css_decl *decl) {
    const css_char *value = pool->data + decl->val_off;
    for (Py_ssize_t index = 0; index + 4 <= decl->val_len; index++) {
        if ((css_lower(value[index]) == 'v' && css_lower(value[index + 1]) == 'a' &&
             css_lower(value[index + 2]) == 'r' && value[index + 3] == '(') ||
            (css_lower(value[index]) == 'e' && css_lower(value[index + 1]) == 'n' &&
             css_lower(value[index + 2]) == 'v' && value[index + 3] == '(')) {
            return 1;
        }
    }
    return 0;
}

static Py_ssize_t css_find_active_decl(const decl_vec *decls, const css_buf *pool, const char *name) {
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        const css_decl *decl = &decls->items[index];
        if (!decl->dropped && !decl->nested && css_run_ieq(pool->data + decl->prop_off, decl->prop_len, name)) {
            return index;
        }
    }
    return -1;
}

static int css_count_active_prefixed(const decl_vec *decls, const css_buf *pool, const char *prefix, Py_ssize_t len) {
    int count = 0;
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        const css_decl *decl = &decls->items[index];
        if (decl->dropped || decl->nested || decl->prop_len < len) {
            continue;
        }
        int match = 1;
        for (Py_ssize_t pos = 0; pos < len; pos++) {
            if (css_lower(pool->data[decl->prop_off + pos]) != (css_char)(unsigned char)prefix[pos]) {
                match = 0;
                break;
            }
        }
        count += match;
    }
    return count;
}

/* Whether a value carries a space outside any parentheses, which marks a multi-part value such as an elliptical
   border-radius corner (``1px 2px``); a space inside ``calc(...)`` is not one. The only caller passes a length value,
   which nests solely with parentheses, so it does not track brackets. */
static int css_value_has_top_space(const css_buf *pool, const css_decl *decl) {
    int depth = 0;
    const css_char *value = pool->data + decl->val_off;
    for (Py_ssize_t index = 0; index < decl->val_len; index++) {
        css_char character = value[index];
        if (character == '(') {
            depth++;
        } else if (character == ')') {
            depth--;
        } else if (depth == 0 && css_is_ws(character)) {
            return 1;
        }
    }
    return 0;
}

/* Merge four longhands (top, right, bottom, left order) into their shorthand when it is value-safe: all four present
   and the only sub-properties of that prefix in the rule (so no logical margin-inline/-block to reorder), same
   importance, no var()/env() substitution, and -- if any is a CSS-wide keyword -- all four the identical keyword. A
   non-NULL forbidden list names sibling longhands (the logical border-radius corners) whose presence blocks the merge
   for the same reordering reason; single_value blocks it when any value is multi-part (an elliptical corner needs the
   ``/`` syntax). The shorthand replaces the last longhand and a re-dedup removes any now-redundant earlier one. */
static void css_merge_box(css_buf *pool, decl_vec *decls, const char *shorthand, const char *const longhands[4],
                          const char *prefix, const char *const *forbidden, int single_value) {
    Py_ssize_t idx[4];
    for (int edge = 0; edge < 4; edge++) {
        idx[edge] = css_find_active_decl(decls, pool, longhands[edge]);
        if (idx[edge] < 0) {
            return;
        }
    }
    if (prefix != NULL && css_count_active_prefixed(decls, pool, prefix, (Py_ssize_t)strlen(prefix)) != 4) {
        return; /* a NULL prefix (inset) needs no logical-sibling guard: its four longhands share no prefix */
    }
    for (int sibling = 0; forbidden != NULL && forbidden[sibling] != NULL; sibling++) {
        if (css_find_active_decl(decls, pool, forbidden[sibling]) >= 0) {
            return;
        }
    }
    for (int edge = 0; single_value && edge < 4; edge++) {
        if (css_value_has_top_space(pool, &decls->items[idx[edge]])) {
            return;
        }
    }
    int important = decls->items[idx[0]].important;
    for (int edge = 0; edge < 4; edge++) {
        if (decls->items[idx[edge]].important != important ||
            css_decl_has_substitution(pool, &decls->items[idx[edge]])) {
            return;
        }
    }
    int any_wide = 0;
    for (int edge = 0; edge < 4; edge++) {
        any_wide |= css_decl_is_wide_keyword(pool, &decls->items[idx[edge]]);
    }
    css_buf value = {NULL, 0, 0, 0};
    if (any_wide) {
        for (int edge = 0; edge < 4; edge++) {
            if (!css_decl_is_wide_keyword(pool, &decls->items[idx[edge]]) ||
                !css_decl_value_eq(pool, &decls->items[idx[edge]], &decls->items[idx[0]])) {
                return; /* a CSS-wide keyword cannot mix with other values in a shorthand */
            }
        }
        cbuf_put_run(&value, pool->data + decls->items[idx[0]].val_off, decls->items[idx[0]].val_len);
    } else {
        int kept = 4;
        if (css_decl_value_eq(pool, &decls->items[idx[3]], &decls->items[idx[1]])) {
            kept = 3;
        }
        if (kept == 3 && css_decl_value_eq(pool, &decls->items[idx[2]], &decls->items[idx[0]])) {
            kept = 2;
        }
        if (kept == 2 && css_decl_value_eq(pool, &decls->items[idx[1]], &decls->items[idx[0]])) {
            kept = 1;
        }
        for (int edge = 0; edge < kept; edge++) {
            if (edge > 0) {
                cbuf_putc(&value, ' ');
            }
            cbuf_put_run(&value, pool->data + decls->items[idx[edge]].val_off, decls->items[idx[edge]].val_len);
        }
    }
    Py_ssize_t last = idx[0];
    for (int edge = 1; edge < 4; edge++) {
        if (idx[edge] > last) {
            last = idx[edge];
        }
    }
    css_decl *merged = &decls->items[last];
    merged->prop_off = pool_cstr(pool, shorthand);
    merged->prop_len = (Py_ssize_t)strlen(shorthand);
    merged->val_off = pool_run(pool, value.data, value.len);
    merged->val_len = value.len;
    merged->important = important;
    for (int edge = 0; edge < 4; edge++) {
        if (idx[edge] != last) {
            decls->items[idx[edge]].dropped = 1;
        }
    }
    cbuf_free(&value);
    css_dedup(pool, decls);
}

/* Merge two longhands into a two-value shorthand (longhand0 then longhand1, collapsing to one value when both are
   equal), under the same value-safety gates as css_merge_box: both present, same importance, no var()/env(), and any
   CSS-wide keyword the identical sole value for both. A non-NULL forbidden list names siblings whose presence blocks
   the merge -- the physical longhand a logical one aliases (margin-inline-start resolves to margin-left or -right by
   writing mode), which the shorthand could reorder against. */
static void css_merge_pair(css_buf *pool, decl_vec *decls, const char *shorthand, const char *longhand0,
                           const char *longhand1, const char *const *forbidden) {
    Py_ssize_t idx0 = css_find_active_decl(decls, pool, longhand0);
    Py_ssize_t idx1 = css_find_active_decl(decls, pool, longhand1);
    if (idx0 < 0 || idx1 < 0) {
        return;
    }
    for (int sibling = 0; forbidden != NULL && forbidden[sibling] != NULL; sibling++) {
        if (css_find_active_decl(decls, pool, forbidden[sibling]) >= 0) {
            return;
        }
    }
    if (decls->items[idx0].important != decls->items[idx1].important ||
        css_decl_has_substitution(pool, &decls->items[idx0]) || css_decl_has_substitution(pool, &decls->items[idx1])) {
        return;
    }
    int wide0 = css_decl_is_wide_keyword(pool, &decls->items[idx0]);
    if (wide0 || css_decl_is_wide_keyword(pool, &decls->items[idx1])) {
        if (!wide0 || !css_decl_is_wide_keyword(pool, &decls->items[idx1]) ||
            !css_decl_value_eq(pool, &decls->items[idx0], &decls->items[idx1])) {
            return;
        }
    }
    css_buf value = {NULL, 0, 0, 0};
    cbuf_put_run(&value, pool->data + decls->items[idx0].val_off, decls->items[idx0].val_len);
    if (!css_decl_value_eq(pool, &decls->items[idx0], &decls->items[idx1])) {
        cbuf_putc(&value, ' ');
        cbuf_put_run(&value, pool->data + decls->items[idx1].val_off, decls->items[idx1].val_len);
    }
    Py_ssize_t last = idx0 > idx1 ? idx0 : idx1;
    css_decl *merged = &decls->items[last];
    merged->prop_off = pool_cstr(pool, shorthand);
    merged->prop_len = (Py_ssize_t)strlen(shorthand);
    merged->val_off = pool_run(pool, value.data, value.len);
    merged->val_len = value.len;
    merged->important = decls->items[idx0].important;
    decls->items[idx0 > idx1 ? idx1 : idx0].dropped = 1;
    cbuf_free(&value);
    css_dedup(pool, decls);
}

/* Merge three longhands into a space-joined shorthand (lh0 lh1 lh2, always three parts) under the same value-safety
   gates as css_merge_pair: all present, same importance, no var()/env(), and -- if any is a CSS-wide keyword -- all
   three the identical keyword. Used for outline, whose shorthand covers only width/style/color and resets nothing
   else, so no sibling can be reordered against it. */
static void css_merge_triple(css_buf *pool, decl_vec *decls, const char *shorthand, const char *lh0, const char *lh1,
                             const char *lh2) {
    Py_ssize_t idx[3] = {css_find_active_decl(decls, pool, lh0), css_find_active_decl(decls, pool, lh1),
                         css_find_active_decl(decls, pool, lh2)};
    if (idx[0] < 0 || idx[1] < 0 || idx[2] < 0) {
        return;
    }
    int important = decls->items[idx[0]].important;
    int any_wide = 0;
    for (int part = 0; part < 3; part++) {
        if (decls->items[idx[part]].important != important ||
            css_decl_has_substitution(pool, &decls->items[idx[part]])) {
            return;
        }
        any_wide |= css_decl_is_wide_keyword(pool, &decls->items[idx[part]]);
    }
    css_buf value = {NULL, 0, 0, 0};
    if (any_wide) {
        for (int part = 0; part < 3; part++) {
            if (!css_decl_is_wide_keyword(pool, &decls->items[idx[part]]) ||
                !css_decl_value_eq(pool, &decls->items[idx[part]], &decls->items[idx[0]])) {
                return;
            }
        }
        cbuf_put_run(&value, pool->data + decls->items[idx[0]].val_off, decls->items[idx[0]].val_len);
    } else {
        for (int part = 0; part < 3; part++) {
            if (part > 0) {
                cbuf_putc(&value, ' ');
            }
            cbuf_put_run(&value, pool->data + decls->items[idx[part]].val_off, decls->items[idx[part]].val_len);
        }
    }
    Py_ssize_t last = idx[0] > idx[1] ? idx[0] : idx[1];
    if (idx[2] > last) {
        last = idx[2];
    }
    css_decl *merged = &decls->items[last];
    merged->prop_off = pool_cstr(pool, shorthand);
    merged->prop_len = (Py_ssize_t)strlen(shorthand);
    merged->val_off = pool_run(pool, value.data, value.len);
    merged->val_len = value.len;
    merged->important = important;
    for (int part = 0; part < 3; part++) {
        if (idx[part] != last) {
            decls->items[idx[part]].dropped = 1;
        }
    }
    cbuf_free(&value);
    css_dedup(pool, decls);
}

static void css_merge_shorthands(css_buf *pool, decl_vec *decls, int baseline) {
    static const char *const margin[4] = {"margin-top", "margin-right", "margin-bottom", "margin-left"};
    static const char *const padding[4] = {"padding-top", "padding-right", "padding-bottom", "padding-left"};
    static const char *const inset[4] = {"top", "right", "bottom", "left"};
    static const char *const radius[4] = {"border-top-left-radius", "border-top-right-radius",
                                          "border-bottom-right-radius", "border-bottom-left-radius"};
    static const char *const radius_logical[] = {"border-start-start-radius", "border-start-end-radius",
                                                 "border-end-start-radius", "border-end-end-radius", NULL};
    css_merge_box(pool, decls, "margin", margin, "margin-", NULL, 0);
    css_merge_box(pool, decls, "padding", padding, "padding-", NULL, 0);
    /* border-radius takes the four corners in TL, TR, BR, BL order; a logical corner in the rule could reorder against
       the shorthand, and an elliptical corner is multi-part, so both block the merge. */
    css_merge_box(pool, decls, "border-radius", radius, NULL, radius_logical, 1);
    css_merge_triple(pool, decls, "outline", "outline-width", "outline-style", "outline-color");
    css_merge_pair(pool, decls, "flex-flow", "flex-direction", "flex-wrap", NULL);
    css_merge_pair(pool, decls, "place-content", "align-content", "justify-content", NULL);
    css_merge_pair(pool, decls, "place-items", "align-items", "justify-items", NULL);
    css_merge_pair(pool, decls, "place-self", "align-self", "justify-self", NULL);
    if (baseline >= 2021) {
        /* These shorthands reached Baseline in 2021, so emit them only when the caller targets that year or later.
           inset resets nothing else and shares no prefix, so it merges with no guard; each logical margin/padding/inset
           pair is blocked when its physical alias is in the rule, which the shorthand could reorder against. */
        static const char *const margin_inline_alias[] = {"margin-left", "margin-right", "margin", NULL};
        static const char *const margin_block_alias[] = {"margin-top", "margin-bottom", "margin", NULL};
        static const char *const padding_inline_alias[] = {"padding-left", "padding-right", "padding", NULL};
        static const char *const padding_block_alias[] = {"padding-top", "padding-bottom", "padding", NULL};
        static const char *const inset_inline_alias[] = {"left", "right", "inset", NULL};
        static const char *const inset_block_alias[] = {"top", "bottom", "inset", NULL};
        css_merge_box(pool, decls, "inset", inset, NULL, NULL, 0);
        css_merge_pair(pool, decls, "overflow", "overflow-x", "overflow-y", NULL);
        css_merge_pair(pool, decls, "gap", "row-gap", "column-gap", NULL);
        css_merge_pair(pool, decls, "margin-inline", "margin-inline-start", "margin-inline-end", margin_inline_alias);
        css_merge_pair(pool, decls, "margin-block", "margin-block-start", "margin-block-end", margin_block_alias);
        css_merge_pair(pool, decls, "padding-inline", "padding-inline-start", "padding-inline-end",
                       padding_inline_alias);
        css_merge_pair(pool, decls, "padding-block", "padding-block-start", "padding-block-end", padding_block_alias);
        css_merge_pair(pool, decls, "inset-inline", "inset-inline-start", "inset-inline-end", inset_inline_alias);
        css_merge_pair(pool, decls, "inset-block", "inset-block-start", "inset-block-end", inset_block_alias);
    }
}

static void css_render_declarations(css_buf *pool, decl_vec *decls, int baseline, css_buf *out) {
    css_dedup(pool, decls);
    css_merge_shorthands(pool, decls, baseline);
    int first = 1;
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *decl = &decls->items[index];
        if (decl->dropped) {
            continue;
        }
        if (!first) {
            cbuf_putc(out, ';');
        }
        first = 0;
        if (decl->nested) {
            cbuf_put_run(out, pool->data + decl->prop_off, decl->prop_len);
            continue;
        }
        cbuf_put_run(out, pool->data + decl->prop_off, decl->prop_len);
        cbuf_putc(out, ':');
        cbuf_put_run(out, pool->data + decl->val_off, decl->val_len);
        if (decl->important) {
            cbuf_puts(out, "!important");
        }
    }
}

static int css_is_nested_rule_at(const css_char *name, Py_ssize_t len) {
    return css_run_ieq(name, len, "@media") || css_run_ieq(name, len, "@supports") ||
           css_run_ieq(name, len, "@document") || css_run_ieq(name, len, "@-moz-document") ||
           css_run_ieq(name, len, "@container") || css_run_ieq(name, len, "@layer") || css_run_ieq(name, len, "@scope");
}

static void css_parse_declarations(css_buf *pool, cursor *cur, decl_vec *decls);
static void css_parse_rules(css_buf *pool, cursor *cur, int top, int keyframe, css_buf *out);

/* Render an at-rule prelude into out (a leading space unless it opens with '('). */
static void css_at_prelude(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, int allow_url_string,
                           css_buf *out) {
    Py_ssize_t mark = out->len;
    int pending_ws = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            /* a comment separates tokens: dropping it must leave a space so a commented-out `and (` does not glue
               into the function-token `and(` (an invalid media query), matching the declaration-value path */
            pending_ws = 1;
            continue;
        }
        if (token->kind == CSS_URL) {
            if (pending_ws && out->len > mark && out->data[out->len - 1] != '(' && out->data[out->len - 1] != ',') {
                cbuf_putc(out, ' ');
            }
            pending_ws = 0;
            if (!allow_url_string) {
                /* outside @import/@namespace the url() is part of a tested value (e.g. @supports (background:url(x))),
                   so minify it in place; unwrapping it to a string would change the condition the rule tests */
                Py_ssize_t off;
                Py_ssize_t len;
                css_minify_url(pool, token->text, token->text_len, &off, &len);
                cbuf_put_run(out, pool->data + off, len);
                continue;
            }
            /* an @import/@namespace url() keeps a quoted body, or wraps a bare body in quotes */
            if (token->text_len > 4 && token->text[token->text_len - 1] == ')') {
                Py_ssize_t body_start = 4;
                Py_ssize_t body_end = token->text_len - 1;
                while (body_start < body_end && css_is_ws(token->text[body_start])) {
                    body_start++;
                }
                while (body_end > body_start && css_is_ws(token->text[body_end - 1])) {
                    body_end--;
                }
                if (body_end > body_start && (token->text[body_start] == '"' || token->text[body_start] == '\'')) {
                    cbuf_put_run(out, token->text + body_start, body_end - body_start);
                } else {
                    cbuf_putc(out, '"');
                    cbuf_put_run(out, token->text + body_start, body_end - body_start);
                    cbuf_putc(out, '"');
                }
                continue;
            }
            cbuf_put_run(out, token->text, token->text_len);
            continue;
        }
        if (token->kind == CSS_DELIM && (token->delim == ':' || token->delim == ',')) {
            css_rtrim(out);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '(') {
            if (pending_ws && out->len > mark && out->data[out->len - 1] != '(' && out->data[out->len - 1] != ',') {
                cbuf_putc(out, ' ');
            }
            cbuf_putc(out, '(');
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == ')') {
            css_rtrim(out);
            cbuf_putc(out, ')');
            pending_ws = 0;
            continue;
        }
        /* `)and`/`)or` tokenizes the same as `) and`/`) or` (Syntax 3 §4: a ')' then an ident), so the space before a
           combinator after a ')' is dropped; the space *after* it is kept by the '(' branch above, since `and(` would
           otherwise be one function token. */
        int after_paren_combinator =
            out->len > mark && out->data[out->len - 1] == ')' && token->kind == CSS_IDENT &&
            (css_run_ieq(token->text, token->text_len, "and") || css_run_ieq(token->text, token->text_len, "or"));
        if (pending_ws && !after_paren_combinator && out->len > mark && out->data[out->len - 1] != '(' &&
            out->data[out->len - 1] != ',' && out->data[out->len - 1] != ':') {
            cbuf_putc(out, ' ');
        }
        pending_ws = 0;
        if (token->kind == CSS_NUM) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_format_dimension(pool, token, 1, &off, &len);
            cbuf_put_run(out, pool->data + off, len);
        } else {
            cbuf_put_run(out, token->text, token->text_len);
        }
    }
    css_rtrim(out);
    /* a non-empty prelude not opening with '(' is preceded by a single space */
    if (out->len > mark && out->data[mark] != '(') {
        cbuf_reserve(out, 1);
        memmove(out->data + mark + 1, out->data + mark, (size_t)(out->len - mark) * sizeof(css_char));
        out->data[mark] = ' ';
        out->len++;
    }
}

static void css_parse_at(css_buf *pool, cursor *cur, css_buf *out) {
    css_token *name_token = &cur->vec->items[cur->index];
    const css_char *name = name_token->text;
    Py_ssize_t name_len = name_token->text_len;
    cur->index++;
    int allow_url_string = css_run_ieq(name, name_len, "@import") || css_run_ieq(name, name_len, "@namespace");
    Py_ssize_t prelude_start = cur->index;
    Py_ssize_t prelude_end = css_read_until(cur, ";{}");
    css_token *token = cursor_peek(cur);
    /* css_read_until stops only on a stop DELIM (or EOF -> NULL peek), so a non-NULL peek is always that DELIM */
    if (token && token->delim == '{') {
        cur->index++;
        for (Py_ssize_t pos = 0; pos < name_len; pos++) {
            cbuf_putc(out, css_lower(name[pos]));
        }
        css_at_prelude(pool, cur->vec, prelude_start, prelude_end, allow_url_string, out);
        cbuf_putc(out, '{');
        int is_keyframes =
            css_run_ieq(name, name_len, "@keyframes") || css_run_ieq(name, name_len, "@-webkit-keyframes") ||
            css_run_ieq(name, name_len, "@-moz-keyframes") || css_run_ieq(name, name_len, "@-o-keyframes");
        if (is_keyframes || css_is_nested_rule_at(name, name_len)) {
            /* a @keyframes body is a rule list (its "selectors" are keyframe selectors), so parse it as rules -- not as
               a declaration list, which would wrongly join the keyframe blocks with ';' */
            css_parse_rules(pool, cur, 0, is_keyframes, out);
        } else {
            decl_vec decls = {NULL, 0, 0, 0};
            css_parse_declarations(pool, cur, &decls);
            css_render_declarations(pool, &decls, cur->baseline, out);
            css_free(decls.items);
        }
        cbuf_putc(out, '}');
        return;
    }
    if (token && token->delim == ';') {
        cur->index++;
    }
    for (Py_ssize_t pos = 0; pos < name_len; pos++) {
        cbuf_putc(out, css_lower(name[pos]));
    }
    css_at_prelude(pool, cur->vec, prelude_start, prelude_end, allow_url_string, out);
}

/* Parse a declaration list (between { }); appends declarations (and nested rules) to decls. */
static void css_parse_declarations(css_buf *pool, cursor *cur, decl_vec *decls) {
    comp_vec scratch = {NULL, 0, 0, 0}; /* reused across this list's values, freed once below */
    while (cur->index < cur->vec->len) {
        css_token *token = cursor_peek(cur);
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            cur->index++;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '}') {
            cur->index++;
            break;
        }
        if (token->kind == CSS_AT) {
            css_buf nested = {NULL, 0, 0, 0};
            css_parse_at(pool, cur, &nested);
            css_decl decl = {0};
            decl.prop_off = pool_run(pool, nested.data, nested.len);
            decl.prop_len = nested.len;
            decl.nested = 1;
            cbuf_free(&nested);
            decl_vec_push(decls, decl);
            continue;
        }
        Py_ssize_t segment_start = cur->index;
        Py_ssize_t segment_end = css_read_until(cur, ";{}");
        css_token *terminator = cursor_peek(cur);
        /* css_read_until stops only on a stop DELIM (or EOF -> NULL peek), so a non-NULL peek is always that DELIM */
        if (terminator && terminator->delim == '{') {
            cur->index++;
            decl_vec inner = {NULL, 0, 0, 0};
            css_parse_declarations(pool, cur, &inner);
            css_buf selector = {NULL, 0, 0, 0};
            css_minify_selector(cur->vec, segment_start, segment_end, 0, &selector);
            css_buf body = {NULL, 0, 0, 0};
            css_render_declarations(pool, &inner, cur->baseline, &body);
            css_decl decl = {0};
            decl.prop_off = pool->len;
            cbuf_put_run(pool, selector.data, selector.len);
            cbuf_putc(pool, '{');
            cbuf_put_run(pool, body.data, body.len);
            cbuf_putc(pool, '}');
            decl.prop_len = pool->len - decl.prop_off;
            decl.nested = 1;
            css_free(inner.items);
            cbuf_free(&selector);
            cbuf_free(&body);
            decl_vec_push(decls, decl);
            continue;
        }
        int closed_brace = terminator && terminator->delim == '}';
        /* the '{' terminator already returned above, so a non-NULL terminator here is always ';' or '}' */
        if (terminator) {
            cur->index++;
        }
        css_decl decl = {0};
        if (css_make_declaration(pool, cur->vec, segment_start, segment_end, &scratch, &decl)) {
            decl_vec_push(decls, decl);
        }
        if (closed_brace) {
            break;
        }
    }
    css_free(scratch.items);
}

/* A top-level node collected before serialization, so adjacent qualified rules can be merged. is_rule holds a
   selector + rendered body (no braces); otherwise it is opaque text (an at-rule, a bang comment, or stray recovery
   text), which breaks rule adjacency. at_statement marks an at-statement that needs a ';' before the next node. */
typedef struct {
    int is_rule;
    Py_ssize_t sel_off;
    Py_ssize_t sel_len;
    Py_ssize_t body_off;
    Py_ssize_t body_len;
    Py_ssize_t text_off;
    Py_ssize_t text_len;
    int at_statement;
    int dropped;
} rule_item;

typedef struct {
    rule_item *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} rule_vec;

static void rule_vec_push(rule_vec *vec, rule_item item) {
    if (vec->len == vec->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(vec->len + 1), (size_t)vec->cap, 16, sizeof(rule_item), &cap, &bytes);
        if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            vec->failed = 1; /* GCOVR_EXCL_LINE */
            return;          /* GCOVR_EXCL_LINE */
        }
        rule_item *grown = css_realloc(vec->items, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE */
            return;          /* GCOVR_EXCL_LINE */
        }
        vec->items = grown;
        vec->cap = (Py_ssize_t)cap;
    }
    vec->items[vec->len++] = item;
}

/* Parse a qualified rule into item (selector + rendered body), or recover a stray segment as opaque text. */
static void css_parse_qualified(css_buf *pool, cursor *cur, int keyframe, rule_item *item) {
    item->is_rule = 0;
    item->dropped = 0;
    item->at_statement = 0;
    item->text_off = 0;
    item->text_len = 0;
    Py_ssize_t prelude_start = cur->index;
    Py_ssize_t prelude_end = css_read_until(cur, "{};");
    css_token *token = cursor_peek(cur);
    /* css_read_until stops only on a stop DELIM (or EOF -> NULL peek), so a non-NULL peek is always that DELIM */
    if (token && token->delim == '{') {
        cur->index++;
        decl_vec decls = {NULL, 0, 0, 0};
        css_parse_declarations(pool, cur, &decls);
        css_buf body = {NULL, 0, 0, 0};
        css_render_declarations(pool, &decls, cur->baseline, &body);
        if (body.len > 0) {
            /* render the selector straight into the pool (it reads only the source tokens, never the pool scratch),
               which avoids a per-rule temporary buffer allocation; the body needs its own buffer because rendering it
               uses the pool as value scratch, so it is interned afterwards */
            item->is_rule = 1;
            item->sel_off = pool->len;
            css_minify_selector(cur->vec, prelude_start, prelude_end, keyframe, pool);
            item->sel_len = pool->len - item->sel_off;
            item->body_off = pool_run(pool, body.data, body.len);
            item->body_len = body.len;
        }
        css_free(decls.items);
        cbuf_free(&body);
        return;
    }
    if (token && token->delim == ';') {
        cur->index++;
    }
    /* a stray segment with no block: keep its trimmed text verbatim (error recovery). The caller (css_parse_rules)
       consumes leading whitespace/comments before dispatching here, so only the trailing edge needs trimming. */
    Py_ssize_t start = prelude_start;
    Py_ssize_t end = prelude_end;
    while (end > start && cur->vec->items[end - 1].kind == CSS_WS) {
        end--;
    }
    css_buf text = {NULL, 0, 0, 0};
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *piece = &cur->vec->items[index];
        cbuf_put_run(&text, piece->text, piece->text_len);
        if (piece->kind == CSS_NUM) {
            cbuf_put_run(&text, (piece->text + piece->text_len), piece->unit_len);
        }
    }
    item->text_off = pool_run(pool, text.data, text.len);
    item->text_len = text.len;
    cbuf_free(&text);
}

static int rule_run_eq(const css_buf *pool, Py_ssize_t a_off, Py_ssize_t a_len, Py_ssize_t b_off, Py_ssize_t b_len) {
    return a_len == b_len && memcmp(pool->data + a_off, pool->data + b_off, (size_t)a_len * sizeof(css_char)) == 0;
}

/* Re-minify "prev_body;it_body" so a same-selector merge dedups overlapping declarations the same way one rule would.
 */
static void css_merge_rule_bodies(css_buf *pool, rule_item *prev, const rule_item *it, int baseline) {
    css_buf combined = {NULL, 0, 0, 0};
    cbuf_put_run(&combined, pool->data + prev->body_off, prev->body_len);
    cbuf_putc(&combined, ';');
    cbuf_put_run(&combined, pool->data + it->body_off, it->body_len);
    token_vec tokens = {NULL, 0, 0, 0};
    css_tokenize(combined.data, combined.len, &tokens);
    cursor inner = {&tokens, 0, baseline};
    decl_vec decls = {NULL, 0, 0, 0};
    css_parse_declarations(pool, &inner, &decls);
    css_buf body = {NULL, 0, 0, 0};
    css_render_declarations(pool, &decls, baseline, &body);
    prev->body_off = pool_run(pool, body.data, body.len);
    prev->body_len = body.len;
    css_free(tokens.items);
    css_free(decls.items);
    cbuf_free(&body);
    cbuf_free(&combined);
}

/* For a rendered at-rule node, the length of an @media block's prelude through the first '{', or -1 when the node is
   not a mergeable @media block. @media is the only conditional-group rule merged here: @layer declares cascade order,
   and @supports/@keyframes/@font-face have no safe identical-prelude merge in this corpus. The char after "@media"
   must end the keyword (space, '(' or '{'), so "@media-foo" is excluded. */
static Py_ssize_t css_media_prelude_len(const css_char *text, Py_ssize_t len, int at_statement) {
    if (at_statement || len < 7 || memcmp(text, "@media", 6) != 0 ||
        (text[6] != ' ' && text[6] != '(' && text[6] != '{')) {
        return -1;
    }
    for (Py_ssize_t index = 6; index < len; index++) { /* GCOVR_EXCL_BR_LINE: a non-statement @media block has a '{' */
        if (text[index] == '{') {
            return index + 1;
        }
    }
    return -1; /* GCOVR_EXCL_LINE: a rendered @media block always carries its '{' */
}

/* Two properties conflict when one could override the other on an element: the same name, a shorthand and one of its
   longhands, or `all` (which resets every property). Conflict decides whether a declaration can be moved past another
   rule without changing the cascade. */
static int css_props_conflict(const css_char *a, Py_ssize_t a_len, const css_char *b, Py_ssize_t b_len) {
    if (css_run_ieq(a, a_len, "all") || css_run_ieq(b, b_len, "all")) {
        return 1;
    }
    if (a_len == b_len && memcmp(a, b, (size_t)a_len * sizeof(css_char)) == 0) {
        return 1;
    }
    const char *a_longhands = css_longhand_list(a, a_len);
    if (a_longhands != NULL && css_prop_in_list(b, b_len, a_longhands)) {
        return 1;
    }
    const char *b_longhands = css_longhand_list(b, b_len);
    return b_longhands != NULL && css_prop_in_list(a, a_len, b_longhands);
}

/* Advance *pos over one declaration of a rendered body [0,len), returning its property-name run [*start,*end) -- the
   ident up to the declaration's ':'. A property name holds no parenthesis, so the first ':' is always the separator;
   the value's terminating ';' is read at paren depth 0, since a ';' can sit inside an unquoted data URL. The callers
   have already excluded a body carrying a string or a nested rule, so no quote or brace state is tracked. Returns 0 at
   the end. */
static int css_body_next_prop(const css_char *body, Py_ssize_t len, Py_ssize_t *pos, Py_ssize_t *start,
                              Py_ssize_t *end) {
    if (*pos >= len) {
        return 0;
    }
    *start = *pos;
    Py_ssize_t index = *pos;
    while (body[index] != ':') {
        index++;
    }
    *end = index;
    int depth = 0;
    for (; index < len; index++) {
        css_char character = body[index];
        if (character == '(') {
            depth++;
        } else if (character == ')') {
            depth--;
        } else if (depth == 0 && character == ';') {
            break;
        }
    }
    *pos = index < len ? index + 1 : index;
    return 1;
}

/* A body the flat property scan cannot analyze: it nests a rule (`{`) or carries a string (a quote), inside which a
   ';' would be misread as a declaration boundary. Such a body is treated as conflicting, so it never moves. */
static int css_body_is_opaque(const css_char *body, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (body[index] == '{' || body[index] == '"' || body[index] == '\'') {
            return 1;
        }
    }
    return 0;
}

/* Whether moving one rendered body past another could change the cascade: either is opaque, or they set a conflicting
   property. */
static int css_bodies_conflict(const css_buf *pool, Py_ssize_t a_off, Py_ssize_t a_len, Py_ssize_t b_off,
                               Py_ssize_t b_len) {
    const css_char *a = pool->data + a_off;
    const css_char *b = pool->data + b_off;
    if (css_body_is_opaque(a, a_len) || css_body_is_opaque(b, b_len)) {
        return 1;
    }
    Py_ssize_t a_pos = 0;
    Py_ssize_t a_start = 0;
    Py_ssize_t a_end = 0;
    while (css_body_next_prop(a, a_len, &a_pos, &a_start, &a_end)) {
        Py_ssize_t b_pos = 0;
        Py_ssize_t b_start = 0;
        Py_ssize_t b_end = 0;
        while (css_body_next_prop(b, b_len, &b_pos, &b_start, &b_end)) {
            if (css_props_conflict(a + a_start, a_end - a_start, b + b_start, b_end - b_start)) {
                return 1;
            }
        }
    }
    return 0;
}

/* Merge qualified rules: same selector -> combine declaration bodies; identical body -> combine selectors into a list.
   A rule may merge with an earlier one across intervening rules, but only while every rule between them sets no
   property the moved body sets (so the cascade cannot change); an opaque node (a bang comment) or a conflicting rule
   ends the reach. Consecutive @media blocks with an identical prelude fold into one wrapper. */
static void css_merge_adjacent_rules(css_buf *pool, rule_vec *items, int baseline) {
    Py_ssize_t media_prev = -1;
    Py_ssize_t media_prev_prelude = 0;
    for (Py_ssize_t index = 0; index < items->len; index++) {
        rule_item *it = &items->items[index];
        if (!it->is_rule) {
            /* every non-rule item carries text (the push guard drops empty rules), so text_len is always positive */
            Py_ssize_t prelude = css_media_prelude_len(pool->data + it->text_off, it->text_len, it->at_statement);
            if (prelude < 0) {
                media_prev = -1;
                continue;
            }
            if (media_prev >= 0 && media_prev_prelude == prelude &&
                memcmp(pool->data + items->items[media_prev].text_off, pool->data + it->text_off, (size_t)prelude) ==
                    0) {
                /* drop the previous block's trailing '}' and this block's "@media ...{" prelude, fusing the bodies */
                rule_item *target = &items->items[media_prev];
                css_buf merged = {NULL, 0, 0, 0};
                cbuf_put_run(&merged, pool->data + target->text_off, target->text_len - 1);
                cbuf_put_run(&merged, pool->data + it->text_off + prelude, it->text_len - prelude);
                target->text_off = pool_run(pool, merged.data, merged.len);
                target->text_len = merged.len;
                cbuf_free(&merged);
                it->dropped = 1;
                continue;
            }
            media_prev = index;
            media_prev_prelude = prelude;
            continue;
        }
        media_prev = -1;
        /* Reach back for a rule to merge into, keeping the merged rule at the earlier position so this rule's body
           moves back. Each intervening rule that sets a property this body sets ends the reach; an opaque node does
           too. */
        for (Py_ssize_t back = index - 1; back >= 0; back--) {
            rule_item *target = &items->items[back];
            if (target->dropped) {
                continue;
            }
            if (!target->is_rule) {
                break;
            }
            if (rule_run_eq(pool, target->sel_off, target->sel_len, it->sel_off, it->sel_len)) {
                css_merge_rule_bodies(pool, target, it, baseline);
                it->dropped = 1;
                break;
            }
            if (rule_run_eq(pool, target->body_off, target->body_len, it->body_off, it->body_len)) {
                css_buf selector = {NULL, 0, 0, 0};
                cbuf_put_run(&selector, pool->data + target->sel_off, target->sel_len);
                cbuf_putc(&selector, ',');
                cbuf_put_run(&selector, pool->data + it->sel_off, it->sel_len);
                target->sel_off = pool_run(pool, selector.data, selector.len);
                target->sel_len = selector.len;
                cbuf_free(&selector);
                it->dropped = 1;
                break;
            }
            if (css_bodies_conflict(pool, target->body_off, target->body_len, it->body_off, it->body_len)) {
                break;
            }
        }
    }
}

/* An empty conditional group rule (@media/@supports/@container with a `{}` body) has no effect, so it is dropped. Other
   empty at-rules are kept: an empty @layer still declares its place in the cascade order, and an empty @keyframes still
   fires animation events, so removing either is observable. */
static int css_is_empty_conditional_atrule(const css_char *text, Py_ssize_t len) {
    if (text[len - 1] != '}' || text[len - 2] != '{') {
        return 0;
    }
    Py_ssize_t keyword = 1;
    /* the guard above proved the text ends with a `{}` body, so the scan always stops at the `{` before running off
       the end; no length bound is needed. */
    while (css_is_ident(text[keyword])) {
        keyword++;
    }
    return css_run_ieq(text + 1, keyword - 1, "media") || css_run_ieq(text + 1, keyword - 1, "supports") ||
           css_run_ieq(text + 1, keyword - 1, "container");
}

/* Parse a rule list, collecting nodes so adjacent rules can be merged, then serialize. At the top level, declarations
   between rules are stray text; nested (inside an at-block) a '}' ends the list. */
static void css_parse_rules(css_buf *pool, cursor *cur, int top, int keyframe, css_buf *out) {
    rule_vec items = {NULL, 0, 0, 0};
    while (cur->index < cur->vec->len) {
        css_token *token = cursor_peek(cur);
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            /* a comment token's text always starts with the slash-star the tokenizer matched */
            if (token->kind == CSS_COMMENT && token->text_len >= 3 && token->text[2] == '!') {
                /* a bang comment is a license/copyright banner kept byte-exact: its body (SPDX text, newlines,
                   alignment) must survive verbatim, so copy the whole token unchanged */
                rule_item item = {0};
                item.text_off = pool_run(pool, token->text, token->text_len);
                item.text_len = token->text_len;
                rule_vec_push(&items, item);
            }
            cur->index++;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '}') {
            cur->index++;
            if (!top) {
                break;
            }
            continue;
        }
        if (token->kind == CSS_AT) {
            css_buf piece = {NULL, 0, 0, 0};
            css_parse_at(pool, cur, &piece);
            /* css_parse_at always emits at least the lowercased at-rule name, so piece is never empty */
            if (!css_is_empty_conditional_atrule(piece.data, piece.len)) {
                rule_item item = {0};
                item.text_off = pool_run(pool, piece.data, piece.len);
                item.text_len = piece.len;
                /* an at-statement produced no block: detect by the absence of a closing '}' */
                item.at_statement = piece.data[piece.len - 1] != '}';
                rule_vec_push(&items, item);
            }
            cbuf_free(&piece);
        } else {
            rule_item item = {0};
            css_parse_qualified(pool, cur, keyframe, &item);
            if (item.is_rule || item.text_len > 0) {
                rule_vec_push(&items, item);
            }
        }
    }
    css_merge_adjacent_rules(pool, &items, cur->baseline);
    int prev_at_statement = 0;
    for (Py_ssize_t index = 0; index < items.len; index++) {
        rule_item *item = &items.items[index];
        if (item->dropped) {
            continue;
        }
        if (prev_at_statement) {
            cbuf_putc(out, ';');
        }
        if (item->is_rule) {
            cbuf_put_run(out, pool->data + item->sel_off, item->sel_len);
            cbuf_putc(out, '{');
            cbuf_put_run(out, pool->data + item->body_off, item->body_len);
            cbuf_putc(out, '}');
            prev_at_statement = 0;
        } else {
            cbuf_put_run(out, pool->data + item->text_off, item->text_len);
            prev_at_statement = item->at_statement;
        }
    }
    css_free(items.items);
}

#endif /* TURBOHTML_CSS_GRAMMAR_H */
