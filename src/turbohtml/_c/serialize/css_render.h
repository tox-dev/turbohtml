#ifndef TURBOHTML_CSS_RENDER_H
#define TURBOHTML_CSS_RENDER_H

/* Render a value's tokens [start, end) into components in the pool. */
static void css_render_components(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, int is_color,
                                  comp_vec *comps) {
    Py_ssize_t index = start;
    while (index < end) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            index++;
            continue;
        }
        css_comp comp = {0};
        if (token->kind == CSS_DELIM && (token->delim == ',' || token->delim == '/')) {
            comp.off = pool->len;
            cbuf_putc(pool, token->delim);
            comp.len = 1;
            comp.isfunc = 2;
            comp.kind = CK_SEP;
            comp_vec_push(comps, comp);
            index++;
            continue;
        }
        if (token->kind == CSS_IDENT && index + 1 < end && vec->items[index + 1].kind == CSS_DELIM &&
            vec->items[index + 1].delim == '(') {
            Py_ssize_t close_index = css_match_paren(vec, index + 1, end);
            int ends_paren;
            css_emit_function(pool, vec, index, close_index, &comp.off, &comp.len, &ends_paren);
            comp.isfunc = ends_paren ? 1 : 0;
            comp.kind = ends_paren ? CK_FUNC : CK_IDENT;
            comp_vec_push(comps, comp);
            index = close_index + 1;
            continue;
        }
        if (token->kind == CSS_NUM) {
            css_format_dimension(pool, token, 1, &comp.off, &comp.len);
            comp.kind = token->unit_len ? CK_DIM : CK_NUM;
        } else if (token->kind == CSS_HASH) {
            if (!css_color_keyword_or_hash(pool, token, 1, &comp.off, &comp.len)) {
                comp.off = pool_run(pool, token->text, token->text_len); /* GCOVR_EXCL_LINE: hash always renders */
                comp.len = token->text_len;                              /* GCOVR_EXCL_LINE */
            }
            comp.kind = CK_HASH;
        } else if (token->kind == CSS_IDENT) {
            if (!css_color_keyword_or_hash(pool, token, is_color, &comp.off, &comp.len)) {
                comp.off = pool_run(pool, token->text, token->text_len);
                comp.len = token->text_len;
            }
            comp.kind = CK_IDENT;
        } else if (token->kind == CSS_URL) {
            css_minify_url(pool, token->text, token->text_len, &comp.off, &comp.len);
            comp.isfunc = 1;
            comp.kind = CK_URL;
        } else if (token->kind == CSS_STR) {
            css_minify_string(pool, token->text, token->text_len, &comp.off, &comp.len);
            comp.kind = CK_STR;
        } else {
            comp.off = pool_run(pool, token->text, token->text_len);
            comp.len = token->text_len;
            comp.kind = CK_DELIM;
        }
        /* every branch above renders at least one char (a delim is text_len 1, a string keeps its quotes, etc.) */
        comp_vec_push(comps, comp);
        index++;
    }
}

/* Assemble components into the value buffer: a single space between components, except after a function/url/separator,
   before a parenthesised piece, or around a bare delimiter. */
static void css_assemble(css_buf *pool, comp_vec *comps, css_buf *out) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (index > 0) {
            css_comp *prev = &comps->items[index - 1];
            int starts_paren = pool->data[comp->off] == '('; /* every assembled comp has len >= 1 */
            int glued = comp->isfunc == 2 || prev->isfunc == 1 || prev->isfunc == 2 || starts_paren ||
                        comp->kind == CK_DELIM || prev->kind == CK_DELIM;
            if (!glued) {
                cbuf_putc(out, ' ');
            }
        }
        cbuf_put_run(out, pool->data + comp->off, comp->len);
    }
}

/* Property classification for the value pipeline. */
static int css_prop_is_color(const css_char *prop, Py_ssize_t len) {
    static const char *const color_props[] = {"color",
                                              "background-color",
                                              "border-color",
                                              "border-top-color",
                                              "border-right-color",
                                              "border-bottom-color",
                                              "border-left-color",
                                              "outline-color",
                                              "text-decoration-color",
                                              "caret-color",
                                              "column-rule-color",
                                              "fill",
                                              "stroke",
                                              "stop-color",
                                              "flood-color",
                                              "lighting-color",
                                              "text-emphasis-color"};
    for (size_t index = 0; index < sizeof(color_props) / sizeof(color_props[0]); index++) {
        if (css_run_ieq(prop, len, color_props[index])) {
            return 1;
        }
    }
    return 0;
}

/* Render a custom property or otherwise-raw value: collapse whitespace and comments, keep everything else verbatim. */
static void css_render_raw_value(token_vec *vec, Py_ssize_t start, Py_ssize_t end, css_buf *out) {
    int pending_ws = 0;
    int any_ws = 0;
    Py_ssize_t written = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            any_ws = 1;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            continue;
        }
        if (pending_ws && written > 0) {
            cbuf_putc(out, ' ');
        }
        pending_ws = 0;
        if (token->kind == CSS_NUM) {
            cbuf_put_run(out, token->text, token->text_len);
            cbuf_put_run(out, (token->text + token->text_len), token->unit_len);
        } else {
            cbuf_put_run(out, token->text, token->text_len);
        }
        written++;
    }
    /* strip(): trailing space cannot occur (only inserted before a piece); a pure-whitespace value folds to one space
     */
    if (written == 0 && any_ws) {
        cbuf_putc(out, ' ');
    }
}

static int css_prop_in(const css_char *prop, Py_ssize_t len, const char *const *list, size_t count) {
    for (size_t index = 0; index < count; index++) {
        if (css_run_ieq(prop, len, list[index])) {
            return 1;
        }
    }
    return 0;
}

static int css_comp_text_eq(const css_buf *pool, const css_comp *left, const css_comp *right) {
    return left->len == right->len &&
           memcmp(pool->data + left->off, pool->data + right->off, (size_t)left->len * sizeof(css_char)) == 0;
}

static int css_comp_kw(const css_buf *pool, const css_comp *comp, const char *keyword) {
    return comp->kind == CK_IDENT && comp_ieq(pool, comp, keyword);
}

static void css_set_comp(css_buf *pool, css_comp *comp, const char *text, css_compkind kind) {
    comp->off = pool_cstr(pool, text);
    comp->len = (Py_ssize_t)strlen(text);
    comp->isfunc = 0;
    comp->kind = kind;
}

static int css_comp_is_zero(const css_buf *pool, const css_comp *comp) {
    if (comp->kind != CK_NUM && comp->kind != CK_DIM && comp->kind != CK_PCT) {
        return 0;
    }
    char buffer[64];
    Py_ssize_t written = 0;
    for (Py_ssize_t index = 0; index < comp->len && written < 63; index++) {
        css_char character = pool->data[comp->off + index];
        if (character == '%') {
            break;
        }
        buffer[written++] = (char)character; /* numeric/percentage value text is ASCII only */
    }
    buffer[written] = '\0';
    return strtod(buffer, NULL) == 0.0;
}

/* Replace an ident component naming a color with its shortest hash. */
static void css_minify_color_comp(css_buf *pool, css_comp *comp) {
    if (comp->kind != CK_IDENT) {
        return;
    }
    Py_ssize_t off;
    Py_ssize_t len;
    if (css_name_to_hex(pool, pool->data + comp->off, comp->len, &off, &len)) {
        comp->off = off;
        comp->len = len;
        comp->kind = CK_HASH;
        comp->isfunc = 0;
    }
}

static const char *const CSS_BOX_PROPS[] = {"margin", "padding",       "border-width",  "border-style",
                                            "inset",  "border-radius", "scroll-margin", "scroll-padding",
                                            "gap",    "grid-gap",      "overflow",      "overscroll-behavior"};

static const char *const CSS_CURRENTCOLOR_INIT[] = {"border-left-color",     "border-right-color",
                                                    "border-top-color",      "border-bottom-color",
                                                    "text-decoration-color", "text-emphasis-color"};

static const char *const CSS_SINGLE_COLOR[] = {"color", "caret-color", "outline-color",
                                               "fill",  "stroke",      "background-color"};

/* Collapse a 2-4 value box shorthand (margin/padding/...) when the mirrored edges are equal. */
static void css_handle_box(const css_buf *pool, comp_vec *comps) {
    /* dispatched only when the value has no separator (css_apply_handler), so no component is a CK_SEP here */
    Py_ssize_t count = comps->len;
    if (count < 2 || count > 4) {
        return;
    }
    if (count == 4 && css_comp_text_eq(pool, &comps->items[3], &comps->items[1])) {
        count = 3;
    }
    if (count == 3 && css_comp_text_eq(pool, &comps->items[2], &comps->items[0])) {
        count = 2;
    }
    if (count == 2 && css_comp_text_eq(pool, &comps->items[1], &comps->items[0])) {
        count = 1;
    }
    comps->len = count;
}

static void css_handle_font_weight(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (css_comp_kw(pool, comp, "normal")) {
            css_set_comp(pool, comp, "400", CK_NUM);
        } else if (css_comp_kw(pool, comp, "bold")) {
            css_set_comp(pool, comp, "700", CK_NUM);
        }
    }
}

static void css_handle_flex_basis(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (css_comp_kw(pool, comp, "initial")) {
            css_set_comp(pool, comp, "auto", CK_IDENT);
        } else if (css_comp_is_zero(pool, comp)) {
            css_set_comp(pool, comp, "0", CK_NUM);
        }
    }
}

static void css_handle_flex_grow(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (css_comp_kw(pool, &comps->items[index], "initial")) {
            css_set_comp(pool, &comps->items[index], "0", CK_NUM);
        }
    }
}

static void css_handle_flex_shrink(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (css_comp_kw(pool, &comps->items[index], "initial")) {
            css_set_comp(pool, &comps->items[index], "1", CK_NUM);
        }
    }
}

static void css_handle_color_single(css_buf *pool, comp_vec *comps, const css_char *prop, Py_ssize_t prop_len) {
    int currentcolor_init =
        css_prop_in(prop, prop_len, CSS_CURRENTCOLOR_INIT, sizeof(CSS_CURRENTCOLOR_INIT) / sizeof(char *));
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (comp->kind == CK_SEP) {
            continue;
        }
        if (currentcolor_init && css_comp_kw(pool, comp, "currentcolor")) {
            css_set_comp(pool, comp, "initial", CK_IDENT);
            continue;
        }
        css_minify_color_comp(pool, comp);
    }
}

static void css_handle_color_drop(css_buf *pool, comp_vec *comps, const css_char *prop, Py_ssize_t prop_len) {
    static const char *const border_drop[] = {"none", "currentcolor", "medium"};
    static const char *const outline_drop[] = {"invert", "none", "medium"};
    static const char *const column_drop[] = {"currentcolor", "none", "medium"};
    static const char *const decoration_drop[] = {"currentcolor", "none", "solid"};
    static const char *const emphasis_drop[] = {"currentcolor", "none"};
    const char *const *drop = border_drop;
    size_t drop_count = 3;
    if (css_run_ieq(prop, prop_len, "outline")) {
        drop = outline_drop;
    } else if (css_run_ieq(prop, prop_len, "column-rule")) {
        drop = column_drop;
    } else if (css_run_ieq(prop, prop_len, "text-decoration")) {
        drop = decoration_drop;
    } else if (css_run_ieq(prop, prop_len, "text-emphasis")) {
        drop = emphasis_drop;
        drop_count = 2;
    }
    Py_ssize_t write = 0;
    int any_value = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp comp = comps->items[index];
        if (comp.kind == CK_SEP) {
            comps->items[write++] = comp;
            continue;
        }
        if (comp.kind == CK_IDENT) {
            int dropped = 0;
            for (size_t which = 0; which < drop_count; which++) {
                if (comp_ieq(pool, &comp, drop[which])) {
                    dropped = 1;
                    break;
                }
            }
            if (dropped) {
                continue;
            }
        }
        css_minify_color_comp(pool, &comp);
        comps->items[write++] = comp;
        any_value = 1;
    }
    comps->len = write;
    if (!any_value) {
        comps->len = 0;
        css_comp none = {0};
        css_set_comp(pool, &none, "none", CK_IDENT);
        comp_vec_push(comps, none);
    }
}

static void css_handle_border_color(css_buf *pool, comp_vec *comps) {
    Py_ssize_t write = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp comp = comps->items[index];
        if (comp.kind == CK_SEP) {
            continue;
        }
        if (css_comp_kw(pool, &comp, "currentcolor")) {
            css_set_comp(pool, &comp, "initial", CK_IDENT);
        } else {
            css_minify_color_comp(pool, &comp);
        }
        comps->items[write++] = comp;
    }
    comps->len = write;
    if (write > 0) {
        int all_equal = 1;
        for (Py_ssize_t index = 1; index < write; index++) {
            if (!css_comp_text_eq(pool, &comps->items[index], &comps->items[0])) {
                all_equal = 0;
                break;
            }
        }
        if (all_equal) {
            comps->len = 1;
        }
    }
}

static void css_handle_text_shadow(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind != CK_SEP) {
            css_minify_color_comp(pool, &comps->items[index]);
        }
    }
}

/* Collapse a two-keyword background-repeat run (repeat repeat -> repeat, repeat no-repeat -> repeat-x, ...). */
static Py_ssize_t css_collapse_repeat_run(css_buf *pool, css_comp *items, Py_ssize_t count) {
    if (count == 2 && items[0].kind == CK_IDENT && items[1].kind == CK_IDENT) {
        if (css_comp_text_eq(pool, &items[0], &items[1])) {
            return 1;
        }
        if (comp_ieq(pool, &items[0], "repeat") && comp_ieq(pool, &items[1], "no-repeat")) {
            css_set_comp(pool, &items[0], "repeat-x", CK_IDENT);
            return 1;
        }
        if (comp_ieq(pool, &items[0], "no-repeat") && comp_ieq(pool, &items[1], "repeat")) {
            css_set_comp(pool, &items[0], "repeat-y", CK_IDENT);
            return 1;
        }
    }
    return count;
}

/* Apply a per-run transform across comma-separated runs in place, rebuilding comps. */
typedef Py_ssize_t (*css_run_transform)(css_buf *pool, css_comp *items, Py_ssize_t count);

static void css_handle_runs(css_buf *pool, comp_vec *comps, css_run_transform transform) {
    comp_vec result = {NULL, 0, 0, 0};
    Py_ssize_t run_start = 0;
    Py_ssize_t index = 0;
    while (index <= comps->len) {
        int at_sep = index < comps->len && comps->items[index].kind == CK_SEP;
        if (index == comps->len || at_sep) {
            Py_ssize_t kept = transform(pool, comps->items + run_start, index - run_start);
            for (Py_ssize_t pos = 0; pos < kept; pos++) {
                comp_vec_push(&result, comps->items[run_start + pos]);
            }
            if (at_sep) {
                comp_vec_push(&result, comps->items[index]);
            }
            run_start = index + 1;
        }
        index++;
    }
    css_free(comps->items);
    *comps = result;
}

static Py_ssize_t css_collapse_size_run(css_buf *pool, css_comp *items, Py_ssize_t count) {
    if (count == 2 && comp_ieq(pool, &items[1], "auto")) {
        return 1;
    }
    return count;
}

static Py_ssize_t css_collapse_box_shadow_run(css_buf *pool, css_comp *items, Py_ssize_t count) {
    /* css_handle_runs splits on CK_SEP, so a run never contains a separator -- every item is a value to color-minify */
    for (Py_ssize_t index = 0; index < count; index++) {
        css_minify_color_comp(pool, &items[index]);
    }
    if (count == 1 && css_comp_kw(pool, &items[0], "initial")) {
        css_set_comp(pool, &items[0], "none", CK_IDENT);
        return 1;
    }
    Py_ssize_t numbers[8];
    Py_ssize_t number_count = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        if ((items[index].kind == CK_NUM || items[index].kind == CK_DIM) && number_count < 8) {
            numbers[number_count++] = index;
        }
    }
    /* drop a zero blur/spread (the 4th then 3rd length); delete the element in place, since a color may follow it */
    if (number_count == 4 && css_comp_is_zero(pool, &items[numbers[3]])) {
        for (Py_ssize_t index = numbers[3]; index + 1 < count; index++) {
            items[index] = items[index + 1];
        }
        count--;
        number_count = 3;
    }
    if (number_count == 3 && css_comp_is_zero(pool, &items[numbers[2]])) {
        for (Py_ssize_t index = numbers[2]; index + 1 < count; index++) {
            items[index] = items[index + 1];
        }
        count--;
    }
    return count;
}

/* Append a color-function-style filter value (progid alpha shortening), returning 1 when handled. */
static void css_handle_filter(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *prop,
                              Py_ssize_t prop_len, css_buf *out) {
    css_buf joined = {NULL, 0, 0, 0};
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_NUM) {
            cbuf_put_run(&joined, token->text, token->text_len);
            cbuf_put_run(&joined, (token->text + token->text_len), token->unit_len);
        } else if (token->kind == CSS_STR) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_minify_string(pool, token->text, token->text_len, &off, &len);
            cbuf_put_run(&joined, pool->data + off, len);
        } else if (token->kind == CSS_URL) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_minify_url(pool, token->text, token->text_len, &off, &len);
            cbuf_put_run(&joined, pool->data + off, len);
        } else {
            cbuf_put_run(&joined, token->text, token->text_len);
        }
    }
    const char *legacy = "progid:dximagetransform.microsoft.alpha(opacity=";
    Py_ssize_t legacy_len = (Py_ssize_t)strlen(legacy);
    int is_ms = css_run_ieq(prop, prop_len, "-ms-filter");
    int quote = joined.len > 0 && (joined.data[0] == '"' || joined.data[0] == '\'');
    Py_ssize_t scan = is_ms && quote ? 1 : 0;
    int matches_legacy = joined.len - scan >= legacy_len;
    for (Py_ssize_t index = 0; matches_legacy && index < legacy_len; index++) {
        if (css_lower(joined.data[scan + index]) != (css_char)(unsigned char)legacy[index]) {
            matches_legacy = 0;
        }
    }
    /* matches_legacy implies joined.len >= legacy_len (48), so it is always positive here */
    if (!is_ms && matches_legacy && joined.data[joined.len - 1] == ')') {
        cbuf_puts(out, "alpha(opacity=");
        cbuf_put_run(out, joined.data + legacy_len, joined.len - legacy_len);
    } else if (is_ms && quote && matches_legacy) {
        cbuf_putc(out, joined.data[0]);
        cbuf_puts(out, "alpha(opacity=");
        cbuf_put_run(out, joined.data + 1 + legacy_len, joined.len - 1 - legacy_len);
    } else {
        cbuf_put_run(out, joined.data, joined.len);
    }
    cbuf_free(&joined);
}

static int css_parse_unicode_range(const css_char *data, Py_ssize_t len, long long *low, long long *high) {
    Py_ssize_t pos = 2;
    long long start = 0;
    Py_ssize_t wildcard_at = 0;
    while (pos < len && data[pos] != '-') {
        start *= 16;
        css_char character = css_lower(data[pos]);
        /* a U+ token's range body is only [0-9a-f?]; '-' ends this loop, so no char below '0' reaches here */
        if (character <= '9') {
            start += character - '0';
        } else if (character >= 'a') {
            start += character - 'a' + 10;
        } else if (wildcard_at == 0) {
            wildcard_at = pos; /* the only remaining body char is '?' */
        }
        pos++;
    }
    long long end = start;
    if (wildcard_at != 0) {
        long long span = 1;
        for (Py_ssize_t index = 0; index < len - wildcard_at; index++) {
            span *= 16;
        }
        end = start + span - 1;
    } else if (pos < len) { /* the first loop stops only at '-' or end, so a non-end position is always '-' */
        pos++;
        end = 0;
        while (pos < len) {
            end *= 16;
            css_char character = css_lower(data[pos]);
            /* the end body is only [0-9a-f] (no '?'), so the two hex classes are exhaustive */
            if (character <= '9') {
                end += character - '0';
            } else {
                end += character - 'a' + 10;
            }
            pos++;
        }
        if (end <= start) {
            end = start;
        }
    }
    *low = start;
    *high = end;
    return 1;
}

/* Minify a unicode-range value, returning 1 when handled (every token was a range), 0 to fall through. */
static int css_handle_unicode_range(token_vec *vec, Py_ssize_t start, Py_ssize_t end, css_buf *out) {
    Py_ssize_t capacity = 16;
    long long (*ranges)[2] = css_malloc((size_t)capacity * sizeof(*ranges));
    if (ranges == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 0;         /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == ',') {
            continue;
        }
        if (token->kind != CSS_URANGE) {
            css_free(ranges);
            return 0;
        }
        if (count == capacity) {
            capacity *= 2;
            long long (*grown)[2] = css_realloc(ranges, (size_t)capacity * sizeof(*ranges));
            if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                css_free(ranges); /* GCOVR_EXCL_LINE */
                return 0;         /* GCOVR_EXCL_LINE */
            }
            ranges = grown;
        }
        css_parse_unicode_range(token->text, token->text_len, &ranges[count][0], &ranges[count][1]);
        count++;
    }
    for (Py_ssize_t outer = 1; outer < count; outer++) {
        long long low = ranges[outer][0];
        long long high = ranges[outer][1];
        Py_ssize_t inner = outer - 1;
        while (inner >= 0 && ranges[inner][0] > low) {
            ranges[inner + 1][0] = ranges[inner][0];
            ranges[inner + 1][1] = ranges[inner][1];
            inner--;
        }
        ranges[inner + 1][0] = low;
        ranges[inner + 1][1] = high;
    }
    Py_ssize_t merged = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        if (merged > 0 && ranges[index][0] <= ranges[merged - 1][1] + 1) {
            if (ranges[index][1] > ranges[merged - 1][1]) {
                ranges[merged - 1][1] = ranges[index][1];
            }
        } else {
            ranges[merged][0] = ranges[index][0];
            ranges[merged][1] = ranges[index][1];
            merged++;
        }
    }
    for (Py_ssize_t index = 0; index < merged; index++) {
        if (index != 0) {
            cbuf_putc(out, ',');
        }
        long long low = ranges[index][0];
        long long high = ranges[index][1];
        char buffer[32];
        if (low == high) {
            int written = snprintf(buffer, sizeof(buffer), "U+%llX", low);
            for (int pos = 0; pos < written; pos++) {
                cbuf_putc(out, (css_char)(unsigned char)buffer[pos]);
            }
        } else if (low == 0 && high == 0x10FFFF) {
            cbuf_puts(out, "initial");
        } else {
            int nibble = 0;
            while (nibble < 6 && ((low >> (nibble * 4)) & 0xF) == 0 && ((high >> (nibble * 4)) & 0xF) == 0xF) {
                nibble++;
            }
            int wildcards = nibble;
            while (nibble < 6) {
                if (((low >> (nibble * 4)) & 0xF) != ((high >> (nibble * 4)) & 0xF)) {
                    wildcards = 0;
                    break;
                }
                nibble++;
            }
            if (wildcards != 0) {
                long long prefix = low >> (wildcards * 4);
                cbuf_puts(out, "U+");
                if (prefix != 0) {
                    int written = snprintf(buffer, sizeof(buffer), "%llX", prefix);
                    for (int pos = 0; pos < written; pos++) {
                        cbuf_putc(out, (css_char)(unsigned char)buffer[pos]);
                    }
                }
                for (int pos = 0; pos < wildcards; pos++) {
                    cbuf_putc(out, '?');
                }
            } else {
                int written = snprintf(buffer, sizeof(buffer), "U+%llX-%llX", low, high);
                for (int pos = 0; pos < written; pos++) {
                    cbuf_putc(out, (css_char)(unsigned char)buffer[pos]);
                }
            }
        }
    }
    css_free(ranges);
    return 1;
}

#endif /* TURBOHTML_CSS_RENDER_H */
