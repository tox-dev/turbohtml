/* Native HTML sanitizer allowlist walk.

   The Python facade compiles policy and serializes the result. This entrypoint parses text input or snapshots an
   Element under its tree lock, releases the shared tree, then mutates the private tree. The mandatory safety rules run
   here so policy callbacks cannot bypass them. */

#include "core/ascii.h"
#include "core/common.h"
#include "core/vec.h"
#include "url/url.h"

#include "dom/nodes.h"
#include "tokenizer/binding.h"
#include "tokenizer/charref.h"

#include <string.h>
#include <stdlib.h>

enum on_disallowed { ON_ESCAPE = 0, ON_STRIP = 1, ON_REMOVE = 2 };

typedef struct {
    const Py_UCS4 *value;
    uint32_t name_atom;
    th_src_span span;
} bleach_origin;

typedef struct {
    th_tree *tree;
    PyObject *tags;           /* frozenset[str]: allowed element names */
    PyObject *attributes;     /* Mapping[str, frozenset[str]]: per-tag allowed attribute names, "*" wildcards */
    PyObject *wildcard_attrs; /* attributes.get("*"), borrowed, or NULL */
    PyObject *url_schemes;    /* frozenset[str]: allowed URL schemes, lowercase */
    PyObject *star;           /* the interned "*" string, for the any-name wildcard */
    PyObject *add_link_rel;   /* str to set as an <a> rel, or None */
    PyObject *attribute_predicate;
    PyObject *attribute_filter; /* callable (tag, name, value) -> str | None, or None */
    PyObject *set_attributes;   /* dict[str, dict[str, str]]: per-tag attribute values to force-set on kept elements */
    PyObject *remove_with_content; /* frozenset[str]: disallowed tags whose whole subtree is dropped, not escaped */
    PyObject *css_properties;      /* frozenset[str]: CSS property names kept when scrubbing a `style` attribute */
    PyObject *attribute_prefixes;  /* frozenset[str]: allow any attribute whose name starts with one of these */
    PyObject *prefix_tuple;
    PyObject *attribute_values; /* dict[str, dict[str, frozenset[str]]]: per (tag, attr) literal value allowlist */
    PyObject *allowed_styles;   /* dict[str, dict[str, tuple[re.Pattern, ...]]]: per (tag or "*") allowed style
                                   properties, each mapped to the compiled patterns its value must match, or an empty
                                   dict when no per-property value allowlist is in force */
    PyObject *re_search; /* the interned "search" method name, for calling re.Pattern.search from the style scrubber */
    PyObject *media_hosts;    /* frozenset[str]: allowed hosts for an embedded-media (audio/video/source/track) src */
    PyObject *transform_tags; /* dict[str, tuple[str, dict[str, str]]]: source tag -> (target tag, added attributes),
                                 applied before the allowlist so the renamed element is re-checked, or an empty dict */
    PyObject
        *removed; /* list to append (tag, attr_or_None) records to as the walk drops things, or NULL to not report */
    int allow_relative;
    int allow_fragments;
    int on_disallowed;
    int strip_comments;
    int strip_templates;     /* SAFE_FOR_TEMPLATES: collapse {{ }}, ${ }, <% %> runs so kept text/attrs stay
                                template-safe */
    int isolate_named_props; /* SANITIZE_NAMED_PROPS: prefix kept id/name values with "user-content-" so they cannot
                                shadow a document/form named property (DOM clobbering) */
    PyObject *custom_element_check;   /* callable(tag) -> bool: keep an unlisted HTML custom element, or None (off) */
    PyObject *custom_attribute_check; /* callable(tag, name) -> bool: keep an unlisted attr on a kept custom element,
                                         or None (only allowlisted attrs survive) */
    int allow_customized_builtins;    /* allowCustomizedBuiltInElements: keep an `is` attribute whose value passes
                                         custom_element_check, so a customized built-in survives */
    int allow_html;   /* USE_PROFILES.html: keep HTML-namespace elements (off drops the whole HTML namespace) */
    int allow_svg;    /* USE_PROFILES.svg: keep SVG-namespace elements */
    int allow_mathml; /* USE_PROFILES.mathMl: keep MathML-namespace elements */
    int bleach_raw_values;
    int bleach_url_policy;
    int bleach_raw_urls;
    bleach_origin *bleach_origins;
    Py_ssize_t bleach_origin_count;
} sanitizer;

static PyObject *bleach_predicate(PyObject *bound, PyObject *args);
static int bleach_url_allowed(sanitizer *s, th_node *element, th_node_attr *attr, const char *name,
                              Py_ssize_t name_len);

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

static int is_unsafe_svg_animation(const th_node *element) {
    if (element->ns != TH_NS_SVG) {
        return 0;
    }
    static const char *const names[] = {"animate", "animateColor", "animateMotion", "animateTransform", "set"};
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        size_t len = strlen(names[index]);
        if (element->text_len != (Py_ssize_t)len) {
            continue;
        }
        size_t position = 0;
        while (position < len && element->text[position] == (Py_UCS4)names[index][position]) {
            position++;
        }
        if (position == len) {
            return 1;
        }
    }
    return 0;
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

/* javascript: runs script in the page, so the safety baseline refuses it whatever the policy's url_schemes lists. */
static int is_script_scheme(const char *scheme, size_t len) {
    return len == 10 && memcmp(scheme, "javascript", 10) == 0;
}

static int authority_allowed(const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t len) {
    for (int slash = 0; slash < 2; slash++) {
        while (start < len && is_url_ignorable(value[start])) {
            start++;
        }
        if (start == len || value[start] != '/') {
            return 1;
        }
        start++;
    }
    return th_url_authority_end(value, start, len) >= 0;
}

/* Unicode and control characters can conceal a disallowed scheme. */
static int scheme_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t len) {
    char scheme[40];
    Py_ssize_t length = 0;
    int started = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 c = value[index];
        if (is_url_ignorable(c) || c >= 0x80) {
            continue;
        }
        if (c == ':' && started) {
            if (is_script_scheme(scheme, (size_t)length)) {
                return 0;
            }
            PyObject *name = PyUnicode_FromStringAndSize(scheme, length);
            if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int allowed = PySet_Contains(s->url_schemes, name);
            Py_DECREF(name);
            return allowed > 0 ? authority_allowed(value, index + 1, len) : allowed;
        }
        int letter = th_scheme_start(c);
        /* before the colon, every byte must be a scheme byte and the first must be a letter (so 1http:// is relative)
         */
        if (started ? !th_scheme_char(c) : !letter) {
            if (!started && c == '#' && s->allow_fragments) {
                return 1;
            }
            return s->allow_relative && (started || authority_allowed(value, index, len));
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

static Py_ssize_t skip_space(const Py_UCS4 *value, Py_ssize_t pos, Py_ssize_t len) {
    while (pos < len && is_space(value[pos])) {
        pos++;
    }
    return pos;
}

/* A <meta http-equiv=refresh> navigates to the URL inside its content attribute, so that URL gets the same scheme
   check as an href. The span follows the HTML "shared declarative refresh steps"; returns 0 when content names no URL,
   which refreshes the current document. */
static int refresh_url(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t *start, Py_ssize_t *end) {
    Py_ssize_t pos = skip_space(value, 0, len);
    Py_ssize_t digits = pos;
    while (pos < len && is_ascii_digit(value[pos])) {
        pos++;
    }
    if (pos == digits && (pos == len || value[pos] != '.')) {
        return 0;
    }
    while (pos < len && (is_ascii_digit(value[pos]) || value[pos] == '.')) {
        pos++;
    }
    if (pos < len) {
        if (value[pos] != ';' && value[pos] != ',' && !is_space(value[pos])) {
            return 0;
        }
        pos = skip_space(value, pos, len);
        if (pos < len && (value[pos] == ';' || value[pos] == ',')) {
            pos++;
        }
        pos = skip_space(value, pos, len);
    }
    *start = pos;
    *end = len;
    if (pos < len && lower_ascii(value[pos]) == 'u') {
        /* anything short of "url" then "=" is itself the URL, read from the "u" */
        if (pos + 2 >= len || lower_ascii(value[pos + 1]) != 'r' || lower_ascii(value[pos + 2]) != 'l') {
            return 1;
        }
        pos = skip_space(value, pos + 3, len);
        if (pos == len || value[pos] != '=') {
            return 1;
        }
        pos = skip_space(value, pos + 1, len);
        *start = pos;
    }
    if (pos < len && (value[pos] == '"' || value[pos] == '\'')) {
        Py_UCS4 quote = value[pos];
        *start = ++pos;
        while (pos < len && value[pos] != quote) {
            pos++;
        }
        *end = pos;
    }
    return *start < *end;
}

static int is_refresh_meta(sanitizer *s, th_node *element) {
    if (element->atom != TH_TAG_META) {
        return 0;
    }
    Py_ssize_t index = th_node_attr_find(s->tree, element, "http-equiv", 10);
    if (index < 0 || element->attrs[index].value_len != 7) {
        return 0;
    }
    for (Py_ssize_t pos = 0; pos < 7; pos++) {
        if (lower_ascii(element->attrs[index].value[pos]) != (Py_UCS4) "refresh"[pos]) {
            return 0;
        }
    }
    return 1;
}

static int attribute_prefix_matches(PyObject *prefix, const char *name, Py_ssize_t len) {
    Py_ssize_t prefix_len = 0;
    const char *prefix_bytes = PyUnicode_AsUTF8AndSize(prefix, &prefix_len);
    if (prefix_bytes == NULL) {
        return -1;
    }
    return len >= prefix_len && memcmp(name, prefix_bytes, (size_t)prefix_len) == 0;
}

static int name_has_allowed_prefix(sanitizer *s, const char *name, Py_ssize_t len) {
    if (PyFrozenSet_CheckExact(s->attribute_prefixes)) {
        if (s->prefix_tuple == NULL) {
            s->prefix_tuple = PySequence_Tuple(s->attribute_prefixes);
            if (s->prefix_tuple == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(s->prefix_tuple); index++) {
            int matched = attribute_prefix_matches(PyTuple_GET_ITEM(s->prefix_tuple, index), name, len);
            if (matched != 0) {
                return matched;
            }
        }
        return 0;
    }
    /* Callbacks can mutate sets; subclasses can override iteration. */
    PyObject *iterator = PyObject_GetIter(s->attribute_prefixes);
    if (iterator == NULL) { /* GCOVR_EXCL_BR_LINE: getting an iterator over a set cannot fail */
        return -1;          /* GCOVR_EXCL_LINE: error path */
    }
    int matched = 0;
    PyObject *prefix;
    while (!matched && (prefix = PyIter_Next(iterator)) != NULL) {
        matched = attribute_prefix_matches(prefix, name, len);
        Py_DECREF(prefix);
    }
    Py_DECREF(iterator);
    if (PyErr_Occurred()) {
        return -1;
    }
    return matched;
}

/* Is `name` allowed on element `tag` by the policy? A "*" inside an attribute set allows every attribute name, and an
   allowlisted name prefix allows a whole family. Returns 1 allow, 0 drop, -1 error. */
static int attr_allowed(sanitizer *s, PyObject *tag, uint32_t atom, const char *name, Py_ssize_t len) {
    PyObject *sets[2] = {PyDict_GetItem(s->attributes, tag), s->wildcard_attrs};
    if (sets[0] == NULL && sets[1] == NULL) {
        int ascii = 1;
        if (atom >= TH_ATTR__DYNAMIC_BASE) {
            for (Py_ssize_t index = 0; ascii && index < len; index++) {
                ascii = (unsigned char)name[index] < 0x80;
            }
        }
        if (ascii) {
            return PySet_GET_SIZE(s->attribute_prefixes) > 0 ? name_has_allowed_prefix(s, name, len) : 0;
        }
    }
    /* Non-ASCII names still need UTF-8 validation even without an exact-name rule. */
    PyObject *attr = PyUnicode_FromStringAndSize(name, len);
    if (attr == NULL) {
        return -1;
    }
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

/* Return an ASCII-lowercased copy of an HTML tag or attribute name. Non-ASCII UTF-8 bytes are left intact. */
static char *html_name_lower(const char *name, Py_ssize_t len) {
    char *lowered = PyMem_Malloc((size_t)len + 1);
    if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        unsigned char byte = (unsigned char)name[index];
        lowered[index] = (char)(byte >= 'A' && byte <= 'Z' ? byte | 0x20 : byte);
    }
    lowered[len] = '\0';
    return lowered;
}

/* Apply the optional attribute filter, replacing the attribute's value or deleting it. Returns 1 when it wrote a new
   value, 0 when it left no late-written value, or -1 on error. */
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
        /* GCOVR_EXCL_BR_START: attribute storage allocation failure cannot be forced */
        status =
            th_node_attr_set(s->tree, element, name, name_len, points, PyUnicode_GET_LENGTH(result), 1) < 0 ? -1 : 1;
        /* GCOVR_EXCL_BR_STOP */
        PyMem_Free(points);
    }
    Py_XDECREF(result);
    Py_DECREF(attr);
    Py_DECREF(text);
    return status;
}

/* Force-set the policy's per-tag attribute values on a kept element, adding the attribute if absent and overwriting it
   if present (what attribute_filter cannot do, since it only sees attributes already there). HTML attribute names are
   ASCII case-insensitive, so policy-created names are canonicalized before interning. Returns 1 when any value was
   written, 0 when this tag has no values to set, or -1 on error. */
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
    int mutated = 0;
    while (PyDict_Next(per_tag, &pos, &name, &value)) {
        Py_ssize_t name_len = 0;
        const char *name_bytes = PyUnicode_AsUTF8AndSize(name, &name_len);
        if (name_bytes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        char *canonical_name = NULL;
        const char *write_name = name_bytes;
        if (element->ns == TH_NS_HTML) {
            canonical_name = html_name_lower(name_bytes, name_len);
            if (canonical_name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            write_name = canonical_name;
        }
        Py_UCS4 *points = PyUnicode_AsUCS4Copy(value);
        if (points == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(canonical_name); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                  /* GCOVR_EXCL_LINE */
        }
        int status = th_node_attr_set(s->tree, element, write_name, name_len, points, PyUnicode_GET_LENGTH(value), 1);
        PyMem_Free(canonical_name);
        PyMem_Free(points);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        mutated = 1;
    }
    return mutated;
}

static int css_decode_escape(const Py_UCS4 *value, Py_ssize_t *pos, Py_ssize_t end, Py_UCS4 *decoded);

/* A CSS property name is ASCII letters, digits, and '-', so an ASCII lowercase key is enough to look it up. Callers
   guarantee 0 < len < 64 (a real property name), so the fixed buffer never overflows. Returns a new str, or NULL on
   allocation failure. */
static PyObject *css_property_key(const Py_UCS4 *name, Py_ssize_t len) {
    char lowered[64];
    for (Py_ssize_t index = 0; index < len; index++) {
        lowered[index] = (char)(lower_ascii(name[index]));
    }
    return PyUnicode_FromStringAndSize(lowered, len);
}

/* Legacy engines execute these properties independently of their values. Decode escapes before comparing so an
   allowlist cannot admit an obfuscated spelling. */
static int css_property_blocked(const Py_UCS4 *name, Py_ssize_t len) {
    char decoded[64];
    Py_ssize_t decoded_len = 0;
    for (Py_ssize_t pos = 0; pos < len;) {
        Py_UCS4 codepoint = name[pos];
        if (codepoint == '\\') {
            if (!css_decode_escape(name, &pos, len, &codepoint)) {
                return 1;
            }
        } else {
            pos++;
        }
        if (codepoint > 0x7F) {
            return 1;
        }
        decoded[decoded_len++] = (char)lower_ascii(codepoint);
    }
    return (decoded_len == 8 && memcmp(decoded, "behavior", 8) == 0) ||
           (decoded_len == 12 && memcmp(decoded, "-moz-binding", 12) == 0);
}

/* Is the CSS property `name[0:len]` in the name allowlist? Returns 1 allow, 0 drop, -1 error. */
static int css_property_allowed(sanitizer *s, const Py_UCS4 *name, Py_ssize_t len) {
    if (len == 0 || len >= 64 || css_property_blocked(name, len)) {
        return 0; /* empty, or longer than any real property name */
    }
    PyObject *key = css_property_key(name, len);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int allowed = PySet_Contains(s->css_properties, key);
    Py_DECREF(key);
    return allowed;
}

/* The per-element resolution of Policy.allowed_styles: the allowlist keyed by the element's own tag and the one keyed
   by the "*" wildcard, either borrowed or NULL when absent. A NULL styles pointer means allowed_styles imposes nothing
   on this declaration list (the `<style>` body, and any element no rule applies to), keeping that path branch-free. */
typedef struct {
    PyObject *tag_rule;  /* allowed_styles[tag], borrowed, or NULL */
    PyObject *star_rule; /* allowed_styles["*"], borrowed, or NULL */
} style_allowlist;

/* Does `value_obj` match one of the patterns `rule` lists for `prop_key`? Following sanitize-html's allowedStyles, a
   declaration survives only when its property is listed and its value matches a pattern (an unanchored `re.search`,
   like JavaScript's `RegExp.test`). Returns 1 a pattern matched, 0 the property is absent or no pattern matched, -1
   error. A NULL rule (the wildcard or tag entry not present) contributes no match. */
static int css_style_patterns_match(sanitizer *s, PyObject *rule, PyObject *prop_key, PyObject *value_obj) {
    if (rule == NULL) {
        return 0;
    }
    PyObject *patterns = PyDict_GetItemWithError(rule, prop_key);
    if (patterns == NULL) {
        return PyErr_Occurred() ? -1 : 0; /* GCOVR_EXCL_BR_LINE: the str-key lookup cannot itself error */
    }
    Py_ssize_t count = PyTuple_GET_SIZE(patterns);
    for (Py_ssize_t index = 0; index < count; index++) {
        /* gcc pins the search call's untaken exception edge to this loop-body opening; a compiled re.Pattern.search
           over a str cannot raise, so that edge is uncoverable */
        PyObject *pattern = PyTuple_GET_ITEM(patterns, index); /* GCOVR_EXCL_BR_LINE */
        PyObject *result = PyObject_CallMethodOneArg(pattern, s->re_search, value_obj);
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: only a caller-supplied broken pattern could reach here */
            return -1;        /* GCOVR_EXCL_LINE: allocation/exception-failure path */
        }
        int matched = result != Py_None;
        Py_DECREF(result);
        if (matched) {
            return 1;
        }
    }
    return 0;
}

/* Narrow a name-allowlisted declaration through Policy.allowed_styles: the property `name[start:end]` must be listed
   for the element's own tag or the "*" wildcard (their pattern lists union, so either can admit it) and its value
   `value_obj` must match one of that property's patterns. Returns 1 keep, 0 drop, -1 error. */
static int css_style_declaration_allowed(sanitizer *s, const style_allowlist *styles, const Py_UCS4 *name,
                                         Py_ssize_t name_len, PyObject *value_obj) {
    PyObject *prop_key = css_property_key(name, name_len);
    if (prop_key == NULL) { /* GCOVR_EXCL_BR_LINE: the property already passed the name allowlist, so len < 64 */
        return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int matched = css_style_patterns_match(s, styles->tag_rule, prop_key, value_obj);
    if (matched == 0) {
        matched = css_style_patterns_match(s, styles->star_rule, prop_key, value_obj);
    }
    Py_DECREF(prop_key);
    return matched;
}

/* Bytes that continue a CSS identifier, so `expression`/`url` is only recognized as a function name at a token boundary
   (`background:curl(x)` is not a `url()`). */
static int is_css_ident_char(Py_UCS4 c) {
    Py_UCS4 lower = c | 0x20;
    return (lower >= 'a' && lower <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c >= 0x80;
}

static int is_css_hex(Py_UCS4 codepoint) {
    Py_UCS4 lower = codepoint | 0x20;
    return (codepoint >= '0' && codepoint <= '9') || (lower >= 'a' && lower <= 'f');
}

static int is_css_newline(Py_UCS4 codepoint) {
    switch (codepoint) {
    case '\n':
    case '\r':
    case '\f':
        return 1;
    default:
        return 0;
    }
}

/* Decode one CSS escape at `*pos`, which points at its backslash. CSS consumes one to six hexadecimal digits and one
   optional whitespace terminator, or one escaped code point. Returns 1 and advances `*pos`, or 0 for a trailing
   backslash or escaped newline. */
static int css_decode_escape(const Py_UCS4 *value, Py_ssize_t *pos, Py_ssize_t end, Py_UCS4 *decoded) {
    Py_ssize_t index = *pos + 1;
    if (index >= end || is_css_newline(value[index])) {
        return 0;
    }
    if (!is_css_hex(value[index])) {
        *decoded = value[index];
        *pos = index + 1;
        return 1;
    }
    Py_UCS4 point = 0;
    int digits = 0;
    while (index < end && digits < 6 && is_css_hex(value[index])) {
        Py_UCS4 digit = value[index++] | 0x20;
        point = (point << 4) + (digit <= '9' ? digit - '0' : digit - 'a' + 10);
        digits++;
    }
    if (index < end && is_space(value[index])) {
        if (value[index] == '\r') {
            index++;
            if (index < end && value[index] == '\n') {
                index++;
            }
        } else {
            index++;
        }
    }
    *decoded = point == 0 || point > 0x10FFFF || (point >= 0xD800 && point <= 0xDFFF) ? 0xFFFD : point;
    *pos = index;
    return 1;
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
    enum { SCHEME_UNDECIDED = -2 };
    pos = css_skip_ws_comments(value, pos, end);
    Py_UCS4 quote = 0;
    if (pos < end && (value[pos] == '"' || value[pos] == '\'')) {
        quote = value[pos++];
    }
    char inline_scheme[40];
    char *scheme = inline_scheme;
    size_t scheme_len = 0;
    size_t scheme_capacity = sizeof(inline_scheme);
    int scheme_started = 0;
    int allowed = SCHEME_UNDECIDED;
    int result = 0;
    while (pos < end && value[pos] != ')' && (quote ? value[pos] != quote : !is_space(value[pos]))) {
        Py_UCS4 codepoint = value[pos];
        if (codepoint == '\\') {
            if (!css_decode_escape(value, &pos, end, &codepoint)) {
                goto done;
            }
        } else {
            if (!quote &&
                (codepoint == '"' || codepoint == '\'' || codepoint == '(' || codepoint < 0x20 || codepoint == 0x7F)) {
                goto done;
            }
            if (quote && is_css_newline(codepoint)) {
                goto done;
            }
            pos++;
        }
        if (allowed != SCHEME_UNDECIDED || is_url_ignorable(codepoint)) {
            continue;
        }
        if (codepoint == ':' && scheme_started) {
            if (is_script_scheme(scheme, scheme_len)) {
                allowed = 0;
                continue;
            }
            PyObject *name = PyUnicode_FromStringAndSize(scheme, (Py_ssize_t)scheme_len);
            if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                result = -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
                goto done;      /* GCOVR_EXCL_LINE */
            }
            allowed = PySet_Contains(s->url_schemes, name);
            Py_DECREF(name);
            continue;
        }
        int is_scheme_letter = th_scheme_start(codepoint);
        if (scheme_started ? !th_scheme_char(codepoint) : !is_scheme_letter) {
            allowed = s->allow_relative;
            continue;
        }
        if (scheme_len == scheme_capacity) {
            size_t grown_capacity;
            size_t bytes;
            int fits = th_grow_cap(scheme_len + 1, scheme_capacity, sizeof(inline_scheme), sizeof(*scheme),
                                   &grown_capacity, &bytes);
            if (!fits) {          /* GCOVR_EXCL_BR_LINE: a CSS token cannot exhaust size_t */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
                result = -1;      /* GCOVR_EXCL_LINE */
                goto done;        /* GCOVR_EXCL_LINE */
            }
            char *grown = PyMem_Malloc(bytes);
            if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
                result = -1;      /* GCOVR_EXCL_LINE */
                goto done;        /* GCOVR_EXCL_LINE */
            }
            memcpy(grown, scheme, scheme_len);
            if (scheme != inline_scheme) {
                PyMem_Free(scheme);
            }
            scheme = grown;
            scheme_capacity = grown_capacity;
        }
        scheme[scheme_len++] = (char)(is_scheme_letter ? (codepoint | 0x20) : codepoint);
        scheme_started = 1;
    }
    if (quote) {
        if (pos >= end) { /* GCOVR_EXCL_BR_LINE: the declaration splitter rejects an unterminated quoted URL */
            goto done;    /* GCOVR_EXCL_LINE: rejected-declaration path */
        }
        if (value[pos] != quote) {
            goto done;
        }
        pos++;
    }
    pos = css_skip_ws_comments(value, pos, end);
    if (pos < end && value[pos] == ')') {
        result = allowed == SCHEME_UNDECIDED ? s->allow_relative : allowed;
    }
done:
    if (scheme != inline_scheme) {
        PyMem_Free(scheme);
    }
    return result;
}

/* Consume one identifier token, decoding escapes and classifying the exact token as url, expression, or neither.
   Matching the whole token avoids treating an escaped space inside `safe\\ url(...)` as a token boundary. */
static int css_identifier_kind(const Py_UCS4 *value, Py_ssize_t *pos, Py_ssize_t end) {
    char token[11];
    Py_ssize_t length = 0;
    int overflow = 0;
    while (*pos < end && (is_css_ident_char(value[*pos]) || value[*pos] == '\\')) {
        Py_UCS4 codepoint = value[*pos];
        if (codepoint == '\\') {
            if (!css_decode_escape(value, pos, end, &codepoint)) {
                return -1;
            }
        } else {
            (*pos)++;
        }
        if (length < (Py_ssize_t)sizeof(token)) {
            token[length++] = codepoint <= 0x7F ? (char)lower_ascii(codepoint) : '\0';
        } else {
            overflow = 1;
        }
    }
    if (overflow) {
        return 0;
    }
    if (length == 3 && memcmp(token, "url", 3) == 0) {
        return 1;
    }
    return length == 10 && memcmp(token, "expression", 10) == 0 ? 2 : 0;
}

/* Skip a quoted CSS string. Function-like text inside it is data, not a token. */
static Py_ssize_t css_skip_string(const Py_UCS4 *value, Py_ssize_t pos, Py_ssize_t end) {
    Py_UCS4 quote = value[pos++];
    while (pos < end) {
        if (value[pos] == quote) {
            return pos + 1;
        }
        if (value[pos] != '\\') {
            pos++;
            continue;
        }
        pos++;
        if (pos >= end) { /* GCOVR_EXCL_BR_LINE: the declaration splitter rejects a terminal string escape */
            continue;     /* GCOVR_EXCL_LINE: rejected-declaration path */
        }
        if (value[pos] == '\r') {
            pos++;
            if (pos < end && value[pos] == '\n') { /* GCOVR_EXCL_BR_LINE: a terminal CR is rejected upstream */
                pos++;
            }
        } else {
            pos++;
        }
    }
    return end;
}

/* A declaration whose property name is allowlisted can still carry a dangerous value: IE's `expression(...)` runs
   script, and `url(javascript:...)` a disallowed scheme. Scan CSS tokens so inert strings, comments, and longer
   identifiers do not trigger the executable-function checks. Returns 1 allow, 0 drop, -1 error. */
static int css_value_allowed(sanitizer *s, const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end) {
    Py_ssize_t pos = start;
    while (pos < end) {
        if (value[pos] == '/' && pos + 1 < end && value[pos + 1] == '*') {
            pos = css_skip_ws_comments(value, pos, end);
            continue;
        }
        if (value[pos] == '\'' || value[pos] == '"') {
            pos = css_skip_string(value, pos, end);
            continue;
        }
        if (!is_css_ident_char(value[pos]) && value[pos] != '\\') {
            pos++;
            continue;
        }
        int identifier_kind = css_identifier_kind(value, &pos, end);
        if (identifier_kind < 0) {
            return 0;
        }
        Py_ssize_t paren = css_skip_ws_comments(value, pos, end);
        if (identifier_kind == 2 && paren < end && value[paren] == '(') {
            return 0;
        }
        if (identifier_kind == 1 && paren < end && value[paren] == '(') {
            int url_allowed = css_url_scheme_allowed(s, value, paren + 1, end);
            if (url_allowed <= 0) {
                return url_allowed;
            }
        }
    }
    return 1;
}

/* Decide whether the declaration `value[start:end)`, split at `colon` into property and value, survives the policy:
   its property name must be allowlisted and its value free of expression()/url(disallowed-scheme). When `styles` is
   non-NULL, Policy.allowed_styles narrows further -- the property must be listed and its value match a pattern -- on
   top of that baseline, never weakening it. On a keep, the trimmed declaration span (property start through the value's
   last non-whitespace byte) is returned in *name_start and *decl_end. Returns 1 keep, 0 drop, -1 error. Shared by the
   `style` attribute and `<style>` body scrubbers so the safety rules stay identical across both. */
static int css_declaration_kept(sanitizer *s, const style_allowlist *styles, const Py_UCS4 *value, Py_ssize_t start,
                                Py_ssize_t end, Py_ssize_t colon, Py_ssize_t *name_start_out,
                                Py_ssize_t *decl_end_out) {
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
    if (styles != NULL) {
        Py_ssize_t value_start = colon + 1;
        while (value_start < decl_end && is_space(value[value_start])) {
            value_start++;
        }
        PyObject *value_obj =
            PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value + value_start, decl_end - value_start);
        if (value_obj == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int narrowed = css_style_declaration_allowed(s, styles, value + name_start, name_end - name_start, value_obj);
        Py_DECREF(value_obj);
        if (narrowed < 0) { /* GCOVR_EXCL_BR_LINE: css_style_declaration_allowed only fails on allocation failure */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (!narrowed) {
            return 0; /* an unlisted property, or a value no pattern admits, is dropped */
        }
    }
    *name_start_out = name_start;
    *decl_end_out = decl_end;
    return 1;
}

/* Append the declaration `value[start:end)` to `out` if it survives the policy, trimming surrounding whitespace and
   joining kept declarations with "; " (the `style` attribute's declaration-list form). Returns 0, or -1 on error. */
static int css_flush_declaration(sanitizer *s, const style_allowlist *styles, const Py_UCS4 *value, Py_ssize_t start,
                                 Py_ssize_t end, Py_ssize_t colon, Py_UCS4 *out, Py_ssize_t *out_len) {
    Py_ssize_t name_start = 0;
    Py_ssize_t decl_end = 0;
    int kept = css_declaration_kept(s, styles, value, start, end, colon, &name_start, &decl_end);
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

/* Scrub a kept `style` attribute: keep only declarations whose property name is allowlisted, and, when a
   Policy.allowed_styles rule applies to `tag` (its own entry or the "*" wildcard), whose value matches one of that
   property's patterns too. The value is a CSS declaration list; split it on top-level ';' and ':' while skipping
   strings, comments, and parenthesised groups so a separator inside url()/quotes/comments is not mistaken for one.
   Rewrites the attribute, deleting it if nothing survives. Returns 0 on success, -1 on error. */
static int sanitize_style(sanitizer *s, th_node *element, th_node_attr *attr, PyObject *tag) {
    style_allowlist rules = {NULL, NULL};
    const style_allowlist *styles = NULL;
    if (PyDict_GET_SIZE(s->allowed_styles) > 0) {
        rules.tag_rule = PyDict_GetItemWithError(s->allowed_styles, tag);
        if (rules.tag_rule == NULL && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the tag lookup cannot itself error */
            return -1;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        rules.star_rule = PyDict_GetItemWithError(s->allowed_styles, s->star);
        if (rules.star_rule == NULL && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the "*" lookup cannot itself error */
            return -1;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (rules.tag_rule != NULL || rules.star_rule != NULL) {
            styles = &rules; /* a rule applies to this element, so its declarations are narrowed */
        }
    }
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
        int flushed = css_flush_declaration(s, styles, value, decl_start, index, colon, out, &out_len);
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
    int kept = css_declaration_kept(s, NULL, value, start, end, colon, &name_start, &decl_end);
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
        /* a string or comment left open at the end would swallow the `;` and `}` appended after it, so the next pass
           would read them as data and append another pair; drop that declaration to keep the output a fixpoint */
        int flushed = mode == 0 ? css_emit_block_declaration(s, value, seg_start, len, colon, out, &out_len) : 0;
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

/* Does `text[at:]` start a `</style` end tag, matched ASCII case-insensitively as the tokenizer does? */
static int is_style_end_tag(const Py_UCS4 *text, Py_ssize_t at, Py_ssize_t len) {
    static const char end_tag[] = "</style";
    Py_ssize_t tag_len = (Py_ssize_t)(sizeof(end_tag) - 1);
    if (len - at < tag_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < tag_len; index++) {
        if (lower_ascii(text[at + index]) != (Py_UCS4)end_tag[index]) {
            return 0;
        }
    }
    return 1;
}

/* Write the scrubbed stylesheet into `text`. The serializer emits a raw-text body verbatim, so a `</style` left in it
   would end the element early and turn the rest into live markup; its solidus is escaped as `<\/style`, which a CSS
   string reads as the same text and which no longer closes the element. Returns 0, or -1 on error. */
static int set_style_body(sanitizer *s, th_node *text, const Py_UCS4 *css, Py_ssize_t len) {
    Py_ssize_t end_tags = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        end_tags += is_style_end_tag(css, index, len);
    }
    if (end_tags == 0) {
        return th_node_set_data(s->tree, text, css, len);
    }
    Py_UCS4 *escaped = PyMem_Malloc((size_t)(len + end_tags) * sizeof(Py_UCS4));
    if (escaped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t written = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        escaped[written++] = css[index];
        if (is_style_end_tag(css, index, len)) {
            escaped[written++] = '\\';
        }
    }
    int status = th_node_set_data(s->tree, text, escaped, written);
    PyMem_Free(escaped);
    return status;
}

/* Scrub the CSS a kept `<style>` element holds. A parsed raw-text element carries its stylesheet as one text child, but
   a built tree or a transformed element can hold several text runs, elements, or comments; the serializer would emit
   those elements as markup inside the raw text. Drop every non-text child, merge the text into the first child, and
   rewrite it with the policy-safe subset. Returns 0, or -1 on error. */
static int sanitize_style_body(sanitizer *s, th_node *element) {
    for (th_node *child = element->first_child, *next; child != NULL; child = next) {
        next = child->next_sibling;
        if (child->type != TH_NODE_TEXT) {
            th_node_remove(child);
        }
    }
    th_node *text = element->first_child;
    if (text == NULL) {
        return 0; /* an empty <style></style> has no body to scrub */
    }
    Py_ssize_t len = 0;
    Py_UCS4 *body = th_node_text(s->tree, element, &len);
    if (body == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    while (text->next_sibling != NULL) {
        th_node_remove(text->next_sibling);
    }
    Py_UCS4 *out = PyMem_Malloc((size_t)(2 * len + 16) * sizeof(Py_UCS4));
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(body); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t out_len = 0;
    int status = scrub_stylesheet(s, body, len, out, &out_len);
    if (status == 0) { /* GCOVR_EXCL_BR_LINE: scrub_stylesheet's non-zero return is its allocation-failure path */
        status = set_style_body(s, text, out, out_len);
    }
    PyMem_Free(out);
    PyMem_Free(body);
    return status;
}

static Py_ssize_t template_start(const Py_UCS4 *data, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index + 1 < len; index++) {
        if (((data[index] == '{' || data[index] == '$') && data[index + 1] == '{') ||
            (data[index] == '<' && data[index + 1] == '%')) {
            return index;
        }
    }
    return len;
}

/* Template engines can evaluate markers after sanitization; collapse runs through their nearest close. */
static Py_ssize_t strip_template_markers(const Py_UCS4 *in, Py_ssize_t len, Py_UCS4 *out, Py_ssize_t start) {
    memcpy(out, in, (size_t)start * sizeof(Py_UCS4));
    Py_ssize_t write = start;
    Py_ssize_t read = start;
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
        read = scan;
    }
    return write;
}

static int strip_attr_templates(sanitizer *s, th_node *element, th_node_attr *attr) {
    Py_ssize_t start = template_start(attr->value, attr->value_len);
    if (start == attr->value_len) {
        return 0;
    }
    Py_UCS4 *out = PyMem_Malloc((size_t)attr->value_len * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t out_len = strip_template_markers(attr->value, attr->value_len, out, start);
    Py_ssize_t name_len = 0;
    const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
    int status = th_node_attr_set(s->tree, element, name, name_len, out, out_len, 1);
    PyMem_Free(out);
    return status;
}

/* DOMPurify's SANITIZE_NAMED_PROPS. An attacker-controlled id or name whose value matches a built-in document or form
   property name shadows that property through named access -- DOM clobbering, where `<input name="attributes">` makes
   `form.attributes` resolve to the input and `<img name="body">` hides `document.body`. Prefixing every kept id/name
   value with "user-content-" moves it out of the property namespace, so no value can collide with a real property
   name; the prefix is left in place when already present, so re-sanitizing is a fixpoint. A bare id/name carries no
   value to shadow with, but is prefixed all the same so the isolation is unconditional on the two attributes.
   Returns 0, or -1 on allocation failure. */
static int prefix_named_prop(sanitizer *s, th_node *element, const th_node_attr *attr) {
    Py_ssize_t name_len = 0;
    const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
    if (!((name_len == 2 && memcmp(name, "id", 2) == 0) || (name_len == 4 && memcmp(name, "name", 4) == 0))) {
        return 0; /* only id and name expose named-property access, so no other attribute is isolated */
    }
    static const char prefix[] = "user-content-";
    Py_ssize_t prefix_len = (Py_ssize_t)(sizeof(prefix) - 1);
    const Py_UCS4 *value = attr->value;
    Py_ssize_t len = value == NULL ? 0 : attr->value_len;
    if (len >= prefix_len) {
        int already = 1;
        for (Py_ssize_t index = 0; already && index < prefix_len; index++) {
            already = value[index] == (Py_UCS4)prefix[index];
        }
        if (already) {
            return 0; /* already isolated, so re-prefixing would double the marker and corrupt the value */
        }
    }
    Py_UCS4 *out = PyMem_Malloc((size_t)(prefix_len + len) * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < prefix_len; index++) {
        out[index] = (Py_UCS4)prefix[index];
    }
    if (len > 0) { /* a bare id/name has no value to copy; the prefix alone becomes its value */
        memcpy(out + prefix_len, value, (size_t)len * sizeof(Py_UCS4));
    }
    int status = th_node_attr_set(s->tree, element, name, name_len, out, prefix_len + len, 1);
    PyMem_Free(out);
    return status;
}

/* Call one boolean policy predicate on `arg`, returning 1 keep, 0 drop, -1 when the predicate raised. */
static int predicate_true(PyObject *callable, PyObject *arg) {
    PyObject *result = PyObject_CallOneArg(callable, arg);
    if (result == NULL) {
        return -1;
    }
    int truth = PyObject_IsTrue(result);
    Py_DECREF(result);
    return truth;
}

/* Call one boolean policy predicate on two arguments, returning 1 keep, 0 drop, -1 when the predicate raised. */
static int predicate_true2(PyObject *callable, PyObject *first, PyObject *second) {
    PyObject *result = PyObject_CallFunctionObjArgs(callable, first, second, NULL);
    if (result == NULL) {
        return -1;
    }
    int truth = PyObject_IsTrue(result);
    Py_DECREF(result);
    return truth;
}

/* The hyphenated names the HTML spec reserves from valid-custom-element-name, so a permissive custom_element_check
   cannot treat a real SVG/MathML element (annotation-xml, font-face, ...) as a custom element. `name` is the NUL-
   terminated UTF-8 the tag interns to. */
static int is_reserved_custom_name(const char *name) {
    static const char *const reserved[] = {"annotation-xml", "color-profile", "font-face",     "font-face-format",
                                           "font-face-name", "font-face-src", "font-face-uri", "missing-glyph"};
    for (size_t index = 0; index < sizeof(reserved) / sizeof(reserved[0]); index++) {
        if (strcmp(name, reserved[index]) == 0) {
            return 1;
        }
    }
    return 0;
}

/* DOMPurify's _isBasicCustomElement: does `name` (a parsed element's lowercased tag) meet the basic custom-element
   grammar `^[a-z][.\w]*(-[.\w]+)+$` and stay clear of the reserved names? The tokenizer starts a tag name only on an
   ASCII letter, so the leading-letter clause already holds; what remains is at least one '-', each followed by a name
   character, over the [.\w-] set. Returns 1 a basic custom element, 0 not. */
static int is_custom_element_name(const char *name, Py_ssize_t len) {
    int has_dash = 0;
    for (Py_ssize_t index = 1; index < len; index++) {
        unsigned char c = (unsigned char)name[index];
        if (c == '-') {
            if (name[index - 1] == '-' || index + 1 == len) {
                return 0; /* a '-' must be followed by a name character: no trailing or doubled dash */
            }
            has_dash = 1;
        } else if (!is_ascii_alpha(c) && !is_ascii_digit(c) && c != '_' && c != '.') {
            return 0; /* only [.\w-] continue a custom-element name */
        }
    }
    return has_dash && !is_reserved_custom_name(name);
}

/* Is `tag` a basic HTML custom element the caller's custom_element_check admits? Only consulted when a check is set;
   the tag's UTF-8 is already interned, so the name test is a byte scan. Returns 1 keep as a custom element, 0 not, -1
   when the predicate raised. */
static int custom_element_kept(sanitizer *s, PyObject *tag) {
    Py_ssize_t name_len = 0;
    const char *name = PyUnicode_AsUTF8AndSize(tag, &name_len);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: the tag is a freshly built str, so encoding it cannot fail */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!is_custom_element_name(name, name_len)) {
        return 0;
    }
    return predicate_true(s->custom_element_check, tag);
}

static int is_event_attribute(const char *name, Py_ssize_t name_len) {
    return name_len >= 2 && name[0] == 'o' && name[1] == 'n';
}

enum attribute_safety_result {
    ATTRIBUTE_SAFETY_ERROR = -1,
    ATTRIBUTE_SAFETY_REMOVED,
    ATTRIBUTE_SAFETY_KEPT,
    ATTRIBUTE_SAFETY_RESCAN,
};

/* Apply the safety rules that no policy callback can bypass. `isolate` is false before attribute_filter, which must see
   the original id/name, and true after a late write. */
static enum attribute_safety_result apply_attribute_safety_at(sanitizer *s, th_node *element, PyObject *tag,
                                                              Py_ssize_t index, int isolate) {
    th_node_attr *attr = &element->attrs[index];
    Py_ssize_t name_len = 0;
    const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
    int drop = is_event_attribute(name, name_len);
    if (!drop && s->strip_templates && strip_attr_templates(s, element, attr) < 0) { /* GCOVR_EXCL_BR_LINE */
        return ATTRIBUTE_SAFETY_ERROR;                                               /* GCOVR_EXCL_LINE */
    }
    attr = &element->attrs[index];
    if (!drop && PyDict_GET_SIZE(s->attribute_values) > 0) {
        int keep = value_allowed(s, tag, name, name_len, attr->value, attr->value_len);
        if (keep < 0) {                    /* GCOVR_EXCL_BR_LINE: value_allowed only fails on allocation failure */
            return ATTRIBUTE_SAFETY_ERROR; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        drop = !keep;
    }
    int url_disallowed = 0;
    if (!drop && is_url_attr(name, name_len)) {
        int keep = s->bleach_url_policy && !isolate /* GCOVR_EXCL_BR_LINE: migration does not rewrite attributes */
                       ? bleach_url_allowed(s, element, attr, name, name_len)
                       : scheme_allowed(s, attr->value, attr->value_len);
        if (keep < 0) {                    /* GCOVR_EXCL_BR_LINE: scheme_allowed only fails on allocation failure */
            return ATTRIBUTE_SAFETY_ERROR; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        url_disallowed = !keep;
    }
    Py_ssize_t url_start = 0;
    Py_ssize_t url_end = 0;
    if (!drop && name_len == 7 && memcmp(name, "content", 7) == 0 && is_refresh_meta(s, element) &&
        refresh_url(attr->value, attr->value_len, &url_start, &url_end)) {
        int keep = scheme_allowed(s, attr->value + url_start, url_end - url_start);
        if (keep < 0) {                    /* GCOVR_EXCL_BR_LINE: scheme_allowed only fails on allocation failure */
            return ATTRIBUTE_SAFETY_ERROR; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        url_disallowed = !keep;
    }
    if (!drop && is_srcset_attr(name, name_len)) {
        int keep = srcset_allowed(s, attr->value, attr->value_len);
        if (keep < 0) {                    /* GCOVR_EXCL_BR_LINE: srcset_allowed only fails on allocation failure */
            return ATTRIBUTE_SAFETY_ERROR; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        url_disallowed = !keep;
    }
    if (!drop && !url_disallowed && PySet_GET_SIZE(s->media_hosts) > 0 && is_media_host_tag(element->atom) &&
        name_len == 3 && memcmp(name, "src", 3) == 0) {
        int keep = host_allowed(s, attr->value, attr->value_len);
        if (keep < 0) {                    /* GCOVR_EXCL_BR_LINE: host_allowed only fails on allocation failure */
            return ATTRIBUTE_SAFETY_ERROR; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        url_disallowed = !keep;
    }
    if (drop || url_disallowed) {
        if (record_removed(s, tag, name, name_len) < 0) { /* GCOVR_EXCL_BR_LINE: record only fails on allocation */
            return ATTRIBUTE_SAFETY_ERROR;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (url_disallowed) {
            /* A browser keeps one duplicate URL attribute; removing every occurrence prevents a later unsafe value
               from becoming the serialized one. */
            while (th_node_attr_del(s->tree, element, name, name_len)) {
            }
            return ATTRIBUTE_SAFETY_RESCAN;
        }
        th_node_attr_del(s->tree, element, name, name_len);
        return ATTRIBUTE_SAFETY_REMOVED;
    }
    if (name_len == 5 && memcmp(name, "style", 5) == 0) {
        Py_ssize_t before = element->attr_count;
        if (sanitize_style(s, element, attr, tag) < 0) { /* GCOVR_EXCL_BR_LINE: only on allocation failure */
            return ATTRIBUTE_SAFETY_ERROR;               /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (element->attr_count < before) {
            return ATTRIBUTE_SAFETY_REMOVED;
        }
    }
    if (isolate && s->isolate_named_props && /* GCOVR_EXCL_BR_LINE: prefix_named_prop only fails on allocation */
        prefix_named_prop(s, element, &element->attrs[index]) < 0) { /* GCOVR_EXCL_BR_LINE: only allocation fails */
        return ATTRIBUTE_SAFETY_ERROR; /* GCOVR_EXCL_LINE: allocation failure cannot be forced from a test */
    }
    return ATTRIBUTE_SAFETY_KEPT;
}

/* Re-check the values that will reach the serializer after callbacks and forced attributes have written them. */
static int apply_late_attribute_safety(sanitizer *s, th_node *element, PyObject *tag) {
    Py_ssize_t index = 0;
    while (index < element->attr_count) {
        enum attribute_safety_result result = apply_attribute_safety_at(s, element, tag, index, 1);
        if (result == ATTRIBUTE_SAFETY_ERROR) { /* GCOVR_EXCL_BR_LINE: only allocation failures reach this path */
            return -1;                          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (result == ATTRIBUTE_SAFETY_RESCAN) {
            index = 0;
        } else if (result == ATTRIBUTE_SAFETY_KEPT) {
            index++;
        }
    }
    return 0;
}

static int compact_disallowed_attributes(sanitizer *s, th_node *element, PyObject *tag) {
    Py_ssize_t kept = 0;
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len;
        const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
        int allowed = is_event_attribute(name, name_len) ? 0 : attr_allowed(s, tag, attr->name_atom, name, name_len);
        if (allowed < 0) {
            return -1;
        }
        if (allowed) {
            if (kept != index) {
                element->attrs[kept] = *attr;
            }
            kept++;
        }
    }
    element->attr_count = kept;
    return 0;
}

static int apply_attribute_predicate(sanitizer *s, th_node *element, PyObject *tag);

static int sanitize_attributes(sanitizer *s, th_node *element, PyObject *tag, int custom) {
    if (s->attribute_predicate != Py_None && apply_attribute_predicate(s, element, tag) < 0) {
        return -1;
    }
    int compacted = element->attr_count >= 32 && s->removed == NULL && s->attribute_filter == Py_None &&
                    s->custom_attribute_check == Py_None && s->custom_element_check == Py_None;
    /* The private tree has no observers or cached lookups before sanitization returns. */
    if (compacted) {
        if (compact_disallowed_attributes(s, element, tag) < 0) {
            return -1;
        }
    }
    Py_ssize_t index = 0;
    int late_written = 0;
    while (index < element->attr_count) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = compacted ? NULL : th_attr_name(s->tree, attr->name_atom, &name_len);
        int drop = !compacted && is_event_attribute(name, name_len);
        if (!compacted && !drop) {
            int allowed = attr_allowed(s, tag, attr->name_atom, name, name_len);
            if (allowed < 0) {
                return -1;
            }
            if (!allowed) {
                /* an unlisted attribute survives on a kept custom element only when custom_attribute_check admits it,
                   and an `is` on any element only when allow_customized_builtins is on and its value names a custom
                   element -- both an allowlist substitute, never a bypass: the on*, URL, and style baseline below still
                   runs on whatever they keep */
                int keep = 0;
                if (custom && s->custom_attribute_check != Py_None) {
                    PyObject *attr = PyUnicode_FromStringAndSize(name, name_len);
                    if (attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    keep = predicate_true2(s->custom_attribute_check, tag, attr);
                    Py_DECREF(attr);
                    if (keep < 0) {
                        return -1;
                    }
                }
                if (!keep && s->allow_customized_builtins && s->custom_element_check != Py_None && name_len == 2 &&
                    name[0] == 'i' && name[1] == 's' && attr->value != NULL) {
                    PyObject *value = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len);
                    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    keep = predicate_true(s->custom_element_check, value);
                    Py_DECREF(value);
                    if (keep < 0) {
                        return -1;
                    }
                }
                drop = !keep;
            }
        }
        if (drop) {
            if (record_removed(s, tag, name, name_len) < 0) { /* GCOVR_EXCL_BR_LINE: record only fails on alloc */
                return -1;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            th_node_attr_del(s->tree, element, name, name_len);
            continue; /* the next attribute shifted into this slot */
        }
        enum attribute_safety_result safety = apply_attribute_safety_at(s, element, tag, index, 0);
        if (safety == ATTRIBUTE_SAFETY_ERROR) { /* GCOVR_EXCL_BR_LINE: only allocation failures reach this path */
            return -1;                          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (safety == ATTRIBUTE_SAFETY_RESCAN) {
            index = 0;
            continue;
        }
        if (safety == ATTRIBUTE_SAFETY_REMOVED) {
            continue;
        }
        attr = &element->attrs[index];
        name = th_attr_name(s->tree, attr->name_atom, &name_len);
        if (s->attribute_filter != Py_None) {
            Py_ssize_t before = element->attr_count;
            int filtered = run_attribute_filter(s, element, tag, name, name_len, attr->value, attr->value_len);
            if (filtered < 0) {
                return -1;
            }
            late_written |= filtered;
            if (element->attr_count < before) {
                continue; /* the filter deleted it */
            }
        }
        /* Isolate last, after the filter has had its say, so the value it returns is what gets namespaced and no
           attribute_filter can leave a clobbering value un-prefixed. Re-fetch the slot: a set above may have
           reallocated the attribute array. prefix_named_prop only fails on allocation, which no test can force. */
        th_node_attr *slot = &element->attrs[index];
        if (s->isolate_named_props && prefix_named_prop(s, element, slot) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                           /* GCOVR_EXCL_LINE */
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
    int forced = apply_set_attributes(s, element, tag);
    if (forced < 0) { /* GCOVR_EXCL_BR_LINE: only on allocation failure */
        return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    late_written |= forced;
    if (!late_written) {
        return 0;
    }
    return apply_late_attribute_safety(s, element, tag);
}

static th_node *next_sanitizer_node(th_node *node, th_node *root) {
    if (node->first_child != NULL) {
        return node->first_child;
    }
    while (node != root && node->next_sibling == NULL) {
        node = node->parent;
    }
    return node == root ? NULL : node->next_sibling;
}

static int compare_bleach_origins(const void *left, const void *right) {
    const bleach_origin *first = left, *second = right;
    uintptr_t first_value = (uintptr_t)first->value, second_value = (uintptr_t)second->value;
    if (first_value != second_value) {
        return first_value < second_value ? -1 : 1; /* GCOVR_EXCL_BR_LINE: qsort traversal is platform-dependent */
    }
    return (first->name_atom > second->name_atom) - (first->name_atom < second->name_atom);
}

static int collect_bleach_origins(sanitizer *s, th_node *root) {
    Py_ssize_t count = 0;
    int has_clone = 0;
    for (th_node *node = root; node != NULL; node = next_sanitizer_node(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->attr_count == 0) {
            continue;
        }
        has_clone |= th_node_source_location(s->tree, node) == NULL;
        count += node->attr_count;
    }
    if (!has_clone) {
        return 0;
    }
    s->bleach_origins = PyMem_Malloc((size_t)count * sizeof(bleach_origin));
    if (s->bleach_origins == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();            /* GCOVR_EXCL_LINE */
        return -1;                   /* GCOVR_EXCL_LINE */
    }
    for (th_node *node = root; node != NULL; node = next_sanitizer_node(node, root)) {
        const th_src_loc *location = th_node_source_location(s->tree, node);
        if (location == NULL) {
            continue;
        }
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            th_node_attr *attr = &node->attrs[index];
            Py_ssize_t source_count = location->attr_count;
            for (Py_ssize_t source_index = 0; source_index < source_count; source_index++) { /* GCOVR_EXCL_BR_LINE */
                if (location->attrs[source_index].name_atom == attr->name_atom) {
                    s->bleach_origins[s->bleach_origin_count++] =
                        (bleach_origin){attr->value, attr->name_atom, location->attrs[source_index].span};
                    break;
                }
            }
        }
    }
    qsort(s->bleach_origins, (size_t)s->bleach_origin_count, sizeof(bleach_origin), compare_bleach_origins);
    return 0;
}

static PyObject *bleach_raw_value(sanitizer *s, th_node *element, th_node_attr *attr, Py_ssize_t name_length) {
    const th_src_loc *location = th_node_source_location(s->tree, element);
    th_src_span origin_span;
    if (location == NULL) {
        if (s->bleach_origins == NULL) {
            return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len);
        }
        bleach_origin key = {.value = attr->value, .name_atom = attr->name_atom};
        bleach_origin *found = bsearch(&key, s->bleach_origins, (size_t)s->bleach_origin_count, sizeof(bleach_origin),
                                       compare_bleach_origins);
        if (found == NULL) { /* GCOVR_EXCL_BR_LINE: parser clones share the source attribute buffer */
            return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len); /* GCOVR_EXCL_LINE */
        }
        origin_span = found->span;
    }
    int kind, has_nul;
    const void *data = th_tree_source_data(s->tree, &kind, &has_nul);
    Py_ssize_t count = location == NULL ? 1 : location->attr_count;
    for (Py_ssize_t index = 0; index < count; index++) { /* GCOVR_EXCL_BR_LINE */
        if (location != NULL && location->attrs[index].name_atom != attr->name_atom) {
            continue;
        }
        th_src_span span = location == NULL ? origin_span : location->attrs[index].span;
        Py_ssize_t start = span.start_offset + name_length;
        Py_ssize_t end = span.end_offset;
        while (start < end && PyUnicode_READ(kind, data, start) != '=') {
            start++;
        }
        if (start < end) {
            start++;
            while (start < end && is_space(PyUnicode_READ(kind, data, start))) { /* GCOVR_EXCL_BR_LINE */
                start++;
            }
            if (start < end) { /* GCOVR_EXCL_BR_LINE: a tokenizer value follows '=' */
                Py_UCS4 quote = PyUnicode_READ(kind, data, start);
                if (quote == '\'' || quote == '"') {
                    start++;
                    if (end > start && PyUnicode_READ(kind, data, end - 1) == quote) { /* GCOVR_EXCL_BR_LINE */
                        end--;
                    }
                }
            }
        }
        PyObject *raw = th_str_from_kind(kind, (const char *)data + start * kind, end - start);
        if (raw == NULL || !has_nul) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return raw;
        }
        PyObject *nul = PyUnicode_FromStringAndSize("\0", 1);
        PyObject *replacement = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, (Py_UCS4[]){0xfffd}, 1);
        PyObject *normalized = nul != NULL && replacement != NULL /* GCOVR_EXCL_BR_LINE: allocation failure */
                                   ? PyUnicode_Replace(raw, nul, replacement, -1)
                                   : NULL; /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(raw);
        Py_XDECREF(nul);
        Py_XDECREF(replacement);
        return normalized;
    }
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len); /* GCOVR_EXCL_LINE */
}

static Py_ssize_t bleach_uri_entity(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t index, Py_UCS4 *first) {
    if (index + 2 >= len) {
        return 0;
    }
    Py_ssize_t end = index + 1;
    if (value[end] == '#') {
        int base = 10;
        end++;
        if (end < len && (value[end] == 'x' || value[end] == 'X')) { /* GCOVR_EXCL_BR_LINE: short refs return above */
            base = 16;
            end++;
        }
        Py_ssize_t digits = end;
        Py_UCS4 number = 0;
        while (end < len) {
            int digit = charref_hex_value(value[end]);
            if (digit < 0 || digit >= base) {
                break;
            }
            if (number <= 0x10ffff) {
                number = number * (Py_UCS4)base + (Py_UCS4)digit;
            }
            end++;
        }
        if (end == digits || end == len || value[end] != ';' || number == 0 || number > 0x10ffff) {
            return 0;
        }
        *first = number;
        return end - index + 1;
    }
    char name[HTML5_MAX_NAME_LEN];
    Py_ssize_t count = 0;
    while (end < len && count < (Py_ssize_t)sizeof(name) &&
           ((value[end] >= 'a' && value[end] <= 'z') ||  /* GCOVR_EXCL_BR_LINE: GCC short-circuit edge */
            (value[end] >= 'A' && value[end] <= 'Z') ||  /* GCOVR_EXCL_BR_LINE: GCC splits the short-circuit edge */
            (value[end] >= '0' && value[end] <= '9'))) { /* GCOVR_EXCL_BR_LINE: LLVM splits the short-circuit edge */
        name[count++] = (char)value[end++];
    }
    if (count == 0 || end == len || value[end] != ';') {
        return 0;
    }
    const html5_entity *entity = charref_find_entity(name, count);
    if (entity == NULL) {
        return 0;
    }
    /* Only semicolonless aliases resolve here; none expands to two code points. */
    *first = entity->cp0;
    return end - index + 1;
}

static int bleach_script_url(const Py_UCS4 *value, Py_ssize_t len) {
    char scheme[10];
    Py_ssize_t size = 0;
    int started = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 codepoint = value[index];
        if (is_url_ignorable(codepoint) || codepoint >= 0x80) {
            continue;
        }
        if (codepoint == ':' && started) {
            return is_script_scheme(scheme, (size_t)size);
        }
        int letter = th_scheme_start(codepoint);
        if (started ? !th_scheme_char(codepoint) : !letter) {
            return 0;
        }
        if (size < (Py_ssize_t)sizeof(scheme)) {
            scheme[size++] = (char)(letter ? codepoint | 0x20 : codepoint);
        }
        started = 1;
    }
    return 0;
}

static int bleach_scheme_in(sanitizer *s, const char *name, Py_ssize_t len) {
    PyObject *scheme = PyUnicode_FromStringAndSize(name, len);
    if (scheme == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    int allowed = PySet_Contains(s->url_schemes, scheme);
    Py_DECREF(scheme);
    return allowed;
}

static int bleach_plain_url(const Py_UCS4 *value, Py_ssize_t len) {
    int scheme = len > 0 && th_scheme_start(value[0]);
    int colon = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 codepoint = value[index];
        if (codepoint <= 0x20 || codepoint >= 0x7f || codepoint == '`') {
            return 0;
        }
        if (!colon) {
            if (codepoint == ':') {
                colon = 1;
            } else if (!th_scheme_char(codepoint)) {
                scheme = 0;
            }
        }
    }
    return !colon || scheme;
}

static int bleach_url_has_raw_amp(sanitizer *s, th_node *element, th_node_attr *attr) {
    if (!s->bleach_raw_urls) {
        return 0;
    }
    const th_src_loc *location = th_node_source_location(s->tree, element);
    th_src_span span;
    if (location == NULL) {
        bleach_origin key = {.value = attr->value, .name_atom = attr->name_atom};
        bleach_origin *found = bsearch(&key, s->bleach_origins, (size_t)s->bleach_origin_count, sizeof(bleach_origin),
                                       compare_bleach_origins);
        if (found == NULL) { /* GCOVR_EXCL_BR_LINE: parser clones share the source attribute buffer */
            return 1;        /* GCOVR_EXCL_LINE: clone origins are collected first */
        }
        span = found->span;
    } else {
        Py_ssize_t index = 0;
        while (index < location->attr_count && /* GCOVR_EXCL_BR_LINE: tokenizer supplies the matching span */
               location->attrs[index].name_atom != attr->name_atom) {
            index++;
        }
        if (index == location->attr_count) { /* GCOVR_EXCL_BR_LINE: a parsed attribute has a source span */
            return 1;                        /* GCOVR_EXCL_LINE: tokenizer supplies the matching span */
        }
        span = location->attrs[index].span;
    }
    int kind, has_nul;
    const void *data = th_tree_source_data(s->tree, &kind, &has_nul);
    for (Py_ssize_t index = span.start_offset; index < span.end_offset; index++) {
        if (PyUnicode_READ(kind, data, index) == '&') {
            return 1;
        }
    }
    return 0;
}

static int bleach_url_allowed(sanitizer *s, th_node *element, th_node_attr *attr, const char *name,
                              Py_ssize_t name_len) {
    int raw_url = bleach_url_has_raw_amp(s, element, attr);
    if (!raw_url && bleach_plain_url(attr->value, attr->value_len)) {
        return scheme_allowed(s, attr->value, attr->value_len);
    }
    if (bleach_script_url(attr->value, attr->value_len) || !authority_allowed(attr->value, 0, attr->value_len)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < attr->value_len; index++) {
        if (attr->value[index] == ':') {
            if (!authority_allowed(attr->value, index + 1, attr->value_len)) {
                return 0;
            }
            break;
        }
    }
    const Py_UCS4 *value = attr->value;
    Py_ssize_t len = attr->value_len;
    PyObject *raw = NULL;
    Py_UCS4 *raw_points = NULL;
    if (raw_url) {
        Py_ssize_t points = 0;
        for (Py_ssize_t index = 0; index < name_len; index++) {
            points += ((unsigned char)name[index] & 0xc0) != 0x80;
        }
        raw = bleach_raw_value(s, element, attr, points);
        if (raw == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;     /* GCOVR_EXCL_LINE */
        }
        raw_points = PyUnicode_AsUCS4Copy(raw);
        if (raw_points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(raw);       /* GCOVR_EXCL_LINE */
            return -1;            /* GCOVR_EXCL_LINE */
        }
        value = raw_points;
        len = PyUnicode_GET_LENGTH(raw);
    }
    char stack[128];
    char *normalized = len < (Py_ssize_t)sizeof(stack) ? stack : PyMem_Malloc((size_t)len + 1);
    if (normalized == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(raw_points); /* GCOVR_EXCL_LINE */
        Py_XDECREF(raw);        /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();       /* GCOVR_EXCL_LINE */
        return -1;              /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t size = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 codepoint = value[index];
        Py_ssize_t consumed = codepoint == '&' ? bleach_uri_entity(value, len, index, &codepoint) : 0;
        if (consumed > 0) {
            index += consumed - 1;
        }
        if (codepoint > 0x20 && codepoint < 0x7f && codepoint != '`') {
            normalized[size++] = (char)((codepoint >= 'A' && codepoint <= 'Z') ? codepoint | 0x20 : codepoint);
        }
    }
    PyMem_Free(raw_points);
    Py_XDECREF(raw);
    Py_ssize_t colon = -1;
    int scheme = 1;
    for (Py_ssize_t index = 0; index < size; index++) {
        char character = normalized[index];
        if (character == ':') {
            colon = index;
            break;
        }
        if (character == '/' || character == '?' || character == '#') {
            scheme = 0;
        }
        if (!th_scheme_char((unsigned char)character)) {
            scheme = 0;
        }
    }
    Py_ssize_t authority = colon > 0 && scheme ? colon + 1 : 0;
    if (authority + 1 < size && normalized[authority] == '/' && normalized[authority + 1] == '/') {
        Py_UCS4 wide_stack[128];
        Py_UCS4 *wide = size < (Py_ssize_t)(sizeof(wide_stack) / sizeof(wide_stack[0]))
                            ? wide_stack
                            : PyMem_Malloc((size_t)size * sizeof(Py_UCS4));
        if (wide == NULL) {             /* GCOVR_EXCL_BR_LINE: allocation failure */
            if (normalized != stack) {  /* GCOVR_EXCL_LINE */
                PyMem_Free(normalized); /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < size; index++) {
            wide[index] = (Py_UCS4)(unsigned char)normalized[index];
        }
        int valid = authority_allowed(wide, authority, size);
        if (wide != wide_stack) {
            PyMem_Free(wide);
        }
        if (!valid) {
            if (normalized != stack) {
                PyMem_Free(normalized);
            }
            return 0;
        }
    }
    int allowed;
    if (colon > 0 && scheme) {
        allowed = bleach_scheme_in(s, normalized, colon);
    } else if (size > 0 && normalized[0] == '#') {
        allowed = 1;
    } else if (colon > 0) {
        allowed = bleach_scheme_in(s, normalized, colon);
        if (allowed == 0) {
            allowed = s->allow_relative;
        }
    } else {
        allowed = s->allow_relative;
    }
    if (normalized != stack) {
        PyMem_Free(normalized);
    }
    return allowed;
}

static int apply_attribute_predicate(sanitizer *s, th_node *element, PyObject *tag) {
    Py_ssize_t kept = 0;
    /* Safety checks can restart the attribute walk; predicates must run once on the original values. */
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len;
        const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
        PyObject *key = PyUnicode_FromStringAndSize(name, name_len);
        PyObject *value = s->bleach_raw_values && key != NULL /* GCOVR_EXCL_BR_LINE: allocation failure */
                              ? bleach_raw_value(s, element, attr, PyUnicode_GET_LENGTH(key))
                              : PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len);
        if (key == NULL || value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_XDECREF(key);                /* GCOVR_EXCL_LINE */
            Py_XDECREF(value);              /* GCOVR_EXCL_LINE */
            return -1;                      /* GCOVR_EXCL_LINE */
        }
        PyObject *result = PyObject_CallFunctionObjArgs(s->attribute_predicate, tag, key, value, NULL);
        Py_DECREF(key);
        Py_DECREF(value);
        int keep = result == NULL ? -1 : PyObject_IsTrue(result);
        Py_XDECREF(result);
        if (keep < 0) {
            return -1;
        }
        if (keep) {
            if (kept != index) {
                element->attrs[kept] = *attr;
            }
            kept++;
        } else if (record_removed(s, tag, name, name_len) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                           /* GCOVR_EXCL_LINE */
        }
    }
    element->attr_count = kept;
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
    PyObject *out = th_str_format("<%U", tag);
    Py_DECREF(tag);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *pieces = PyList_New(element->attr_count + 1);
    if (pieces == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(out);   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    PyList_SET_ITEM(pieces, 0, out);
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        th_node_attr *attr = &element->attrs[index];
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(s->tree, attr->name_atom, &name_len);
        PyObject *name_str = PyUnicode_FromStringAndSize(name, name_len);
        if (name_str == NULL) {
            Py_DECREF(pieces);
            return NULL;
        }
        PyObject *piece;
        if (attr->value == NULL) {
            piece = th_str_format(" %U", name_str);
        } else {
            PyObject *value = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len);
            if (value == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(name_str); /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(pieces);   /* GCOVR_EXCL_LINE */
                return NULL;         /* GCOVR_EXCL_LINE */
            }
            piece = th_str_format(" %U=\"%U\"", name_str, value);
            Py_DECREF(value);
        }
        Py_DECREF(name_str);
        if (piece == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(pieces); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;       /* GCOVR_EXCL_LINE */
        }
        PyList_SET_ITEM(pieces, index + 1, piece);
    }
    PyObject *separator = PyUnicode_FromString("");
    if (separator == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(pieces);   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    out = PyUnicode_Join(separator, pieces);
    Py_DECREF(separator);
    Py_DECREF(pieces);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_SETREF(out, th_str_format("%U>", out));
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

/* Replace a disallowed element with its escaped start tag, its already-sanitized children, and its escaped end tag. */
static int escape_element(sanitizer *s, th_node *element) {
    /* Only reproduce a start tag the source actually wrote: the tbody a bare
       `<table><tr>` implies has none, so escaping must not fabricate one. */
    if (!(element->tag_flags & TH_ELEM_IMPLIED)) {
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
    }
    hoist_children(element);
    /* Only reproduce an end tag the source actually wrote: a void element or an
       unclosed one (`<name of movie>`, `I love <sarcasm> this`) carries no close
       tag, so escaping must not fabricate one. */
    if (element->tag_flags & TH_ELEM_CLOSED_BY_END_TAG) {
        PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, element->text, element->text_len);
        if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *closing = th_str_format("</%U>", tag);
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

/* ASCII case-insensitive match of an attribute value against a lowercase ASCII string. */
static int value_matches_ci(const Py_UCS4 *value, Py_ssize_t len, const char *target) {
    if (len != (Py_ssize_t)strlen(target)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (lower_ascii(value[index]) != (Py_UCS4)target[index]) {
            return 0;
        }
    }
    return 1;
}

/* An HTML integration point: an SVG foreignObject/desc/title, or a MathML annotation-xml whose encoding is text/html
   or application/xhtml+xml. The encoding is read from the attributes the element will serialize with, which
   sanitize_attributes has already settled because a parent is sanitized before its children are walked: a policy that
   strips the encoding turns the annotation-xml back into plain MathML on reparse, so its HTML children are then
   unreachable. The caller has already established the node is foreign, so a non-SVG parent is MathML. */
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
    if (node->atom != TH_TAG_ANNOTATION_XML) {
        return 0;
    }
    /* a reparse keeps the first of duplicate attributes, so the first encoding decides */
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        if (attr->name_atom == TH_ATTR_ENCODING) {
            /* a bare attribute has a NULL value of length 0, which the length check rejects before any read */
            return value_matches_ci(attr->value, attr->value_len, "text/html") ||
                   value_matches_ci(attr->value, attr->value_len, "application/xhtml+xml");
        }
    }
    return 0;
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
    if (is_mathml_text_point(parent)) {
        /* a text point parses an mglyph or malignmark start tag as MathML, so an HTML one there (foster-parented out
           of a table) turns MathML on reparse and takes its children with it */
        return element->atom != TH_TAG_MGLYPH && element->atom != TH_TAG_MALIGNMARK;
    }
    return is_html_integration_point(parent);
}

/* Rename an element to a transform's target tag and add its extra attributes when the policy maps the element's current
   name (sanitize-html's transformTags / simpleTransform). The rename happens before the allowlist and safety checks and
   re-points *tag at the target, so the renamed element is re-checked as if the author had written the target: the
   allowlist decides its disposition, is_unsafe_tag still neutralizes a target like `script`, and the added attributes
   join the element's own to be scrubbed with them, so a transform can smuggle neither a disallowed tag nor an
   unscrubbed attribute. Only HTML elements transform, matched by serialized name. Returns 1 when a transform applied, 0
   when none did, -1 on error. */
static int apply_transform(sanitizer *s, th_node *element, PyObject **tag) {
    PyObject *entry = PyDict_GetItemWithError(s->transform_tags, *tag);
    if (entry == NULL) {
        return PyErr_Occurred() ? -1 : 0; /* GCOVR_EXCL_BR_LINE: the str-key lookup cannot itself error */
    }
    PyObject *target = PyTuple_GET_ITEM(entry, 0);
    Py_ssize_t target_utf8_len = 0;
    const char *target_utf8 = PyUnicode_AsUTF8AndSize(target, &target_utf8_len);
    if (target_utf8 == NULL) { /* GCOVR_EXCL_BR_LINE: the target is a non-empty str validated at setup */
        return -1;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    char *canonical_target = html_name_lower(target_utf8, target_utf8_len);
    if (canonical_target == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *target_name = PyUnicode_FromStringAndSize(canonical_target, target_utf8_len);
    if (target_name == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(canonical_target); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                    /* GCOVR_EXCL_LINE */
    }
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(target_name);
    if (points == NULL) {             /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(target_name);       /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(canonical_target); /* GCOVR_EXCL_LINE */
        return -1;                    /* GCOVR_EXCL_LINE */
    }
    int renamed = th_node_set_data(s->tree, element, points, PyUnicode_GET_LENGTH(target_name));
    PyMem_Free(points);
    if (renamed < 0) {                /* GCOVR_EXCL_BR_LINE: th_node_set_data only fails on allocation failure */
        Py_DECREF(target_name);       /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(canonical_target); /* GCOVR_EXCL_LINE */
        return -1;                    /* GCOVR_EXCL_LINE */
    }
    element->atom = th_tag_lookup(canonical_target, target_utf8_len);
    PyMem_Free(canonical_target);
    element->tag_flags =
        (uint8_t)(th_tag_flags(element->atom) | (element->tag_flags & (TH_ELEM_CLOSED_BY_END_TAG | TH_ELEM_IMPLIED)));
    PyObject *added_name, *added_value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(PyTuple_GET_ITEM(entry, 1), &pos, &added_name, &added_value)) {
        Py_ssize_t name_len = 0;
        const char *name_bytes = PyUnicode_AsUTF8AndSize(added_name, &name_len);
        if (name_bytes == NULL) {   /* GCOVR_EXCL_BR_LINE: attribute names are str validated at setup */
            Py_DECREF(target_name); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        char *canonical_name = html_name_lower(name_bytes, name_len);
        if (canonical_name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(target_name);   /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE */
        }
        Py_UCS4 *value_points = PyUnicode_AsUCS4Copy(added_value);
        if (value_points == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(canonical_name); /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(target_name);     /* GCOVR_EXCL_LINE */
            return -1;                  /* GCOVR_EXCL_LINE */
        }
        int status = th_node_attr_set(s->tree, element, canonical_name, name_len, value_points,
                                      PyUnicode_GET_LENGTH(added_value), 1);
        PyMem_Free(canonical_name);
        PyMem_Free(value_points);
        if (status < 0) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(target_name); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_SETREF(*tag, target_name);
    return 1;
}

/* Keep, escape, strip, or remove one element according to the policy. parent_kept is
   1 when the element's parent is itself being kept; an allowlisted foreign (SVG/MathML)
   element is kept only then, so it never outlives its namespace context (e.g. an svg
   <a> must not survive into HTML as a live anchor when its <svg> is escaped). */
enum sanitize_action { SANITIZE_DONE, SANITIZE_KEEP_CHILDREN, SANITIZE_STRIP_CHILDREN, SANITIZE_ESCAPE_CHILDREN };

/* Record a disallowed element and pick its disposition: drop the whole subtree for a content-removal tag (e.g.
   script/style, so its text never leaks), in REMOVE mode, or for foreign content under STRIP (unwrapping it would
   invite namespace confusion); otherwise strip or escape it once its children are done. Returns 0, or -1 on error. */
static int dispose_disallowed(sanitizer *s, th_node *element, PyObject *tag, enum sanitize_action *action) {
    if (record_removed(s, tag, NULL, 0) < 0) { /* GCOVR_EXCL_BR_LINE: record only fails on alloc */
        return -1;                             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int remove_content = PySet_Contains(s->remove_with_content, tag);
    if (remove_content < 0) { /* GCOVR_EXCL_BR_LINE: PySet_Contains never fails on a str key */
        return -1;            /* GCOVR_EXCL_LINE */
    }
    *action = SANITIZE_DONE;
    if (remove_content || s->on_disallowed == ON_REMOVE ||
        (s->on_disallowed == ON_STRIP && element->ns != TH_NS_HTML)) {
        th_node_remove(element);
    } else if (s->on_disallowed == ON_STRIP) {
        *action = SANITIZE_STRIP_CHILDREN;
    } else {
        *action = SANITIZE_ESCAPE_CHILDREN;
    }
    return 0;
}

static int sanitize_element(sanitizer *s, th_node *element, int parent_kept, enum sanitize_action *action) {
    int is_html = element->ns == TH_NS_HTML;
    PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, element->text, element->text_len);
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (is_html && PyDict_GET_SIZE(s->transform_tags) > 0) {
        if (apply_transform(s, element, &tag) < 0) { /* GCOVR_EXCL_BR_LINE: apply_transform only fails on allocation */
            Py_DECREF(tag);                          /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                               /* GCOVR_EXCL_LINE */
        }
    }
    /* an allowlisted element matches by its serialized name (a foreign element by
       e.g. "svg"/"foreignObject"); the unsafe-tag set still escapes scripting and
       raw-text elements in any namespace, and a foreign element also needs kept
       context so it stays inside real foreign markup. An HTML <style> is exempt from
       the unsafe-tag block: a policy that allowlists it keeps it with its body scrubbed
       against css_properties (like a `style` attribute), rather than dropping its CSS. */
    int style_element = element->atom == TH_TAG_STYLE && is_html;
    int allowed = ((is_unsafe_tag(element->atom) && !style_element) || is_unsafe_svg_animation(element))
                      ? 0
                      : PySet_Contains(s->tags, tag);
    /* USE_PROFILES: a policy enables the HTML, SVG, and MathML namespaces independently, so a whole namespace can be
       dropped regardless of the tag allowlist (an SVG-only policy keeps <svg> and drops <math>, or the reverse) */
    int ns_allowed = element->ns == TH_NS_HTML  ? s->allow_html
                     : element->ns == TH_NS_SVG ? s->allow_svg
                                                : s->allow_mathml;
    if (allowed > 0 && !ns_allowed) {
        allowed = 0;
    }
    /* CUSTOM_ELEMENT_HANDLING: a basic HTML custom element the caller's matcher admits is kept even when unlisted, and
       marked so sanitize_attributes vets its unlisted attributes with custom_attribute_check; the safety baseline still
       escapes an unsafe tag and drops event-handler and URL attributes, so the matcher decides names, never safety */
    int custom = 0;
    if (is_html && ns_allowed && !is_unsafe_tag(element->atom) && s->custom_element_check != Py_None) {
        custom = custom_element_kept(s, tag);
        if (custom < 0) { /* the matcher raised */
            Py_DECREF(tag);
            return -1;
        }
    }
    if (allowed == 0 && custom) {
        allowed = 1;
    }
    if (allowed > 0 && !is_html && !parent_kept) {
        allowed = 0;
    }
    /* defense in depth: a node whose namespace is unreachable from its parent's is a namespace-confusion mutation, not
       anything the parser produced, so drop it like any disallowed node even when the allowlist would admit its name */
    if (allowed > 0 && !namespace_reachable(element)) {
        allowed = 0;
    }
    int status = 0;
    *action = SANITIZE_DONE;
    if (allowed < 0) { /* GCOVR_EXCL_BR_LINE: PySet_Contains never fails on a str key */
        status = -1;   /* GCOVR_EXCL_LINE */
    } else if (allowed && style_element) {
        /* a kept <style> holds raw CSS, not child elements, so scrub its stylesheet body instead of walking children */
        status = sanitize_attributes(s, element, tag, custom) < 0 ? -1 : sanitize_style_body(s, element);
    } else if (allowed) {
        if (sanitize_attributes(s, element, tag, custom) < 0) {
            status = -1;
        } else {
            *action = SANITIZE_KEEP_CHILDREN;
        }
    } else {
        status = dispose_disallowed(s, element, tag, action);
    }
    Py_DECREF(tag);
    return status;
}

static int strip_text_templates(sanitizer *s, th_node *node) {
    if (node->text_len < 2) {
        return 0;
    }
    const Py_UCS4 *data = th_node_realize_text(s->tree, node);
    if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t start = template_start(data, node->text_len);
    if (start == node->text_len) {
        return 0;
    }
    Py_UCS4 *out = PyMem_Malloc((size_t)node->text_len * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t out_len = strip_template_markers(data, node->text_len, out, start);
    int status = th_node_set_data(s->tree, node, out, out_len);
    PyMem_Free(out);
    return status;
}

/* Merge the run of adjacent text siblings starting at `first` into it, then strip its template markers, so a marker
   split across the run (`{` then `{x}}`) is seen whole. Returns 0, or -1 on error. */
static int strip_text_run(sanitizer *s, th_node *first) {
    if (first->next_sibling == NULL || first->next_sibling->type != TH_NODE_TEXT) {
        return strip_text_templates(s, first);
    }
    Py_ssize_t total = 0;
    for (th_node *node = first; node != NULL && node->type == TH_NODE_TEXT; node = node->next_sibling) {
        total += node->text_len;
    }
    Py_UCS4 *joined = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
    if (joined == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t len = 0;
    for (th_node *node = first; node != NULL && node->type == TH_NODE_TEXT; node = node->next_sibling) {
        const Py_UCS4 *data = th_node_realize_text(s->tree, node);
        if (data == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            PyMem_Free(joined);                   /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                            /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < node->text_len; index++) {
            joined[len++] = data[index];
        }
    }
    int status = th_node_set_data(s->tree, first, joined, len);
    PyMem_Free(joined);
    while (first->next_sibling != NULL && first->next_sibling->type == TH_NODE_TEXT) {
        th_node_remove(first->next_sibling);
    }
    return status < 0 ? -1 : strip_text_templates(s, first); /* GCOVR_EXCL_BR_LINE: set only fails on allocation */
}

/* SAFE_FOR_TEMPLATES runs once the walk has settled the tree: removing a comment or element, or unwrapping one, joins
   text the walk saw apart (`{<!---->{x}}` becomes `{{x}}`), and escaping an element adds its tag as text. Visit every
   text run in document order, without recursion. Returns 0, or -1 on error. */
static int strip_tree_templates(sanitizer *s, th_node *root) {
    th_node *node = root->first_child;
    while (node != NULL) {
        if (node->type == TH_NODE_TEXT) {
            if (strip_text_run(s, node) < 0) { /* GCOVR_EXCL_BR_LINE: only allocation failures reach this path */
                return -1;                     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        } else if (node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
        }
        node = node == root ? NULL : node->next_sibling;
    }
    return 0;
}

/* Strip or escape an element whose children are done, moving them up in its place. Returns 0, or -1 on error. */
static int unwrap_element(sanitizer *s, th_node *element, enum sanitize_action action) {
    if (action == SANITIZE_STRIP_CHILDREN) {
        hoist_children(element);
        th_node_remove(element);
        return 0;
    }
    return escape_element(s, element);
}

/* The walk validated each child against the parent it was parsed under, but unwrapping that parent moved the children
   into the grandparent: an HTML <style> hoisted out of an escaped svg <desc> now sits directly under <svg>, where a
   reparse reads its body as markup. Re-check every element between `before` (NULL for the parent's start) and `stop`
   against its new parent and dispose of the unreachable ones like any disallowed node; an unwrapped node's own children
   land in the same range, so the scan resumes just before it and checks them in turn, without recursion. Returns 0, or
   -1 on error. */
static int settle_hoisted(sanitizer *s, th_node *parent, th_node *before, th_node *stop) {
    th_node *cursor = before == NULL ? parent->first_child : before->next_sibling;
    while (cursor != stop) {
        if (cursor->type != TH_NODE_ELEMENT || namespace_reachable(cursor)) {
            cursor = cursor->next_sibling;
            continue;
        }
        before = cursor->prev_sibling;
        PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, cursor->text, cursor->text_len);
        if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        enum sanitize_action action;
        int status = dispose_disallowed(s, cursor, tag, &action);
        Py_DECREF(tag);
        if (status == 0 && action != SANITIZE_DONE) { /* GCOVR_EXCL_BR_LINE: dispose only fails on allocation */
            status = unwrap_element(s, cursor, action);
        }
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: only allocation failures reach this path */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* the escaped start tag always precedes the unwrapped node's children, so the scan resumes after it */
        cursor = before->next_sibling;
    }
    return 0;
}

typedef struct {
    th_node *element;
    th_node *next;
    enum sanitize_action action;
    int parent_kept;
} sanitize_frame;

/* Walk descendants with an explicit stack. A frame keeps the post-order strip/escape action and the sibling that
   follows the element, so mutations cannot invalidate traversal state. */
static int sanitize_children(sanitizer *s, th_node *parent, int parent_kept) {
    sanitize_frame *frames = NULL;
    Py_ssize_t depth = 0;
    Py_ssize_t capacity = 0;
    th_node *child = parent->first_child;
    for (;;) {
        if (child == NULL) {
            if (depth == 0) {
                PyMem_Free(frames);
                return 0;
            }
            sanitize_frame frame = frames[--depth];
            if (frame.action != SANITIZE_KEEP_CHILDREN) {
                th_node *grandparent = frame.element->parent;
                th_node *before = frame.element->prev_sibling;
                /* GCOVR_EXCL_BR_START: unwrapping and settling only fail on allocation */
                if (unwrap_element(s, frame.element, frame.action) < 0 ||
                    settle_hoisted(s, grandparent, before, frame.next) < 0) {
                    PyMem_Free(frames); /* GCOVR_EXCL_LINE: allocation-failure cleanup */
                    return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                /* GCOVR_EXCL_BR_STOP */
            }
            parent_kept = frame.parent_kept;
            child = frame.next;
            continue;
        }
        th_node *next = child->next_sibling;
        if (child->type == TH_NODE_ELEMENT) {
            enum sanitize_action action;
            if (sanitize_element(s, child, parent_kept, &action) < 0) {
                PyMem_Free(frames);
                return -1;
            }
            if (action != SANITIZE_DONE) {
                if (depth == capacity) {
                    size_t grown;
                    size_t bytes;
                    int fits =
                        th_grow_cap((size_t)depth + 1, (size_t)capacity, 16, sizeof(sanitize_frame), &grown, &bytes);
                    if (!fits) {            /* GCOVR_EXCL_BR_LINE: no representable tree can overflow size_t here */
                        PyMem_Free(frames); /* GCOVR_EXCL_LINE: size-overflow path */
                        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: size-overflow path */
                        return -1;          /* GCOVR_EXCL_LINE: size-overflow path */
                    }
                    sanitize_frame *resized = PyMem_Realloc(frames, bytes);
                    if (resized == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        PyMem_Free(frames); /* GCOVR_EXCL_LINE: allocation-failure path */
                        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
                        return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    frames = resized;
                    capacity = (Py_ssize_t)grown;
                }
                frames[depth++] = (sanitize_frame){child, next, action, parent_kept};
                parent_kept = action == SANITIZE_KEEP_CHILDREN;
                child = child->first_child;
                continue;
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

/* A private tree keeps callbacks from changing the caller's source. */
/* A fresh dict holding a mapping's items, the dict(mapping) copy the walk indexes. */
static PyObject *policy_dict(PyObject *mapping) {
    return PyObject_CallOneArg((PyObject *)&PyDict_Type, mapping);
}

/* The rel value add_link_rel asks for: its tokens sorted and space-joined, or None when there are none. */
static PyObject *policy_link_rel(PyObject *tokens) {
    PyObject *sorted_tokens = PySequence_List(tokens);
    if (sorted_tokens == NULL || PyList_Sort(sorted_tokens) < 0) {
        Py_XDECREF(sorted_tokens);
        return NULL;
    }
    if (PyList_GET_SIZE(sorted_tokens) == 0) {
        Py_DECREF(sorted_tokens);
        Py_RETURN_NONE;
    }
    PyObject *space = PyUnicode_FromString(" ");
    if (space == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(sorted_tokens); /* GCOVR_EXCL_LINE */
        return NULL;              /* GCOVR_EXCL_LINE */
    }
    PyObject *joined = PyUnicode_Join(space, sorted_tokens);
    Py_DECREF(space);
    Py_DECREF(sorted_tokens);
    return joined;
}

/* Rebuild a two-level mapping {tag: {name: value}} with `inner` applied to each leaf value. */
static PyObject *policy_nested(PyObject *mapping, PyObject *(*inner)(PyObject *value, module_state *state),
                               module_state *state) {
    PyObject *items = PyMapping_Items(mapping);
    if (items == NULL) {
        return NULL;
    }
    PyObject *out = PyDict_New();
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(items); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(items); index++) {
        PyObject *pair = PyList_GET_ITEM(items, index);
        PyObject *leaves = PyMapping_Items(PyTuple_GET_ITEM(pair, 1));
        PyObject *rebuilt = leaves == NULL ? NULL : PyDict_New();
        int failed = rebuilt == NULL;
        for (Py_ssize_t leaf = 0; !failed && leaf < PyList_GET_SIZE(leaves); leaf++) {
            PyObject *entry = PyList_GET_ITEM(leaves, leaf);
            PyObject *value = inner(PyTuple_GET_ITEM(entry, 1), state);
            if (value == NULL) {
                failed = 1;
                break;
            }
            failed = PyDict_SetItem(rebuilt, PyTuple_GET_ITEM(entry, 0), value) < 0; /* GCOVR_EXCL_BR_LINE: alloc */
            Py_DECREF(value);
        }
        if (!failed && PyDict_SetItem(out, PyTuple_GET_ITEM(pair, 0), rebuilt) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            failed = 1;                                                               /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: llvm attributes the unexecuted fall-through to this brace */
        Py_XDECREF(leaves);
        Py_XDECREF(rebuilt);
        if (failed) {
            Py_DECREF(out);
            Py_DECREF(items);
            return NULL;
        }
    }
    Py_DECREF(items);
    return out;
}

static PyObject *policy_str_leaf(PyObject *value, module_state *Py_UNUSED(state)) {
    return Py_NewRef(value);
}

static PyObject *policy_frozenset_leaf(PyObject *value, module_state *Py_UNUSED(state)) {
    return PyFrozenSet_New(value);
}

/* The compiled patterns an allowed_styles property lists: a compiled pattern is kept as is, a str is compiled. The
   tuple is built fresh rather than patched in place: a tuple handed back by the sequence conversion is not a fresh
   allocation on every interpreter, and only a fresh one takes PyTuple_SET_ITEM. */
static PyObject *policy_patterns_leaf(PyObject *value, module_state *state) {
    PyObject *listed = PySequence_Tuple(value);
    if (listed == NULL) {
        return NULL;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(listed);
    PyObject *patterns = PyTuple_New(count);
    if (patterns == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(listed);  /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *pattern = PyTuple_GET_ITEM(listed, index);
        PyObject *compiled = PyObject_TypeCheck(pattern, (PyTypeObject *)state->pattern_type)
                                 ? Py_NewRef(pattern)
                                 : PyObject_CallOneArg(state->re_compile, pattern);
        if (compiled == NULL) {
            Py_DECREF(listed);
            Py_DECREF(patterns);
            return NULL;
        }
        PyTuple_SET_ITEM(patterns, index, compiled);
    }
    Py_DECREF(listed);
    return patterns;
}

/* allowed_styles with each property name lowercased, the spelling the walk looks a declaration up by. */
static PyObject *policy_styles(PyObject *mapping, module_state *state) {
    PyObject *nested = policy_nested(mapping, policy_patterns_leaf, state);
    if (nested == NULL) {
        return NULL;
    }
    PyObject *out = PyDict_New();
    if (out == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(nested); /* GCOVR_EXCL_LINE */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t position = 0;
    PyObject *tag, *props;
    int failed = 0;
    while (!failed && PyDict_Next(nested, &position, &tag, &props)) {
        PyObject *lowered = PyDict_New();
        if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;        /* GCOVR_EXCL_LINE */
            break;             /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t inner = 0;
        PyObject *prop, *patterns;
        while (PyDict_Next(props, &inner, &prop, &patterns)) {
            PyObject *name = PyObject_CallMethod(prop, "lower", NULL);
            if (name == NULL) {
                failed = 1; /* a property name that is not a str */
                break;
            }
            failed = PyDict_SetItem(lowered, name, patterns) < 0; /* GCOVR_EXCL_BR_LINE: a str key only fails on OOM */
            Py_DECREF(name);
        }
        if (!failed && PyDict_SetItem(out, tag, lowered) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            failed = 1;                                         /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: llvm attributes the unexecuted fall-through to this brace */
        Py_XDECREF(lowered);
    }
    Py_DECREF(nested);
    if (failed) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

/* transform_tags normalized to {source: (target_tag, added_attributes)}: a bare str renames, a Transform renames and
   adds attributes, anything else is a TypeError and an empty target tag a ValueError. */
static PyObject *policy_transforms(PyObject *mapping, PyObject *transform_type) {
    PyObject *items = PyMapping_Items(mapping);
    if (items == NULL) {
        return NULL;
    }
    PyObject *out = PyDict_New();
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(items); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(items); index++) {
        PyObject *source = PyTuple_GET_ITEM(PyList_GET_ITEM(items, index), 0);
        PyObject *target = PyTuple_GET_ITEM(PyList_GET_ITEM(items, index), 1);
        PyObject *name;
        PyObject *attributes;
        if (PyUnicode_Check(target)) {
            name = Py_NewRef(target);
            attributes = PyDict_New();
        } else if (PyObject_IsInstance(target, transform_type) == 1) {
            name = PyObject_GetAttrString(target, "tag");
            PyObject *added = name == NULL ? NULL : PyObject_GetAttrString(target, "attributes");
            attributes = added == NULL ? NULL : policy_dict(added);
            Py_XDECREF(added);
        } else {
            PyErr_Format(PyExc_TypeError, "transform_tags[%R] must be a str or Transform, got %s", source,
                         Py_TYPE(target)->tp_name);
            name = NULL;
            attributes = NULL;
        }
        int failed = name == NULL || attributes == NULL; /* GCOVR_EXCL_BR_LINE: a Transform's fields always read */
        if (!failed && (!PyUnicode_Check(name) || PyUnicode_GET_LENGTH(name) == 0)) {
            PyErr_Format(PyExc_ValueError, "transform_tags[%R] target tag must be a non-empty string", source);
            failed = 1;
        }
        PyObject *rule = failed ? NULL : PyTuple_Pack(2, name, attributes);
        failed = rule == NULL || PyDict_SetItem(out, source, rule) < 0; /* GCOVR_EXCL_BR_LINE: allocation */
        Py_XDECREF(rule);
        Py_XDECREF(name);
        Py_XDECREF(attributes);
        if (failed) {
            Py_DECREF(out);
            Py_DECREF(items);
            return NULL;
        }
    }
    Py_DECREF(items);
    return out;
}

/* _sanitize_policy(attributes, add_link_rel, set_attributes, attribute_values, allowed_styles, transform_tags,
   transform_type) -> (attributes, link_rel, set_attributes, attribute_values, allowed_styles, transform_tags): the
   forms of a Policy the walk indexes, compiled once per Sanitizer. */
PyObject *turbohtml_sanitize_policy(PyObject *module, PyObject *args) {
    PyObject *attributes, *add_link_rel, *set_attributes, *attribute_values, *allowed_styles, *transform_tags,
        *transform_type;
    if (!PyArg_ParseTuple(args, "OOOOOOO:_sanitize_policy", &attributes, &add_link_rel, &set_attributes,
                          &attribute_values, &allowed_styles, &transform_tags, &transform_type)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *parts[6] = {policy_dict(attributes), NULL, NULL, NULL, NULL, NULL};
    if (parts[0] != NULL) {
        parts[1] = policy_link_rel(add_link_rel);
    }
    if (parts[1] != NULL) {
        parts[2] = policy_nested(set_attributes, policy_str_leaf, state);
    }
    if (parts[2] != NULL) {
        parts[3] = policy_nested(attribute_values, policy_frozenset_leaf, state);
    }
    if (parts[3] != NULL) {
        parts[4] = policy_styles(allowed_styles, state);
    }
    if (parts[4] != NULL) {
        parts[5] = policy_transforms(transform_tags, transform_type);
    }
    PyObject *compiled =
        parts[5] == NULL ? NULL : PyTuple_Pack(6, parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]);
    for (size_t index = 0; index < 6; index++) {
        Py_XDECREF(parts[index]);
    }
    return compiled;
}

static int bleach_rule_keeps(PyObject *rule, PyObject *tag, PyObject *name, PyObject *value);

PyObject *turbohtml_bleach_allow_relative(PyObject *Py_UNUSED(module), PyObject *schemes) {
    PyObject *http = PyUnicode_FromString("http");
    if (http == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    int allowed = PySet_Contains(schemes, http);
    Py_DECREF(http);
    if (allowed != 0) {
        return allowed < 0 ? NULL : Py_NewRef(Py_True); /* GCOVR_EXCL_BR_LINE: frozenset lookup of a str cannot fail */
    }
    PyObject *https = PyUnicode_FromString("https");
    if (https == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    allowed = PySet_Contains(schemes, https);
    Py_DECREF(https);
    return allowed < 0 ? NULL                      /* GCOVR_EXCL_BR_LINE: frozenset lookup of a str cannot fail */
                       : PyBool_FromLong(allowed); /* GCOVR_EXCL_BR_LINE: frozenset lookup of a str cannot fail */
}

static PyObject *bleach_predicate(PyObject *bound, PyObject *args) {
    PyObject *tag, *name, *value;
    if (!PyArg_ParseTuple(args, "OOO:bleach_attribute_predicate", &tag, &name, &value)) {
        return NULL;
    }
    for (int index = 0; index < 2; index++) {
        PyObject *key = index == 0 ? tag : PyTuple_GET_ITEM(bound, 1);
        int present = PySequence_Contains(PyTuple_GET_ITEM(bound, 0), key);
        if (present < 0) {
            return NULL;
        }
        if (!present) {
            continue;
        }
        PyObject *rule = PyObject_GetItem(PyTuple_GET_ITEM(bound, 0), key);
        if (rule == NULL) {
            return NULL;
        }
        int terminal = PyCallable_Check(rule);
        int keep = terminal ? bleach_rule_keeps(rule, tag, name, value) : PySequence_Contains(rule, name);
        Py_DECREF(rule);
        if (keep != 0 || terminal) {
            return keep < 0 ? NULL : PyBool_FromLong(keep);
        }
    }
    Py_RETURN_FALSE;
}

static int bleach_rule_keeps(PyObject *rule, PyObject *tag, PyObject *name, PyObject *value) {
    PyObject *verdict = PyObject_CallFunctionObjArgs(rule, tag, name, value, NULL);
    if (verdict == NULL) {
        return -1;
    }
    int keep = PyObject_IsTrue(verdict);
    Py_DECREF(verdict);
    return keep;
}

static PyMethodDef BLEACH_PREDICATE_DEF = {"bleach_attribute_predicate", bleach_predicate, METH_VARARGS, NULL};

static int bleach_predicate_is_bound(PyObject *predicate) {
    if (!PyCFunction_Check(predicate)) {
        return 0;
    }
    PyCFunction function = PyCFunction_GetFunction(predicate);
    if (function == NULL) { /* GCOVR_EXCL_BR_LINE: PyPy native builtins */
        /* PyPy reports native builtins as PyCFunction but rejects them here. */
        if (PyErr_ExceptionMatches(PyExc_TypeError) ||   /* GCOVR_EXCL_LINE: PyPy native builtins */
            PyErr_ExceptionMatches(PyExc_SystemError)) { /* GCOVR_EXCL_LINE: PyPy native builtins */
            PyErr_Clear();                               /* GCOVR_EXCL_LINE */
            return 0;                                    /* GCOVR_EXCL_LINE */
        }
        return -1; /* GCOVR_EXCL_LINE: unexpected C API error */
    }
    return function == bleach_predicate;
}

static PyObject *bleach_wildcard(void) {
    PyObject *seed = Py_BuildValue("(s)", "*");
    if (seed == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    PyObject *every = PyFrozenSet_New(seed);
    Py_DECREF(seed);
    return every;
}

static int bleach_static_rule(PyObject *value) {
    return PyList_CheckExact(value) || PyTuple_CheckExact(value) || PySet_CheckExact(value) ||
           PyFrozenSet_CheckExact(value);
}

static int bleach_tag_entry(PyObject *names, PyObject *rules, PyObject *tag, PyObject *value, int *needs_predicate) {
    PyObject *listed;
    if (!bleach_static_rule(value)) {
        if (!PyCallable_Check(value) && Py_TYPE(value)->tp_iter == NULL && !PySequence_Check(value)) {
            PyErr_SetString(PyExc_TypeError, "attribute rules must be callable or iterable");
            return -1;
        }
        *needs_predicate = 1;
        listed = bleach_wildcard();
    } else {
        listed = PyFrozenSet_New(value);
        value = listed;
    }
    if (listed == NULL) {
        return -1;
    }
    if (!*needs_predicate) {
        PyObject *star = PyUnicode_FromString("*");
        if (star == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(listed); /* GCOVR_EXCL_LINE */
            return -1;         /* GCOVR_EXCL_LINE */
        }
        /* Bleach treats an attribute named "*" literally; Policy uses it as a wildcard. */
        *needs_predicate = PySet_Contains(listed, star);
        Py_DECREF(star);
    }
    int stored = PyDict_SetItem(names, tag, listed);
    if (stored == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        stored = PyDict_SetItem(rules, tag, value);
    }
    Py_DECREF(listed);
    return stored; /* GCOVR_EXCL_BR_LINE: a dict insert only fails on allocation failure */
}

PyObject *turbohtml_bleach_attributes(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *attributes, *mapping_type;
    if (!PyArg_ParseTuple(args, "OO:_bleach_attributes", &attributes, &mapping_type)) {
        return NULL;
    }
    PyObject *names = PyDict_New();
    PyObject *rules = PyDict_New();
    PyObject *star = PyUnicode_FromString("*");
    PyObject *result = NULL;
    if (names == NULL || rules == NULL || star == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        goto done;                                        /* GCOVR_EXCL_LINE */
    }
    int failed;
    int needs_predicate = 0;
    PyObject *predicate_rules = rules;
    if (PyCallable_Check(attributes)) {
        failed = bleach_tag_entry(names, rules, star, attributes, &needs_predicate) < 0;
    } else {
        int is_mapping = PyObject_IsInstance(attributes, mapping_type);
        if (is_mapping < 0) {
            goto done;
        }
        if (is_mapping && !PyDict_CheckExact(attributes)) {
            predicate_rules = attributes;
            needs_predicate = 1;
            failed = 0;
        } else if (is_mapping) {
            PyObject *items = PyDict_Items(attributes);
            if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                goto done;       /* GCOVR_EXCL_LINE */
            }
            failed = 0;
            for (Py_ssize_t index = 0; index < PyList_GET_SIZE(items); index++) {
                PyObject *rule = PyTuple_GET_ITEM(PyList_GET_ITEM(items, index), 1);
                if (!bleach_static_rule(rule)) {
                    predicate_rules = attributes;
                    needs_predicate = 1;
                    break;
                }
            }
            if (predicate_rules == rules) {
                for (Py_ssize_t index = 0; !failed && index < PyList_GET_SIZE(items); index++) {
                    PyObject *pair = PyList_GET_ITEM(items, index);
                    failed = bleach_tag_entry(names, rules, PyTuple_GET_ITEM(pair, 0), PyTuple_GET_ITEM(pair, 1),
                                              &needs_predicate) < 0;
                }
            }
            Py_DECREF(items);
        } else {
            failed = bleach_tag_entry(names, rules, star, attributes, &needs_predicate) < 0;
        }
    }
    if (failed) {
        goto done;
    }
    if (predicate_rules != rules) {
        PyDict_Clear(names);
        PyObject *wildcard = bleach_wildcard();
        if (wildcard == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto done;          /* GCOVR_EXCL_LINE */
        }
        int stored = PyDict_SetItem(names, star, wildcard);
        Py_DECREF(wildcard);
        if (stored < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto done;    /* GCOVR_EXCL_LINE */
        }
    }
    PyObject *bound = needs_predicate ? PyTuple_Pack(2, predicate_rules, star) : Py_NewRef(Py_None);
    if (bound == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto done;       /* GCOVR_EXCL_LINE */
    }
    PyObject *predicate = needs_predicate ? PyCFunction_New(&BLEACH_PREDICATE_DEF, bound) : Py_NewRef(Py_None);
    Py_DECREF(bound);
    if (predicate == NULL) { /* GCOVR_EXCL_BR_LINE: the bound function only fails on allocation failure */
        goto done;           /* GCOVR_EXCL_LINE */
    }
    result = PyTuple_Pack(2, names, predicate);
    Py_DECREF(predicate);
done:
    Py_XDECREF(names);
    Py_XDECREF(rules);
    Py_XDECREF(star);
    return result;
}

TH_NODE_API(, PyObject *, turbohtml_sanitize, (PyObject * module, PyObject *args), (module, args),
            (PyObject * module, PyObject *args), node_argument(PyModule_GetState(module), args, NULL, 0, NULL), NULL) {
    PyObject *source;
    PyObject *removed = NULL;
    sanitizer s = {0};
    if (!PyArg_ParseTuple(args, "OOOOppipOOOOOOOOpOOOpOOppppOp:_sanitize", &source, &s.tags, &s.attributes,
                          &s.url_schemes, &s.allow_relative, &s.allow_fragments, &s.on_disallowed, &s.strip_comments,
                          &s.add_link_rel, &s.attribute_filter, &s.set_attributes, &s.remove_with_content,
                          &s.css_properties, &s.attribute_prefixes, &s.attribute_values, &s.media_hosts,
                          &s.strip_templates, &removed, &s.allowed_styles, &s.transform_tags, &s.isolate_named_props,
                          &s.custom_element_check, &s.custom_attribute_check, &s.allow_customized_builtins,
                          &s.allow_html, &s.allow_svg, &s.allow_mathml, &s.attribute_predicate, &s.bleach_url_policy)) {
        return NULL;
    }
    s.removed = removed == Py_None ? NULL : removed;
    s.bleach_raw_values = bleach_predicate_is_bound(s.attribute_predicate);
    if (s.bleach_raw_values < 0) { /* GCOVR_EXCL_BR_LINE: unexpected C API error */
        return NULL;               /* GCOVR_EXCL_LINE */
    }
    if (require_anyset(s.tags, "tags") < 0 || require_anyset(s.url_schemes, "url_schemes") < 0 ||
        require_anyset(s.remove_with_content, "remove_with_content") < 0 ||
        require_anyset(s.css_properties, "css_properties") < 0 ||
        require_anyset(s.attribute_prefixes, "attribute_prefixes") < 0 ||
        require_anyset(s.media_hosts, "media_hosts") < 0 || require_prefixes(s.attribute_prefixes) < 0) {
        return NULL;
    }
    th_node *root;
    PyObject *retained_source = NULL;
    if (PyUnicode_Check(source)) {
        s.bleach_raw_urls =
            s.bleach_url_policy && PyUnicode_FindChar(source, '&', 0, PyUnicode_GET_LENGTH(source), 1) >= 0;
        s.tree = th_tree_parse_fragment(PyUnicode_KIND(source), PyUnicode_DATA(source), PyUnicode_GET_LENGTH(source),
                                        "div", 3, 0, s.bleach_raw_values || s.bleach_raw_urls, 0, 0);
        if (s.tree == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        root = th_tree_document(s.tree);
        retained_source = source;
    } else {
        th_tree *source_tree;
        th_node *source_root;
        if (turbohtml_node_borrow(module, source, &source_tree, &source_root) < 0) { /* GCOVR_EXCL_BR_LINE: the typed
                                                                                     facade passes str or Element */
            return NULL; /* GCOVR_EXCL_LINE: private-call type error */
        }
        s.tree = th_tree_new();
        if (s.tree == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_tree_set_xml(s.tree, th_tree_is_xml(source_tree));
        Py_BEGIN_CRITICAL_SECTION(turbohtml_node_handle(source));
        root = th_tree_copy_node(s.tree, source_tree, source_root);
        Py_END_CRITICAL_SECTION();
    }
    if (root == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(s.tree);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    s.star = PyUnicode_InternFromString("*");
    if (s.star == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(s.tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;          /* GCOVR_EXCL_LINE */
    }
    s.re_search = PyUnicode_InternFromString("search"); /* interned once; the style scrubber calls Pattern.search */
    if (s.re_search == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(s.star);     /* GCOVR_EXCL_LINE */
        th_tree_free(s.tree);  /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    s.wildcard_attrs = PyDict_GetItemWithError(s.attributes, s.star);
    if (s.wildcard_attrs == NULL && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the "*" lookup cannot itself error */
        Py_DECREF(s.star);                              /* GCOVR_EXCL_LINE */
        Py_DECREF(s.re_search);                         /* GCOVR_EXCL_LINE */
        th_tree_free(s.tree);                           /* GCOVR_EXCL_LINE */
        return NULL;                                    /* GCOVR_EXCL_LINE */
    }
    int failed = 0;
    if ((s.bleach_raw_values || s.bleach_raw_urls) &&
        retained_source != NULL) {                     /* GCOVR_EXCL_BR_LINE: raw checks require source text */
        failed = collect_bleach_origins(&s, root) < 0; /* GCOVR_EXCL_BR_LINE: origin-map allocation failure */
    }
    if (!failed) { /* GCOVR_EXCL_BR_LINE: only origin-map allocation failure skips the walk */
        failed = sanitize_children(&s, root, 1) < 0; /* the fragment root is kept context */
    }
    if (!failed && s.strip_templates) {
        failed = strip_tree_templates(&s, root) < 0;
    }
    Py_XDECREF(s.prefix_tuple);
    PyMem_Free(s.bleach_origins);
    Py_DECREF(s.star);
    Py_DECREF(s.re_search);
    if (failed) {
        th_tree_free(s.tree);
        return NULL;
    }
    if (retained_source == NULL) {
        return wrap_fresh_tree_node(PyModule_GetState(module), s.tree, root);
    }
    module_state *state = PyModule_GetState(module);
    PyObject *handle = handle_new(state, s.tree, retained_source, Py_None, 0);
    if (handle == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(s.tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;          /* GCOVR_EXCL_LINE */
    }
    PyObject *wrapped = node_wrap(state, handle, root);
    Py_DECREF(handle);
    return wrapped;
}
