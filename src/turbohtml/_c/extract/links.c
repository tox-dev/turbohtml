/* Link enumeration and rewriting over a parsed tree, the C engine behind the Node.links()/rewrite_links()/
   resolve_links() methods (wired in dom/node.c).

   This file is the walk that locates every link-bearing location and, for the rewrite path, splices a replacement back
   in place. The genuinely new capability over iterating <a href> by hand is the URLs embedded in CSS url()/@import (in
   a style attribute and in <style> text), in a <meta http-equiv=refresh> content value, and in the srcset/ping/archive
   list attributes. URL resolution itself (resolve_links) is stdlib urllib.parse.urljoin, bound through
   functools.partial here, so RFC 3986 is not reinvented. */

#include "core/ascii.h"
#include "core/common.h"

#include "core/vec.h"
#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "dom/tree.h"

#include <string.h>

/* A scanner reports each URL span [start, end) inside a value buffer through this callback; returning -1 aborts the
   walk with a Python error already set. Keeping the scanners callback-driven means none of them allocates. */
typedef int (*link_emit)(void *ctx, Py_ssize_t start, Py_ssize_t end);
typedef int (*scanner_fn)(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx);

/* A CSS/identifier byte, used to reject url(/import/url= matches that are only the tail of a longer identifier (e.g.
   "blur(" must not match "url(", "curl" must not match the refresh keyword). */
static int is_ident(Py_UCS4 c) {
    return is_ascii_alpha(c) || is_ascii_digit(c) || c == '_' || c == '-';
}

/* Case-insensitive (ASCII) match of the literal `lit` at offset `pos`; letters compare case-folded, every other byte
   exactly, so "(", "=", and "@" match literally. */
static int imatch(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t pos, const char *lit, Py_ssize_t lit_len) {
    if (pos + lit_len > len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < lit_len; index++) {
        Py_UCS4 c = value[pos + index];
        Py_UCS4 folded = lower_ascii(c);
        if (folded != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

/* A whole single-URL attribute value (href, src, ...), with surrounding whitespace trimmed off the reported span. */
static int scan_single(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    if (end > start) {
        return emit(ctx, start, end);
    }
    return 0;
}

/* A whitespace-separated URL list (a/area ping, object/applet archive): each non-empty token is a URL. */
static int scan_space_list(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && is_space(value[pos])) {
            pos++;
        }
        Py_ssize_t start = pos;
        while (pos < len && !is_space(value[pos])) {
            pos++;
        }
        if (pos > start) {
            if (emit(ctx, start, pos) < 0) { /* GCOVR_EXCL_BR_LINE: emit fails only on collect_span's realloc */
                return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    return 0;
}

/* A srcset/imagesrcset candidate list: the leading URL of each comma-separated candidate (the same split PR #228 uses
   to scheme-check candidates), with any trailing "1x"/"640w" descriptor skipped. */
static int scan_srcset(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && (value[pos] == ',' || is_space(value[pos]))) {
            pos++;
        }
        Py_ssize_t start = pos;
        while (pos < len && value[pos] != ',' && !is_space(value[pos])) {
            pos++;
        }
        if (pos > start) {
            if (emit(ctx, start, pos) < 0) { /* GCOVR_EXCL_BR_LINE: emit fails only on collect_span's realloc */
                return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        while (pos < len && value[pos] != ',') {
            pos++;
        }
    }
    return 0;
}

/* The URL inside a url(...) function: skip whitespace and an optional quote after the '(', then read to the matching
   quote, or to the first whitespace or ')'. Emits the span between, or nothing for an empty url(). */
static int css_url_span(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t open, link_emit emit, void *ctx,
                        Py_ssize_t *resume) {
    Py_ssize_t pos = open;
    while (pos < len && is_space(value[pos])) {
        pos++;
    }
    Py_UCS4 quote = 0;
    if (pos < len && (value[pos] == '"' || value[pos] == '\'')) {
        quote = value[pos++];
    }
    Py_ssize_t start = pos;
    while (pos < len && value[pos] != ')' && (quote ? value[pos] != quote : !is_space(value[pos]))) {
        pos++;
    }
    *resume = pos;
    if (pos > start) {
        return emit(ctx, start, pos);
    }
    return 0;
}

/* The URL inside an unguarded @import "..."/@import '...' string (the @import url(...) form is caught by the url()
   scan). Emits the quoted string's contents. */
static int css_import_span(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t after, link_emit emit, void *ctx,
                           Py_ssize_t *resume) {
    Py_ssize_t pos = after;
    while (pos < len && is_space(value[pos])) {
        pos++;
    }
    if (pos < len && (value[pos] == '"' || value[pos] == '\'')) {
        Py_UCS4 quote = value[pos++];
        Py_ssize_t start = pos;
        while (pos < len && value[pos] != quote) {
            pos++;
        }
        *resume = pos;
        if (pos > start) {
            return emit(ctx, start, pos);
        }
    }
    return 0;
}

/* CSS text (a style attribute or <style> content): every url(...) target and every @import string. */
static int scan_css(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        int boundary = pos == 0 || !is_ident(value[pos - 1]);
        if (boundary && imatch(value, len, pos, "url(", 4)) {
            Py_ssize_t resume = pos + 4;
            if (css_url_span(value, len, pos + 4, emit, ctx, &resume) < 0) { /* GCOVR_EXCL_BR_LINE: alloc only */
                return -1;                                                   /* GCOVR_EXCL_LINE: alloc-failure path */
            }
            pos = resume;
        } else if (boundary && value[pos] == '@' && imatch(value, len, pos, "@import", 7)) {
            Py_ssize_t resume = pos + 7;
            if (css_import_span(value, len, pos + 7, emit, ctx, &resume) < 0) { /* GCOVR_EXCL_BR_LINE: alloc only */
                return -1;                                                      /* GCOVR_EXCL_LINE: alloc-failure */
            }
            pos = resume;
        } else {
            pos++;
        }
    }
    return 0;
}

/* A <meta http-equiv=refresh content="delay; url=..."> target: the value after the first url= keyword, trimmed and
   unquoted. A bare timed refresh with no url= carries no link. */
static int scan_meta_refresh(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        int boundary = pos == 0 || !is_ident(value[pos - 1]);
        if (!boundary || !imatch(value, len, pos, "url", 3)) {
            continue;
        }
        Py_ssize_t after = pos + 3;
        while (after < len && is_space(value[after])) {
            after++;
        }
        if (after >= len || value[after] != '=') {
            continue;
        }
        after++;
        while (after < len && is_space(value[after])) {
            after++;
        }
        Py_UCS4 quote = 0;
        if (after < len && (value[after] == '"' || value[after] == '\'')) {
            quote = value[after++];
        }
        Py_ssize_t start = after;
        Py_ssize_t end = len;
        while (after < len && (quote ? value[after] != quote : !is_space(value[after]))) {
            after++;
        }
        end = after;
        if (end > start) {
            return emit(ctx, start, end);
        }
        return 0;
    }
    return 0;
}

typedef struct {
    Py_ssize_t start;
    Py_ssize_t end;
} link_span;

/* The walk's shared state. Exactly one of `result` (enumerate) and `replace` (rewrite) is set. `spans` is reused across
   values to avoid a per-value allocation. */
typedef struct {
    th_tree *tree;
    module_state *state; /* the module's per-interpreter state, for the Link record type */
    PyObject *handle;    /* the per-tree handle, held for the critical section across the walk */
    PyObject *owner;     /* the element/document the public call was made on, for wrapping enumerated nodes */
    PyObject *result;    /* list[Link] for enumerate, else NULL */
    PyObject *replace;   /* callable (str) -> str | None for rewrite, else NULL */
    link_span *spans;
    Py_ssize_t span_count;
    Py_ssize_t span_cap;
    th_node **nodes;       /* a pure-C snapshot of the elements to process, so no structural pointer */
    Py_ssize_t node_count; /* is dereferenced across a Python call that could suspend the critical section */
    Py_ssize_t node_cap;
} link_walk;

/* Append a node to a growable pointer array (the element snapshot, or a <style>'s text children). */
static int collect_node(th_node ***array, Py_ssize_t *count, Py_ssize_t *cap, th_node *node) {
    if (*count == *cap) {
        size_t new_cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(*count + 1), (size_t)*cap, 16, sizeof(th_node *), &new_cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        th_node **grown = PyMem_Realloc(*array, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        *array = grown;
        *cap = (Py_ssize_t)new_cap;
    }
    (*array)[(*count)++] = node;
    return 0;
}

static int collect_span(void *ctx, Py_ssize_t start, Py_ssize_t end) {
    link_walk *walk = ctx;
    if (walk->span_count == walk->span_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)(walk->span_count + 1), (size_t)walk->span_cap, 8, sizeof(link_span), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        link_span *grown = PyMem_Realloc(walk->spans, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        walk->spans = grown;
        walk->span_cap = (Py_ssize_t)cap;
    }
    walk->spans[walk->span_count].start = start;
    walk->spans[walk->span_count].end = end;
    walk->span_count++;
    return 0;
}

static PyObject *ucs4_slice(const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value + start, end - start);
}

/* Append a Link(element, attr, url) for each span to the result list. `report_element` is the owning Element wrapper
   and `report_attr` its attribute name (or None for <style> text); both are borrowed for the call's duration. */
static int emit_enumerated(link_walk *walk, PyObject *report_element, PyObject *report_attr, const Py_UCS4 *value) {
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        PyObject *url = ucs4_slice(value, walk->spans[index].start, walk->spans[index].end);
        if (url == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *record = PyObject_CallFunction(walk->state->link_type, "OOO", report_element, report_attr, url);
        Py_DECREF(url);
        if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int failed = PyList_Append(walk->result, record);
        Py_DECREF(record);
        if (failed < 0) { /* GCOVR_EXCL_BR_LINE: list append fails only on allocation failure */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* Build the value with each recorded replacement spliced over its span and write it back, to the named attribute or
   (attr_name == NULL) to the target text node. Returns the tree edit's status: 0, or -1 on allocation failure. */
static int splice_value(link_walk *walk, th_node *target, const char *attr_name, Py_ssize_t attr_len,
                        const Py_UCS4 *value, Py_ssize_t len, PyObject **replacements, Py_ssize_t new_len) {
    Py_UCS4 *out = PyMem_Malloc((size_t)new_len * sizeof(Py_UCS4) + 1);
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t out_len = 0;
    Py_ssize_t cursor = 0;
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        for (; cursor < walk->spans[index].start; cursor++) {
            out[out_len++] = value[cursor];
        }
        if (replacements[index] != NULL) {
            Py_ssize_t rep_len = PyUnicode_GET_LENGTH(replacements[index]);
            for (Py_ssize_t at = 0; at < rep_len; at++) {
                out[out_len++] = PyUnicode_READ_CHAR(replacements[index], at);
            }
        } else {
            for (; cursor < walk->spans[index].end; cursor++) {
                out[out_len++] = value[cursor];
            }
        }
        cursor = walk->spans[index].end;
    }
    for (; cursor < len; cursor++) {
        out[out_len++] = value[cursor];
    }
    int status = attr_name != NULL ? th_node_attr_set(walk->tree, target, attr_name, attr_len, out, out_len, 1)
                                   : th_node_set_data(walk->tree, target, out, out_len);
    PyMem_Free(out);
    return status;
}

/* Call replace(url) for each span and build a rewritten value, or leave the value untouched when every replacement is
   None or unchanged. */
static int rewrite_value(link_walk *walk, th_node *target, const char *attr_name, Py_ssize_t attr_len,
                         const Py_UCS4 *value, Py_ssize_t len) {
    PyObject **replacements = PyMem_Calloc((size_t)walk->span_count, sizeof(PyObject *));
    if (replacements == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();       /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;              /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t new_len = len;
    int changed = 0;
    int failed = 0;
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        PyObject *url = ucs4_slice(value, walk->spans[index].start, walk->spans[index].end);
        if (url == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
            break;         /* GCOVR_EXCL_LINE */
        }
        PyObject *out = PyObject_CallFunctionObjArgs(walk->replace, url, NULL);
        if (out == NULL) {
            Py_DECREF(url);
            failed = 1;
            break;
        }
        if (out == Py_None || PyUnicode_Check(out)) {
            if (out != Py_None && PyUnicode_Compare(out, url) != 0) {
                replacements[index] = Py_NewRef(out);
                new_len += PyUnicode_GET_LENGTH(out) - (walk->spans[index].end - walk->spans[index].start);
                changed = 1;
            }
        } else {
            PyErr_SetString(PyExc_TypeError, "a link rewrite must return str or None");
            failed = 1;
        }
        Py_DECREF(out);
        Py_DECREF(url);
        if (failed) {
            break;
        }
    }
    if (!failed && changed) {
        /* splice_value returns -1 only on an unforceable allocation failure; the branchless assignment keeps that
           dead path off the branch count without an excluded fall-through brace */
        failed = splice_value(walk, target, attr_name, attr_len, value, len, replacements, new_len) < 0;
    }
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        Py_XDECREF(replacements[index]);
    }
    PyMem_Free(replacements);
    return failed ? -1 : 0;
}

/* Scan one value with `scan`, then either record the spans or rewrite them. `report_element`/`report_attr` describe the
   enumeration location; `target`/`attr_name`/`attr_len` describe where a rewrite writes back. */
static int handle_value(link_walk *walk, PyObject *report_element, PyObject *report_attr, th_node *target,
                        const char *attr_name, Py_ssize_t attr_len, const Py_UCS4 *value, Py_ssize_t len,
                        scanner_fn scan) {
    walk->span_count = 0;
    if (scan(value, len, collect_span, walk) < 0) { /* GCOVR_EXCL_BR_LINE: scan alloc only (collect_span realloc) */
        return -1;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (walk->span_count == 0) {
        return 0;
    }
    if (walk->result != NULL) {
        return emit_enumerated(walk, report_element, report_attr, value);
    }
    return rewrite_value(walk, target, attr_name, attr_len, value, len);
}

/* The attribute value `name` on `element`, or NULL with *len=0 when absent or valueless. */
static const Py_UCS4 *attr_value(link_walk *walk, th_node *element, const char *name, Py_ssize_t name_len,
                                 Py_ssize_t *len) {
    Py_ssize_t index = th_node_attr_find(walk->tree, element, name, name_len);
    if (index < 0 || element->attrs[index].value == NULL) {
        *len = 0;
        return NULL;
    }
    *len = element->attrs[index].value_len;
    return element->attrs[index].value;
}

static int value_imatch(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    return len == lit_len && imatch(value, len, 0, lit, lit_len);
}

/* Single-URL attribute names (ping/archive are whitespace lists and srcset a candidate list, handled separately). */
static int is_single_url_attr(const char *name, Py_ssize_t len) {
    switch (len) {
    case 3:
        return memcmp(name, "src", 3) == 0;
    case 4:
        return memcmp(name, "href", 4) == 0 || memcmp(name, "cite", 4) == 0 || memcmp(name, "data", 4) == 0;
    case 6:
        return memcmp(name, "action", 6) == 0 || memcmp(name, "poster", 6) == 0;
    case 8:
        return memcmp(name, "longdesc", 8) == 0;
    case 10:
        return memcmp(name, "formaction", 10) == 0 || memcmp(name, "background", 10) == 0 ||
               memcmp(name, "xlink:href", 10) == 0;
    default:
        return 0;
    }
}

static int is_srcset_attr(const char *name, Py_ssize_t len) {
    return (len == 6 && memcmp(name, "srcset", 6) == 0) || (len == 11 && memcmp(name, "imagesrcset", 11) == 0);
}

/* Pick the scanner for attribute `name` on an element with tag `atom`, or NULL if the attribute holds no link. */
static scanner_fn scanner_for_attr(uint16_t atom, const char *name, Py_ssize_t len) {
    if (is_srcset_attr(name, len)) {
        return scan_srcset;
    }
    if (len == 5 && memcmp(name, "style", 5) == 0) {
        return scan_css;
    }
    if (len == 4 && memcmp(name, "ping", 4) == 0 && (atom == TH_TAG_A || atom == TH_TAG_AREA)) {
        return scan_space_list;
    }
    if (len == 7 && memcmp(name, "archive", 7) == 0 && (atom == TH_TAG_OBJECT || atom == TH_TAG_APPLET)) {
        return scan_space_list;
    }
    if (is_single_url_attr(name, len)) {
        return scan_single;
    }
    return NULL;
}

/* Does <meta> declare http-equiv=refresh (the only meta whose content carries a URL)? */
static int is_meta_refresh(link_walk *walk, th_node *element) {
    Py_ssize_t len = 0;
    const Py_UCS4 *equiv = attr_value(walk, element, "http-equiv", 10, &len);
    return equiv != NULL && value_imatch(equiv, len, "refresh", 7);
}

static int process_element(link_walk *walk, th_node *element) {
    PyObject *report_element = NULL;
    if (walk->result != NULL) {
        report_element = turbohtml_node_wrap_in(walk->owner, element);
        if (report_element == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    int failed = 0;
    /* a <style>'s text children are a stylesheet, scanned for url()/@import like a style attribute. A parsed text node
       holds a zero-copy span, not a ready buffer, so its data is materialized (and freed) per child. The children are
       snapshotted in a pure-C pass first so a concurrent mutation cannot relink the child list while handle_value runs
       a Python callback (which can suspend the per-tree critical section) below. */
    if (element->atom == TH_TAG_STYLE) {
        th_node **children = NULL;
        Py_ssize_t child_count = 0, child_cap = 0;
        for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_TEXT) {
                continue;
            }
            if (collect_node(&children, &child_count, &child_cap, child) < 0) { /* GCOVR_EXCL_BR_LINE: alloc only */
                failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;      /* GCOVR_EXCL_LINE */
            }
        }
        for (Py_ssize_t index = 0; index < child_count && !failed; index++) {
            Py_ssize_t text_len = 0;
            Py_UCS4 *text = th_node_data(walk->tree, children[index], &text_len);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                failed = 1;     /* GCOVR_EXCL_LINE: allocation-failure path */
                break;          /* GCOVR_EXCL_LINE */
            }
            failed =
                handle_value(walk, report_element, Py_None, children[index], NULL, 0, text, text_len, scan_css) < 0;
            PyMem_Free(text);
        }
        PyMem_Free(children);
    }
    /* an element is either a <style> or a <meta>, never both, so the style block above cannot have set failed here */
    if (element->atom == TH_TAG_META && is_meta_refresh(walk, element)) {
        Py_ssize_t len = 0;
        const Py_UCS4 *content = attr_value(walk, element, "content", 7, &len);
        if (content != NULL) {
            PyObject *report_attr = NULL;
            if (walk->result != NULL) {
                report_attr = PyUnicode_FromString("content");
                if (report_attr == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
                    Py_XDECREF(report_element); /* GCOVR_EXCL_LINE: allocation-failure path */
                    return -1;                  /* GCOVR_EXCL_LINE */
                }
            }
            failed = handle_value(walk, report_element, report_attr, element, "content", 7, content, len,
                                  scan_meta_refresh) < 0;
            Py_XDECREF(report_attr);
        }
    }
    for (Py_ssize_t index = 0; index < element->attr_count && !failed; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(walk->tree, element->attrs[index].name_atom, &name_len);
        scanner_fn scan = scanner_for_attr(element->atom, name, name_len);
        if (scan == NULL || element->attrs[index].value == NULL) {
            continue;
        }
        PyObject *report_attr = NULL;
        if (walk->result != NULL) {
            report_attr = PyUnicode_FromStringAndSize(name, name_len);
            if (report_attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                failed = 1;            /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                 /* GCOVR_EXCL_LINE */
            }
        }
        failed = handle_value(walk, report_element, report_attr, element, name, name_len, element->attrs[index].value,
                              element->attrs[index].value_len, scan) < 0;
        Py_XDECREF(report_attr);
    }
    Py_XDECREF(report_element);
    return failed ? -1 : 0;
}

/* Pre-order snapshot of every element under `root` (not its siblings) into walk->nodes. This runs no Python API, so the
   per-tree critical section cannot be suspended here and a concurrent structural mutation cannot relink a node while we
   follow first_child/next_sibling/parent. process_element then works off the snapshot, never re-deriving the structure
   across the Python callbacks/allocations it performs (which may suspend the lock). */
static int collect_subtree(link_walk *walk, th_node *root) {
    th_node *current = root;
    while (current != NULL) {
        if (current->type == TH_NODE_ELEMENT) {
            if (collect_node(&walk->nodes, &walk->node_count, &walk->node_cap, current) < 0) { /* GCOVR_EXCL_BR_LINE */
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        if (current->first_child != NULL) {
            current = current->first_child;
            continue;
        }
        while (current != root && current->next_sibling == NULL) {
            current = current->parent;
        }
        current = current == root ? NULL : current->next_sibling;
    }
    return 0;
}

/* Shared engine: run the walk over `root` under `owner`'s per-tree critical section. The thin Node methods in
   dom/node.c derive (owner, tree, root) straight from the node and hand them here; the module state for the Link
   record type and the per-tree handle for the lock come off `owner`. */
static PyObject *run_walk(PyObject *owner, th_tree *tree, th_node *root, PyObject *result, PyObject *replace) {
    link_walk walk = {.tree = tree, .owner = owner, .result = result, .replace = replace};
    walk.state = PyType_GetModuleState(Py_TYPE(owner));
    /* read the handle into the struct (always, so it is covered) rather than into the macro argument, which the GIL
       build's no-op Py_BEGIN_CRITICAL_SECTION does not evaluate */
    walk.handle = turbohtml_node_handle(owner);
    int failed;
    Py_BEGIN_CRITICAL_SECTION(walk.handle); /* per-tree lock: no concurrent mutation mid-walk */
    failed = collect_subtree(&walk, root) < 0;
    for (Py_ssize_t index = 0; !failed && index < walk.node_count; index++) {
        failed = process_element(&walk, walk.nodes[index]) < 0;
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(walk.spans);
    PyMem_Free(walk.nodes);
    if (failed) {
        Py_XDECREF(result);
        return NULL;
    }
    return result != NULL ? result : Py_NewRef(Py_None);
}

/* Store the Link record type the C core constructs for each enumerated link; turbohtml._links registers it on import.
 */
PyObject *turbohtml_register_links(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->link_type, Py_NewRef(type));
    Py_RETURN_NONE;
}

/* Node.links() -> list[Link]. Enumerates every link-bearing location under the node in document order. */
PyObject *turbohtml_node_links(PyObject *owner, th_tree *tree, th_node *root) {
    PyObject *result = PyList_New(0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return run_walk(owner, tree, root, result, NULL);
}

/* Node.rewrite_links(replace) -> None. Calls replace(url) for every link; a str result replaces it, None leaves it. */
PyObject *turbohtml_node_rewrite_links(PyObject *owner, th_tree *tree, th_node *root, PyObject *replace) {
    if (!PyCallable_Check(replace)) {
        PyErr_SetString(PyExc_TypeError, "rewrite_links expected a callable");
        return NULL;
    }
    return run_walk(owner, tree, root, NULL, replace);
}

/* Node.resolve_links(base_url) -> None. Rewrites every link absolute against base_url with functools.partial bound over
   stdlib urllib.parse.urljoin, so RFC 3986 resolution is not reinvented. */
PyObject *turbohtml_node_resolve_links(PyObject *owner, th_tree *tree, th_node *root, PyObject *base_url) {
    if (!PyUnicode_Check(base_url)) {
        PyErr_SetString(PyExc_TypeError, "resolve_links expected a base URL string");
        return NULL;
    }
    PyObject *parse_module = PyImport_ImportModule("urllib.parse");
    if (parse_module == NULL) { /* GCOVR_EXCL_BR_LINE: a stdlib import cannot be forced to fail from a test */
        return NULL;            /* GCOVR_EXCL_LINE: import-failure path */
    }
    PyObject *urljoin = PyObject_GetAttrString(parse_module, "urljoin");
    Py_DECREF(parse_module);
    if (urljoin == NULL) { /* GCOVR_EXCL_BR_LINE: urllib.parse.urljoin always exists */
        return NULL;       /* GCOVR_EXCL_LINE: attribute-failure path */
    }
    PyObject *functools_module = PyImport_ImportModule("functools");
    if (functools_module == NULL) { /* GCOVR_EXCL_BR_LINE: a stdlib import cannot be forced to fail from a test */
        Py_DECREF(urljoin);         /* GCOVR_EXCL_LINE: import-failure path */
        return NULL;                /* GCOVR_EXCL_LINE */
    }
    PyObject *partial = PyObject_GetAttrString(functools_module, "partial");
    Py_DECREF(functools_module);
    if (partial == NULL) {  /* GCOVR_EXCL_BR_LINE: functools.partial always exists */
        Py_DECREF(urljoin); /* GCOVR_EXCL_LINE: attribute-failure path */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    PyObject *bound = PyObject_CallFunctionObjArgs(partial, urljoin, base_url, NULL);
    Py_DECREF(partial);
    Py_DECREF(urljoin);
    if (bound == NULL) { /* GCOVR_EXCL_BR_LINE: binding a partial cannot be forced to fail from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = run_walk(owner, tree, root, NULL, bound);
    Py_DECREF(bound);
    return result;
}
