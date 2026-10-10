#ifndef TURBOHTML_CSS_CALC_H
#define TURBOHTML_CSS_CALC_H

typedef struct {
    long long num;
    long long den;
} crat;

typedef struct {
    crat coeff;
    char unit[CALC_MAX_UNIT];
    int unit_len;
} cterm;

typedef struct {
    cterm terms[CALC_MAX_TERMS];
    int count;
    int ok;
} csum;

typedef struct {
    token_vec *vec;
    Py_ssize_t pos;
    Py_ssize_t end;
} calc_parser;

/* The sole caller (rat_set) passes a strictly positive denominator as right, so right is already positive and the
   resulting gcd is always >= 1. */
static long long css_gcd(long long left, long long right) {
    left = left < 0 ? -left : left;
    while (right != 0) {
        long long rem = left % right;
        left = right;
        right = rem;
    }
    return left;
}

/* The denominator is always strictly positive here: css_num_to_rat builds it as a power of ten, rat_mul/rat_add
   multiply already-positive denominators, and division bails on a zero divisor and sign-normalizes the inverse before
   multiplying (calc_parse_product), so neither a zero nor a negative denominator can reach this point. */
static int rat_set(crat *out, long long num, long long den) {
    long long divisor = css_gcd(num, den);
    out->num = num / divisor;
    out->den = den / divisor;
    return 1;
}

static int rat_mul(crat *out, crat left, crat right) {
    long long num;
    long long den;
    if (css_mul_overflow(left.num, right.num, &num) || css_mul_overflow(left.den, right.den, &den)) {
        return 0;
    }
    return rat_set(out, num, den);
}

static int rat_add(crat *out, crat left, crat right) {
    long long cross_left;
    long long cross_right;
    long long num;
    long long den;
    if (css_mul_overflow(left.num, right.den, &cross_left) || css_mul_overflow(right.num, left.den, &cross_right) ||
        css_add_overflow(cross_left, cross_right, &num) || css_mul_overflow(left.den, right.den, &den)) {
        return 0;
    }
    return rat_set(out, num, den);
}

/* The tokenizer guarantees valid number syntax; values outside the rational's 64-bit range stay unfolded. */
static int css_num_to_rat(const css_char *text, Py_ssize_t len, crat *out) {
    long long num = 0;
    long long frac_digits = 0;
    int negative = 0;
    Py_ssize_t index = 0;
    if (text[index] == '+' || text[index] == '-') { /* len >= 1: a NUM token's numeric part is never empty */
        negative = text[index] == '-';
        index++;
    }
    for (; index < len && text[index] >= '0' && text[index] <= '9'; index++) {
        if (css_mul_overflow(num, 10, &num) || css_add_overflow(num, text[index] - '0', &num)) {
            return 0;
        }
    }
    if (index < len && text[index] == '.') {
        index++;
        /* css_scan_number only admits digits in the fractional run, so no char below '0' reaches the loop body */
        for (; index < len && text[index] <= '9'; index++) {
            if (css_mul_overflow(num, 10, &num) || css_add_overflow(num, text[index] - '0', &num)) {
                return 0;
            }
            frac_digits++;
        }
    }
    if (num == 0) {
        out->num = 0;
        out->den = 1;
        return 1;
    }
    long long exponent = 0;
    if (index < len && !css_parse_exponent(text + index + 1, len - index - 1, &exponent)) {
        return 0;
    }
    if (negative) {
        num = -num;
    }
    long long power;
    /* A nonzero numerator or denominator cannot grow by more than 18 powers of ten. */
    if (css_add_overflow(exponent, -frac_digits, &power) || power > 18 || power < -18) {
        return 0;
    }
    long long den = 1;
    if (power >= 0) {
        for (int step = 0; step < power; step++) {
            if (css_mul_overflow(num, 10, &num)) {
                return 0;
            }
        }
    } else {
        for (int step = 0; step < -power; step++) {
            den *= 10;
        }
    }
    return rat_set(out, num, den);
}

/* Add a term to a sum, combining a like unit. Zero terms are kept (not dropped) so a result that cancels to zero
   keeps its unit -- a 0% must not become a bare 0, which is invalid for a percentage-only property like font-stretch;
   the formatter drops the redundant zeros at the end. Returns 0 on overflow or running out of term slots. */
static int csum_add_term(csum *sum, const cterm *term) {
    for (int index = 0; index < sum->count; index++) {
        if (sum->terms[index].unit_len == term->unit_len &&
            memcmp(sum->terms[index].unit, term->unit, (size_t)term->unit_len) == 0) {
            return rat_add(&sum->terms[index].coeff, sum->terms[index].coeff, term->coeff);
        }
    }
    if (sum->count >= CALC_MAX_TERMS) {
        return 0;
    }
    sum->terms[sum->count++] = *term;
    return 1;
}

/* A sum is a scalar when it is empty (zero) or a single unitless term. */
static int csum_as_scalar(const csum *sum, crat *out) {
    if (sum->count == 0) {
        out->num = 0;
        out->den = 1;
        return 1;
    }
    if (sum->count == 1 && sum->terms[0].unit_len == 0) {
        *out = sum->terms[0].coeff;
        return 1;
    }
    return 0;
}

static int csum_scale(csum *sum, crat factor) {
    if (factor.num == 0) {
        sum->count = 0;
        return 1;
    }
    for (int index = 0; index < sum->count; index++) {
        if (!rat_mul(&sum->terms[index].coeff, sum->terms[index].coeff, factor)) {
            return 0;
        }
    }
    return 1;
}

static void calc_skip_ws(calc_parser *parser) {
    while (parser->pos < parser->end &&
           (parser->vec->items[parser->pos].kind == CSS_WS || parser->vec->items[parser->pos].kind == CSS_COMMENT)) {
        parser->pos++;
    }
}

/* Each nesting level recurses through all three parsers, so they fill a caller-owned csum (over 500 bytes) instead of
   returning one, and an operator's right operand lives on the heap. */
static void calc_parse_sum(calc_parser *parser, csum *out);

static void calc_parse_value(calc_parser *parser, csum *out) {
    out->count = 0;
    out->ok = 0;
    calc_skip_ws(parser);
    if (parser->pos >= parser->end) {
        return;
    }
    css_token *token = &parser->vec->items[parser->pos];
    if (token->kind == CSS_NUM) {
        parser->pos++;
        if (token->unit_len >= CALC_MAX_UNIT) {
            return;
        }
        cterm term;
        if (!css_num_to_rat(token->text, token->text_len, &term.coeff)) {
            return;
        }
        term.unit_len = (int)token->unit_len;
        for (int index = 0; index < term.unit_len; index++) {
            term.unit[index] = (char)css_lower((token->text + token->text_len)[index]);
        }
        out->ok = 1;
        csum_add_term(out, &term); /* the first term always fits an empty sum (no like-unit, a free slot) */
        return;
    }
    int nested_calc = token->kind == CSS_IDENT && parser->pos + 1 < parser->end &&
                      parser->vec->items[parser->pos + 1].kind == CSS_DELIM &&
                      parser->vec->items[parser->pos + 1].delim == '(' &&
                      css_run_ieq(token->text, token->text_len, "calc");
    /* past the nesting cap a not-ok sum drops the fold and the function renderer emits the value; the sum stops at the
       closing paren, so nothing scans ahead for it */
    if (nested_calc || (token->kind == CSS_DELIM && token->delim == '(')) {
        if (!css_nesting_enter(parser->vec)) {
            return;
        }
        parser->pos += nested_calc ? 2 : 1;
        calc_parse_sum(parser, out);
        css_nesting_leave(parser->vec);
        calc_skip_ws(parser);
        if (parser->pos < parser->end && parser->vec->items[parser->pos].kind == CSS_DELIM &&
            parser->vec->items[parser->pos].delim == ')') {
            parser->pos++;
        } else {
            out->ok = 0;
        }
    }
    /* anything else is an opaque term (var/min/max/clamp/ident/...) the parser cannot evaluate, and out stays not-ok */
}

/* Fold rhs into acc for one '*' or '/' step; 0 when the step cannot fold exactly. */
static int calc_apply_product(csum *acc, csum *rhs, int is_div) {
    crat scalar;
    if (is_div) {
        if (!csum_as_scalar(rhs, &scalar) || scalar.num == 0) {
            return 0;
        }
        crat inverse = {scalar.den, scalar.num};
        if (inverse.den < 0) {
            inverse.num = -inverse.num;
            inverse.den = -inverse.den;
        }
        return csum_scale(acc, inverse);
    }
    if (csum_as_scalar(rhs, &scalar)) {
        return csum_scale(acc, scalar);
    }
    if (csum_as_scalar(acc, &scalar) && csum_scale(rhs, scalar)) {
        *acc = *rhs;
        return 1;
    }
    return 0;
}

static void calc_parse_product(calc_parser *parser, csum *out) {
    calc_parse_value(parser, out);
    csum *rhs = NULL;
    while (out->ok) {
        calc_skip_ws(parser);
        if (parser->pos >= parser->end) {
            break;
        }
        css_token *token = &parser->vec->items[parser->pos];
        if (token->kind != CSS_DELIM || (token->delim != '*' && token->delim != '/')) {
            break;
        }
        int is_div = token->delim == '/';
        parser->pos++;
        if (rhs == NULL && (rhs = css_malloc(sizeof(*rhs))) == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            *parser->vec->oom = 1;                                     /* GCOVR_EXCL_LINE */
            out->ok = 0;                                               /* GCOVR_EXCL_LINE */
            break;                                                     /* GCOVR_EXCL_LINE */
        }
        calc_parse_value(parser, rhs);
        out->ok = rhs->ok && calc_apply_product(out, rhs, is_div);
    }
    css_free(rhs);
}

static void calc_parse_sum(calc_parser *parser, csum *out) {
    calc_parse_product(parser, out);
    csum *rhs = NULL;
    while (out->ok) {
        calc_skip_ws(parser);
        if (parser->pos >= parser->end) {
            break;
        }
        css_token *token = &parser->vec->items[parser->pos];
        /* a lone '-' surrounded by spaces tokenizes as an identifier (it is a valid identifier start), not a delim,
           so the subtraction operator is recognized in both forms; '+' is always a delim */
        int is_plus = token->kind == CSS_DELIM && token->delim == '+';
        int is_sub = token->kind == CSS_IDENT && token->text_len == 1 && token->text[0] == '-';
        if (!is_plus && !is_sub) {
            break;
        }
        /* CSS Values 4 §10.1: a '+'/'-' operator must have whitespace on both sides. The operand to the left always
           consumed its trailing whitespace, so check the raw token stream around the operator; if either side lacks a
           space the input is invalid (a conformant parser drops the declaration), so bail and keep the calc() verbatim
           rather than fold malformed input into a valid value. */
        if (parser->vec->items[parser->pos - 1].kind != CSS_WS || parser->pos + 1 >= parser->end ||
            parser->vec->items[parser->pos + 1].kind != CSS_WS) {
            out->ok = 0;
            break;
        }
        parser->pos++;
        if (rhs == NULL && (rhs = css_malloc(sizeof(*rhs))) == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            *parser->vec->oom = 1;                                     /* GCOVR_EXCL_LINE */
            out->ok = 0;                                               /* GCOVR_EXCL_LINE */
            break;                                                     /* GCOVR_EXCL_LINE */
        }
        calc_parse_product(parser, rhs);
        out->ok = rhs->ok;
        for (int index = 0; out->ok && index < rhs->count; index++) {
            cterm term = rhs->terms[index];
            if (is_sub) {
                term.coeff.num = -term.coeff.num;
            }
            out->ok = csum_add_term(out, &term);
        }
    }
    css_free(rhs);
}

/* Format a rational exactly: an integer, or a terminating decimal, then minified. Returns 0 if non-terminating. */
static int css_format_rat(crat value, css_buf *out, int scientific) {
    css_char raw[40];
    Py_ssize_t raw_len = 0;
    long long num = value.num;
    long long den = value.den;
    int negative = num < 0;
    if (negative) {
        num = -num;
    }
    if (den == 1) {
        char buffer[24];
        int written = snprintf(buffer, sizeof(buffer), "%lld", num);
        if (negative) {
            raw[raw_len++] = '-';
        }
        for (int index = 0; index < written; index++) {
            raw[raw_len++] = (css_char)(unsigned char)buffer[index];
        }
    } else {
        long long reduced = den;
        int twos = 0;
        int fives = 0;
        while (reduced % 2 == 0) {
            reduced /= 2;
            twos++;
        }
        while (reduced % 5 == 0) {
            reduced /= 5;
            fives++;
        }
        if (reduced != 1) {
            return 0; /* non-terminating decimal: keep the expression verbatim */
        }
        int magnitude = twos > fives ? twos : fives;
        long long power = 1;
        for (int step = 0; step < magnitude; step++) {
            if (css_mul_overflow(power, 10, &power)) {
                return 0;
            }
        }
        long long scaled;
        if (css_mul_overflow(num, power / den, &scaled)) {
            return 0;
        }
        char buffer[24];
        int written = snprintf(buffer, sizeof(buffer), "%lld", scaled);
        if (negative) {
            raw[raw_len++] = '-';
        }
        int int_digits = written - magnitude;
        if (int_digits <= 0) {
            raw[raw_len++] = '0';
            raw[raw_len++] = '.';
            for (int pad = 0; pad < -int_digits; pad++) {
                raw[raw_len++] = '0';
            }
            for (int index = 0; index < written; index++) {
                raw[raw_len++] = (css_char)(unsigned char)buffer[index];
            }
        } else {
            for (int index = 0; index < int_digits; index++) {
                raw[raw_len++] = (css_char)(unsigned char)buffer[index];
            }
            raw[raw_len++] = '.';
            for (int index = int_digits; index < written; index++) {
                raw[raw_len++] = (css_char)(unsigned char)buffer[index];
            }
        }
    }
    Py_ssize_t off;
    Py_ssize_t len;
    css_format_number(out, raw, raw_len, scientific, &off, &len);
    return 1;
}

static int css_format_cterm(css_buf *out, const cterm *term) {
    /* a dimensioned coefficient shortens with scientific notation like any dimension (calc(1e3px) -> 1e3px, not
       1000px); a unitless result must not (matching the plain-number rule), so gate scientific on the unit */
    if (!css_format_rat(term->coeff, out, term->unit_len > 0)) {
        return 0;
    }
    for (int index = 0; index < term->unit_len; index++) {
        cbuf_putc(out, (css_char)(unsigned char)term->unit[index]);
    }
    return 1;
}

static int css_char_unit_droppable(const char *unit, int len) {
    css_char wide[CALC_MAX_UNIT];
    for (int index = 0; index < len; index++) {
        wide[index] = (css_char)(unsigned char)unit[index];
    }
    return css_unit_zero_droppable(wide, len);
}

/* Try to simplify calc(args); returns 1 and writes the shortest exact form to the pool, 0 to keep the input. */
CSS_NOINLINE static int css_try_calc(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end,
                                     Py_ssize_t *out_off, Py_ssize_t *out_len, css_compkind *kind,
                                     int bare_zero_allowed) {
    calc_parser parser = {vec, start, end};
    csum sum;
    calc_parse_sum(&parser, &sum);
    calc_skip_ws(&parser);
    if (!sum.ok || parser.pos != end) {
        return 0;
    }
    /* a unitless <number> summed with a dimensioned term (e.g. calc(100% - 30px - 0)) is a type error the whole
       declaration is dropped for; folding away its zero would turn invalid input into a valid value, so bail */
    int has_unitless = 0;
    int has_dimension = 0;
    for (int index = 0; index < sum.count; index++) {
        if (sum.terms[index].unit_len == 0) {
            has_unitless = 1;
        } else {
            has_dimension = 1;
        }
    }
    if (has_unitless && has_dimension) {
        return 0;
    }
    /* Keep canceled terms until their type contribution is checked. */
    cterm nonzero[CALC_MAX_TERMS];
    int nonzero_count = 0;
    for (int index = 0; index < sum.count; index++) {
        if (sum.terms[index].coeff.num != 0) {
            nonzero[nonzero_count++] = sum.terms[index];
        }
    }
    /* Zero terms still participate in math type checking. */
    if (nonzero_count < sum.count && sum.count > 1) {
        return 0;
    }
    css_buf result = {NULL, 0, 0, pool->oom};
    int formatted = 1;
    if (nonzero_count == 0) {
        if (sum.count == 1 && sum.terms[0].unit_len > 0 &&
            !css_char_unit_droppable(sum.terms[0].unit, sum.terms[0].unit_len)) {
            formatted = css_format_cterm(&result, &sum.terms[0]);
            *kind = CK_DIM;
        } else if (sum.count == 1 && (sum.terms[0].unit_len > 0 || !bare_zero_allowed)) {
            /* Bare zero can have a different type from a zero-valued calc(). */
            cbuf_puts(&result, "calc(");
            formatted = css_format_cterm(&result, &sum.terms[0]);
            cbuf_putc(&result, ')');
            *kind = CK_FUNC;
        } else {
            cbuf_putc(&result, '0');
            *kind = CK_NUM;
        }
    } else if (nonzero_count == 1) {
        formatted = css_format_cterm(&result, &nonzero[0]);
        *kind = nonzero[0].unit_len > 0 ? CK_DIM : CK_NUM;
    } else {
        *kind = CK_FUNC;
        cbuf_puts(&result, "calc(");
        formatted = css_format_cterm(&result, &nonzero[0]);
        for (int index = 1; formatted && index < nonzero_count; index++) {
            cterm term = nonzero[index];
            int negative = term.coeff.num < 0;
            if (negative) {
                term.coeff.num = -term.coeff.num;
            }
            cbuf_puts(&result, negative ? " - " : " + ");
            formatted = css_format_cterm(&result, &term);
        }
        cbuf_putc(&result, ')');
    }
    if (!formatted) {
        cbuf_free(&result);
        return 0;
    }
    *out_off = pool_run(pool, result.data, result.len);
    *out_len = result.len;
    cbuf_free(&result);
    return 1;
}

/* Fold a color function rendered at (off, len) whose calc() argument kept the fold from reading a number: rendering
   folded the calc() (`rgb(calc(1),0,0)` became `rgb(1,0,0)`), so the fold reads the arguments as the next call would,
   the post-order esbuild applies to calc() inside a color. Out of line: only a calc() inside a color reaches it. */
CSS_NOINLINE static void css_refold_color(css_buf *pool, Py_ssize_t *out_off, Py_ssize_t *out_len, css_compkind *kind) {
    if (*pool->oom) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;       /* GCOVR_EXCL_LINE: the rendered call may not be in the pool */
    }
    /* the fold appends to the pool, so the tokens read a copy of the rendered call */
    css_buf call = {NULL, 0, 0, pool->oom};
    cbuf_put_run(&call, pool->data + *out_off, *out_len);
    token_vec tokens = {.oom = pool->oom};
    css_tokenize(call.data, call.len, &tokens);
    Py_ssize_t off;
    Py_ssize_t len;
    /* a failed copy or tokenization leaves too few tokens for the call's name, parenthesis and arguments */
    if (!*pool->oom && /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        css_try_color_func(pool, &tokens, 2, tokens.len - 1, &off, &len, kind) > 0) {
        *out_off = off;
        *out_len = len;
    }
    css_free(tokens.items);
    cbuf_free(&call);
}

/* Render a function value, first trying to simplify a calc()/fold an rgb()/hsl() color, else the generic form. kind is
   the rendered text's component kind: CK_FUNC for a function call (so the assembler glues the following component),
   else the hex, keyword, number or dimension a fold produced, typed as the next call would read that text. */
static void css_emit_function(css_buf *pool, token_vec *vec, Py_ssize_t name_index, Py_ssize_t close_index,
                              Py_ssize_t *out_off, Py_ssize_t *out_len, css_compkind *kind, int bare_zero_allowed) {
    css_token *name_token = &vec->items[name_index];
    if (css_run_ieq(name_token->text, name_token->text_len, "calc") &&
        css_try_calc(pool, vec, name_index + 2, close_index, out_off, out_len, kind, bare_zero_allowed)) {
        return;
    }
    /* only rgb()/rgba()/hsl()/hsla() fold to a color, so any other name skips the call */
    css_char first = css_lower(name_token->text[0]);
    int color = first == 'r' || first == 'h'
                    ? css_try_color_func(pool, vec, name_index + 2, close_index, out_off, out_len, kind)
                    : 0;
    if (color > 0) {
        return;
    }
    css_render_function(pool, vec, name_index, close_index, out_off, out_len);
    *kind = CK_FUNC;
    if (color < 0) {
        css_refold_color(pool, out_off, out_len, kind);
    }
}

/* Minify a function's argument list into out, recursing for nested functions. var()
   keeps its raw fallback; calc/min/max/clamp keep their spaced operators. */
static void css_minify_func_args(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *name,
                                 Py_ssize_t name_len, css_buf *out) {
    int is_var = css_run_ieq(name, name_len, "var");
    int keep_ws = css_is_math_func(name, name_len);
    int pending_ws = 0;
    Py_ssize_t index = start;
    while (index < end) {
        css_token *token = &vec->items[index];
        /* a comment separates like whitespace, so dropping it cannot glue its neighbors into one token */
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            pending_ws = 1;
            index++;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == ',') {
            css_rtrim(out);
            cbuf_putc(out, ',');
            pending_ws = 0;
            index++;
            continue;
        }
        if (keep_ws && ((token->kind == CSS_DELIM && token->delim == '+') ||
                        (token->kind == CSS_IDENT && token->text_len == 1 && token->text[0] == '-'))) {
            Py_ssize_t before = index;
            while (before > start && vec->items[before - 1].kind == CSS_COMMENT) {
                before--;
            }
            Py_ssize_t after = index + 1;
            while (after < end && vec->items[after].kind == CSS_COMMENT) {
                after++;
            }
            if (before == start || vec->items[before - 1].kind != CSS_WS || after == end ||
                vec->items[after].kind != CSS_WS) {
                const css_char *source = vec->items[start - 1].text + 1;
                /* Synthetic end-tag whitespace precedes a slash, so it cannot end this argument range. */
                const css_token *last = &vec->items[end - 1];
                out->len = 0;
                cbuf_put_run(out, source, last->text + last->text_len + last->unit_len - source);
                return;
            }
        }
        /* a lone '-' tokenizes as an identifier, never a delim, so only '+' appears as a delim operator here */
        if (!is_var && keep_ws && token->kind == CSS_DELIM && token->delim == '+') {
            if (out->len > 0 && out->data[out->len - 1] != ' ') {
                cbuf_putc(out, ' ');
            }
            cbuf_putc(out, token->delim);
            cbuf_putc(out, ' ');
            pending_ws = 0;
            index++;
            continue;
        }
        /* a leading space is kept only between two value pieces, never after '(' or ',' */
        if (pending_ws && out->len > 0 && out->data[out->len - 1] != ' ' && out->data[out->len - 1] != ',' &&
            out->data[out->len - 1] != '(') {
            cbuf_putc(out, ' ');
        }
        pending_ws = 0;
        if (token->kind == CSS_IDENT && index + 1 < end && vec->items[index + 1].kind == CSS_DELIM &&
            vec->items[index + 1].delim == '(') {
            Py_ssize_t close_index = css_match_paren(vec, index + 1, end);
            if (css_nesting_enter(vec)) {
                Py_ssize_t off;
                Py_ssize_t len;
                css_compkind kind;
                css_emit_function(pool, vec, index, close_index, &off, &len, &kind, 0);
                css_nesting_leave(vec);
                cbuf_put_run(out, pool->data + off, len);
            } else {
                /* past the nesting cap the nested call goes out as its source text, which reparses to the same value */
                const css_token *close = &vec->items[close_index];
                cbuf_put_run(out, token->text, close->text + close->text_len - token->text);
            }
            index = close_index + 1;
            continue;
        }
        if (token->kind == CSS_NUM) {
            if (is_var) { /* var() keeps its fallback verbatim, so the number and unit are not minified */
                cbuf_put_run(out, token->text, token->text_len);
                cbuf_put_run(out, (token->text + token->text_len), token->unit_len);
            } else {
                Py_ssize_t off;
                Py_ssize_t len;
                css_format_dimension(pool, token, !keep_ws, &off, &len);
                /* dropping a sign or unit (e.g. +0/-0/0px -> 0) can glue the number onto the token before it to read as
                   one different token (CSS Syntax 3 §9.1), including a second adjacent number in an invalid calc, so
                   keep a boundary as the declaration-value path does; a negative number after a unitless one starts
                   no unit (§4.3.3), so `1-1` stays two numbers */
                const css_token *before = &vec->items[index - 1];
                int negative_after_number = pool->data[off] == '-' && before->kind == CSS_NUM && before->unit_len == 0;
                if (out->len > 0 && !negative_after_number &&
                    css_would_merge(out->data[out->len - 1], 0, pool->data + off, len)) {
                    cbuf_putc(out, ' ');
                }
                cbuf_put_run(out, pool->data + off, len);
            }
        } else if (token->kind == CSS_STR) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_minify_string(pool, token->text, token->text_len, &off, &len);
            /* local(<font-family-name>): a quoted name drops its quotes only when it is a valid identifier (Fonts 4
               §src local()); local("123") must keep its quotes since 123 is a <number>, not a <custom-ident>. */
            if (!is_var && css_run_ieq(name, name_len, "local") && css_is_ident_string(pool->data + off + 1, len - 2)) {
                cbuf_put_run(out, pool->data + off + 1, len - 2);
            } else {
                cbuf_put_run(out, pool->data + off, len);
            }
        } else {
            cbuf_put_run(out, token->text, token->text_len);
        }
        index++;
    }
    css_rtrim(out);
}

#endif /* TURBOHTML_CSS_CALC_H */
