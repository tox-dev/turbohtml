#ifndef TURBOHTML_CSS_SHORTHAND_H
#define TURBOHTML_CSS_SHORTHAND_H

/* A background-position value during the keyword<->offset normalization passes. data refers to pool text by
   (off, len); ident records the edge keyword so the normalizer can rewrite right/bottom to left/top offsets. */
typedef enum { PVK_COMMA, PVK_IDENT, PVK_PCT, PVK_NUM, PVK_LEN } pv_kind;
typedef enum { PVI_NONE, PVI_LEFT, PVI_RIGHT, PVI_TOP, PVI_BOTTOM, PVI_CENTER } pv_ident;
typedef enum { PVR_INVALID, PVR_HORIZONTAL, PVR_VERTICAL, PVR_CENTER, PVR_LENGTH } pv_role;
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

CSS_NOINLINE static int css_pv_is_zero(css_buf *pool, const pos_val *value) {
    if (value->kind == PVK_IDENT) {
        return 0;
    }
    if (value->kind == PVK_LEN) {
        return value->len == 1;
    }
    for (Py_ssize_t index = 0; index < value->len; index++) {
        css_char character = pool->data[value->off + index];
        if (character == '%' || character == 'e' || character == 'E') {
            break;
        }
        if (character > '0') {
            return 0;
        }
    }
    return 1;
}

static void css_pv_del(pos_val *vals, Py_ssize_t *count, Py_ssize_t index) {
    for (Py_ssize_t pos = index; pos + 1 < *count; pos++) {
        vals[pos] = vals[pos + 1];
    }
    (*count)--;
}

static pv_role css_position_role(const pos_val *value) {
    if (value->kind != PVK_IDENT) {
        return PVR_LENGTH;
    }
    if (value->ident == PVI_LEFT || value->ident == PVI_RIGHT) {
        return PVR_HORIZONTAL;
    }
    if (value->ident == PVI_TOP || value->ident == PVI_BOTTOM) {
        return PVR_VERTICAL;
    }
    return value->ident == PVI_CENTER ? PVR_CENTER : PVR_INVALID;
}

static int css_position_order_valid(const pos_val *vals, Py_ssize_t count) {
    pv_role first = css_position_role(&vals[0]);
    pv_role second = css_position_role(&vals[1]);
    if (count == 2) {
        static const unsigned int second_roles[] = {0, (1u << PVR_VERTICAL) | (1u << PVR_CENTER) | (1u << PVR_LENGTH),
                                                    (1u << PVR_HORIZONTAL) | (1u << PVR_CENTER),
                                                    (1u << PVR_HORIZONTAL) | (1u << PVR_VERTICAL) | (1u << PVR_CENTER) |
                                                        (1u << PVR_LENGTH),
                                                    (1u << PVR_VERTICAL) | (1u << PVR_CENTER) | (1u << PVR_LENGTH)};
        return (second_roles[first] & (1u << second)) != 0;
    }
    pv_role third = css_position_role(&vals[2]);
    if (count == 3) {
        return ((second == PVR_LENGTH) &
                (((first == PVR_HORIZONTAL) & ((third == PVR_VERTICAL) | (third == PVR_CENTER))) |
                 ((first == PVR_VERTICAL) & ((third == PVR_HORIZONTAL) | (third == PVR_CENTER))))) |
               ((third == PVR_LENGTH) &
                (((second == PVR_HORIZONTAL) & ((first == PVR_VERTICAL) | (first == PVR_CENTER))) |
                 ((second == PVR_VERTICAL) & ((first == PVR_HORIZONTAL) | (first == PVR_CENTER)))));
    }
    pv_role fourth = css_position_role(&vals[3]);
    return (second == PVR_LENGTH) & (fourth == PVR_LENGTH) &
           (((first == PVR_HORIZONTAL) & (third == PVR_VERTICAL)) |
            ((first == PVR_VERTICAL) & (third == PVR_HORIZONTAL)));
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
                    Py_ssize_t digits_len = vals[pos + 1].len - 1;
                    if (digits_len <= 18) {
                        const css_char *digits = pool->data + vals[pos + 1].off;
                        int negative = digits[0] == '-';
                        Py_ssize_t index = negative;
                        long long percentage = 0;
                        for (; index < digits_len; index++) {
                            if (digits[index] < '0' || digits[index] > '9') {
                                break;
                            }
                            percentage = percentage * 10 + (long long)(digits[index] - '0');
                        }
                        if (index != digits_len) {
                            continue;
                        }
                        if (negative) {
                            percentage = -percentage;
                        }
                        char flipped[24];
                        snprintf(flipped, sizeof(flipped), "%lld%%", 100 - percentage);
                        vals[pos + 1].off = pool_cstr(pool, flipped);
                        vals[pos + 1].len = (Py_ssize_t)strlen(flipped);
                        if (vals[pos].ident == PVI_RIGHT) {
                            css_pv_set(pool, &vals[pos], PVK_IDENT, "left", PVI_LEFT);
                        } else {
                            css_pv_set(pool, &vals[pos], PVK_IDENT, "top", PVI_TOP);
                        }
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
    if (run_count < 2 || run_count > 4 || css_position_order_valid(out_run, run_count)) {
        css_normalize_position_run(pool, out_run, &run_count);
    }
    return run_count;
}

static void css_emit_position_run(comp_vec *result, const pos_val *run, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        css_comp comp;
        comp.off = run[index].off;
        comp.len = run[index].len;
        comp.isfunc = 0;
        comp.kind = run[index].kind == PVK_PCT     ? CK_PCT
                    : run[index].kind == PVK_IDENT ? CK_IDENT
                    : run[index].kind == PVK_LEN   ? CK_DIM
                                                   : CK_NUM;
        comp_vec_push(result, comp);
    }
}

static void css_handle_background_position(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        const css_comp *comp = &comps->items[index];
        if (comp->kind == CK_FUNC || comp->kind == CK_STR || comp->kind == CK_DELIM ||
            (comp->kind == CK_SEP && pool->data[comp->off] != ',')) {
            return;
        }
    }
    comp_vec result = {NULL, 0, 0, pool->oom};
    css_comp run_items[64];
    pos_val run[64];
    Py_ssize_t run_count = 0;
    int first_run = 1;
    for (Py_ssize_t index = 0; index <= comps->len; index++) {
        int at_comma = index < comps->len && comps->items[index].kind == CK_SEP;
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
    return css_run_ieq(pool->data + comp->off, css_func_name_len(pool, comp), name);
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

static int css_background_non_color(css_buf *pool, const css_comp *comp) {
    static const char *const keywords[] = {
        "fixed",    "scroll",   "local",  "none",    "border-box", "padding-box", "content-box",
        "repeat-x", "repeat-y", "cover",  "contain", "auto",       "left",        "right",
        "top",      "bottom",   "center", "space",   "round",      "repeat",      "no-repeat",
    };
    static const char *const functions[] = {
        "image-set",
        "linear-gradient",
        "radial-gradient",
        "conic-gradient",
        "repeating-linear-gradient",
        "repeating-radial-gradient",
        "repeating-conic-gradient",
        "calc",
        "min",
        "max",
        "clamp",
    };
    if (comp->kind == CK_SEP || comp->kind == CK_URL || comp->kind == CK_NUM || comp->kind == CK_DIM ||
        comp->kind == CK_PCT) {
        return 1;
    }
    if (comp->kind == CK_IDENT) {
        if (comp->len == 5 && css_comp_kw(pool, comp, "fixed")) {
            return 1;
        }
        return css_prop_in(pool->data + comp->off, comp->len, keywords, sizeof(keywords) / sizeof(char *));
    }
    return comp->kind == CK_FUNC && css_prop_in(pool->data + comp->off, css_func_name_len(pool, comp), functions,
                                                sizeof(functions) / sizeof(char *));
}

static CSS_FORCEINLINE int css_background_has_other_color(css_buf *pool, const comp_vec *run, Py_ssize_t transparent) {
    if (run->len == 2) {
        return !css_background_non_color(pool, &run->items[1 - transparent]);
    }
    for (Py_ssize_t index = 0; index < run->len; index++) {
        if (index != transparent && !css_background_non_color(pool, &run->items[index])) {
            return 1;
        }
    }
    return 0;
}

static void css_position_single(css_buf *pool, const css_comp *items, Py_ssize_t count, comp_vec *out) {
    pos_val run[64];
    Py_ssize_t resolved = css_position_run(pool, items, count, run);
    css_emit_position_run(out, run, resolved);
}

CSS_NOINLINE static int css_background_unshortenable(css_buf *pool, const comp_vec *run) {
    static const char *const image_funcs[] = {"image-set",
                                              "linear-gradient",
                                              "radial-gradient",
                                              "conic-gradient",
                                              "repeating-linear-gradient",
                                              "repeating-radial-gradient",
                                              "repeating-conic-gradient"};
    static const char *const position_funcs[] = {"calc", "min", "max", "clamp"};
    int attachments = 0;
    int images = 0;
    int repeats = 0;
    int boxes = 0;
    int positions = 0;
    int position_open = 0;
    int previous_repeat = 0;
    for (Py_ssize_t pos = 0; pos < run->len; pos++) {
        const css_comp *comp = &run->items[pos];
        if (comp->kind == CK_SEP) {
            if (!position_open || pos + 1 == run->len) {
                return 1;
            }
            const css_comp *size = &run->items[++pos];
            if (css_comp_kw(pool, size, "cover") || css_comp_kw(pool, size, "contain")) {
                position_open = 0;
                previous_repeat = 0;
                continue;
            }
            if (!css_comp_is_lp(pool, size) && !css_comp_kw(pool, size, "auto")) {
                return 1;
            }
            if (pos + 1 < run->len &&
                (css_comp_is_lp(pool, &run->items[pos + 1]) || css_comp_kw(pool, &run->items[pos + 1], "auto"))) {
                pos++;
            }
            position_open = 0;
            previous_repeat = 0;
            continue;
        }
        int image = comp->kind == CK_URL;
        int repeat = 0;
        int basic_repeat = 0;
        int box = 0;
        int position = comp->kind == CK_NUM || comp->kind == CK_DIM;
        int attachment = 0;
        if (comp->kind == CK_DELIM) {
            return 1;
        }
        if (comp->kind == CK_FUNC) {
            Py_ssize_t name_len = css_func_name_len(pool, comp);
            image = css_prop_in(pool->data + comp->off, name_len, image_funcs, sizeof(image_funcs) / sizeof(char *));
            position =
                css_prop_in(pool->data + comp->off, name_len, position_funcs, sizeof(position_funcs) / sizeof(char *));
        } else if (comp->kind == CK_IDENT) {
            switch (css_lower(pool->data[comp->off])) {
            case 'b':
                box = css_comp_kw(pool, comp, "border-box");
                position = css_comp_kw(pool, comp, "bottom");
                break;
            case 'c':
                if (comp->len == 6) {
                    position = css_comp_kw(pool, comp, "center");
                } else if (comp->len == 11) {
                    box = css_comp_kw(pool, comp, "content-box");
                }
                break;
            case 'f':
                attachment = css_comp_kw(pool, comp, "fixed");
                break;
            case 'i':
                if (css_comp_kw(pool, comp, "inherit") || css_comp_kw(pool, comp, "initial")) {
                    return 1;
                }
                break;
            case 'l':
                attachment = css_comp_kw(pool, comp, "local");
                position = css_comp_kw(pool, comp, "left");
                break;
            case 'n':
                if (comp->len == 4) {
                    image = css_comp_kw(pool, comp, "none");
                } else if (comp->len == 9) {
                    basic_repeat = css_comp_kw(pool, comp, "no-repeat");
                }
                repeat = basic_repeat;
                break;
            case 'p':
                box = css_comp_kw(pool, comp, "padding-box");
                break;
            case 'r':
                if (css_comp_kw(pool, comp, "revert") || css_comp_kw(pool, comp, "revert-layer")) {
                    return 1;
                }
                basic_repeat = css_comp_kw(pool, comp, "repeat") || css_comp_kw(pool, comp, "round");
                repeat = basic_repeat || css_comp_kw(pool, comp, "repeat-x") || css_comp_kw(pool, comp, "repeat-y");
                position = css_comp_kw(pool, comp, "right");
                break;
            case 's':
                attachment = css_comp_kw(pool, comp, "scroll");
                basic_repeat = css_comp_kw(pool, comp, "space");
                repeat = basic_repeat;
                break;
            case 't':
                position = css_comp_kw(pool, comp, "top");
                break;
            case 'u':
                if (css_comp_kw(pool, comp, "unset")) {
                    return 1;
                }
                break;
            default:
                break;
            }
        }
        if (image && ++images > 1) {
            return 1;
        }
        if (box && ++boxes > 2) {
            return 1;
        }
        if (attachment && ++attachments > 1) {
            return 1;
        }
        if (repeat) {
            repeats++;
            if (repeats > 2 || (repeats == 2 && (!previous_repeat || !basic_repeat))) {
                return 1;
            }
        }
        if (position) {
            if (positions && !position_open) {
                return 1;
            }
            positions = 1;
            position_open = 1;
        } else {
            position_open = 0;
        }
        previous_repeat = basic_repeat;
    }
    return 0;
}

/* Resolve one background layer in place over a single comma-separated run (CSS Backgrounds 3 §3.10). */
static void css_background_run(css_buf *pool, comp_vec *run) {
    if (run->len > 1 && css_background_unshortenable(pool, run)) {
        return;
    }
    int had_tokens = run->len > 0;
    Py_ssize_t index = 0;
    while (index < run->len) {
        css_comp *comp = &run->items[index];
        /* css_handle_background splits on ',' SEPs, so the only SEP left in a layer run is the '/' size separator */
        if (comp->kind == CK_SEP) {
            int first_size =
                css_comp_is_lp(pool, &run->items[index + 1]) || css_comp_kw(pool, &run->items[index + 1], "auto");
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
        if (run->items[index].kind == CK_SEP) {
            index += 2;
            if (index < run->len && css_comp_is_lp(pool, &run->items[index])) {
                index++;
            }
            continue;
        }
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
            if (run->len > 1 && css_background_has_other_color(pool, run, index)) {
                return;
            }
            if (run->len == 2) {
                if (index == 0) {
                    run->items[0] = run->items[1];
                }
                run->len = 1;
            } else {
                comp_vec_splice(run, index, 1, NULL, 0);
            }
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
            comp_vec position = {NULL, 0, 0, pool->oom};
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
    comp_vec result = {NULL, 0, 0, pool->oom};
    Py_ssize_t start = 0;
    int first_run = 1;
    for (Py_ssize_t index = 0; index <= comps->len; index++) {
        int at_comma = index < comps->len && comps->items[index].kind == CK_SEP &&
                       pool->data[comps->items[index].off] == ','; /* a SEP comp is always len 1 */
        if (index == comps->len || at_comma) {
            comp_vec run = {NULL, 0, 0, pool->oom};
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

/* browsers resolve an omitted flex-basis to 0%, which a zero length basis is not */
static int css_flex_is_zero(css_buf *pool, const css_comp *comp) {
    return comp_ieq(pool, comp, "0%");
}

/* A zero length after two flex factors may drop its unit: Flexbox 1 §7.1 reads that unitless zero as the basis. */
static void css_flex_basis_drop_zero_unit(css_buf *pool, comp_vec *comps) {
    css_comp *basis = &comps->items[2];
    if (comps->items[0].kind == CK_NUM && comps->items[1].kind == CK_NUM && basis->kind == CK_DIM &&
        pool->data[basis->off] == '0' && css_unit_zero_droppable(pool->data + basis->off + 1, basis->len - 1)) {
        basis->len = 1;
    }
}

static int css_comp_single_digit(css_buf *pool, const css_comp *comp) {
    return comp->len == 1 && pool->data[comp->off] >= '0' && pool->data[comp->off] <= '9';
}

/* Collapse the flex shorthand to its shortest equivalent (CSS Flexbox 1 §7.1.1, the flex keyword expansions). */
static void css_handle_flex(css_buf *pool, comp_vec *comps) {
    if (comps->len == 3) {
        css_flex_basis_drop_zero_unit(pool, comps);
    }
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
    if (comp.len <= 2) {
        return comp;
    }
    css_char quote = pool->data[comp.off];
    Py_ssize_t body_start = comp.off + 1;
    /* css_minify_string always re-emits the matching closing quote, so a CK_STR comp always ends with it */
    Py_ssize_t body_len = comp.len - 2;
    css_buf lowered = {NULL, 0, 0, pool->oom};
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

static inline int css_font_family_quote_isolated(css_buf *pool, const css_comp *items, Py_ssize_t index,
                                                 Py_ssize_t start, Py_ssize_t len) {
    if (index > start && (items[index - 1].kind != CK_SEP || pool->data[items[index - 1].off] != ',')) {
        return 0;
    }
    return index + 1 == len || (items[index + 1].kind == CK_SEP && pool->data[items[index + 1].off] == ',');
}

static void css_handle_font_family(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind != CK_STR) {
            continue;
        }
        if (!css_font_family_quote_isolated(pool, comps->items, index, 0, comps->len)) {
            return;
        }
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

static CSS_NOINLINE void css_quote_leading_hyphen_family(css_buf *pool, css_comp *comp) {
    if (comp->kind != CK_IDENT) {
        return;
    }
    css_buf quoted = {NULL, 0, 0, pool->oom};
    cbuf_putc(&quoted, '\'');
    cbuf_put_run(&quoted, pool->data + comp->off, comp->len);
    cbuf_putc(&quoted, '\'');
    comp->off = pool_run(pool, quoted.data, quoted.len);
    comp->len = quoted.len;
    comp->kind = CK_STR;
    cbuf_free(&quoted);
}

CSS_NOINLINE static int css_font_has_duplicate_weight(css_buf *pool, const css_comp *items, Py_ssize_t count) {
    int seen = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        const css_comp *value = &items[index];
        int is_weight = value->kind == CK_NUM;
        if (value->kind == CK_IDENT) {
            css_char first = css_lower(pool->data[value->off]);
            is_weight = (first == 'b' && (comp_ieq(pool, value, "bold") || comp_ieq(pool, value, "bolder"))) ||
                        (first == 'l' && comp_ieq(pool, value, "lighter"));
        }
        if (is_weight) {
            if (seen) {
                return 1;
            }
            seen = 1;
        }
    }
    return 0;
}

CSS_NOINLINE static int css_font_options_overlong(css_buf *pool, const css_comp *items, Py_ssize_t count) {
    if (count == 5) {
        for (Py_ssize_t index = 0; index + 1 < count; index++) {
            if (css_comp_kw(pool, &items[index], "oblique") && items[index + 1].kind == CK_DIM) {
                return 0;
            }
        }
    }
    return 1;
}

/* Minify the font shorthand (CSS Fonts 4 §2.7): lower-case the family, normalize font-weight (normal->drop, bold->700,
   400->drop) and drop a normal line-height. The shorthand resets every pre-size longhand to its initial value, so
   dropping a `normal` token there is safe whichever longhand it nominally binds to. */
static void css_handle_font(css_buf *pool, comp_vec *comps) {
    Py_ssize_t non_sep = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind == CK_FUNC) {
            return;
        }
        if (comps->items[index].kind != CK_SEP) {
            non_sep++;
        }
    }
    if (non_sep <= 1) {
        return;
    }
    comp_vec values = {NULL, 0, 0, pool->oom};
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
        if (css_is_font_size_kw(pool, &values.items[family])) {
            break;
        }
        if (css_comp_kw(pool, &values.items[family], "inherit") ||
            css_comp_kw(pool, &values.items[family], "initial") || css_comp_kw(pool, &values.items[family], "unset")) {
            css_free(values.items);
            return;
        }
        family--;
    }
    if (family > 1) {
        Py_ssize_t size = family;
        if (css_comp_is_slash(pool, &values.items[family - 1])) {
            size -= 2;
        }
        if (size > 1 && css_font_has_duplicate_weight(pool, values.items, size)) {
            css_free(values.items);
            return;
        }
        if (size > 4 && css_font_options_overlong(pool, values.items, size)) {
            css_free(values.items);
            return;
        }
    }
    for (Py_ssize_t index = family + 1; index < values.len; index++) {
        if (values.items[index].kind != CK_STR) {
            continue;
        }
        if (!css_font_family_quote_isolated(pool, values.items, index, family + 1, values.len)) {
            css_free(values.items);
            return;
        }
        values.items[index] = css_font_family_comp(pool, values.items[index]);
    }
    /* family was set to at most values.len-1 then decremented at least once above, so family+1 < values.len holds */
    if (pool->data[values.items[family + 1].off] == '-') {
        css_quote_leading_hyphen_family(pool, &values.items[family + 1]);
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
            if (css_comp_kw(pool, &values.items[index], "normal")) {
                if (index > 0 && css_comp_is_slash(pool, &values.items[index - 1])) {
                    css_free(values.items);
                    return;
                }
                comp_vec_splice(&values, index, 1, NULL, 0);
            } else if (values.items[index].kind == CK_NUM && comp_ieq(pool, &values.items[index], "400")) {
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

static inline int css_has_mixed_wide(const css_buf *pool, const comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        const css_comp *comp = &comps->items[index];
        if (comp->kind != CK_IDENT) {
            continue;
        }
        css_char first = css_lower(pool->data[comp->off]);
        if ((first == 'i' || first == 'u' || first == 'r') && css_box_is_wide_keyword(pool, comp)) {
            return 1;
        }
    }
    return 0;
}

/* Dispatch the per-property shorthand handler over the rendered components. */
static void css_apply_handler(css_buf *pool, const css_char *prop, Py_ssize_t prop_len, comp_vec *comps) {
    int has_sep = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind == CK_SEP) {
            has_sep = 1;
        }
    }
    css_char first = css_lower(prop[0]);
    if (!has_sep && css_prop_in(prop, prop_len, CSS_BOX_PROPS, sizeof(CSS_BOX_PROPS) / sizeof(char *))) {
        if (comps->len <= 2 ||
            !css_prop_in(prop, prop_len, CSS_TWO_VALUE_BOX_PROPS, sizeof(CSS_TWO_VALUE_BOX_PROPS) / sizeof(char *))) {
            css_handle_box(pool, comps);
        }
    } else if (css_run_ieq_first(prop, prop_len, first, "background")) {
        css_handle_background(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "background-position")) {
        css_handle_background_position(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "background-repeat")) {
        css_handle_runs(pool, comps, css_collapse_repeat_run);
    } else if (css_run_ieq_first(prop, prop_len, first, "background-size")) {
        css_handle_runs(pool, comps, css_collapse_size_run);
    } else if (css_run_ieq_first(prop, prop_len, first, "border") ||
               css_run_ieq_first(prop, prop_len, first, "border-top") ||
               css_run_ieq_first(prop, prop_len, first, "border-bottom") ||
               css_run_ieq_first(prop, prop_len, first, "border-left") ||
               css_run_ieq_first(prop, prop_len, first, "border-right") ||
               css_run_ieq_first(prop, prop_len, first, "outline") ||
               css_run_ieq_first(prop, prop_len, first, "column-rule") ||
               css_run_ieq_first(prop, prop_len, first, "text-decoration") ||
               css_run_ieq_first(prop, prop_len, first, "text-emphasis")) {
        if (first != 't' && comps->len > 1 && css_has_mixed_wide(pool, comps)) {
            return;
        }
        css_handle_color_drop(pool, comps, prop, prop_len);
    } else if (css_run_ieq_first(prop, prop_len, first, "border-color")) {
        css_handle_border_color(pool, comps);
    } else if (css_prop_in(prop, prop_len, CSS_CURRENTCOLOR_INIT, sizeof(CSS_CURRENTCOLOR_INIT) / sizeof(char *)) ||
               css_prop_in(prop, prop_len, CSS_SINGLE_COLOR, sizeof(CSS_SINGLE_COLOR) / sizeof(char *))) {
        css_handle_color_single(pool, comps, prop, prop_len);
    } else if (css_run_ieq_first(prop, prop_len, first, "font-weight")) {
        css_handle_font_weight(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "flex-basis")) {
        css_handle_flex_basis(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "flex-grow") ||
               css_run_ieq_first(prop, prop_len, first, "order")) {
        css_handle_flex_grow(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "flex-shrink")) {
        css_handle_flex_shrink(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "box-shadow")) {
        css_handle_runs(pool, comps, css_collapse_box_shadow_run);
    } else if (css_run_ieq_first(prop, prop_len, first, "text-shadow")) {
        css_handle_text_shadow(pool, comps);
    } else if (!has_sep && css_run_ieq_first(prop, prop_len, first, "flex")) {
        css_handle_flex(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "font")) {
        css_handle_font(pool, comps);
    } else if (css_run_ieq_first(prop, prop_len, first, "font-family")) {
        css_handle_font_family(pool, comps);
    }
}

static int css_integer_role_needs_raw(token_vec *vec, Py_ssize_t start, Py_ssize_t end, int *whole_calc) {
    while (vec->items[start].kind == CSS_WS || vec->items[start].kind == CSS_COMMENT) {
        start++;
    }
    while (vec->items[end - 1].kind == CSS_WS || vec->items[end - 1].kind == CSS_COMMENT) {
        end--;
    }
    const css_token *first = &vec->items[start];
    if (end == start + 1 && first->kind == CSS_NUM && first->unit_len == 0) {
        for (Py_ssize_t index = 0; index < first->text_len; index++) {
            if (first->text[index] == '.' || first->text[index] == 'e' || first->text[index] == 'E') {
                return 1;
            }
        }
        return 0;
    }
    *whole_calc = first->kind == CSS_IDENT && css_run_ieq(first->text, first->text_len, "calc") && start + 1 < end &&
                  vec->items[start + 1].kind == CSS_DELIM && vec->items[start + 1].delim == '(' &&
                  css_match_paren(vec, start + 1, end) >= end - 1;
    if (*whole_calc) {
        for (Py_ssize_t index = start; index < end; index++) {
            if (vec->items[index].kind == CSS_NUM && vec->items[index].unit_len > 0) {
                return 1;
            }
        }
    }
    return 0;
}

static int css_integer_role_fold_is_integer(const css_buf *pool, const css_comp *comp) {
    if (comp->kind != CK_NUM) {
        return 0;
    }
    const css_char *text = pool->data + comp->off;
    for (Py_ssize_t index = 0; index < comp->len; index++) {
        if (!css_is_digit(text[index]) && (index != 0 || text[index] != '-')) {
            return 0;
        }
    }
    return 1;
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
    if (css_run_ieq(name, name_len, "unicode-range")) {
        if (css_handle_unicode_range(vec, start, end, out)) {
            return;
        }
        /* Rebuilding invalid ranges can join separated U+ and a tokens into a valid range. */
        const css_token *last = &vec->items[end - 1];
        cbuf_put_run(out, vec->items[start].text,
                     last->text + last->text_len + last->unit_len - vec->items[start].text);
        return;
    }
    int is_integer_role = name_len == 7 && css_run_ieq(name, name_len, "z-index");
    if (name_len == 5) {
        is_integer_role = css_run_ieq(name, name_len, "order");
    }
    int whole_calc = 0;
    if (is_integer_role && css_integer_role_needs_raw(vec, start, end, &whole_calc)) {
        css_render_raw_value(vec, start, end, out);
        return;
    }
    scratch->len = 0;
    int color_mode = css_prop_color_mode(name, name_len);
    css_calc_context context = {name, name_len, whole_calc};
    /* Flexbox 1 §7.1: a unitless zero not preceded by two flex factors is a flex factor, so flex keeps zero units */
    css_render_components(pool, vec, start, end, color_mode,
                          !is_integer_role && (name_len != 4 || !css_run_ieq(name, name_len, "flex")), &context,
                          scratch);
    /* the handlers and the assembler read the components' pool text, which a failed growth left unwritten */
    if (*pool->oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (whole_calc) {
        if (!css_integer_role_fold_is_integer(pool, &scratch->items[0])) {
            css_render_raw_value(vec, start, end, out);
            return;
        }
    }
    css_apply_handler(pool, name, name_len, scratch);
    css_assemble(pool, scratch, out);
}

#endif /* TURBOHTML_CSS_SHORTHAND_H */
