/* Structured-data extraction over a parsed tree, the C engine behind the Document.structured_data()/json_ld()/
   opengraph()/microdata() methods (wired into the document method table in dom/document.c).

   Scrapers reach for extruct or metadata_parser to pull JSON-LD, Microdata, and OpenGraph/Twitter metadata out of a
   page. The walk that locates each format runs here in C, under the per-tree critical section so a concurrent mutation
   cannot relink the tree mid-walk. The combined method copies the document under that lock before running its format
   passes, which gives every section one document version. The gathered values hold no reference back into the tree; C
   assembles the plain dict/list/str pieces, then hands Microdata items to the MicrodataItem record class. The combined
   view goes to the StructuredData record class that the thin Python facade defines and registers on import
   (turbohtml._structured_data), so the typed result classes live in Python while all extraction stays in C. JSON-LD
   differs: a pure-C pass gathers the <script type="application/ld+json"> texts into a list of str, releases the
   critical section, and calls the stdlib json parser through the facade instead of reimplementing the JSON grammar.
   RDFa (item-shaped, following the RDFa Core/Lite processing rules for @vocab/@prefix term expansion) and Dublin Core
   (<meta name="dc.*">) join Microdata and OpenGraph in the same result; microformats2 remains a documented later phase.
 */

#include "core/ascii.h"
#include "core/common.h"
#include "core/hash.h"
#include "core/node_map.h"

#include "core/vec.h"
#include "core/work_steps.h"
#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "tokenizer/xml_names.h"
#include "dom/nodes.h"
#include "url/url.h"

#include <stdlib.h>
#include <string.h>

#define STRUCTURED_DATA_MAX_ITEM_DEPTH ((Py_ssize_t)TH_DEPTH_LIMIT(400))
/* itemref lets several items claim one nested item, and each claim builds a fresh record of it, so layers of shared
   items double the records a handful of elements ask for. A call may build one record per itemscope element, which
   covers every item nobody shares whatever the size of the page, plus this allowance for the repeats of shared ones:
   a thousand listings that each reuse ten items fit, and spending all of it takes about ten milliseconds. */
#define STRUCTURED_DATA_MAX_ITEM_REPEATS ((Py_ssize_t)10000)
/* itemref also lets any number of items claim one plain property, and every record of a shared item collects that
   item's own properties again, so items x properties values can come out of a page that holds items + properties
   elements. A property element is valued at most twice per call and later claims take the cached value; this is how
   many such later claims a call hands out. A page that shares nothing through itemref spends none: two thousand
   listings that each reuse a block of twenty properties fit. */
#define STRUCTURED_DATA_MAX_VALUE_REPEATS ((Py_ssize_t)100000)

/* The longest @vocab or @prefix IRI, in code points, that declares a mapping. Every term in scope gets its own copy of
   the mapping, so the limit caps how far one short term grows. The RDFa 1.1 initial context's longest IRI is 48. */
#define RDFA_MAX_MAPPING_LENGTH 256

/* How deep the Microdata or RDFa property elements whose value is their text content may nest in one another. Each of
   them walks its whole subtree and copies the text there into a str of its own, so a run of text is walked and copied
   once for every such element above it: without a limit a chain of them filling half the page, above text filling the
   rest, takes time and memory quadratic in the page, and the 512 levels an HTML tree may have still multiply the text
   five hundred times. Pages nest a text property in another one or two deep; a property that heads a deeper chain is
   left out of its record, the way json_ld() skips a block past its nesting ceiling. */
#define STRUCTURED_DATA_MAX_TEXT_DEPTH ((Py_ssize_t)32)

/* The HTML script type that flags a JSON-LD block, matched case-insensitively after trimming. */
static const char JSON_LD_TYPE[] = "application/ld+json";

/* Whether the UCS4 run, once surrounding ASCII whitespace is trimmed, equals `lit` ignoring ASCII case. */
static int ucs4_ieq_trimmed(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    if (end - start != lit_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < lit_len; index++) {
        Py_UCS4 c = value[start + index];
        Py_UCS4 folded = lower_ascii(c);
        if (folded != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

static int json_ld_type_with_parameters(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t start = 0;
    while (start < len && is_space(value[start])) {
        start++;
    }
    Py_ssize_t type_len = (Py_ssize_t)sizeof(JSON_LD_TYPE) - 1;
    if (len - start <= type_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < type_len; index++) {
        if (lower_ascii(value[start + index]) != (Py_UCS4)(unsigned char)JSON_LD_TYPE[index]) {
            return 0;
        }
    }
    Py_ssize_t pos = start + type_len;
    while (pos + 1 < len && is_space(value[pos])) {
        pos++;
    }
    return value[pos] == ';';
}

/* Whether the UCS4 run begins with the ASCII literal `prefix`. */
static int ucs4_has_prefix(const Py_UCS4 *value, Py_ssize_t len, const char *prefix, Py_ssize_t prefix_len) {
    if (len < prefix_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        if (value[index] != (Py_UCS4)(unsigned char)prefix[index]) {
            return 0;
        }
    }
    return 1;
}

/* Whether a property/name token names an OpenGraph or Twitter card metadata key. An array+loop keeps the branch count
   stable instead of a chained || a compiler can leave with an unreachable arm. */
static int is_social_key(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const prefixes[] = {"og:", "twitter:"};
    static const Py_ssize_t prefix_lens[] = {3, 8};
    for (size_t index = 0; index < sizeof(prefixes) / sizeof(prefixes[0]); index++) {
        if (ucs4_has_prefix(value, len, prefixes[index], prefix_lens[index])) {
            return 1;
        }
    }
    return 0;
}

/* Whether an og:/twitter: key names a URL-valued property, so an active base_url absolutizes its content. The Open
   Graph protocol types og:url and the og:image/og:audio/og:video URLs (plus their :url/:secure_url structured forms) as
   URLs, and the Twitter card spec types twitter:image and twitter:player (plus twitter:player:stream); every other key
   (title, type, description, dimensions, ...) is an opaque string left verbatim. Matched case-insensitively; an
   array+loop keeps the branch count stable. */
static int is_url_social_key(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const keys[] = {
        "og:url",
        "og:image",
        "og:image:url",
        "og:image:secure_url",
        "og:audio",
        "og:audio:url",
        "og:audio:secure_url",
        "og:video",
        "og:video:url",
        "og:video:secure_url",
        "twitter:image",
        "twitter:image:src",
        "twitter:player",
        "twitter:player:stream",
    };
    for (size_t index = 0; index < sizeof(keys) / sizeof(keys[0]); index++) {
        if (ucs4_ieq_trimmed(value, len, keys[index], (Py_ssize_t)strlen(keys[index]))) {
            return 1;
        }
    }
    return 0;
}

/* Absolutize *value against `base` in place: a relative URL joins onto base, an absolute one is left as is, an empty
   value stays empty (an absent attribute is not "the base URL"). A value the URL parser rejects (an unclosed IPv6
   bracket left in the page) stays verbatim rather than failing the whole extraction; only the caller's own base_url is
   validated up front. -1 with an error set only on the excluded allocation-failure path. */
static int resolve_in_place(PyObject *base, PyObject **value) {
    if (PyUnicode_GET_LENGTH(*value) == 0) {
        return 0;
    }
    PyObject *resolved = th_url_resolve(base, *value);
    if (resolved == NULL) {
        if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: only url_percent_encode allocation */
            return -1;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyErr_Clear(); /* a malformed URL in the page is not fatal; keep the raw value verbatim */
        return 0;
    }
    Py_SETREF(*value, resolved);
    return 0;
}

/* An explicitly empty RDFa IRI names the current document; other extractors keep empty URL fields empty. */
static int resolve_rdfa_in_place(PyObject *base, PyObject **value) {
    if (PyUnicode_GET_LENGTH(*value) == 0) {
        Py_SETREF(*value, Py_NewRef(base));
        return 0;
    }
    return resolve_in_place(base, value);
}

/* A named attribute's value as a new str, the empty string when the attribute is absent or valueless (the way
   getAttribute reports a missing URL attribute as ""). NULL only on the excluded allocation-failure path. */
static PyObject *attr_str_or_empty(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    if (index < 0 || node->attrs[index].value == NULL) {
        return PyUnicode_FromString("");
    }
    return ucs4_to_str(node->attrs[index].value, node->attrs[index].value_len);
}

/* The element tags whose Microdata property value is a single attribute rather than text content. `url` marks the
   URL-valued attributes (a/area/link href, media src, object data), whose value the spec resolves as a URL and so
   strips of surrounding ASCII whitespace; data/meter value and meta content are taken verbatim. */
typedef struct {
    uint16_t atom;
    const char *attr;
    Py_ssize_t attr_len;
    unsigned char url;
} microdata_attr_prop;

static const microdata_attr_prop MICRODATA_ATTR_PROPS[] = {
    {TH_TAG_AUDIO, "src", 3, 1},   {TH_TAG_EMBED, "src", 3, 1},    {TH_TAG_IFRAME, "src", 3, 1},
    {TH_TAG_IMG, "src", 3, 1},     {TH_TAG_SOURCE, "src", 3, 1},   {TH_TAG_TRACK, "src", 3, 1},
    {TH_TAG_VIDEO, "src", 3, 1},   {TH_TAG_A, "href", 4, 1},       {TH_TAG_AREA, "href", 4, 1},
    {TH_TAG_LINK, "href", 4, 1},   {TH_TAG_OBJECT, "data", 4, 1},  {TH_TAG_DATA, "value", 5, 0},
    {TH_TAG_METER, "value", 5, 0}, {TH_TAG_META, "content", 7, 0},
};

typedef struct {
    th_node **slots;
    size_t mask;
    int built;
} micro_id_index;

typedef struct {
    th_node **items;
    Py_ssize_t len;
    Py_ssize_t cap;
} node_stack;

/* The property elements of the document, indexed once for every item that claims some through itemref. An item names
   elements by id and takes the properties each one holds: the element itself and its descendants, down to but not into
   a nested item. What an element holds is the same for every item that names it, so it is read off here instead of
   crawled per item, and the order of two properties is a comparison of their ranks instead of a walk between them. */
typedef struct {
    th_node_map ranks; /* property element -> its 1-based position among the document's properties in tree order */
    node_stack claims; /* the property elements, ordered so that those one element holds are one run */
    th_node_map first; /* element with an id -> 1-based start of its run in claims */
    th_node_map past;  /* element with an id -> 1-based end of its run in claims */
    int built;
} micro_prop_index;

typedef struct {
    module_state *state;
    th_tree *tree;
    th_node *document; /* the node microdata() was called on: the items, the ids and the properties all lie under it */
    PyObject *base;
    micro_id_index ids;
    micro_prop_index props;
    node_stack active_items;
    const Py_UCS4 *named_value;   /* the itemref value resolve_named_runs resolved last, by its buffer */
    struct micro_run *named_runs; /* that value's runs of props.claims */
    Py_ssize_t named_count;
    Py_ssize_t walk_budget; /* work the item walks may still do; negative once the call uses the property index */
    Py_ssize_t records_left;
    int funded;         /* whether the document's itemscope elements have topped up records_left */
    Py_ssize_t *valued; /* node seq of a property element -> 1-based index into values, once itemref is in play */
    PyObject *values;   /* list of those values: each a str, or None for a property microdata_text leaves out */
    Py_ssize_t value_repeats_left;
} micro_ctx;

static PyObject *build_item(micro_ctx *ctx, th_node *element);

/* Whether the property elements that take their text content as their value nest more than
   STRUCTURED_DATA_MAX_TEXT_DEPTH deep at `property`, which is one itself. The caller builds no text value this refuses,
   so a node is walked and copied for at most that many property elements above it; the walk here stops at the first
   element past the limit and visits a node no more often. The count follows the tree alone: the text below a property
   is copied whichever item claims it, so an item boundary or an itemref between two of them does not start it again. */
static int text_properties_too_deep(th_tree *tree, th_node *property, int (*is_text_property)(th_tree *, th_node *)) {
    Py_ssize_t depth = 1;
    th_node *current = property->first_child;
    while (current != NULL) {
        const int nested = current->type == TH_NODE_ELEMENT && is_text_property(tree, current);
        if (nested && depth == STRUCTURED_DATA_MAX_TEXT_DEPTH) {
            return 1;
        }
        if (current->first_child != NULL) {
            depth += nested;
            current = current->first_child;
            continue;
        }
        while (current != property && current->next_sibling == NULL) {
            current = current->parent;
            depth -= current != property && current->type == TH_NODE_ELEMENT && is_text_property(tree, current);
        }
        current = current == property ? NULL : current->next_sibling;
    }
    return 0;
}

/* Whether `element` is a Microdata property whose value microdata_value takes from its text content: it names a
   property, is no item, and its tag reads the value from no attribute. */
static int microdata_text_property(th_tree *tree, th_node *element) {
    const th_node_attr *itemprop = find_node_attr(element, TH_ATTR_ITEMPROP);
    if (itemprop == NULL || itemprop->value == NULL || find_node_attr(element, TH_ATTR_ITEMSCOPE) != NULL) {
        return 0;
    }
    if (element->atom == TH_TAG_TIME) {
        Py_ssize_t datetime = th_node_attr_find(tree, element, "datetime", 8);
        return datetime < 0 || element->attrs[datetime].value == NULL;
    }
    for (size_t index = 0; index < sizeof(MICRODATA_ATTR_PROPS) / sizeof(MICRODATA_ATTR_PROPS[0]); index++) {
        if (element->atom == MICRODATA_ATTR_PROPS[index].atom) {
            return 0;
        }
    }
    return 1;
}

/* The text content of a Microdata property element as its value, or None when text-valued properties nest past
   STRUCTURED_DATA_MAX_TEXT_DEPTH in it and the property is to be left out. NULL only on the excluded allocation-failure
   path. */
static PyObject *microdata_text(micro_ctx *ctx, th_node *element) {
    if (text_properties_too_deep(ctx->tree, element, microdata_text_property)) {
        return Py_NewRef(Py_None);
    }
    return str_from_accessor(th_node_text, ctx->tree, element);
}

/* A URL-valued attribute's value with leading and trailing ASCII whitespace stripped (the URL parser trims it), or the
   empty string when the attribute is absent or valueless. NULL only on the excluded allocation-failure path. */
static PyObject *attr_url_or_empty(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    if (index < 0 || node->attrs[index].value == NULL) {
        return PyUnicode_FromString("");
    }
    const Py_UCS4 *value = node->attrs[index].value;
    Py_ssize_t start = 0;
    Py_ssize_t end = node->attrs[index].value_len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    return ucs4_to_str(value + start, end - start);
}

/* A named attribute's value as a new str, or None when the attribute is absent or valueless. NULL only on the excluded
   allocation-failure path. */
static PyObject *attr_value_or_none(const th_node_attr *attr) {
    if (attr == NULL || attr->value == NULL) {
        return Py_NewRef(Py_None);
    }
    return ucs4_to_str(attr->value, attr->value_len);
}

/* One Microdata property element's value: a nested MicrodataItem when it carries itemscope, otherwise the HTML
   Microdata value algorithm (a URL attribute for the link/media tags, datetime or text for <time>, content for <meta>,
   else the element's text content). A URL-valued attribute is absolutized against `base` when the caller passed one
   (base is NULL otherwise, leaving values verbatim). None for text content microdata_text refuses. NULL only on the
   excluded allocation-failure path. */
static PyObject *microdata_value(micro_ctx *ctx, th_node *element) {
    TH_WORK_STEP();
    if (find_node_attr(element, TH_ATTR_ITEMSCOPE) != NULL) {
        return build_item(ctx, element);
    }
    if (element->atom == TH_TAG_TIME) {
        Py_ssize_t datetime = th_node_attr_find(ctx->tree, element, "datetime", 8);
        if (datetime >= 0 && element->attrs[datetime].value != NULL) {
            return ucs4_to_str(element->attrs[datetime].value, element->attrs[datetime].value_len);
        }
        return microdata_text(ctx, element);
    }
    for (size_t index = 0; index < sizeof(MICRODATA_ATTR_PROPS) / sizeof(MICRODATA_ATTR_PROPS[0]); index++) {
        const microdata_attr_prop *prop = &MICRODATA_ATTR_PROPS[index];
        if (element->atom == prop->atom) {
            if (!prop->url) {
                return attr_str_or_empty(ctx->tree, element, prop->attr, prop->attr_len);
            }
            PyObject *value = attr_url_or_empty(ctx->tree, element, prop->attr, prop->attr_len);
            if (value == NULL) { /* GCOVR_EXCL_BR_LINE: attr_url_or_empty only fails on allocation */
                return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (ctx->base != NULL) {
                if (resolve_in_place(ctx->base, &value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    Py_DECREF(value);                          /* GCOVR_EXCL_LINE: allocation-failure path */
                    return NULL;                               /* GCOVR_EXCL_LINE */
                }
            }
            return value;
        }
    }
    return microdata_text(ctx, element);
}

/* A run of more names than this is checked against a set of the earlier ones, so the check stays linear in the
   attribute, which the page controls and itemref hands to every item naming the element. A shorter run, which is what
   an ordinary itemprop is, compares each name with the ones before it and never allocates a set. */
#define STRUCTURED_DATA_MAX_SCANNED_NAMES ((Py_ssize_t)8)

/* Whether the token at `index` repeats an earlier one: an element's property names are its itemprop tokens with the
   duplicates removed, so itemprop="a a" adds its value under "a" once. */
static int token_repeats(PyObject *tokens, Py_ssize_t index) {
    PyObject *token = PyList_GET_ITEM(tokens, index);
    for (Py_ssize_t earlier = 0; earlier < index; earlier++) {
        TH_WORK_STEP();
        if (PyUnicode_Compare(PyList_GET_ITEM(tokens, earlier), token) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Whether `token` repeats one already in `seen`, recording it when it does not. -1 only on the excluded
   allocation-failure path. */
static int token_seen(PyObject *seen, PyObject *token) {
    Py_ssize_t before = PySet_GET_SIZE(seen);
    if (PySet_Add(seen, token) < 0) { /* GCOVR_EXCL_BR_LINE: a str insert only fails on allocation failure */
        return -1;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return PySet_GET_SIZE(seen) == before;
}

/* Append `value` under every distinct whitespace-separated name in the itemprop run to the properties dict, each name
   mapping to a list of values in document order. Returns the number of names it filed the value under, or -1 only on
   the excluded allocation-failure path. */
static Py_ssize_t add_property(PyObject *properties, const Py_UCS4 *names, Py_ssize_t names_len, PyObject *value) {
    PyObject *tokens = split_token_list(names, names_len);
    if (tokens == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = PyList_GET_SIZE(tokens);
    Py_ssize_t added = 0;
    PyObject *seen = NULL;
    if (count > STRUCTURED_DATA_MAX_SCANNED_NAMES) {
        seen = PySet_New(NULL);
        if (seen == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto error;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        TH_WORK_STEP();
        PyObject *key = PyList_GET_ITEM(tokens, index);
        int repeats = seen == NULL ? token_repeats(tokens, index) : token_seen(seen, key);
        if (repeats < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto error;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (repeats) {
            continue;
        }
        PyObject *bucket = PyDict_GetItemWithError(properties, key);
        if (bucket != NULL) {
            Py_INCREF(bucket);
        } else {
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lookup fails only on an unforceable error */
                goto error;         /* GCOVR_EXCL_LINE: error path */
            }
            bucket = PyList_New(0);
            if (bucket == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                goto error;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (PyDict_SetItem(properties, key, bucket) < 0) { /* GCOVR_EXCL_BR_LINE: insert fails only on alloc */
                Py_DECREF(bucket);                             /* GCOVR_EXCL_LINE: allocation-failure path */
                goto error;                                    /* GCOVR_EXCL_LINE */
            }
        }
        int append_failed = PyList_Append(bucket, value) < 0;
        Py_DECREF(bucket);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            goto error;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        added++;
    }
    Py_XDECREF(seen);
    Py_DECREF(tokens);
    return added;
error:                 /* GCOVR_EXCL_LINE: shared cleanup for the unreachable allocation-failure arms */
    Py_XDECREF(seen);  /* GCOVR_EXCL_LINE */
    Py_DECREF(tokens); /* GCOVR_EXCL_LINE */
    return -1;         /* GCOVR_EXCL_LINE */
}

/* Append `node`; -1 only on the excluded allocation-failure path. */
static int node_stack_push(node_stack *stack, th_node *node) {
    if (stack->len == stack->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(stack->len + 1), (size_t)stack->cap, 8, sizeof(th_node *), &cap, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_node **items = PyMem_Realloc(stack->items, bytes);
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        stack->items = items;
        stack->cap = (Py_ssize_t)cap;
    }
    stack->items[stack->len++] = node;
    return 0;
}

static int node_stack_contains(const node_stack *stack, const th_node *node) {
    for (Py_ssize_t index = 0; index < stack->len; index++) {
        if (stack->items[index] == node) {
            return 1;
        }
    }
    return 0;
}

static uint32_t micro_id_hash(const Py_UCS4 *id, Py_ssize_t id_len) {
    return th_name_hash(id, id_len * (Py_ssize_t)sizeof(Py_UCS4)); /* keyed: the page picks its ids */
}

static int micro_id_index_build(micro_id_index *index, th_node *document) {
    Py_ssize_t count = 0;
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type == TH_NODE_ELEMENT) {
            const th_node_attr *id = find_node_attr(node, TH_ATTR_ID);
            count += id != NULL && id->value != NULL;
        }
    }
    index->built = 1;
    if (count == 0) {
        return 0;
    }
    if ((size_t)count > SIZE_MAX / 2) { /* GCOVR_EXCL_BR_LINE: the document cannot contain SIZE_MAX / 2 nodes */
        return -1;                      /* GCOVR_EXCL_LINE: unreachable size-overflow path */
    }
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)count * 2, 0, 16, sizeof(th_node *), &cap, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: the document cannot contain enough nodes to overflow */
        return -1; /* GCOVR_EXCL_LINE: unreachable size-overflow path */
    }
    index->slots = PyMem_Calloc(1, bytes);
    if (index->slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    index->mask = cap - 1;
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        const th_node_attr *id = find_node_attr(node, TH_ATTR_ID);
        if (id == NULL || id->value == NULL) {
            continue;
        }
        size_t probe = micro_id_hash(id->value, id->value_len) & index->mask;
        int duplicate = 0;
        while (index->slots[probe] != NULL) {
            const th_node_attr *stored = find_node_attr(index->slots[probe], TH_ATTR_ID);
            if (stored->value_len == id->value_len &&
                memcmp(stored->value, id->value, (size_t)id->value_len * sizeof(Py_UCS4)) == 0) {
                duplicate = 1;
                break;
            }
            TH_WORK_STEP();
            probe = (probe + 1) & index->mask;
        }
        if (!duplicate) {
            index->slots[probe] = node;
        }
    }
    return 0;
}

/* The first element in `document` whose id equals [id, id + id_len), matching getElementById's duplicate handling. */
static int micro_id_index_find(micro_id_index *index, th_node *document, const Py_UCS4 *id, Py_ssize_t id_len,
                               th_node **result) {
    *result = NULL;
    if (!index->built && micro_id_index_build(index, document) < 0) { /* GCOVR_EXCL_BR_LINE: allocation only */
        return -1;                                                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (index->slots == NULL) {
        return 0;
    }
    for (size_t probe = micro_id_hash(id, id_len) & index->mask; index->slots[probe] != NULL;
         probe = (probe + 1) & index->mask) {
        TH_WORK_STEP();
        const th_node_attr *stored = find_node_attr(index->slots[probe], TH_ATTR_ID);
        if (stored->value_len == id_len && memcmp(stored->value, id, (size_t)id_len * sizeof(Py_UCS4)) == 0) {
            *result = index->slots[probe];
            return 0;
        }
    }
    return 0;
}

/* Push every id in root's itemref attribute onto `pending`, resolving each token to the first element carrying it (an
   unresolved token is skipped, per the spec). -1 only on the excluded allocation-failure path. */
static int push_itemref_targets(micro_ctx *ctx, const th_node_attr *itemref, node_stack *pending) {
    const Py_UCS4 *const value = itemref->value;
    const Py_ssize_t value_len = itemref->value_len;
    Py_ssize_t cursor = 0;
    while (cursor < value_len) {
        while (cursor < value_len && is_space(value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < value_len && !is_space(value[cursor])) {
            cursor++;
        }
        if (cursor > start) {
            th_node *target;
            int found = micro_id_index_find(&ctx->ids, ctx->document, &value[start], cursor - start, &target);
            if (found < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                return -1;   /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (target != NULL) {
                if (node_stack_push(pending, target) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    return -1;                              /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
        }
    }
    return 0;
}

/* Whether itemref can name the node: the id index holds only elements whose id has a value. */
static int has_id(th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    const th_node_attr *const id = find_node_attr(node, TH_ATTR_ID);
    return id != NULL && id->value != NULL;
}

/* Whether the element is a property. An empty (itemprop="") or valueless (itemprop) attribute carries value == NULL,
   so a non-NULL value already names at least one property; no separate length check is needed. */
static int names_property(th_node *element) {
    const th_node_attr *const itemprop = find_node_attr(element, TH_ATTR_ITEMPROP);
    return itemprop != NULL && itemprop->value != NULL;
}

/* Index the properties under `document`. The crawl that collects an element's properties descends through element
   children and stops at an itemscope, whose descendants belong to that nested item, so the elements fall into trees
   that start below the document, below each item and below each node that is not an element (a template's contents).
   Walking one such tree at a time puts the properties of every subtree of it side by side in claims: the run of an
   element opens when the walk enters it and closes when the walk leaves it. A node is visited once for the ranks and
   once for the runs, so the index costs two passes over the document however many items share what it records. -1
   only on the excluded allocation-failure path. */
static int micro_prop_index_build(micro_prop_index *index, th_node *document) {
    node_stack scopes = {NULL, 0, 0};
    int status = -1;
    index->built = 1;
    Py_ssize_t rank = 0;
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type == TH_NODE_ELEMENT && names_property(node)) {
            if (th_node_map_insert(&index->ranks, node, ++rank) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                goto done;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    if (node_stack_push(&scopes, document) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    while (scopes.len > 0) {
        th_node *const scope = scopes.items[--scopes.len];
        th_node *current = scope->first_child;
        while (current != NULL) {
            int descend = 0;
            if (current->type == TH_NODE_ELEMENT) {
                /* GCOVR_EXCL_BR_START: allocation failure */
                if ((has_id(current) && th_node_map_insert(&index->first, current, index->claims.len + 1) < 0) ||
                    (names_property(current) && node_stack_push(&index->claims, current) < 0)) {
                    goto done; /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                /* GCOVR_EXCL_BR_STOP */
                descend = find_node_attr(current, TH_ATTR_ITEMSCOPE) == NULL;
            }
            if (current->first_child != NULL) {
                if (descend) {
                    current = current->first_child;
                    continue;
                }
                if (node_stack_push(&scopes, current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    goto done;                               /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
            for (;;) { /* the walk leaves current, then every parent whose last child it was */
                /* GCOVR_EXCL_BR_START: allocation failure */
                if (has_id(current) && th_node_map_insert(&index->past, current, index->claims.len + 1) < 0) {
                    goto done; /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                /* GCOVR_EXCL_BR_STOP */
                if (current->next_sibling != NULL) {
                    current = current->next_sibling;
                    break;
                }
                current = current->parent;
                if (current == scope) {
                    current = NULL;
                    break;
                }
            }
        }
    }
    status = 0;
done:
    PyMem_Free(scopes.items);
    return status;
}

/* A run of micro_prop_index.claims, and a property with its rank: the two records the item crawl sorts, each by the
   Py_ssize_t it leads with. */
typedef struct micro_run {
    Py_ssize_t first;
    Py_ssize_t past;
} micro_run;

typedef struct {
    Py_ssize_t rank;
    th_node *node;
} ranked_node;

static int leading_ssize_cmp(const void *left, const void *right) {
    const Py_ssize_t left_key = *(const Py_ssize_t *)left;
    const Py_ssize_t right_key = *(const Py_ssize_t *)right;
    return (left_key > right_key) - (left_key < right_key);
}

/* Put `results` in tree order, the order the spec sorts an item's properties into, and drop the repeats: a property
   under the item that its itemref names as well. -1 only on the excluded allocation-failure path. */
static int sort_properties(const micro_prop_index *index, node_stack *results) {
    ranked_node *ranked = PyMem_Malloc((size_t)results->len * sizeof(ranked_node));
    if (ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int ordered = 1;
    for (Py_ssize_t at = 0; at < results->len; at++) {
        ranked[at] = (ranked_node){th_node_map_find(&index->ranks, results->items[at]), results->items[at]};
        ordered &= at == 0 || ranked[at - 1].rank < ranked[at].rank;
    }
    if (!ordered) {
        qsort(ranked, (size_t)results->len, sizeof(ranked_node), leading_ssize_cmp);
        Py_ssize_t kept = 0;
        for (Py_ssize_t at = 0; at < results->len; at++) {
            if (at == 0 || ranked[at - 1].rank != ranked[at].rank) {
                results->items[kept++] = ranked[at].node;
            }
        }
        results->len = kept;
    }
    PyMem_Free(ranked);
    return 0;
}

/* Without itemref, preorder visits each property once in tree order, so no visited set or sort is needed. */
static int crawl_local_properties(th_node *root, node_stack *results) {
    th_node *current = root->first_child;
    while (current != NULL) {
        if (current->type == TH_NODE_ELEMENT) {
            const th_node_attr *const itemprop = find_node_attr(current, TH_ATTR_ITEMPROP);
            if (itemprop != NULL && itemprop->value != NULL) {
                if (node_stack_push(results, current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    return -1;                               /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
            if (find_node_attr(current, TH_ATTR_ITEMSCOPE) == NULL && current->first_child != NULL) {
                current = current->first_child;
                continue;
            }
        }
        while (current != root && current->next_sibling == NULL) {
            current = current->parent;
        }
        current = current == root ? NULL : current->next_sibling;
    }
    return 0;
}

/* Resolve an itemref value to the runs of the property index its tokens name, as their union: sorted, disjoint and
   non-empty, so walking them costs the properties they hold and nothing per token. The result is kept in `ctx` for the
   next item that carries the same value buffer: the tree builder reopens an unclosed formatting element in every block
   that follows it, each clone borrows the start tag's attribute values, and resolving a long itemref again for every
   clone is items x tokens. -1 only on the excluded allocation-failure path. */
static int resolve_named_runs(micro_ctx *ctx, const th_node_attr *itemref) {
    micro_prop_index *const index = &ctx->props;
    node_stack targets = {NULL, 0, 0};
    micro_run *runs = NULL;
    Py_ssize_t merged = 0;
    int status = -1;
    if (push_itemref_targets(ctx, itemref, &targets) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (targets.len > 1) {
        /* an itemref may name one element any number of times; a run per token would be sorted for each */
        th_node_map named = {0};
        Py_ssize_t kept = 0;
        for (Py_ssize_t at = 0; at < targets.len; at++) {
            if (th_node_map_find(&named, targets.items[at]) != 0) {
                continue;
            }
            if (th_node_map_insert(&named, targets.items[at], 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyMem_Free(named.entries);                              /* GCOVR_EXCL_LINE */
                goto done;                                              /* GCOVR_EXCL_LINE */
            }
            targets.items[kept++] = targets.items[at];
        }
        PyMem_Free(named.entries);
        targets.len = kept;
    }
    if (targets.len > 0) {
        if (!index->built && micro_prop_index_build(index, ctx->document) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
            goto done;                                                           /* GCOVR_EXCL_LINE */
        }
        runs = PyMem_Malloc((size_t)targets.len * sizeof(micro_run));
        if (runs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto done;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t at = 0; at < targets.len; at++) {
            runs[at].first = th_node_map_find(&index->first, targets.items[at]) - 1;
            runs[at].past = th_node_map_find(&index->past, targets.items[at]) - 1;
        }
        /* Runs nest when one named element holds another, so take their union: sorted by start, each run adds only
           what lies past the runs before it. */
        qsort(runs, (size_t)targets.len, sizeof(micro_run), leading_ssize_cmp);
        Py_ssize_t covered = 0;
        for (Py_ssize_t at = 0; at < targets.len; at++) {
            const Py_ssize_t first = runs[at].first > covered ? runs[at].first : covered;
            if (first < runs[at].past) {
                covered = runs[at].past;
                runs[merged++] = (micro_run){first, covered};
            }
        }
    }
    PyMem_Free(ctx->named_runs);
    ctx->named_runs = runs;
    ctx->named_count = merged;
    ctx->named_value = itemref->value;
    runs = NULL;
    status = 0;
done:
    PyMem_Free(targets.items);
    PyMem_Free(runs);
    return status;
}

/* The item walks of one call may do this much work per node of the tree, counting the nodes they visit, the children
   they scan, the itemref characters they read and the nodes they step over to order properties. Building the property
   index reads a few attributes of every node in two passes, so walks that cost more than that hand over to it. */
#define STRUCTURED_DATA_WALK_BUDGET_PER_NODE ((Py_ssize_t)4)

/* Take `units` of work from the call's walk budget; nonzero once the budget is spent. */
static int walk_spend(micro_ctx *ctx, Py_ssize_t units) {
    TH_WORK_STEPS_ADD(units);
    ctx->walk_budget -= units;
    return ctx->walk_budget < 0;
}

/* Depth of `node` below the document root, its number of ancestors. */
static Py_ssize_t node_depth(th_node *node) {
    Py_ssize_t depth = 0;
    for (th_node *parent = node->parent; parent != NULL; parent = parent->parent) {
        depth++;
    }
    return depth;
}

/* Whether `left` precedes `right` in tree order, -1 or 1: two distinct nodes of one document. The ancestors and
   siblings it steps over come out of the walk budget. */
static int node_before(micro_ctx *ctx, th_node *left, th_node *right) {
    if (left->next_sibling == right || left->first_child == right) {
        return -1;
    }
    if (right->next_sibling == left || right->first_child == left) {
        return 1;
    }
    Py_ssize_t left_depth = node_depth(left);
    Py_ssize_t right_depth = node_depth(right);
    walk_spend(ctx, left_depth + right_depth);
    th_node *walk_left = left;
    th_node *walk_right = right;
    for (Py_ssize_t index = left_depth; index > right_depth; index--) {
        walk_left = walk_left->parent;
    }
    for (Py_ssize_t index = right_depth; index > left_depth; index--) {
        walk_right = walk_right->parent;
    }
    if (walk_left == walk_right) {
        return left_depth < right_depth ? -1 : 1;
    }
    while (walk_left->parent != walk_right->parent) {
        walk_left = walk_left->parent;
        walk_right = walk_right->parent;
    }
    for (th_node *sibling = walk_left->next_sibling; sibling != NULL; sibling = sibling->next_sibling) {
        walk_spend(ctx, 1);
        if (sibling == walk_right) {
            return -1;
        }
    }
    return 1;
}

/* Add the element children of `parent` to `pending`, paying for each child out of the walk budget. -1 only on the
   excluded allocation-failure path. */
static int push_element_children(micro_ctx *ctx, th_node *parent, node_stack *pending) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        walk_spend(ctx, 1);
        if (child->type == TH_NODE_ELEMENT) {
            if (node_stack_push(pending, child) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                return -1;                             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    return 0;
}

/* Below this many properties a walk out of tree order is sorted by insertion; a longer one is left to the index. */
#define STRUCTURED_DATA_MAX_INSERTION_SORT ((Py_ssize_t)32)

/* Put the walked `results` into tree order. The walk's stack hands them over in or against tree order unless a named
   element holds or precedes another, so one pass over neighbors settles the common case. Returns 1, with `results` to
   be discarded, for a long list in neither order or once the comparisons spend the walk budget. */
static int order_walked_properties(micro_ctx *ctx, node_stack *results) {
    th_node **const items = results->items;
    const Py_ssize_t len = results->len;
    if (len < 2) {
        return 0;
    }
    const int direction = node_before(ctx, items[0], items[1]);
    Py_ssize_t at = 2;
    while (at < len && ctx->walk_budget >= 0 && node_before(ctx, items[at - 1], items[at]) == direction) {
        at++;
    }
    if (at == len) {
        for (Py_ssize_t low = 0, high = len - 1; direction > 0 && low < high; low++, high--) {
            th_node *const node = items[low];
            items[low] = items[high];
            items[high] = node;
        }
        return ctx->walk_budget < 0;
    }
    if (len >= STRUCTURED_DATA_MAX_INSERTION_SORT) {
        return 1;
    }
    for (Py_ssize_t next = 1; next < len; next++) {
        th_node *const node = items[next];
        Py_ssize_t slot = next;
        while (slot > 0 && ctx->walk_budget >= 0 && node_before(ctx, items[slot - 1], node) > 0) {
            items[slot] = items[slot - 1];
            slot--;
        }
        items[slot] = node;
    }
    return ctx->walk_budget < 0;
}

/* The item's properties by walking its subtree and the elements its itemref names, into `results` in tree order: the
   spec's crawl, paid for out of the call's walk budget. Returns 1, with `results` to be discarded, once the budget is
   spent or the order needs the index; -1 only on the excluded allocation-failure path. */
static int walk_item_properties(micro_ctx *ctx, th_node *root, const th_node_attr *itemref, node_stack *results) {
    if (walk_spend(ctx, itemref->value_len)) {
        return 1;
    }
    th_node_map memory = {0};
    node_stack pending = {NULL, 0, 0};
    int status = -1;
    if (th_node_map_insert(&memory, root, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (push_element_children(ctx, root, &pending) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (push_itemref_targets(ctx, itemref, &pending) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    while (pending.len > 0) {
        if (walk_spend(ctx, 1)) {
            status = 1;
            goto done;
        }
        th_node *current = pending.items[--pending.len];
        if (th_node_map_find(&memory, current) != 0) {
            continue; /* already walked: an itemref cycle, or an element named twice */
        }
        if (th_node_map_insert(&memory, current, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            goto done;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (find_node_attr(current, TH_ATTR_ITEMSCOPE) == NULL) {
            if (push_element_children(ctx, current, &pending) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                goto done;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        const th_node_attr *itemprop = find_node_attr(current, TH_ATTR_ITEMPROP);
        if (itemprop != NULL && itemprop->value != NULL) {
            if (node_stack_push(results, current) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                goto done;                               /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    status = order_walked_properties(ctx, results);
done:
    PyMem_Free(memory.entries);
    PyMem_Free(pending.items);
    return status;
}

/* Gather the property elements of the item rooted at `root` into `results`, following the WHATWG "the properties of an
   item" algorithm: crawl root's descendants plus every element its itemref names, stopping at a nested itemscope
   (whose descendants belong to that nested item), taking each element at most once and never root itself, then sort
   into tree order. While the call's walk budget lasts, the walk answers the item alone; once a walk spends it, that
   item and every later one take the property index, where a named element adds its run of the index, so an item costs
   its own subtree and the properties it receives, not the size of the subtrees it names or of the document between
   them. -1 only on the excluded allocation-failure path. */
static int crawl_item_properties(micro_ctx *ctx, th_node *root, const th_node_attr *itemref, node_stack *results) {
    if (ctx->walk_budget >= 0) {
        const int walked = walk_item_properties(ctx, root, itemref, results);
        if (walked < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (walked == 0) {
            return 0;
        }
        ctx->walk_budget = -1;
        results->len = 0;
    }
    if (crawl_local_properties(root, results) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* a value buffer is allocated once with its length and not rewritten during the call, so its address alone
       names the value */
    if (itemref->value != ctx->named_value) {
        if (resolve_named_runs(ctx, itemref) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return -1;                              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (ctx->named_count == 0) {
        return 0; /* nothing is named, and the local crawl is in tree order already */
    }
    for (Py_ssize_t at = 0; at < ctx->named_count; at++) {
        for (Py_ssize_t claim = ctx->named_runs[at].first; claim < ctx->named_runs[at].past; claim++) {
            th_node *const property = ctx->props.claims.items[claim];
            /* an item is not its own property, though an element its itemref names may hold it */
            if (property != root && node_stack_push(results, property) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
                return -1;                                                    /* GCOVR_EXCL_LINE */
            }
        }
    }
    return sort_properties(&ctx->props, results);
}

/* Take `count` repeated values out of the call's allowance. -1 with RecursionError set when fewer are left. */
static int spend_value_repeats(micro_ctx *ctx, Py_ssize_t count) {
    if (count > ctx->value_repeats_left) {
        PyErr_Format(PyExc_RecursionError,
                     "microdata() supports at most %zd repeats of property values shared through itemref",
                     STRUCTURED_DATA_MAX_VALUE_REPEATS);
        return -1;
    }
    ctx->value_repeats_left -= count;
    return 0;
}

/* The value of a property that is no item. Once itemref is in play, a claim after the first valuation takes the cached
   str instead of walking and copying the subtree again, out of the STRUCTURED_DATA_MAX_VALUE_REPEATS allowance;
   *repeat tells such a claim. A property valued before the first itemref lookup is not cached, so its first shared
   claim values it again. NULL with RecursionError set once the allowance is spent, and on the excluded
   allocation-failure path. */
static PyObject *shared_microdata_value(micro_ctx *ctx, th_node *property, int *repeat) {
    if (ctx->valued == NULL) {
        ctx->valued = PyMem_Calloc((size_t)th_tree_node_count(ctx->tree), sizeof(Py_ssize_t));
        if (ctx->valued == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    const Py_ssize_t known = ctx->valued[property->seq];
    *repeat = known != 0;
    if (known != 0) {
        if (spend_value_repeats(ctx, 1) < 0) {
            return NULL;
        }
        return Py_NewRef(PyList_GET_ITEM(ctx->values, known - 1));
    }
    PyObject *value = microdata_value(ctx, property);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: a value that is no item fails only on allocation */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (ctx->values == NULL) {
        ctx->values = PyList_New(0);
    }
    /* GCOVR_EXCL_BR_START: the list fails only on allocation */
    if (ctx->values == NULL || PyList_Append(ctx->values, value) < 0) {
        Py_DECREF(value); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    ctx->valued[property->seq] = PyList_GET_SIZE(ctx->values);
    return value;
}

/* Crawl the properties of the item rooted at `element` into `properties`, in document (tree) order per the spec's
   final sort, each itemprop name mapping to its list of values. A property whose text content microdata_text refuses
   is left out. -1 with RecursionError set past a limit of the call, or on the excluded allocation-failure path. */
static int collect_properties(micro_ctx *ctx, th_node *element, PyObject *properties) {
    node_stack results = {NULL, 0, 0};
    int status = -1;
    const Py_ssize_t itemref = th_node_attr_find(ctx->tree, element, "itemref", 7);
    if (itemref < 0 || element->attrs[itemref].value == NULL) {
        if (crawl_local_properties(element, &results) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            goto done;                                       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    } else {
        const int crawled = crawl_item_properties(ctx, element, &element->attrs[itemref], &results);
        if (crawled < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            goto done;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    for (Py_ssize_t index = 0; index < results.len; index++) {
        th_node *property = results.items[index];
        const th_node_attr *itemprop = find_node_attr(property, TH_ATTR_ITEMPROP);
        /* only itemref gives a property a second claimant, and the first lookup of one builds the id index */
        const int shareable = ctx->ids.built && find_node_attr(property, TH_ATTR_ITEMSCOPE) == NULL;
        int repeat = 0;
        PyObject *value = shareable ? shared_microdata_value(ctx, property, &repeat) : microdata_value(ctx, property);
        if (value == NULL) {
            goto done;
        }
        Py_ssize_t added = 0;
        if (value != Py_None) {
            added = add_property(properties, itemprop->value, itemprop->value_len, value);
        }
        Py_DECREF(value);
        if (added < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            goto done;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* the claim paid for one entry; an element that names several properties adds one per further name */
        if (repeat && added > 1 && spend_value_repeats(ctx, added - 1) < 0) {
            goto done;
        }
    }
    status = 0;
done:
    PyMem_Free(results.items);
    return status;
}

/* Steal `value` into slot `index` of `tuple`, returning -1 when its section builder failed. */
static int tuple_set_or_fail(PyObject *tuple, Py_ssize_t index, PyObject *value) {
    if (value == NULL) {
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: brace of the allocation-failure branch */
        return -1;
    }
    PyTuple_SET_ITEM(tuple, index, value);
    return 0;
}

/* Take one record out of the call's budget, 0 once it is spent. The budget starts as the shared-item allowance, which
   few pages exhaust, so only the first time it runs dry do the document's itemscope elements top it up with the one
   record each builds when nobody shares it. */
static int claim_record(micro_ctx *ctx) {
    if (ctx->records_left == 0 && !ctx->funded) {
        ctx->funded = 1;
        for (th_node *node = ctx->document->first_child; node != NULL; node = preorder_next(node, ctx->document)) {
            ctx->records_left += node->type == TH_NODE_ELEMENT && find_node_attr(node, TH_ATTR_ITEMSCOPE) != NULL;
        }
    }
    if (ctx->records_left == 0) {
        return 0;
    }
    ctx->records_left--;
    return 1;
}

/* Build one MicrodataItem(type, id, properties), bounding the item graph rather than the DOM depth because itemref can
   connect shallow elements into a recursive graph, and the records of the call because itemref can reach one element
   along many paths of that graph. */
static PyObject *build_item(micro_ctx *ctx, th_node *element) {
    if (node_stack_contains(&ctx->active_items, element)) {
        PyErr_SetString(PyExc_RecursionError, "microdata() found a cyclic nested item graph");
        return NULL;
    }
    if (ctx->active_items.len >= STRUCTURED_DATA_MAX_ITEM_DEPTH) {
        PyErr_Format(PyExc_RecursionError, "microdata() supports at most %zd nested items",
                     STRUCTURED_DATA_MAX_ITEM_DEPTH);
        return NULL;
    }
    if (!claim_record(ctx)) {
        PyErr_Format(PyExc_RecursionError, "microdata() supports at most %zd repeats of items shared through itemref",
                     STRUCTURED_DATA_MAX_ITEM_REPEATS);
        return NULL;
    }
    if (node_stack_push(&ctx->active_items, element) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return PyErr_NoMemory();                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *item = NULL;
    PyObject *properties = PyDict_New();
    if (properties == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        goto done;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (collect_properties(ctx, element, properties) < 0) {
        goto done;
    }
    PyObject *type_obj = attr_value_or_none(find_node_attr(element, TH_ATTR_ITEMTYPE));
    if (type_obj == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        goto done;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t itemid = th_node_attr_find(ctx->tree, element, "itemid", 6);
    PyObject *id_obj = attr_value_or_none(itemid >= 0 ? &element->attrs[itemid] : NULL);
    if (id_obj == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(type_obj); /* GCOVR_EXCL_LINE: allocation-failure path */
        goto done;           /* GCOVR_EXCL_LINE */
    }
    item = PyObject_CallFunctionObjArgs(ctx->state->microdata_item_type, type_obj, id_obj, properties, NULL);
    Py_DECREF(type_obj);
    Py_DECREF(id_obj);
done:
    ctx->active_items.len--;
    Py_XDECREF(properties);
    return item;
}

/* Gather the verbatim text of every <script type="application/ld+json"> under the document into a list of str, leaving
   the JSON parsing to the Python facade. NULL only on the excluded allocation-failure path. A block is the element's
   child text content, not its descendant text: an SVG or MathML <script> can hold further <script> elements, and
   descendant text would decode the innermost one once per enclosing element. */
static PyObject *gather_json_ld(PyObject *self) {
    PyObject *texts = PyList_New(0);
    if (texts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_SCRIPT) {
            continue;
        }
        const th_node_attr *type_attr = find_node_attr(node, TH_ATTR_TYPE);
        if (type_attr == NULL || type_attr->value == NULL) {
            continue;
        }
        if (!ucs4_ieq_trimmed(type_attr->value, type_attr->value_len, JSON_LD_TYPE,
                              (Py_ssize_t)sizeof(JSON_LD_TYPE) - 1) &&
            !json_ld_type_with_parameters(type_attr->value, type_attr->value_len)) {
            continue;
        }
        PyObject *text = str_from_accessor(th_node_child_text, tree, node);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;     /* GCOVR_EXCL_LINE: allocation-failure path */
            break;          /* GCOVR_EXCL_LINE */
        }
        int append_failed = PyList_Append(texts, text) < 0;
        Py_DECREF(text);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            break;           /* GCOVR_EXCL_LINE */
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(texts); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    return texts;
}

/* The property/name attribute on a <meta> that carries an og:/twitter: key, or NULL when neither does. property wins
   over name when both qualify (OpenGraph uses property, Twitter uses name, but pages mix the two). */
static const th_node_attr *social_meta_attr(th_tree *tree, th_node *node) {
    Py_ssize_t property = th_node_attr_find(tree, node, "property", 8);
    if (property >= 0 && node->attrs[property].value != NULL &&
        is_social_key(node->attrs[property].value, node->attrs[property].value_len)) {
        return &node->attrs[property];
    }
    const th_node_attr *name = find_node_attr(node, TH_ATTR_NAME);
    if (name != NULL && name->value != NULL && is_social_key(name->value, name->value_len)) {
        return name;
    }
    return NULL;
}

/* Map every <meta property=og:*> / <meta name=twitter:*> key to its content value (first og: and last twitter:
   occurrence wins). A URL-valued key's content is absolutized against `base` when the caller passed one (base is NULL
   otherwise, leaving every value verbatim). NULL only on the excluded allocation-failure path. */
static PyObject *gather_opengraph(PyObject *self, PyObject *base, int strip_prefix) {
    PyObject *result = PyDict_New();
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    Py_ssize_t key_offset = strip_prefix ? 3 : 0;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_META) {
            continue;
        }
        const th_node_attr *key_attr = social_meta_attr(tree, node);
        if (key_attr == NULL || (strip_prefix && !ucs4_has_prefix(key_attr->value, key_attr->value_len, "og:", 3))) {
            continue;
        }
        PyObject *key = ucs4_to_str(key_attr->value + key_offset, key_attr->value_len - key_offset);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
            break;         /* GCOVR_EXCL_LINE */
        }
        PyObject *content = attr_str_or_empty(tree, node, "content", 7);
        if (content == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(key);    /* GCOVR_EXCL_LINE: allocation-failure path */
            failed = 1;        /* GCOVR_EXCL_LINE */
            break;             /* GCOVR_EXCL_LINE */
        }
        if (base != NULL && is_url_social_key(key_attr->value, key_attr->value_len)) {
            if (resolve_in_place(base, &content) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                Py_DECREF(key);                         /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(content);                     /* GCOVR_EXCL_LINE */
                failed = 1;                             /* GCOVR_EXCL_LINE */
                break;                                  /* GCOVR_EXCL_LINE */
            }
        }
        int set_failed;
        if (strip_prefix || ucs4_has_prefix(key_attr->value, key_attr->value_len, "og:", 3)) {
            set_failed = PyDict_SetDefault(result, key, content) == NULL;
        } else {
            set_failed = PyDict_SetItem(result, key, content) < 0;
        }
        Py_DECREF(key);
        Py_DECREF(content);
        if (set_failed) { /* GCOVR_EXCL_BR_LINE: insert fails only on unforceable allocation */
            failed = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
            break;        /* GCOVR_EXCL_LINE */
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {          /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    return result;
}

/* Build one MicrodataItem for every top-level Microdata item (an element with itemscope and no itemprop, so it is not a
   property of another item). URL-valued properties are absolutized against `base` when the caller passed one (base is
   NULL otherwise, leaving values verbatim). NULL only on the excluded allocation-failure path. */
static PyObject *gather_microdata(PyObject *self, PyObject *base) {
    PyObject *items = PyList_New(0);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    micro_ctx ctx = {state_of(self),
                     tree,
                     root,
                     base,
                     {NULL, 0, 0},
                     {{NULL, 0, 0}, {NULL, 0, 0}, {NULL, 0, 0}, {NULL, 0, 0}, 0},
                     {NULL, 0, 0},
                     NULL,
                     NULL,
                     0,
                     STRUCTURED_DATA_WALK_BUDGET_PER_NODE * (Py_ssize_t)th_tree_node_count(tree),
                     STRUCTURED_DATA_MAX_ITEM_REPEATS,
                     0,
                     NULL,
                     NULL,
                     STRUCTURED_DATA_MAX_VALUE_REPEATS};
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || find_node_attr(node, TH_ATTR_ITEMSCOPE) == NULL ||
            find_node_attr(node, TH_ATTR_ITEMPROP) != NULL) {
            continue;
        }
        PyObject *item = build_item(&ctx, node);
        if (item == NULL) {
            failed = 1;
            break;
        }
        int append_failed = PyList_Append(items, item) < 0;
        Py_DECREF(item);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            break;           /* GCOVR_EXCL_LINE */
        }
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(ctx.ids.slots);
    PyMem_Free(ctx.props.ranks.entries);
    PyMem_Free(ctx.props.claims.items);
    PyMem_Free(ctx.props.first.entries);
    PyMem_Free(ctx.props.past.entries);
    PyMem_Free(ctx.named_runs);
    PyMem_Free(ctx.valued);
    Py_XDECREF(ctx.values);
    PyMem_Free(ctx.active_items.items);
    if (failed) {
        Py_DECREF(items);
        if (!PyErr_Occurred()) {     /* GCOVR_EXCL_BR_LINE: raw node-stack allocation failure */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return NULL;
    }
    return items;
}

/* The RDFa 1.1 initial-context prefixes a page inherits without @prefix, so schema:, sosa:, and other vocabularies
   expand out of the box. A page @prefix overrides an entry of the same name for its subtree. */
typedef struct {
    const char *prefix;
    const char *iri;
} rdfa_prefix;

static const rdfa_prefix RDFA_DEFAULT_PREFIXES[] = {
    {"as", "https://www.w3.org/ns/activitystreams#"},
    {"csvw", "http://www.w3.org/ns/csvw#"},
    {"cat", "http://www.w3.org/ns/dcat#"},
    {"cc", "http://creativecommons.org/ns#"},
    {"cnt", "http://www.w3.org/2011/content#"},
    {"ctag", "http://commontag.org/ns#"},
    {"dc", "http://purl.org/dc/terms/"},
    {"dc11", "http://purl.org/dc/elements/1.1/"},
    {"dcat", "http://www.w3.org/ns/dcat#"},
    {"dcterms", "http://purl.org/dc/terms/"},
    {"dctypes", "http://purl.org/dc/dcmitype/"},
    {"dqv", "http://www.w3.org/ns/dqv#"},
    {"duv", "https://www.w3.org/ns/duv#"},
    {"foaf", "http://xmlns.com/foaf/0.1/"},
    {"gr", "http://purl.org/goodrelations/v1#"},
    {"grddl", "http://www.w3.org/2003/g/data-view#"},
    {"ht", "http://www.w3.org/2006/http#"},
    {"ical", "http://www.w3.org/2002/12/cal/icaltzd#"},
    {"ldp", "http://www.w3.org/ns/ldp#"},
    {"ma", "http://www.w3.org/ns/ma-ont#"},
    {"oa", "http://www.w3.org/ns/oa#"},
    {"og", "http://ogp.me/ns#"},
    {"org", "http://www.w3.org/ns/org#"},
    {"owl", "http://www.w3.org/2002/07/owl#"},
    {"prov", "http://www.w3.org/ns/prov#"},
    {"qb", "http://purl.org/linked-data/cube#"},
    {"rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"},
    {"rdfa", "http://www.w3.org/ns/rdfa#"},
    {"rdfs", "http://www.w3.org/2000/01/rdf-schema#"},
    {"rev", "http://purl.org/stuff/rev#"},
    {"rif", "http://www.w3.org/2007/rif#"},
    {"rr", "http://www.w3.org/ns/r2rml#"},
    {"schema", "http://schema.org/"},
    {"sd", "http://www.w3.org/ns/sparql-service-description#"},
    {"sioc", "http://rdfs.org/sioc/ns#"},
    {"skos", "http://www.w3.org/2004/02/skos/core#"},
    {"skosxl", "http://www.w3.org/2008/05/skos-xl#"},
    {"v", "http://rdf.data-vocabulary.org/#"},
    {"vcard", "http://www.w3.org/2006/vcard/ns#"},
    {"void", "http://rdfs.org/ns/void#"},
    {"wdr", "http://www.w3.org/2007/05/powder#"},
    {"wdrs", "http://www.w3.org/2007/05/powder-s#"},
    {"xhv", "http://www.w3.org/1999/xhtml/vocab#"},
    {"xml", "http://www.w3.org/XML/1998/namespace"},
    {"xsd", "http://www.w3.org/2001/XMLSchema#"},
};

/* A fresh prefix map seeded with the RDFa 1.1 initial context, the base every document walk starts from. NULL only on
   the excluded allocation-failure path. */
static PyObject *build_default_prefixes(void) {
    PyObject *prefixes = PyDict_New();
    if (prefixes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (size_t index = 0; index < sizeof(RDFA_DEFAULT_PREFIXES) / sizeof(RDFA_DEFAULT_PREFIXES[0]); index++) {
        PyObject *iri = PyUnicode_FromString(RDFA_DEFAULT_PREFIXES[index].iri);
        if (iri == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(prefixes); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
        int failed = PyDict_SetItemString(prefixes, RDFA_DEFAULT_PREFIXES[index].prefix, iri) < 0;
        Py_DECREF(iri);
        if (failed) {            /* GCOVR_EXCL_BR_LINE: insert fails only on unforceable allocation */
            Py_DECREF(prefixes); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
    }
    return prefixes;
}

/* The in-scope RDFa evaluation context threaded down the walk: the current @vocab (borrowed, NULL when none), the
   prefix map (borrowed, always non-NULL; the one map of the whole extraction, which holds the declarations in scope at
   the element being visited), and the base URL IRIs resolve against (borrowed, NULL to keep them verbatim). */
typedef struct {
    PyObject *vocab;
    PyObject *prefixes;
    PyObject *base;
    Py_ssize_t item_depth;
} rdfa_ctx;

static int rdfa_matches_term(PyObject *token, const char *term, Py_ssize_t len) {
    int kind = PyUnicode_KIND(token);
    void *data = PyUnicode_DATA(token);
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 codepoint = PyUnicode_READ(kind, data, index);
        if ((unsigned int)(codepoint - 'A') < 26) {
            codepoint += 'a' - 'A';
        }
        if (codepoint != (Py_UCS4)(unsigned char)term[index]) {
            return 0;
        }
    }
    return 1;
}

static const char *rdfa_initial_prefix_iri(PyObject *prefix) {
    static const struct {
        const char *prefix;
        Py_ssize_t len;
        const char *iri;
    } additions[] = {
        {"jsonld", 6, "http://www.w3.org/ns/json-ld#"}, {"odrl", 4, "http://www.w3.org/ns/odrl/2/"},
        {"sosa", 4, "http://www.w3.org/ns/sosa/"},      {"ssn", 3, "http://www.w3.org/ns/ssn/"},
        {"time", 4, "http://www.w3.org/2006/time#"},
    };
    Py_ssize_t len = PyUnicode_GET_LENGTH(prefix);
    for (size_t index = 0; index < sizeof(additions) / sizeof(additions[0]); index++) {
        if (len == additions[index].len && PyUnicode_CompareWithASCIIString(prefix, additions[index].prefix) == 0) {
            return additions[index].iri;
        }
    }
    return NULL;
}

/* The IRI a prefix maps to, or NULL when it is undeclared or the lookup fails. A prefix that goes out of scope keeps
   its key with None rather than being deleted: deleting and re-inserting one key of a large dict is quadratic on
   free-threaded builds. */
static PyObject *rdfa_declared_iri(PyObject *prefixes, PyObject *prefix) {
    PyObject *iri = PyDict_GetItemWithError(prefixes, prefix);
    return iri == Py_None ? NULL : iri;
}

/* RDFa @property/@typeof permit terms, CURIEs, and absolute IRIs, but not SafeCURIEs; malformed terms are ignored.
   Bare terms join @vocab or the initial context; unknown terms and undeclared prefixes are kept verbatim, as is a CURIE
   that a relative prefix IRI, once resolved, would lengthen by more than RDFA_MAX_MAPPING_LENGTH. */
static PyObject *rdfa_expand_term(PyObject *token, PyObject *vocab, PyObject *prefixes) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(token);
    Py_ssize_t colon = PyUnicode_FindChar(token, (Py_UCS4)':', 0, length, 1);
    if (colon < 0) {
        if (!is_name_start(PyUnicode_READ_CHAR(token, 0))) {
            return Py_NewRef(Py_None);
        }
        for (Py_ssize_t index = 1; index < length; index++) {
            Py_UCS4 code = PyUnicode_READ_CHAR(token, index);
            if (code != '/' && !is_name_char(code)) {
                return Py_NewRef(Py_None);
            }
        }
        if (vocab != NULL) {
            return PyUnicode_Concat(vocab, token);
        }
        if (length == 4 && rdfa_matches_term(token, "role", 4)) {
            return PyUnicode_FromString("http://www.w3.org/1999/xhtml/vocab#role");
        }
        if (length == 7 && rdfa_matches_term(token, "license", 7)) {
            return PyUnicode_FromString("http://www.w3.org/1999/xhtml/vocab#license");
        }
        if (length == 11 && rdfa_matches_term(token, "describedby", 11)) {
            return PyUnicode_FromString("http://www.w3.org/2007/05/powder-s#describedby");
        }
        return Py_NewRef(token);
    }
    if (PyUnicode_READ_CHAR(token, 0) == '[') {
        return Py_NewRef(Py_None);
    }
    if (colon == 0) {
        PyObject *reference = PyUnicode_Substring(token, 1, length);
        if (reference == NULL) { /* GCOVR_EXCL_BR_LINE: substring fails only on unforceable allocation */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *result = th_str_format("http://www.w3.org/1999/xhtml/vocab#%U", reference);
        Py_DECREF(reference);
        return result;
    }
    PyObject *prefix = PyUnicode_Substring(token, 0, colon);
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: substring fails only on unforceable allocation */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *iri = rdfa_declared_iri(prefixes, prefix);
    if (iri == NULL) {
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lookup fails only on an unforceable error */
            Py_DECREF(prefix);  /* GCOVR_EXCL_LINE: error path */
            return NULL;        /* GCOVR_EXCL_LINE: error path */
        }
        for (Py_ssize_t index = 0; index < colon; index++) {
            Py_UCS4 code = PyUnicode_READ_CHAR(prefix, index);
            if (Py_UNICODE_TOLOWER(code) != code) {
                PyObject *lower = PyObject_CallMethod(prefix, "lower", NULL);
                if (lower == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    Py_DECREF(prefix); /* GCOVR_EXCL_LINE: allocation-failure path */
                    return NULL;       /* GCOVR_EXCL_LINE */
                }
                Py_DECREF(prefix);
                prefix = lower;
                iri = rdfa_declared_iri(prefixes, prefix);
                break;
            }
        }
    }
    if (iri == NULL && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lookup fails only on unforceable error */
        Py_DECREF(prefix);                 /* GCOVR_EXCL_LINE: error path */
        return NULL;                       /* GCOVR_EXCL_LINE */
    }
    const char *initial_iri = iri == NULL ? rdfa_initial_prefix_iri(prefix) : NULL;
    Py_DECREF(prefix);
    if (iri == NULL && initial_iri == NULL) {
        return Py_NewRef(token);
    }
    PyObject *base = NULL;
    PyObject *mapping = iri;
    if (mapping != NULL && PyTuple_CheckExact(mapping)) {
        base = PyTuple_GET_ITEM(mapping, 2);
        iri = PyTuple_GET_ITEM(mapping, 0);
    }
    PyObject *reference = PyUnicode_Substring(token, colon + 1, length);
    if (reference == NULL) { /* GCOVR_EXCL_BR_LINE: substring fails only on unforceable allocation */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (base != NULL && PyTuple_GET_ITEM(mapping, 1) != Py_None) {
        int simple = 1;
        for (Py_ssize_t index = 0; index < PyUnicode_GET_LENGTH(reference); index++) {
            Py_UCS4 code = PyUnicode_READ_CHAR(reference, index);
            if (!((code >= (Py_UCS4)'a' && code <= (Py_UCS4)'z') || (code >= (Py_UCS4)'A' && code <= (Py_UCS4)'Z') ||
                  (code >= (Py_UCS4)'0' && code <= (Py_UCS4)'9') || code == (Py_UCS4)'_' || code == (Py_UCS4)'-')) {
                simple = 0;
                break;
            }
        }
        if (simple) {
            iri = PyTuple_GET_ITEM(mapping, 1);
            base = NULL;
        }
    }
    PyObject *result = iri != NULL ? PyUnicode_Concat(iri, reference) : th_str_format("%s%U", initial_iri, reference);
    Py_DECREF(reference);
    if (result != NULL && base != NULL && resolve_in_place(base, &result) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
        Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    if (result != NULL && base != NULL) { /* GCOVR_EXCL_BR_LINE: a NULL result is an allocation failure */
        /* A relative prefix IRI resolves with each reference, so its length is known only here. The reference's own
           percent-encoded non-ASCII characters are the term's growth, not the mapping's, so they are not counted. */
        Py_ssize_t encoding_growth = 0;
        for (Py_ssize_t index = colon + 1; index < length; index++) {
            Py_UCS4 code = PyUnicode_READ_CHAR(token, index);
            if (code >= 0x10000) {
                encoding_growth += 11;
            } else if (code >= 0x800) {
                encoding_growth += 8;
            } else if (code >= 0x80) {
                encoding_growth += 5;
            }
        }
        if (PyUnicode_GET_LENGTH(result) - length - encoding_growth > RDFA_MAX_MAPPING_LENGTH) {
            Py_SETREF(result, Py_NewRef(token));
        }
    }
    return result;
}

/* RDFa 1.1 requires CURIE expansion before IRI fallback for @about/@resource. An invalid SafeCURIE has no IRI and
   cannot fall back. */
static PyObject *rdfa_resource_iri(PyObject *value, rdfa_ctx ctx) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(value);
    int safe = length > 0 && PyUnicode_READ_CHAR(value, 0) == (Py_UCS4)'[';
    if (safe && (length < 3 || PyUnicode_READ_CHAR(value, length - 1) != (Py_UCS4)']')) {
        Py_DECREF(value);
        return Py_NewRef(Py_None);
    }
    PyObject *curie = value;
    if (safe) {
        curie = PyUnicode_Substring(value, 1, length - 1);
        if (curie == NULL) {  /* GCOVR_EXCL_BR_LINE: substring fails only on unforceable allocation */
            Py_DECREF(value); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
    }
    Py_ssize_t colon = PyUnicode_FindChar(curie, (Py_UCS4)':', 0, PyUnicode_GET_LENGTH(curie), 1);
    Py_ssize_t curie_length = PyUnicode_GET_LENGTH(curie);
    int iri = colon < 0 || (colon + 2 < curie_length && PyUnicode_READ_CHAR(curie, colon + 1) == (Py_UCS4)'/' &&
                            PyUnicode_READ_CHAR(curie, colon + 2) == (Py_UCS4)'/');
    if (iri) {
        if (safe) {
            Py_DECREF(curie);
            Py_DECREF(value);
            return Py_NewRef(Py_None);
        }
        if (ctx.base != NULL && resolve_rdfa_in_place(ctx.base, &value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
            Py_DECREF(value);                                                  /* GCOVR_EXCL_LINE */
            return NULL;                                                       /* GCOVR_EXCL_LINE */
        }
        return value;
    }
    PyObject *expanded = rdfa_expand_term(curie, NULL, ctx.prefixes);
    if (expanded == NULL) {   /* GCOVR_EXCL_BR_LINE: expansion fails only on unforceable allocation */
        if (safe) {           /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(curie); /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(value); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    if (expanded != curie) {
        if (safe) {
            Py_DECREF(curie);
        }
        Py_DECREF(value);
        return expanded;
    }
    Py_DECREF(expanded);
    if (safe) {
        Py_DECREF(curie);
        Py_DECREF(value);
        return Py_NewRef(Py_None);
    }
    if (ctx.base != NULL && resolve_rdfa_in_place(ctx.base, &value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(value); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    return value;
}

/* Merge the whitespace-separated `prefix: iri` pairs of a @prefix attribute into `prefixes`. A `prefix:` token names a
   prefix (the trailing colon is dropped) and the next token is its IRI; a token that is neither is skipped, and a
   trailing `prefix:` with no IRI is dropped (RDFa 1.1 processing), as is one whose IRI is longer than
   RDFA_MAX_MAPPING_LENGTH, as written or as resolved. Each merged pair appends the prefix and the mapping it replaces
   (None when it had none) to the `undo` list, for restore_prefixes. -1 only on the excluded allocation-failure path. */
static int parse_prefix_decls(PyObject *prefixes, const Py_UCS4 *value, Py_ssize_t len, PyObject *base,
                              PyObject *undo) {
    PyObject *pending = NULL;
    Py_ssize_t cursor = 0;
    while (cursor < len) {
        TH_WORK_STEP();
        while (cursor < len && is_space(value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < len && !is_space(value[cursor])) {
            cursor++;
        }
        if (cursor == start) {
            break;
        }
        if (pending == NULL) {
            if (cursor - start > 1 && value[cursor - 1] == (Py_UCS4)':') {
                pending = ucs4_to_str(&value[start], cursor - start - 1);
                if (pending == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                for (Py_ssize_t index = start; index < cursor - 1; index++) {
                    if (Py_UNICODE_TOLOWER(value[index]) == value[index]) {
                        continue;
                    }
                    PyObject *lower = PyObject_CallMethod(pending, "lower", NULL);
                    Py_DECREF(pending);
                    pending = lower;
                    if (pending == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    break;
                }
            }
            continue;
        }
        if (cursor - start > RDFA_MAX_MAPPING_LENGTH) {
            Py_DECREF(pending);
            pending = NULL;
            continue;
        }
        PyObject *iri = ucs4_to_str(&value[start], cursor - start);
        if (iri == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(pending); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        if (base != NULL && th_url_scheme_colon(value, start, cursor) < 0) {
            PyObject *resolved = Py_NewRef(Py_None);
            if (PyUnicode_READ_CHAR(iri, PyUnicode_GET_LENGTH(iri) - 1) == (Py_UCS4)'/') {
                Py_SETREF(resolved, Py_NewRef(iri));
                if (resolve_in_place(base, &resolved) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    Py_DECREF(pending);                      /* GCOVR_EXCL_LINE */
                    Py_DECREF(iri);                          /* GCOVR_EXCL_LINE */
                    Py_DECREF(resolved);                     /* GCOVR_EXCL_LINE */
                    return -1;                               /* GCOVR_EXCL_LINE */
                }
                if (PyUnicode_GET_LENGTH(resolved) > RDFA_MAX_MAPPING_LENGTH) {
                    Py_DECREF(resolved);
                    Py_DECREF(iri);
                    Py_DECREF(pending);
                    pending = NULL;
                    continue;
                }
                if (resolved == iri) {
                    Py_SETREF(resolved, Py_NewRef(Py_None));
                }
            }
            PyObject *relative = PyTuple_Pack(3, iri, resolved, base);
            Py_DECREF(resolved);
            if (relative == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                Py_DECREF(pending); /* GCOVR_EXCL_LINE */
                Py_DECREF(iri);     /* GCOVR_EXCL_LINE */
                return -1;          /* GCOVR_EXCL_LINE */
            }
            Py_SETREF(iri, relative);
        }
        PyObject *shadowed = PyDict_GetItemWithError(prefixes, pending);
        if (shadowed == NULL) {
            shadowed = Py_None;
        }
        /* GCOVR_EXCL_BR_START: lookup, append and insert fail only on unforceable allocation */
        int failed = PyErr_Occurred() != NULL || PyList_Append(undo, pending) < 0 ||
                     PyList_Append(undo, shadowed) < 0 || PyDict_SetItem(prefixes, pending, iri) < 0;
        /* GCOVR_EXCL_BR_STOP */
        Py_DECREF(pending);
        pending = NULL;
        Py_DECREF(iri);
        if (failed) {  /* GCOVR_EXCL_BR_LINE: insert fails only on unforceable allocation */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_XDECREF(pending);
    return 0;
}

/* Put back the mappings one @prefix attribute replaced, newest first so a prefix the attribute declares twice ends on
   the mapping it had outside the element. -1 only on the excluded failure path. */
static int restore_prefixes(PyObject *prefixes, PyObject *undo) {
    for (Py_ssize_t index = PyList_GET_SIZE(undo); index > 0; index -= 2) {
        TH_WORK_STEP();
        PyObject *prefix = PyList_GET_ITEM(undo, index - 2);
        PyObject *shadowed = PyList_GET_ITEM(undo, index - 1);
        int failed = PyDict_SetItem(prefixes, prefix, shadowed) < 0;
        if (failed) {  /* GCOVR_EXCL_BR_LINE: the key is in the map, so replacing its value cannot fail */
            return -1; /* GCOVR_EXCL_LINE: unreachable from a test */
        }
    }
    return 0;
}

/* The child's in-scope @vocab: a new str for a non-empty @vocab, NULL (the vocabulary cleared) for an empty or
   valueless one and for one longer than RDFA_MAX_MAPPING_LENGTH as written or as resolved, or the parent's borrowed
   value when the child declares none. *owned records whether *out is a new reference the caller must release. -1 only
   on the excluded allocation-failure path. */
static int resolve_child_vocab(th_tree *tree, th_node *child, PyObject *parent, PyObject *base, PyObject **out,
                               int *owned) {
    Py_ssize_t index = th_node_attr_find(tree, child, "vocab", 5);
    if (index < 0) {
        *out = parent;
        *owned = 0;
        return 0;
    }
    const th_node_attr *attr = &child->attrs[index];
    if (attr->value == NULL || attr->value_len > RDFA_MAX_MAPPING_LENGTH) {
        *out = NULL;
        *owned = 0;
        return 0;
    }
    PyObject *value = ucs4_to_str(attr->value, attr->value_len);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (base != NULL && th_url_scheme_colon(attr->value, 0, attr->value_len) < 0) {
        if (resolve_in_place(base, &value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            Py_DECREF(value);                     /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                            /* GCOVR_EXCL_LINE */
        }
        if (PyUnicode_GET_LENGTH(value) > RDFA_MAX_MAPPING_LENGTH) {
            Py_DECREF(value);
            *out = NULL;
            *owned = 0;
            return 0;
        }
    }
    *out = value;
    *owned = 1;
    return 0;
}

/* Bring the child's @prefix declarations into scope by merging them into `prefixes`, the one map of the extraction.
   *undo is NULL when the child declares none, else a new list of what the merge replaced, for leave_child_ctx to take
   the declarations back with once the subtree is walked. The child pays for the pairs it declares, where a map of its
   own would cost a copy of every pair in scope. -1 only on the excluded allocation-failure path. */
static int resolve_child_prefixes(th_tree *tree, th_node *child, PyObject *prefixes, PyObject *base, PyObject **undo) {
    *undo = NULL;
    Py_ssize_t index = th_node_attr_find(tree, child, "prefix", 6);
    if (index < 0 || child->attrs[index].value == NULL) {
        return 0;
    }
    *undo = PyList_New(0);
    if (*undo == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    const th_node_attr *attr = &child->attrs[index];
    if (parse_prefix_decls(prefixes, attr->value, attr->value_len, base, *undo) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_CLEAR(*undo); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* Drop the references resolve_child_ctx took for the child: its vocab when freshly allocated, and its undo list. The
   prefix map is left as it is: leave_child_ctx restores it first, and the error paths abandon it. */
static void release_child_ctx(rdfa_ctx *child_ctx, int vocab_owned, PyObject *undo) {
    if (vocab_owned) {
        Py_DECREF(child_ctx->vocab);
    }
    Py_XDECREF(undo);
}

/* Leave the child's scope once its subtree is walked: put back the mappings its @prefix declarations replaced, then
   release the context. -1 only on the excluded failure path, which no test can reach. */
static int leave_child_ctx(rdfa_ctx *child_ctx, int vocab_owned, PyObject *undo) {
    int status = undo != NULL ? restore_prefixes(child_ctx->prefixes, undo) : 0;
    release_child_ctx(child_ctx, vocab_owned, undo);
    return status;
}

/* Derive the child's evaluation context from the parent's, applying the child's @vocab and @prefix. *vocab_owned
   records whether the vocab carried a new reference and *undo what the @prefix replaced in the prefix map, for
   leave_child_ctx after the subtree is walked. -1 only on the excluded allocation-failure path. */
static int resolve_child_ctx(th_tree *tree, th_node *child, rdfa_ctx parent, rdfa_ctx *child_ctx, int *vocab_owned,
                             PyObject **undo) {
    PyObject *vocab = NULL;
    int vocab_rc = resolve_child_vocab(tree, child, parent.vocab, parent.base, &vocab, vocab_owned);
    if (vocab_rc < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int prefixes_rc = resolve_child_prefixes(tree, child, parent.prefixes, parent.base, undo);
    if (prefixes_rc < 0) {                                         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        rdfa_ctx partial = {vocab, NULL, NULL, parent.item_depth}; /* GCOVR_EXCL_LINE: allocation-failure path */
        release_child_ctx(&partial, *vocab_owned, NULL);           /* GCOVR_EXCL_LINE */
        return -1;                                                 /* GCOVR_EXCL_LINE */
    }
    child_ctx->vocab = vocab;
    child_ctx->prefixes = parent.prefixes;
    child_ctx->base = parent.base;
    child_ctx->item_depth = parent.item_depth;
    return 0;
}

/* The resource subject of a typeof element: its @about, else @resource, else @href, else @src (resolved against base
   when set), or None for a blank node when it names none. NULL only on the excluded allocation-failure path. */
static PyObject *rdfa_subject(th_tree *tree, th_node *element, rdfa_ctx ctx) {
    static const char *const names[] = {"about", "resource", "href", "src"};
    static const Py_ssize_t lens[] = {5, 8, 4, 3};
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        if (th_node_attr_find(tree, element, names[index], lens[index]) < 0) {
            continue;
        }
        PyObject *iri = attr_url_or_empty(tree, element, names[index], lens[index]);
        if (iri == NULL) { /* GCOVR_EXCL_BR_LINE: attr_url_or_empty fails only on allocation */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (index < 2) {
            iri = rdfa_resource_iri(iri, ctx);
        } else if (ctx.base != NULL && resolve_rdfa_in_place(ctx.base, &iri) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
            Py_DECREF(iri);                                                         /* GCOVR_EXCL_LINE */
            return NULL;                                                            /* GCOVR_EXCL_LINE */
        }
        if (iri == NULL) { /* GCOVR_EXCL_BR_LINE: expansion fails only on unforceable allocation */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (iri == Py_None) {
            Py_DECREF(iri);
            continue;
        }
        return iri;
    }
    return Py_NewRef(Py_None);
}

static PyObject *build_rdfa_item(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx);
static int collect_rdfa_properties(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx,
                                   PyObject *properties);

/* The expanded @typeof IRIs of an element as a list, empty for a valueless typeof. The caller only reaches here for an
   element carrying typeof, so the attribute is present. NULL only on the excluded allocation-failure path. */
static PyObject *expand_typeof(th_tree *tree, th_node *element, rdfa_ctx ctx) {
    Py_ssize_t index = th_node_attr_find(tree, element, "typeof", 6);
    const th_node_attr *attr = &element->attrs[index];
    if (attr->value == NULL) {
        return PyList_New(0);
    }
    PyObject *tokens = split_token_list(attr->value, attr->value_len);
    if (tokens == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyList_New(0);
    if (result == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(tokens); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = PyList_GET_SIZE(tokens);
    for (Py_ssize_t token = 0; token < count; token++) {
        PyObject *expanded = rdfa_expand_term(PyList_GET_ITEM(tokens, token), ctx.vocab, ctx.prefixes);
        if (expanded == NULL) { /* GCOVR_EXCL_BR_LINE: expansion fails only on unforceable allocation */
            Py_DECREF(tokens);  /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result);  /* GCOVR_EXCL_LINE */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
        if (expanded == Py_None) {
            Py_DECREF(expanded);
            continue;
        }
        int failed = PyList_Append(result, expanded) < 0;
        Py_DECREF(expanded);
        if (failed) {          /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            Py_DECREF(tokens); /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result); /* GCOVR_EXCL_LINE */
            return NULL;       /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(tokens);
    return result;
}

/* Whether `element` is an RDFa property whose object rdfa_value can take from its text content: it names a property
   and carries no @content, @typeof, @href, @src or <time> @datetime to take it from instead. A @resource counts, since
   one that expands to nothing leaves the text. */
static int rdfa_text_property(th_tree *tree, th_node *element) {
    static const char *const names[] = {"content", "typeof", "href", "src"};
    static const Py_ssize_t lens[] = {7, 6, 4, 3};
    Py_ssize_t property = th_node_attr_find(tree, element, "property", 8);
    if (property < 0 || element->attrs[property].value == NULL) {
        return 0;
    }
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        if (th_node_attr_find(tree, element, names[index], lens[index]) >= 0) {
            return 0;
        }
    }
    if (element->atom == TH_TAG_TIME) {
        Py_ssize_t datetime = th_node_attr_find(tree, element, "datetime", 8);
        return datetime < 0 || element->attrs[datetime].value == NULL;
    }
    return 1;
}

/* One RDFa property element's object: the @content literal, else a nested RdfaItem when the element carries @typeof,
   else the @resource/@href/@src IRI (absolutized against base when set), else a <time> @datetime literal, else the
   element's text content, or None when text-valued properties nest past STRUCTURED_DATA_MAX_TEXT_DEPTH in it and the
   property is to be left out. NULL only on the excluded allocation-failure path. */
static PyObject *rdfa_value(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx) {
    Py_ssize_t content = th_node_attr_find(tree, element, "content", 7);
    if (content >= 0) {
        const th_node_attr *attr = &element->attrs[content];
        if (attr->value == NULL) {
            return PyUnicode_FromString("");
        }
        return ucs4_to_str(attr->value, attr->value_len);
    }
    if (th_node_attr_find(tree, element, "typeof", 6) >= 0) {
        return build_rdfa_item(tree, state, element, ctx);
    }
    static const char *const iri_names[] = {"resource", "href", "src"};
    static const Py_ssize_t iri_lens[] = {8, 4, 3};
    for (size_t index = 0; index < sizeof(iri_names) / sizeof(iri_names[0]); index++) {
        if (th_node_attr_find(tree, element, iri_names[index], iri_lens[index]) < 0) {
            continue;
        }
        PyObject *iri = attr_url_or_empty(tree, element, iri_names[index], iri_lens[index]);
        if (iri == NULL) { /* GCOVR_EXCL_BR_LINE: attr_url_or_empty fails only on allocation */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (index == 0) {
            iri = rdfa_resource_iri(iri, ctx);
        } else if (ctx.base != NULL && resolve_rdfa_in_place(ctx.base, &iri) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
            Py_DECREF(iri);                                                         /* GCOVR_EXCL_LINE */
            return NULL;                                                            /* GCOVR_EXCL_LINE */
        }
        if (iri == NULL) { /* GCOVR_EXCL_BR_LINE: expansion fails only on unforceable allocation */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (iri == Py_None) {
            Py_DECREF(iri);
            continue;
        }
        return iri;
    }
    if (element->atom == TH_TAG_TIME) {
        Py_ssize_t datetime = th_node_attr_find(tree, element, "datetime", 8);
        if (datetime >= 0 && element->attrs[datetime].value != NULL) {
            return ucs4_to_str(element->attrs[datetime].value, element->attrs[datetime].value_len);
        }
    }
    if (text_properties_too_deep(tree, element, rdfa_text_property)) {
        return Py_NewRef(Py_None);
    }
    return str_from_accessor(th_node_text, tree, element);
}

/* Build one RdfaItem(vocab, type, resource, properties), bounding recursive record construction while the DOM walk
   itself stays iterative. */
static PyObject *build_rdfa_item(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx) {
    if (ctx.item_depth >= STRUCTURED_DATA_MAX_ITEM_DEPTH) {
        PyErr_Format(PyExc_RecursionError, "rdfa() supports at most %zd nested items", STRUCTURED_DATA_MAX_ITEM_DEPTH);
        return NULL;
    }
    ctx.item_depth++;
    PyObject *properties = PyDict_New();
    if (properties == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (collect_rdfa_properties(tree, state, element, ctx, properties) < 0) {
        Py_DECREF(properties);
        return NULL;
    }
    PyObject *type_list = expand_typeof(tree, element, ctx);
    if (type_list == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(properties); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    PyObject *resource = rdfa_subject(tree, element, ctx);
    if (resource == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(type_list);  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(properties); /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    PyObject *vocab = ctx.vocab != NULL ? Py_NewRef(ctx.vocab) : Py_NewRef(Py_None);
    PyObject *item = PyObject_CallFunctionObjArgs(state->rdfa_item_type, vocab, type_list, resource, properties, NULL);
    Py_DECREF(vocab);
    Py_DECREF(type_list);
    Py_DECREF(resource);
    Py_DECREF(properties);
    return item;
}

/* Append `value` under every expanded name in the property run to the properties dict, each name mapping to a list of
   values in document order. -1 only on the excluded allocation-failure path. */
static int rdfa_add_property(PyObject *properties, const Py_UCS4 *names, Py_ssize_t names_len, rdfa_ctx ctx,
                             PyObject *value) {
    PyObject *tokens = split_token_list(names, names_len);
    if (tokens == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = PyList_GET_SIZE(tokens);
    int status = -1;
    for (Py_ssize_t token = 0; token < count; token++) {
        PyObject *key = rdfa_expand_term(PyList_GET_ITEM(tokens, token), ctx.vocab, ctx.prefixes);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: expansion fails only on unforceable allocation */
            goto done;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (key == Py_None) {
            Py_DECREF(key);
            continue;
        }
        PyObject *bucket = PyDict_GetItemWithError(properties, key);
        if (bucket != NULL) {
            Py_INCREF(bucket);
        } else {
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: lookup fails only on an unforceable error */
                Py_DECREF(key);     /* GCOVR_EXCL_LINE: error path */
                goto done;          /* GCOVR_EXCL_LINE */
            }
            bucket = PyList_New(0);
            if (bucket == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(key);   /* GCOVR_EXCL_LINE: allocation-failure path */
                goto done;        /* GCOVR_EXCL_LINE */
            }
            if (PyDict_SetItem(properties, key, bucket) < 0) { /* GCOVR_EXCL_BR_LINE: insert fails only on alloc */
                Py_DECREF(key);                                /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(bucket);                             /* GCOVR_EXCL_LINE */
                goto done;                                     /* GCOVR_EXCL_LINE */
            }
        }
        Py_DECREF(key);
        int append_failed = PyList_Append(bucket, value) < 0;
        Py_DECREF(bucket);
        if (append_failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
            goto done;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    status = 0;
done:
    Py_DECREF(tokens);
    return status;
}

typedef struct {
    th_node *next_child;
    rdfa_ctx ctx;
    int vocab_owned;
    PyObject *undo;
} rdfa_frame;

typedef struct {
    rdfa_frame *items;
    Py_ssize_t len;
    Py_ssize_t cap;
} rdfa_stack;

static int rdfa_stack_push(rdfa_stack *stack, rdfa_frame frame) {
    if (stack->len == stack->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(stack->len + 1), (size_t)stack->cap, 8, sizeof(rdfa_frame), &cap, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        rdfa_frame *items = PyMem_Realloc(stack->items, bytes);
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        stack->items = items;
        stack->cap = (Py_ssize_t)cap;
    }
    stack->items[stack->len++] = frame;
    return 0;
}

static void rdfa_stack_clear(rdfa_stack *stack) {
    while (stack->len > 0) {
        rdfa_frame *frame = &stack->items[--stack->len];
        release_child_ctx(&frame->ctx, frame->vocab_owned, frame->undo);
    }
    PyMem_Free(stack->items);
}

enum rdfa_walk_mode { RDFA_WALK_PROPERTIES, RDFA_WALK_ITEMS };

/* Walk DOM depth on a checked heap stack. Nested record construction remains recursive and carries its own
   bound because its depth follows RDFa resources rather than element ancestry. */
static int walk_rdfa(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx, PyObject *output,
                     enum rdfa_walk_mode mode) {
    rdfa_stack stack = {NULL, 0, 0};
    if (rdfa_stack_push(&stack, (rdfa_frame){element->first_child, ctx, 0, NULL}) < 0) { /* GCOVR_EXCL_BR_LINE */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    int status = -1;
    while (stack.len > 0) {
        rdfa_frame *frame = &stack.items[stack.len - 1];
        th_node *child = frame->next_child;
        if (child == NULL) {
            stack.len--;
            if (leave_child_ctx(&frame->ctx, frame->vocab_owned, frame->undo) < 0) { /* GCOVR_EXCL_BR_LINE */
                goto done;                                                           /* GCOVR_EXCL_LINE */
            }
            continue;
        }
        frame->next_child = child->next_sibling;
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        rdfa_ctx child_ctx;
        int vocab_owned;
        PyObject *undo;
        int resolve_failed = resolve_child_ctx(tree, child, frame->ctx, &child_ctx, &vocab_owned, &undo) < 0;
        if (resolve_failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto done;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int descend = 1;
        if (mode == RDFA_WALK_PROPERTIES) {
            Py_ssize_t property = th_node_attr_find(tree, child, "property", 8);
            if (property >= 0 && child->attrs[property].value != NULL) {
                PyObject *value = rdfa_value(tree, state, child, child_ctx);
                if (value == NULL) {
                    release_child_ctx(&child_ctx, vocab_owned, undo);
                    goto done;
                }
                int failed = 0;
                if (value != Py_None) {
                    failed = rdfa_add_property(output, child->attrs[property].value, child->attrs[property].value_len,
                                               child_ctx, value) < 0;
                }
                Py_DECREF(value);
                if (failed) {                                         /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                    release_child_ctx(&child_ctx, vocab_owned, undo); /* GCOVR_EXCL_LINE */
                    goto done;                                        /* GCOVR_EXCL_LINE */
                }
            }
            descend = th_node_attr_find(tree, child, "typeof", 6) < 0;
        } else if (th_node_attr_find(tree, child, "typeof", 6) >= 0) {
            PyObject *item = build_rdfa_item(tree, state, child, child_ctx);
            if (item == NULL) {
                release_child_ctx(&child_ctx, vocab_owned, undo);
                goto done;
            }
            int failed = PyList_Append(output, item) < 0;
            Py_DECREF(item);
            if (failed) { /* GCOVR_EXCL_BR_LINE: append fails only on unforceable allocation */
                release_child_ctx(&child_ctx, vocab_owned, undo); /* GCOVR_EXCL_LINE */
                goto done;                                        /* GCOVR_EXCL_LINE */
            }
            descend = 0;
        }
        if (!descend) {
            if (leave_child_ctx(&child_ctx, vocab_owned, undo) < 0) { /* GCOVR_EXCL_BR_LINE */
                goto done;                                            /* GCOVR_EXCL_LINE */
            }
            continue;
        }
        int push_failed = rdfa_stack_push(&stack, (rdfa_frame){child->first_child, child_ctx, vocab_owned, undo}) < 0;
        if (push_failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            release_child_ctx(&child_ctx, vocab_owned, undo); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();                                 /* GCOVR_EXCL_LINE */
            goto done;                                        /* GCOVR_EXCL_LINE */
        }
    }
    status = 0;
done:
    rdfa_stack_clear(&stack);
    return status;
}

static int collect_rdfa_properties(th_tree *tree, module_state *state, th_node *element, rdfa_ctx ctx,
                                   PyObject *properties) {
    return walk_rdfa(tree, state, element, ctx, properties, RDFA_WALK_PROPERTIES);
}

/* Build one RdfaItem for every top-level RDFa resource, absolutizing IRIs against `base` when the caller passed one.
   NULL only on the excluded allocation-failure path. */
static PyObject *gather_rdfa(PyObject *self, PyObject *base) {
    PyObject *items = PyList_New(0);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    module_state *state = state_of(self);
    th_node *root = ((NodeObject *)self)->node;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    /* A document that never interns a typeof attribute carries no RDFa resource, so skip the walk and the prefix map it
       would need -- this is the whole cost of the RDFa pass on the pages (most of them) that use no RDFa. */
    int has_rdfa = th_attr_lookup(tree, "typeof", 6) != UINT32_MAX;
    if (has_rdfa) {
        PyObject *prefixes = build_default_prefixes();
        failed = prefixes == NULL; /* the build fails only on unforceable allocation */
        if (prefixes != NULL) {    /* GCOVR_EXCL_BR_LINE: the build fails only on unforceable allocation */
            rdfa_ctx ctx = {NULL, prefixes, base, 0};
            failed = walk_rdfa(tree, state, root, ctx, items, RDFA_WALK_ITEMS) < 0;
            Py_DECREF(prefixes);
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {
        Py_DECREF(items);
        return NULL;
    }
    return items;
}

/* Whether a <meta name> value starts with a case-insensitive Dublin Core prefix (dc. or dcterms.). */
static int ucs4_has_prefix_ci(const Py_UCS4 *value, Py_ssize_t len, const char *prefix, Py_ssize_t prefix_len) {
    if (len < prefix_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        Py_UCS4 c = value[index];
        Py_UCS4 folded = lower_ascii(c);
        if (folded != (Py_UCS4)(unsigned char)prefix[index]) {
            return 0;
        }
    }
    return 1;
}

static int is_dc_name(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const prefixes[] = {"dc.", "dcterms."};
    static const Py_ssize_t prefix_lens[] = {3, 8};
    for (size_t index = 0; index < sizeof(prefixes) / sizeof(prefixes[0]); index++) {
        if (ucs4_has_prefix_ci(value, len, prefixes[index], prefix_lens[index])) {
            return 1;
        }
    }
    return 0;
}

/* The UCS4 run as a new str with ASCII A-Z folded to lower case, so a `DC.Title` name keys the same as `dc.title`. NULL
   only on the excluded allocation-failure path. */
static PyObject *ucs4_lower_str(const Py_UCS4 *data, Py_ssize_t len) {
    Py_UCS4 *buffer = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 c = data[index];
        buffer[index] = lower_ascii(c);
    }
    PyObject *result = ucs4_to_str(buffer, len);
    PyMem_Free(buffer);
    return result;
}

/* Map every <meta name="dc.*"> / <meta name="dcterms.*"> name (lower-cased) to its content value, last occurrence
   winning. NULL only on the excluded allocation-failure path. */
static PyObject *gather_dublin_core(PyObject *self) {
    PyObject *result = PyDict_New();
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_META) {
            continue;
        }
        const th_node_attr *name = find_node_attr(node, TH_ATTR_NAME);
        if (name == NULL || name->value == NULL) {
            continue;
        }
        if (!is_dc_name(name->value, name->value_len)) {
            continue;
        }
        PyObject *key = ucs4_lower_str(name->value, name->value_len);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
            break;         /* GCOVR_EXCL_LINE */
        }
        PyObject *content = attr_str_or_empty(tree, node, "content", 7);
        if (content == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(key);    /* GCOVR_EXCL_LINE: allocation-failure path */
            failed = 1;        /* GCOVR_EXCL_LINE */
            break;             /* GCOVR_EXCL_LINE */
        }
        int set_failed = PyDict_SetItem(result, key, content) < 0;
        Py_DECREF(key);
        Py_DECREF(content);
        if (set_failed) { /* GCOVR_EXCL_BR_LINE: insert fails only on unforceable allocation */
            failed = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
            break;        /* GCOVR_EXCL_LINE */
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {          /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    return result;
}

/* Render one Microdata item as nested plain dicts: `type` whitespace-split, `id`, and every property recursively.
   A property value is either a string, which passes through, or a nested item, which shapes the same way. The engine
   behind MicrodataItem.json(). */
static PyObject *microdata_as_dict(PyObject *item, PyObject *item_type) {
    PyObject *kind = PyObject_GetAttrString(item, "type");
    PyObject *identifier = PyObject_GetAttrString(item, "id");
    PyObject *properties = PyObject_GetAttrString(item, "properties");
    PyObject *result = PyDict_New();
    PyObject *shaped = PyDict_New();
    PyObject *rendered = NULL;
    /* GCOVR_EXCL_BR_START: the attributes exist on every registered item and dict allocation cannot be forced */
    if (kind == NULL || identifier == NULL || properties == NULL || result == NULL || shaped == NULL) {
        goto fail; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    if (kind != Py_None) { /* itemtype is a space-separated list of types, the way microdata.Item.json writes it */
        PyObject *split = PyObject_CallMethod(kind, "split", NULL);
        int stored = split == NULL ? -1 : PyDict_SetItemString(result, "type", split); /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(split);
        if (stored < 0) { /* GCOVR_EXCL_BR_LINE: str.split and the insert cannot fail */
            goto fail;    /* GCOVR_EXCL_LINE */
        }
    }
    if (identifier != Py_None) {
        if (PyDict_SetItemString(result, "id", identifier) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
            goto fail;                                            /* GCOVR_EXCL_LINE */
        }
    }
    Py_ssize_t position = 0;
    PyObject *name;
    PyObject *values;
    while (PyDict_Next(properties, &position, &name, &values)) {
        rendered = PyList_New(0);
        if (rendered == NULL) { /* GCOVR_EXCL_BR_LINE: list allocation cannot be forced */
            goto fail;          /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < PyList_GET_SIZE(values); index++) {
            PyObject *value = PyList_GET_ITEM(values, index);
            /* a value is either the property's text, which passes through, or a nested item, which shapes the same */
            int nested = PyObject_TypeCheck(value, (PyTypeObject *)item_type);
            PyObject *entry = nested ? microdata_as_dict(value, item_type) : Py_NewRef(value);
            int added = entry == NULL ? -1 : PyList_Append(rendered, entry); /* GCOVR_EXCL_BR_LINE: allocation */
            Py_XDECREF(entry);
            if (added < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
                goto fail;   /* GCOVR_EXCL_LINE */
            }
        }
        int stored = PyDict_SetItem(shaped, name, rendered);
        Py_DECREF(rendered); /* never NULL here, so Py_CLEAR would add a branch that cannot go both ways */
        rendered = NULL;
        if (stored < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
            goto fail;    /* GCOVR_EXCL_LINE */
        }
    }
    if (PyDict_SetItemString(result, "properties", shaped) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
        goto fail;                                                /* GCOVR_EXCL_LINE */
    }
    Py_DECREF(kind);
    Py_DECREF(identifier);
    Py_DECREF(properties);
    Py_DECREF(shaped);
    return result;
fail: /* GCOVR_EXCL_START: every jump here is an allocation failure that cannot be forced from a test */
    Py_XDECREF(kind);
    Py_XDECREF(identifier);
    Py_XDECREF(properties);
    Py_XDECREF(shaped);
    Py_XDECREF(rendered);
    Py_XDECREF(result);
    return NULL;
    /* GCOVR_EXCL_STOP */
}

/* _microdata_as_dict(item) -> dict: the item as nested plain dicts, ready for json.dumps.

   A nested property value is an item exactly when it shares the outer item's type, so the walk carries that type
   rather than reaching for the registered one. */
PyObject *turbohtml_microdata_as_dict(PyObject *Py_UNUSED(module), PyObject *item) {
    PyObject *properties = PyObject_GetAttrString(item, "properties");
    if (properties == NULL || !PyDict_Check(properties)) {
        Py_XDECREF(properties);
        PyErr_Clear();
        PyErr_SetString(PyExc_TypeError, "expected a MicrodataItem");
        return NULL;
    }
    Py_DECREF(properties);
    return microdata_as_dict(item, (PyObject *)Py_TYPE(item));
}

static int json_ld_nesting_exceeds_cap(PyObject *block);

/* Document.json_ld() -> list. Decodes every JSON-LD block, keeping the ones that carry data.

   The decoder is the registered json.loads: reference JSON parsing is a solved standard problem and belongs to the
   standard library, the way RFC 3986 reference resolution does. Which blocks survive is not -- a block whose JSON is
   malformed, whose nesting is past the depth cap, or whose payload is a scalar or null rather than a node object,
   carries nothing -- so that decision is made here. */
TH_NODE_API(, PyObject *, turbohtml_document_json_ld, (PyObject * self, PyObject *ignored), (self, ignored),
            (PyObject * self, PyObject *Py_UNUSED(ignored)), (NodeObject *)self, NULL) {
    PyObject *texts = gather_json_ld(self);
    if (texts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *parsed = PyList_New(0);
    if (parsed == NULL) { /* GCOVR_EXCL_BR_LINE: list allocation cannot be forced to fail */
        Py_DECREF(texts); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    PyObject *decoder = state_of(self)->json_ld_parser;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(texts); index++) {
        PyObject *block = PyList_GET_ITEM(texts, index);
        if (json_ld_nesting_exceeds_cap(block)) {
            continue;
        }
        PyObject *value = PyObject_CallOneArg(decoder, block);
        if (value == NULL) {
            /* ValueError is malformed JSON or a NaN/Infinity constant the registered decoder refuses; anything else
               (MemoryError, KeyboardInterrupt) must reach the caller. */
            if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: unforceable interpreter error */
                Py_DECREF(texts);                            /* GCOVR_EXCL_LINE */
                Py_DECREF(parsed);                           /* GCOVR_EXCL_LINE */
                return NULL;                                 /* GCOVR_EXCL_LINE */
            }
            PyErr_Clear();
            continue;
        }
        int carries = PyDict_Check(value) || PyList_Check(value);
        int added =
            carries ? PyList_Append(parsed, value) : 0; /* GCOVR_EXCL_BR_LINE: append only fails on allocation */
        Py_DECREF(value);
        if (added < 0) {       /* GCOVR_EXCL_BR_LINE: allocation */
            Py_DECREF(texts);  /* GCOVR_EXCL_LINE */
            Py_DECREF(parsed); /* GCOVR_EXCL_LINE */
            return NULL;       /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(texts);
    return parsed;
}

/* Whether the JSON-LD block nests arrays or objects deeper than STRUCTURED_DATA_MAX_ITEM_DEPTH, the microdata and RDFa
   ceiling, which keeps the stdlib json C decoder within its recursion budget. Closers decrement without a floor: a
   surplus closer makes the block malformed, and the decoder rejects it as invalid JSON before recursing past it. */
static int json_ld_nesting_exceeds_cap(PyObject *block) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(block);
    int kind = PyUnicode_KIND(block);
    const void *data = PyUnicode_DATA(block);
    Py_ssize_t depth = 0;
    int in_string = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (in_string) {
            if (ch == '\\') {
                index++; /* a backslash escapes the next char, so an escaped quote does not close the string */
            } else if (ch == '"') {
                in_string = 0;
            }
            continue;
        }
        if (ch == '"') {
            in_string = 1;
        } else if (ch == '[' || ch == '{') {
            if (++depth > STRUCTURED_DATA_MAX_ITEM_DEPTH) {
                return 1;
            }
        } else if (ch == ']' || ch == '}') {
            depth--;
        }
    }
    return 0;
}

/* The effective document base for an opt-in base_url: the caller's URL validated once (so a malformed one raises an
   actionable ValueError here rather than surfacing deep in the walk) with any <base href> resolved against it (HTML
   spec 4.2.3). A malformed <base href> is ignored so an untrusted page cannot break the extraction. Returns a new
   reference, NULL with an exception set on a bad base_url. */
static PyObject *resolve_base(PyObject *self, PyObject *base_url) {
    PyObject *probe = th_url_resolve(base_url, base_url);
    if (probe == NULL) {
        if (PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: the else is an allocation-only failure */
            PyErr_Clear();
            PyErr_Format(PyExc_ValueError, "base_url %R is not a valid absolute URL to resolve relative URLs against",
                         base_url);
        }
        return NULL;
    }
    Py_DECREF(probe);
    return th_document_base_url(self, base_url);
}

/* Parse the optional base_url the opengraph/microdata/structured_data methods share, `spec` naming the method for the
   error. None or an omitted argument leaves *base NULL (verbatim output); a str resolves the effective document base.
   Returns 0 on success, -1 with an exception on a bad type or a malformed base_url. */
static int parse_base_url(PyObject *self, PyObject *args, PyObject *kwargs, const char *spec, PyObject **base) {
    static char *keywords[] = {"base_url", NULL};
    PyObject *base_url = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, spec, keywords, &base_url)) {
        return -1;
    }
    *base = NULL;
    if (base_url == NULL || base_url == Py_None) {
        return 0;
    }
    if (!PyUnicode_Check(base_url)) {
        PyErr_Format(PyExc_TypeError, "base_url must be a str or None, got %.200s", Py_TYPE(base_url)->tp_name);
        return -1;
    }
    *base = resolve_base(self, base_url);
    return *base == NULL ? -1 : 0;
}

/* Copy the document under its tree lock so combined extraction reads one immutable version without keeping the live
   tree locked across JSON parsing or record constructors. */
static PyObject *snapshot_document(PyObject *self) {
    th_tree *snapshot_tree;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    snapshot_tree = th_tree_copy_document(tree_of(self));
    Py_END_CRITICAL_SECTION();
    if (snapshot_tree == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(state_of(self), snapshot_tree, th_tree_document(snapshot_tree));
}

static PyObject *build_opengraph_record(PyObject *self, PyObject *base) {
    PyObject *properties = gather_opengraph(self, base, 1);
    if (properties == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *record = PyObject_CallOneArg(state_of(self)->opengraph_type, properties);
    Py_DECREF(properties);
    return record;
}

/* Document.opengraph(base_url=None) -> OpenGraph. */
TH_NODE_API(, PyObject *, turbohtml_document_opengraph, (PyObject * self, PyObject *args, PyObject *kwargs),
            (self, args, kwargs), (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    PyObject *base = NULL;
    if (parse_base_url(self, args, kwargs, "|O:opengraph", &base) < 0) {
        return NULL;
    }
    PyObject *result = build_opengraph_record(self, base);
    Py_XDECREF(base);
    return result;
}

/* Document.microdata(base_url=None) -> list[MicrodataItem]. */
TH_NODE_API(, PyObject *, turbohtml_document_microdata, (PyObject * self, PyObject *args, PyObject *kwargs),
            (self, args, kwargs), (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    PyObject *base = NULL;
    if (parse_base_url(self, args, kwargs, "|O:microdata", &base) < 0) {
        return NULL;
    }
    PyObject *result = gather_microdata(self, base);
    Py_XDECREF(base);
    return result;
}

/* Document.rdfa(base_url=None) -> list[RdfaItem]. */
TH_NODE_API(, PyObject *, turbohtml_document_rdfa, (PyObject * self, PyObject *args, PyObject *kwargs),
            (self, args, kwargs), (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    PyObject *base = NULL;
    if (parse_base_url(self, args, kwargs, "|O:rdfa", &base) < 0) {
        return NULL;
    }
    PyObject *result = gather_rdfa(self, base);
    Py_XDECREF(base);
    return result;
}

/* Document.dublin_core() -> dict. */
TH_NODE_API(, PyObject *, turbohtml_document_dublin_core, (PyObject * self, PyObject *ignored), (self, ignored),
            (PyObject * self, PyObject *Py_UNUSED(ignored)), (NodeObject *)self, NULL) {
    return gather_dublin_core(self);
}

/* Document.structured_data(base_url=None) -> StructuredData(json_ld, microdata, opengraph, microformats, rdfa,
   dublin_core). A base_url absolutizes the microdata, opengraph, and RDFa URL fields; json_ld stays verbatim (resolving
   @id inside arbitrary JSON is a separate concern) and Dublin Core content is verbatim (its values are literals, not
   typed URLs). microformats is still a later phase, present as an empty list so the record's shape is stable. NULL only
   on the excluded allocation-failure path (or with an exception set on a bad base_url). */
TH_NODE_API(, PyObject *, turbohtml_document_structured_data, (PyObject * self, PyObject *args, PyObject *kwargs),
            (self, args, kwargs), (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    PyObject *snapshot = snapshot_document(self);
    if (snapshot == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *base = NULL;
    if (parse_base_url(snapshot, args, kwargs, "|O:structured_data", &base) < 0) {
        Py_DECREF(snapshot);
        return NULL;
    }
    int has_script = 0;
    int has_meta = 0;
    int has_microdata = 0;
    th_node *const root = ((NodeObject *)snapshot)->node;
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_ELEMENT) {
            has_script |= node->atom == TH_TAG_SCRIPT;
            has_meta |= node->atom == TH_TAG_META;
            has_microdata |= find_node_attr(node, TH_ATTR_ITEMSCOPE) != NULL;
            if (has_script && has_meta && has_microdata) {
                break;
            }
        }
    }
    PyObject *sections = PyTuple_New(6);
    if (sections == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(snapshot); /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(base);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    int failed =
        tuple_set_or_fail(sections, 0, has_script ? turbohtml_document_json_ld(snapshot, NULL) : PyList_New(0)) < 0;
    if (!failed) { /* GCOVR_EXCL_BR_LINE: JSON parsing returns a list */
        failed = tuple_set_or_fail(sections, 1, has_microdata ? gather_microdata(snapshot, base) : PyList_New(0)) < 0;
    }
    if (!failed) {
        failed = tuple_set_or_fail(sections, 2, has_meta ? gather_opengraph(snapshot, base, 0) : PyDict_New()) < 0;
    }
    if (!failed) { /* GCOVR_EXCL_BR_LINE: the previous dict build fails only on allocation */
        failed = tuple_set_or_fail(sections, 3, PyList_New(0)) < 0; /* GCOVR_EXCL_BR_LINE */
    }
    if (!failed) { /* GCOVR_EXCL_BR_LINE: list allocation cannot be forced from a test */
        failed = tuple_set_or_fail(sections, 4, gather_rdfa(snapshot, base)) < 0;
    }
    if (!failed) {
        failed = tuple_set_or_fail(sections, 5, has_meta ? gather_dublin_core(snapshot) : PyDict_New()) < 0;
    }
    Py_DECREF(snapshot);
    Py_XDECREF(base);
    if (failed) {
        Py_DECREF(sections);
        return NULL;
    }
    PyObject *result = PyObject_Call(state_of(self)->structured_data_type, sections, NULL);
    Py_DECREF(sections);
    return result;
}

/* Store the JSON-LD text parser and the MicrodataItem / RdfaItem / StructuredData / OpenGraph record classes the C
   walks build their results from; turbohtml._structured_data registers all five on import. */
PyObject *turbohtml_register_structured_data(PyObject *module, PyObject *args) {
    PyObject *parser = NULL;
    PyObject *microdata_item = NULL;
    PyObject *rdfa_item = NULL;
    PyObject *structured_data = NULL;
    PyObject *opengraph = NULL;
    int parsed = PyArg_ParseTuple(args, "OOOOO", &parser, &microdata_item, &rdfa_item, &structured_data, &opengraph);
    if (!parsed) {   /* GCOVR_EXCL_BR_LINE: the facade always registers with five callables */
        return NULL; /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->json_ld_parser, Py_NewRef(parser));
    Py_XSETREF(state->microdata_item_type, Py_NewRef(microdata_item));
    Py_XSETREF(state->rdfa_item_type, Py_NewRef(rdfa_item));
    Py_XSETREF(state->structured_data_type, Py_NewRef(structured_data));
    Py_XSETREF(state->opengraph_type, Py_NewRef(opengraph));
    Py_RETURN_NONE;
}
