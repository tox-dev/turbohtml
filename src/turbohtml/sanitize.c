/* The HTML sanitizer's allowlist walk, kept in C so only the policy facade is Python.

   turbohtml/sanitizer.py parses the input into a tree and serializes the result; this file is the middle, the part that
   runs once per node: keep an allowed element, drop a disallowed attribute, normalize a URL scheme, escape or unwrap or
   remove a tag the policy rejects. It mutates the parsed tree in place with the treebuilder's edit primitives, so the
   Python layer only compiles the policy into the frozensets passed here and reads back the serialized result. The
   safety baseline (scripting/framing elements, on* handlers, non-allowlisted URL schemes) is enforced here, not in
   Python, so a policy cannot route around it. */

#include "turbohtml.h"

#include "treebuilder.h"

#include <string.h>

enum on_disallowed { ON_ESCAPE = 0, ON_STRIP = 1, ON_REMOVE = 2 };

/* The compiled policy and the tree being walked, threaded through the recursion. */
typedef struct {
    th_tree *tree;
    PyObject *tags;             /* frozenset[str]: allowed element names */
    PyObject *attributes;       /* Mapping[str, frozenset[str]]: per-tag allowed attribute names, "*" wildcards */
    PyObject *wildcard_attrs;   /* attributes.get("*"), borrowed, or NULL */
    PyObject *url_schemes;      /* frozenset[str]: allowed URL schemes, lowercase */
    PyObject *star;             /* the interned "*" string, for the any-name wildcard */
    PyObject *add_link_rel;     /* str to set as an <a> rel, or None */
    PyObject *attribute_filter; /* callable (tag, name, value) -> str | None, or None */
    PyObject *set_attributes;   /* dict[str, dict[str, str]]: per-tag attribute values to force-set on kept elements */
    PyObject *remove_with_content; /* frozenset[str]: disallowed tags whose whole subtree is dropped, not escaped */
    PyObject *css_properties;      /* frozenset[str]: CSS property names kept when scrubbing a `style` attribute */
    int allow_relative;
    int on_disallowed;
    int strip_comments;
} sanitizer;

/* Elements removed regardless of the allowlist: scripting, plugin, and raw-text containers. (frame and frameset need
   no entry: the parser drops them outside a frameset document, so a fragment can never contain one to neutralize.) */
static int is_unsafe_tag(uint16_t atom) {
    switch (atom) {
    case TH_TAG_SCRIPT:
    case TH_TAG_STYLE:
    case TH_TAG_IFRAME:
    case TH_TAG_EMBED:
    case TH_TAG_OBJECT:
    case TH_TAG_NOSCRIPT:
    case TH_TAG_NOEMBED:
    case TH_TAG_NOFRAMES:
    case TH_TAG_BASE:
    case TH_TAG_BASEFONT:
    case TH_TAG_TITLE:
    case TH_TAG_PLAINTEXT:
    case TH_TAG_XMP:
    case TH_TAG_TEMPLATE:
        return 1;
    default:
        return 0;
    }
}

/* HTML void elements, which serialize without an end tag. */
static int is_void_tag(uint16_t atom) {
    switch (atom) {
    case TH_TAG_AREA:
    case TH_TAG_BASE:
    case TH_TAG_BASEFONT:
    case TH_TAG_BR:
    case TH_TAG_COL:
    case TH_TAG_EMBED:
    case TH_TAG_HR:
    case TH_TAG_IMG:
    case TH_TAG_INPUT:
    case TH_TAG_KEYGEN:
    case TH_TAG_LINK:
    case TH_TAG_META:
    case TH_TAG_PARAM:
    case TH_TAG_SOURCE:
    case TH_TAG_TRACK:
    case TH_TAG_WBR:
        return 1;
    default:
        return 0;
    }
}

/* Attributes whose value is a URL, so its scheme is checked against the allowlist. Matched on the interned name bytes.
 */
static int is_url_attr(const char *name, Py_ssize_t len) {
    switch (len) {
    case 3:
        return memcmp(name, "src", 3) == 0;
    case 4:
        return memcmp(name, "href", 4) == 0 || memcmp(name, "cite", 4) == 0 || memcmp(name, "data", 4) == 0 ||
               memcmp(name, "ping", 4) == 0;
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

static int is_scheme_char(Py_UCS4 c) {
    Py_UCS4 lower = c | 0x20;
    return (lower >= 'a' && lower <= 'z') || (c >= '0' && c <= '9') || c == '+' || c == '-' || c == '.';
}

/* Bytes to ignore when reading a URL scheme: the control characters and whitespace browsers strip, plus the
   zero-width and soft-hyphen format characters that obfuscate a scheme, so java&zwsp;script: is still caught. */
static int is_url_ignorable(Py_UCS4 c) {
    switch (c) {
    case 0x00AD: /* soft hyphen */
    case 0x200B: /* zero-width space */
    case 0x200C: /* zero-width non-joiner */
    case 0x200D: /* zero-width joiner */
    case 0x2060: /* word joiner */
    case 0xFEFF: /* zero-width no-break space / BOM */
        return 1;
    default:
        return c <= 0x20 || c == 0x7F;
    }
}

/* Read the URL's scheme the way a browser does -- skipping the whitespace and control bytes it ignores -- and allow the
   attribute only if that scheme is on the allowlist, or there is no scheme and relative URLs are allowed. The parser
   has already resolved entity references, so the value arrives decoded. Returns 1 allow, 0 drop, -1 error. */
static int scheme_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t len) {
    char scheme[40];
    Py_ssize_t length = 0;
    int started = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 c = value[index];
        if (is_url_ignorable(c)) {
            continue;
        }
        if (c == ':' && started) {
            PyObject *name = PyUnicode_FromStringAndSize(scheme, length);
            if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int allowed = PySet_Contains(s->url_schemes, name);
            Py_DECREF(name);
            return allowed;
        }
        int letter = (c | 0x20) >= 'a' && (c | 0x20) <= 'z';
        /* before the colon, every byte must be a scheme byte and the first must be a letter (so 1http:// is relative)
         */
        if (started ? !is_scheme_char(c) : !letter) {
            return s->allow_relative;
        }
        if (length < (Py_ssize_t)sizeof(scheme)) { /* cap the buffer but keep scanning, so an over-long scheme is */
            scheme[length++] = (char)(letter ? (c | 0x20) : c); /* recorded truncated and never matches the allowlist */
        }
        started = 1;
    }
    return s->allow_relative; /* no colon: a relative URL */
}

/* The ASCII whitespace that separates srcset candidates, minus CR: the WHATWG input preprocessor converts CR to LF, so
   a parsed attribute value never carries a 0x0D. An array+loop keeps the branch count stable when clang inlines this
   into srcset_allowed's two call sites. */
static int is_ascii_ws(Py_UCS4 c) {
    static const Py_UCS4 whitespace[] = {0x09, 0x0A, 0x0C, 0x20};
    for (size_t index = 0; index < sizeof(whitespace) / sizeof(whitespace[0]); index++) {
        if (c == whitespace[index]) {
            return 1;
        }
    }
    return 0;
}

/* srcset and imagesrcset hold a comma-separated list of "URL descriptor" candidates. Each candidate's leading URL is
   scheme-checked; the whole attribute is rejected if any candidate carries a disallowed scheme. Splitting on commas
   can over-segment a URL that contains one, but that only adds scheme checks of schemeless tails (read as relative),
   so it never lets a disallowed scheme through. Returns 1 allow, 0 drop, -1 error. */
static int srcset_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && (value[pos] == ',' || is_ascii_ws(value[pos]))) {
            pos++; /* skip separators before the candidate URL */
        }
        Py_ssize_t start = pos;
        while (pos < len && value[pos] != ',' && !is_ascii_ws(value[pos])) {
            pos++; /* the URL runs up to a descriptor (whitespace) or the next candidate (comma) */
        }
        if (pos > start) {
            int ok = scheme_allowed(s, value + start, pos - start);
            if (ok < 0) {  /* GCOVR_EXCL_BR_LINE: scheme_allowed only fails on allocation failure */
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (!ok) {
                return 0;
            }
        }
        while (pos < len && value[pos] != ',') {
            pos++; /* skip the candidate's descriptor */
        }
    }
    return 1;
}

/* srcset-valued attributes, matched on the interned name bytes. */
static int is_srcset_attr(const char *name, Py_ssize_t len) {
    if (len == 6) {
        return memcmp(name, "srcset", 6) == 0;
    }
    if (len == 11) {
        return memcmp(name, "imagesrcset", 11) == 0;
    }
    return 0;
}

/* Is `name` allowed on element `tag` by the policy? A "*" inside an attribute set allows every attribute name.
   Returns 1 allow, 0 drop, -1 error. */
static int attr_allowed(sanitizer *s, PyObject *tag, const char *name, Py_ssize_t len) {
    PyObject *attr = PyUnicode_FromStringAndSize(name, len);
    if (attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *sets[2] = {PyDict_GetItem(s->attributes, tag), s->wildcard_attrs};
    int allowed = 0;
    for (int which = 0; which < 2 && !allowed; which++) {
        if (sets[which] != NULL) {
            allowed = PySet_Contains(sets[which], attr) || PySet_Contains(sets[which], s->star);
        }
    }
    Py_DECREF(attr);
    return allowed;
}

/* Does the element carry an attribute named `name`? Scans the interned names. */
static int has_attr(sanitizer *s, th_node *element, const char *name, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        Py_ssize_t got_len = 0;
        const char *got = th_attr_name(s->tree, element->attrs[index].name_atom, &got_len);
        if (got_len == len && memcmp(got, name, (size_t)len) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Apply the optional attribute filter, replacing the attribute's value or deleting it. Returns 0, or -1 on error. */
static int run_attribute_filter(sanitizer *s, th_node *element, PyObject *tag, const char *name, Py_ssize_t name_len,
                                const Py_UCS4 *value, Py_ssize_t value_len) {
    PyObject *attr = PyUnicode_FromStringAndSize(name, name_len);
    PyObject *text = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value, value_len);
    if (attr == NULL || text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_XDECREF(attr);               /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(text);               /* GCOVR_EXCL_LINE */
        return -1;                      /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyObject_CallFunctionObjArgs(s->attribute_filter, tag, attr, text, NULL);
    int status = 0;
    if (result == NULL) {
        status = -1;
    } else if (result == Py_None) {
        th_node_attr_del(s->tree, element, name, name_len);
    } else if (!PyUnicode_Check(result)) {
        PyErr_SetString(PyExc_TypeError, "an attribute filter must return str or None");
        status = -1;
    } else if (PyUnicode_Compare(result, text) != 0) {
        Py_UCS4 *points = PyUnicode_AsUCS4Copy(result);
        if (points == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(attr);   /* GCOVR_EXCL_LINE */
            Py_DECREF(text);   /* GCOVR_EXCL_LINE */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        status = th_node_attr_set(s->tree, element, name, name_len, points, PyUnicode_GET_LENGTH(result), 1);
        PyMem_Free(points);
    }
    Py_XDECREF(result);
    Py_DECREF(attr);
    Py_DECREF(text);
    return status;
}

/* Strip every attribute the policy rejects from an allowed element, then add the configured link rel. */
/* Force-set the policy's per-tag attribute values on a kept element, adding the attribute if absent and overwriting it
   if present (what attribute_filter cannot do, since it only sees attributes already there). */
static int apply_set_attributes(sanitizer *s, th_node *element, PyObject *tag) {
    PyObject *per_tag = PyDict_GetItemWithError(s->set_attributes, tag);
    if (per_tag == NULL) {
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: PyDict_GetItemWithError only errors on a non-hashable key */
            return -1;          /* GCOVR_EXCL_LINE: error path */
        }
        return 0; /* no forced attributes for this tag */
    }
    PyObject *name, *value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(per_tag, &pos, &name, &value)) {
        Py_ssize_t name_len = 0;
        const char *name_bytes = PyUnicode_AsUTF8AndSize(name, &name_len);
        if (name_bytes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_UCS4 *points = PyUnicode_AsUCS4Copy(value);
        if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int status = th_node_attr_set(s->tree, element, name_bytes, name_len, points, PyUnicode_GET_LENGTH(value), 1);
        PyMem_Free(points);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* A CSS property name is ASCII letters, digits, and '-', so an ASCII lowercase is enough to look it up. Returns 1
   allow, 0 drop, -1 error. */
static int css_property_allowed(sanitizer *s, const Py_UCS4 *name, Py_ssize_t len) {
    char lowered[64];
    if (len == 0 || len >= (Py_ssize_t)sizeof(lowered)) {
        return 0; /* empty, or longer than any real property name */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 c = name[index];
        lowered[index] = (char)((c >= 'A' && c <= 'Z') ? (c | 0x20) : c);
    }
    PyObject *key = PyUnicode_FromStringAndSize(lowered, len);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int allowed = PySet_Contains(s->css_properties, key);
    Py_DECREF(key);
    return allowed;
}

/* Append the declaration `value[start:end)` to `out` if the property name (the part before `colon`) is allowlisted,
   trimming surrounding whitespace and joining kept declarations with "; ". Returns 0, or -1 on error. */
static int css_flush_declaration(sanitizer *s, const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end, Py_ssize_t colon,
                                 Py_UCS4 *out, Py_ssize_t *out_len) {
    if (colon < start) {
        return 0; /* no property:value split in this declaration, so drop it */
    }
    Py_ssize_t name_start = start;
    Py_ssize_t name_end = colon;
    while (name_start < name_end && is_ascii_ws(value[name_start])) {
        name_start++;
    }
    while (name_end > name_start && is_ascii_ws(value[name_end - 1])) {
        name_end--;
    }
    int allowed = css_property_allowed(s, value + name_start, name_end - name_start);
    if (allowed < 0) { /* GCOVR_EXCL_BR_LINE: css_property_allowed only fails on allocation failure */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!allowed) {
        return 0;
    }
    /* output from the trimmed name start to the value's last non-whitespace byte (the leading whitespace was already
       trimmed into name_start, so a kept declaration is emitted without its surrounding whitespace) */
    Py_ssize_t decl_end = end;
    while (decl_end > colon + 1 && is_ascii_ws(value[decl_end - 1])) {
        decl_end--;
    }
    if (*out_len > 0) {
        out[(*out_len)++] = ';';
        out[(*out_len)++] = ' ';
    }
    for (Py_ssize_t index = name_start; index < decl_end; index++) {
        out[(*out_len)++] = value[index];
    }
    return 0;
}

/* Scrub a kept `style` attribute: keep only declarations whose property name is allowlisted. The value is a CSS
   declaration list; split it on top-level ';' and ':' while skipping strings, comments, and parenthesised groups so a
   separator inside url()/quotes/comments is not mistaken for one. Rewrites the attribute, deleting it if nothing
   survives. Returns 0 on success, -1 on error. */
static int sanitize_style(sanitizer *s, th_node *element, th_node_attr *attr) {
    const Py_UCS4 *value = attr->value;
    Py_ssize_t len = attr->value_len;
    Py_UCS4 *out = PyMem_Malloc((size_t)(2 * len + 2) * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t out_len = 0;
    Py_ssize_t decl_start = 0;
    Py_ssize_t colon = -1;
    int mode = 0; /* 0 normal, 1 string, 2 comment */
    Py_UCS4 quote = 0;
    int depth = 0;
    for (Py_ssize_t index = 0; index <= len; index++) {
        int boundary = index == len; /* the end of the value flushes the final declaration */
        Py_UCS4 c = boundary ? 0 : value[index];
        if (!boundary && mode == 2) {
            if (c == '*' && index + 1 < len && value[index + 1] == '/') {
                mode = 0;
                index++;
            }
            continue;
        }
        if (!boundary && mode == 1) {
            if (c == '\\') {
                index++; /* a backslash escapes the next byte, even a quote */
            } else if (c == quote) {
                mode = 0;
            }
            continue;
        }
        if (!boundary) {
            if (c == '/' && index + 1 < len && value[index + 1] == '*') {
                mode = 2;
                index++;
                continue;
            }
            if (c == '"' || c == '\'') {
                mode = 1;
                quote = c;
                continue;
            }
            if (c == '(') {
                depth++;
                continue;
            }
            if (c == ')') {
                depth -= depth > 0;
                continue;
            }
            if (depth > 0) {
                continue;
            }
            if (c == ':' && colon < 0) {
                colon = index;
                continue;
            }
            if (c != ';') {
                continue;
            }
        }
        int flushed = css_flush_declaration(s, value, decl_start, index, colon, out, &out_len);
        if (flushed < 0) {   /* GCOVR_EXCL_BR_LINE: css_flush_declaration only fails on allocation failure */
            PyMem_Free(out); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        decl_start = index + 1;
        colon = -1;
    }
    if (out_len == 0) {
        th_node_attr_del(s->tree, element, "style", 5);
        PyMem_Free(out);
        return 0;
    }
    int result = th_node_attr_set(s->tree, element, "style", 5, out, out_len, 1);
    PyMem_Free(out);
    return result;
}

static int sanitize_attributes(sanitizer *s, th_node *element, PyObject *tag) {
    Py_ssize_t index = 0;
    while (index < element->attr_count) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
        int drop = name_len >= 2 && name[0] == 'o' && name[1] == 'n';
        if (!drop) {
            int allowed = attr_allowed(s, tag, name, name_len);
            if (allowed < 0) { /* GCOVR_EXCL_BR_LINE: attr_allowed only fails on allocation failure */
                return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            drop = !allowed;
        }
        int url_disallowed = 0;
        if (!drop && is_url_attr(name, name_len)) {
            int ok = scheme_allowed(s, attr->value, attr->value_len);
            if (ok < 0) {  /* GCOVR_EXCL_BR_LINE: scheme_allowed only fails on allocation failure */
                return -1; /* GCOVR_EXCL_LINE */
            }
            url_disallowed = !ok;
        }
        if (!drop && is_srcset_attr(name, name_len)) {
            int ok = srcset_allowed(s, attr->value, attr->value_len);
            if (ok < 0) {  /* GCOVR_EXCL_BR_LINE: srcset_allowed only fails on allocation failure */
                return -1; /* GCOVR_EXCL_LINE */
            }
            url_disallowed = !ok;
        }
        if (url_disallowed) {
            /* A duplicate URL attribute desyncs the per-value scheme check from the
               single value a re-parsing browser keeps (WHATWG keeps the first): dropping
               only this occurrence deletes the first match by name, which may be a benign
               sibling, leaving the disallowed value as the lone attribute. Drop every
               occurrence of the name so no disallowed scheme survives, then rescan from
               the start because removing an earlier slot shifts the rest down. */
            while (th_node_attr_del(s->tree, element, name, name_len)) {
            }
            index = 0;
            continue;
        }
        if (drop) {
            th_node_attr_del(s->tree, element, name, name_len);
            continue; /* the next attribute shifted into this slot */
        }
        if (name_len == 5 && memcmp(name, "style", 5) == 0) {
            Py_ssize_t before = element->attr_count;
            if (sanitize_style(s, element, attr) < 0) { /* GCOVR_EXCL_BR_LINE: only on allocation failure */
                return -1;                              /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (element->attr_count < before) {
                continue; /* the style scrubbed to empty and was deleted */
            }
        }
        if (s->attribute_filter != Py_None) {
            Py_ssize_t before = element->attr_count;
            if (run_attribute_filter(s, element, tag, name, name_len, attr->value, attr->value_len) < 0) {
                return -1;
            }
            if (element->attr_count < before) {
                continue; /* the filter deleted it */
            }
        }
        index++;
    }
    if (s->add_link_rel != Py_None && element->atom == TH_TAG_A && has_attr(s, element, "href", 4)) {
        Py_UCS4 *points = PyUnicode_AsUCS4Copy(s->add_link_rel);
        if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int status = th_node_attr_set(s->tree, element, "rel", 3, points, PyUnicode_GET_LENGTH(s->add_link_rel), 1);
        PyMem_Free(points);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (apply_set_attributes(s, element, tag) < 0) { /* GCOVR_EXCL_BR_LINE: only on allocation failure */
        return -1;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

static int sanitize_children(sanitizer *s, th_node *parent, int parent_kept);

/* Make a Text node owning a copy of `text` in the walked tree. */
static th_node *make_text(sanitizer *s, PyObject *text) {
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(text);
    if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *node = th_tree_make_data_node(s->tree, TH_NODE_TEXT, points, PyUnicode_GET_LENGTH(text));
    PyMem_Free(points);
    return node;
}

/* Build a disallowed element's start tag as a raw string; the serializer escapes the <, >, and & when it emits it. */
static PyObject *open_tag(sanitizer *s, th_node *element) {
    PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, element->text, element->text_len);
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *out = PyUnicode_FromFormat("<%U", tag);
    Py_DECREF(tag);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
        PyObject *name_str = PyUnicode_FromStringAndSize(name, name_len);
        if (name_str == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(out);     /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
        PyObject *piece;
        if (attr->value == NULL) {
            piece = PyUnicode_FromFormat(" %U", name_str);
        } else {
            PyObject *value = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len);
            if (value == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(name_str); /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(out);      /* GCOVR_EXCL_LINE */
                return NULL;         /* GCOVR_EXCL_LINE */
            }
            piece = PyUnicode_FromFormat(" %U=\"%U\"", name_str, value);
            Py_DECREF(value);
        }
        Py_DECREF(name_str);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(out);  /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        Py_SETREF(out, PyUnicode_Concat(out, piece));
        Py_DECREF(piece);
        if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_SETREF(out, PyUnicode_FromFormat("%U>", out));
    return out; /* NULL on allocation failure; the caller checks */
}

/* Move an element's children up before it (used by both escape and strip). */
static void hoist_children(th_node *element) {
    th_node *parent = element->parent;
    th_node *child = element->first_child;
    while (child != NULL) {
        th_node *next = child->next_sibling;
        th_node_remove(child);
        th_node_insert_before(parent, child, element);
        child = next;
    }
}

/* Replace a disallowed element with its escaped start tag, its sanitized children, and its escaped end tag. */
static int escape_element(sanitizer *s, th_node *element) {
    /* the element is being escaped to text, so its children lose this element as a
       kept ancestor (parent_kept = 0): a foreign child must not survive into HTML */
    if (sanitize_children(s, element, 0) < 0) {
        return -1;
    }
    PyObject *opening = open_tag(s, element);
    if (opening == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *open_node = make_text(s, opening);
    Py_DECREF(opening);
    if (open_node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node_insert_before(element->parent, open_node, element);
    hoist_children(element);
    if (!is_void_tag(element->atom)) {
        PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, element->text, element->text_len);
        if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *closing = PyUnicode_FromFormat("</%U>", tag);
        Py_DECREF(tag);
        if (closing == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node *close_node = make_text(s, closing);
        Py_DECREF(closing);
        if (close_node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_insert_before(element->parent, close_node, element);
    }
    th_node_remove(element);
    return 0;
}

/* Keep, escape, strip, or remove one element according to the policy. parent_kept is
   1 when the element's parent is itself being kept; an allowlisted foreign (SVG/MathML)
   element is kept only then, so it never outlives its namespace context (e.g. an svg
   <a> must not survive into HTML as a live anchor when its <svg> is escaped). */
static int sanitize_element(sanitizer *s, th_node *element, int parent_kept) {
    int is_html = element->ns == TH_NS_HTML;
    PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, element->text, element->text_len);
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* an allowlisted element matches by its serialized name (a foreign element by
       e.g. "svg"/"foreignObject"); the unsafe-tag set still escapes scripting and
       raw-text elements in any namespace, and a foreign element also needs kept
       context so it stays inside real foreign markup */
    int allowed = is_unsafe_tag(element->atom) ? 0 : PySet_Contains(s->tags, tag);
    if (allowed > 0 && !is_html && !parent_kept) {
        allowed = 0;
    }
    /* only disallowed elements consult remove_with_content, so a kept element never pays the set lookup */
    int remove_content = allowed > 0 ? 0 : PySet_Contains(s->remove_with_content, tag);
    int status = 0;
    if (allowed < 0 || remove_content < 0) { /* GCOVR_EXCL_BR_LINE: PySet_Contains never fails on a str key */
        status = -1;                         /* GCOVR_EXCL_LINE */
    } else if (allowed) {
        status = sanitize_attributes(s, element, tag) < 0 ? -1 : sanitize_children(s, element, 1);
    } else if (remove_content || s->on_disallowed == ON_REMOVE || (s->on_disallowed == ON_STRIP && !is_html)) {
        /* drop the whole subtree: a content-removal tag (e.g. script/style, so its text never leaks), REMOVE mode, or
           foreign content under STRIP (unwrapping it would invite namespace confusion) */
        th_node_remove(element);
    } else if (s->on_disallowed == ON_STRIP) {
        if ((status = sanitize_children(s, element, 0)) == 0) {
            hoist_children(element);
            th_node_remove(element);
        }
    } else {
        status = escape_element(s, element);
    }
    Py_DECREF(tag);
    return status;
}

/* Walk an element's children, applying the policy; capturing the next sibling keeps the loop safe across edits. */
static int sanitize_children(sanitizer *s, th_node *parent, int parent_kept) {
    th_node *child = parent->first_child;
    while (child != NULL) {
        th_node *next = child->next_sibling;
        if (child->type == TH_NODE_ELEMENT) {
            if (sanitize_element(s, child, parent_kept) < 0) {
                return -1;
            }
        } else if (child->type == TH_NODE_COMMENT) {
            if (s->strip_comments) {
                th_node_remove(child);
            }
        } else if (child->type != TH_NODE_TEXT) {
            th_node_remove(child); /* doctype, processing instruction, CDATA: never valid in a sanitized fragment */
        }
        child = next;
    }
    return 0;
}

/* _sanitize(element, tags, attributes, url_schemes, allow_relative, on_disallowed, strip_comments, add_link_rel,
   attribute_filter, set_attributes, remove_with_content, css_properties) -> None. Filters the fragment in place;
   sanitizer.py serializes it. */
PyObject *turbohtml_sanitize(PyObject *module, PyObject *args) {
    PyObject *element;
    sanitizer s = {0};
    if (!PyArg_ParseTuple(args, "OOOOpipOOOOO:_sanitize", &element, &s.tags, &s.attributes, &s.url_schemes,
                          &s.allow_relative, &s.on_disallowed, &s.strip_comments, &s.add_link_rel, &s.attribute_filter,
                          &s.set_attributes, &s.remove_with_content, &s.css_properties)) {
        return NULL;
    }
    th_node *root;
    if (turbohtml_node_borrow(module, element, &s.tree, &root) < 0) {
        return NULL;
    }
    s.star = PyUnicode_InternFromString("*");
    if (s.star == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    s.wildcard_attrs = PyDict_GetItemWithError(s.attributes, s.star);
    if (s.wildcard_attrs == NULL && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the "*" lookup cannot itself error */
        Py_DECREF(s.star);                              /* GCOVR_EXCL_LINE */
        return NULL;                                    /* GCOVR_EXCL_LINE */
    }
    int failed = sanitize_children(&s, root, 1) < 0; /* the fragment root is kept context */
    Py_DECREF(s.star);
    return failed ? NULL : Py_NewRef(Py_None);
}
