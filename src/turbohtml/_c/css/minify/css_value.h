#ifndef TURBOHTML_CSS_VALUE_H
#define TURBOHTML_CSS_VALUE_H

/* A rendered value component refers to its already-minified text by (offset, length) into a per-call code-point pool,
   so the pool can grow without invalidating earlier components. isfunc is 0, 1 (function/url, no following space) or
   2 (a comma/slash separator); kind drives the per-property handlers and the spacing rules. */
typedef enum {
    CK_SEP,
    CK_FUNC,
    CK_IDENT,
    CK_DIM,
    CK_NUM,
    CK_HASH,
    CK_URL,
    CK_STR,
    CK_DELIM,
    CK_PCT,
} css_compkind;

typedef struct {
    Py_ssize_t off;
    Py_ssize_t len;
    int isfunc;
    css_compkind kind;
} css_comp;

typedef struct {
    css_comp *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} comp_vec;

static void comp_vec_push(comp_vec *vec, css_comp comp) {
    if (vec->len == vec->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(vec->len + 1), (size_t)vec->cap, 16, sizeof(css_comp), &cap, &bytes);
        if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            vec->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;          /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        css_comp *grown = css_realloc(vec->items, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        vec->items = grown;
        vec->cap = (Py_ssize_t)cap;
    }
    vec->items[vec->len++] = comp;
}

/* Append a run of code points to the pool and return its starting offset. */
static Py_ssize_t pool_run(css_buf *pool, const css_char *text, Py_ssize_t len) {
    Py_ssize_t off = pool->len;
    cbuf_put_run(pool, text, len);
    return off;
}

/* Append an ASCII C string to the pool and return its starting offset. */
static Py_ssize_t pool_cstr(css_buf *pool, const char *text) {
    Py_ssize_t off = pool->len;
    cbuf_puts(pool, text);
    return off;
}

/* Case-insensitively compare a code-point run against an ASCII literal. */
static int css_run_ieq(const css_char *text, Py_ssize_t len, const char *ascii) {
    Py_ssize_t index = 0;
    for (; index < len && ascii[index]; index++) {
        if (css_lower(text[index]) != (css_char)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return index == len && ascii[index] == '\0';
}

static int comp_ieq(const css_buf *pool, const css_comp *comp, const char *ascii) {
    return css_run_ieq(pool->data + comp->off, comp->len, ascii);
}

/* Decimal integer width of a (possibly negative) exponent. */
static int css_int_width(int value) {
    int width = value < 0 ? 1 : 0;
    unsigned magnitude = value < 0 ? (unsigned)(-value) : (unsigned)value;
    do {
        width++;
        magnitude /= 10;
    } while (magnitude);
    return width;
}

/* Append the decimal digits of value to the pool (sign handled by the caller for the mantissa exponent). */
static void pool_put_int(css_buf *pool, int value) {
    char buffer[16];
    int written = snprintf(buffer, sizeof(buffer), "%d", value);
    for (int index = 0; index < written; index++) {
        cbuf_putc(pool, (css_char)(unsigned char)buffer[index]);
    }
}

/* Shorten a numeric run into the pool (CSS Syntax 3 §4.3.3, Values 4 §6.1): drop a leading +, drop redundant zeros,
   and use e-notation when shorter (only when scientific, i.e. for dimensions/percentages). Returns (offset, length). */
static void css_format_number(css_buf *pool, const css_char *num, Py_ssize_t numlen, int scientific,
                              Py_ssize_t *out_off, Py_ssize_t *out_len) {
    /* every caller passes a number-token or built-rational text with at least one digit, so numlen >= 1 */
    Py_ssize_t start = pool->len;
    int negative = num[0] == '-';
    Py_ssize_t cursor = (num[0] == '+' || num[0] == '-') ? 1 : 0;
    Py_ssize_t mant_end = numlen;
    int exponent = 0;
    /* the tokenizer only folds an e/E into a number token when valid exponent digits follow (css_scan_number), so the
       exponent here is always well-formed -- no malformed-exponent fallback is needed */
    for (Py_ssize_t index = cursor; index < numlen; index++) {
        if (num[index] == 'e' || num[index] == 'E') {
            mant_end = index;
            Py_ssize_t exp_pos = index + 1;
            int exp_neg = 0;
            if (num[exp_pos] == '+' || num[exp_pos] == '-') {
                exp_neg = num[exp_pos] == '-';
                exp_pos++;
            }
            for (; exp_pos < numlen; exp_pos++) {
                exponent = exponent * 10 + (int)(num[exp_pos] - '0');
            }
            if (exp_neg) {
                exponent = -exponent;
            }
            break;
        }
    }
    /* collect the mantissa's digits (without the dot), tracking the integer-part length and leading-zero count */
    css_char digits[128];
    Py_ssize_t digit_count = 0;
    Py_ssize_t int_len = 0;
    int seen_dot = 0;
    for (Py_ssize_t index = cursor; index < mant_end; index++) {
        if (num[index] == '.') {
            seen_dot = 1;
            continue;
        }
        if (!seen_dot) {
            int_len++;
        }
        if (digit_count < (Py_ssize_t)(sizeof(digits) / sizeof(digits[0]))) {
            digits[digit_count++] = num[index];
        }
    }
    Py_ssize_t lead = 0;
    while (lead < digit_count && digits[lead] == '0') {
        lead++;
    }
    Py_ssize_t point = int_len + exponent - lead;
    Py_ssize_t first = lead;
    Py_ssize_t last = digit_count;
    while (last > first && digits[last - 1] == '0') {
        last--;
    }
    if (last == first) {
        cbuf_putc(pool, '0');
        *out_off = start;
        *out_len = pool->len - start;
        return;
    }
    Py_ssize_t significant = last - first;
    Py_ssize_t scale = point - significant;
    Py_ssize_t plain_len = scale >= 0 ? significant + scale : (-scale >= significant ? 1 - scale : significant + 1);
    int use_sci = 0;
    if (scientific && scale != 0) {
        Py_ssize_t sci_len = significant + 1 + css_int_width((int)scale);
        if (sci_len < plain_len) {
            use_sci = 1;
        }
    }
    if (negative) {
        cbuf_putc(pool, '-');
    }
    if (use_sci) {
        cbuf_put_run(pool, digits + first, significant);
        cbuf_putc(pool, 'e');
        pool_put_int(pool, (int)scale);
    } else if (scale >= 0) {
        cbuf_put_run(pool, digits + first, significant);
        for (Py_ssize_t index = 0; index < scale; index++) {
            cbuf_putc(pool, '0');
        }
    } else if (-scale >= significant) {
        cbuf_putc(pool, '.');
        for (Py_ssize_t index = 0; index < -scale - significant; index++) {
            cbuf_putc(pool, '0');
        }
        cbuf_put_run(pool, digits + first, significant);
    } else {
        cbuf_put_run(pool, digits + first, significant + scale);
        cbuf_putc(pool, '.');
        cbuf_put_run(pool, digits + first + significant + scale, -scale);
    }
    *out_off = start;
    *out_len = pool->len - start;
}

/* Known dimension units, lower-cased for the zero-collapse check. */
static int css_unit_known(const css_char *unit, Py_ssize_t len) {
    static const char *const units[] = {"px", "em", "rem", "ex",  "ch",   "vw",   "vh",   "vmin", "vmax", "cm",
                                        "mm", "in", "pt",  "pc",  "q",    "deg",  "grad", "rad",  "turn", "s",
                                        "ms", "hz", "khz", "dpi", "dpcm", "dppx", "fr",   "%"};
    for (size_t index = 0; index < sizeof(units) / sizeof(units[0]); index++) {
        if (css_run_ieq(unit, len, units[index])) {
            return 1;
        }
    }
    return 0;
}

/* CSS Values 4 §5.2: a zero length may drop its unit (bare 0 is a valid <length>). No other dimension type may --
   bare 0 is not a valid <angle>/<time>/<frequency>/<resolution>/<flex>, so angle/time/etc. units stay. */
static int css_unit_zero_droppable(const css_char *unit, Py_ssize_t len) {
    static const char *const zero_units[] = {"ch", "cm", "em",  "ex", "in",   "mm",   "pc", "pt",
                                             "px", "q",  "rem", "vh", "vmax", "vmin", "vw"};
    for (size_t index = 0; index < sizeof(zero_units) / sizeof(zero_units[0]); index++) {
        if (css_run_ieq(unit, len, zero_units[index])) {
            return 1;
        }
    }
    return 0;
}

/* Render a dimension (number + unit) into the pool: shorten the number, lower-case a known unit, and drop the unit
   when the value is 0 and the unit is a length (CSS Values 4 §5.2). */
static void css_format_dimension(css_buf *pool, const css_token *token, int drop_zero_unit, Py_ssize_t *out_off,
                                 Py_ssize_t *out_len) {
    Py_ssize_t off;
    Py_ssize_t len;
    css_format_number(pool, token->text, token->text_len, token->unit_len != 0, &off, &len);
    int is_zero = len == 1 && pool->data[off] == '0';
    if (drop_zero_unit && is_zero && css_unit_zero_droppable((token->text + token->text_len), token->unit_len)) {
        *out_off = off;
        *out_len = len;
        return;
    }
    if (css_unit_known((token->text + token->text_len), token->unit_len)) {
        for (Py_ssize_t index = 0; index < token->unit_len; index++) {
            cbuf_putc(pool, css_lower((token->text + token->text_len)[index]));
        }
    } else {
        cbuf_put_run(pool, (token->text + token->text_len), token->unit_len);
    }
    *out_off = off;
    *out_len = pool->len - off;
}

/* keyword -> shortest hash via the first-byte bucketed table; returns 1 and sets (off,len) on a hit. */
static int css_name_to_hex(css_buf *pool, const css_char *name, Py_ssize_t len, Py_ssize_t *out_off,
                           Py_ssize_t *out_len) {
    css_char first = css_lower(name[0]);
    if (first < 'a' || first > 'z') {
        return 0;
    }
    for (int index = th_css_name_first[first]; index < th_css_name_first[first + 1]; index++) {
        const th_css_color_entry *entry = &th_css_name_to_hex[index];
        if (entry->key_len == len && css_run_ieq(name, len, entry->key)) {
            *out_off = pool_cstr(pool, entry->val);
            *out_len = entry->val_len;
            return 1;
        }
    }
    return 0;
}

/* hash string (with '#', lower-case) -> shortest keyword via binary search; returns the entry or NULL. */
static const th_css_color_entry *css_hex_to_name(const css_char *hash, Py_ssize_t len) {
    int low = 0;
    int high = th_css_hex_count - 1;
    while (low <= high) {
        int mid = (low + high) / 2;
        const th_css_color_entry *entry = &th_css_hex_to_name[mid];
        Py_ssize_t index = 0;
        int order = 0;
        for (; index < len && index < entry->key_len; index++) {
            css_char left = hash[index];
            css_char right = (css_char)(unsigned char)entry->key[index];
            if (left != right) {
                order = left < right ? -1 : 1;
                break;
            }
        }
        if (order == 0) {
            order = (int)(len - entry->key_len);
        }
        if (order == 0) {
            return entry;
        }
        if (order < 0) {
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    return NULL;
}

/* Try to shorten an ident (color keyword) or hash; returns 1 and sets (off,len) when it changes, else 0. */
static int css_color_keyword_or_hash(css_buf *pool, const css_token *token, int is_color_context, Py_ssize_t *out_off,
                                     Py_ssize_t *out_len) {
    if (token->kind == CSS_IDENT) {
        if (!is_color_context) {
            return 0;
        }
        return css_name_to_hex(pool, token->text, token->text_len, out_off, out_len);
    }
    /* a hash: lower-case it, fold #rrggbbaa with aa==ff/00, then map to a keyword or a 3/4-digit short form */
    css_char data[10];
    Py_ssize_t len = token->text_len;
    if (len > 9) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        data[index] = css_lower(token->text[index]);
    }
    if (len == 9 && data[7] == data[8]) {
        if (data[7] == 'f') {
            len = 7;
        } else if (data[7] == '0') {
            data[0] = '#';
            data[1] = data[2] = data[3] = data[4] = '0';
            len = 5;
        }
    }
    const th_css_color_entry *named = css_hex_to_name(data, len);
    if (named != NULL) {
        *out_off = pool_cstr(pool, named->val);
        *out_len = named->val_len;
        return 1;
    }
    if (len == 7 && data[1] == data[2] && data[3] == data[4] && data[5] == data[6]) {
        css_char packed[4] = {'#', data[1], data[3], data[5]};
        *out_off = pool_run(pool, packed, 4);
        *out_len = 4;
        return 1;
    }
    if (len == 9 && data[1] == data[2] && data[3] == data[4] && data[5] == data[6] && data[7] == data[8]) {
        css_char packed[5] = {'#', data[1], data[3], data[5], data[7]};
        *out_off = pool_run(pool, packed, 5);
        *out_len = 5;
        return 1;
    }
    /* unchanged except for lower-casing */
    *out_off = pool_run(pool, data, len);
    *out_len = len;
    return 1;
}

/* Drop CSS line continuations (a backslash followed by a newline) from a run, appending the rest to the pool. */
static void css_strip_continuations(css_buf *pool, const css_char *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        if (text[index] == '\\' && index + 1 < len && (text[index + 1] == '\n' || text[index + 1] == '\r')) {
            if (text[index + 1] == '\r' && index + 2 < len && text[index + 2] == '\n') {
                index += 3;
            } else {
                index += 2;
            }
            continue;
        }
        cbuf_putc(pool, text[index]);
        index++;
    }
}

/* Whether a url body can drop its quotes and stay a url-token (CSS Syntax 3 §4): non-empty and free of whitespace,
   quotes, parens, backslash, and any non-printable code point (U+0000-08, U+000B, U+000E-1F, U+007F) -- a quoted
   string may carry those raw, but in url-token position they force a <bad-url-token>. */
static int css_url_unquotable(const css_char *text, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        css_char character = text[index];
        if (css_is_ws(character) || character < 0x20 || character == 0x7f || character == '"' || character == '\'' ||
            character == '(' || character == ')' || character == '\\') {
            return 0;
        }
    }
    return 1;
}

static void css_minify_string(css_buf *pool, const css_char *text, Py_ssize_t len, Py_ssize_t *out_off,
                              Py_ssize_t *out_len) {
    Py_ssize_t off = pool->len;
    if (len < 2) {
        cbuf_put_run(pool, text, len);
        *out_off = off;
        *out_len = len;
        return;
    }
    css_char quote = text[0];
    int closed = text[len - 1] == quote;
    Py_ssize_t body_len = closed ? len - 2 : len - 1;
    cbuf_putc(pool, quote);
    css_strip_continuations(pool, text + 1, body_len);
    cbuf_putc(pool, quote);
    *out_off = off;
    *out_len = pool->len - off;
}

static void css_minify_url(css_buf *pool, const css_char *text, Py_ssize_t len, Py_ssize_t *out_off,
                           Py_ssize_t *out_len) {
    Py_ssize_t off = pool->len;
    cbuf_put_run(pool, text, 4 < len ? 4 : len); /* the "url(" prefix */
    Py_ssize_t inner_start = 4;
    Py_ssize_t inner_end = len;
    int closed = len > 4 && text[len - 1] == ')';
    if (closed) {
        inner_end--;
    }
    while (inner_start < inner_end && css_is_ws(text[inner_start])) {
        inner_start++;
    }
    while (inner_end > inner_start && css_is_ws(text[inner_end - 1])) {
        inner_end--;
    }
    Py_ssize_t inner_len = inner_end - inner_start;
    const css_char *inner = text + inner_start;
    if (inner_len >= 2 && (inner[0] == '"' || inner[0] == '\'') && inner[inner_len - 1] == inner[0]) {
        /* a quoted body: strip continuations, then drop the quotes when the body is url-unquotable */
        css_buf scratch = {NULL, 0, 0, 0};
        css_strip_continuations(&scratch, inner + 1, inner_len - 2);
        if (css_url_unquotable(scratch.data, scratch.len)) {
            cbuf_put_run(pool, scratch.data, scratch.len);
        } else {
            cbuf_putc(pool, inner[0]);
            cbuf_put_run(pool, scratch.data, scratch.len);
            cbuf_putc(pool, inner[0]);
        }
        cbuf_free(&scratch);
    } else {
        cbuf_put_run(pool, inner, inner_len);
    }
    if (closed) {
        cbuf_putc(pool, ')');
    }
    *out_off = off;
    *out_len = pool->len - off;
}

static double css_run_to_double(const css_char *text, Py_ssize_t len) {
    char buffer[64];
    Py_ssize_t copy = len < 63 ? len : 63;
    for (Py_ssize_t index = 0; index < copy; index++) {
        buffer[index] = (char)text[index]; /* numeric token text is ASCII [+-.0-9eE] only */
    }
    buffer[copy] = '\0';
    return strtod(buffer, NULL);
}

static double css_hue_to_rgb(double lower, double upper, double hue) {
    if (hue < 0) {
        hue += 1.0;
    }
    if (hue > 1) {
        hue -= 1.0;
    }
    if (hue * 6.0 < 1.0) {
        return lower + (upper - lower) * hue * 6.0;
    }
    if (hue * 2.0 < 1.0) {
        return upper;
    }
    if (hue * 3.0 < 2.0) {
        return lower + (upper - lower) * (2.0 / 3.0 - hue) * 6.0;
    }
    return lower;
}

static void css_hsl_to_rgb(double hue, double saturation, double lightness, double *red, double *green, double *blue) {
    double upper = lightness <= 0.5 ? lightness * (saturation + 1.0) : lightness + saturation - lightness * saturation;
    double lower = lightness * 2.0 - upper;
    *red = css_hue_to_rgb(lower, upper, hue + 1.0 / 3.0);
    *green = css_hue_to_rgb(lower, upper, hue);
    *blue = css_hue_to_rgb(lower, upper, hue - 1.0 / 3.0);
}

/* An rgb channel (a fraction in [0,1]) folds to a hex byte only when it lands exactly on an 8-bit value. CSS Color 4
   §4.1/§15 lets modern (space-separated) syntax keep more than 8 bits of precision, so rounding a fractional channel
   to 8-bit hex changes the value; we fold only the exact case and keep the functional form otherwise. Out-of-range
   values are clamped to [0,255] first (§15), which is itself value-safe. Returns 1 and the byte on an exact fold. */
static int css_channel_to_bits(double channel, int *out) {
    double bits = channel * 255.0;
    bits = bits < 0.0 ? 0.0 : (bits > 255.0 ? 255.0 : bits);
    double rounded = floor(bits + 0.5);
    if (fabs(bits - rounded) > 1e-6) {
        return 0;
    }
    *out = (int)rounded;
    return 1;
}

/* Render an opaque rgb triple (exact 8-bit channels) as the shortest hex or color keyword. */
static void css_rgb_to_hex(css_buf *pool, int red, int green, int blue, Py_ssize_t *out_off, Py_ssize_t *out_len) {
    char hex[8];
    snprintf(hex, sizeof(hex), "#%02x%02x%02x", red, green, blue);
    css_char wide[7];
    for (int index = 0; index < 7; index++) {
        wide[index] = (css_char)(unsigned char)hex[index];
    }
    const th_css_color_entry *named = css_hex_to_name(wide, 7);
    if (named != NULL) {
        *out_off = pool_cstr(pool, named->val);
        *out_len = named->val_len;
        return;
    }
    if (hex[1] == hex[2] && hex[3] == hex[4] && hex[5] == hex[6]) {
        css_char packed[4] = {'#', wide[1], wide[3], wide[5]};
        *out_off = pool_run(pool, packed, 4);
        *out_len = 4;
        return;
    }
    *out_off = pool_run(pool, wide, 7);
    *out_len = 7;
}

/* Append an alpha value in its shorter form, swapping between number and percentage (CSS Color 4 §15: a percentage and
   its equivalent number are interchangeable). */
static void css_append_num_pct(css_buf *out, const css_char *text, Py_ssize_t len) {
    if (len == 3 && text[len - 1] == '%' && text[1] == '0') {
        cbuf_putc(out, '.');
        cbuf_putc(out, text[0]);
        return;
    }
    if (len > 2 && text[0] == '.' && text[1] == '0') {
        if (text[2] == '0') {
            cbuf_putc(out, '.');
            cbuf_put_run(out, text + 3, len - 3);
            cbuf_putc(out, '%');
            return;
        }
        if (len == 3) {
            cbuf_putc(out, text[2]);
            cbuf_putc(out, '%');
            return;
        }
    }
    cbuf_put_run(out, text, len);
}

/* Append a color-function component: the minified number, with a percent sign when it is a percentage. */
static void css_append_color_number(css_buf *out, const css_token *raw, int is_pct) {
    css_buf scratch = {NULL, 0, 0, 0};
    Py_ssize_t off;
    Py_ssize_t len;
    css_format_number(&scratch, raw->text, raw->text_len, is_pct, &off, &len);
    cbuf_put_run(out, scratch.data + off, len);
    if (is_pct) {
        cbuf_putc(out, '%');
    }
    cbuf_free(&scratch);
}

/* Try to fold an rgb()/rgba()/hsl()/hsla() call to a hex, keyword, transparent or shortest functional form. Returns 1
   and sets (off, len) on success, 0 when the name is not a color function or the argument shape is not a color. */
static int css_try_color_func(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *name,
                              Py_ssize_t name_len, Py_ssize_t *out_off, Py_ssize_t *out_len) {
    int is_rgb = css_run_ieq(name, name_len, "rgb") || css_run_ieq(name, name_len, "rgba");
    int is_hsl = css_run_ieq(name, name_len, "hsl") || css_run_ieq(name, name_len, "hsla");
    if (!is_rgb && !is_hsl) {
        return 0;
    }
    double values[4];
    int types[4]; /* 1 = percentage, 0 = number */
    const css_token *raws[4];
    int count = 0;
    int has_slash = 0;
    int has_comma = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_COMMENT || token->kind == CSS_WS) {
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '/') {
            has_slash = 1;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == ',') {
            has_comma = 1;
            continue;
        }
        if (token->kind != CSS_NUM || count >= 4) {
            /* a var()/env()/none/calc() argument (or a 5th component) means this is not a plain numeric color we can
               fold; keep the function verbatim rather than drop or reorder its arguments */
            return 0;
        }
        double magnitude = css_run_to_double(token->text, token->text_len);
        int is_pct = token->unit_len == 1 && (token->text + token->text_len)[0] == '%';
        if (is_pct) {
            magnitude /= 100.0;
        }
        values[count] = magnitude;
        types[count] = is_pct;
        raws[count] = token;
        count++;
    }
    if (count < 3) {
        return 0;
    }
    double alpha = 1.0;
    int has_alpha = count == 4;
    if (has_alpha) {
        /* alpha clamps to [0,1], so any alpha <= 0 over a zero rgb triple is the keyword transparent (rgba(0,0,0,0)) */
        int all_zero = values[0] == 0.0 && values[1] == 0.0 && values[2] == 0.0 && values[3] <= 0.0;
        if (all_zero) { /* rgba(0,0,0,0) is the keyword transparent, whose shortest form is the 4-digit hex #0000 */
            *out_off = pool_cstr(pool, "#0000");
            *out_len = 5;
            return 1;
        }
        if (values[3] >= 1.0) { /* alpha clamps to [0,1] (Color 4 §17), so any alpha >= 1 is opaque and droppable */
            has_alpha = 0;
            count = 3;
        } else {
            alpha = values[3];
        }
    }
    /* alpha stays exactly 1.0 only on the paths that also clear has_alpha (count==3, or a fourth value of 1) */
    if (alpha == 1.0) {
        int bits[3];
        int foldable = 1;
        if (is_rgb) {
            for (int index = 0; index < 3; index++) {
                if (!css_channel_to_bits(types[index] ? values[index] : values[index] / 255.0, &bits[index])) {
                    foldable = 0;
                }
            }
        } else if (!types[0] && types[1] && types[2]) {
            double hue = values[0] / 360.0;
            hue -= floor(hue); /* hue is modulo 360; saturation and lightness clamp to [0,1] (Color 4 §7) */
            double sat = values[1] < 0.0 ? 0.0 : (values[1] > 1.0 ? 1.0 : values[1]);
            double light = values[2] < 0.0 ? 0.0 : (values[2] > 1.0 ? 1.0 : values[2]);
            double channels[3];
            css_hsl_to_rgb(hue, sat, light, &channels[0], &channels[1], &channels[2]);
            for (int index = 0; index < 3; index++) {
                if (!css_channel_to_bits(channels[index], &bits[index])) {
                    foldable = 0;
                }
            }
        } else {
            foldable = 0; /* an hsl() whose hue is a percentage or saturation/lightness a number is not a color */
        }
        if (foldable) {
            css_rgb_to_hex(pool, bits[0], bits[1], bits[2], out_off, out_len);
            return 1;
        }
    }
    /* opaque-but-inexact or non-opaque: rebuild the function in shortest form, keeping rgb percentages as integers
       when they fall on exact 20% steps (51/102/.../255) */
    css_buf result = {NULL, 0, 0, 0};
    /* rgb()/hsl() are the modern aliases of rgba()/hsla() and take an optional alpha, so always use the shorter
       three-letter name (CSS Color 4 §4): rgba(...) -> rgb(...), hsla(...) -> hsl(...) */
    cbuf_puts(&result, is_rgb ? "rgb" : "hsl");
    cbuf_putc(&result, '(');
    const char *separator = has_slash ? " " : (has_comma ? "," : " ");
    int exact = is_rgb;
    if (is_rgb) {
        for (int index = 0; index < 3; index++) {
            double scaled = values[index] * 5.0;
            if (!types[index] || fabs(scaled - floor(scaled + 0.5)) >= 1e-3) {
                exact = 0;
            }
        }
    }
    for (int index = 0; index < 3; index++) {
        if (index > 0) {
            cbuf_puts(&result, separator);
        }
        if (is_rgb && exact) {
            char buffer[8];
            int written = snprintf(buffer, sizeof(buffer), "%d", (int)(values[index] * 255.0 + 0.5));
            for (int pos = 0; pos < written; pos++) {
                cbuf_putc(&result, (css_char)(unsigned char)buffer[pos]);
            }
        } else {
            css_append_color_number(&result, raws[index], types[index]);
        }
    }
    if (has_alpha) {
        css_buf alpha_text = {NULL, 0, 0, 0};
        css_append_color_number(&alpha_text, raws[3], types[3]);
        cbuf_puts(&result, has_slash ? "/" : ",");
        css_append_num_pct(&result, alpha_text.data, alpha_text.len);
        cbuf_free(&alpha_text);
    }
    cbuf_putc(&result, ')');
    *out_off = pool_run(pool, result.data, result.len);
    *out_len = result.len;
    cbuf_free(&result);
    return 1;
}

/* A token cursor for the grammar. baseline is the Baseline year the caller targets (0 = only long-interoperable
   syntax), so the renderer can gate a transform on the year its output syntax reached Baseline: a transform tagged
   with year Y is emitted only when baseline >= Y. */
typedef struct {
    token_vec *vec;
    Py_ssize_t index;
    int baseline;
} cursor;

static css_token *cursor_peek(cursor *cur) {
    return cur->index < cur->vec->len ? &cur->vec->items[cur->index] : NULL;
}

/* Advance the cursor over a run of component values up to a top-level stop delimiter, honoring (){}[] nesting, and
   return the end index; the run is [start, end). */
static Py_ssize_t css_read_until(cursor *cur, const char *stops) {
    int depth = 0;
    while (cur->index < cur->vec->len) {
        css_token *token = &cur->vec->items[cur->index];
        if (token->kind == CSS_DELIM) {
            css_char delim = token->delim;
            if (depth == 0 && strchr(stops, (int)delim) != NULL && delim != 0) {
                break;
            }
            if (delim == '(' || delim == '[' || delim == '{') {
                depth++;
            } else if ((delim == ')' || delim == ']' || delim == '}') && depth > 0) {
                /* a stray depth-0 ')' or ']' is consumed below; a depth-0 '}' always ends the run at the stop check
                   above, since every caller includes '}' in its stop set */
                depth--;
            }
        }
        cur->index++;
    }
    return cur->index;
}

/* Find the index of the ')' matching an opening '(' that sits at open_paren, scanning over delim tokens. */
static Py_ssize_t css_match_paren(token_vec *vec, Py_ssize_t open_paren, Py_ssize_t end) {
    int depth = 1;
    Py_ssize_t index = open_paren + 1;
    while (index < end) { /* depth is decremented to 0 only with an immediate break, so it is always >0 at the test */
        if (vec->items[index].kind == CSS_DELIM) {
            if (vec->items[index].delim == '(') {
                depth++;
            } else if (vec->items[index].delim == ')') {
                depth--;
                if (depth == 0) {
                    break;
                }
            }
        }
        index++;
    }
    return index;
}

static int css_is_math_func(const css_char *name, Py_ssize_t len) {
    return css_run_ieq(name, len, "calc") || css_run_ieq(name, len, "min") || css_run_ieq(name, len, "max") ||
           css_run_ieq(name, len, "clamp");
}

/* Trim trailing spaces already written to a scratch buffer (the func-arg comma rule). */
static void css_rtrim(css_buf *buffer) {
    while (buffer->len > 0 && buffer->data[buffer->len - 1] == ' ') {
        buffer->len--;
    }
}

static void css_minify_func_args(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *name,
                                 Py_ssize_t name_len, css_buf *out);
static int css_is_ident_string(const css_char *text, Py_ssize_t len);

static int css_arg_is_zero(const css_char *text, Py_ssize_t len) {
    return (len == 1 && text[0] == '0') || (len == 2 && text[0] == '0' && text[1] == '%');
}

/* Drop a redundant trailing argument from a transform function: translate(x,0) -> translate(x) and
   translate(0,0) -> translate(0) (the omitted Y defaults to 0), scale(n,n) -> scale(n). These are exact identities,
   so the transform is value-safe; translate3d and the single-axis variants are intentionally left untouched. */
static void css_collapse_transform_args(const css_char *name, Py_ssize_t name_len, css_buf *args) {
    int is_translate = css_run_ieq(name, name_len, "translate");
    int is_scale = css_run_ieq(name, name_len, "scale");
    if (!is_translate && !is_scale) {
        return;
    }
    Py_ssize_t comma = -1;
    int depth = 0;
    for (Py_ssize_t index = 0; index < args->len; index++) {
        css_char character = args->data[index];
        if (character == '(') {
            depth++;
        } else if (character == ')') {
            depth--;
        } else if (character == ',' && depth == 0) {
            if (comma != -1) { /* more than two arguments: not a form we collapse */
                return;
            }
            comma = index;
        }
    }
    if (comma < 0) {
        return;
    }
    Py_ssize_t first_len = comma;
    const css_char *second = args->data + comma + 1;
    Py_ssize_t second_len = args->len - comma - 1;
    if ((is_translate && css_arg_is_zero(second, second_len)) ||
        (is_scale && first_len == second_len &&
         memcmp(args->data, second, (size_t)first_len * sizeof(css_char)) == 0)) {
        args->len = first_len;
    }
}

/* Render a function `name(args)` into the pool and return its (offset, length). The function text is assembled in a
   local buffer first: minifying the arguments uses the pool as scratch, so building straight into the pool would
   interleave that scratch into the function's bytes. */
static void css_render_function(css_buf *pool, token_vec *vec, Py_ssize_t name_index, Py_ssize_t close_index,
                                Py_ssize_t *out_off, Py_ssize_t *out_len) {
    css_token *name_token = &vec->items[name_index];
    css_buf function = {NULL, 0, 0, 0};
    cbuf_put_run(&function, name_token->text, name_token->text_len);
    cbuf_putc(&function, '(');
    css_buf args = {NULL, 0, 0, 0};
    css_minify_func_args(pool, vec, name_index + 2, close_index, name_token->text, name_token->text_len, &args);
    css_collapse_transform_args(name_token->text, name_token->text_len, &args);
    cbuf_put_run(&function, args.data, args.len);
    cbuf_free(&args);
    cbuf_putc(&function, ')');
    *out_off = pool_run(pool, function.data, function.len);
    *out_len = function.len;
    cbuf_free(&function);
}

/* A calc() simplifier. It evaluates the expression exactly with rational arithmetic and bails -- keeping the input
   verbatim -- on anything it cannot prove safe: an opaque term (var/min/max/clamp/env/an unknown function), units
   that do not combine, integer overflow, or a non-terminating decimal result. So every rewrite is value-exact. */
#define CALC_MAX_TERMS 16
#define CALC_MAX_UNIT 12

#endif /* TURBOHTML_CSS_VALUE_H */
