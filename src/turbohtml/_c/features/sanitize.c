/* The HTML sanitizer's allowlist walk, kept in C so only the policy facade is Python.

   turbohtml/sanitizer.py parses the input into a tree and serializes the result; this file is the middle, the part that
   runs once per node: keep an allowed element, drop a disallowed attribute, normalize a URL scheme, escape or unwrap or
   remove a tag the policy rejects. It mutates the parsed tree in place with the treebuilder's edit primitives, so the
   Python layer only compiles the policy into the frozensets passed here and reads back the serialized result. The
   safety baseline (scripting/framing elements, on* handlers, non-allowlisted URL schemes) is enforced here, not in
   Python, so a policy cannot route around it. */

#include "core/ascii.h"
#include "core/common.h"
#include "url/url.h"

#include "dom/tree.h"

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
    PyObject *attribute_prefixes;  /* frozenset[str]: allow any attribute whose name starts with one of these */
    PyObject *attribute_values;    /* dict[str, dict[str, frozenset[str]]]: per (tag, attr) literal value allowlist */
    PyObject *media_hosts; /* frozenset[str]: allowed hosts for an embedded-media (audio/video/source/track) src */
    PyObject
        *removed; /* list to append (tag, attr_or_None) records to as the walk drops things, or NULL to not report */
    int allow_relative;
    int on_disallowed;
    int strip_comments;
    int strip_templates; /* SAFE_FOR_TEMPLATES: collapse {{ }}, ${ }, <% %> runs so kept text/attrs stay template-safe
                          */
} sanitizer;

/* Append one dropped item to the audit list when reporting is on: (tag, None) for a removed or escaped element, (tag,
   attribute_name) for a stripped attribute. A no-op when s->removed is NULL, the common non-reporting path. Returns 0,
   or -1 on error. */
static int record_removed(sanitizer *s, PyObject *tag, const char *attr, Py_ssize_t attr_len) {
    if (s->removed == NULL) {
        return 0;
    }
    PyObject *name = attr == NULL ? Py_NewRef(Py_None) : PyUnicode_FromStringAndSize(attr, attr_len);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *record = PyTuple_Pack(2, tag, name);
    Py_DECREF(name);
    if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int status = PyList_Append(s->removed, record);
    Py_DECREF(record);
    return status;
}

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
        int letter = th_scheme_start(c);
        /* before the colon, every byte must be a scheme byte and the first must be a letter (so 1http:// is relative)
         */
        if (started ? !th_scheme_char(c) : !letter) {
            return s->allow_relative;
        }
        if (length < (Py_ssize_t)sizeof(scheme)) { /* cap the buffer but keep scanning, so an over-long scheme is */
            scheme[length++] = (char)(letter ? (c | 0x20) : c); /* recorded truncated and never matches the allowlist */
        }
        started = 1;
    }
    return s->allow_relative; /* no colon: a relative URL */
}

/* srcset and imagesrcset hold a comma-separated list of "URL descriptor" candidates. Each candidate's leading URL is
   scheme-checked; the whole attribute is rejected if any candidate carries a disallowed scheme. Splitting on commas
   can over-segment a URL that contains one, but that only adds scheme checks of schemeless tails (read as relative),
   so it never lets a disallowed scheme through. Returns 1 allow, 0 drop, -1 error. */
static int srcset_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && (value[pos] == ',' || is_space(value[pos]))) {
            pos++; /* skip separators before the candidate URL */
        }
        Py_ssize_t start = pos;
        while (pos < len && value[pos] != ',' && !is_space(value[pos])) {
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

/* Does `name` begin with any allowlisted attribute-name prefix (nh3's generic_attribute_prefixes, the `data-*` case)?
   The prefixes are validated as non-empty str at setup, so each check is a byte-prefix compare. Returns 1 match, 0
   none, -1 error. Only reached when the prefix set is non-empty, so a policy without prefixes never iterates. */
static int name_has_allowed_prefix(sanitizer *s, const char *name, Py_ssize_t len) {
    PyObject *iterator = PyObject_GetIter(s->attribute_prefixes);
    if (iterator == NULL) { /* GCOVR_EXCL_BR_LINE: getting an iterator over a set cannot fail */
        return -1;          /* GCOVR_EXCL_LINE: error path */
    }
    int matched = 0;
    PyObject *prefix;
    while (!matched && (prefix = PyIter_Next(iterator)) != NULL) {
        Py_ssize_t prefix_len = 0;
        const char *prefix_bytes = PyUnicode_AsUTF8AndSize(prefix, &prefix_len);
        if (prefix_bytes == NULL) { /* GCOVR_EXCL_BR_LINE: prefixes are validated as str at setup */
            Py_DECREF(prefix);      /* GCOVR_EXCL_LINE: error path */
            Py_DECREF(iterator);    /* GCOVR_EXCL_LINE */
            return -1;              /* GCOVR_EXCL_LINE */
        }
        matched = len >= prefix_len && memcmp(name, prefix_bytes, (size_t)prefix_len) == 0;
        Py_DECREF(prefix);
    }
    Py_DECREF(iterator);
    if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: set iteration raises no error of its own */
        return -1;          /* GCOVR_EXCL_LINE: error path */
    }
    return matched;
}

/* Is `name` allowed on element `tag` by the policy? A "*" inside an attribute set allows every attribute name, and an
   allowlisted name prefix allows a whole family. Returns 1 allow, 0 drop, -1 error. */
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
    if (!allowed && PySet_GET_SIZE(s->attribute_prefixes) > 0) {
        allowed = name_has_allowed_prefix(s, name, len);
    }
    return allowed;
}

/* Restrict a surviving (tag, attribute) to its literal value allowlist (nh3's tag_attribute_values), if the policy set
   one for it. An attribute with no per-(tag, attr) entry is unrestricted. Returns 1 keep, 0 drop, -1 error. Only
   reached when the value map is non-empty. */
static int value_allowed(sanitizer *s, PyObject *tag, const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                         Py_ssize_t value_len) {
    PyObject *per_tag = PyDict_GetItemWithError(s->attribute_values, tag);
    if (per_tag == NULL) {
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: PyDict_GetItemWithError only errors on a non-hashable key */
            return -1;          /* GCOVR_EXCL_LINE: error path */
        }
        return 1; /* no value allowlist for this tag */
    }
    PyObject *attr = PyUnicode_FromStringAndSize(name, name_len);
    if (attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *allowed_values = PyDict_GetItemWithError(per_tag, attr);
    Py_DECREF(attr);
    if (allowed_values == NULL) {
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the key is a freshly built str, always hashable */
            return -1;          /* GCOVR_EXCL_LINE: error path */
        }
        return 1; /* this attribute is unrestricted */
    }
    PyObject *text = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value, value_len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int keep = PySet_Contains(allowed_values, text);
    Py_DECREF(text);
    return keep;
}

/* The embedded-media elements whose `src` a host allowlist governs. iframe/embed/object are absent because the safety
   baseline escapes them outright (is_unsafe_tag), so they never reach a kept element's attributes; these media elements
   a policy can allowlist and keep. */
static int is_media_host_tag(uint16_t atom) {
    switch (atom) {
    case TH_TAG_AUDIO:
    case TH_TAG_VIDEO:
    case TH_TAG_SOURCE:
    case TH_TAG_TRACK:
        return 1;
    default:
        return 0;
    }
}

/* The authority marker bytes that end a URL host: a path, query, or fragment. */
static int ends_authority(Py_UCS4 c) {
    switch (c) {
    case '/':
    case '?':
    case '#':
        return 1;
    default:
        return 0;
    }
}

/* Locate the authority host of a URL value: the host after "scheme://" or a protocol-relative "//". The authority is
   bounded here without preprocessing the value (the WHATWG tab/newline stripping a browser applies is intentionally not
   done, so an obfuscated host never masquerades as an allowlisted one), then th_url_authority -- the same decomposition
   url_split runs -- splits off any "userinfo@" and ":port" and reports the host span and its literal kind. Sets
   *start,*end to the host span and *kind to the host literal, returns 0, or returns -1 when the URL carries no
   authority (a relative or opaque src, which has no host to match). */
static int url_host_span(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t *start, Py_ssize_t *end, int *kind) {
    Py_ssize_t authority = -1;
    if (len >= 2 && value[0] == '/' && value[1] == '/') {
        authority = 2; /* protocol-relative //host/path */
    }
    for (Py_ssize_t index = 0; authority < 0 && index + 2 < len; index++) {
        if (value[index] == ':' && value[index + 1] == '/' && value[index + 2] == '/') {
            authority = index + 3; /* scheme://host */
        }
    }
    if (authority < 0) {
        return -1;
    }
    Py_ssize_t authority_end = authority;
    while (authority_end < len && !ends_authority(value[authority_end])) {
        authority_end++; /* the authority runs to the path, query, or fragment */
    }
    th_authority parts;
    th_url_authority(value, authority, authority_end, &parts);
    *start = parts.host_start;
    *end = parts.host_end;
    *kind = parts.kind;
    return 0;
}

/* Keep an embedded-media `src` only when its URL host is on the allowlist, compared case-insensitively against the
   lowercase entries. Returns 1 allow, 0 drop, -1 error. */
static int host_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t host_start = 0;
    Py_ssize_t host_end = 0;
    int host_kind = TH_HOST_REGNAME;
    if (url_host_span(value, len, &host_start, &host_end, &host_kind) < 0) {
        return 0; /* a relative or opaque src has no host, so no allowlisted host can admit it */
    }
    if (host_kind == TH_HOST_IPV6) {
        return 0; /* an IPv6 literal is never a reg-name allowlist entry, so reject it rather than match its inner
                     address, keeping the pre-unification behavior that a bracketed host admits no media src */
    }
    Py_UCS4 host[256];
    Py_ssize_t host_len = host_end - host_start;
    if (host_len >= (Py_ssize_t)(sizeof(host) / sizeof(host[0]))) {
        return 0; /* longer than any real DNS host (max 253), so it cannot be on the allowlist */
    }
    for (Py_ssize_t index = 0; index < host_len; index++) {
        Py_UCS4 c = value[host_start + index];
        host[index] = lower_ascii(c);
    }
    PyObject *key = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, host, host_len);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int allowed = PySet_Contains(s->media_hosts, key);
    Py_DECREF(key);
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
        lowered[index] = (char)(lower_ascii(c));
    }
    PyObject *key = PyUnicode_FromStringAndSize(lowered, len);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int allowed = PySet_Contains(s->css_properties, key);
    Py_DECREF(key);
    return allowed;
}

/* Bytes that continue a CSS identifier, so `expression`/`url` is only recognized as a function name at a token boundary
   (`background:curl(x)` is not a `url()`). */
static int is_css_ident_char(Py_UCS4 c) {
    Py_UCS4 lower = c | 0x20;
    return (lower >= 'a' && lower <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_';
}

/* Does the lowercase ASCII `keyword` sit at value[pos:]? Returns the index just past it, or -1. */
static Py_ssize_t css_match_keyword(const Py_UCS4 *value, Py_ssize_t pos, Py_ssize_t end, const char *keyword) {
    Py_ssize_t index = 0;
    while (keyword[index] != '\0') {
        if (pos + index >= end || (value[pos + index] | 0x20) != (Py_UCS4)keyword[index]) {
            return -1;
        }
        index++;
    }
    return pos + index;
}

/* Skip the whitespace and CSS comments a browser ignores between a function name and its opening paren, so a comment
   spliced in as `url` then comment then `(...)` is not read as a bare identifier. */
static Py_ssize_t css_skip_ws_comments(const Py_UCS4 *value, Py_ssize_t pos, Py_ssize_t end) {
    while (pos < end) {
        if (is_space(value[pos])) {
            pos++;
        } else if (value[pos] == '/' && pos + 1 < end && value[pos + 1] == '*') {
            pos += 2;
            while (pos < end && !(value[pos] == '*' && pos + 1 < end && value[pos + 1] == '/')) {
                pos++;
            }
            pos += pos < end ? 2 : 0; /* step over the closing star-slash when the comment was terminated */
        } else {
            break;
        }
    }
    return pos;
}

/* Read the URL inside a `url(...)` starting at `pos` (just past the paren) and check its scheme against the allowlist,
   the same scan the URL-attribute path uses, after stripping an optional surrounding quote so `url("javascript:...")`
   cannot smuggle a scheme past the check. Returns 1 allow, 0 drop, -1 error. */
static int css_url_scheme_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t pos, Py_ssize_t end) {
    pos = css_skip_ws_comments(value, pos, end);
    Py_UCS4 quote = 0;
    if (pos < end && (value[pos] == '"' || value[pos] == '\'')) {
        quote = value[pos++];
    }
    Py_ssize_t url_start = pos;
    while (pos < end && value[pos] != ')' && (quote ? value[pos] != quote : !is_space(value[pos]))) {
        pos++;
    }
    return scheme_allowed(s, value + url_start, pos - url_start);
}

/* A declaration whose property name is allowlisted can still carry a dangerous value: IE's `expression(...)` runs
   script, and `url(javascript:...)` a disallowed scheme. Reject the whole declaration in either case. Returns 1 allow,
   0 drop, -1 error. */
static int css_value_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end) {
    for (Py_ssize_t pos = start; pos < end; pos++) {
        if (pos > start && is_css_ident_char(value[pos - 1])) {
            continue; /* mid-identifier: not a function-name boundary */
        }
        Py_ssize_t after_expr = css_match_keyword(value, pos, end, "expression");
        if (after_expr >= 0) {
            Py_ssize_t paren = css_skip_ws_comments(value, after_expr, end);
            if (paren < end && value[paren] == '(') {
                return 0;
            }
        }
        Py_ssize_t after_url = css_match_keyword(value, pos, end, "url");
        if (after_url >= 0) {
            Py_ssize_t paren = css_skip_ws_comments(value, after_url, end);
            if (paren < end && value[paren] == '(') {
                int ok = css_url_scheme_allowed(s, value, paren + 1, end);
                if (ok <= 0) {
                    return ok;
                }
            }
        }
    }
    return 1;
}

/* Decide whether the declaration `value[start:end)`, split at `colon` into property and value, survives the policy:
   its property name must be allowlisted and its value free of expression()/url(disallowed-scheme). On a keep, the
   trimmed declaration span (property start through the value's last non-whitespace byte) is returned in *name_start
   and *decl_end. Returns 1 keep, 0 drop, -1 error. Shared by the `style` attribute and `<style>` body scrubbers so the
   safety rules stay identical across both. */
static int css_declaration_kept(sanitizer *s, const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end, Py_ssize_t colon,
                                Py_ssize_t *name_start_out, Py_ssize_t *decl_end_out) {
    if (colon < start) {
        return 0; /* no property:value split in this declaration, so drop it */
    }
    Py_ssize_t name_start = start;
    Py_ssize_t name_end = colon;
    while (name_start < name_end && is_space(value[name_start])) {
        name_start++;
    }
    while (name_end > name_start && is_space(value[name_end - 1])) {
        name_end--;
    }
    int allowed = css_property_allowed(s, value + name_start, name_end - name_start);
    if (allowed < 0) { /* GCOVR_EXCL_BR_LINE: css_property_allowed only fails on allocation failure */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!allowed) {
        return 0;
    }
    int value_ok = css_value_allowed(s, value, colon + 1, end);
    if (value_ok < 0) { /* GCOVR_EXCL_BR_LINE: css_value_allowed only fails on allocation failure */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!value_ok) {
        return 0; /* an allowlisted property carrying expression()/url(disallowed-scheme) is still dropped */
    }
    Py_ssize_t decl_end = end;
    while (decl_end > colon + 1 && is_space(value[decl_end - 1])) {
        decl_end--;
    }
    *name_start_out = name_start;
    *decl_end_out = decl_end;
    return 1;
}

/* Append the declaration `value[start:end)` to `out` if it survives the policy, trimming surrounding whitespace and
   joining kept declarations with "; " (the `style` attribute's declaration-list form). Returns 0, or -1 on error. */
static int css_flush_declaration(sanitizer *s, const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end, Py_ssize_t colon,
                                 Py_UCS4 *out, Py_ssize_t *out_len) {
    Py_ssize_t name_start = 0;
    Py_ssize_t decl_end = 0;
    int kept = css_declaration_kept(s, value, start, end, colon, &name_start, &decl_end);
    if (kept <= 0) { /* GCOVR_EXCL_BR_LINE: the -1 half is css_declaration_kept's allocation-failure path */
        return kept;
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

/* Copy the rule prelude (a selector or at-rule head) `value[start:end)` to `out`, trimmed of surrounding whitespace.
   A prelude carries no declarations, so it is kept verbatim: CSS selectors cannot run script, and the value-level
   dangers (expression(), url(disallowed-scheme)) live in declaration values, which css_declaration_kept still vets. */
static void css_emit_prelude(const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end, Py_UCS4 *out,
                             Py_ssize_t *out_len) {
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    for (Py_ssize_t index = start; index < end; index++) {
        out[(*out_len)++] = value[index];
    }
}

/* Append the declaration `value[start:end)` to `out` if it survives the policy, terminated with ';' (the stylesheet's
   block form, so `p{color:red}` re-serializes to `p{color:red;}` and re-scrubbing is a fixpoint). Returns 0, or -1 on
   error. */
static int css_emit_block_declaration(sanitizer *s, const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end,
                                      Py_ssize_t colon, Py_UCS4 *out, Py_ssize_t *out_len) {
    Py_ssize_t name_start = 0;
    Py_ssize_t decl_end = 0;
    int kept = css_declaration_kept(s, value, start, end, colon, &name_start, &decl_end);
    if (kept <= 0) { /* GCOVR_EXCL_BR_LINE: the -1 half is css_declaration_kept's allocation-failure path */
        return kept;
    }
    for (Py_ssize_t index = name_start; index < decl_end; index++) {
        out[(*out_len)++] = value[index];
    }
    out[(*out_len)++] = ';';
    return 0;
}

/* Scrub a `<style>` body: a stylesheet is a sequence of rules, each a prelude (selector or at-rule head) and a `{...}`
   block. A block's declarations are vetted like a `style` attribute -- only allowlisted, expression()/url-safe
   declarations survive -- while preludes and block nesting are kept, so `p{color:red;position:fixed}` becomes
   `p{color:red;}`. Segmentation runs a single pass whose terminator classifies each run: a `{` makes the run a prelude
   (open a block), a `;`/`}` a declaration. An at-rule statement (`@import ...;`, `@charset ...`) has no property:value
   split, so css_declaration_kept drops it, and a `url()`/quoted/commented `;`, `:`, `{`, or `}` is skipped so it is
   never mistaken for a separator. brace_depth is an int counter, not recursion, so a pathologically nested body cannot
   overflow the C stack. Writes the scrubbed body length to *out_len_out. Returns 0, or -1 on error. */
static int scrub_stylesheet(sanitizer *s, const Py_UCS4 *value, Py_ssize_t len, Py_UCS4 *out, Py_ssize_t *out_len_out) {
    Py_ssize_t out_len = 0;
    Py_ssize_t seg_start = 0;
    Py_ssize_t colon = -1;
    int mode = 0; /* 0 normal, 1 string, 2 comment */
    Py_UCS4 quote = 0;
    int depth = 0; /* parenthesis nesting, so a separator inside url(...) is not a separator */
    int brace_depth = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 c = value[index];
        if (mode == 2) {
            if (c == '*' && index + 1 < len && value[index + 1] == '/') {
                mode = 0;
                index++;
            }
            continue;
        }
        if (mode == 1) {
            if (c == '\\') {
                index++; /* a backslash escapes the next byte, even a quote */
            } else if (c == quote) {
                mode = 0;
            }
            continue;
        }
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
        if (c == '{') {
            css_emit_prelude(value, seg_start, index, out, &out_len);
            out[out_len++] = '{';
            brace_depth++;
            seg_start = index + 1;
            colon = -1;
            continue;
        }
        if (c == '}') {
            if (brace_depth > 0) { /* a stray '}' outside any block is dropped, carrying no declaration to flush */
                int flushed = css_emit_block_declaration(s, value, seg_start, index, colon, out, &out_len);
                if (flushed < 0) { /* GCOVR_EXCL_BR_LINE: css_emit_block_declaration only fails on allocation failure */
                    return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                out[out_len++] = '}';
                brace_depth--;
            }
            seg_start = index + 1;
            colon = -1;
            continue;
        }
        if (c == ';') {
            if (brace_depth > 0) { /* a run ended by ';' outside a block is an at-statement, dropped with its body */
                int flushed = css_emit_block_declaration(s, value, seg_start, index, colon, out, &out_len);
                if (flushed < 0) { /* GCOVR_EXCL_BR_LINE: css_emit_block_declaration only fails on allocation failure */
                    return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
            seg_start = index + 1;
            colon = -1;
            continue;
        }
        if (c == ':' && colon < 0) {
            colon = index;
        }
    }
    if (brace_depth > 0) { /* an unclosed block: flush its trailing declaration and balance the missing braces */
        int flushed = css_emit_block_declaration(s, value, seg_start, len, colon, out, &out_len);
        if (flushed < 0) { /* GCOVR_EXCL_BR_LINE: css_emit_block_declaration only fails on allocation failure */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        while (brace_depth-- > 0) {
            out[out_len++] = '}';
        }
    }
    *out_len_out = out_len;
    return 0;
}

/* Scrub the CSS a kept `<style>` element holds: a raw-text element carries its stylesheet as one text child (or none
   when empty), so rewrite that child's data in place with the policy-safe subset. Returns 0, or -1 on error. */
static int sanitize_style_body(sanitizer *s, th_node *element) {
    th_node *text = element->first_child;
    if (text == NULL) {
        return 0; /* an empty <style></style> has no body to scrub */
    }
    Py_ssize_t len = 0;
    Py_UCS4 *body = th_node_data(s->tree, text, &len); /* a parsed text node is a source slice, so materialize it */
    if (body == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS4 *out = PyMem_Malloc((size_t)(2 * len + 16) * sizeof(Py_UCS4));
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(body); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t out_len = 0;
    int status = scrub_stylesheet(s, body, len, out, &out_len);
    if (status == 0) { /* GCOVR_EXCL_BR_LINE: scrub_stylesheet's non-zero return is its allocation-failure path */
        status = th_node_set_data(s->tree, text, out, out_len);
    }
    PyMem_Free(out);
    PyMem_Free(body);
    return status;
}

/* SAFE_FOR_TEMPLATES. A template engine (Angular, Vue, Mustache, EJS, ERB) evaluates {{ }}, ${ }, and <% %> in the
   strings it later renders, so a sanitized value that still carries one can re-inject once the output is fed through
   that engine. Copy `in` to `out` -- caller-sized to `len`, since a run only ever shrinks -- replacing every such run,
   its opening delimiter through the nearest matching close (or through the end when the run is left unclosed), with a
   single space, matching DOMPurify's SAFE_FOR_TEMPLATES. Returns the written length and sets *changed when a run was
   collapsed, so a caller rewrites the node only when the value held a marker. */
static Py_ssize_t strip_template_markers(const Py_UCS4 *in, Py_ssize_t len, Py_UCS4 *out, int *changed) {
    Py_ssize_t write = 0;
    *changed = 0;
    Py_ssize_t read = 0;
    while (read < len) {
        Py_UCS4 opener = in[read];
        Py_UCS4 next = read + 1 < len ? in[read + 1] : 0;
        Py_UCS4 close_lead = 0;
        Py_UCS4 close_tail = 0;
        if (opener == '{' && next == '{') {
            close_lead = '}';
            close_tail = '}';
        } else if (opener == '$' && next == '{') {
            close_lead = '}';
            close_tail = 0;
        } else if (opener == '<' && next == '%') {
            close_lead = '%';
            close_tail = '>';
        } else {
            out[write++] = opener;
            read++;
            continue;
        }
        Py_ssize_t scan = read + 2;
        int closed = 0;
        while (scan < len && !closed) {
            if (in[scan] == close_lead && close_tail == 0) {
                scan++; /* ${ ... } closes on the single } */
                closed = 1;
            } else if (in[scan] == close_lead && scan + 1 < len && in[scan + 1] == close_tail) {
                scan += 2; /* {{ ... }} and <% ... %> close on the two-char delimiter */
                closed = 1;
            } else {
                scan++; /* an ordinary character, or a lead byte that is not the full close */
            }
        }
        out[write++] = ' ';
        *changed = 1;
        read = scan;
    }
    return write;
}

/* Collapse the template markers in one kept attribute's value, rewriting it in place when SAFE_FOR_TEMPLATES is on and
   the value held a marker. Returns 0, or -1 on allocation failure. */
static int strip_attr_templates(sanitizer *s, th_node *element, th_node_attr *attr) {
    Py_UCS4 *out = PyMem_Malloc((size_t)(attr->value_len > 0 ? attr->value_len : 1) * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int changed = 0;
    Py_ssize_t out_len = strip_template_markers(attr->value, attr->value_len, out, &changed);
    int status = 0;
    if (changed) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
        status = th_node_attr_set(s->tree, element, name, name_len, out, out_len, 1);
    }
    PyMem_Free(out);
    return status;
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
        if (!drop && PyDict_GET_SIZE(s->attribute_values) > 0) {
            int keep = value_allowed(s, tag, name, name_len, attr->value, attr->value_len);
            if (keep < 0) { /* GCOVR_EXCL_BR_LINE: value_allowed only fails on allocation failure */
                return -1;  /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            drop = !keep;
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
        if (!drop && !url_disallowed && PySet_GET_SIZE(s->media_hosts) > 0 && is_media_host_tag(element->atom) &&
            name_len == 3 && memcmp(name, "src", 3) == 0) {
            int ok = host_allowed(s, attr->value, attr->value_len);
            if (ok < 0) {  /* GCOVR_EXCL_BR_LINE: host_allowed only fails on allocation failure */
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            url_disallowed = !ok; /* a disallowed host drops every src occurrence, like a disallowed scheme */
        }
        if (url_disallowed) {
            if (record_removed(s, tag, name, name_len) < 0) { /* GCOVR_EXCL_BR_LINE: record only fails on alloc */
                return -1;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
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
            if (record_removed(s, tag, name, name_len) < 0) { /* GCOVR_EXCL_BR_LINE: record only fails on alloc */
                return -1;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            th_node_attr_del(s->tree, element, name, name_len);
            continue; /* the next attribute shifted into this slot */
        }
        /* strip_attr_templates only fails on allocation, which no test can force */
        if (s->strip_templates && strip_attr_templates(s, element, attr) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                          /* GCOVR_EXCL_LINE */
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
    /* Only reproduce an end tag the source actually wrote: a void element or an
       unclosed one (`<name of movie>`, `I love <sarcasm> this`) carries no close
       tag, so escaping must not fabricate one. */
    if (element->tag_flags & TH_ELEM_CLOSED_BY_END_TAG) {
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

/* A MathML text integration point (mi/mo/mn/ms/mtext), where the parser resumes HTML parsing so an HTML child there is
   no confusion. The caller has already established the node is in the MathML namespace. */
static int is_mathml_text_point(const th_node *node) {
    static const uint16_t atoms[] = {TH_TAG_MI, TH_TAG_MO, TH_TAG_MN, TH_TAG_MS, TH_TAG_MTEXT};
    for (size_t index = 0; index < sizeof(atoms) / sizeof(atoms[0]); index++) {
        if (node->atom == atoms[index]) {
            return 1;
        }
    }
    return 0;
}

/* An HTML integration point: an SVG foreignObject/desc/title, or a MathML annotation-xml. annotation-xml counts by
   name, the way DOMPurify does, not by its encoding attribute: sanitize_attributes runs on a parent before its
   children are walked, so an encoding-based test would drop a legitimately parsed HTML child the moment a policy
   strips the parent's encoding. The caller has already established the node is foreign, so a non-SVG parent is MathML.
 */
static int is_html_integration_point(const th_node *node) {
    static const uint16_t svg_atoms[] = {TH_TAG_FOREIGNOBJECT, TH_TAG_DESC, TH_TAG_TITLE};
    if (node->ns == TH_NS_SVG) {
        for (size_t index = 0; index < sizeof(svg_atoms) / sizeof(svg_atoms[0]); index++) {
            if (node->atom == svg_atoms[index]) {
                return 1;
            }
        }
        return 0;
    }
    return node->atom == TH_TAG_ANNOTATION_XML;
}

/* Is the element's namespace reachable from its parent's? The (element, namespace, parent) triples the HTML parser can
   legitimately produce, per the WHATWG foreign-content and integration-point rules (DOMPurify's _checkValidNamespace
   equivalent): enter SVG only through <svg>, MathML only through <math>, and return to HTML only through an integration
   point. A tree the parser built always passes; only a namespace-confused node a later mutation could splice in -- an
   HTML element reparented under SVG/MathML, or a foreign element escaped into HTML content where its children would
   reparse as HTML -- fails, and is then dropped like any disallowed node (mXSS defense-in-depth). Returns 1 reachable,
   0 confused. */
static int namespace_reachable(const th_node *element) {
    /* the walk only reaches an element through its parent, so element->parent is never NULL here; a fragment or
       document root carries the HTML namespace, so reading parent->ns needs no element-type guard */
    const th_node *parent = element->parent;
    uint8_t parent_ns = parent->ns;
    uint8_t ns = element->ns;
    if (ns == parent_ns) {
        return 1; /* within one namespace every element stays reachable (HTML<-HTML, SVG<-SVG, MathML<-MathML) */
    }
    if (ns == TH_NS_SVG) {
        /* from HTML only <svg> enters SVG; from MathML only an <svg> under annotation-xml or a text point */
        return element->atom == TH_TAG_SVG &&
               (parent_ns == TH_NS_HTML || parent->atom == TH_TAG_ANNOTATION_XML || is_mathml_text_point(parent));
    }
    if (ns == TH_NS_MATHML) {
        /* from HTML only <math> enters MathML; from SVG only a <math> under an HTML integration point */
        return element->atom == TH_TAG_MATH && (parent_ns == TH_NS_HTML || is_html_integration_point(parent));
    }
    /* an HTML element under a foreign parent is reachable only at an integration point */
    if (parent_ns == TH_NS_SVG) {
        return is_html_integration_point(parent);
    }
    return is_mathml_text_point(parent) || is_html_integration_point(parent);
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
       context so it stays inside real foreign markup. An HTML <style> is exempt from
       the unsafe-tag block: a policy that allowlists it keeps it with its body scrubbed
       against css_properties (like a `style` attribute), rather than dropping its CSS. */
    int style_element = element->atom == TH_TAG_STYLE && is_html;
    int allowed = (is_unsafe_tag(element->atom) && !style_element) ? 0 : PySet_Contains(s->tags, tag);
    if (allowed > 0 && !is_html && !parent_kept) {
        allowed = 0;
    }
    /* defense in depth: a node whose namespace is unreachable from its parent's is a namespace-confusion mutation, not
       anything the parser produced, so drop it like any disallowed node even when the allowlist would admit its name */
    if (allowed > 0 && !namespace_reachable(element)) {
        allowed = 0;
    }
    /* only disallowed elements consult remove_with_content, so a kept element never pays the set lookup */
    int remove_content = allowed > 0 ? 0 : PySet_Contains(s->remove_with_content, tag);
    /* a disallowed element is about to be removed, stripped, or escaped; note it before the disposition splits */
    if (allowed == 0 && record_removed(s, tag, NULL, 0) < 0) { /* GCOVR_EXCL_BR_LINE: record only fails on alloc */
        Py_DECREF(tag);                                        /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int status = 0;
    if (allowed < 0 || remove_content < 0) { /* GCOVR_EXCL_BR_LINE: PySet_Contains never fails on a str key */
        status = -1;                         /* GCOVR_EXCL_LINE */
    } else if (allowed && style_element) {
        /* a kept <style> holds raw CSS, not child elements, so scrub its stylesheet body instead of walking children */
        status = sanitize_attributes(s, element, tag) < 0 ? -1 : sanitize_style_body(s, element);
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

/* Rewrite one text node in place with its template markers collapsed, when SAFE_FOR_TEMPLATES is on and the node held a
   marker. Returns 0, or -1 on allocation failure. */
static int strip_text_templates(sanitizer *s, th_node *node) {
    Py_ssize_t len = 0;
    Py_UCS4 *data = th_node_data(s->tree, node, &len);
    if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS4 *out = PyMem_Malloc((size_t)len * sizeof(Py_UCS4)); /* a text node carries >= 1 point, so len >= 1 */
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    int changed = 0;
    Py_ssize_t out_len = strip_template_markers(data, len, out, &changed);
    int status = 0;
    if (changed) {
        status = th_node_set_data(s->tree, node, out, out_len);
    }
    PyMem_Free(out);
    PyMem_Free(data);
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
        } else if (child->type == TH_NODE_TEXT) {
            /* strip_text_templates only fails on allocation, which no test can force */
            if (s->strip_templates && strip_text_templates(s, child) < 0) { /* GCOVR_EXCL_BR_LINE */
                return -1;                                                  /* GCOVR_EXCL_LINE */
            }
        } else {
            th_node_remove(child); /* doctype, processing instruction, CDATA: never valid in a sanitized fragment */
        }
        child = next;
    }
    return 0;
}

/* The set-typed policy fields reach a PySet_Contains in the walk, which raises a bare SystemError on a non-set. Reject
   a wrong type up front with a message that names the offending Policy field, so a caller gets a clear TypeError. */
static int require_anyset(PyObject *value, const char *field) {
    if (!PyAnySet_Check(value)) {
        PyErr_Format(PyExc_TypeError, "Policy.%s must be a set or frozenset, got %.100s", field,
                     Py_TYPE(value)->tp_name);
        return -1;
    }
    return 0;
}

/* Every attribute-name prefix must be a non-empty str: a non-string cannot be a name prefix, and an empty prefix would
   match every name and quietly defeat the allowlist. Checked once at setup so the per-attribute test stays a compare.
 */
static int require_prefixes(PyObject *prefixes) {
    PyObject *iterator = PyObject_GetIter(prefixes);
    if (iterator == NULL) { /* GCOVR_EXCL_BR_LINE: getting an iterator over a set cannot fail */
        return -1;          /* GCOVR_EXCL_LINE: error path */
    }
    int status = 0;
    PyObject *prefix;
    while (status == 0 && (prefix = PyIter_Next(iterator)) != NULL) {
        if (!PyUnicode_Check(prefix)) {
            PyErr_Format(PyExc_TypeError, "Policy.attribute_prefixes must contain only str, got %.100s",
                         Py_TYPE(prefix)->tp_name);
            status = -1;
        } else if (PyUnicode_GET_LENGTH(prefix) == 0) {
            PyErr_SetString(PyExc_ValueError, "Policy.attribute_prefixes must not contain an empty prefix");
            status = -1;
        }
        Py_DECREF(prefix);
    }
    Py_DECREF(iterator);
    if (status == 0 && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: set iteration raises no error of its own */
        return -1;                         /* GCOVR_EXCL_LINE: error path */
    }
    return status;
}

/* _sanitize(element, tags, attributes, url_schemes, allow_relative, on_disallowed, strip_comments, add_link_rel,
   attribute_filter, set_attributes, remove_with_content, css_properties, attribute_prefixes, attribute_values,
   media_hosts, strip_templates, removed) -> None. Filters the fragment in place; sanitizer.py serializes it.
   `removed` is a list the walk appends (tag, attr_or_None) records to, or None to skip the audit. */
PyObject *turbohtml_sanitize(PyObject *module, PyObject *args) {
    PyObject *element;
    PyObject *removed = NULL;
    sanitizer s = {0};
    if (!PyArg_ParseTuple(args, "OOOOpipOOOOOOOOpO:_sanitize", &element, &s.tags, &s.attributes, &s.url_schemes,
                          &s.allow_relative, &s.on_disallowed, &s.strip_comments, &s.add_link_rel, &s.attribute_filter,
                          &s.set_attributes, &s.remove_with_content, &s.css_properties, &s.attribute_prefixes,
                          &s.attribute_values, &s.media_hosts, &s.strip_templates, &removed)) {
        return NULL;
    }
    s.removed = removed == Py_None ? NULL : removed;
    if (require_anyset(s.tags, "tags") < 0 || require_anyset(s.url_schemes, "url_schemes") < 0 ||
        require_anyset(s.remove_with_content, "remove_with_content") < 0 ||
        require_anyset(s.css_properties, "css_properties") < 0 ||
        require_anyset(s.attribute_prefixes, "attribute_prefixes") < 0 ||
        require_anyset(s.media_hosts, "media_hosts") < 0 || require_prefixes(s.attribute_prefixes) < 0) {
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
