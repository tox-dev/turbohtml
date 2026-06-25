/* A CSS-selector to XPath-1.0 translator, the engine behind turbohtml.convert.

   It compiles a selector with the css/selector.h parser (against a throwaway empty
   tree, since translation needs the parsed names, not a tree to match), then walks
   the resulting AST left to right emitting an equivalent XPath 1.0 location-path
   union. The emitted path, evaluated by the query/xpath engine, selects the same
   element set the css engine matches, which the differential test suite checks.

   The supported subset mirrors cssselect's GenericTranslator: type, universal,
   class, id, every attribute operator, the four combinators, and the structural
   pseudo-classes (:root, :empty, the first/last/only and nth families, :not). A
   construct the parser accepts but this subset cannot faithfully express in XPath
   1.0 (a relational :has(), the input-state pseudo-classes, :lang(), an *-of-type
   without a concrete type) raises a ValueError the facade re-raises as
   SelectorSyntaxError. This header is included only by dom/element.c, which already
   includes selector.h, so the parser's static helpers stay in one translation unit. */

#ifndef TURBOHTML_CSS_TRANSLATE_H
#define TURBOHTML_CSS_TRANSLATE_H

/* A growable UCS-4 buffer the emitted XPath is built into; a sticky error flag
   short-circuits every append once an allocation fails. */
typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int error;
} xtr_buf;

static int xtr_reserve(xtr_buf *buf, Py_ssize_t extra) {
    if (buf->len + extra <= buf->cap) {
        return 0;
    }
    Py_ssize_t cap = buf->cap > 0 ? buf->cap : 64;
    while (cap < buf->len + extra) {
        cap *= 2;
    }
    Py_UCS4 *grown = PyMem_Realloc(buf->data, (size_t)cap * sizeof(Py_UCS4));
    if (grown == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        buf->error = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    buf->data = grown;
    buf->cap = cap;
    return 0;
}

/* Append a NUL-terminated ASCII run (the XPath operators and axis names). */
static void xtr_puts(xtr_buf *buf, const char *text) {
    Py_ssize_t length = (Py_ssize_t)strlen(text);
    if (xtr_reserve(buf, length) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;                         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        buf->data[buf->len++] = (Py_UCS4)(unsigned char)text[index];
    }
}

/* Append a decoded name/value slice, lowercasing ASCII A-Z when lower is set (tag
   and attribute names are stored lowercased in the tree, and a case-insensitive
   attribute literal is folded to compare against a translate()-lowered value). */
static void xtr_slice(xtr_buf *buf, const Py_UCS4 *text, Py_ssize_t length, int lower) {
    if (xtr_reserve(buf, length) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;                         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < length; index++) {
        Py_UCS4 ch = text[index];
        if (lower && ch >= 'A' && ch <= 'Z') {
            ch += 32;
        }
        buf->data[buf->len++] = ch;
    }
}

static void xtr_int(xtr_buf *buf, int value) {
    char digits[16];
    PyOS_snprintf(digits, sizeof(digits), "%d", value);
    xtr_puts(buf, digits);
}

/* Append an XPath string literal for a slice, lowercasing it when lower is set.
   A value free of single quotes is wrapped in them; one with single but no double
   quotes is wrapped in double quotes; one with both is split into a concat() of
   single-quoted runs joined by the "'" character, the only XPath-1.0-portable way
   to embed a single quote. */
static void xtr_literal(xtr_buf *buf, const Py_UCS4 *text, Py_ssize_t length, int lower) {
    int has_single = 0;
    int has_double = 0;
    for (Py_ssize_t index = 0; index < length; index++) {
        has_single |= text[index] == '\'';
        has_double |= text[index] == '"';
    }
    if (!has_single) {
        xtr_puts(buf, "'");
        xtr_slice(buf, text, length, lower);
        xtr_puts(buf, "'");
        return;
    }
    if (!has_double) {
        xtr_puts(buf, "\"");
        xtr_slice(buf, text, length, lower);
        xtr_puts(buf, "\"");
        return;
    }
    xtr_puts(buf, "concat(");
    Py_ssize_t start = 0;
    int first = 1;
    for (Py_ssize_t index = 0; index <= length; index++) {
        if (index == length || text[index] == '\'') {
            if (!first) {
                xtr_puts(buf, ", \"'\", ");
            }
            xtr_puts(buf, "'");
            xtr_slice(buf, text + start, index - start, lower);
            xtr_puts(buf, "'");
            first = 0;
            start = index + 1;
        }
    }
    xtr_puts(buf, ")");
}

/* The concrete element type a compound carries, or has_type 0 for the universal
   selector or no type at all; the of-type pseudo-classes need it. */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    int has_type;
} xtr_type;

static xtr_type xtr_step_type(const sel_compound *compound) {
    for (int index = 0; index < compound->count; index++) {
        if (compound->simples[index].kind == 'e') {
            return (xtr_type){compound->simples[index].name, compound->simples[index].name_len, 1};
        }
    }
    return (xtr_type){NULL, 0, 0};
}

/* Reference the attribute value for an operator comparison: the bare @name, or a
   translate() that ASCII-lowercases it when the comparison is case-insensitive (an
   explicit i flag or an HTML default-case-insensitive attribute name). */
static void xtr_attr_value_ref(xtr_buf *buf, const sel_simple *simple, int ci) {
    if (ci) {
        xtr_puts(buf, "translate(@");
        xtr_slice(buf, simple->name, simple->name_len, 1);
        xtr_puts(buf, ", 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')");
        return;
    }
    xtr_puts(buf, "@");
    xtr_slice(buf, simple->name, simple->name_len, 1);
}

/* The substring-style operators (~= *= ^= $=) carry a wanted value the css engine
   short-circuits to no match when it is empty; = and |= accept an empty value. */
static int xtr_op_needs_value(enum sel_attr_op op) {
    switch (op) {
    case OP_INCLUDE:
    case OP_SUBSTR:
    case OP_PREFIX:
    case OP_SUFFIX:
        return 1;
    default:
        return 0;
    }
}

static void xtr_attr_condition(xtr_buf *buf, const sel_simple *simple) {
    int ci = simple->ci || simple->ci_default;
    if (simple->op == OP_EXISTS) {
        xtr_puts(buf, "@");
        xtr_slice(buf, simple->name, simple->name_len, 1);
        return;
    }
    if (simple->value_len == 0 && xtr_op_needs_value(simple->op)) {
        xtr_puts(buf, "0");
        return;
    }
    if (simple->op == OP_EQ) {
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, " = ");
        xtr_literal(buf, simple->value, simple->value_len, ci);
        return;
    }
    /* every remaining operator first guards on the attribute existing */
    xtr_puts(buf, "@");
    xtr_slice(buf, simple->name, simple->name_len, 1);
    xtr_puts(buf, " and ");
    if (simple->op == OP_PREFIX) {
        xtr_puts(buf, "starts-with(");
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, ", ");
        xtr_literal(buf, simple->value, simple->value_len, ci);
        xtr_puts(buf, ")");
    } else if (simple->op == OP_SUFFIX) {
        xtr_puts(buf, "substring(");
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, ", string-length(");
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, ") - ");
        xtr_int(buf, (int)simple->value_len - 1);
        xtr_puts(buf, ") = ");
        xtr_literal(buf, simple->value, simple->value_len, ci);
    } else if (simple->op == OP_SUBSTR) {
        xtr_puts(buf, "contains(");
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, ", ");
        xtr_literal(buf, simple->value, simple->value_len, ci);
        xtr_puts(buf, ")");
    } else if (simple->op == OP_INCLUDE) {
        xtr_puts(buf, "contains(concat(' ', normalize-space(");
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, "), ' '), ");
        xtr_puts(buf, "' ");
        xtr_slice(buf, simple->value, simple->value_len, ci);
        xtr_puts(buf, " '");
        xtr_puts(buf, ")");
    } else { /* OP_DASH, the |= operator: an exact value or a "value-" prefix */
        xtr_puts(buf, "(");
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, " = ");
        xtr_literal(buf, simple->value, simple->value_len, ci);
        xtr_puts(buf, " or starts-with(");
        xtr_attr_value_ref(buf, simple, ci);
        xtr_puts(buf, ", concat(");
        xtr_literal(buf, simple->value, simple->value_len, ci);
        xtr_puts(buf, ", '-')))");
    }
}

/* Emit the An+B membership test over the 1-based position of an element among the
   siblings reached by count_axis (a star axis for the child families, a typed axis
   for the of-type families). Position is count(axis) + 1. */
static void xtr_nth(xtr_buf *buf, const char *count_open, const xtr_type *type, int has_type, int a, int b) {
    /* a small closure: append "count(<axis>::<test>)" */
#define XTR_COUNT()                                                                                                    \
    do {                                                                                                               \
        xtr_puts(buf, count_open);                                                                                     \
        if (has_type) {                                                                                                \
            xtr_slice(buf, type->name, type->name_len, 1);                                                            \
        } else {                                                                                                       \
            xtr_puts(buf, "*");                                                                                        \
        }                                                                                                              \
        xtr_puts(buf, ")");                                                                                            \
    } while (0)

    if (a == 0) {
        XTR_COUNT();
        xtr_puts(buf, " = ");
        xtr_int(buf, b - 1);
    } else if (a > 0) {
        int shift = 1 - b; /* position + shift, folded into one signed constant */
        xtr_puts(buf, "(");
        XTR_COUNT();
        if (shift > 0) {
            xtr_puts(buf, " + ");
            xtr_int(buf, shift);
        } else if (shift < 0) {
            xtr_puts(buf, " - ");
            xtr_int(buf, -shift);
        }
        xtr_puts(buf, ") >= 0 and (");
        XTR_COUNT();
        if (shift > 0) {
            xtr_puts(buf, " + ");
            xtr_int(buf, shift);
        } else if (shift < 0) {
            xtr_puts(buf, " - ");
            xtr_int(buf, -shift);
        }
        xtr_puts(buf, ") mod ");
        xtr_int(buf, a);
        xtr_puts(buf, " = 0");
    } else { /* a < 0: a finite window of leading positions */
        XTR_COUNT();
        xtr_puts(buf, " <= ");
        xtr_int(buf, b - 1);
        xtr_puts(buf, " and (");
        xtr_int(buf, b - 1);
        xtr_puts(buf, " - ");
        XTR_COUNT();
        xtr_puts(buf, ") mod ");
        xtr_int(buf, -a);
        xtr_puts(buf, " = 0");
    }
#undef XTR_COUNT
}

static int xtr_simple_condition(xtr_buf *buf, const sel_simple *simple, const xtr_type *type);

/* Emit the negation of a single :not() arm (one compound, no combinator): the
   conjunction of its simples' conditions, each through xtr_simple_condition (a type
   simple folds to self::name there, so the arm composes like any predicate). */
static int xtr_not_arm(xtr_buf *buf, const sel_compound *compound) {
    xtr_type type = xtr_step_type(compound);
    xtr_puts(buf, "not(");
    for (int index = 0; index < compound->count; index++) {
        if (index > 0) {
            xtr_puts(buf, " and ");
        }
        if (xtr_simple_condition(buf, &compound->simples[index], &type) < 0) {
            return -1;
        }
    }
    xtr_puts(buf, ")");
    return 0;
}

/* The structural pseudo-classes this subset supports, as an XPath predicate body. */
static int xtr_pseudo_condition(xtr_buf *buf, const sel_simple *simple, const xtr_type *type) {
    switch (simple->pseudo) {
    case PSEUDO_ROOT:
        xtr_puts(buf, "not(parent::*)");
        return 0;
    case PSEUDO_EMPTY:
        xtr_puts(buf, "not(*) and not(normalize-space())");
        return 0;
    case PSEUDO_FIRST_CHILD:
        xtr_puts(buf, "count(preceding-sibling::*) = 0");
        return 0;
    case PSEUDO_LAST_CHILD:
        xtr_puts(buf, "count(following-sibling::*) = 0");
        return 0;
    case PSEUDO_ONLY_CHILD:
        xtr_puts(buf, "count(preceding-sibling::*) = 0 and count(following-sibling::*) = 0");
        return 0;
    case PSEUDO_FIRST_OF_TYPE:
    case PSEUDO_LAST_OF_TYPE:
    case PSEUDO_ONLY_OF_TYPE:
        if (!type->has_type) {
            PyErr_SetString(PyExc_ValueError, "the *-of-type pseudo-class needs a concrete element type to translate");
            return -1;
        }
        if (simple->pseudo != PSEUDO_LAST_OF_TYPE) {
            xtr_puts(buf, "count(preceding-sibling::");
            xtr_slice(buf, type->name, type->name_len, 1);
            xtr_puts(buf, ") = 0");
        }
        if (simple->pseudo == PSEUDO_ONLY_OF_TYPE) {
            xtr_puts(buf, " and ");
        }
        if (simple->pseudo != PSEUDO_FIRST_OF_TYPE) {
            xtr_puts(buf, "count(following-sibling::");
            xtr_slice(buf, type->name, type->name_len, 1);
            xtr_puts(buf, ") = 0");
        }
        return 0;
    case PSEUDO_NTH_CHILD:
    case PSEUDO_NTH_LAST_CHILD:
        if (simple->sub != NULL) {
            PyErr_SetString(PyExc_ValueError, "the :nth-child(... of S) form has no XPath 1.0 translation");
            return -1;
        }
        xtr_nth(buf, simple->pseudo == PSEUDO_NTH_CHILD ? "count(preceding-sibling::" : "count(following-sibling::",
                type, 0, simple->nth_a, simple->nth_b);
        return 0;
    case PSEUDO_NTH_OF_TYPE:
    case PSEUDO_NTH_LAST_OF_TYPE:
        if (!type->has_type) {
            PyErr_SetString(PyExc_ValueError, "the *-of-type pseudo-class needs a concrete element type to translate");
            return -1;
        }
        xtr_nth(buf, simple->pseudo == PSEUDO_NTH_OF_TYPE ? "count(preceding-sibling::" : "count(following-sibling::",
                type, 1, simple->nth_a, simple->nth_b);
        return 0;
    case PSEUDO_NOT: {
        for (int arm = 0; arm < simple->sub_count; arm++) {
            if (simple->sub[arm].count != 1) {
                PyErr_SetString(PyExc_ValueError, "only a compound :not() argument (no combinator) is translatable");
                return -1;
            }
        }
        for (int arm = 0; arm < simple->sub_count; arm++) {
            if (arm > 0) {
                xtr_puts(buf, " and ");
            }
            if (xtr_not_arm(buf, &simple->sub[arm].compounds[0]) < 0) {
                return -1;
            }
        }
        return 0;
    }
    default:
        PyErr_SetString(PyExc_ValueError, "the pseudo-class has no XPath 1.0 translation");
        return -1;
    }
}

/* One simple selector as an XPath predicate body (no surrounding brackets); a type
   simple becomes a self:: test so it composes inside :not() and as an extra type. */
static int xtr_simple_condition(xtr_buf *buf, const sel_simple *simple, const xtr_type *type) {
    switch (simple->kind) {
    case 'e':
        xtr_puts(buf, "self::");
        xtr_slice(buf, simple->name, simple->name_len, 1);
        return 0;
    case '*':
        xtr_puts(buf, "self::*");
        return 0;
    case '.':
        xtr_puts(buf, "@class and contains(concat(' ', normalize-space(@class), ' '), ' ");
        xtr_slice(buf, simple->name, simple->name_len, 0);
        xtr_puts(buf, " ')");
        return 0;
    case '#':
        xtr_puts(buf, "@id = ");
        xtr_literal(buf, simple->name, simple->name_len, 0);
        return 0;
    case '[':
        xtr_attr_condition(buf, simple);
        return 0;
    default: /* ':' */
        return xtr_pseudo_condition(buf, simple, type);
    }
}

/* One compound (a node test plus bracketed predicates) reached through axis_open
   (the axis and "::" of its combinator, already emitted by the caller). */
static int xtr_step(xtr_buf *buf, const sel_compound *compound) {
    xtr_type type = xtr_step_type(compound);
    const sel_simple *node_test = NULL;
    for (int index = 0; index < compound->count; index++) {
        char kind = compound->simples[index].kind;
        if (kind == 'e' || kind == '*') {
            node_test = &compound->simples[index];
            break;
        }
    }
    if (node_test != NULL && node_test->kind == 'e') {
        xtr_slice(buf, node_test->name, node_test->name_len, 1);
    } else {
        xtr_puts(buf, "*");
    }
    for (int index = 0; index < compound->count; index++) {
        const sel_simple *simple = &compound->simples[index];
        if (simple == node_test) {
            continue;
        }
        xtr_puts(buf, "[");
        if (xtr_simple_condition(buf, simple, &type) < 0) {
            return -1;
        }
        xtr_puts(buf, "]");
    }
    return 0;
}

/* One complex selector (compounds joined by combinators), prefixed by the caller's
   axis so the first step descends from the context the way cssselect's prefix does. */
static int xtr_complex(xtr_buf *buf, const sel_complex *complex, const Py_UCS4 *prefix, Py_ssize_t prefix_len) {
    xtr_slice(buf, prefix, prefix_len, 0);
    if (xtr_step(buf, &complex->compounds[0]) < 0) {
        return -1;
    }
    for (int index = 1; index < complex->count; index++) {
        switch (complex->compounds[index].combinator) {
        case '>':
            xtr_puts(buf, "/");
            break;
        case '+':
            xtr_puts(buf, "/following-sibling::*[1]/self::");
            break;
        case '~':
            xtr_puts(buf, "/following-sibling::");
            break;
        default: /* ' ' descendant */
            xtr_puts(buf, "/descendant-or-self::*/");
            break;
        }
        if (xtr_step(buf, &complex->compounds[index]) < 0) {
            return -1;
        }
    }
    return 0;
}

/* The body of turbohtml.convert._css_to_xpath: compile the selector, then emit the
   union of each comma alternative's translated path. Returns a new str, or NULL with
   a ValueError set (an unsupported construct, or a selector syntax error). */
static PyObject *css_translate(PyObject *selector, const Py_UCS4 *prefix, Py_ssize_t prefix_len) {
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    sel_compiled *compiled = selector_compile(tree, selector);
    if (compiled == NULL) {
        th_tree_free(tree);
        return NULL;
    }
    xtr_buf buf = {NULL, 0, 0, 0};
    int failed = 0;
    for (int alt = 0; alt < compiled->count && !failed; alt++) {
        if (alt > 0) {
            xtr_puts(&buf, " | ");
        }
        failed = xtr_complex(&buf, &compiled->alts[alt], prefix, prefix_len) < 0;
    }
    selector_free(compiled);
    th_tree_free(tree);
    if (failed) {
        PyMem_Free(buf.data);
        return NULL; /* xtr_complex set the ValueError */
    }
    if (buf.error) {             /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(buf.data);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, buf.data, buf.len);
    PyMem_Free(buf.data);
    return result;
}

/* The METH_VARARGS binding: _css_to_xpath(selector, prefix). */
PyObject *turbohtml_css_to_xpath(PyObject *module, PyObject *args) {
    (void)module;
    PyObject *selector;
    PyObject *prefix;
    if (!PyArg_ParseTuple(args, "UU", &selector, &prefix)) {
        return NULL;
    }
    Py_ssize_t prefix_len = PyUnicode_GET_LENGTH(prefix);
    Py_UCS4 *prefix_points = PyUnicode_AsUCS4Copy(prefix);
    if (prefix_points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = css_translate(selector, prefix_points, prefix_len);
    PyMem_Free(prefix_points);
    return result;
}

#endif /* TURBOHTML_CSS_TRANSLATE_H */
