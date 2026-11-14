#ifndef TURBOHTML_C14N_BASE_H
#define TURBOHTML_C14N_BASE_H

#include "serialize/internal.h"
#include "url/url.h"

static const th_node_attr *c14n_base_attribute(th_tree *tree, const th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t length;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &length);
        if (length == 8 && memcmp(name, "xml:base", 8) == 0) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

/* The xml:base joined so far. Once it has a scheme or an authority a join only normalizes it, or lends a scheme to an
   authority without one, so it is kept whole in `absolute`. Before that it reads `head`, `parents` "../" segments,
   `tail` and `query`: a join rewrites the head and may consume parent segments, but copies the tail and the query. */
typedef struct {
    PyObject *absolute;
    int settled;
    sbuf head;
    Py_ssize_t parents;
    sbuf tail; /* reversed, since a join prepends to it */
    int tail_scheme;
    sbuf query;
    PyObject *scheme_only;
} c14n_chain;

static void c14n_chain_clear(c14n_chain *chain) {
    Py_XDECREF(chain->absolute);
    Py_XDECREF(chain->scheme_only);
    PyMem_Free(chain->head.data);
    PyMem_Free(chain->tail.data);
    PyMem_Free(chain->query.data);
}

static void c14n_chain_flatten(const c14n_chain *chain, const Py_UCS4 *head, Py_ssize_t head_len, Py_ssize_t parents,
                               sbuf *out) {
    out->failed |= chain->tail.failed | chain->query.failed;
    sbuf_put_ucs4(out, head, head_len);
    for (Py_ssize_t index = 0; index < parents; index++) {
        sbuf_puts(out, "../");
    }
    for (Py_ssize_t index = chain->tail.len; index > 0; index--) {
        sbuf_putc(out, chain->tail.data[index - 1]);
    }
    sbuf_put_ucs4(out, chain->query.data, chain->query.len);
}

static int c14n_is_dot_segment(const Py_UCS4 *segment, Py_ssize_t length) {
    return (length == 1 && segment[0] == '.') || (length == 2 && segment[0] == '.' && segment[1] == '.');
}

/* Take a join's result: `path` is the value up to where `parents` "../" segments, the tail and the query follow
   unchanged. A result with a scheme or an authority is kept whole. Otherwise its trailing segments that a later join
   copies verbatim move into the tail: no "." or "..", and no empty one except a final one, since a relative join drops
   the empty segments inside its path (a rooted one keeps them). Its trailing "../" segments then join the count. */
static int c14n_chain_absorb(c14n_chain *chain, const Py_UCS4 *path, Py_ssize_t length, Py_ssize_t parents) {
    if (chain->tail.len == 0 && chain->query.len == 0) {
        for (Py_ssize_t index = 0; index < length; index++) {
            if (path[index] == '?') {
                sbuf_put_ucs4(&chain->query, path + index, length - index);
                length = index;
                break;
            }
        }
    }
    /* parent segments only follow a path ending in "../", so an empty path puts the tail's first segment first */
    int absolute = length == 0
                       ? chain->tail_scheme
                       : th_url_scheme_colon(path, 0, length) > 0 || (length >= 2 && path[0] == '/' && path[1] == '/');
    if (absolute) {
        sbuf whole = {0};
        c14n_chain_flatten(chain, path, length, parents, &whole);
        if (whole.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(whole.data); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();       /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        chain->absolute = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, whole.data, whole.len);
        PyMem_Free(whole.data);
        return chain->absolute == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure */
    }
    Py_ssize_t cut = length;
    if (parents == 0) {
        int rooted = length > 0 && path[0] == '/';
        Py_ssize_t start = length;
        if (chain->tail.len == 0) {
            Py_ssize_t slash = length;
            while (slash > 0 && path[slash - 1] != '/') {
                slash--;
            }
            if (!c14n_is_dot_segment(path + slash, length - slash)) {
                start = slash;
            }
        }
        cut = start;
        while (start > 0 && path[start - 1] == '/') {
            Py_ssize_t segment = start - 1;
            while (segment > 0 && path[segment - 1] != '/') {
                segment--;
            }
            Py_ssize_t segment_len = start - 1 - segment;
            if (c14n_is_dot_segment(path + segment, segment_len) || (segment_len == 0 && (!rooted || segment == 0))) {
                break;
            }
            start = segment;
            if (segment_len > 0) {
                cut = start; /* the tail never starts with an empty segment, which would read as an authority */
            }
        }
        if (cut < length) {
            for (Py_ssize_t index = length; index > cut; index--) {
                sbuf_putc(&chain->tail, path[index - 1]);
            }
            chain->tail_scheme = th_url_scheme_colon(path, cut, length) > 0;
        }
    }
    while (cut >= 3 && path[cut - 1] == '/' && path[cut - 2] == '.' && path[cut - 3] == '.' &&
           (cut == 3 || path[cut - 4] == '/')) {
        cut -= 3;
        parents++;
    }
    chain->head.len = 0;
    sbuf_put_ucs4(&chain->head, path, cut);
    chain->parents = parents;
    if (chain->head.failed || chain->tail.failed || chain->query.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

/* Join `base` onto the head, `parents` "../" segments, a plain segment standing for the tail and a bare "?" standing
   for the query. A join copies the tail and the query verbatim, so the result ends with the stand-ins, whose length
   goes to `suffix`. */
static PyObject *c14n_chain_join_proxy(const c14n_chain *chain, const Py_UCS4 *base, Py_ssize_t base_len,
                                       Py_ssize_t parents, Py_ssize_t *suffix) {
    sbuf proxy = {0};
    sbuf_put_ucs4(&proxy, chain->head.data, chain->head.len);
    for (Py_ssize_t index = 0; index < parents; index++) {
        sbuf_puts(&proxy, "../");
    }
    Py_ssize_t start = proxy.len;
    if (chain->tail.len > 0) {
        sbuf_puts(&proxy, "z/");
    }
    if (chain->query.len > 0) {
        sbuf_putc(&proxy, '?');
    }
    *suffix = proxy.len - start;
    if (proxy.failed) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(proxy.data);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *target = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, proxy.data, proxy.len);
    PyMem_Free(proxy.data);
    if (target == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *joined = th_url_join_c14n_raw(base, base_len, target);
    Py_DECREF(target);
    return joined;
}

static int c14n_chain_join_relative(c14n_chain *chain, const Py_UCS4 *base, Py_ssize_t base_len) {
    /* A "../" removes a segment of the base or the head until none is left; from then on every further one is kept
       (in a relative path) or dropped (in a rooted one). Past `limit` they all do what the one after the probe does. */
    Py_ssize_t limit = base_len + chain->head.len + 4;
    Py_ssize_t probe = chain->parents < limit ? chain->parents : limit;
    Py_ssize_t suffix;
    PyObject *joined = c14n_chain_join_proxy(chain, base, base_len, probe, &suffix);
    if (joined == NULL) {
        return -1;
    }
    Py_ssize_t parents = 0;
    if (chain->parents > probe) {
        PyObject *next = c14n_chain_join_proxy(chain, base, base_len, probe + 1, &suffix);
        if (next == NULL) {    /* GCOVR_EXCL_BR_LINE: the join above parsed this base, so only allocation fails */
            Py_DECREF(joined); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(next) != PyUnicode_GET_LENGTH(joined)) {
            parents = chain->parents - probe;
        }
        Py_DECREF(next);
    }
    Py_UCS4 *data = PyUnicode_AsUCS4Copy(joined);
    Py_ssize_t length = PyUnicode_GET_LENGTH(joined) - suffix;
    Py_DECREF(joined);
    if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int status = c14n_chain_absorb(chain, data, length, parents);
    PyMem_Free(data);
    return status;
}

static int c14n_chain_join_absolute(c14n_chain *chain, const Py_UCS4 *base, Py_ssize_t base_len) {
    /* A settled value is one a join returned unchanged, so it is normalized: only a base that lends a scheme to an
       authority without one can change it. Any other base is still parsed, for the ValueError it may raise. */
    if (chain->settled &&
        (PyUnicode_READ_CHAR(chain->absolute, 0) != '/' || th_url_scheme_colon(base, 0, base_len) <= 0)) {
        if (chain->scheme_only == NULL) {
            chain->scheme_only = PyUnicode_FromString("x:");
            if (chain->scheme_only == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;                    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        PyObject *checked = th_url_join_c14n_raw(base, base_len, chain->scheme_only);
        Py_XDECREF(checked);
        return checked == NULL ? -1 : 0;
    }
    PyObject *joined = th_url_join_c14n_raw(base, base_len, chain->absolute);
    if (joined == NULL) {
        return -1;
    }
    chain->settled = PyUnicode_Compare(joined, chain->absolute) == 0;
    Py_SETREF(chain->absolute, joined);
    return 0;
}

static int c14n_chain_start(c14n_chain *chain, PyObject *joined) {
    Py_UCS4 *data = PyUnicode_AsUCS4Copy(joined);
    if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int status = c14n_chain_absorb(chain, data, PyUnicode_GET_LENGTH(joined), 0);
    PyMem_Free(data);
    return status;
}

/* A join reads all of the value it joins onto, which costs no more than the chain's bookkeeping while the value is
   this short; past it the value moves into the chain. */
#define C14N_BASE_EXACT_LENGTH 128

/* Canonical XML 1.1 (section 2.4) joins the apex's xml:base onto those of its omitted ancestors, innermost first. Once
   the joined value is long, a join reads the ancestor's value and the head the previous join wrote, not the tail it
   copies, so the fixup takes time linear in the length of the values, not in the square of their depth. Returns -1
   with an exception set: a join's ValueError, since a canonical form can neither drop the attribute nor write it
   unresolved. */
static int c14n_base_fixup(th_tree *tree, const th_node *apex, Py_UCS4 **value, Py_ssize_t *length) {
    /* gcc -Werror=maybe-uninitialized cannot tie the callers' read after a 0 status to the write on that path */
    *length = 0;
    const th_node_attr *own = c14n_base_attribute(tree, apex);
    PyObject *current = own == NULL ? PyUnicode_FromString("")
                                    : PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, own->value, own->value_len);
    if (current == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    c14n_chain chain = {0};
    int joined_once = 0;
    int status = 0;
    for (const th_node *ancestor = apex->parent; status == 0 && ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        const th_node_attr *base = c14n_base_attribute(tree, ancestor);
        if (base == NULL) {
            continue;
        }
        /* only a join's result moves into the chain, since the apex's own value may still carry a fragment */
        if (current != NULL && joined_once && PyUnicode_GET_LENGTH(current) > C14N_BASE_EXACT_LENGTH) {
            status = c14n_chain_start(&chain, current);
            Py_DECREF(current);
            current = NULL;
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                break;        /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        if (current != NULL) {
            Py_SETREF(current, th_url_join_c14n_raw(base->value, base->value_len, current));
            joined_once = 1;
            status = current == NULL ? -1 : 0;
        } else {
            status = chain.absolute != NULL ? c14n_chain_join_absolute(&chain, base->value, base->value_len)
                                            : c14n_chain_join_relative(&chain, base->value, base->value_len);
        }
    }
    PyObject *result = current != NULL ? current : chain.absolute;
    if (status == 0 && result != NULL) {
        *length = PyUnicode_GET_LENGTH(result);
        *value = PyUnicode_AsUCS4Copy(result);
        status = *value == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
    } else if (status == 0) {
        sbuf whole = {0};
        c14n_chain_flatten(&chain, chain.head.data, chain.head.len, chain.parents, &whole);
        *value = sbuf_finish(&whole, length);
        if (*value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            status = -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_XDECREF(current);
    c14n_chain_clear(&chain);
    return status;
}

#endif
