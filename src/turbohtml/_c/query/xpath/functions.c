/* The XPath 1.0 core function library plus the EXSLT regular-expression extensions
   parsel and scrapy lean on, and the Python hook the parser conformance tests call. */

#include "core/common.h"
#include "dom/tree.h"
#include "query/xpath/internal.h"
#include "query/xpath/xpath.h"

#include <math.h>
#include <string.h>

/* XPath 1.0 round(): the integer closest to the argument, ties resolved toward
   positive infinity (§4.4, so round(-2.5) is -2, not the -3 that C round() gives
   by rounding half away from zero). NaN and the infinities pass through floor. */
static double xp_round(double value) {
    return floor(value + 0.5);
}

/* number() with no argument: the context node's string-value parsed as first number. */
static double context_node_number(xp_ctx *ctx) {
    xp_item item = {ctx->node, -1};
    Py_ssize_t length;
    Py_UCS4 *text = item_string(ctx->tree, item, &length);
    if (text == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return (double)NAN; /* GCOVR_EXCL_LINE */
    }
    double value = parse_number(text, length);
    PyMem_Free(text);
    return value;
}

static int func_is(const xn *fn, const char *kw) {
    Py_ssize_t index = 0;
    for (; kw[index] != '\0'; index++) {
        if (index >= fn->str_len || fn->str[index] != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return index == fn->str_len;
}

static Py_ssize_t ucs4_find_kmp(const Py_UCS4 *hay, Py_ssize_t hlen, const Py_UCS4 *needle, Py_ssize_t nlen,
                                Py_ssize_t offset) {
    Py_ssize_t *prefix = PyMem_Malloc((size_t)nlen * sizeof(*prefix));
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -2;        /* GCOVR_EXCL_LINE */
    }
    prefix[0] = 0;
    for (Py_ssize_t index = 1, matched = 0; index < nlen; index++) {
        while (matched > 0 && needle[index] != needle[matched]) {
            matched = prefix[matched - 1];
        }
        if (needle[index] == needle[matched]) {
            matched++;
        }
        prefix[index] = matched;
    }
    Py_ssize_t matched = 0;
    for (Py_ssize_t index = offset; index < hlen; index++) {
        while (matched > 0 && hay[index] != needle[matched]) {
            matched = prefix[matched - 1];
        }
        if (hay[index] == needle[matched]) {
            matched++;
            if (matched == nlen) {
                PyMem_Free(prefix);
                return index - nlen + 1;
            }
        }
    }
    PyMem_Free(prefix);
    return -1;
}

static Py_ssize_t ucs4_find(const Py_UCS4 *hay, Py_ssize_t hlen, const Py_UCS4 *needle, Py_ssize_t nlen) {
    if (nlen == 0) {
        return 0;
    }
    if (nlen > hlen) {
        return -1;
    }
    if (memcmp(hay, needle, (size_t)nlen * sizeof(Py_UCS4)) == 0) {
        return 0;
    }
    size_t candidates = nlen > 64 && hlen >= 256 ? 64 : SIZE_MAX;
    Py_ssize_t last = nlen - 1;
    for (Py_ssize_t index = 1; index + nlen <= hlen; index++) {
        if (hay[index + last] != needle[last]) {
            continue;
        }
        if (memcmp(hay + index, needle, (size_t)nlen * sizeof(Py_UCS4)) == 0) {
            return index;
        }
        if (candidates != SIZE_MAX) {
            candidates--;
            if (candidates == 0) {
                Py_ssize_t found = ucs4_find_kmp(hay, hlen, needle, nlen, index + 1);
                if (found != -2) { /* GCOVR_EXCL_BR_LINE: -2 only after unforceable allocation failure */
                    return found;
                }
                candidates = SIZE_MAX; /* GCOVR_EXCL_LINE: retain the allocation-free scan */
            } /* GCOVR_EXCL_LINE: brace of the allocation-failure fallback */
        }
    }
    return -1;
}

/* The string-value to operate on: the first argument's, or the context node's when
   the function was called with no arguments. */
static Py_UCS4 *arg_or_context_string(xp_ctx *ctx, xp_result *args, int argc, Py_ssize_t *len) {
    if (argc >= 1) {
        return to_string(ctx->tree, &args[0], len);
    }
    xp_item item = {ctx->node, -1};
    return item_string(ctx->tree, item, len);
}

/* The qualified name of a node-set's first node (or the context node), as a fresh
   string; empty for a non-named node or an empty node-set. */
static Py_UCS4 *node_name_string(xp_ctx *ctx, xp_result *args, int argc, Py_ssize_t *len) {
    struct th_node *node = ctx->node;
    Py_ssize_t attr = -1;
    if (argc >= 1) {
        if (args[0].kind != XP_NODESET || args[0].nodes.len == 0) {
            *len = 0;
            return ucs4_dup(NULL, 0);
        }
        node = args[0].nodes.items[0].node;
        attr = args[0].nodes.items[0].attr;
    }
    if (attr == -2) {
        return ucs4_from_ascii(XP_XML_NS_PREFIX, sizeof(XP_XML_NS_PREFIX) - 1, len);
    }
    if (attr >= 0) {
        Py_ssize_t blen;
        const char *bytes = th_attr_name(ctx->tree, node->attrs[attr].name_atom, &blen);
        Py_UCS4 *buffer = ucs4_from_ascii(bytes, blen, len);
        if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        return buffer;
    }
    if (node->type == TH_NODE_ELEMENT) {
        *len = node->text_len;
        return ucs4_dup(node->text, node->text_len);
    }
    *len = 0;
    return ucs4_dup(NULL, 0);
}

/* normalize-space: trim ends, collapse internal whitespace runs to one space. */
static int normalize_space(const Py_UCS4 *text, Py_ssize_t len, xp_result *out) {
    Py_UCS4 *buf = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t write_pos = 0;
    int in_space = 1; /* leading whitespace is dropped */
    for (Py_ssize_t index = 0; index < len; index++) {
        if (xp_is_space(text[index])) {
            in_space = 1;
        } else {
            if (in_space && write_pos > 0) {
                buf[write_pos++] = ' ';
            }
            buf[write_pos++] = text[index];
            in_space = 0;
        }
    }
    result_string(out, buf, write_pos);
    return 0;
}

static int translate(const Py_UCS4 *text, Py_ssize_t slen, const Py_UCS4 *from, Py_ssize_t flen, const Py_UCS4 *to,
                     Py_ssize_t tlen, xp_result *out) {
    Py_UCS4 *buf = PyMem_Malloc((size_t)slen * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t write_pos = 0;
    for (Py_ssize_t index = 0; index < slen; index++) {
        Py_ssize_t from_index = 0;
        while (from_index < flen && from[from_index] != text[index]) {
            from_index++;
        }
        if (from_index >= flen) {
            buf[write_pos++] = text[index];
        } else if (from_index < tlen) {
            buf[write_pos++] = to[from_index];
        }
        /* else: in `from` but past the end of `to`, so the character is removed */
    }
    result_string(out, buf, write_pos);
    return 0;
}

static int substring(struct th_tree *tree, xp_result *args, int argc, xp_result *out) {
    Py_ssize_t slen;
    Py_UCS4 *text = to_string(tree, &args[0], &slen);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    double start = xp_round(to_number(tree, &args[1]));
    double last = argc >= 3 ? start + xp_round(to_number(tree, &args[2])) : (double)slen + 1;
    Py_ssize_t lo = start < 1 ? 0 : (start > (double)slen + 1 ? slen : (Py_ssize_t)start - 1);
    Py_ssize_t hi = last < 1 ? 0 : (last > (double)slen + 1 ? slen : (Py_ssize_t)last - 1);
    if (hi < lo) {
        hi = lo;
    }
    Py_UCS4 *result_text = ucs4_dup(text + lo, hi - lo);
    PyMem_Free(text);
    if (result_text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;             /* GCOVR_EXCL_LINE */
    }
    result_string(out, result_text, hi - lo);
    return 0;
}

/* concat(s, s, s*): the string-values of every argument joined. Variadic (two or more
   arguments), so the per-argument strings are held in a heap array sized to the call. */
static int concat(struct th_tree *tree, xp_result *args, int argc, xp_result *out) {
    Py_UCS4 **parts = PyMem_Calloc((size_t)argc, sizeof(Py_UCS4 *));
    Py_ssize_t *lens = PyMem_Calloc((size_t)argc, sizeof(Py_ssize_t));
    if (parts == NULL || lens == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(parts);               /* GCOVR_EXCL_LINE */
        PyMem_Free(lens);                /* GCOVR_EXCL_LINE */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    int rc = 0;
    Py_ssize_t total = 0;
    for (int index = 0; index < argc; index++) {
        parts[index] = to_string(tree, &args[index], &lens[index]);
        if (parts[index] == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        total += lens[index];
    }
    if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        Py_UCS4 *buf = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;       /* GCOVR_EXCL_LINE */
        } else {           /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            Py_ssize_t write_pos = 0;
            for (int index = 0; index < argc; index++) {
                memcpy(buf + write_pos, parts[index], (size_t)lens[index] * sizeof(Py_UCS4));
                write_pos += lens[index];
            }
            result_string(out, buf, total);
        }
    }
    for (int index = 0; index < argc; index++) {
        PyMem_Free(parts[index]);
    }
    PyMem_Free(parts);
    PyMem_Free(lens);
    return rc;
}

/* namespace-uri(): empty for HTML elements, attributes, namespace nodes, and
   non-elements; the foreign-content URI for an SVG or MathML element. */
static Py_UCS4 *node_namespace_uri(xp_ctx *ctx, xp_result *args, int argc, Py_ssize_t *len) {
    struct th_node *node = ctx->node;
    Py_ssize_t attr = -1;
    if (argc >= 1) {
        if (args[0].kind != XP_NODESET || args[0].nodes.len == 0) {
            *len = 0;
            return ucs4_dup(NULL, 0);
        }
        node = args[0].nodes.items[0].node;
        attr = args[0].nodes.items[0].attr;
    }
    const char *uri = "";
    Py_ssize_t uri_len = 0;
    if (attr == -1 && node->type == TH_NODE_ELEMENT) {
        if (node->ns == TH_NS_SVG) {
            uri = XP_SVG_NS_URI;
            uri_len = sizeof(XP_SVG_NS_URI) - 1;
        } else if (node->ns == TH_NS_MATHML) {
            uri = XP_MATHML_NS_URI;
            uri_len = sizeof(XP_MATHML_NS_URI) - 1;
        }
    }
    return ucs4_from_ascii(uri, uri_len, len);
}

/* RFC 4647 prefix match, ASCII case-insensitive: the tag equals the wanted code
   or extends it with a "-subtag" suffix (so "en" matches "en" and "en-US"). */
static int lang_tag_matches(const Py_UCS4 *tag, Py_ssize_t tag_len, const Py_UCS4 *want, Py_ssize_t want_len) {
    if (tag_len < want_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < want_len; index++) {
        Py_UCS4 in_tag = tag[index] >= 'A' && tag[index] <= 'Z' ? tag[index] + 32 : tag[index];
        Py_UCS4 in_want = want[index] >= 'A' && want[index] <= 'Z' ? want[index] + 32 : want[index];
        if (in_tag != in_want) {
            return 0;
        }
    }
    return tag_len == want_len || tag[want_len] == '-';
}

/* lang(): true when the nearest self-or-ancestor element carrying a lang
   attribute names a language the wanted code is a prefix of. */
static int node_lang(xp_ctx *ctx, xp_result *arg) {
    Py_ssize_t want_len;
    Py_UCS4 *want = to_string(ctx->tree, arg, &want_len);
    if (want == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    int result = 0;
    for (struct th_node *node = ctx->node; node != NULL; node = node->parent) {
        th_node_attr *attrs;
        Py_ssize_t attr_count = th_node_attributes(node, &attrs);
        const th_node_attr *lang_attr = NULL;
        for (Py_ssize_t index = 0; index < attr_count; index++) {
            if (attrs[index].name_atom == TH_ATTR_LANG) {
                lang_attr = &attrs[index];
                break;
            }
        }
        if (lang_attr != NULL) {
            result = lang_tag_matches(lang_attr->value, lang_attr->value_len, want, want_len);
            break;
        }
    }
    PyMem_Free(want);
    return result;
}

/* True when `token` appears as a whitespace-delimited entry in `list`. */
static int token_in_list(const Py_UCS4 *list, Py_ssize_t list_len, const Py_UCS4 *token, Py_ssize_t token_len) {
    if (token_len == 0) {
        return 0;
    }
    Py_ssize_t index = 0;
    while (index < list_len) {
        while (index < list_len && xp_is_space(list[index])) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < list_len && !xp_is_space(list[index])) {
            index++;
        }
        if (index - start == token_len && memcmp(list + start, token, (size_t)token_len * sizeof(Py_UCS4)) == 0) {
            return 1;
        }
    }
    return 0;
}

/* id(object): the elements whose id attribute is one of the whitespace-separated
   tokens in the argument's string-value (a node-set argument contributes the
   string-value of each member). */
static int eval_id(xp_ctx *ctx, xp_result *arg, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    Py_UCS4 *list = NULL;
    Py_ssize_t list_len = 0;
    if (arg->kind == XP_NODESET) {
        for (Py_ssize_t index = 0; index < arg->nodes.len; index++) {
            Py_ssize_t each;
            Py_UCS4 *text = item_string(ctx->tree, arg->nodes.items[index], &each);
            if (text == NULL) {   /* GCOVR_EXCL_BR_LINE: alloc */
                PyMem_Free(list); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            Py_UCS4 *grown = PyMem_Realloc(list, (size_t)(list_len + each + 1) * sizeof(Py_UCS4));
            if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: alloc */
                PyMem_Free(text); /* GCOVR_EXCL_LINE */
                PyMem_Free(list); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            list = grown;
            list[list_len++] = ' ';
            memcpy(list + list_len, text, (size_t)each * sizeof(Py_UCS4));
            list_len += each;
            PyMem_Free(text);
        }
    } else {
        list = to_string(ctx->tree, arg, &list_len);
        if (list == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;      /* GCOVR_EXCL_LINE */
        }
    }
    int rc = 0;
    for (struct th_node *node = th_tree_document(ctx->tree); node != NULL; node = document_next(node)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            if (node->attrs[index].name_atom == TH_ATTR_ID &&
                token_in_list(list, list_len, node->attrs[index].value, node->attrs[index].value_len)) {
                if (ns_push(&out->nodes, node, -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;                              /* GCOVR_EXCL_LINE */
                } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
                break;
            }
        }
    }
    PyMem_Free(list);
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* The EXSLT regular-expression functions (re:test / re:replace). */
/* parsel and scrapy lean on these, so the engine borrows Python's re module.
   Evaluation runs inside the tree's critical section, but re touches no turbohtml
   handle, so the call cannot deadlock against it. */

/* Build a Python pattern string, folding the EXSLT flag letters into an inline
   "(?imsx)" group and reporting a 'g' (global) flag through *global. NULL with an
   exception set on failure. */
static PyObject *exslt_pattern(struct th_tree *tree, xp_result *pattern_arg, xp_result *flags_arg, int *global) {
    *global = 0;
    Py_ssize_t pat_len;
    Py_UCS4 *pattern = to_string(tree, pattern_arg, &pat_len);
    if (pattern == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    Py_UCS4 inline_flags[4];
    Py_ssize_t flag_count = 0;
    if (flags_arg != NULL) {
        Py_ssize_t flag_len;
        Py_UCS4 *flags = to_string(tree, flags_arg, &flag_len);
        if (flags == NULL) {     /* GCOVR_EXCL_BR_LINE: alloc */
            PyMem_Free(pattern); /* GCOVR_EXCL_LINE */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < flag_len; index++) {
            Py_UCS4 letter = flags[index];
            if (letter == 'g') {
                *global = 1;
            } else if (letter == 'i' || letter == 'm' || letter == 's' || letter == 'x') {
                inline_flags[flag_count++] = letter;
            }
        }
        PyMem_Free(flags);
    }
    Py_ssize_t prefix_len = flag_count > 0 ? flag_count + 3 : 0; /* "(?" flags ")" */
    Py_UCS4 *buf = PyMem_Malloc((size_t)(prefix_len + pat_len) * sizeof(Py_UCS4));
    if (buf == NULL) {       /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(pattern); /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t write = 0;
    if (flag_count > 0) {
        buf[write++] = '(';
        buf[write++] = '?';
        for (Py_ssize_t index = 0; index < flag_count; index++) {
            buf[write++] = inline_flags[index];
        }
        buf[write++] = ')';
    }
    memcpy(buf + write, pattern, (size_t)pat_len * sizeof(Py_UCS4));
    PyMem_Free(pattern);
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, buf, prefix_len + pat_len);
    PyMem_Free(buf);
    return result;
}

/* re:test(input, regex, flags?): true when the regex matches anywhere in input. */
static int exslt_re_test(struct th_tree *tree, xp_result *args, int argc, xp_result *out) {
    int global;
    PyObject *pattern = exslt_pattern(tree, &args[1], argc >= 3 ? &args[2] : NULL, &global);
    Py_ssize_t input_len;
    Py_UCS4 *input_text = to_string(tree, &args[0], &input_len);
    if (pattern == NULL || input_text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        Py_XDECREF(pattern);                     /* GCOVR_EXCL_LINE */
        PyMem_Free(input_text);                  /* GCOVR_EXCL_LINE */
        return -1;                               /* GCOVR_EXCL_LINE */
    }
    PyObject *input = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, input_text, input_len);
    PyMem_Free(input_text);
    PyObject *re_module = PyImport_ImportModule("re");
    if (input == NULL || re_module == NULL) { /* GCOVR_EXCL_BR_LINE: a UCS-4 alloc or re import cannot be forced */
        Py_XDECREF(input);                    /* GCOVR_EXCL_LINE */
        Py_XDECREF(re_module);                /* GCOVR_EXCL_LINE */
        Py_DECREF(pattern);                   /* GCOVR_EXCL_LINE */
        return -1;                            /* GCOVR_EXCL_LINE */
    }
    PyObject *match = PyObject_CallMethod(re_module, "search", "OO", pattern, input);
    Py_DECREF(re_module);
    Py_DECREF(input);
    Py_DECREF(pattern);
    if (match == NULL) {
        return -1; /* a malformed pattern set re.error */
    }
    result_bool(out, match != Py_None);
    Py_DECREF(match);
    return 0;
}

/* re:replace(input, regex, flags, replacement): substitute matches, all of them
   under a 'g' flag, otherwise the first. */
static int exslt_re_replace(struct th_tree *tree, xp_result *args, xp_result *out) {
    int global;
    PyObject *pattern = exslt_pattern(tree, &args[1], &args[2], &global);
    Py_ssize_t input_len;
    Py_ssize_t repl_len;
    Py_UCS4 *input_text = to_string(tree, &args[0], &input_len);
    Py_UCS4 *repl_text = to_string(tree, &args[3], &repl_len);
    PyObject *input = NULL;
    PyObject *repl = NULL;
    PyObject *re_module = NULL;
    if (pattern != NULL && input_text != NULL && repl_text != NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        input = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, input_text, input_len);
        repl = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, repl_text, repl_len);
        re_module = PyImport_ImportModule("re");
    }
    PyMem_Free(input_text);
    PyMem_Free(repl_text);
    if (pattern == NULL || input == NULL || repl == NULL || re_module == NULL) { /* GCOVR_EXCL_BR_LINE: alloc/import */
        Py_XDECREF(pattern);                                                     /* GCOVR_EXCL_LINE */
        Py_XDECREF(input);                                                       /* GCOVR_EXCL_LINE */
        Py_XDECREF(repl);                                                        /* GCOVR_EXCL_LINE */
        Py_XDECREF(re_module);                                                   /* GCOVR_EXCL_LINE */
        return -1;                                                               /* GCOVR_EXCL_LINE */
    }
    /* count is keyword-only since 3.13; 0 replaces every match, 1 only the first */
    PyObject *call_args = Py_BuildValue("(OOO)", pattern, repl, input);
    PyObject *call_kwargs = Py_BuildValue("{s:i}", "count", global ? 0 : 1);
    PyObject *re_sub = PyObject_GetAttrString(re_module, "sub");
    Py_DECREF(re_module);
    Py_DECREF(input);
    Py_DECREF(repl);
    Py_DECREF(pattern);
    PyObject *replaced = NULL;
    if (call_args != NULL && call_kwargs != NULL && re_sub != NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        replaced = PyObject_Call(re_sub, call_args, call_kwargs);
    }
    Py_XDECREF(call_args);
    Py_XDECREF(call_kwargs);
    Py_XDECREF(re_sub);
    if (replaced == NULL) {
        return -1; /* a malformed pattern set re.error */
    }
    Py_ssize_t result_len = PyUnicode_GET_LENGTH(replaced);
    Py_UCS4 *buf = PyUnicode_AsUCS4Copy(replaced);
    Py_DECREF(replaced);
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    result_string(out, buf, result_len);
    return 0;
}

/* The XPath 2.0 string convenience functions (fn:ends-with, fn:string-join,
   fn:lower-case, fn:upper-case, fn:matches, fn:replace) ported lxml/elementpath and
   antchfx/htmlquery expressions reach for. matches reuses the EXSLT re:test pipeline;
   replace maps to a global re.sub after rewriting its replacement string. */

/* lower-case / upper-case: Unicode case mapping (W3C fn:lower-case / fn:upper-case),
   delegated to CPython's str.lower()/str.upper() so accented and non-Latin letters map
   correctly rather than ASCII-only -- the same call-into-CPython path re:test takes. */
static int case_convert(struct th_tree *tree, xp_result *arg, int to_upper, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, arg, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    PyObject *str = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, text, len);
    PyMem_Free(text);
    if (str == NULL) { /* GCOVR_EXCL_BR_LINE: a UCS-4 alloc cannot be forced */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    PyObject *mapped = PyObject_CallMethod(str, to_upper ? "upper" : "lower", NULL);
    Py_DECREF(str);
    if (mapped == NULL) { /* GCOVR_EXCL_BR_LINE: str.upper/str.lower cannot fail here */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t result_len = PyUnicode_GET_LENGTH(mapped);
    Py_UCS4 *buf = PyUnicode_AsUCS4Copy(mapped);
    Py_DECREF(mapped);
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    result_string(out, buf, result_len);
    return 0;
}

/* string-join(seq, sep): the string-values of the node-set members joined by the
   separator -- the XPath-1.0-engine reading of fn:string-join, where a node-set is the
   sequence (antchfx does the same). A non-node-set first argument is the lone item, so
   its string-value passes through unseparated. */
static int string_join(struct th_tree *tree, xp_result *args, xp_result *out) {
    Py_ssize_t sep_len;
    Py_UCS4 *sep = to_string(tree, &args[1], &sep_len);
    if (sep == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    int is_set = args[0].kind == XP_NODESET;
    Py_ssize_t count = is_set ? args[0].nodes.len : 1;
    Py_UCS4 **parts = PyMem_Calloc((size_t)count, sizeof(Py_UCS4 *));
    Py_ssize_t *lens = PyMem_Calloc((size_t)count, sizeof(Py_ssize_t));
    if (parts == NULL || lens == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(parts);               /* GCOVR_EXCL_LINE */
        PyMem_Free(lens);                /* GCOVR_EXCL_LINE */
        PyMem_Free(sep);                 /* GCOVR_EXCL_LINE */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    int rc = 0;
    Py_ssize_t total = count > 0 ? sep_len * (count - 1) : 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        parts[index] = is_set ? item_string(tree, args[0].nodes.items[index], &lens[index])
                              : to_string(tree, &args[0], &lens[index]);
        if (parts[index] == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        total += lens[index];
    }
    if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        Py_UCS4 *buf = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;       /* GCOVR_EXCL_LINE */
        } else {           /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            Py_ssize_t write_pos = 0;
            for (Py_ssize_t index = 0; index < count; index++) {
                if (index > 0) {
                    memcpy(buf + write_pos, sep, (size_t)sep_len * sizeof(Py_UCS4));
                    write_pos += sep_len;
                }
                memcpy(buf + write_pos, parts[index], (size_t)lens[index] * sizeof(Py_UCS4));
                write_pos += lens[index];
            }
            result_string(out, buf, total);
        }
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyMem_Free(parts[index]);
    }
    PyMem_Free(parts);
    PyMem_Free(lens);
    PyMem_Free(sep);
    return rc;
}

/* Rewrite an fn:replace replacement string into a Python re-module template: a ``$N``
   group reference becomes ``\g<N>``, ``\$`` and ``\\`` collapse to a literal ``$`` and
   ``\``, and any other backslash is doubled so re reads it literally. NULL with an
   exception set on failure. */
static PyObject *fn_replacement_template(struct th_tree *tree, xp_result *repl_arg) {
    Py_ssize_t len;
    Py_UCS4 *repl = to_string(tree, repl_arg, &len);
    if (repl == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)(len * 4 + 1) * sizeof(Py_UCS4));
    if (buf == NULL) {    /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(repl); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t write = 0;
    Py_ssize_t index = 0;
    while (index < len) {
        Py_UCS4 ch = repl[index];
        if (ch == '\\' && index + 1 < len && (repl[index + 1] == '\\' || repl[index + 1] == '$')) {
            if (repl[index + 1] == '\\') {
                buf[write++] = '\\';
            }
            buf[write++] = repl[index + 1];
            index += 2;
        } else if (ch == '$' && index + 1 < len && repl[index + 1] >= '0' && repl[index + 1] <= '9') {
            buf[write++] = '\\';
            buf[write++] = 'g';
            buf[write++] = '<';
            index++;
            while (index < len && repl[index] >= '0' && repl[index] <= '9') {
                buf[write++] = repl[index++];
            }
            buf[write++] = '>';
        } else if (ch == '\\') {
            buf[write++] = '\\';
            buf[write++] = '\\';
            index++;
        } else {
            buf[write++] = ch;
            index++;
        }
    }
    PyMem_Free(repl);
    PyObject *template = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, buf, write);
    PyMem_Free(buf);
    return template;
}

/* replace(input, pattern, repl, flags?): every non-overlapping match of the pattern
   rewritten (fn:replace always replaces all), sharing the EXSLT flag handling and
   Python re backend. */
static int fn_replace(struct th_tree *tree, xp_result *args, int argc, xp_result *out) {
    int global;
    PyObject *pattern = exslt_pattern(tree, &args[1], argc >= 4 ? &args[3] : NULL, &global);
    PyObject *repl = fn_replacement_template(tree, &args[2]);
    Py_ssize_t input_len;
    Py_UCS4 *input_text = to_string(tree, &args[0], &input_len);
    PyObject *input = NULL;
    PyObject *re_module = NULL;
    if (pattern != NULL && repl != NULL && input_text != NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        input = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, input_text, input_len);
        re_module = PyImport_ImportModule("re");
    }
    PyMem_Free(input_text);
    if (pattern == NULL || repl == NULL || input == NULL || re_module == NULL) { /* GCOVR_EXCL_BR_LINE: alloc/import */
        Py_XDECREF(pattern);                                                     /* GCOVR_EXCL_LINE */
        Py_XDECREF(repl);                                                        /* GCOVR_EXCL_LINE */
        Py_XDECREF(input);                                                       /* GCOVR_EXCL_LINE */
        Py_XDECREF(re_module);                                                   /* GCOVR_EXCL_LINE */
        return -1;                                                               /* GCOVR_EXCL_LINE */
    }
    PyObject *replaced = PyObject_CallMethod(re_module, "sub", "OOO", pattern, repl, input);
    Py_DECREF(re_module);
    Py_DECREF(input);
    Py_DECREF(repl);
    Py_DECREF(pattern);
    if (replaced == NULL) {
        return -1; /* a malformed pattern set re.error */
    }
    Py_ssize_t result_len = PyUnicode_GET_LENGTH(replaced);
    Py_UCS4 *buf = PyUnicode_AsUCS4Copy(replaced);
    Py_DECREF(replaced);
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    result_string(out, buf, result_len);
    return 0;
}

/* The EXSLT set functions (set:). */
/* The node-set arguments arrive in document order and duplicate-free (every
   node-set the engine builds is sorted_unique), so the results below preserve that
   order by copying in place and never need a re-sort. */

/* Whether the same node-set member -- the identical node pointer and attribute
   index -- appears anywhere in `other`. */
static int item_in_nodeset(const xp_nodeset *other, xp_item probe) {
    for (Py_ssize_t index = 0; index < other->len; index++) {
        if (other->items[index].node == probe.node && other->items[index].attr == probe.attr) {
            return 1;
        }
    }
    return 0;
}

/* set:difference / set:intersection: members of the first node-set kept when their
   presence in the second equals `want_present` (0 for difference, 1 for
   intersection). */
static int set_filter(const xp_result *args, int want_present, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *first = &args[0].nodes;
    const xp_nodeset *second = &args[1].nodes;
    int rc = 0;
    for (Py_ssize_t index = 0; index < first->len; index++) {
        xp_item member = first->items[index];
        if (item_in_nodeset(second, member) == want_present) {
            if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                rc = -1;                                              /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        }
    }
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* set:has-same-node: true when any member of the first node-set is also a member of
   the second. */
static void set_has_same_node(const xp_result *args, xp_result *out) {
    const xp_nodeset *first = &args[0].nodes;
    const xp_nodeset *second = &args[1].nodes;
    for (Py_ssize_t index = 0; index < first->len; index++) {
        if (item_in_nodeset(second, first->items[index])) {
            result_bool(out, 1);
            return;
        }
    }
    result_bool(out, 0);
}

/* set:distinct: the first member, in document order, of every distinct string-value. */
static int set_distinct(struct th_tree *tree, const xp_result *arg, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *nodes = &arg->nodes;
    int rc = 0;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t candidate_len;
        Py_UCS4 *candidate = item_string(tree, nodes->items[index], &candidate_len);
        if (candidate == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;             /* GCOVR_EXCL_LINE */
            break;               /* GCOVR_EXCL_LINE */
        }
        int duplicate = 0;
        for (Py_ssize_t earlier = 0; earlier < index; earlier++) {
            Py_ssize_t earlier_len;
            Py_UCS4 *earlier_text = item_string(tree, nodes->items[earlier], &earlier_len);
            if (earlier_text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                rc = -1;                /* GCOVR_EXCL_LINE */
                break;                  /* GCOVR_EXCL_LINE */
            }
            if (earlier_len == candidate_len &&
                memcmp(earlier_text, candidate, (size_t)candidate_len * sizeof(Py_UCS4)) == 0) {
                duplicate = 1;
            }
            PyMem_Free(earlier_text);
            if (duplicate) {
                break;
            }
        }
        PyMem_Free(candidate);
        if (rc < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            break;    /* GCOVR_EXCL_LINE */
        }
        if (!duplicate) {
            xp_item member = nodes->items[index];
            if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                rc = -1;                                              /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        }
    }
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* set:leading / set:trailing: the members of the first node-set that fall before
   (`want_before`) or after the first node of the second in document order. An empty
   second node-set yields the first unchanged; a pivot absent from the first yields
   nothing -- both per the EXSLT contract. */
static int set_split(const xp_result *args, int want_before, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *first = &args[0].nodes;
    const xp_nodeset *second = &args[1].nodes;
    Py_ssize_t lo = 0;
    Py_ssize_t hi = 0;
    if (second->len == 0) {
        lo = 0;
        hi = first->len; /* the whole first node-set */
    } else {
        Py_ssize_t pivot = -1;
        for (Py_ssize_t index = 0; index < first->len; index++) {
            if (first->items[index].node == second->items[0].node &&
                first->items[index].attr == second->items[0].attr) {
                pivot = index;
                break;
            }
        }
        if (pivot < 0) {
            return 0; /* the pivot is not in the first node-set */
        }
        lo = want_before ? 0 : pivot + 1;
        hi = want_before ? pivot : first->len;
    }
    int rc = 0;
    for (Py_ssize_t index = lo; index < hi; index++) {
        xp_item member = first->items[index];
        if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                                              /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    }
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* The EXSLT string functions (str:). */

/* str:concat(node-set): the string-values of every member joined in document order. */
static int str_concat(struct th_tree *tree, const xp_result *arg, xp_result *out) {
    const xp_nodeset *nodes = &arg->nodes;
    Py_UCS4 *buf = NULL;
    Py_ssize_t total = 0;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t part_len;
        Py_UCS4 *part = item_string(tree, nodes->items[index], &part_len);
        if (part == NULL) {  /* GCOVR_EXCL_BR_LINE: alloc */
            PyMem_Free(buf); /* GCOVR_EXCL_LINE */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        Py_UCS4 *grown = PyMem_Realloc(buf, (size_t)(total + part_len) * sizeof(Py_UCS4));
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: alloc */
            PyMem_Free(part); /* GCOVR_EXCL_LINE */
            PyMem_Free(buf);  /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        buf = grown;
        memcpy(buf + total, part, (size_t)part_len * sizeof(Py_UCS4));
        total += part_len;
        PyMem_Free(part);
    }
    if (buf == NULL) { /* an empty node-set, or only empty string-values */
        buf = ucs4_dup(NULL, 0);
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;     /* GCOVR_EXCL_LINE */
        }
    }
    result_string(out, buf, total);
    return 0;
}

/* str:replace(string, search, replace): every non-overlapping literal occurrence of
   `search` in `string` swapped for `replace`. An empty `search` leaves the input
   untouched (no zero-width match loop). */
static int str_replace(struct th_tree *tree, const xp_result *args, xp_result *out) {
    Py_ssize_t src_len;
    Py_ssize_t search_len;
    Py_ssize_t repl_len;
    Py_UCS4 *src = to_string(tree, &args[0], &src_len);
    Py_UCS4 *search = to_string(tree, &args[1], &search_len);
    Py_UCS4 *repl = to_string(tree, &args[2], &repl_len);
    if (src == NULL || search == NULL || repl == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(src);                                 /* GCOVR_EXCL_LINE */
        PyMem_Free(search);                              /* GCOVR_EXCL_LINE */
        PyMem_Free(repl);                                /* GCOVR_EXCL_LINE */
        return -1;                                       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = 0;
    Py_ssize_t scan = 0;
    while (search_len > 0 && scan + search_len <= src_len) {
        if (memcmp(src + scan, search, (size_t)search_len * sizeof(Py_UCS4)) == 0) {
            count++;
            scan += search_len;
        } else {
            scan++;
        }
    }
    Py_ssize_t out_len = src_len + count * (repl_len - search_len);
    Py_UCS4 *buf = PyMem_Malloc((size_t)out_len * sizeof(Py_UCS4));
    if (buf == NULL) {      /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(src);    /* GCOVR_EXCL_LINE */
        PyMem_Free(search); /* GCOVR_EXCL_LINE */
        PyMem_Free(repl);   /* GCOVR_EXCL_LINE */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t read = 0;
    Py_ssize_t write = 0;
    while (read < src_len) {
        if (search_len > 0 && read + search_len <= src_len &&
            memcmp(src + read, search, (size_t)search_len * sizeof(Py_UCS4)) == 0) {
            memcpy(buf + write, repl, (size_t)repl_len * sizeof(Py_UCS4));
            write += repl_len;
            read += search_len;
        } else {
            buf[write++] = src[read++];
        }
    }
    PyMem_Free(src);
    PyMem_Free(search);
    PyMem_Free(repl);
    result_string(out, buf, write);
    return 0;
}

/* str:padding(length, pattern?): a string of `length` characters built by cycling
   `pattern` (a single space by default). An empty pattern pads with spaces. */
static int str_padding(struct th_tree *tree, const xp_result *args, int argc, xp_result *out) {
    double requested = round(to_number(tree, &args[0]));
    Py_ssize_t target = requested >= 1 ? (Py_ssize_t)requested : 0;
    const Py_UCS4 space = ' ';
    const Py_UCS4 *pattern = &space;
    Py_ssize_t pattern_len = 1;
    Py_UCS4 *pattern_buf = NULL;
    if (argc >= 2) {
        pattern_buf = to_string(tree, &args[1], &pattern_len);
        if (pattern_buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;             /* GCOVR_EXCL_LINE */
        }
        pattern = pattern_buf;
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)target * sizeof(Py_UCS4));
    if (buf == NULL) {           /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(pattern_buf); /* GCOVR_EXCL_LINE */
        return -1;               /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < target; index++) {
        buf[index] = pattern_len > 0 ? pattern[index % pattern_len] : space;
    }
    PyMem_Free(pattern_buf);
    result_string(out, buf, target);
    return 0;
}

/* Whether a UCS-4 run equals an ASCII keyword. */
static int ucs4_eq_ascii(const Py_UCS4 *text, Py_ssize_t len, const char *kw) {
    Py_ssize_t index = 0;
    for (; kw[index] != '\0'; index++) {
        if (index >= len || text[index] != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return index == len;
}

/* str:align(string, width, alignment?): `string` placed within a field as wide as
   the `width` string and filled from it, aligned left (the default), right, or
   center. A string longer than the field is truncated to it. */
static int str_align(struct th_tree *tree, const xp_result *args, int argc, xp_result *out) {
    Py_ssize_t str_len;
    Py_ssize_t pad_len;
    Py_UCS4 *str_text = to_string(tree, &args[0], &str_len);
    Py_UCS4 *pad_text = to_string(tree, &args[1], &pad_len);
    Py_UCS4 *align_text = NULL;
    Py_ssize_t align_len = 0;
    if (argc >= 3) {
        align_text = to_string(tree, &args[2], &align_len);
    }
    if (str_text == NULL || pad_text == NULL || (argc >= 3 && align_text == NULL)) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(str_text);                                                        /* GCOVR_EXCL_LINE */
        PyMem_Free(pad_text);                                                        /* GCOVR_EXCL_LINE */
        PyMem_Free(align_text);                                                      /* GCOVR_EXCL_LINE */
        return -1;                                                                   /* GCOVR_EXCL_LINE */
    }
    int alignment = 0; /* 0 = left, 1 = right, 2 = center */
    if (ucs4_eq_ascii(align_text, align_len, "right")) {
        alignment = 1;
    } else if (ucs4_eq_ascii(align_text, align_len, "center")) {
        alignment = 2;
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)pad_len * sizeof(Py_UCS4));
    if (buf == NULL) {          /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(str_text);   /* GCOVR_EXCL_LINE */
        PyMem_Free(pad_text);   /* GCOVR_EXCL_LINE */
        PyMem_Free(align_text); /* GCOVR_EXCL_LINE */
        return -1;              /* GCOVR_EXCL_LINE */
    }
    if (str_len >= pad_len) {
        const Py_UCS4 *source = alignment == 1 ? str_text + (str_len - pad_len) : str_text;
        memcpy(buf, source, (size_t)pad_len * sizeof(Py_UCS4));
    } else {
        memcpy(buf, pad_text, (size_t)pad_len * sizeof(Py_UCS4));
        Py_ssize_t gap = pad_len - str_len;
        Py_ssize_t offset = alignment == 1 ? gap : alignment == 2 ? gap / 2 : 0;
        memcpy(buf + offset, str_text, (size_t)str_len * sizeof(Py_UCS4));
    }
    PyMem_Free(str_text);
    PyMem_Free(pad_text);
    PyMem_Free(align_text);
    result_string(out, buf, pad_len);
    return 0;
}

/* The EXSLT math functions (math:). */

/* math:min / math:max over a node-set's numeric string-values. An empty node-set or
   any non-numeric member yields NaN, matching EXSLT. */
static int math_extreme(struct th_tree *tree, const xp_result *arg, int want_max, xp_result *out) {
    const xp_nodeset *nodes = &arg->nodes;
    if (nodes->len == 0) {
        result_number(out, (double)NAN);
        return 0;
    }
    double extreme = want_max ? -INFINITY : INFINITY;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t length;
        Py_UCS4 *text = item_string(tree, nodes->items[index], &length);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        double value = parse_number(text, length);
        PyMem_Free(text);
        if (isnan(value)) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
            extreme = (double)NAN;
            break;
        }
        if (want_max ? value > extreme : value < extreme) {
            extreme = value;
        }
    }
    result_number(out, extreme);
    return 0;
}

/* math:highest / math:lowest: the members whose numeric value is the maximum
   (`want_max`) or minimum. Any non-numeric member, or an empty node-set, yields an
   empty node-set. */
static int math_select(struct th_tree *tree, const xp_result *arg, int want_max, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *nodes = &arg->nodes;
    if (nodes->len == 0) {
        return 0;
    }
    double *values = PyMem_Malloc((size_t)nodes->len * sizeof(double));
    if (values == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    double extreme = want_max ? -INFINITY : INFINITY;
    int numeric = 1;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t length;
        Py_UCS4 *text = item_string(tree, nodes->items[index], &length);
        if (text == NULL) {     /* GCOVR_EXCL_BR_LINE: alloc */
            PyMem_Free(values); /* GCOVR_EXCL_LINE */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        values[index] = parse_number(text, length);
        PyMem_Free(text);
        if (isnan(values[index])) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
            numeric = 0;
        } else if (want_max ? values[index] > extreme : values[index] < extreme) {
            extreme = values[index];
        }
    }
    int rc = 0;
    if (numeric) {
        for (Py_ssize_t index = 0; index < nodes->len; index++) {
            xp_item member = nodes->items[index];
            if (values[index] == extreme) {
                if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;                                              /* GCOVR_EXCL_LINE */
                } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            }
        }
    }
    PyMem_Free(values);
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* The EXSLT date functions (date:). */

/* Parse `count` ASCII digits into *value; 0 on a non-digit. */
static int ucs4_digits(const Py_UCS4 *text, Py_ssize_t count, int *value) {
    int accumulator = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        if (text[index] < '0' || text[index] > '9') {
            return 0;
        }
        accumulator = accumulator * 10 + (int)(text[index] - '0');
    }
    *value = accumulator;
    return 1;
}

/* Parse an ISO 8601 "YYYY-MM-DD" calendar date, ignoring any trailing time. 0 when
   the text does not begin with a well-formed, in-range date. */
static int parse_iso_date(const Py_UCS4 *text, Py_ssize_t len, int *year, int *month, int *day) {
    if (len < 10 || text[4] != '-' || text[7] != '-') {
        return 0;
    }
    if (!ucs4_digits(text, 4, year) || !ucs4_digits(text + 5, 2, month) || !ucs4_digits(text + 8, 2, day)) {
        return 0;
    }
    if (*month < 1 || *month > 12 || *day < 1 || *day > 31) {
        return 0;
    }
    return len == 10 || text[10] == 'T' || text[10] == ' ';
}

/* The day of the week for a Gregorian date, 0 = Sunday (Sakamoto's algorithm). */
static int day_of_week(int year, int month, int day) {
    static const int month_offsets[12] = {0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4};
    int adjusted_year = month < 3 ? year - 1 : year;
    return (adjusted_year + adjusted_year / 4 - adjusted_year / 100 + adjusted_year / 400 + month_offsets[month - 1] +
            day) %
           7;
}

/* date:year / date:month-in-year / date:day-in-month / date:day-in-week, selected by
   `field`. A string that is not a valid date yields NaN. */
static int date_number(struct th_tree *tree, const xp_result *arg, int field, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, arg, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    int year;
    int month;
    int day;
    int valid = parse_iso_date(text, len, &year, &month, &day);
    PyMem_Free(text);
    if (!valid) {
        result_number(out, (double)NAN);
        return 0;
    }
    double value = field == 0   ? year
                   : field == 1 ? month
                   : field == 2 ? day
                                : day_of_week(year, month, day) + 1; /* EXSLT counts Sunday as 1 */
    result_number(out, value);
    return 0;
}

/* date:leap-year: true when the date's year is a Gregorian leap year, false for a
   non-leap year or an unparsable date. */
static int date_leap_year(struct th_tree *tree, const xp_result *arg, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, arg, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    int year;
    int month;
    int day;
    int valid = parse_iso_date(text, len, &year, &month, &day);
    PyMem_Free(text);
    int leap = valid && ((year % 4 == 0 && year % 100 != 0) || year % 400 == 0);
    result_bool(out, leap);
    return 0;
}

/* Every core and EXSLT function's fixed arity (XPath 1.0 §4): the least and most
   arguments it accepts, with max_args -1 for the one variadic function, concat. A call
   whose count falls outside this range is a static error rejected before the body runs,
   so no branch ever reads an argument the caller did not supply (issue #420). */
typedef struct {
    const char *name;
    int8_t min_args;
    int8_t max_args;
} xp_func_sig;

static const xp_func_sig FUNC_SIGS[] = {
    {"last", 0, 0},
    {"position", 0, 0},
    {"count", 1, 1},
    {"id", 1, 1},
    {"local-name", 0, 1},
    {"namespace-uri", 0, 1},
    {"name", 0, 1},
    {"string", 0, 1},
    {"concat", 2, -1},
    {"starts-with", 2, 2},
    {"contains", 2, 2},
    {"substring-before", 2, 2},
    {"substring-after", 2, 2},
    {"substring", 2, 3},
    {"string-length", 0, 1},
    {"normalize-space", 0, 1},
    {"translate", 3, 3},
    {"ends-with", 2, 2},
    {"string-join", 2, 2},
    {"lower-case", 1, 1},
    {"upper-case", 1, 1},
    {"matches", 2, 3},
    {"replace", 3, 4},
    {"boolean", 1, 1},
    {"not", 1, 1},
    {"true", 0, 0},
    {"false", 0, 0},
    {"lang", 1, 1},
    {"number", 0, 1},
    {"sum", 1, 1},
    {"floor", 1, 1},
    {"ceiling", 1, 1},
    {"round", 1, 1},
    {"re:test", 2, 3},
    {"re:replace", 4, 4},
    {"set:difference", 2, 2},
    {"set:intersection", 2, 2},
    {"set:has-same-node", 2, 2},
    {"set:leading", 2, 2},
    {"set:trailing", 2, 2},
    {"set:distinct", 1, 1},
    {"str:concat", 1, 1},
    {"str:replace", 3, 3},
    {"str:padding", 1, 2},
    {"str:align", 2, 3},
    {"math:min", 1, 1},
    {"math:max", 1, 1},
    {"math:highest", 1, 1},
    {"math:lowest", 1, 1},
    {"math:abs", 1, 1},
    {"math:power", 2, 2},
    {"date:year", 1, 1},
    {"date:month-in-year", 1, 1},
    {"date:day-in-month", 1, 1},
    {"date:day-in-week", 1, 1},
    {"date:leap-year", 1, 1},
};

static const xp_func_sig *func_signature(const xn *fn) {
    for (size_t index = 0; index < sizeof(FUNC_SIGS) / sizeof(FUNC_SIGS[0]); index++) {
        if (func_is(fn, FUNC_SIGS[index].name)) {
            return &FUNC_SIGS[index];
        }
    }
    return NULL;
}

/* A fresh Python str of the called function's name, for an error message. */
static PyObject *function_name(const xn *fn) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, fn->str, fn->str_len);
}

/* Report a call to a name that is neither a core function nor a registered extension.
   Returns -1 with a ValueError set, which xpath_raise_status then surfaces unchanged. */
static int raise_unknown_function(const xn *fn) {
    PyObject *name = function_name(fn);
    if (name != NULL) { /* GCOVR_EXCL_BR_LINE: the name allocation cannot be forced to fail */
        PyErr_Format(PyExc_ValueError, "xpath: unknown function '%U'", name);
        Py_DECREF(name);
    }
    return -1;
}

/* Report a call whose argument count is outside the function's fixed arity. */
static int raise_arity(const xn *fn, const xp_func_sig *sig, int argc) {
    PyObject *name = function_name(fn);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: the name allocation cannot be forced to fail */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    if (sig->max_args < 0) {
        PyErr_Format(PyExc_ValueError, "xpath: %U() takes at least %d arguments, got %d", name, sig->min_args, argc);
    } else if (sig->min_args == sig->max_args) {
        PyErr_Format(PyExc_ValueError, "xpath: %U() takes %d argument%s, got %d", name, sig->min_args,
                     sig->min_args == 1 ? "" : "s", argc);
    } else {
        PyErr_Format(PyExc_ValueError, "xpath: %U() takes %d to %d arguments, got %d", name, sig->min_args,
                     sig->max_args, argc);
    }
    Py_DECREF(name);
    return -1;
}

int eval_function(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out) {
    const xn *fn = &prog->nodes[idx];
    int argc = 0;
    for (int32_t arg_node = fn->first; arg_node >= 0; arg_node = prog->nodes[arg_node].next) {
        argc++;
    }
    const xp_func_sig *sig = func_signature(fn);
    if (sig != NULL && (argc < sig->min_args || (sig->max_args >= 0 && argc > sig->max_args))) {
        return raise_arity(fn, sig, argc);
    }
    xp_result *args = NULL;
    if (argc > 0) {
        args = PyMem_Calloc((size_t)argc, sizeof(xp_result));
        if (args == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
    }
    int filled = 0;
    for (int32_t arg_node = fn->first; arg_node >= 0; arg_node = prog->nodes[arg_node].next) {
        int arg_rc = eval_expr(prog, arg_node, ctx, &args[filled]);
        if (arg_rc < 0) {
            for (int cleanup_index = 0; cleanup_index < filled; cleanup_index++) {
                xp_result_free(&args[cleanup_index]);
            }
            PyMem_Free(args);
            return arg_rc;
        }
        filled++;
    }
    int rc = 0;
    if (func_is(fn, "true") || func_is(fn, "false")) {
        result_bool(out, func_is(fn, "true"));
    } else if (func_is(fn, "position")) {
        result_number(out, (double)ctx->pos);
    } else if (func_is(fn, "last")) {
        result_number(out, (double)ctx->size);
    } else if (func_is(fn, "not")) {
        result_bool(out, !to_boolean(ctx->tree, &args[0]));
    } else if (func_is(fn, "boolean")) {
        result_bool(out, to_boolean(ctx->tree, &args[0]));
    } else if (func_is(fn, "number")) {
        result_number(out, argc >= 1 ? to_number(ctx->tree, &args[0]) : context_node_number(ctx));
    } else if (func_is(fn, "floor") || func_is(fn, "ceiling") || func_is(fn, "round")) {
        double value = to_number(ctx->tree, &args[0]);
        result_number(out, func_is(fn, "floor")     ? floor(value)
                           : func_is(fn, "ceiling") ? ceil(value)
                                                    : xp_round(value));
    } else if (func_is(fn, "count")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "count() of a non-node-set";
            rc = -4;
        } else {
            result_number(out, (double)args[0].nodes.len);
        }
    } else if (func_is(fn, "sum")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "sum() of a non-node-set";
            rc = -4;
        } else {
            double total = 0;
            for (Py_ssize_t index = 0; index < args[0].nodes.len; index++) {
                Py_ssize_t length;
                Py_UCS4 *text = item_string(ctx->tree, args[0].nodes.items[index], &length);
                if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;        /* GCOVR_EXCL_LINE */
                    break;          /* GCOVR_EXCL_LINE */
                }
                total += parse_number(text, length);
                PyMem_Free(text);
            }
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                result_number(out, total);
            }
        }
    } else if (func_is(fn, "string")) {
        Py_ssize_t length;
        Py_UCS4 *text = arg_or_context_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_string(out, text, length);
        }
    } else if (func_is(fn, "string-length")) {
        Py_ssize_t length;
        Py_UCS4 *text = arg_or_context_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_number(out, (double)length);
            PyMem_Free(text);
        }
    } else if (func_is(fn, "normalize-space")) {
        Py_ssize_t length;
        Py_UCS4 *text = arg_or_context_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : normalize_space(text, length, out); /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(text);
    } else if (func_is(fn, "local-name") || func_is(fn, "name")) {
        Py_ssize_t length;
        Py_UCS4 *text = node_name_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_string(out, text, length);
        }
    } else if (func_is(fn, "namespace-uri")) {
        Py_ssize_t length;
        Py_UCS4 *text = node_namespace_uri(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_string(out, text, length);
        }
    } else if (func_is(fn, "lang")) {
        int matched = node_lang(ctx, &args[0]);
        rc = matched < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {             /* GCOVR_EXCL_BR_LINE: alloc */
            result_bool(out, matched);
        }
    } else if (func_is(fn, "id")) {
        rc = eval_id(ctx, &args[0], out);
    } else if (func_is(fn, "re:test") || func_is(fn, "matches")) {
        /* fn:matches shares the EXSLT re:test regex pipeline (input, pattern, flags?) */
        rc = exslt_re_test(ctx->tree, args, argc, out);
    } else if (func_is(fn, "re:replace")) {
        rc = exslt_re_replace(ctx->tree, args, out);
    } else if (func_is(fn, "set:difference") || func_is(fn, "set:intersection") || func_is(fn, "set:has-same-node") ||
               func_is(fn, "set:leading") || func_is(fn, "set:trailing")) {
        if (args[0].kind != XP_NODESET || args[1].kind != XP_NODESET) {
            *ctx->feature = "a set: function on a non-node-set";
            rc = -4;
        } else if (func_is(fn, "set:difference")) {
            rc = set_filter(args, 0, out);
        } else if (func_is(fn, "set:intersection")) {
            rc = set_filter(args, 1, out);
        } else if (func_is(fn, "set:has-same-node")) {
            set_has_same_node(args, out);
        } else {
            rc = set_split(args, func_is(fn, "set:leading"), out);
        }
    } else if (func_is(fn, "set:distinct")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "set:distinct on a non-node-set";
            rc = -4;
        } else {
            rc = set_distinct(ctx->tree, &args[0], out);
        }
    } else if (func_is(fn, "str:concat")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "str:concat on a non-node-set";
            rc = -4;
        } else {
            rc = str_concat(ctx->tree, &args[0], out);
        }
    } else if (func_is(fn, "str:replace")) {
        rc = str_replace(ctx->tree, args, out);
    } else if (func_is(fn, "str:padding")) {
        rc = str_padding(ctx->tree, args, argc, out);
    } else if (func_is(fn, "str:align")) {
        rc = str_align(ctx->tree, args, argc, out);
    } else if (func_is(fn, "math:min") || func_is(fn, "math:max") || func_is(fn, "math:highest") ||
               func_is(fn, "math:lowest")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "a math: function on a non-node-set";
            rc = -4;
        } else if (func_is(fn, "math:min") || func_is(fn, "math:max")) {
            rc = math_extreme(ctx->tree, &args[0], func_is(fn, "math:max"), out);
        } else {
            rc = math_select(ctx->tree, &args[0], func_is(fn, "math:highest"), out);
        }
    } else if (func_is(fn, "math:abs")) {
        result_number(out, fabs(to_number(ctx->tree, &args[0])));
    } else if (func_is(fn, "math:power")) {
        result_number(out, pow(to_number(ctx->tree, &args[0]), to_number(ctx->tree, &args[1])));
    } else if (func_is(fn, "date:year")) {
        rc = date_number(ctx->tree, &args[0], 0, out);
    } else if (func_is(fn, "date:month-in-year")) {
        rc = date_number(ctx->tree, &args[0], 1, out);
    } else if (func_is(fn, "date:day-in-month")) {
        rc = date_number(ctx->tree, &args[0], 2, out);
    } else if (func_is(fn, "date:day-in-week")) {
        rc = date_number(ctx->tree, &args[0], 3, out);
    } else if (func_is(fn, "date:leap-year")) {
        rc = date_leap_year(ctx->tree, &args[0], out);
    } else if (func_is(fn, "concat")) {
        rc = concat(ctx->tree, args, argc, out);
    } else if (func_is(fn, "starts-with") || func_is(fn, "contains")) {
        Py_ssize_t hl;
        Py_ssize_t nl;
        Py_UCS4 *hay = to_string(ctx->tree, &args[0], &hl);
        Py_UCS4 *needle = to_string(ctx->tree, &args[1], &nl);
        if (hay == NULL || needle == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                         /* GCOVR_EXCL_LINE */
        } else if (func_is(fn, "starts-with")) {
            result_bool(out, nl <= hl && memcmp(hay, needle, (size_t)nl * sizeof(Py_UCS4)) == 0);
        } else {
            result_bool(out, ucs4_find(hay, hl, needle, nl) >= 0);
        }
        PyMem_Free(hay);
        PyMem_Free(needle);
    } else if (func_is(fn, "substring-before") || func_is(fn, "substring-after")) {
        Py_ssize_t hl;
        Py_ssize_t nl;
        Py_UCS4 *hay = to_string(ctx->tree, &args[0], &hl);
        Py_UCS4 *needle = to_string(ctx->tree, &args[1], &nl);
        if (hay == NULL || needle == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                         /* GCOVR_EXCL_LINE */
        } else {                             /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            Py_ssize_t at = ucs4_find(hay, hl, needle, nl);
            const Py_UCS4 *start = at < 0 ? hay : func_is(fn, "substring-before") ? hay : hay + at + nl;
            Py_ssize_t rlen = at < 0 ? 0 : func_is(fn, "substring-before") ? at : hl - (at + nl);
            Py_UCS4 *result_text = ucs4_dup(start, rlen);
            rc = result_text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
            if (rc == 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
                result_string(out, result_text, rlen);
            }
        }
        PyMem_Free(hay);
        PyMem_Free(needle);
    } else if (func_is(fn, "substring")) {
        rc = substring(ctx->tree, args, argc, out);
    } else if (func_is(fn, "translate")) {
        Py_ssize_t sl;
        Py_ssize_t fl;
        Py_ssize_t tl;
        Py_UCS4 *text = to_string(ctx->tree, &args[0], &sl);
        Py_UCS4 *from = to_string(ctx->tree, &args[1], &fl);
        Py_UCS4 *to = to_string(ctx->tree, &args[2], &tl);
        if (text == NULL || from == NULL || to == NULL) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
            rc = -1;                                      /* GCOVR_EXCL_LINE */
        } else { /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            rc = translate(text, sl, from, fl, to, tl, out);
        }
        PyMem_Free(text);
        PyMem_Free(from);
        PyMem_Free(to);
    } else if (func_is(fn, "ends-with")) {
        Py_ssize_t hl;
        Py_ssize_t nl;
        Py_UCS4 *hay = to_string(ctx->tree, &args[0], &hl);
        Py_UCS4 *needle = to_string(ctx->tree, &args[1], &nl);
        if (hay == NULL || needle == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                         /* GCOVR_EXCL_LINE */
        } else {                             /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            result_bool(out, nl <= hl && memcmp(hay + hl - nl, needle, (size_t)nl * sizeof(Py_UCS4)) == 0);
        }
        PyMem_Free(hay);
        PyMem_Free(needle);
    } else if (func_is(fn, "string-join")) {
        rc = string_join(ctx->tree, args, out);
    } else if (func_is(fn, "lower-case") || func_is(fn, "upper-case")) {
        rc = case_convert(ctx->tree, &args[0], func_is(fn, "upper-case"), out);
    } else if (func_is(fn, "replace")) {
        rc = fn_replace(ctx->tree, args, argc, out);
    } else if (ctx->extension != NULL &&
               (rc = ctx->extension(ctx->extension_ctx, ctx->node, fn->str, fn->str_len, args, argc, out)) != -2) {
        /* a registered extension handled it (rc is 0 or a propagated error) */
    } else {
        rc = raise_unknown_function(fn);
    }
    for (int cleanup_index = 0; cleanup_index < argc; cleanup_index++) {
        xp_result_free(&args[cleanup_index]);
    }
    PyMem_Free(args);
    return rc;
}

PyObject *turbohtml_xpath_parse(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "xpath expression must be a str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    Py_UCS4 *src = PyUnicode_AsUCS4Copy(arg);
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    char err[128];
    xp_program *prog = xp_compile(src, len, err, sizeof(err));
    PyMem_Free(src);
    if (prog == NULL) {
        PyErr_SetString(PyExc_ValueError, err);
        return NULL;
    }
    Py_ssize_t dlen;
    Py_UCS4 *dump = xp_dump(prog, &dlen);
    xp_free(prog);
    if (dump == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, dump, dlen);
    PyMem_Free(dump);
    return result;
}
