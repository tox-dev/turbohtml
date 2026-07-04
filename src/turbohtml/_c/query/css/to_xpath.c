/* Translate a CSS selector list into an equivalent XPath 1.0 expression, the engine
   behind turbohtml.convert.css_to_xpath (the cssselect replacement). compiled as its own
   translation unit (spec-tracked in issue #478); it reuses the selector parser (run with no
   tree: atoms stay unresolved, only the parsed structure matters).

   The output is not cssselect's string: every emitted predicate is context-free (no
   bare position() tests), so the same fragment is valid as a step predicate and inside
   a nested condition, and the selected node-set matches turbohtml's own CSS engine --
   including the HTML case-insensitive attribute set and the Selectors 4 semantics of
   :empty and the input pseudo-classes, where cssselect approximates. Constructs XPath
   1.0 cannot express (:scope beyond the leftmost compound, :dir(), :default, of-type
   pseudo-classes without a type) raise NotImplementedError, which the Python facade
   maps to turbohtml.convert.ExpressionError. */

#include "dom/nodes.h"
#include "query/css/selector.h"

typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed; /* an allocation failed; emission becomes a no-op */
} xt_buf;

typedef struct {
    xt_buf out;
    const char *error; /* the ExpressionError message, NULL while translatable */
} xt_ctx;

static void xt_reserve(xt_buf *buf, Py_ssize_t extra) {
    if (buf->len + extra <= buf->cap) {
        return;
    }
    Py_ssize_t grown = buf->cap ? buf->cap : 256;
    while (grown < buf->len + extra) {
        grown *= 2;
    }
    Py_UCS4 *fresh = PyMem_Realloc(buf->data, (size_t)grown * sizeof(Py_UCS4));
    if (fresh == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        buf->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    buf->data = fresh;
    buf->cap = grown;
}

static void xt_char(xt_buf *buf, Py_UCS4 ch) {
    xt_reserve(buf, 1);
    if (buf->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    buf->data[buf->len++] = ch;
}

/* Append an ASCII string (the emitters' fixed XPath syntax). */
static void xt_text(xt_buf *buf, const char *text) {
    Py_ssize_t length = (Py_ssize_t)strlen(text);
    xt_reserve(buf, length);
    if (buf->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        buf->data[buf->len++] = (Py_UCS4)(unsigned char)text[index];
    }
}

/* Append a code-point slice, ASCII-lowercased when fold is set. */
static void xt_slice(xt_buf *buf, const Py_UCS4 *text, Py_ssize_t length, int fold) {
    xt_reserve(buf, length);
    if (buf->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        buf->data[buf->len++] = sel_fold(text[index], fold);
    }
}

static void xt_int(xt_buf *buf, long value) {
    char digits[24];
    (void)snprintf(digits, sizeof(digits), "%ld", value);
    xt_text(buf, digits);
}

/* Whether a name can appear verbatim as an XPath name test / @-reference; the same
   conservative pattern cssselect uses. The letter and digit tests use the unsigned-wrap
   range form (one comparison each) so every gcov branch here is reachable. */
static int xt_safe_name(const Py_UCS4 *name, Py_ssize_t length) {
    /* the selector parser rejects empty identifiers, so length is at least 1 */
    if ((name[0] | 32) - 'a' >= 26 && name[0] != '_') {
        return 0;
    }
    for (Py_ssize_t index = 1; index < length; index++) {
        Py_UCS4 ch = name[index];
        if ((ch | 32) - 'a' >= 26 && ch - '0' >= 10 && ch != '_' && ch != '.' && ch != '-') {
            return 0;
        }
    }
    return 1;
}

/* Whether the slice contains CSS whitespace (a ~= / class token can never match one). */
static int xt_has_space(const Py_UCS4 *text, Py_ssize_t length) {
    for (Py_ssize_t index = 0; index < length; index++) {
        if (is_space(text[index])) {
            return 1;
        }
    }
    return 0;
}

/* Emit an XPath 1.0 string literal. XPath has no quote escapes, so a value holding
   both quote kinds becomes a concat() of single-quoted runs and double-quoted
   apostrophe runs (the cssselect xpath_literal construction). */
static void xt_literal(xt_ctx *ctx, const Py_UCS4 *text, Py_ssize_t length) {
    int squote = 0;
    int dquote = 0;
    for (Py_ssize_t index = 0; index < length; index++) {
        squote |= text[index] == '\'';
        dquote |= text[index] == '"';
    }
    if (!squote || !dquote) {
        Py_UCS4 quote = squote ? '"' : '\'';
        xt_char(&ctx->out, quote);
        xt_slice(&ctx->out, text, length, 0);
        xt_char(&ctx->out, quote);
        return;
    }
    xt_text(&ctx->out, "concat(");
    Py_ssize_t cursor = 0;
    int first = 1;
    while (cursor < length) {
        Py_UCS4 quote = text[cursor] == '\'' ? '"' : '\'';
        Py_ssize_t start = cursor;
        while (cursor < length && (text[cursor] == '\'') == (quote == '"')) {
            cursor++;
        }
        if (!first) {
            xt_char(&ctx->out, ',');
        }
        first = 0;
        xt_char(&ctx->out, quote);
        xt_slice(&ctx->out, text + start, cursor - start, 0);
        xt_char(&ctx->out, quote);
    }
    xt_text(&ctx->out, ")");
}

/* Emit a literal of the transformed attribute value: ASCII-lowercased when fold (the
   case-insensitive comparisons lowercase both sides), space-padded for the ~= token
   test, or dash-suffixed for the |= prefix test. */
static void xt_value_literal(xt_ctx *ctx, const Py_UCS4 *value, Py_ssize_t length, int fold, int pad, int dash) {
    Py_UCS4 *temp = PyMem_Malloc((size_t)(length + 3) * sizeof(Py_UCS4));
    if (temp == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t at = 0;
    if (pad) {
        temp[at++] = ' ';
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        temp[at++] = sel_fold(value[index], fold);
    }
    if (pad) {
        temp[at++] = ' ';
    }
    if (dash) {
        temp[at++] = '-';
    }
    xt_literal(ctx, temp, at);
    PyMem_Free(temp);
}

/* Emit the node-set reference for an attribute: @name for a safe name, otherwise the
   attribute::*[name() = '...'] form XPath needs for names its grammar rejects. */
static void xt_attr_ref(xt_ctx *ctx, const sel_simple *simple) {
    if (xt_safe_name(simple->name, simple->name_len)) {
        xt_char(&ctx->out, '@');
        xt_slice(&ctx->out, simple->name, simple->name_len, 1);
        return;
    }
    xt_text(&ctx->out, "attribute::*[name() = ");
    xt_value_literal(ctx, simple->name, simple->name_len, 1, 0, 0);
    xt_char(&ctx->out, ']');
}

/* Emit the attribute's comparison value: the reference itself, wrapped in an
   ASCII-lowercasing translate() when the comparison is case-insensitive. */
static void xt_attr_value_ref(xt_ctx *ctx, const sel_simple *simple, int fold) {
    if (!fold) {
        xt_attr_ref(ctx, simple);
        return;
    }
    xt_text(&ctx->out, "translate(");
    xt_attr_ref(ctx, simple);
    xt_text(&ctx->out, ", 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')");
}

/* Emit translate(@name, upper, lower) for a known safe ASCII attribute name (the
   input pseudo-classes compare @type and friends case-insensitively). */
static void xt_folded_attr(xt_ctx *ctx, const char *name) {
    xt_text(&ctx->out, "translate(@");
    xt_text(&ctx->out, name);
    xt_text(&ctx->out, ", 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')");
}

static int xt_compound_conds(xt_ctx *ctx, const sel_compound *compound, const sel_simple *skip);
static void xt_condition_alts(xt_ctx *ctx, const sel_complex *alts, int count);
static void xt_has_path(xt_ctx *ctx, const sel_complex *rel);

/* The type simple the step's node test can express: the first 'e' simple whose
   (lowercased) name XPath accepts verbatim, or NULL when the test must stay '*'. */
static const sel_simple *xt_nodetest(const sel_compound *compound) {
    for (int index = 0; index < compound->count; index++) {
        const sel_simple *simple = &compound->simples[index];
        if (simple->kind == 'e' && xt_safe_name(simple->name, simple->name_len)) {
            return simple;
        }
    }
    return NULL;
}

/* Emit the chosen node test: the type simple's lowercased name, or '*'. */
static void xt_emit_nodetest(xt_ctx *ctx, const sel_simple *test) {
    if (test != NULL) {
        xt_slice(&ctx->out, test->name, test->name_len, 1);
    } else {
        xt_char(&ctx->out, '*');
    }
}

/* Emit the An+B condition over a sibling count expression (the axis text), following
   the same derivation cssselect documents: with EXPR = count(axis) = position-1, an
   index matches An+B for some n >= 0 iff
     a == 0: EXPR = b-1
     a > 0:  EXPR >= b-1  and  (EXPR - (b-1)) mod a = 0
     a < 0:  EXPR <= b-1  and  ((b-1) - EXPR) mod -a = 0
   The mod operand is shifted by a non-negative constant so the dividend stays
   non-negative and truncating mod cannot flip its sign. Returns the number of
   condition terms written (0 when every index matches). */
static int xt_nth(xt_ctx *ctx, int nth_a, int nth_b, const Py_UCS4 *axis, Py_ssize_t axis_len) {
    long b_minus_1 = (long)nth_b - 1;
    if (nth_a == 1 && b_minus_1 <= 0) {
        return 0; /* n+b with b <= 1 matches every position */
    }
    if (nth_a <= 0 && b_minus_1 < 0) {
        xt_text(&ctx->out, "false()");
        return 1; /* a fixed or descending series that starts below the first position */
    }
    if (nth_a == 0) {
        xt_text(&ctx->out, "count(");
        xt_slice(&ctx->out, axis, axis_len, 0);
        xt_text(&ctx->out, ") = ");
        xt_int(&ctx->out, b_minus_1);
        return 1;
    }
    int wrote = 0;
    if (nth_a < 0 || b_minus_1 > 0) {
        xt_text(&ctx->out, "count(");
        xt_slice(&ctx->out, axis, axis_len, 0);
        xt_text(&ctx->out, nth_a > 0 ? ") >= " : ") <= ");
        xt_int(&ctx->out, b_minus_1);
        wrote = 1;
    }
    long step = nth_a > 0 ? nth_a : -(long)nth_a;
    if (step != 1) {
        if (wrote) {
            xt_text(&ctx->out, " and ");
        }
        long shift = ((-b_minus_1) % step + step) % step;
        if (shift != 0) {
            xt_text(&ctx->out, "(count(");
        } else {
            xt_text(&ctx->out, "count(");
        }
        xt_slice(&ctx->out, axis, axis_len, 0);
        xt_char(&ctx->out, ')');
        if (shift != 0) {
            xt_text(&ctx->out, " + ");
            xt_int(&ctx->out, shift);
            xt_char(&ctx->out, ')');
        }
        xt_text(&ctx->out, " mod ");
        xt_int(&ctx->out, step);
        xt_text(&ctx->out, " = 0");
        wrote++;
    }
    return wrote;
}

/* Copy the emitted tail of the output (from mark) into a fresh slice and truncate the
   output back, so a fragment can be re-spliced (the nth-* count axis appears in up to
   two terms). Returns NULL and fails the buffer on allocation failure. */
static Py_UCS4 *xt_detach(xt_ctx *ctx, Py_ssize_t mark, Py_ssize_t *out_len) {
    *out_len = ctx->out.len - mark; /* never zero: every detached fragment starts with an axis name */
    Py_UCS4 *copy = PyMem_Malloc((size_t)*out_len * sizeof(Py_UCS4));
    if (copy == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(copy, ctx->out.data + mark, (size_t)*out_len * sizeof(Py_UCS4));
    ctx->out.len = mark;
    return copy;
}

/* Emit the of-type sibling node test for the compound: its type simple's name, or
   *[name() = '...'] when XPath rejects the spelling. Returns 0 (with the error set)
   for a compound with no type selector, whose element type the translation cannot
   know. */
static int xt_of_type_test(xt_ctx *ctx, const sel_compound *compound) {
    const sel_simple *test = xt_nodetest(compound);
    if (test != NULL) {
        xt_emit_nodetest(ctx, test);
        return 1;
    }
    for (int index = 0; index < compound->count; index++) {
        const sel_simple *named = &compound->simples[index];
        if (named->kind == 'e') {
            xt_text(&ctx->out, "*[name() = ");
            xt_value_literal(ctx, named->name, named->name_len, 1, 0, 0);
            xt_char(&ctx->out, ']');
            return 1;
        }
    }
    ctx->error = "the of-type pseudo-classes need a type selector";
    return 0;
}

/* Emit the whole nth-* condition for a pseudo-class: build the sibling-count axis
   (preceding or following, filtered to the compound's type or the 'of S' condition),
   then the An+B algebra over it; ANDs in the 'of S' self test when present. */
static void xt_nth_pseudo(xt_ctx *ctx, const sel_simple *simple, const sel_compound *compound) {
    int last = simple->pseudo == PSEUDO_NTH_LAST_CHILD || simple->pseudo == PSEUDO_NTH_LAST_OF_TYPE;
    int of_type = simple->pseudo == PSEUDO_NTH_OF_TYPE || simple->pseudo == PSEUDO_NTH_LAST_OF_TYPE;
    Py_ssize_t mark = ctx->out.len;
    xt_text(&ctx->out, last ? "following-sibling::" : "preceding-sibling::");
    if (of_type) {
        if (!xt_of_type_test(ctx, compound)) {
            ctx->out.len = mark;
            return;
        }
    } else {
        xt_char(&ctx->out, '*');
        if (simple->sub != NULL) {
            xt_char(&ctx->out, '[');
            xt_condition_alts(ctx, simple->sub, simple->sub_count);
            xt_char(&ctx->out, ']');
        }
    }
    Py_ssize_t axis_len = 0;
    Py_UCS4 *axis = xt_detach(ctx, mark, &axis_len);
    if (axis == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int wrote = 0;
    /* only :nth-child()/:nth-last-child() can carry an 'of S' list, never the of-type pair */
    if (simple->sub != NULL) {
        xt_char(&ctx->out, '(');
        xt_condition_alts(ctx, simple->sub, simple->sub_count);
        xt_char(&ctx->out, ')');
        wrote = 1;
    }
    Py_ssize_t nth_mark = ctx->out.len;
    if (wrote) {
        xt_text(&ctx->out, " and ");
    }
    if (xt_nth(ctx, simple->nth_a, simple->nth_b, axis, axis_len) == 0) {
        ctx->out.len = nth_mark; /* every position matches: drop the dangling " and " */
    }
    PyMem_Free(axis);
}

/* The elements HTML can disable, as a self-test union. */
static void xt_disableable(xt_ctx *ctx) {
    xt_text(&ctx->out, "(self::button or self::input or self::select or self::textarea"
                       " or self::optgroup or self::option or self::fieldset)");
}

/* The condition for HTML "actually disabled": an own disabled attribute on a
   disableable element, an option inside a disabled optgroup, or a form control inside
   a disabled fieldset but not inside that fieldset's first legend. The legend rule
   compares counts: every ancestor first-legend's disabled-fieldset parent is itself a
   disabled fieldset ancestor, so a strict surplus means one fieldset disables us. */
static void xt_disabled_cond(xt_ctx *ctx) {
    xt_text(&ctx->out, "(@disabled and ");
    xt_disableable(ctx);
    xt_text(&ctx->out, ") or (self::option and parent::optgroup[@disabled])"
                       " or ((self::button or self::input or self::select or self::textarea)"
                       " and count(ancestor::fieldset[@disabled])"
                       " > count(ancestor::legend[not(preceding-sibling::legend)]/parent::fieldset[@disabled]))");
}

/* The condition for :read-write: a mutable text input, a writable textarea, or an
   element made editable by its own contenteditable attribute. */
static void xt_read_write_cond(xt_ctx *ctx) {
    static const char *const mutable_types[] = {
        "text", "search",         "url",   "tel",  "email", "password", "number",
        "date", "datetime-local", "month", "week", "time",
    };
    xt_text(&ctx->out, "(self::input and (not(@type) or @type = ''");
    for (size_t index = 0; index < sizeof(mutable_types) / sizeof(mutable_types[0]); index++) {
        xt_text(&ctx->out, " or ");
        xt_folded_attr(ctx, "type");
        xt_text(&ctx->out, " = '");
        xt_text(&ctx->out, mutable_types[index]);
        xt_char(&ctx->out, '\'');
    }
    xt_text(&ctx->out, ") and not(@readonly) and not(");
    xt_disabled_cond(ctx);
    xt_text(&ctx->out, ")) or (self::textarea and not(@readonly) and not(");
    xt_disabled_cond(ctx);
    xt_text(&ctx->out, ")) or (@contenteditable = '' or ");
    xt_folded_attr(ctx, "contenteditable");
    xt_text(&ctx->out, " = 'true')");
}

/* The condition for :lang(): the nearest non-empty lang attribute on the element or
   an ancestor matches one of the comma-separated ranges by BCP 47 basic filtering. */
static void xt_lang_cond(xt_ctx *ctx, const sel_simple *simple) {
    xt_text(&ctx->out, "ancestor-or-self::*[@lang != ''][1][");
    Py_ssize_t cursor = 0;
    int wrote = 0;
    while (cursor < simple->value_len) {
        Py_ssize_t start = cursor;
        while (cursor < simple->value_len && simple->value[cursor] != ',') {
            cursor++;
        }
        Py_ssize_t end = cursor;
        if (cursor < simple->value_len) {
            cursor++; /* step over the comma */
        }
        while (start < end && is_space(simple->value[start])) {
            start++;
        }
        while (end > start && is_space(simple->value[end - 1])) {
            end--;
        }
        if (end - start >= 2 && (simple->value[start] == '"' || simple->value[start] == '\'') &&
            simple->value[end - 1] == simple->value[start]) {
            start++;
            end--;
        }
        if (end == start) {
            continue; /* an empty range matches nothing, mirroring the matcher */
        }
        if (wrote) {
            xt_text(&ctx->out, " or ");
        }
        wrote = 1;
        xt_char(&ctx->out, '(');
        xt_folded_attr(ctx, "lang");
        xt_text(&ctx->out, " = ");
        xt_value_literal(ctx, simple->value + start, end - start, 1, 0, 0);
        xt_text(&ctx->out, " or starts-with(");
        xt_folded_attr(ctx, "lang");
        xt_text(&ctx->out, ", ");
        xt_value_literal(ctx, simple->value + start, end - start, 1, 0, 1);
        xt_text(&ctx->out, "))");
    }
    if (!wrote) {
        xt_text(&ctx->out, "false()");
    }
    xt_char(&ctx->out, ']');
}

/* Emit the condition for one pseudo-class simple; the compound provides the type
   selector the of-type family counts by. Returns 0 for a pseudo-class that holds on
   every element (no condition needed). */
static int xt_pseudo_cond(xt_ctx *ctx, const sel_simple *simple, const sel_compound *compound) {
    switch (simple->pseudo) { /* GCOVR_EXCL_BR_LINE: the parser only stores known pseudo ids */
    case PSEUDO_ROOT:
        xt_text(&ctx->out, "not(parent::*)");
        return 1;
    case PSEUDO_EMPTY:
        xt_text(&ctx->out, "not(*) and not(text()[normalize-space(.)])");
        return 1;
    case PSEUDO_FIRST_CHILD:
        xt_text(&ctx->out, "not(preceding-sibling::*)");
        return 1;
    case PSEUDO_LAST_CHILD:
        xt_text(&ctx->out, "not(following-sibling::*)");
        return 1;
    case PSEUDO_ONLY_CHILD:
        xt_text(&ctx->out, "not(preceding-sibling::*) and not(following-sibling::*)");
        return 1;
    case PSEUDO_FIRST_OF_TYPE:
    case PSEUDO_LAST_OF_TYPE:
    case PSEUDO_ONLY_OF_TYPE: {
        if (simple->pseudo != PSEUDO_LAST_OF_TYPE) {
            xt_text(&ctx->out, "not(preceding-sibling::");
            if (!xt_of_type_test(ctx, compound)) {
                return 1;
            }
            xt_char(&ctx->out, ')');
        }
        if (simple->pseudo == PSEUDO_ONLY_OF_TYPE) {
            xt_text(&ctx->out, " and ");
        }
        if (simple->pseudo != PSEUDO_FIRST_OF_TYPE) {
            xt_text(&ctx->out, "not(following-sibling::");
            if (!xt_of_type_test(ctx, compound)) {
                return 1;
            }
            xt_char(&ctx->out, ')');
        }
        return 1;
    }
    case PSEUDO_NTH_CHILD:
    case PSEUDO_NTH_LAST_CHILD:
    case PSEUDO_NTH_OF_TYPE:
    case PSEUDO_NTH_LAST_OF_TYPE: {
        /* a series every position satisfies (such as :nth-child(n)) emits nothing */
        Py_ssize_t mark = ctx->out.len;
        xt_nth_pseudo(ctx, simple, compound);
        return ctx->out.len != mark;
    }
    case PSEUDO_SCOPE:
        /* only the leftmost lone :scope compound is expressible (handled by the path
           emitter); anywhere else there is no XPath 1.0 way to name the context node */
        ctx->error = ":scope is only supported as the leftmost compound of a selector";
        return 1;
    case PSEUDO_CHECKED:
        xt_text(&ctx->out, "((self::input and @checked and (");
        xt_folded_attr(ctx, "type");
        xt_text(&ctx->out, " = 'checkbox' or ");
        xt_folded_attr(ctx, "type");
        xt_text(&ctx->out, " = 'radio')) or (self::option and @selected))");
        return 1;
    case PSEUDO_DISABLED:
        xt_char(&ctx->out, '(');
        xt_disabled_cond(ctx);
        xt_char(&ctx->out, ')');
        return 1;
    case PSEUDO_ENABLED:
        xt_char(&ctx->out, '(');
        xt_disableable(ctx);
        xt_text(&ctx->out, " and not(");
        xt_disabled_cond(ctx);
        xt_text(&ctx->out, "))");
        return 1;
    case PSEUDO_REQUIRED:
        xt_text(&ctx->out, "((self::input or self::select or self::textarea) and @required)");
        return 1;
    case PSEUDO_OPTIONAL:
        xt_text(&ctx->out, "((self::input or self::select or self::textarea) and not(@required))");
        return 1;
    case PSEUDO_READ_ONLY:
        xt_text(&ctx->out, "not(");
        xt_read_write_cond(ctx);
        xt_char(&ctx->out, ')');
        return 1;
    case PSEUDO_READ_WRITE:
        xt_char(&ctx->out, '(');
        xt_read_write_cond(ctx);
        xt_char(&ctx->out, ')');
        return 1;
    case PSEUDO_DEFAULT:
        /* the form's first submit control needs a node-identity test XPath 1.0 lacks */
        ctx->error = ":default cannot be expressed in XPath 1.0";
        return 1;
    case PSEUDO_LANG:
        xt_lang_cond(ctx, simple);
        return 1;
    case PSEUDO_DIR:
        if (simple->nth_a == 0) {
            xt_text(&ctx->out, "false()"); /* a direction that is neither ltr nor rtl */
            return 1;
        }
        /* dir=auto resolves from the first strong character of the content, which
           XPath 1.0 string functions cannot inspect */
        ctx->error = ":dir() cannot be expressed in XPath 1.0";
        return 1;
    case PSEUDO_ANY_LINK:
        xt_text(&ctx->out, "((self::a or self::area) and @href)");
        return 1;
    case PSEUDO_NEVER:
        xt_text(&ctx->out, "false()");
        return 1;
    case PSEUDO_IS:
    case PSEUDO_WHERE:
        if (simple->sub == NULL) {
            xt_text(&ctx->out, "false()"); /* a forgiving list whose arms all dropped */
            return 1;
        }
        xt_char(&ctx->out, '(');
        xt_condition_alts(ctx, simple->sub, simple->sub_count);
        xt_char(&ctx->out, ')');
        return 1;
    case PSEUDO_NOT:
        xt_text(&ctx->out, "not(");
        xt_condition_alts(ctx, simple->sub, simple->sub_count);
        xt_char(&ctx->out, ')');
        return 1;
    default: { /* PSEUDO_HAS; its non-forgiving list always parses at least one arm */
        xt_char(&ctx->out, '(');
        for (int index = 0; index < simple->sub_count; index++) {
            if (index > 0) {
                xt_text(&ctx->out, " or ");
            }
            xt_has_path(ctx, &simple->sub[index]);
        }
        xt_char(&ctx->out, ')');
        return 1;
    }
    }
}

/* Emit the condition for one simple selector. Returns 0 when the simple holds on
   every element (the universal selector and the type already used as the node test). */
static int xt_simple_cond(xt_ctx *ctx, const sel_simple *simple, const sel_compound *compound, const sel_simple *skip) {
    switch (simple->kind) {
    case '*':
        return 0;
    case 'e':
        if (simple == skip) {
            return 0; /* already expressed as the step's node test */
        }
        if (xt_safe_name(simple->name, simple->name_len)) {
            xt_text(&ctx->out, "self::");
            xt_slice(&ctx->out, simple->name, simple->name_len, 1);
        } else {
            xt_text(&ctx->out, "name() = ");
            xt_value_literal(ctx, simple->name, simple->name_len, 1, 0, 0);
        }
        return 1;
    case '#':
        xt_text(&ctx->out, "@id = ");
        xt_value_literal(ctx, simple->name, simple->name_len, 0, 0, 0);
        return 1;
    case '.':
        if (xt_has_space(simple->name, simple->name_len)) {
            xt_text(&ctx->out, "false()"); /* a class token can never contain whitespace */
            return 1;
        }
        xt_text(&ctx->out, "@class and contains(concat(' ', normalize-space(@class), ' '), ");
        xt_value_literal(ctx, simple->name, simple->name_len, 0, 1, 0);
        xt_char(&ctx->out, ')');
        return 1;
    case ':':
        return xt_pseudo_cond(ctx, simple, compound);
    default: { /* '[' attribute */
        int fold = simple->ci || simple->ci_default;
        switch (simple->op) {
        case OP_EXISTS:
            xt_attr_ref(ctx, simple);
            return 1;
        case OP_EQ:
            xt_attr_value_ref(ctx, simple, fold);
            xt_text(&ctx->out, " = ");
            xt_value_literal(ctx, simple->value, simple->value_len, fold, 0, 0);
            return 1;
        case OP_INCLUDE:
            if (simple->value_len == 0 || xt_has_space(simple->value, simple->value_len)) {
                xt_text(&ctx->out, "false()"); /* a token list never yields such a token */
                return 1;
            }
            xt_attr_ref(ctx, simple);
            xt_text(&ctx->out, " and contains(concat(' ', normalize-space(");
            xt_attr_value_ref(ctx, simple, fold);
            xt_text(&ctx->out, "), ' '), ");
            xt_value_literal(ctx, simple->value, simple->value_len, fold, 1, 0);
            xt_char(&ctx->out, ')');
            return 1;
        case OP_DASH:
            xt_char(&ctx->out, '(');
            xt_attr_value_ref(ctx, simple, fold);
            xt_text(&ctx->out, " = ");
            xt_value_literal(ctx, simple->value, simple->value_len, fold, 0, 0);
            xt_text(&ctx->out, " or starts-with(");
            xt_attr_value_ref(ctx, simple, fold);
            xt_text(&ctx->out, ", ");
            xt_value_literal(ctx, simple->value, simple->value_len, fold, 0, 1);
            xt_text(&ctx->out, "))");
            return 1;
        case OP_PREFIX:
        case OP_SUFFIX:
        default: /* OP_SUBSTR */
            if (simple->value_len == 0) {
                xt_text(&ctx->out, "false()"); /* the spec: an empty pattern matches nothing */
                return 1;
            }
            if (simple->op == OP_PREFIX) {
                xt_text(&ctx->out, "starts-with(");
                xt_attr_value_ref(ctx, simple, fold);
                xt_text(&ctx->out, ", ");
                xt_value_literal(ctx, simple->value, simple->value_len, fold, 0, 0);
                xt_char(&ctx->out, ')');
            } else if (simple->op == OP_SUBSTR) {
                xt_text(&ctx->out, "contains(");
                xt_attr_value_ref(ctx, simple, fold);
                xt_text(&ctx->out, ", ");
                xt_value_literal(ctx, simple->value, simple->value_len, fold, 0, 0);
                xt_char(&ctx->out, ')');
            } else {
                xt_text(&ctx->out, "substring(");
                xt_attr_value_ref(ctx, simple, fold);
                xt_text(&ctx->out, ", string-length(");
                xt_attr_value_ref(ctx, simple, fold);
                xt_text(&ctx->out, ") - ");
                xt_int(&ctx->out, (long)simple->value_len - 1);
                xt_text(&ctx->out, ") = ");
                xt_value_literal(ctx, simple->value, simple->value_len, fold, 0, 0);
            }
            return 1;
        }
    }
    }
}

/* Emit the compound's conditions joined by " and ", skipping the type simple already
   expressed as the node test. Returns the number of conditions written. */
static int xt_compound_conds(xt_ctx *ctx, const sel_compound *compound, const sel_simple *skip) {
    int wrote = 0;
    for (int index = 0; index < compound->count; index++) {
        Py_ssize_t mark = ctx->out.len;
        if (wrote) {
            xt_text(&ctx->out, " and ");
        }
        if (!xt_simple_cond(ctx, &compound->simples[index], compound, skip)) {
            ctx->out.len = mark; /* the simple needs no condition: drop the joiner */
        } else {
            wrote++;
        }
    }
    return wrote;
}

/* Emit the existence condition for the chain to the left of compounds[index]: the
   reverse axis named by the combinator, its target's node test, and (bracketed) the
   target's own conditions plus the chain further left. */
static void xt_left_chain(xt_ctx *ctx, const sel_complex *complex, int index) {
    char combinator = complex->compounds[index].combinator;
    const sel_compound *target = &complex->compounds[index - 1];
    const sel_simple *test = combinator == '+' ? NULL : xt_nodetest(target);
    switch (combinator) {
    case '>':
        xt_text(&ctx->out, "parent::");
        break;
    case '+':
        /* the nearest preceding element sibling: the reverse axis' position 1; its
           node test moves into the predicate as a self test */
        xt_text(&ctx->out, "preceding-sibling::*[1]");
        break;
    case '~':
        xt_text(&ctx->out, "preceding-sibling::");
        break;
    default: /* descendant */
        xt_text(&ctx->out, "ancestor::");
        break;
    }
    if (combinator != '+') {
        xt_emit_nodetest(ctx, test);
    }
    Py_ssize_t mark = ctx->out.len;
    xt_char(&ctx->out, '[');
    int wrote = xt_compound_conds(ctx, target, test);
    if (index - 1 > 0) {
        if (wrote) {
            xt_text(&ctx->out, " and ");
        }
        xt_left_chain(ctx, complex, index - 1);
        wrote = 1;
    }
    if (wrote) {
        xt_char(&ctx->out, ']');
    } else {
        ctx->out.len = mark; /* a bare axis step needs no empty predicate */
    }
}

/* Emit the condition that the context element is the subject of the complex selector:
   its own compound's conditions plus the left-chain existence test. */
static void xt_condition_complex(xt_ctx *ctx, const sel_complex *complex) {
    int subject = complex->count - 1;
    int wrote = xt_compound_conds(ctx, &complex->compounds[subject], NULL);
    if (subject > 0) {
        if (wrote) {
            xt_text(&ctx->out, " and ");
        }
        xt_left_chain(ctx, complex, subject);
        wrote = 1;
    }
    if (!wrote) {
        xt_text(&ctx->out, "self::*"); /* the universal subject holds on every element */
    }
}

/* Emit '(arm) or (arm) ...' for a nested selector list (:is()/:not()/nth 'of S'). */
static void xt_condition_alts(xt_ctx *ctx, const sel_complex *alts, int count) {
    for (int index = 0; index < count; index++) {
        if (index > 0) {
            xt_text(&ctx->out, " or ");
        }
        xt_char(&ctx->out, '(');
        xt_condition_complex(ctx, &alts[index]);
        xt_char(&ctx->out, ')');
    }
}

/* Emit one location step: the node test plus the compound's bracketed conditions. */
static void xt_step(xt_ctx *ctx, const sel_compound *compound) {
    const sel_simple *test = xt_nodetest(compound);
    xt_emit_nodetest(ctx, test);
    Py_ssize_t mark = ctx->out.len;
    xt_char(&ctx->out, '[');
    if (xt_compound_conds(ctx, compound, test) == 0) {
        ctx->out.len = mark;
    } else {
        xt_char(&ctx->out, ']');
    }
}

/* Emit a :has() relative selector as a predicate path from the anchor: each compound
   becomes a step whose axis is named by its (leading or interior) combinator. */
static void xt_has_path(xt_ctx *ctx, const sel_complex *rel) {
    for (int index = 0; index < rel->count; index++) {
        if (index > 0) {
            xt_char(&ctx->out, '/');
        }
        switch (rel->compounds[index].combinator) {
        case '>':
            break; /* the default child axis */
        case '+':
            xt_text(&ctx->out, "following-sibling::*[1]/self::");
            break;
        case '~':
            xt_text(&ctx->out, "following-sibling::");
            break;
        default: /* descendant */
            xt_text(&ctx->out, "descendant::");
            break;
        }
        xt_step(ctx, &rel->compounds[index]);
    }
}

/* Whether the compound is a lone :scope, the one placement XPath can express. */
static int xt_is_lone_scope(const sel_compound *compound) {
    return compound->count == 1 && compound->simples[0].kind == ':' && compound->simples[0].pseudo == PSEUDO_SCOPE;
}

/* Emit the absolute path for one top-level complex selector after the prefix. */
static void xt_path_complex(xt_ctx *ctx, const sel_complex *complex) {
    for (int index = 0; index < complex->count; index++) {
        const sel_compound *compound = &complex->compounds[index];
        if (index == 0) {
            if (xt_is_lone_scope(compound)) {
                /* position 1 on the prefix's descendant-or-self axis is the context
                   node itself, the same trick cssselect uses for :scope */
                xt_text(&ctx->out, "*[1]");
                continue;
            }
        } else {
            switch (compound->combinator) {
            case '>':
                xt_char(&ctx->out, '/');
                break;
            case '+':
                xt_text(&ctx->out, "/following-sibling::*[1]/self::");
                break;
            case '~':
                xt_text(&ctx->out, "/following-sibling::");
                break;
            default: /* descendant */
                xt_text(&ctx->out, "/descendant::");
                break;
            }
        }
        xt_step(ctx, compound);
    }
}

/* _css_to_xpath(selector, prefix, /): translate a CSS selector list to XPath 1.0.
   Raises SelectorSyntaxError on a selector the CSS grammar rejects and NotImplementedError
   on a valid selector XPath 1.0 cannot express; the facade maps the latter to ExpressionError. */
PyObject *turbohtml_css_to_xpath(PyObject *module, PyObject *args) {
    PyObject *selector = NULL;
    PyObject *prefix = NULL;
    if (!PyArg_ParseTuple(args, "UU:_css_to_xpath", &selector, &prefix)) {
        return NULL;
    }
    Py_UCS4 *source = PyUnicode_AsUCS4Copy(selector);
    if (source == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    sel_parser parser = {source, 0, PyUnicode_GET_LENGTH(selector), NULL, 0, 0, 0, "unexpected token"};
    sel_complex *alts = NULL;
    int count = 0;
    if (sel_parse_alts(&parser, &alts, &count, 0, 0, 0) < 0) {
        sel_raise(((module_state *)PyModule_GetState(module))->selector_error, selector, &parser);
        PyMem_Free(source);
        return NULL;
    }
    Py_UCS4 *prefix_ucs4 = PyUnicode_AsUCS4Copy(prefix);
    if (prefix_ucs4 == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        sel_free_alts(alts, count); /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(source);         /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    xt_ctx ctx = {{NULL, 0, 0, 0}, NULL};
    for (int index = 0; index < count && ctx.error == NULL; index++) {
        if (index > 0) {
            xt_text(&ctx.out, " | ");
        }
        xt_slice(&ctx.out, prefix_ucs4, PyUnicode_GET_LENGTH(prefix), 0);
        xt_path_complex(&ctx, &alts[index]);
    }
    sel_free_alts(alts, count);
    PyMem_Free(source);
    PyMem_Free(prefix_ucs4);
    PyObject *result = NULL;
    if (ctx.error != NULL) {
        PyErr_SetString(PyExc_NotImplementedError, ctx.error);
    } else if (!ctx.out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, ctx.out.data, ctx.out.len);
    } else {              /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyMem_Free(ctx.out.data);
    return result;
}
