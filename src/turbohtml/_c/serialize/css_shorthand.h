#ifndef TURBOHTML_CSS_SHORTHAND_H
#define TURBOHTML_CSS_SHORTHAND_H

/* A background-position value during the keyword<->offset normalization passes. data refers to pool text by
   (off, len); ident records the edge keyword so the normalizer can rewrite right/bottom to left/top offsets. */
typedef enum { PVK_COMMA, PVK_IDENT, PVK_PCT, PVK_NUM, PVK_LEN } pv_kind;
typedef enum { PVI_NONE, PVI_LEFT, PVI_RIGHT, PVI_TOP, PVI_BOTTOM, PVI_CENTER } pv_ident;
typedef struct {
    pv_kind kind;
    Py_ssize_t off;
    Py_ssize_t len;
    pv_ident ident;
} pos_val;

/* The only caller guards this with comp->kind == CK_IDENT, so the component is always an identifier here. */
static pv_ident css_pv_ident_of(css_buf *pool, const css_comp *comp) {
    if (comp_ieq(pool, comp, "left")) {
        return PVI_LEFT;
    }
    if (comp_ieq(pool, comp, "right")) {
        return PVI_RIGHT;
    }
    if (comp_ieq(pool, comp, "top")) {
        return PVI_TOP;
    }
    if (comp_ieq(pool, comp, "bottom")) {
        return PVI_BOTTOM;
    }
    if (comp_ieq(pool, comp, "center")) {
        return PVI_CENTER;
    }
    return PVI_NONE;
}

static void css_pv_set(css_buf *pool, pos_val *value, pv_kind kind, const char *text, pv_ident ident) {
    value->kind = kind;
    value->off = pool_cstr(pool, text);
    value->len = (Py_ssize_t)strlen(text);
    value->ident = ident;
}

static int css_pv_is_zero(css_buf *pool, const pos_val *value) {
    if (value->kind == PVK_IDENT) {
        return 0;
    }
    char buffer[64];
    Py_ssize_t written = 0;
    for (Py_ssize_t index = 0; index < value->len && written < 63; index++) {
        css_char character = pool->data[value->off + index];
        if (character == '%') {
            break;
        }
        buffer[written++] = (char)character; /* numeric/percentage value text is ASCII only */
    }
    buffer[written] = '\0';
    return strtod(buffer, NULL) == 0.0;
}

static void css_pv_del(pos_val *vals, Py_ssize_t *count, Py_ssize_t index) {
    for (Py_ssize_t pos = index; pos + 1 < *count; pos++) {
        vals[pos] = vals[pos + 1];
    }
    (*count)--;
}

/* Resolve a 1-4 value background-position run to the shortest equivalent offsets (CSS Backgrounds 3 §3.6). */
static void css_normalize_position_run(css_buf *pool, pos_val *vals, Py_ssize_t *count_io) {
    Py_ssize_t count = *count_io;
    if (count == 3 || count == 4) {
        Py_ssize_t kept = count;
        Py_ssize_t order[2] = {count - 1, 1};
        for (int which = 0; which < 2; which++) {
            Py_ssize_t pos = order[which];
            /* pos is count-1 or 1, and kept>2 holds only before any delete, so pos<count is always true here */
            if (kept > 2 && css_pv_is_zero(pool, &vals[pos])) {
                css_pv_del(vals, &count, pos);
                kept--;
            }
        }
        Py_ssize_t axis = (count > 2 && vals[2].kind == PVK_IDENT) ? 2 : 1;
        pos_val offsets[2];
        int has_offset[2] = {0, 0};
        int offsets_len = 2;
        Py_ssize_t loop[2] = {axis, 0};
        for (int which = 0; which < 2; which++) {
            Py_ssize_t pos = loop[which];
            if (pos + 1 < count && pos + 1 != axis) {
                if (vals[pos + 1].kind == PVK_PCT && (vals[pos].ident == PVI_RIGHT || vals[pos].ident == PVI_BOTTOM)) {
                    char digits[64];
                    Py_ssize_t written = 0;
                    for (Py_ssize_t index = 0; index < vals[pos + 1].len - 1 && written < 63; index++) {
                        css_char character = pool->data[vals[pos + 1].off + index];
                        digits[written++] = (char)character; /* percentage value text is ASCII only */
                    }
                    digits[written] = '\0';
                    char flipped[24];
                    /* long long, not long: long is 32-bit on Windows, so a percentage that overflows it would flip to
                       a different value there than on a 64-bit platform */
                    snprintf(flipped, sizeof(flipped), "%lld%%", 100 - strtoll(digits, NULL, 10));
                    vals[pos + 1].off = pool_cstr(pool, flipped);
                    vals[pos + 1].len = (Py_ssize_t)strlen(flipped);
                    if (vals[pos].ident == PVI_RIGHT) {
                        css_pv_set(pool, &vals[pos], PVK_IDENT, "left", PVI_LEFT);
                    } else {
                        css_pv_set(pool, &vals[pos], PVK_IDENT, "top", PVI_TOP);
                    }
                }
                if (vals[pos].ident == PVI_LEFT) {
                    offsets[0] = vals[pos + 1];
                    has_offset[0] = 1;
                } else if (vals[pos].ident == PVI_TOP) {
                    offsets[1] = vals[pos + 1];
                    has_offset[1] = 1;
                }
            } else if (vals[pos].ident == PVI_LEFT) {
                css_pv_set(pool, &offsets[0], PVK_NUM, "0", PVI_NONE);
                has_offset[0] = 1;
            } else if (vals[pos].ident == PVI_TOP) {
                css_pv_set(pool, &offsets[1], PVK_NUM, "0", PVI_NONE);
                has_offset[1] = 1;
            } else if (vals[pos].ident == PVI_RIGHT) {
                css_pv_set(pool, &offsets[0], PVK_PCT, "100%", PVI_NONE);
                has_offset[0] = 1;
                vals[pos].ident = PVI_LEFT;
            } else if (vals[pos].ident == PVI_BOTTOM) {
                css_pv_set(pool, &offsets[1], PVK_PCT, "100%", PVI_NONE);
                has_offset[1] = 1;
                vals[pos].ident = PVI_TOP;
            }
        }
        if (vals[0].ident == PVI_CENTER || vals[axis].ident == PVI_CENTER) {
            if (vals[0].ident == PVI_LEFT || vals[axis].ident == PVI_LEFT) {
                offsets_len = 1;
            } else if (vals[0].ident == PVI_TOP || vals[axis].ident == PVI_TOP) {
                css_pv_set(pool, &offsets[0], PVK_PCT, "50%", PVI_NONE);
                has_offset[0] = 1;
            }
        }
        if (has_offset[0] && (offsets_len == 1 || has_offset[1])) {
            /* the guard above makes has_offset[which] true for every which in [0, offsets_len) */
            Py_ssize_t rebuilt = 0;
            for (int which = 0; which < offsets_len; which++) {
                vals[rebuilt++] = offsets[which];
            }
            count = rebuilt;
        }
    }
    if (count == 1 || count == 2) {
        if (count == 1 && (vals[0].ident == PVI_TOP || vals[0].ident == PVI_BOTTOM)) {
            *count_io = count;
            return;
        }
        if (count == 2 && ((vals[0].ident == PVI_TOP || vals[0].ident == PVI_BOTTOM) ||
                           (vals[1].ident == PVI_LEFT || vals[1].ident == PVI_RIGHT))) {
            pos_val swap = vals[0];
            vals[0] = vals[1];
            vals[1] = swap;
        }
        Py_ssize_t limit = count;
        Py_ssize_t pos = 0;
        while (pos < limit) {
            pos_val *value = &vals[pos];
            if (value->kind == PVK_IDENT) {
                if (value->ident == PVI_LEFT || value->ident == PVI_TOP) {
                    css_pv_set(pool, value, PVK_NUM, "0", PVI_NONE);
                } else if (value->ident == PVI_RIGHT || value->ident == PVI_BOTTOM) {
                    css_pv_set(pool, value, PVK_PCT, "100%", PVI_NONE);
                } else if (value->ident == PVI_CENTER) {
                    if (pos == 0) {
                        css_pv_set(pool, value, PVK_PCT, "50%", PVI_NONE);
                    } else {
                        css_pv_del(vals, &count, 1);
                        limit--;
                    }
                }
            } else if (pos == 1 && value->kind == PVK_PCT && css_run_ieq(pool->data + value->off, value->len, "50%")) {
                css_pv_del(vals, &count, 1);
                limit--;
            } else if (value->kind == PVK_PCT && pool->data[value->off] == '0') {
                css_pv_set(pool, value, PVK_NUM, "0", PVI_NONE);
            }
            pos++;
        }
    }
    *count_io = count;
}

/* Convert a component run [start, end) to position values and normalize it into out_run; returns the value count. */
static Py_ssize_t css_position_run(css_buf *pool, const css_comp *items, Py_ssize_t count, pos_val *out_run) {
    Py_ssize_t run_count = 0;
    for (Py_ssize_t index = 0; index < count && run_count < 64; index++) {
        const css_comp *comp = &items[index];
        pos_val value;
        value.off = comp->off;
        value.len = comp->len;
        value.ident = PVI_NONE;
        if (comp->kind == CK_IDENT) {
            value.kind = PVK_IDENT;
            value.ident = css_pv_ident_of(pool, comp);
        } else if (comp->kind == CK_DIM && pool->data[comp->off + comp->len - 1] == '%') {
            /* a CK_DIM component always has at least one character, so its last byte is in bounds */
            value.kind = PVK_PCT;
        } else if (comp->kind == CK_NUM) {
            value.kind = PVK_NUM;
        } else {
            value.kind = PVK_LEN;
        }
        out_run[run_count++] = value;
    }
    css_normalize_position_run(pool, out_run, &run_count);
    return run_count;
}

static void css_emit_position_run(comp_vec *result, const pos_val *run, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        css_comp comp;
        comp.off = run[index].off;
        comp.len = run[index].len;
        comp.isfunc = 0;
        comp.kind = run[index].kind == PVK_PCT ? CK_PCT : (run[index].kind == PVK_IDENT ? CK_IDENT : CK_NUM);
        comp_vec_push(result, comp);
    }
}

static void css_handle_background_position(css_buf *pool, comp_vec *comps) {
    comp_vec result = {NULL, 0, 0, 0};
    css_comp run_items[64];
    pos_val run[64];
    Py_ssize_t run_count = 0;
    int first_run = 1;
    for (Py_ssize_t index = 0; index <= comps->len; index++) {
        int at_comma = index < comps->len && comps->items[index].kind == CK_SEP &&
                       pool->data[comps->items[index].off] == ','; /* a SEP comp is always len 1 */
        if (index == comps->len || at_comma) {
            Py_ssize_t resolved = css_position_run(pool, run_items, run_count, run);
            if (!first_run) {
                css_comp sep;
                sep.off = pool_cstr(pool, ",");
                sep.len = 1;
                sep.isfunc = 2;
                sep.kind = CK_SEP;
                comp_vec_push(&result, sep);
            }
            first_run = 0;
            css_emit_position_run(&result, run, resolved);
            run_count = 0;
        } else if (run_count < 64) {
            run_items[run_count++] = comps->items[index];
        }
    }
    css_free(comps->items);
    *comps = result;
}

/* Replace vec[at, at+remove) with the add_count components from add (which must not alias vec). Every call site
   replaces a run with no more components than it removes -- minification never expands a value -- so this only ever
   shrinks or rewrites in place and never has to grow the backing store. */
static void comp_vec_splice(comp_vec *vec, Py_ssize_t at, Py_ssize_t remove, const css_comp *add,
                            Py_ssize_t add_count) {
    if (add_count != remove) {
        memmove(&vec->items[at + add_count], &vec->items[at + remove],
                (size_t)(vec->len - at - remove) * sizeof(css_comp));
    }
    for (Py_ssize_t index = 0; index < add_count; index++) {
        vec->items[at + index] = add[index];
    }
    vec->len += add_count - remove;
}

static int css_func_name_is(css_buf *pool, const css_comp *comp, const char *name) {
    if (comp->kind != CK_FUNC) {
        return 0;
    }
    /* a CK_FUNC component always contains its '(' within comp->len, so the scan always finds it before the end */
    Py_ssize_t paren = 0;
    while (pool->data[comp->off + paren] != '(') {
        paren++;
    }
    return css_run_ieq(pool->data + comp->off, paren, name);
}

static int css_comp_is_lp(css_buf *pool, const css_comp *comp) {
    /* a percentage value component is a CK_DIM (unit '%') here; CK_PCT is only produced for position output, which is
       never re-examined through this predicate, so a bare number or dimension is the length/percentage case */
    if (comp->kind == CK_NUM || comp->kind == CK_DIM) {
        return 1;
    }
    return css_func_name_is(pool, comp, "calc") || css_func_name_is(pool, comp, "min") ||
           css_func_name_is(pool, comp, "max") || css_func_name_is(pool, comp, "clamp");
}

static int css_comp_is_position_keyword(css_buf *pool, const css_comp *comp) {
    return css_comp_kw(pool, comp, "left") || css_comp_kw(pool, comp, "right") || css_comp_kw(pool, comp, "top") ||
           css_comp_kw(pool, comp, "bottom") || css_comp_kw(pool, comp, "center");
}

static int css_comp_is_repeat_keyword(css_buf *pool, const css_comp *comp) {
    return comp->kind == CK_IDENT && (css_comp_kw(pool, comp, "space") || css_comp_kw(pool, comp, "round") ||
                                      css_comp_kw(pool, comp, "repeat") || css_comp_kw(pool, comp, "no-repeat"));
}

static void css_position_single(css_buf *pool, const css_comp *items, Py_ssize_t count, comp_vec *out) {
    pos_val run[64];
    Py_ssize_t resolved = css_position_run(pool, items, count, run);
    css_emit_position_run(out, run, resolved);
}

/* Resolve one background layer in place over a single comma-separated run (CSS Backgrounds 3 §3.10). */
static void css_background_run(css_buf *pool, comp_vec *run) {
    int had_tokens = run->len > 0;
    Py_ssize_t index = 0;
    while (index < run->len) {
        css_comp *comp = &run->items[index];
        /* css_handle_background splits on ',' SEPs, so the only SEP left in a layer run is the '/' size separator */
        if (comp->kind == CK_SEP) {
            int first_size = index + 1 < run->len && (css_comp_is_lp(pool, &run->items[index + 1]) ||
                                                      css_comp_kw(pool, &run->items[index + 1], "auto"));
            if (first_size) {
                int second_size = index + 2 < run->len && (css_comp_is_lp(pool, &run->items[index + 2]) ||
                                                           css_comp_kw(pool, &run->items[index + 2], "auto"));
                if (second_size) {
                    css_comp size[2] = {run->items[index + 1], run->items[index + 2]};
                    Py_ssize_t size_len = css_comp_kw(pool, &size[1], "auto") ? 1 : 2;
                    if (size_len == 1 && css_comp_kw(pool, &size[0], "auto")) {
                        comp_vec_splice(run, index, 3, NULL, 0);
                        index--;
                    } else {
                        comp_vec_splice(run, index + 1, 2, size, size_len);
                    }
                } else if (css_comp_kw(pool, &run->items[index + 1], "auto")) {
                    comp_vec_splice(run, index, 2, NULL, 0);
                    index--;
                }
            }
        }
        index++;
    }
    Py_ssize_t padding_box_at = -1;
    index = 0;
    while (index < run->len) {
        css_minify_color_comp(pool, &run->items[index]);
        css_comp *comp = &run->items[index];
        if (comp->kind == CK_IDENT) {
            css_comp *next = index + 1 < run->len ? &run->items[index + 1] : NULL;
            if (next != NULL && css_comp_is_repeat_keyword(pool, comp) && css_comp_is_repeat_keyword(pool, next)) {
                css_comp pair[2] = {run->items[index], run->items[index + 1]};
                Py_ssize_t repeat = css_collapse_repeat_run(pool, pair, 2);
                if (repeat == 1 && css_comp_kw(pool, &pair[0], "repeat")) {
                    comp_vec_splice(run, index, 2, NULL, 0);
                    index--;
                } else {
                    comp_vec_splice(run, index, 2, pair, repeat);
                }
                index++;
                continue;
            }
            if (css_comp_kw(pool, comp, "none") || css_comp_kw(pool, comp, "scroll")) {
                /* a transparent background-color reaches here as the hash #0000 (handled below), never the keyword */
                comp_vec_splice(run, index, 1, NULL, 0);
                continue;
            }
            if (css_comp_kw(pool, comp, "border-box") || css_comp_kw(pool, comp, "padding-box")) {
                if (padding_box_at == -1 && css_comp_kw(pool, comp, "padding-box")) {
                    padding_box_at = index;
                } else if (padding_box_at != -1 && css_comp_kw(pool, comp, "border-box")) {
                    comp_vec_splice(run, index, 1, NULL, 0);
                    comp_vec_splice(run, padding_box_at, 1, NULL, 0);
                    index -= 2;
                }
                index++;
                continue;
            }
        } else if (comp->kind == CK_HASH && comp_ieq(pool, comp, "#0000")) {
            comp_vec_splice(run, index, 1, NULL, 0);
            continue;
        } else if (css_func_name_is(pool, comp, "var")) {
            index++;
            continue;
        }
        if (css_comp_is_lp(pool, &run->items[index]) || css_comp_is_position_keyword(pool, &run->items[index])) {
            Py_ssize_t scan = index + 1;
            while (scan < run->len &&
                   (css_comp_is_position_keyword(pool, &run->items[scan]) || css_comp_is_lp(pool, &run->items[scan]))) {
                scan++;
            }
            comp_vec position = {NULL, 0, 0, 0};
            css_position_single(pool, &run->items[index], scan - index, &position);
            /* in a single background layer run the only SEP is the '/' size separator, always len 1 */
            int has_size = scan < run->len && run->items[scan].kind == CK_SEP;
            if (!has_size && position.len == 2 && css_comp_is_zero(pool, &position.items[0]) &&
                css_comp_is_zero(pool, &position.items[1])) {
                if (run->len == 2) {
                    css_set_comp(pool, &run->items[index], "0", CK_NUM);
                    css_set_comp(pool, &run->items[index + 1], "0", CK_NUM);
                    index++;
                } else {
                    comp_vec_splice(run, index, scan - index, NULL, 0);
                    index--;
                }
            } else {
                comp_vec_splice(run, index, scan - index, position.items, position.len);
                index += position.len - 1;
            }
            css_free(position.items);
        }
        index++;
    }
    if (had_tokens && run->len == 0) {
        css_comp zero_x;
        css_comp zero_y;
        css_set_comp(pool, &zero_x, "0", CK_NUM);
        css_set_comp(pool, &zero_y, "0", CK_NUM);
        comp_vec_push(run, zero_x);
        comp_vec_push(run, zero_y);
    }
}

static void css_handle_background(css_buf *pool, comp_vec *comps) {
    comp_vec result = {NULL, 0, 0, 0};
    Py_ssize_t start = 0;
    int first_run = 1;
    for (Py_ssize_t index = 0; index <= comps->len; index++) {
        int at_comma = index < comps->len && comps->items[index].kind == CK_SEP &&
                       pool->data[comps->items[index].off] == ','; /* a SEP comp is always len 1 */
        if (index == comps->len || at_comma) {
            comp_vec run = {NULL, 0, 0, 0};
            for (Py_ssize_t pos = start; pos < index; pos++) {
                comp_vec_push(&run, comps->items[pos]);
            }
            css_background_run(pool, &run);
            if (!first_run) {
                css_comp sep;
                sep.off = pool_cstr(pool, ",");
                sep.len = 1;
                sep.isfunc = 2;
                sep.kind = CK_SEP;
                comp_vec_push(&result, sep);
            }
            first_run = 0;
            for (Py_ssize_t pos = 0; pos < run.len; pos++) {
                comp_vec_push(&result, run.items[pos]);
            }
            css_free(run.items);
            start = index + 1;
        }
    }
    css_free(comps->items);
    *comps = result;
}

static int css_flex_is_zero(css_buf *pool, const css_comp *comp) {
    Py_ssize_t len = comp->len;
    while (len > 0 && pool->data[comp->off + len - 1] == '%') {
        len--;
    }
    return len == 1 && pool->data[comp->off] == '0';
}

static int css_comp_single_digit(css_buf *pool, const css_comp *comp) {
    return comp->len == 1 && pool->data[comp->off] >= '0' && pool->data[comp->off] <= '9';
}

/* Collapse the flex shorthand to its shortest equivalent (CSS Flexbox 1 §7.1.1, the flex keyword expansions). */
static void css_handle_flex(css_buf *pool, comp_vec *comps) {
    if (comps->len == 3 && css_comp_single_digit(pool, &comps->items[0]) &&
        css_comp_single_digit(pool, &comps->items[1])) {
        int zero0 = comp_ieq(pool, &comps->items[0], "0");
        int one0 = comp_ieq(pool, &comps->items[0], "1");
        int one1 = comp_ieq(pool, &comps->items[1], "1");
        if (comp_ieq(pool, &comps->items[2], "auto")) {
            if (zero0 && one1) {
                css_set_comp(pool, &comps->items[0], "initial", CK_IDENT);
                comps->len = 1;
            } else if (one0 && one1) {
                css_set_comp(pool, &comps->items[0], "auto", CK_IDENT);
                comps->len = 1;
            } else if (zero0 && comp_ieq(pool, &comps->items[1], "0")) {
                css_set_comp(pool, &comps->items[0], "none", CK_IDENT);
                comps->len = 1;
            }
        } else if (one1 && css_flex_is_zero(pool, &comps->items[2])) {
            comps->len = 1;
        } else if (css_flex_is_zero(pool, &comps->items[2])) {
            comps->len = 2;
        }
    } else if (comps->len == 2 && comps->items[0].kind == CK_NUM && comps->items[1].kind != CK_NUM &&
               css_flex_is_zero(pool, &comps->items[1])) {
        comps->len = 1;
    }
}

static int css_is_name_start(css_char character) {
    return (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') || character == '_';
}

/* Whether text is a valid <ident-token> (CSS Syntax 3 §4.3.11): a name-start (ASCII letter or _), or a leading "-"
   followed by a name-start or a second "-" (a custom-ident), then name code points. Decides when a quoted
   attribute/local() value can drop its quotes; a leading digit, lone "-", escape, or non-ASCII keeps them. */
static int css_is_ident_string(const css_char *text, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    Py_ssize_t index;
    if (text[0] == '-') {
        if (len < 2 || !(text[1] == '-' || css_is_name_start(text[1]))) {
            return 0;
        }
        index = 2;
    } else if (css_is_name_start(text[0])) {
        index = 1;
    } else {
        return 0;
    }
    for (; index < len; index++) {
        css_char character = text[index];
        if (!(css_is_name_start(character) || (character >= '0' && character <= '9') || character == '-')) {
            return 0;
        }
    }
    return 1;
}

/* Lower-case a quoted font-family name, dropping the quotes when every space-separated word is a bare identifier. */
/* A quoted family name equal to a generic-family or CSS-wide keyword must keep its quotes: unquoted, "serif" would
   become the generic serif family and "inherit" the CSS-wide keyword, both different from a family of that name. */
static int css_is_reserved_family(const css_char *text, Py_ssize_t len) {
    static const char *const reserved[] = {"serif",     "sans-serif", "monospace",     "cursive",      "fantasy",
                                           "system-ui", "ui-serif",   "ui-sans-serif", "ui-monospace", "ui-rounded",
                                           "math",      "emoji",      "fangsong",      "inherit",      "initial",
                                           "unset",     "revert",     "revert-layer",  "default"};
    for (size_t index = 0; index < sizeof(reserved) / sizeof(reserved[0]); index++) {
        if (css_run_ieq(text, len, reserved[index])) {
            return 1;
        }
    }
    return 0;
}

static css_comp css_font_family_comp(css_buf *pool, css_comp comp) {
    if (comp.kind != CK_STR || comp.len <= 2) {
        return comp;
    }
    css_char quote = pool->data[comp.off];
    Py_ssize_t body_start = comp.off + 1;
    /* css_minify_string always re-emits the matching closing quote, so a CK_STR comp always ends with it */
    Py_ssize_t body_len = comp.len - 2;
    css_buf lowered = {NULL, 0, 0, 0};
    for (Py_ssize_t index = 0; index < body_len; index++) {
        cbuf_putc(&lowered, css_lower(pool->data[body_start + index]));
    }
    int all_ident =
        !css_is_reserved_family(lowered.data, lowered.len); /* the comp.len <= 2 guard ensures lowered.len > 0 */
    Py_ssize_t word_start = 0;
    for (Py_ssize_t index = 0; all_ident && index <= lowered.len; index++) {
        if (index == lowered.len || lowered.data[index] == ' ') {
            if (!css_is_ident_string(lowered.data + word_start, index - word_start)) {
                all_ident = 0;
            }
            word_start = index + 1;
        }
    }
    css_comp result;
    if (all_ident) {
        result.off = pool_run(pool, lowered.data, lowered.len);
        result.len = lowered.len;
        result.isfunc = 0;
        result.kind = CK_IDENT;
    } else {
        result.off = pool->len;
        cbuf_putc(pool, quote);
        cbuf_put_run(pool, lowered.data, lowered.len);
        cbuf_putc(pool, quote);
        result.len = pool->len - result.off;
        result.isfunc = 0;
        result.kind = CK_STR;
    }
    cbuf_free(&lowered);
    return result;
}

static void css_handle_font_family(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        comps->items[index] = css_font_family_comp(pool, comps->items[index]);
    }
}

static int css_is_font_size_kw(css_buf *pool, const css_comp *comp) {
    static const char *const sizes[] = {"xx-small", "x-small",  "small",   "medium", "large",
                                        "x-large",  "xx-large", "smaller", "larger"};
    if (comp->kind != CK_IDENT) {
        return 0;
    }
    for (size_t index = 0; index < sizeof(sizes) / sizeof(sizes[0]); index++) {
        if (comp_ieq(pool, comp, sizes[index])) {
            return 1;
        }
    }
    return 0;
}

static int css_comp_is_slash(css_buf *pool, const css_comp *comp) {
    if (comp->kind != CK_SEP) {
        return 0;
    }
    return pool->data[comp->off] == '/'; /* a SEP comp is always len 1 */
}

static int css_comp_is_comma(css_buf *pool, const css_comp *comp) {
    if (comp->kind != CK_SEP) {
        return 0;
    }
    return pool->data[comp->off] == ','; /* a SEP comp is always len 1 */
}

/* Minify the font shorthand (CSS Fonts 4 §2.7): lower-case the family, normalize font-weight (normal->drop, bold->700,
   400->drop) and drop a normal line-height. The shorthand resets every pre-size longhand to its initial value, so
   dropping a `normal` token there is safe whichever longhand it nominally binds to. */
static void css_handle_font(css_buf *pool, comp_vec *comps) {
    Py_ssize_t non_sep = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind != CK_SEP) {
            non_sep++;
        }
    }
    if (non_sep <= 1) {
        return;
    }
    comp_vec values = {NULL, 0, 0, 0};
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        comp_vec_push(&values, comps->items[index]);
    }
    Py_ssize_t family = values.len - 1;
    for (Py_ssize_t index = 2; index < values.len; index++) {
        if (css_comp_is_comma(pool, &values.items[index])) {
            family = index - 1;
            break;
        }
    }
    family--;
    while (family > 0) {
        if (css_comp_is_slash(pool, &values.items[family - 1])) {
            break;
        }
        if (values.items[family].kind != CK_IDENT && values.items[family].kind != CK_STR) {
            break;
        }
        if (css_is_font_size_kw(pool, &values.items[family]) || css_comp_kw(pool, &values.items[family], "inherit") ||
            css_comp_kw(pool, &values.items[family], "initial") || css_comp_kw(pool, &values.items[family], "unset")) {
            break;
        }
        family--;
    }
    for (Py_ssize_t index = family + 1; index < values.len; index++) {
        values.items[index] = css_font_family_comp(pool, values.items[index]);
    }
    /* family was set to at most values.len-1 then decremented at least once above, so family+1 < values.len holds */
    if (pool->data[values.items[family + 1].off] == '-') {
        css_buf quoted = {NULL, 0, 0, 0};
        cbuf_putc(&quoted, '\'');
        cbuf_put_run(&quoted, pool->data + values.items[family + 1].off, values.items[family + 1].len);
        cbuf_putc(&quoted, '\'');
        values.items[family + 1].off = pool_run(pool, quoted.data, quoted.len);
        values.items[family + 1].len = quoted.len;
        values.items[family + 1].kind = CK_STR;
        cbuf_free(&quoted);
    }
    if (family > 0) {
        Py_ssize_t index = family;
        if (family > 1 && css_comp_is_slash(pool, &values.items[family - 1])) {
            if (css_comp_kw(pool, &values.items[family], "normal")) {
                comp_vec_splice(&values, family - 1, 2, NULL, 0);
            }
            index -= 2;
        }
        index--;
        while (index > -1) {
            if (css_comp_kw(pool, &values.items[index], "normal") ||
                (values.items[index].kind == CK_NUM && comp_ieq(pool, &values.items[index], "400"))) {
                comp_vec_splice(&values, index, 1, NULL, 0);
            } else if (css_comp_kw(pool, &values.items[index], "bold")) {
                css_set_comp(pool, &values.items[index], "700", CK_NUM);
            }
            index--;
        }
    }
    css_free(comps->items);
    *comps = values;
}

/* Dispatch the per-property shorthand handler over the rendered components. */
static void css_apply_handler(css_buf *pool, const css_char *prop, Py_ssize_t prop_len, comp_vec *comps) {
    int has_sep = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind == CK_SEP) {
            has_sep = 1;
        }
    }
    if (!has_sep && css_prop_in(prop, prop_len, CSS_BOX_PROPS, sizeof(CSS_BOX_PROPS) / sizeof(char *))) {
        css_handle_box(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "background")) {
        css_handle_background(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "background-position")) {
        css_handle_background_position(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "background-repeat")) {
        css_handle_runs(pool, comps, css_collapse_repeat_run);
    } else if (css_run_ieq(prop, prop_len, "background-size")) {
        css_handle_runs(pool, comps, css_collapse_size_run);
    } else if (css_run_ieq(prop, prop_len, "border") || css_run_ieq(prop, prop_len, "border-top") ||
               css_run_ieq(prop, prop_len, "border-bottom") || css_run_ieq(prop, prop_len, "border-left") ||
               css_run_ieq(prop, prop_len, "border-right") || css_run_ieq(prop, prop_len, "outline") ||
               css_run_ieq(prop, prop_len, "column-rule") || css_run_ieq(prop, prop_len, "text-decoration") ||
               css_run_ieq(prop, prop_len, "text-emphasis")) {
        css_handle_color_drop(pool, comps, prop, prop_len);
    } else if (css_run_ieq(prop, prop_len, "border-color")) {
        css_handle_border_color(pool, comps);
    } else if (css_prop_in(prop, prop_len, CSS_CURRENTCOLOR_INIT, sizeof(CSS_CURRENTCOLOR_INIT) / sizeof(char *)) ||
               css_prop_in(prop, prop_len, CSS_SINGLE_COLOR, sizeof(CSS_SINGLE_COLOR) / sizeof(char *))) {
        css_handle_color_single(pool, comps, prop, prop_len);
    } else if (css_run_ieq(prop, prop_len, "font-weight")) {
        css_handle_font_weight(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "flex-basis")) {
        css_handle_flex_basis(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "flex-grow") || css_run_ieq(prop, prop_len, "order")) {
        css_handle_flex_grow(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "flex-shrink")) {
        css_handle_flex_shrink(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "box-shadow")) {
        css_handle_runs(pool, comps, css_collapse_box_shadow_run);
    } else if (css_run_ieq(prop, prop_len, "text-shadow")) {
        css_handle_text_shadow(pool, comps);
    } else if (!has_sep && css_run_ieq(prop, prop_len, "flex")) {
        css_handle_flex(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "font")) {
        css_handle_font(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "font-family")) {
        css_handle_font_family(pool, comps);
    }
}

/* Minify a declaration value's tokens [start, end) into out. raw covers custom properties (--*). */
/* scratch is a reusable component vector owned by the caller: resetting its length and reusing its backing buffer
   across declarations avoids a malloc/free for every value. */
static void css_minify_value(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *prop,
                             Py_ssize_t prop_len, int raw, comp_vec *scratch, css_buf *out) {
    if (raw) {
        css_render_raw_value(vec, start, end, out);
        return;
    }
    /* prop points into the pool, which the component rendering below grows (and so reallocates); copy the name to a
       stable buffer first. No real property name approaches this length, so a longer one is truncated and simply
       matches no handler. */
    css_char name[128];
    Py_ssize_t name_len = prop_len < 128 ? prop_len : 128;
    memcpy(name, prop, (size_t)name_len * sizeof(css_char));
    if (css_run_ieq(name, name_len, "filter") || css_run_ieq(name, name_len, "-ms-filter")) {
        css_handle_filter(pool, vec, start, end, name, name_len, out);
        return;
    }
    if (css_run_ieq(name, name_len, "unicode-range") && css_handle_unicode_range(vec, start, end, out)) {
        return;
    }
    scratch->len = 0;
    css_render_components(pool, vec, start, end, css_prop_is_color(name, name_len), scratch);
    css_apply_handler(pool, name, name_len, scratch);
    css_assemble(pool, scratch, out);
}

#endif /* TURBOHTML_CSS_SHORTHAND_H */
