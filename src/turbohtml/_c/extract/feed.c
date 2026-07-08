/* RSS 2.0 / Atom 1.0 / RDF(RSS 1.0) feed extraction over a parsed tree, the C engine behind Document.feed() (wired into
   the document method table in dom/document.c) and the turbohtml.extract.feed(xml) facade.

   feedparser is the canonical Python feed library and htmlparser2's parseFeed the compact minimal model; both normalize
   an RSS <item>, an Atom <entry>, and an RDF item into one shape. This follows htmlparser2's minimal-first design (a
   dozen fields, first-present-wins precedence) with feedparser's field precedence for the cases that matter (the
   content:encoded/content/summary/description chain, guid-as-permalink link, the Atom rel="alternate" link selection).

   A feed is XML, but turbohtml has no XML parser: the WHATWG HTML tree builder parses it well enough because RSS/Atom
   element names are lowercase ASCII, and it keeps namespaced names (dc:creator, content:encoded) verbatim except for
   lowercasing. Two HTML quirks are handled here rather than fought: <link> is a void element, so an RSS/RDF
   <link>URL</link> leaves the URL as the void element's next text sibling (Atom's <link href=...> keeps the URL in the
   attribute, which survives); and <title> is RCDATA, which is exactly the plain-text value a feed title wants.

   The walk runs under the per-tree critical section so a concurrent mutation cannot relink the tree mid-walk, and hands
   the gathered plain str/None fields to the Feed and Entry NamedTuple classes the thin Python facade defines and
   registers on import (turbohtml._feed), so the typed result classes live in Python while every walk stays in C.
 */

#include "core/ascii.h"
#include "core/common.h"

#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "dom/nodes.h"

#include <string.h>

/* One candidate element tag for a normalized field: the name and its length, tried in precedence order. */
typedef struct {
    const char *name;
    Py_ssize_t len;
} feed_tag;

/* Whether the UCS4 run equals the ASCII literal [lit, lit + lit_len) ignoring ASCII case. The parser lowercases tag and
   attribute names, so an exact compare would do; folding keeps a hand-written test feed's mixed case (isPermaLink)
   matching too. */
static int ucs4_ieq(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    if (len != lit_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (lower_ascii(value[index]) != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

/* Whether element `node`'s tag name equals the ASCII literal [name, name + len). */
static int tag_is(const th_node *node, const char *name, Py_ssize_t len) {
    return ucs4_ieq(node->text, node->text_len, name, len);
}

/* The first direct element child of `parent` whose tag equals [name, name + len), or NULL when it has none. */
static th_node *child_named(th_node *parent, const char *name, Py_ssize_t len) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && tag_is(child, name, len)) {
            return child;
        }
    }
    return NULL;
}

/* A UCS4 run as a new str with leading and trailing ASCII whitespace stripped. NULL only on the excluded
   allocation-failure path. */
static PyObject *ucs4_trimmed(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    return ucs4_to_str(value + start, end - start);
}

/* The whitespace-trimmed text content of an element as a new str (empty when it holds only whitespace). NULL only on
   the excluded allocation-failure path. */
static PyObject *node_text_trimmed(th_tree *tree, th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *buffer = th_node_text(tree, node, &len);
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_trimmed(buffer, len);
    PyMem_Free(buffer);
    return result;
}

/* The first non-empty trimmed text among `parent`'s direct children named by the precedence-ordered `tags`, or None
   when none is present with a non-empty value (or when `parent` is NULL, a feed whose channel the parser dropped). NULL
   only on the excluded allocation-failure path. */
static PyObject *field_text(th_tree *tree, th_node *parent, const feed_tag *tags, size_t count) {
    if (parent == NULL) {
        return Py_NewRef(Py_None);
    }
    for (size_t index = 0; index < count; index++) {
        th_node *child = child_named(parent, tags[index].name, tags[index].len);
        if (child == NULL) {
            continue;
        }
        PyObject *value = node_text_trimmed(tree, child);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

/* A <link>'s trimmed href attribute (the Atom form), or NULL when it carries no non-empty href (the RSS/RDF form, whose
   URL is the void element's text sibling instead). */
static PyObject *link_href(th_tree *tree, th_node *link) {
    Py_ssize_t index = th_node_attr_find(tree, link, "href", 4);
    if (index < 0 || link->attrs[index].value == NULL) {
        return NULL;
    }
    PyObject *href = ucs4_trimmed(link->attrs[index].value, link->attrs[index].value_len);
    if (href == NULL) { /* GCOVR_EXCL_BR_LINE: ucs4_trimmed fails only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyUnicode_GET_LENGTH(href) == 0) {
        Py_DECREF(href);
        return NULL;
    }
    return href;
}

/* Whether an Atom <link>'s rel makes it the entry's primary link: an absent, valueless, or "alternate" rel. A rel of
   self/enclosure/edit/replies is a secondary link the walk skips unless no alternate is found. */
static int link_is_alternate(th_tree *tree, th_node *link) {
    Py_ssize_t index = th_node_attr_find(tree, link, "rel", 3);
    if (index < 0 || link->attrs[index].value == NULL) {
        return 1;
    }
    return ucs4_ieq(link->attrs[index].value, link->attrs[index].value_len, "alternate", 9);
}

/* The permalink URL of `parent`: the first Atom <link rel="alternate"> href, else the first RSS/RDF void <link>'s text
   sibling, else the first non-alternate Atom href as a fallback, else None. NULL only on the excluded
   allocation-failure path. */
static PyObject *extract_link(th_tree *tree, th_node *parent) {
    if (parent == NULL) {
        return Py_NewRef(Py_None);
    }
    PyObject *fallback = NULL;
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT || !tag_is(child, "link", 4)) {
            continue;
        }
        PyObject *href = link_href(tree, child);
        if (href != NULL) {
            if (link_is_alternate(tree, child)) {
                Py_XDECREF(fallback);
                return href;
            }
            if (fallback == NULL) {
                fallback = href;
            } else {
                Py_DECREF(href);
            }
            continue;
        }
        th_node *sibling = child->next_sibling;
        if (sibling != NULL && sibling->type == TH_NODE_TEXT) {
            PyObject *text = node_text_trimmed(tree, sibling);
            if (text == NULL) {       /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
                Py_XDECREF(fallback); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;          /* GCOVR_EXCL_LINE */
            }
            if (PyUnicode_GET_LENGTH(text) > 0) {
                Py_XDECREF(fallback);
                return text;
            }
            Py_DECREF(text);
        }
    }
    if (fallback != NULL) {
        return fallback;
    }
    return Py_NewRef(Py_None);
}

/* The author display name of `parent`: an <author>'s nested <name> (the Atom form), else the <author>'s own text (an
   RSS email), else a <dc:creator>, else None. NULL only on the excluded allocation-failure path. */
static PyObject *extract_author(th_tree *tree, th_node *parent) {
    static const feed_tag AUTHOR_TAGS[] = {{"author", 6}, {"dc:creator", 10}};
    for (size_t index = 0; index < sizeof(AUTHOR_TAGS) / sizeof(AUTHOR_TAGS[0]); index++) {
        th_node *child = child_named(parent, AUTHOR_TAGS[index].name, AUTHOR_TAGS[index].len);
        if (child == NULL) {
            continue;
        }
        th_node *name = child_named(child, "name", 4);
        PyObject *value = node_text_trimmed(tree, name != NULL ? name : child);
        if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(value) > 0) {
            return value;
        }
        Py_DECREF(value);
    }
    return Py_NewRef(Py_None);
}

/* The entry identifier: an RSS <guid> / Atom <id> element text, else the RDF item's rdf:about attribute, else None.
   NULL only on the excluded allocation-failure path. */
static PyObject *extract_id(th_tree *tree, th_node *entry) {
    static const feed_tag ID_TAGS[] = {{"guid", 4}, {"id", 2}};
    PyObject *value = field_text(tree, entry, ID_TAGS, sizeof(ID_TAGS) / sizeof(ID_TAGS[0]));
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: field_text fails only on allocation */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (value != Py_None) {
        return value;
    }
    Py_DECREF(value);
    Py_ssize_t index = th_node_attr_find(tree, entry, "rdf:about", 9);
    if (index >= 0 && entry->attrs[index].value != NULL) {
        return ucs4_trimmed(entry->attrs[index].value, entry->attrs[index].value_len);
    }
    return Py_NewRef(Py_None);
}

/* When an entry has no link element, an RSS <guid> that is a permalink (its isPermaLink is not "false") doubles as the
   link, mirroring feedparser. Replaces *link with the guid text when one qualifies. -1 only on the excluded
   allocation-failure path. */
static int apply_guid_permalink(th_tree *tree, th_node *entry, PyObject **link) {
    if (*link != Py_None) {
        return 0;
    }
    th_node *guid = child_named(entry, "guid", 4);
    if (guid == NULL) {
        return 0;
    }
    Py_ssize_t index = th_node_attr_find(tree, guid, "ispermalink", 11);
    if (index >= 0 && guid->attrs[index].value != NULL &&
        ucs4_ieq(guid->attrs[index].value, guid->attrs[index].value_len, "false", 5)) {
        return 0;
    }
    PyObject *value = node_text_trimmed(tree, guid);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: node_text_trimmed fails only on allocation */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyUnicode_GET_LENGTH(value) > 0) {
        Py_SETREF(*link, value);
    } else {
        Py_DECREF(value);
    }
    return 0;
}

/* Steal `value` into slot `index` of `record`, returning -1 only on the excluded allocation-failure path (a NULL value
   a field builder could not allocate). */
static int record_set(PyObject *record, Py_ssize_t index, PyObject *value) {
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: every field builder fails only on unforceable allocation */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyTuple_SET_ITEM(record, index, value);
    return 0;
}

/* The entry's link with the guid-permalink fallback applied, ready for record_set. NULL only on the excluded
   allocation-failure path. */
static PyObject *entry_link(th_tree *tree, th_node *item) {
    PyObject *link = extract_link(tree, item);
    if (link == NULL) { /* GCOVR_EXCL_BR_LINE: extract_link fails only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (apply_guid_permalink(tree, item, &link) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(link);                               /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                   /* GCOVR_EXCL_LINE */
    }
    return link;
}

/* Build one Entry(title, link, id, updated, published, summary, content, author) from an RSS <item>, an Atom <entry>,
   or an RDF item, each field the first present value in its precedence order. NULL only on the excluded
   allocation-failure path. */
static PyObject *build_entry(module_state *state, th_tree *tree, th_node *item) {
    static const feed_tag TITLE[] = {{"title", 5}};
    static const feed_tag UPDATED[] = {{"updated", 7}, {"lastbuilddate", 13}};
    static const feed_tag PUBLISHED[] = {{"published", 9}, {"pubdate", 7}, {"dc:date", 7}};
    static const feed_tag SUMMARY[] = {{"summary", 7}, {"description", 11}, {"dc:description", 14}};
    static const feed_tag CONTENT[] = {{"content:encoded", 15}, {"content", 7}};
    PyObject *record = PyTuple_New(8);
    if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    failed |= record_set(record, 0, field_text(tree, item, TITLE, 1));
    failed |= record_set(record, 1, entry_link(tree, item));
    failed |= record_set(record, 2, extract_id(tree, item));
    failed |= record_set(record, 3, field_text(tree, item, UPDATED, 2));
    failed |= record_set(record, 4, field_text(tree, item, PUBLISHED, 3));
    failed |= record_set(record, 5, field_text(tree, item, SUMMARY, 3));
    failed |= record_set(record, 6, field_text(tree, item, CONTENT, 2));
    failed |= record_set(record, 7, extract_author(tree, item));
    if (failed != 0) {     /* GCOVR_EXCL_BR_LINE: a field build fails only on unforceable allocation */
        Py_DECREF(record); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    PyObject *entry = PyObject_Call(state->entry_type, record, NULL);
    Py_DECREF(record);
    return entry;
}

/* The tuple of Entry records for every direct child of `parent` named [name, name + len) (item for RSS/RDF, entry for
   Atom), an empty tuple when `parent` is NULL. NULL only on the excluded allocation-failure path. */
static PyObject *build_entries(module_state *state, th_tree *tree, th_node *parent, const char *name, Py_ssize_t len) {
    PyObject *list = PyList_New(0);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (parent != NULL) {
        for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_ELEMENT || !tag_is(child, name, len)) {
                continue;
            }
            PyObject *entry = build_entry(state, tree, child);
            if (entry == NULL) { /* GCOVR_EXCL_BR_LINE: build_entry fails only on allocation */
                Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;     /* GCOVR_EXCL_LINE */
            }
            int append_failed = PyList_Append(list, entry) < 0;
            Py_DECREF(entry);
            if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
                Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;     /* GCOVR_EXCL_LINE */
            }
        }
    }
    PyObject *entries = PyList_AsTuple(list);
    Py_DECREF(list);
    return entries;
}

/* Build the Feed(type, title, link, description, updated, entries) record rooted at `root`. RSS and RDF read their feed
   metadata from the <channel> element (absent in a malformed feed, leaving the fields None); Atom reads it from the
   feed root itself. RSS nests its <item>s in the channel, RDF makes them siblings of the channel, Atom nests its
   <entry>s in the root. NULL only on the excluded allocation-failure path. */
static PyObject *build_feed(module_state *state, th_tree *tree, th_node *root, const char *type) {
    static const feed_tag TITLE[] = {{"title", 5}};
    static const feed_tag DESCRIPTION[] = {{"description", 11}, {"subtitle", 8}, {"tagline", 7}};
    static const feed_tag UPDATED[] = {{"updated", 7}, {"lastbuilddate", 13}, {"pubdate", 7}, {"dc:date", 7}};
    int is_atom = strcmp(type, "atom") == 0;
    th_node *meta = is_atom ? root : child_named(root, "channel", 7);
    th_node *item_parent = is_atom ? root : (strcmp(type, "rdf") == 0 ? root : meta);
    const char *item_name = is_atom ? "entry" : "item";
    Py_ssize_t item_len = is_atom ? 5 : 4;
    PyObject *record = PyTuple_New(6);
    if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    failed |= record_set(record, 0, PyUnicode_FromString(type));
    failed |= record_set(record, 1, field_text(tree, meta, TITLE, 1));
    failed |= record_set(record, 2, extract_link(tree, meta));
    failed |= record_set(record, 3, field_text(tree, meta, DESCRIPTION, 3));
    failed |= record_set(record, 4, field_text(tree, meta, UPDATED, 4));
    failed |= record_set(record, 5, build_entries(state, tree, item_parent, item_name, item_len));
    if (failed != 0) {     /* GCOVR_EXCL_BR_LINE: a field build fails only on unforceable allocation */
        Py_DECREF(record); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    PyObject *feed = PyObject_Call(state->feed_type, record, NULL);
    Py_DECREF(record);
    return feed;
}

/* The feed root and its format: the first <feed> (atom), <rss> (rss), or <rdf:RDF> (rdf) element in document order, or
   NULL when the document is not a feed. */
static th_node *find_feed_root(th_node *document, const char **type_out) {
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (tag_is(node, "feed", 4)) {
            *type_out = "atom";
            return node;
        }
        if (tag_is(node, "rss", 3)) {
            *type_out = "rss";
            return node;
        }
        if (tag_is(node, "rdf:rdf", 7)) {
            *type_out = "rdf";
            return node;
        }
    }
    return NULL;
}

/* Document.feed() -> Feed | None. Normalizes an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one Feed record, or
   None when the document carries no feed root. */
PyObject *turbohtml_document_feed(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_tree *tree = tree_of(self);
    module_state *state = state_of(self);
    th_node *document = ((NodeObject *)self)->node;
    PyObject *result = NULL;
    th_node *root = NULL;
    const char *type = NULL;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    root = find_feed_root(document, &type);
    if (root != NULL) {
        result = build_feed(state, tree, root, type);
    }
    Py_END_CRITICAL_SECTION();
    if (root == NULL) {
        Py_RETURN_NONE;
    }
    return result; /* Feed on success, NULL with an error set only on the excluded allocation-failure path */
}

/* Store the Feed and Entry NamedTuple classes the walk builds its results from; turbohtml._feed registers both on
   import. */
PyObject *turbohtml_register_feed(PyObject *module, PyObject *args) {
    PyObject *feed = NULL;
    PyObject *entry = NULL;
    if (!PyArg_ParseTuple(args, "OO", &feed, &entry)) { /* GCOVR_EXCL_BR_LINE: the facade always registers with two */
        return NULL;                                    /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->feed_type, Py_NewRef(feed));
    Py_XSETREF(state->entry_type, Py_NewRef(entry));
    Py_RETURN_NONE;
}
