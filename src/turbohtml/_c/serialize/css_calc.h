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

/* Parse a CSS number string exactly into a rational; returns 0 on overflow. The text is a whole number token from the
   tokenizer's css_scan_number grammar, so it always carries a digit, any exponent has digits, and the parse consumes
   the entire string -- integer overflow is the only failure. */
static int css_num_to_rat(const css_char *text, Py_ssize_t len, crat *out) {
    long long num = 0;
    int frac_digits = 0;
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
    int exponent = 0;
    int exp_negative = 0;
    /* after the mantissa, the only remaining char a number token can carry is the exponent marker e/E */
    if (index < len) {
        index++;
        /* css_scan_number includes the exponent marker only when digits follow, so a char is always present here */
        if (text[index] == '+' || text[index] == '-') {
            exp_negative = text[index] == '-';
            index++;
        }
        /* the exponent's digit run is the final part of the numeric text, so every remaining char is a digit */
        for (; index < len; index++) {
            exponent = exponent * 10 + (text[index] - '0');
        }
    }
    if (negative) {
        num = -num;
    }
    int power = (exp_negative ? -exponent : exponent) - frac_digits;
    long long den = 1;
    if (power >= 0) {
        for (int step = 0; step < power; step++) {
            if (css_mul_overflow(num, 10, &num)) {
                return 0;
            }
        }
    } else {
        for (int step = 0; step < -power; step++) {
            if (css_mul_overflow(den, 10, &den)) {
                return 0;
            }
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

static csum calc_parse_sum(calc_parser *parser);

static csum calc_parse_value(calc_parser *parser) {
    csum result = {0};
    calc_skip_ws(parser);
    if (parser->pos >= parser->end) {
        return result;
    }
    css_token *token = &parser->vec->items[parser->pos];
    if (token->kind == CSS_NUM) {
        parser->pos++;
        if (token->unit_len >= CALC_MAX_UNIT) {
            return result;
        }
        cterm term;
        if (!css_num_to_rat(token->text, token->text_len, &term.coeff)) {
            return result;
        }
        term.unit_len = (int)token->unit_len;
        for (int index = 0; index < term.unit_len; index++) {
            term.unit[index] = (char)css_lower((token->text + token->text_len)[index]);
        }
        result.ok = 1;
        csum_add_term(&result, &term); /* the first term always fits an empty sum (no like-unit, a free slot) */
        return result;
    }
    if (token->kind == CSS_DELIM && token->delim == '(') {
        parser->pos++;
        result = calc_parse_sum(parser);
        calc_skip_ws(parser);
        if (parser->pos < parser->end && parser->vec->items[parser->pos].kind == CSS_DELIM &&
            parser->vec->items[parser->pos].delim == ')') {
            parser->pos++;
        } else {
            result.ok = 0;
        }
        return result;
    }
    if (token->kind == CSS_IDENT && parser->pos + 1 < parser->end &&
        parser->vec->items[parser->pos + 1].kind == CSS_DELIM && parser->vec->items[parser->pos + 1].delim == '(' &&
        css_run_ieq(token->text, token->text_len, "calc")) {
        Py_ssize_t close = css_match_paren(parser->vec, parser->pos + 1, parser->end);
        calc_parser inner = {parser->vec, parser->pos + 2, close};
        result = calc_parse_sum(&inner);
        calc_skip_ws(&inner);
        if (inner.pos != close) {
            result.ok = 0;
        }
        parser->pos = close + 1;
        return result;
    }
    return result; /* an opaque term (var/min/max/clamp/ident/...) cannot be evaluated */
}

static csum calc_parse_product(calc_parser *parser) {
    csum acc = calc_parse_value(parser);
    if (!acc.ok) {
        return acc;
    }
    for (;;) {
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
        csum rhs = calc_parse_value(parser);
        if (!rhs.ok) {
            acc.ok = 0;
            return acc;
        }
        crat scalar;
        if (is_div) {
            if (csum_as_scalar(&rhs, &scalar) && scalar.num != 0) {
                crat inverse = {scalar.den, scalar.num};
                if (inverse.den < 0) {
                    inverse.num = -inverse.num;
                    inverse.den = -inverse.den;
                }
                if (!csum_scale(&acc, inverse)) {
                    acc.ok = 0;
                    return acc;
                }
            } else {
                acc.ok = 0;
                return acc;
            }
        } else if (csum_as_scalar(&rhs, &scalar)) {
            if (!csum_scale(&acc, scalar)) {
                acc.ok = 0;
                return acc;
            }
        } else if (csum_as_scalar(&acc, &scalar)) {
            if (!csum_scale(&rhs, scalar)) {
                acc.ok = 0;
                return acc;
            }
            acc = rhs;
        } else {
            acc.ok = 0;
            return acc;
        }
    }
    return acc;
}

static csum calc_parse_sum(calc_parser *parser) {
    csum acc = calc_parse_product(parser);
    if (!acc.ok) {
        return acc;
    }
    for (;;) {
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
            acc.ok = 0;
            return acc;
        }
        parser->pos++;
        csum rhs = calc_parse_product(parser);
        if (!rhs.ok) {
            acc.ok = 0;
            return acc;
        }
        for (int index = 0; index < rhs.count; index++) {
            cterm term = rhs.terms[index];
            if (is_sub) {
                term.coeff.num = -term.coeff.num;
            }
            if (!csum_add_term(&acc, &term)) {
                acc.ok = 0;
                return acc;
            }
        }
    }
    return acc;
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

/* Format a zero term, keeping the unit only where a 0 of that unit is not the same as a bare 0 (so 0px -> 0 but a
   0% / 0s / 0deg... keeps its unit, matching the dimension rules). */
static void css_format_zero_term(css_buf *out, const cterm *term) {
    cbuf_putc(out, '0');
    if (term->unit_len > 0 && !css_char_unit_droppable(term->unit, term->unit_len)) {
        for (int index = 0; index < term->unit_len; index++) {
            cbuf_putc(out, (css_char)(unsigned char)term->unit[index]);
        }
    }
}

/* Try to simplify calc(args); returns 1 and writes the shortest exact form to the pool, 0 to keep the input. */
static int css_try_calc(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, Py_ssize_t *out_off,
                        Py_ssize_t *out_len) {
    calc_parser parser = {vec, start, end};
    csum sum = calc_parse_sum(&parser);
    calc_skip_ws(&parser);
    if (!sum.ok || parser.pos != end) {
        return 0;
    }
    /* the sum keeps canceled (zero) terms to preserve units; drop them now unless every term is zero */
    cterm nonzero[CALC_MAX_TERMS];
    int nonzero_count = 0;
    for (int index = 0; index < sum.count; index++) {
        if (sum.terms[index].coeff.num != 0) {
            nonzero[nonzero_count++] = sum.terms[index];
        }
    }
    css_buf result = {NULL, 0, 0, 0};
    int formatted = 1;
    if (nonzero_count == 0) {
        if (sum.count == 1) {
            css_format_zero_term(&result, &sum.terms[0]);
        } else {
            cbuf_putc(&result, '0');
        }
    } else if (nonzero_count == 1) {
        formatted = css_format_cterm(&result, &nonzero[0]);
    } else {
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

/* Render a function value, first trying to simplify a calc()/fold an rgb()/hsl() color, else the generic form.
   ends_paren is set when the rendered text is a function call (so the assembler glues the following component). */
static void css_emit_function(css_buf *pool, token_vec *vec, Py_ssize_t name_index, Py_ssize_t close_index,
                              Py_ssize_t *out_off, Py_ssize_t *out_len, int *ends_paren) {
    css_token *name_token = &vec->items[name_index];
    if (css_run_ieq(name_token->text, name_token->text_len, "calc") &&
        css_try_calc(pool, vec, name_index + 2, close_index, out_off, out_len)) {
        /* a successful calc/color render always emits at least one byte, so out_len is positive */
        *ends_paren = pool->data[*out_off + *out_len - 1] == ')';
        return;
    }
    if (css_try_color_func(pool, vec, name_index + 2, close_index, name_token->text, name_token->text_len, out_off,
                           out_len)) {
        *ends_paren = pool->data[*out_off + *out_len - 1] == ')';
        return;
    }
    css_render_function(pool, vec, name_index, close_index, out_off, out_len);
    *ends_paren = 1;
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
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            index++;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
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
            Py_ssize_t off;
            Py_ssize_t len;
            int ends_paren;
            css_emit_function(pool, vec, index, close_index, &off, &len, &ends_paren);
            cbuf_put_run(out, pool->data + off, len);
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
                cbuf_put_run(out, pool->data + off, len);
            }
        } else if (token->kind == CSS_STR) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_minify_string(pool, token->text, token->text_len, &off, &len);
            /* local(<font-family-name>): a quoted name drops its quotes only when it is a valid identifier (Fonts 4
               §src local()); local("123") must keep its quotes since 123 is a <number>, not a <custom-ident>. */
            if (!is_var && css_run_ieq(name, name_len, "local") && len >= 2 &&
                css_is_ident_string(pool->data + off + 1, len - 2)) {
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
