/* Paragraph segmentation and boilerplate classification behind turbohtml.extract.boilerplate.

   A page segments into units -- the block elements a reader sees as paragraphs -- and each unit is either article
   text or the navigation, footer and sidebar noise around it. The scoring that finds the content body is
   th_node_main_content; what remains is deciding which units are paragraphs at all and which of them survive the
   caller's thresholds, and that decision is what the caller gets back, so it runs here. */

#include "core/common.h"
#include "dom/tree.h"

/* The block elements a page segments into. A unit holding another unit is a container -- a <li> wrapping a <p>, a
   <td> wrapping a list -- so the nested units are the real paragraphs and the container is skipped. */
static int boilerplate_is_unit(const th_node *node) {
    switch (node->atom) {
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_P:
    case TH_TAG_LI:
    case TH_TAG_PRE:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_TD:
    case TH_TAG_DD:
    case TH_TAG_DT:
    case TH_TAG_FIGCAPTION:
        return node->type == TH_NODE_ELEMENT;
    default:
        return 0;
    }
}

static int boilerplate_is_heading(const th_node *node) {
    return node->atom >= TH_TAG_H1 && node->atom <= TH_TAG_H6;
}

/* Does any descendant of `node` open a unit of its own? */
static int boilerplate_holds_a_unit(const th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (boilerplate_is_unit(child) || boilerplate_holds_a_unit(child)) {
            return 1;
        }
    }
    return 0;
}

/* Is `ancestor` above `node`? The content body has to contain a unit for that unit to be article text. */
static int boilerplate_within(const th_node *node, const th_node *ancestor) {
    for (const th_node *walk = node->parent; walk != NULL; walk = walk->parent) {
        if (walk == ancestor) {
            return 1;
        }
    }
    return 0;
}

/* The combined length of the unit's text that sits inside an anchor. */
static Py_ssize_t boilerplate_linked_length(th_tree *tree, th_node *node) {
    Py_ssize_t linked = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (child->atom == TH_TAG_A) {
            Py_ssize_t length = 0;
            th_node_text(tree, child, &length);
            linked += length;
            continue;
        }
        linked += boilerplate_linked_length(tree, child);
    }
    return linked;
}

/* The ASCII whitespace ``str.split`` breaks on. Kept as a table rather than a chain of comparisons so each
   spelling is one iteration of one branch, which both coverage gates can see the whole of. */
static int boilerplate_is_space(Py_UCS4 point) {
    static const Py_UCS4 spaces[] = {' ', '\t', '\n', '\r', '\f', 0x0B};
    for (size_t index = 0; index < sizeof(spaces) / sizeof(spaces[0]); index++) {
        if (point == spaces[index]) {
            return 1;
        }
    }
    return 0;
}

/* Collapse the unit's text the way ``" ".join(text.split())`` does: runs of ASCII whitespace become one space and
   the edges are trimmed. Returns the collapsed string, or NULL with the error set. */
static PyObject *boilerplate_collapse(const Py_UCS4 *text, Py_ssize_t length) {
    Py_UCS4 *packed = PyMem_Malloc((size_t)(length + 1) * sizeof(*packed));
    if (packed == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation cannot be forced to fail */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t written = 0;
    int pending = 0;
    for (Py_ssize_t index = 0; index < length; index++) {
        Py_UCS4 point = text[index];
        if (boilerplate_is_space(point)) {
            pending = written > 0;
            continue;
        }
        if (pending) {
            packed[written++] = ' ';
            pending = 0;
        }
        packed[written++] = point;
    }
    PyObject *collapsed = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, packed, written);
    PyMem_Free(packed);
    return collapsed;
}

/* Classify one unit: outside the content body, link-dense, or below the length floor is boilerplate. */
static int boilerplate_classify(th_tree *tree, th_node *unit, const th_node *content, Py_ssize_t collapsed_length,
                                Py_ssize_t min_length, double max_link_density, int keep_headings) {
    if (content == NULL || !boilerplate_within(unit, content)) {
        return 1;
    }
    Py_ssize_t length = 0;
    th_node_text(tree, unit, &length);
    Py_ssize_t linked = boilerplate_linked_length(tree, unit);
    if (linked > 0 && (double)linked / (double)length > max_link_density) {
        return 1;
    }
    if (keep_headings && boilerplate_is_heading(unit)) {
        return 0; /* a heading is short by nature, so the length floor does not apply to it */
    }
    return collapsed_length < min_length;
}

/* Walk the tree in document order, appending one row per non-blank unit. */
static int boilerplate_walk(th_tree *tree, th_node *node, const th_node *content, Py_ssize_t min_length,
                            double max_link_density, int keep_headings, PyObject *out) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (!boilerplate_is_unit(child) || boilerplate_holds_a_unit(child)) {
            /* GCOVR_EXCL_BR_START: only an allocation failure below returns -1 */
            if (boilerplate_walk(tree, child, content, min_length, max_link_density, keep_headings, out) < 0) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            continue;
        }
        Py_ssize_t length = 0;
        const Py_UCS4 *text = th_node_text(tree, child, &length);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: text realization cannot be forced to fail */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        PyObject *collapsed = boilerplate_collapse(text, length);
        if (collapsed == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced to fail */
            return -1;           /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t collapsed_length = PyUnicode_GET_LENGTH(collapsed);
        if (collapsed_length == 0) { /* a blank unit is not a paragraph */
            Py_DECREF(collapsed);
            continue;
        }
        int boiler =
            boilerplate_classify(tree, child, content, collapsed_length, min_length, max_link_density, keep_headings);
        PyObject *row =
            PyTuple_Pack(3, collapsed, boiler ? Py_True : Py_False, boilerplate_is_heading(child) ? Py_True : Py_False);
        Py_DECREF(collapsed);
        /* GCOVR_EXCL_BR_START: tuple and list allocation cannot be forced to fail */
        int status = row == NULL ? -1 : PyList_Append(out, row);
        Py_XDECREF(row);
        if (status < 0) {
            return -1; /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    return 0;
}

/* _boilerplate(root, content, min_length, max_link_density, keep_headings) -> list[(text, is_boilerplate, is_heading)]

   `root` is the parsed document's root and `content` the element th_node_main_content scored as the article body, or
   None when the page has none. Each row carries what a Paragraph holds, so the typed layer only names the fields. */
PyObject *turbohtml_extract_boilerplate(PyObject *module, PyObject *args) {
    PyObject *root;
    PyObject *content;
    Py_ssize_t min_length;
    double max_link_density;
    int keep_headings;
    if (!PyArg_ParseTuple(args, "OOndp:_boilerplate", &root, &content, &min_length, &max_link_density,
                          &keep_headings)) {
        return NULL;
    }
    th_tree *tree;
    th_node *node;
    if (turbohtml_node_borrow(module, root, &tree, &node) < 0) {
        return NULL;
    }
    th_tree *content_tree;
    th_node *body = NULL;
    if (content != Py_None && turbohtml_node_borrow(module, content, &content_tree, &body) < 0) {
        return NULL;
    }
    PyObject *out = PyList_New(0);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: list allocation cannot be forced to fail */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    int walked = boilerplate_walk(tree, node, body, min_length, max_link_density, keep_headings, out);
    if (walked < 0) {   /* GCOVR_EXCL_BR_LINE: allocation */
        Py_DECREF(out); /* GCOVR_EXCL_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    return out;
}
