#ifndef TURBOHTML_CSS_GRAMMAR_H
#define TURBOHTML_CSS_GRAMMAR_H

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
    /* name is a lower-case longhand literal; a declaration whose lowered first byte differs cannot match, so this
       skips the css_run_ieq call for every declaration that is not this longhand (nearly all of them). */
    css_char first = (css_char)(unsigned char)name[0];
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        const css_decl *decl = &decls->items[index];
        if (!decl->dropped && css_lower(pool->data[decl->prop_off]) == first &&
            css_run_ieq(pool->data + decl->prop_off, decl->prop_len, name)) {
            return index;
        }
    }
    return -1;
}

static int css_has_repeated_active_decl(const decl_vec *decls, const css_buf *pool, const char *name,
                                        Py_ssize_t first) {
    /* A later value may be unsupported, so both declarations must survive. */
    for (Py_ssize_t index = first + 1; index < decls->len; index++) {
        const css_decl *decl = &decls->items[index];
        if (!decl->dropped && css_lower(pool->data[decl->prop_off]) == (css_char)(unsigned char)name[0] &&
            css_run_ieq(pool->data + decl->prop_off, decl->prop_len, name)) {
            return 1;
        }
    }
    return 0;
}

static int css_count_active_prefixed(const decl_vec *decls, const css_buf *pool, const char *prefix, Py_ssize_t len) {
    int count = 0;
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        const css_decl *decl = &decls->items[index];
        if (decl->dropped || decl->prop_len < len) {
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

static int css_shorthand_between(const decl_vec *decls, const css_buf *pool, const char *name, Py_ssize_t first,
                                 Py_ssize_t last) {
    for (Py_ssize_t index = first + 1; index < last; index++) {
        const css_decl *decl = &decls->items[index];
        if (!decl->dropped && css_run_ieq(pool->data + decl->prop_off, decl->prop_len, name)) {
            return 1;
        }
    }
    return 0;
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

static int css_box_edge_supported(const css_buf *pool, const css_decl *decl, int padding);

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
    if (decls->check_repeats) {
        for (int edge = 0; edge < 4; edge++) {
            if (css_has_repeated_active_decl(decls, pool, longhands[edge], idx[edge])) {
                return;
            }
        }
    }
    Py_ssize_t first = idx[0];
    Py_ssize_t last = idx[0];
    for (int edge = 1; edge < 4; edge++) {
        if (idx[edge] < first) {
            first = idx[edge];
        }
        if (idx[edge] > last) {
            last = idx[edge];
        }
    }
    if (css_shorthand_between(decls, pool, shorthand, first, last)) {
        return;
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
    css_buf value = {NULL, 0, 0, pool->oom};
    if (any_wide) {
        for (int edge = 0; edge < 4; edge++) {
            if (!css_decl_is_wide_keyword(pool, &decls->items[idx[edge]]) ||
                !css_decl_value_eq(pool, &decls->items[idx[edge]], &decls->items[idx[0]])) {
                return; /* a CSS-wide keyword cannot mix with other values in a shorthand */
            }
        }
        cbuf_put_run(&value, pool->data + decls->items[idx[0]].val_off, decls->items[idx[0]].val_len);
    } else {
        for (int edge = 0; edge < 4; edge++) {
            if (!css_box_edge_supported(pool, &decls->items[idx[edge]], shorthand[0] == 'p' || shorthand[0] == 'b')) {
                return;
            }
        }
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

/* An invalid edge would invalidate the shorthand and discard its valid edges (CSS Box 3 §3/§4). */
CSS_NOINLINE static int css_box_edge_supported(const css_buf *pool, const css_decl *decl, int padding) {
    const css_char *value = pool->data + decl->val_off;
    if (!padding && css_run_ieq(value, decl->val_len, "auto")) {
        return 1;
    }
    if (!css_starts_number(value, 0, decl->val_len) || (padding && value[0] == '-')) {
        return 0;
    }
    Py_ssize_t number_end = css_scan_number(value, 0, decl->val_len);
    if (number_end == decl->val_len) {
        return decl->val_len == 1 && value[0] == '0';
    }
    return css_run_ieq(value + number_end, decl->val_len - number_end, "%") ||
           css_unit_zero_droppable(value + number_end, decl->val_len - number_end);
}

static int css_decl_keyword_in(const css_buf *pool, const css_decl *decl, const char *const *keywords) {
    for (int index = 0; keywords[index] != NULL; index++) {
        if (css_run_ieq(pool->data + decl->val_off, decl->val_len, keywords[index])) {
            return 1;
        }
    }
    return 0;
}

static int css_pair_value_safe(const css_buf *pool, const css_decl *decl, const char *shorthand, int part) {
    static const char *const flex_direction[] = {"row", "row-reverse", "column", "column-reverse", NULL};
    static const char *const flex_wrap[] = {"nowrap", "wrap", "wrap-reverse", NULL};
    static const char *const align_content[] = {"normal",       "stretch",      "center",   "start",
                                                "end",          "flex-start",   "flex-end", "space-between",
                                                "space-around", "space-evenly", "baseline", NULL};
    static const char *const justify_content[] = {
        "normal",        "stretch",      "center",       "start", "end",   "flex-start", "flex-end",
        "space-between", "space-around", "space-evenly", "left",  "right", NULL};
    static const char *const items[] = {"normal",   "stretch",    "center",   "start",    "end", "self-start",
                                        "self-end", "flex-start", "flex-end", "baseline", NULL};
    static const char *const self[] = {"auto",       "normal",   "stretch",    "center",   "start",    "end",
                                       "self-start", "self-end", "flex-start", "flex-end", "baseline", NULL};
    static const char *const overflow[] = {"visible", "hidden", "clip", "scroll", "auto", NULL};
    if (strcmp(shorthand, "flex-flow") == 0) {
        return css_decl_keyword_in(pool, decl, part == 0 ? flex_direction : flex_wrap);
    }
    if (strcmp(shorthand, "place-content") == 0) {
        return css_decl_keyword_in(pool, decl, part == 0 ? align_content : justify_content);
    }
    if (strcmp(shorthand, "place-items") == 0 || strcmp(shorthand, "place-self") == 0) {
        const char *const *keywords = strcmp(shorthand, "place-items") == 0 ? items : self;
        return css_decl_keyword_in(pool, decl, keywords) ||
               (part == 1 && (css_run_ieq(pool->data + decl->val_off, decl->val_len, "left") ||
                              css_run_ieq(pool->data + decl->val_off, decl->val_len, "right") ||
                              (strcmp(shorthand, "place-items") == 0 &&
                               css_run_ieq(pool->data + decl->val_off, decl->val_len, "legacy"))));
    }
    if (strcmp(shorthand, "overflow") == 0) {
        return css_decl_keyword_in(pool, decl, overflow);
    }
    if (strcmp(shorthand, "gap") == 0) {
        return css_run_ieq(pool->data + decl->val_off, decl->val_len, "normal") ||
               css_box_edge_supported(pool, decl, 1);
    }
    return css_box_edge_supported(pool, decl, shorthand[0] == 'p');
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
    if (decls->check_repeats && (css_has_repeated_active_decl(decls, pool, longhand0, idx0) ||
                                 css_has_repeated_active_decl(decls, pool, longhand1, idx1))) {
        return;
    }
    if (css_shorthand_between(decls, pool, shorthand, idx0 < idx1 ? idx0 : idx1, idx0 > idx1 ? idx0 : idx1)) {
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
    } else if (!css_pair_value_safe(pool, &decls->items[idx0], shorthand, 0) ||
               !css_pair_value_safe(pool, &decls->items[idx1], shorthand, 1)) {
        return;
    }
    css_buf value = {NULL, 0, 0, pool->oom};
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

static int css_outline_color_safe(const css_buf *pool, const css_decl *decl) {
    const css_char *value = pool->data + decl->val_off;
    Py_ssize_t len = decl->val_len;
    if (css_run_ieq(value, len, "currentcolor")) {
        return 1;
    }
    if (value[0] == '#' && (len == 4 || len == 5 || len == 7 || len == 9)) {
        for (Py_ssize_t index = 1; index < len; index++) {
            if (!css_is_hex(value[index])) {
                return 0;
            }
        }
        return 1;
    }
    for (int index = 0; index < th_css_hex_count; index++) {
        const th_css_color_entry *entry = &th_css_hex_to_name[index];
        if (entry->val_len == len && css_run_ieq(value, len, entry->val)) {
            return 1;
        }
    }
    return 0;
}

static int css_outline_value_safe(const css_buf *pool, const css_decl *decl, int part) {
    static const char *const widths[] = {"thin", "medium", "thick", NULL};
    static const char *const styles[] = {"auto",   "none",  "dotted", "dashed", "solid", "double",
                                         "groove", "ridge", "inset",  "outset", NULL};
    if (part == 0) {
        return css_decl_keyword_in(pool, decl, widths) ||
               (pool->data[decl->val_off + decl->val_len - 1] != '%' && css_box_edge_supported(pool, decl, 1));
    }
    return part == 1 ? css_decl_keyword_in(pool, decl, styles) : css_outline_color_safe(pool, decl);
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
    if (decls->check_repeats && (css_has_repeated_active_decl(decls, pool, lh0, idx[0]) ||
                                 css_has_repeated_active_decl(decls, pool, lh1, idx[1]) ||
                                 css_has_repeated_active_decl(decls, pool, lh2, idx[2]))) {
        return;
    }
    Py_ssize_t first = idx[0] < idx[1] ? idx[0] : idx[1];
    Py_ssize_t last = idx[0] > idx[1] ? idx[0] : idx[1];
    if (idx[2] < first) {
        first = idx[2];
    }
    if (idx[2] > last) {
        last = idx[2];
    }
    if (css_shorthand_between(decls, pool, shorthand, first, last)) {
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
    css_buf value = {NULL, 0, 0, pool->oom};
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
            if (!css_outline_value_safe(pool, &decls->items[idx[part]], part)) {
                cbuf_free(&value);
                return;
            }
            if (part > 0) {
                cbuf_putc(&value, ' ');
            }
            cbuf_put_run(&value, pool->data + decls->items[idx[part]].val_off, decls->items[idx[part]].val_len);
        }
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

enum { CSS_DECL_NORMAL, CSS_DECL_FONT_VALUES, CSS_DECL_FONT_FEATURE };

static void css_render_declarations(css_buf *pool, decl_vec *decls, int baseline, css_buf *out, int mode) {
    /* a declaration parsed after a failed growth may point at pool text that was never written */
    if (*pool->oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (mode != CSS_DECL_FONT_FEATURE) {
        Py_ssize_t start = 0;
        for (Py_ssize_t index = 0; index <= decls->len; index++) {
            if (index < decls->len && !decls->items[index].nested) {
                continue;
            }
            if (index > start) {
                decl_vec run = {decls->items + start, index - start, index - start, pool->oom, 0};
                css_dedup(pool, &run);
                css_merge_shorthands(pool, &run, baseline);
            }
            start = index + 1;
        }
    }
    /* a shorthand merged after a failed growth points at the pool's start, which holds at least the longhands it
       replaced, so the merges read inside the pool; the render must not copy it */
    if (*pool->oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int first = 1;
    int previous_block = 0;
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *decl = &decls->items[index];
        if (decl->dropped) {
            continue;
        }
        if (!first && (mode != CSS_DECL_FONT_VALUES || !previous_block)) {
            cbuf_putc(out, ';');
        }
        first = 0;
        previous_block = decl->nested && !decl->at_statement;
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
           css_run_ieq(name, len, "@container") || css_run_ieq(name, len, "@layer") ||
           css_run_ieq(name, len, "@scope") || css_run_ieq(name, len, "@starting-style");
}

static int css_is_font_feature_block_at(const css_char *name, Py_ssize_t len) {
    return css_run_ieq(name, len, "@stylistic") || css_run_ieq(name, len, "@historical-forms") ||
           css_run_ieq(name, len, "@styleset") || css_run_ieq(name, len, "@character-variant") ||
           css_run_ieq(name, len, "@swash") || css_run_ieq(name, len, "@ornaments") ||
           css_run_ieq(name, len, "@annotation");
}

static void css_parse_declarations(css_buf *pool, cursor *cur, decl_vec *decls, int preserve_names);
static void css_parse_rules(css_buf *pool, cursor *cur, int top, int keyframe, int media, css_buf *out);

/* Whether an @import/@namespace url() carries a <url-modifier> after its URL (CSS Values 4 §4.5.4): any content beyond
   trailing whitespace past the quoted or bare URL. text[start, end) is the url()'s whitespace-trimmed interior. A url()
   with a modifier keeps its function form, since the @import grammar (CSS Cascade 5 §3.1) reads a token moved outside
   the url() as a media query, and @namespace reads it as the prefix. */
static int css_url_has_modifier(const css_char *text, Py_ssize_t start, Py_ssize_t end) {
    if (start >= end) {
        return 0;
    }
    css_char first = text[start];
    if (first == '"' || first == '\'') {
        /* th_minify_css_bytes closes a string the input left open, so a quoted body's closing quote is present; a body
           that ends on it is the bare URL, anything after it (a modifier) leaves a different last byte */
        return text[end - 1] != first;
    }
    for (Py_ssize_t index = start; index < end; index++) {
        if (css_is_ws(text[index])) {
            return 1;
        }
    }
    return 0;
}

/* Whether an @import/@namespace url() token prints as its quoted body, setting [*body_start, *body_end) to the body
   with its whitespace trimmed: it does unless a <url-modifier> follows the URL. th_minify_css_bytes closed a url the
   input left open, so the text ends in its `)`. */
static int css_url_unwraps(const css_token *token, Py_ssize_t *body_start, Py_ssize_t *body_end) {
    *body_start = 4;
    *body_end = token->text_len - 1;
    while (*body_start < *body_end && css_is_ws(token->text[*body_start])) {
        (*body_start)++;
    }
    while (*body_end > *body_start && css_is_ws(token->text[*body_end - 1])) {
        (*body_end)--;
    }
    return !css_url_has_modifier(token->text, *body_start, *body_end);
}

/* Whether the space before prelude token index can go because a closed string sits on one side of it: a string ends at
   its quote and nothing reads past one (CSS Syntax 3 §4.3.5). The token before, past whitespace and comments, printed a
   closed string when it is a string token ending in its quote or an @import/@namespace url() printed as its body; a
   string a newline cut short keeps the space after it, the newline having ended it. The caller has written output for
   an earlier token, so the scan back stops on one. Kept out of line: only a space next to a quote reaches it. */
CSS_NOINLINE static int css_prelude_space_optional(const token_vec *vec, Py_ssize_t index, int allow_url_string) {
    Py_ssize_t prev = index - 1;
    while (vec->items[prev].kind == CSS_WS || vec->items[prev].kind == CSS_COMMENT) {
        prev--;
    }
    const css_token *before = &vec->items[prev];
    if (before->kind == CSS_STR) {
        return css_string_closed(before->text, before->text_len);
    }
    Py_ssize_t body_start;
    Py_ssize_t body_end;
    if (before->kind == CSS_URL && allow_url_string && css_url_unwraps(before, &body_start, &body_end)) {
        return 1;
    }
    return vec->items[index].kind == CSS_STR;
}

/* Render an at-rule prelude into out: a leading space unless it opens with '(' or a string, where only @charset keeps
   one, since a stylesheet's encoding is sniffed from the exact bytes `@charset "` (CSS Syntax 3 §3.2). */
static void css_at_prelude(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, int allow_url_string,
                           const css_char *name, Py_ssize_t name_len, css_buf *out) {
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
            /* outside @import/@namespace the url() is part of a tested value (e.g. @supports (background:url(x))), so
               it is minified in place: unwrapping it to a string would change the condition the rule tests */
            Py_ssize_t body_start;
            Py_ssize_t body_end;
            int unwrap = allow_url_string && css_url_unwraps(token, &body_start, &body_end);
            css_char last = out->len > mark ? out->data[out->len - 1] : '(';
            if (pending_ws && !unwrap && last != '(' && last != ',' &&
                !((last == '"' || last == '\'') && css_prelude_space_optional(vec, index, allow_url_string))) {
                cbuf_putc(out, ' ');
            }
            pending_ws = 0;
            if (!unwrap) {
                Py_ssize_t off;
                Py_ssize_t len;
                css_minify_url(pool, token->text, token->text_len, &off, &len);
                cbuf_put_run(out, pool->data + off, len);
                continue;
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
        if (token->kind == CSS_DELIM && (token->delim == ':' || token->delim == ',')) {
            css_rtrim(out);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '(') {
            if (pending_ws && out->len > mark && out->data[out->len - 1] != '(' && out->data[out->len - 1] != ',' &&
                !((out->data[out->len - 1] == '"' || out->data[out->len - 1] == '\'') &&
                  css_prelude_space_optional(vec, index, allow_url_string))) {
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
        const css_char *next_text = token->text;
        Py_ssize_t next_len = token->text_len;
        if (token->kind == CSS_NUM) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_format_dimension(pool, token, 1, NULL, 0, &off, &len);
            next_text = pool->data + off;
            next_len = len;
        }
        int after_paren_combinator =
            out->len > mark && out->data[out->len - 1] == ')' && token->kind == CSS_IDENT &&
            (css_run_ieq(token->text, token->text_len, "and") || css_run_ieq(token->text, token->text_len, "or"));
        int spaced = pending_ws && !after_paren_combinator && out->len > mark && out->data[out->len - 1] != '(' &&
                     out->data[out->len - 1] != ',' && out->data[out->len - 1] != ':' &&
                     !((out->data[out->len - 1] == '"' || out->data[out->len - 1] == '\'' || token->kind == CSS_STR) &&
                       css_prelude_space_optional(vec, index, allow_url_string));
        /* with no source whitespace, shortening a number (dropping a zero unit or sign) can glue it onto the previous
           token to read as one different token (CSS Syntax 3 §9.1), so keep the boundary the value path keeps */
        if (spaced || (out->len > mark && css_would_merge(out->data[out->len - 1], 0, next_text, next_len))) {
            cbuf_putc(out, ' ');
        }
        pending_ws = 0;
        cbuf_put_run(out, next_text, next_len);
    }
    css_rtrim(out);
    if (out->len <= mark) {
        return;
    }
    if (out->data[mark] == '(') {
        return;
    }
    switch (out->data[mark]) {
    case '"':
    case '\'':
        if (!css_run_ieq(name, name_len, "@charset")) {
            return;
        }
        break;
    default:
        break;
    }
    if (!cbuf_reserve(out, 1)) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memmove(out->data + mark + 1, out->data + mark, (size_t)(out->len - mark) * sizeof(css_char));
    out->data[mark] = ' ';
    out->len++;
}

/* Write a closer for each block a statement at-rule's prelude leaves open at the end of input, where consuming a simple
   block returns it (CSS Syntax 3 §5.5.9); the `;` ending the stylesheet would otherwise land inside the block. A closer
   ends only a block of its kind, as css_collect_open_blocks matches them, unless the prelude holds a `{`: then each
   closer ends the innermost block of any kind, as css_read_until nests them, since a `}` written for a `{` that a `)`
   already closed would end the reparsed prelude early. Kept out of line: only an at-rule ending the input reaches it.
 */
CSS_NOINLINE static void css_close_prelude_blocks(const token_vec *vec, Py_ssize_t start, Py_ssize_t end,
                                                  css_buf *out) {
    int braced = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        braced |= vec->items[index].delim == '{';
    }
    css_buf blocks = {NULL, 0, 0, vec->oom};
    for (Py_ssize_t index = start; index < end; index++) {
        css_char delim = vec->items[index].delim;
        if (delim == '(' || delim == '[' || delim == '{') {
            cbuf_putc(&blocks, delim);
        } else if ((delim == ')' || delim == ']' || delim == '}') && blocks.len > 0 &&
                   (braced || blocks.data[blocks.len - 1] == (delim == ')' ? '(' : delim - 2))) {
            /* `[` and `{` sit two code points before their closers, `(` one */
            blocks.len--;
        }
    }
    for (Py_ssize_t depth = blocks.len - 1; depth >= 0; depth--) {
        cbuf_putc(out, blocks.data[depth] == '(' ? ')' : blocks.data[depth] + 2);
    }
    cbuf_free(&blocks);
}

/* Render the at-rule at the cursor into out. Returns -1 for a statement at-rule, the length of an @media block's header
   (name, prelude and '{'), or 0 for another at-rule with a block. */
static Py_ssize_t css_parse_at(css_buf *pool, cursor *cur, css_buf *out, int in_declarations) {
    Py_ssize_t media_head = 0;
    if (cur->index == cur->media_at) {
        /* out is the caller's fresh piece buffer, so it can take over the prelude css_media_continues rendered */
        *out = cur->media_next;
        cur->media_next = (css_buf){NULL, 0, 0, pool->oom};
        cur->index = cur->media_body;
        media_head = out->len;
        css_parse_rules(pool, cur, 0, 0, 1, out);
        cbuf_putc(out, '}');
        return media_head;
    }
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
        css_at_prelude(pool, cur->vec, prelude_start, prelude_end, allow_url_string, name, name_len, out);
        cbuf_putc(out, '{');
        int is_keyframes =
            css_run_ieq(name, name_len, "@keyframes") || css_run_ieq(name, name_len, "@-webkit-keyframes") ||
            css_run_ieq(name, name_len, "@-moz-keyframes") || css_run_ieq(name, name_len, "@-o-keyframes");
        if ((is_keyframes || css_is_nested_rule_at(name, name_len)) &&
            (!in_declarations || !css_run_ieq(name, name_len, "@starting-style"))) {
            /* a @keyframes body is a rule list (its "selectors" are keyframe selectors), so parse it as rules -- not as
               a declaration list, which would wrongly join the keyframe blocks with ';'. For @media, out holds the
               rendered prelude, so the inner parse pulls in the rules of an immediately following same-query @media
               (#1065), merging them in this pass instead of leaving adjacent blocks for the next call. */
            media_head = css_run_ieq(name, name_len, "@media") ? out->len : 0;
            css_parse_rules(pool, cur, 0, is_keyframes, media_head > 0, out);
        } else {
            decl_vec decls = {NULL, 0, 0, pool->oom, 0};
            int font_feature_block = css_is_font_feature_block_at(name, name_len);
            css_parse_declarations(pool, cur, &decls, font_feature_block);
            css_render_declarations(pool, &decls, cur->baseline, out,
                                    font_feature_block                                    ? CSS_DECL_FONT_FEATURE
                                    : css_run_ieq(name, name_len, "@font-feature-values") ? CSS_DECL_FONT_VALUES
                                                                                          : CSS_DECL_NORMAL);
            css_free(decls.items);
        }
        cbuf_putc(out, '}');
        return media_head;
    }
    if (token && token->delim == ';') {
        cur->index++;
    }
    for (Py_ssize_t pos = 0; pos < name_len; pos++) {
        cbuf_putc(out, css_lower(name[pos]));
    }
    css_at_prelude(pool, cur->vec, prelude_start, prelude_end, allow_url_string, name, name_len, out);
    if (token == NULL) {
        css_close_prelude_blocks(cur->vec, prelude_start, prelude_end, out);
    }
    return -1;
}

/* Skip the rest of a block whose '{' the cursor has passed, through its closing '}'. Out of line: only a dropped or
   over-nested block reaches it, and its own copy of css_read_until would keep LTO from inlining the one that reads
   each declaration. */
CSS_NOINLINE static void css_skip_block(cursor *cur) {
    css_read_until(cur, "}");
    if (cur->index < cur->vec->len) {
        cur->index++;
    }
}

/* Skip a block whose '{' the cursor has passed when a bad token invalidates it: one in its prelude [start, end), which
   no selector admits, or one inside the block when the prelude ends in ':'. Such a prelude (`color:{...}`; a custom
   property's block never gets here) is no selector either, so the block reads as a declaration value, which a bad token
   invalidates as a whole. Returns whether it skipped. Out of line: only input with a bad token reaches it. */
CSS_NOINLINE static int css_skip_bad_nested_block(cursor *cur, Py_ssize_t start, Py_ssize_t end) {
    int bad = css_holds_bad(cur->vec, start, end);
    if (!bad && start < end) {
        /* the caller skips leading whitespace and comments, so the backward scan stops at start at the latest */
        Py_ssize_t last = end - 1;
        while (cur->vec->items[last].kind == CSS_WS || cur->vec->items[last].kind == CSS_COMMENT) {
            last--;
        }
        if (cur->vec->items[last].kind == CSS_DELIM && cur->vec->items[last].delim == ':') {
            cursor block = *cur;
            css_skip_block(&block);
            bad = css_holds_bad(cur->vec, cur->index, block.index);
        }
    }
    if (bad) {
        css_skip_block(cur);
    }
    return bad;
}

/* Whether the item at index opens with a custom property name and a colon, which CSS Syntax 3 §5.5.5 makes a
   declaration that is never reparsed as a nested rule. The caller found a `{` after the item's first token, so the
   colon scan past a name stops inside the vector. */
static int css_starts_custom_property(const token_vec *vec, Py_ssize_t index) {
    const css_token *name = &vec->items[index];
    if (name->kind != CSS_IDENT) {
        return 0;
    }
    Py_ssize_t colon = index + 1;
    while (vec->items[colon].kind == CSS_WS || vec->items[colon].kind == CSS_COMMENT) {
        colon++;
    }
    return vec->items[colon].delim == ':' && css_name_is_custom(name->text);
}

/* Parse a declaration list (between { }); appends declarations (and nested rules) to decls. */
static void css_parse_declarations(css_buf *pool, cursor *cur, decl_vec *decls, int preserve_names) {
    if (!css_nesting_enter(cur->vec)) {
        /* over-nested: drop this block's remaining declarations rather than recurse into a C stack overflow */
        css_skip_block(cur);
        return;
    }
    comp_vec scratch = {NULL, 0, 0, pool->oom}; /* reused across this list's values, freed once below */
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
            css_buf nested = {NULL, 0, 0, pool->oom};
            Py_ssize_t kind = css_parse_at(pool, cur, &nested, 1);
            css_decl decl = {0};
            decl.prop_off = pool_run(pool, nested.data, nested.len);
            decl.prop_len = nested.len;
            decl.nested = 1;
            decl.at_statement = kind < 0;
            cbuf_free(&nested);
            decl_vec_push(decls, decl);
            continue;
        }
        Py_ssize_t segment_start = cur->index;
        Py_ssize_t segment_end = css_read_until(cur, ";{}");
        css_token *terminator = cursor_peek(cur);
        /* css_read_until stops only on a stop DELIM (or EOF -> NULL peek), so a non-NULL peek is always that DELIM */
        if (terminator && terminator->delim == '{' && css_starts_custom_property(cur->vec, segment_start)) {
            /* a custom property's value is any token sequence, {} blocks included (CSS Variables 1 §2), so the `{`
               opens a block inside the value */
            segment_end = css_read_until(cur, ";}");
            terminator = cursor_peek(cur);
        }
        if (terminator && terminator->delim == '{') {
            cur->index++;
            if (cur->vec->bad && css_skip_bad_nested_block(cur, segment_start, segment_end)) {
                continue;
            }
            decl_vec inner = {NULL, 0, 0, pool->oom, 0};
            css_parse_declarations(pool, cur, &inner, 0);
            css_buf selector = {NULL, 0, 0, pool->oom};
            css_minify_selector(cur->vec, segment_start, segment_end, 0, &selector);
            css_buf body = {NULL, 0, 0, pool->oom};
            css_render_declarations(pool, &inner, cur->baseline, &body, CSS_DECL_NORMAL);
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
        if (css_make_declaration(pool, cur->vec, segment_start, segment_end, &scratch, &decl, preserve_names)) {
            decl_vec_push(decls, decl);
        }
        if (closed_brace) {
            break;
        }
    }
    css_free(scratch.items);
    css_nesting_leave(cur->vec);
}

/* A top-level node collected before serialization, so adjacent qualified rules can be merged. is_rule holds a
   selector + rendered body (no braces); otherwise it is opaque text (an at-rule, a bang comment, or stray recovery
   text), which breaks rule adjacency. A statement separator prevents the next node from joining its prelude, and
   stray marks recovered stray text. */
typedef struct {
    int is_rule;
    int stray; /* fills the padding after is_rule, keeping the item at 64 bytes */
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
    int *oom;
} rule_vec;

/* Inlined into css_parse_rules, its only caller: out of line, passing the 64-byte item by value costs more than the
   push. */
static CSS_FORCEINLINE void rule_vec_push(rule_vec *vec, rule_item item) {
    if (vec->len == vec->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(vec->len + 1), (size_t)vec->cap, 16, sizeof(rule_item), &cap, &bytes);
        if (!grew) {       /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            *vec->oom = 1; /* GCOVR_EXCL_LINE */
            return;        /* GCOVR_EXCL_LINE */
        }
        rule_item *grown = css_realloc(vec->items, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            *vec->oom = 1;   /* GCOVR_EXCL_LINE */
            return;          /* GCOVR_EXCL_LINE */
        }
        vec->items = grown;
        vec->cap = (Py_ssize_t)cap;
    }
    vec->items[vec->len++] = item;
}

/* Index of the first depth-0 '{' from `from`, or -1. At the top level a qualified rule's prelude runs to this '{'
   (CSS Syntax 3 §5.4.3 consume a qualified rule), so a ';' or '}' before it is a prelude component value, not a
   boundary. */
static Py_ssize_t css_find_top_block(const token_vec *vec, Py_ssize_t from) {
    int depth = 0;
    for (Py_ssize_t index = from; index < vec->len; index++) {
        const css_token *token = &vec->items[index];
        if (token->kind != CSS_DELIM) {
            continue;
        }
        css_char delim = token->delim;
        if (depth == 0 && delim == '{') {
            return index;
        }
        if (delim == '(' || delim == '[' || delim == '{') {
            depth++;
        } else if ((delim == ')' || delim == ']' || delim == '}') && depth > 0) {
            depth--;
        }
    }
    return -1;
}

/* Parse a qualified rule into item (selector + rendered body), or recover a stray segment as opaque text. */
static void css_parse_qualified(css_buf *pool, cursor *cur, int top, int keyframe, rule_item *item) {
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
        if (cur->vec->bad && css_holds_bad(cur->vec, prelude_start, prelude_end)) {
            css_skip_block(cur);
            return;
        }
        decl_vec decls = {NULL, 0, 0, pool->oom, 0};
        css_parse_declarations(pool, cur, &decls, 0);
        css_buf body = {NULL, 0, 0, pool->oom};
        css_render_declarations(pool, &decls, cur->baseline, &body, CSS_DECL_NORMAL);
        if (body.len > 0) {
            /* render the selector straight into the pool (it reads only the source tokens, never the pool scratch),
               which avoids a per-rule temporary buffer allocation; the body needs its own buffer because rendering it
               uses the pool as value scratch, so it is interned afterwards */
            item->sel_off = pool->len;
            css_minify_selector(cur->vec, prelude_start, prelude_end, keyframe, pool);
            /* an empty prelude ({--x:}) lets css_minify_selector's rtrim cut below sel_off, and a negative length
               would reach cbuf_put_run's memcpy as (size_t)-1 */
            if (pool->len < item->sel_off) {
                pool->len = item->sel_off;
            }
            item->sel_len = pool->len - item->sel_off;
            /* an empty prelude is not a selector list (CSS Syntax 3 §5.4.3), so the rule is invalid and dropped rather
               than merged into the previous list as an empty entry; lightningcss 1.33.0 and csso 5.0.5 drop it too */
            if (item->sel_len > 0) {
                item->is_rule = 1;
                item->body_off = pool_run(pool, body.data, body.len);
                item->body_len = body.len;
            }
        }
        css_free(decls.items);
        cbuf_free(&body);
        return;
    }
    /* A non-NULL peek here is a ';' or '}' stop (the '{' case returned above). At the top level both are prelude
       component values, so read on to the block: if one follows, the prelude holds a token no selector list can, so
       the invalid rule is dropped with its block (CSS Syntax 3 §5.4.3), matching lightningcss 1.33.0 and csso 5.0.5.
       With no block it is stray recovery text kept verbatim, and a stray '}' is that text's terminator to consume. */
    if (top && token) {
        Py_ssize_t block = cur->no_top_block ? -1 : css_find_top_block(cur->vec, prelude_end);
        if (block >= 0) {
            cur->index = block + 1;
            css_skip_block(cur);
            return;
        }
        cur->no_top_block = 1;
        if (token->delim == '}') {
            cur->index++;
        }
    }
    if (token && token->delim == ';') {
        cur->index++;
        item->at_statement = !top;
    }
    /* a stray segment with no block: keep its trimmed text verbatim (error recovery). The caller (css_parse_rules)
       consumes leading whitespace/comments before dispatching here, so only the trailing edge needs trimming. */
    Py_ssize_t start = prelude_start;
    Py_ssize_t end = prelude_end;
    while (end > start && cur->vec->items[end - 1].kind == CSS_WS) {
        end--;
    }
    css_buf text = {NULL, 0, 0, pool->oom};
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *piece = &cur->vec->items[index];
        cbuf_put_run(&text, piece->text, piece->text_len);
        if (piece->kind == CSS_NUM) {
            cbuf_put_run(&text, (piece->text + piece->text_len), piece->unit_len);
        }
    }
    item->text_off = pool_run(pool, text.data, text.len);
    item->text_len = text.len;
    item->stray = 1;
    cbuf_free(&text);
}

static int rule_run_eq(const css_buf *pool, Py_ssize_t a_off, Py_ssize_t a_len, Py_ssize_t b_off, Py_ssize_t b_len) {
    return a_len == b_len && memcmp(pool->data + a_off, pool->data + b_off, (size_t)a_len * sizeof(css_char)) == 0;
}

static uint32_t css_rule_hash(const css_buf *pool, Py_ssize_t off, Py_ssize_t len) {
    uint32_t hash = css_hash_run(pool->data + off, len);
    return hash | (hash == 0);
}

static int css_rule_hash_seen(const uint32_t *table, size_t mask, uint32_t hash) {
    size_t slot = hash & mask;
    while (table[slot] != 0) {
        if (table[slot] == hash) {
            return 1;
        }
        slot = (slot + 1) & mask;
    }
    return 0;
}

static void css_rule_hash_add(uint32_t *table, size_t mask, uint32_t hash) {
    size_t slot = hash & mask;
    while (table[slot] != 0 && table[slot] != hash) {
        slot = (slot + 1) & mask;
    }
    table[slot] = hash;
}

/* Re-minify "prev_body;it_body" so a same-selector merge dedups overlapping declarations the same way one rule would.
 */
static void css_merge_rule_bodies(css_buf *pool, rule_item *prev, const rule_item *it, int baseline, int depth) {
    css_buf combined = {NULL, 0, 0, pool->oom};
    cbuf_put_run(&combined, pool->data + prev->body_off, prev->body_len);
    cbuf_putc(&combined, ';');
    cbuf_put_run(&combined, pool->data + it->body_off, it->body_len);
    /* the merged body parses below the rule list that holds it, so it inherits that list's depth */
    token_vec tokens = {.depth = depth, .oom = pool->oom};
    css_tokenize(combined.data, combined.len, &tokens);
    cursor inner = {&tokens, 0, baseline, -1, 0, {NULL, 0, 0, pool->oom}, 0};
    decl_vec decls = {NULL, 0, 0, pool->oom, 0};
    css_parse_declarations(pool, &inner, &decls, 0);
    cbuf_free(&inner.media_next);
    css_buf body = {NULL, 0, 0, pool->oom};
    css_render_declarations(pool, &decls, baseline, &body, CSS_DECL_NORMAL);
    prev->body_off = pool_run(pool, body.data, body.len);
    prev->body_len = body.len;
    css_free(tokens.items);
    css_free(decls.items);
    cbuf_free(&body);
    cbuf_free(&combined);
}

/* Unbounded, n same-selector rules cost O(n^2) time and memory (~115 KiB of CSS reached ~1 GiB); past the cap rules
   stay unmerged, as tdewolff/minify and clean-css level 1 leave them. 256 is well above the longest merge reach in the
   bench corpora (normalize.css, pico). */
#define CSS_MAX_MERGE_REACH 256

static Py_ssize_t css_merge_attribute_end(const css_char *selector, Py_ssize_t len, Py_ssize_t open) {
    Py_ssize_t index = open + 1;
    Py_ssize_t start = index;
    while (index < len && css_is_ident(selector[index])) { /* GCOVR_EXCL_BR_LINE: unclosed [ */
        index++;
    }
    if (!css_is_ident_string(selector + start, index - start)) {
        return -1;
    }
    while (index < len && css_is_ws(selector[index])) { /* GCOVR_EXCL_BR_LINE: unclosed [ */
        index++;
    }
    if (index < len && selector[index] == ']') { /* GCOVR_EXCL_BR_LINE: unclosed [ */
        return index;
    }
    if (index < len && selector[index] == '=') { /* GCOVR_EXCL_BR_LINE: unclosed [ */
        index++;
    } else if (index + 1 < len && /* GCOVR_EXCL_BR_LINE: unclosed [ */
               (selector[index] == '~' || selector[index] == '|' || selector[index] == '^' || selector[index] == '$' ||
                selector[index] == '*') &&
               selector[index + 1] == '=') {
        index += 2;
    } else {
        return -1;
    }
    if (index < len &&                                         /* GCOVR_EXCL_BR_LINE: unclosed [ */
        (selector[index] == '\'' || selector[index] == '"')) { /* GCOVR_EXCL_BR_LINE: unclosed [ */
        css_char quote = selector[index++];
        while (index < len && selector[index] != quote) { /* GCOVR_EXCL_BR_LINE: unclosed quote */
            if (selector[index] == '\\') {
                index++;
            }
            index++;
        }
        if (index >= len) { /* GCOVR_EXCL_BR_LINE: unclosed quote */
            return -1;      /* GCOVR_EXCL_LINE: the CSS tokenizer consumes the rule on an unclosed quote */
        }
        index++;
    } else {
        start = index;
        while (index < len && css_is_ident(selector[index])) { /* GCOVR_EXCL_BR_LINE: unclosed [ */
            index++;
        }
        if (!css_is_ident_string(selector + start, index - start)) {
            return -1;
        }
    }
    while (index < len && css_is_ws(selector[index])) { /* GCOVR_EXCL_BR_LINE: unclosed [ */
        index++;
    }
    if (index < len && css_lower(selector[index]) == 'i' && /* GCOVR_EXCL_BR_LINE: unclosed [ */
        index + 1 < len &&                                  /* GCOVR_EXCL_BR_LINE: unclosed [ */
        (css_is_ws(selector[index + 1]) || selector[index + 1] == ']')) {
        index++;
        while (index < len && css_is_ws(selector[index])) { /* GCOVR_EXCL_BR_LINE: unclosed [ */
            index++;
        }
    }
    return index < len && selector[index] == ']' ? index : -1; /* GCOVR_EXCL_BR_LINE: unclosed [ */
}

static int css_merge_known_pseudo(const css_char *name, Py_ssize_t len, int element) {
    if (!element && len == 5 && (css_run_ieq(name, len, "hover") || css_run_ieq(name, len, "focus"))) {
        return 1;
    }
    if (!element && len == 6 && css_run_ieq(name, len, "active")) {
        return 1;
    }
    static const char *const classes[] = {
        "active",        "any-link",      "checked",      "default",      "defined",
        "disabled",      "empty",         "enabled",      "first-child",  "first-of-type",
        "focus",         "focus-visible", "focus-within", "hover",        "in-range",
        "indeterminate", "invalid",       "last-child",   "last-of-type", "link",
        "only-child",    "only-of-type",  "optional",     "out-of-range", "placeholder-shown",
        "read-only",     "read-write",    "required",     "root",         "scope",
        "target",        "user-invalid",  "valid",        "visited",      "before",
        "after",         "first-line",    "first-letter"};
    static const char *const elements[] = {"before",      "after",     "first-line", "first-letter",        "marker",
                                           "placeholder", "selection", "backdrop",   "file-selector-button"};
    const char *const *names = element ? elements : classes;
    size_t count = element ? sizeof(elements) / sizeof(elements[0]) : sizeof(classes) / sizeof(classes[0]);
    css_char initial = css_lower(name[0]);
    for (size_t index = 0; index < count; index++) {
        if (initial == (css_char)(unsigned char)names[index][0] && css_run_ieq(name, len, names[index])) {
            return element || index >= count - 4 ? 2 : 1;
        }
    }
    return 0;
}

/* Ordinary selector lists are unforgiving, so merge only selectors this subset can validate. */
static int css_selector_merge_safe(const css_buf *pool, const rule_item *rule) {
    const css_char *selector = pool->data + rule->sel_off;
    if ((rule->sel_len == 1 && css_is_name_start(selector[0])) ||
        (rule->sel_len == 2 && (selector[0] == '.' || selector[0] == '#') && css_is_name_start(selector[1]))) {
        return 1;
    }
    Py_ssize_t ident_start = selector[0] == '.' || selector[0] == '#';
    if (css_is_ident_string(selector + ident_start, rule->sel_len - ident_start)) {
        return 1;
    }
    if (selector[0] == '[' && css_merge_attribute_end(selector, rule->sel_len, 0) == rule->sel_len - 1) {
        return 1;
    }
    if (rule->sel_len > 20 && memchr(selector, '(', (size_t)rule->sel_len) != NULL) {
        return 0;
    }
    int arm = 0;
    int combinator = 0;
    int type_allowed = 1;
    int element = 0;
    for (Py_ssize_t index = 0; index < rule->sel_len; index++) {
        css_char ch = selector[index];
        if (css_is_ws(ch)) {
            if (arm && !combinator) { /* GCOVR_EXCL_BR_LINE: normalized space */
                type_allowed = 1;
            }
            continue;
        }
        if (element && ch != ',') {
            return 0;
        }
        if (ch == '[') {
            if ((index = css_merge_attribute_end(selector, rule->sel_len, index)) < 0) {
                return 0;
            }
            arm = 1;
            combinator = 0;
            type_allowed = 0;
            continue;
        }
        if (ch == ':') {
            int double_colon = index + 1 < rule->sel_len && selector[index + 1] == ':';
            Py_ssize_t start = index + 1 + double_colon;
            Py_ssize_t end = start;
            while (end < rule->sel_len && css_is_ident(selector[end])) {
                end++;
            }
            if (!css_is_ident_string(selector + start, end - start)) {
                return 0;
            }
            if (end < rule->sel_len && selector[end] == '(') {
                return 0;
            }
            int kind = css_merge_known_pseudo(selector + start, end - start, double_colon);
            if (!kind) {
                return 0;
            }
            element = kind == 2;
            index = end - 1;
            arm = 1;
            combinator = 0;
            type_allowed = 0;
            continue;
        }
        if (ch == '.' || ch == '#') {
            Py_ssize_t start = index + 1;
            Py_ssize_t end = start;
            while (end < rule->sel_len && css_is_ident(selector[end])) {
                end++;
            }
            if (!css_is_ident_string(selector + start, end - start)) {
                return 0;
            }
            index = end - 1;
            arm = 1;
            combinator = 0;
            type_allowed = 0;
            continue;
        }
        if (ch == '*') {
            if (!type_allowed) {
                return 0;
            }
            arm = 1;
            combinator = 0;
            type_allowed = 0;
            continue;
        }
        if (css_starts_ident(selector, index, rule->sel_len)) {
            Py_ssize_t end = index;
            while (end < rule->sel_len && css_is_ident(selector[end])) {
                end++;
            }
            if (end < rule->sel_len && selector[end] == '>' && end - index == 2 && selector[index] == '-' &&
                selector[index + 1] == '-') {
                return 0; /* a bare -- followed by > retokenizes as CDC */
            }
            if (!type_allowed || !css_is_ident_string(selector + index, end - index)) {
                return 0;
            }
            index = end - 1;
            arm = 1;
            combinator = 0;
            type_allowed = 0;
            continue;
        }
        if (ch == '>' || ch == '+' || ch == '~') {
            if (!arm || combinator) {
                return 0;
            }
            combinator = 1;
            type_allowed = 1;
            continue;
        }
        if (ch == ',') {
            if (!arm || combinator) {
                return 0;
            }
            arm = 0;
            type_allowed = 1;
            element = 0;
            continue;
        }
        return 0;
    }
    return arm && !combinator;
}

typedef struct {
    Py_ssize_t offset;
    Py_ssize_t length;
} css_type_arm;

static int css_ascii_type_char(css_char character) {
    return (character >= 'a' && character <= 'z') || /* GCOVR_EXCL_BR_LINE: ASCII type proof */
           (character >= 'A' && character <= 'Z') || (character >= '0' && character <= '9') || character == '-' ||
           character == '_';
}

static int css_type_arms(const css_char *selector, Py_ssize_t length, css_type_arm *arms) {
    if (!length) { /* GCOVR_EXCL_BR_LINE: empty rules removed earlier */
        return 0;  /* GCOVR_EXCL_LINE: empty rules removed earlier */
    }
    Py_ssize_t index = 0;
    int count = 0;
    while (index < length && count < 32) {
        Py_ssize_t start = index;
        if (!((selector[index] >= 'a' && selector[index] <= 'z') ||  /* GCOVR_EXCL_BR_LINE: ASCII type proof */
              (selector[index] >= 'A' && selector[index] <= 'Z'))) { /* GCOVR_EXCL_BR_LINE: ASCII type proof */
            return 0;
        }
        while (index < length && css_ascii_type_char(selector[index])) {
            index++;
        }
        arms[count++] = (css_type_arm){start, index - start};
        while (index < length && selector[index] != ',') {
            if (selector[index] == '[') {
                index++;
                Py_ssize_t attribute = index;
                while (index < length &&                       /* GCOVR_EXCL_BR_LINE: unclosed [ */
                       css_ascii_type_char(selector[index])) { /* GCOVR_EXCL_BR_LINE: unclosed [ rejected earlier */
                    index++;
                }
                if (index == attribute || index == length || /* GCOVR_EXCL_BR_LINE: unclosed [ */
                    selector[index] != ']') {                /* GCOVR_EXCL_BR_LINE: unclosed [ rejected earlier */
                    return 0;
                }
                index++;
            } else if (selector[index] == '.' || selector[index] == '#') {
                index++;
                Py_ssize_t qualifier = index;
                while (index < length && css_ascii_type_char(selector[index])) {
                    index++;
                }
                if (index == qualifier) {
                    return 0;
                }
            } else {
                return 0;
            }
        }
        if (index < length) {
            index++;
        }
    }
    return index == length && selector[length - 1] != ',' ? count : 0;
}

/* Distinct explicit type names cannot match one element, even when the declarations use unknown CSS shorthands. */
static int css_type_lists_disjoint(const css_buf *pool, const rule_item *left, const rule_item *right) {
    const css_char *left_selector = pool->data + left->sel_off;
    const css_char *right_selector = pool->data + right->sel_off;
    css_type_arm left_arms[32];
    css_type_arm right_arms[32];
    int left_count = css_type_arms(left_selector, left->sel_len, left_arms);
    int right_count = css_type_arms(right_selector, right->sel_len, right_arms);
    if (!left_count || !right_count) {
        return 0;
    }
    for (int outer = 0; outer < left_count; outer++) {
        for (int inner = 0; inner < right_count; inner++) {
            if (left_arms[outer].length != right_arms[inner].length) {
                continue;
            }
            Py_ssize_t position = 0;
            while (position < left_arms[outer].length &&
                   css_lower(left_selector[left_arms[outer].offset + position]) ==
                       css_lower(right_selector[right_arms[inner].offset + position])) {
                position++;
            }
            if (position == left_arms[outer].length) {
                return 0;
            }
        }
    }
    return 1;
}

typedef struct {
    Py_ssize_t offset;
    Py_ssize_t length;
    uint32_t hash;
} css_custom_name;

static int css_custom_names(const css_char *body, Py_ssize_t length, css_custom_name *names) {
    Py_ssize_t index = 0;
    int count = 0;
    while (index < length && count < 64) {
        Py_ssize_t start = index;
        if (index + 2 >= length || body[index] != '-' || /* GCOVR_EXCL_BR_LINE: invalid decl removed earlier */
            body[index + 1] != '-') {
            return 0;
        }
        index += 2;
        while (index < length &&                   /* GCOVR_EXCL_BR_LINE: invalid name */
               css_ascii_type_char(body[index])) { /* GCOVR_EXCL_BR_LINE: invalid name removed earlier */
            index++;
        }
        if (index == start + 2 || index == length || /* GCOVR_EXCL_BR_LINE: invalid decl removed earlier */
            body[index] != ':') {                    /* GCOVR_EXCL_BR_LINE: invalid decl */
            return 0;                                /* GCOVR_EXCL_LINE: invalid declaration removed earlier */
        }
        names[count++] = (css_custom_name){start, index - start, css_hash_run(body + start, index - start)};
        index++;
        while (index < length && body[index] != ';') {
            if (body[index] == '\\' || body[index] == '\'' || /* GCOVR_EXCL_BR_LINE: complex value */
                body[index] == '"' ||                         /* GCOVR_EXCL_BR_LINE: quoted value */
                body[index] == '{' || body[index] == '}' ||   /* GCOVR_EXCL_BR_LINE: nested body rejected earlier */
                (body[index] == '/' && index + 1 < length &&  /* GCOVR_EXCL_BR_LINE: comments removed */
                 body[index + 1] == '*')) {                   /* GCOVR_EXCL_LINE: comments removed earlier */
                return 0;
            }
            index++;
        }
        if (index < length) {
            index++;
        }
    }
    return index == length ? count : 0;
}

static int css_custom_bodies_disjoint(const css_buf *pool, const rule_item *left, const rule_item *right) {
    const css_char *left_body = pool->data + left->body_off;
    const css_char *right_body = pool->data + right->body_off;
    css_custom_name left_names[64];
    css_custom_name right_names[64];
    int left_count = css_custom_names(left_body, left->body_len, left_names);
    int right_count = css_custom_names(right_body, right->body_len, right_names);
    if (!left_count || !right_count) {
        return 0;
    }
    for (int outer = 0; outer < left_count; outer++) {
        for (int inner = 0; inner < right_count; inner++) {
            if (left_names[outer].hash == right_names[inner].hash &&
                left_names[outer].length == right_names[inner].length &&
                memcmp(left_body + left_names[outer].offset, right_body + right_names[inner].offset,
                       (size_t)left_names[outer].length) == 0) {
                return 0;
            }
        }
    }
    return 1;
}

/* An incomplete shorthand table cannot prove declarations independent. Cross only distinct type selectors or
   distinct custom-property names, which have no shorthand overlap. */
CSS_NOINLINE static void css_merge_adjacent_rules(css_buf *pool, rule_vec *items, int baseline, int depth) {
    uint32_t *hashes = NULL;
    size_t hash_cap = 0;
    if (items->len > 32) {
        hash_cap = 128;
        while (hash_cap < (size_t)items->len * 4) {
            hash_cap *= 2;
        }
        hashes = css_malloc(hash_cap * sizeof(uint32_t));
        if (hashes != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure falls back to the backward scan */
            memset(hashes, 0, hash_cap * sizeof(uint32_t));
        } else {
            *pool->oom = 1; /* GCOVR_EXCL_LINE: the backward scan below still merges correctly */
        } /* GCOVR_EXCL_LINE: llvm flags the OOM branch's closing brace */
    }
    for (Py_ssize_t index = 0; index < items->len; index++) {
        rule_item *it = &items->items[index];
        if (!it->is_rule) {
            continue;
        }
        uint32_t selector_hash = css_rule_hash(pool, it->sel_off, it->sel_len);
        uint32_t body_hash = css_rule_hash(pool, it->body_off, it->body_len);
        if (hashes != NULL && !css_rule_hash_seen(hashes, hash_cap - 1, selector_hash) &&
            !css_rule_hash_seen(hashes, hash_cap - 1, body_hash)) {
            css_rule_hash_add(hashes, hash_cap - 1, selector_hash);
            css_rule_hash_add(hashes, hash_cap - 1, body_hash);
            continue;
        }
        rule_item *merged = NULL;
        const int nested_body = memchr(pool->data + it->body_off, '{', (size_t)it->body_len) != NULL;
        Py_ssize_t reach = 0;
        for (Py_ssize_t back = index - 1; back >= 0; back--) {
            if (++reach > CSS_MAX_MERGE_REACH) {
                break;
            }
            if (back < index - 1 && nested_body) {
                break;
            }
            rule_item *target = &items->items[back];
            if (target->dropped) {
                continue;
            }
            if (!target->is_rule) {
                break;
            }
            if (rule_run_eq(pool, target->sel_off, target->sel_len, it->sel_off, it->sel_len)) {
                css_merge_rule_bodies(pool, target, it, baseline, depth);
                it->dropped = 1;
                merged = target;
                break;
            }
            if (rule_run_eq(pool, target->body_off, target->body_len, it->body_off, it->body_len) &&
                css_selector_merge_safe(pool, it) && css_selector_merge_safe(pool, target)) {
                Py_ssize_t end = index + 1;
                Py_ssize_t length = target->sel_len + 1 + it->sel_len;
                if (back == index - 1) {
                    while (end < items->len) {
                        rule_item *next = &items->items[end];
                        if (!next->is_rule ||
                            !rule_run_eq(pool, target->body_off, target->body_len, next->body_off, next->body_len) ||
                            memchr(pool->data + next->sel_off, ',', (size_t)next->sel_len) != NULL ||
                            !css_selector_merge_safe(pool, next)) {
                            break;
                        }
                        length += 1 + next->sel_len;
                        end++;
                    }
                }
                css_buf selector = {NULL, 0, 0, pool->oom};
                cbuf_reserve(&selector, length);
                cbuf_put_run(&selector, pool->data + target->sel_off, target->sel_len);
                for (Py_ssize_t next = index; next < end; next++) {
                    rule_item *item = &items->items[next];
                    cbuf_putc(&selector, ',');
                    cbuf_put_run(&selector, pool->data + item->sel_off, item->sel_len);
                    item->dropped = 1;
                }
                target->sel_off = pool_run(pool, selector.data, selector.len);
                target->sel_len = selector.len;
                cbuf_free(&selector);
                merged = target;
                index = end - 1;
                break;
            }
            if (memchr(pool->data + target->body_off, '{', (size_t)target->body_len) != NULL ||
                (!css_type_lists_disjoint(pool, it, target) && !css_custom_bodies_disjoint(pool, it, target))) {
                break;
            }
        }
        if (hashes != NULL) {
            if (merged != NULL) {
                selector_hash = css_rule_hash(pool, merged->sel_off, merged->sel_len);
                body_hash = css_rule_hash(pool, merged->body_off, merged->body_len);
            }
            css_rule_hash_add(hashes, hash_cap - 1, selector_hash);
            css_rule_hash_add(hashes, hash_cap - 1, body_hash);
        }
    }
    css_free(hashes);
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

/* Just past an @media block's '}', test whether the next node is another @media with the same query. out holds the
   "@media <query>{" text of the block we are inside and nothing else. Whitespace and ordinary comments are skipped as
   they are between any two rules; a bang comment, a qualified rule, or a different at-rule ends the run, so the cursor
   is restored and 0 returned. On a match the cursor is left just past the following block's '{', so the caller keeps
   collecting its rules into the same list -- merging them in this pass (#1065). */
static int css_media_continues(cursor *cur, css_buf *pool, css_buf *out) {
    Py_ssize_t restore = cur->index;
    while (cur->index < cur->vec->len) {
        css_token *token = &cur->vec->items[cur->index];
        if (token->kind == CSS_WS) {
            cur->index++;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            if (token->text_len >= 3 && token->text[2] == '!') {
                break; /* a bang comment is a barrier between rules, so the blocks do not merge */
            }
            cur->index++;
            continue;
        }
        break;
    }
    css_token *name = cursor_peek(cur);
    /* out starts with the lowercased "@media", so a name of another length can neither match nor be handed over */
    if (!name || name->kind != CSS_AT || name->text_len != 6) {
        cur->index = restore;
        return 0;
    }
    Py_ssize_t name_index = cur->index;
    cur->index++;
    Py_ssize_t prelude_start = cur->index;
    Py_ssize_t prelude_end = css_read_until(cur, ";{}");
    css_token *stop = cursor_peek(cur);
    if (!stop || stop->delim != '{') {
        cur->index = restore;
        return 0;
    }
    css_buf *candidate = &cur->media_next;
    candidate->len = 0;
    for (Py_ssize_t pos = 0; pos < name->text_len; pos++) {
        cbuf_putc(candidate, css_lower(name->text[pos]));
    }
    css_at_prelude(pool, cur->vec, prelude_start, prelude_end, 0, name->text, name->text_len, candidate);
    cbuf_putc(candidate, '{');
    if (candidate->len == out->len && memcmp(candidate->data, out->data, (size_t)out->len * sizeof(css_char)) == 0) {
        cur->index++;
        return 1;
    }
    if (memcmp(candidate->data, out->data, 6 * sizeof(css_char)) == 0) {
        /* the caller parses this @media next and takes over the prelude rendered here rather than reading it twice */
        cur->media_at = name_index;
        cur->media_body = cur->index + 1;
    }
    cur->index = restore;
    return 0;
}

/* Whether stray text comes before a rule list's last item. Top-level stray text reads on through the bang comments and
   at-statements after it when reparsed, and a block after it is dropped with it, so only those can follow it and a
   last at-statement there is not an at-rule. Kept out of line: only a sheet ending in an at-statement reaches it. */
CSS_NOINLINE static int css_follows_stray(const rule_vec *items) {
    for (Py_ssize_t index = items->len - 2; index >= 0; index--) {
        if (items->items[index].stray) {
            return 1;
        }
    }
    return 0;
}

/* Same-query @media blocks joined across dropped nodes: the index of the first block's item, and the blocks' text
   without the closing '}'. */
typedef struct {
    Py_ssize_t item;
    css_buf text;
} media_run;

/* Join piece, an @media block whose header is head long, to the last node pushed to items when that node is an @media
   block with the same header; returns whether it did. A header ends at its first '{' outside parentheses, so only an
   @media block with the same header starts with it. Kept out of line with the run on the heap: css_parse_rules recurses
   per nesting level, so its frame stays as small as a list without a run needs. */
CSS_NOINLINE static int css_join_media(const css_buf *pool, const rule_vec *items, const css_buf *piece,
                                       Py_ssize_t head, media_run **run) {
    /* the last item's text may sit past what a failed growth wrote */
    if (items->len == 0 || *pool->oom) { /* GCOVR_EXCL_BR_LINE: the oom operand is an allocation failure */
        return 0;
    }
    const rule_item *last = &items->items[items->len - 1];
    const css_char *text = pool->data + last->text_off;
    if (last->text_len <= head || text[head - 1] != '{' ||
        memcmp(text, piece->data, (size_t)head * sizeof(css_char)) != 0) {
        return 0;
    }
    if (*run == NULL) {
        *run = css_malloc(sizeof(media_run));
        if (*run == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            *pool->oom = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return 0;       /* GCOVR_EXCL_LINE: the block stays unjoined */
        }
        (*run)->item = items->len - 1;
        (*run)->text = (css_buf){NULL, 0, 0, pool->oom};
        cbuf_put_run(&(*run)->text, text, last->text_len - 1);
    }
    /* a block's last at-statement is rendered without its ';', which the next body must not extend */
    cbuf_putc(&(*run)->text, ';');
    cbuf_put_run(&(*run)->text, piece->data + head, piece->len - head - 1);
    return 1;
}

/* Re-minify the joined text of run into its first block's item, so the later blocks' rules merge with the first
   block's as they would inside one block, then free the run. */
CSS_NOINLINE static void css_rejoin_media(css_buf *pool, cursor *cur, rule_vec *items, media_run *run) {
    cbuf_putc(&run->text, '}');
    /* the block parses where the first one did, so it inherits the rule list's depth */
    token_vec tokens = {.depth = cur->vec->depth, .oom = pool->oom};
    css_tokenize(run->text.data, run->text.len, &tokens);
    cursor inner = {&tokens, 0, cur->baseline, -1, 0, {NULL, 0, 0, pool->oom}, 0};
    css_buf merged = {NULL, 0, 0, pool->oom};
    css_parse_at(pool, &inner, &merged, 0);
    cbuf_free(&inner.media_next);
    rule_item *block = &items->items[run->item];
    Py_ssize_t text_off = pool_run(pool, merged.data, merged.len);
    if (!*pool->oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        block->text_off = text_off;
        block->text_len = merged.len;
    }
    css_free(tokens.items);
    cbuf_free(&merged);
    cbuf_free(&run->text);
    css_free(run);
}

/* Parse a rule list, collecting nodes so adjacent rules can be merged, then serialize. At the top level, declarations
   between rules are stray text; nested (inside an at-block) a '}' ends the list. */
static void css_parse_rules(css_buf *pool, cursor *cur, int top, int keyframe, int media, css_buf *out) {
    if (!css_nesting_enter(cur->vec)) {
        /* over-nested: drop the remaining rules rather than overflow the C stack; only a nested call bails, so
           consume its closing brace when the input has one */
        css_skip_block(cur);
        return;
    }
    rule_vec items = {NULL, 0, 0, pool->oom};
    /* css_media_continues folds only blocks that whitespace and comments separate, since it cannot tell a node that
       minifies away, and the blocks it folds may each end and start with a nested same-query block. joinable_at is
       items.len after a dropped node or a folded block boundary; an @media block pushed then that repeats the last
       item's query joins the run, re-minified at the next at-rule or the end of the list. */
    Py_ssize_t joinable_at = -1;
    media_run *run = NULL;
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
        if (token->kind == CSS_DELIM && token->delim == '}' && !top) {
            cur->index++;
            /* a same-query @media right after this block's '}' is pulled into this rule list so its rules merge
               with ours now, not on the next call (#1065) */
            if (media && css_media_continues(cur, pool, out)) {
                joinable_at = items.len;
                continue;
            }
            break;
        }
        if (token->kind == CSS_AT) {
            css_buf piece = {NULL, 0, 0, pool->oom};
            Py_ssize_t kind = css_parse_at(pool, cur, &piece, 0);
            /* css_parse_at always emits at least the lowercased at-rule name, so piece is never empty */
            if (css_is_empty_conditional_atrule(piece.data, piece.len)) {
                joinable_at = items.len;
            } else if (kind <= 0 || joinable_at != items.len || !css_join_media(pool, &items, &piece, kind, &run)) {
                if (run != NULL) {
                    css_rejoin_media(pool, cur, &items, run);
                    run = NULL;
                }
                rule_item item = {0};
                item.text_off = pool_run(pool, piece.data, piece.len);
                item.text_len = piece.len;
                item.at_statement = kind < 0;
                rule_vec_push(&items, item);
            }
            cbuf_free(&piece);
        } else {
            rule_item item = {0};
            css_parse_qualified(pool, cur, top, keyframe, &item);
            if (item.is_rule || item.text_len > 0) {
                rule_vec_push(&items, item);
            } else {
                joinable_at = items.len;
            }
        }
    }
    if (run != NULL) {
        css_rejoin_media(pool, cur, &items, run);
    }
    /* an item collected after a failed growth may point at pool text that was never written */
    if (*pool->oom) {                /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        css_free(items.items);       /* GCOVR_EXCL_LINE: allocation-failure path */
        css_nesting_leave(cur->vec); /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    css_merge_adjacent_rules(pool, &items, cur->baseline, cur->vec->depth);
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
    if (prev_at_statement && top && !css_follows_stray(&items)) {
        /* a stylesheet ending in a statement at-rule keeps its `;`: once another sheet is appended, consume an at-rule
           (CSS Syntax 3 §5.5.2) would otherwise read on into the next rule and take its block */
        cbuf_putc(out, ';');
    }
    css_free(items.items);
    css_nesting_leave(cur->vec);
}

#endif /* TURBOHTML_CSS_GRAMMAR_H */
