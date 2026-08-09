/* URL and email detection plus linkification.

   C snapshots eligible nodes, scans text runs, and applies completed rewrites.
   Python runs for configured callbacks. Detection uses the trigger-then-expand
   model from the Rust `linkify` crate, not a regex:
   scan for the few bytes that can begin a link (`:` for a scheme, `@` for an
   email, `.` for a bare domain), then expand left and right from the trigger to
   the link's bounds. A bare domain counts as a link only when its last label is
   a real TLD, matched case-insensitively against the generated tld_table.h, the
   same rule bleach used. Matches are returned as (start, end, kind) spans into
   the input; the scan never allocates per match. */

#include "core/ascii.h"
#include "core/common.h"
#include "dom/tree.h"
#include "tokenizer/binding.h"
#include "url/url.h"

#include "data/tld_table.h"

#include <stdint.h>

enum th_link_kind {
    /* A bare domain (``example.com``): no scheme of its own, so the Python layer prefixes ``http://``. */
    TH_LINK_URL = 0,
    TH_LINK_EMAIL = 1,
    /* A registered scheme-less URL (``tel:+1``, ``bitcoin:1abc``): its opaque part carries the scheme. */
    TH_LINK_SCHEME = 2,
    /* A ``scheme://host`` URL: the scanner matched an explicit scheme, so the match is kept verbatim. Classifying it
       here spares the Python layer a per-match re.match against the scheme grammar. */
    TH_LINK_HAS_SCHEME = 3,
};

/* A non-ASCII Unicode White_Space code point (the ASCII ones are c <= 0x20). UTS46
   domain-to-ASCII forbids these in host labels, and they end a URL in running text,
   so they bound a host or URL the way an ASCII space does; the zero-width format
   characters (ZWSP, BOM, word joiner) are not White_Space and stay in the URL. */
static inline int is_unicode_space(Py_UCS4 c) {
    return c == 0x85 || c == 0xA0 || c == 0x1680 || (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 ||
           c == 0x202F || c == 0x205F || c == 0x3000;
}

/* A host label character: ASCII alphanumeric, underscore (an RFC 3986 unreserved
   character, valid anywhere in a reg-name host, as in ``_dmarc``/``cdn_1``), or
   any non-ASCII code point that is not Unicode whitespace, so an internationalized
   domain stays in one piece (the IRI case) but an ``&nbsp;`` ends the host. */
static inline int is_label_char(Py_UCS4 c) {
    return is_ascii_alpha(c) || is_ascii_digit(c) || c == '_' || (c >= 0x80 && !is_unicode_space(c));
}

/* Email local-part atext, per the addr-spec dot-atom plus the characters real
   addresses use; non-ASCII is allowed for internationalized local parts. */
static inline int is_local_char(Py_UCS4 c) {
    switch (c) {
    case '!':
    case '#':
    case '$':
    case '%':
    case '&':
    case '\'':
    case '*':
    case '+':
    case '-':
    case '/':
    case '=':
    case '?':
    case '^':
    case '_':
    case '`':
    case '{':
    case '|':
    case '}':
    case '~':
        return 1;
    default:
        return is_ascii_alpha(c) || is_ascii_digit(c) || (c >= 0x80 && !is_unicode_space(c));
    }
}

/* A code point that can appear in the path/query/fragment tail of a URL. The
   exclusions are the WHATWG "forbidden host" and whitespace bytes plus the few
   that end a URL in running text; brackets and parens are handled by balancing,
   not exclusion, so a Wikipedia URL keeps its trailing ``)``. */
static inline int is_url_tail_char(Py_UCS4 c) {
    if (c <= 0x20 || c == 0x7F || is_unicode_space(c)) {
        return 0;
    }
    switch (c) {
    case '"':
    case '<':
    case '>':
    case '`':
    case '|':
        return 0;
    default:
        return 1;
    }
}

/* Read one code point of any storage width. */
#define READ(index) PyUnicode_READ(kind, data, index)

/* Does the label [start, end) appear in a caller-supplied tuple of lowercased
   names (the custom TLDs or scheme-less schemes)? The candidate is already lower,
   so only the input is folded; the compare reads both strings in place and never
   allocates. A NULL tuple (the default, scan-only path) matches nothing. */
static int tuple_has_label(PyObject *names, int kind, const void *data, Py_ssize_t start, Py_ssize_t end) {
    if (names == NULL) {
        return 0;
    }
    Py_ssize_t length = end - start;
    Py_ssize_t count = PyTuple_GET_SIZE(names);
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *candidate = PyTuple_GET_ITEM(names, index);
        if (PyUnicode_GET_LENGTH(candidate) != length) {
            continue;
        }
        int candidate_kind = PyUnicode_KIND(candidate);
        const void *candidate_data = PyUnicode_DATA(candidate);
        int matched = 1;
        for (Py_ssize_t offset = 0; offset < length; offset++) {
            if (PyUnicode_READ(candidate_kind, candidate_data, offset) != lower_ascii(READ(start + offset))) {
                matched = 0;
                break;
            }
        }
        if (matched) {
            return 1;
        }
    }
    return 0;
}

/* Is the label [start, end) a known TLD? Matched case-insensitively in the
   first-byte bucket of the generated table, which includes the xn-- punycode
   TLDs, so a real xn--p1ai matches and an invented xn--whatever does not. A
   caller's extra_tlds tuple extends the table with custom TLDs (e.g. an internal
   .corp); a custom TLD whose first byte is not ASCII a-z still matches there. */
static int is_known_tld(int kind, const void *data, Py_ssize_t start, Py_ssize_t end, PyObject *extra_tlds) {
    Py_ssize_t length = end - start;
    if (length < 2) {
        return 0;
    }
    Py_UCS4 first = lower_ascii(READ(start));
    if (first < 'a' || first > 'z') {
        return tuple_has_label(extra_tlds, kind, data, start, end);
    }
    int low = th_tld_first[first];
    int high = th_tld_first[first + 1];
    while (low < high) {
        int middle = low + (high - low) / 2;
        Py_ssize_t compared = length < th_tld_table[middle].name_len ? length : th_tld_table[middle].name_len;
        int order = 0;
        for (Py_ssize_t offset = 0; offset < compared; offset++) {
            Py_UCS4 candidate = lower_ascii(READ(start + offset));
            Py_UCS4 table = (unsigned char)th_tld_table[middle].name[offset];
            if (candidate != table) {
                order = candidate < table ? -1 : 1;
                break;
            }
        }
        if (order == 0 && length != th_tld_table[middle].name_len) {
            order = length < th_tld_table[middle].name_len ? -1 : 1;
        }
        if (order < 0) {
            high = middle;
        } else if (order > 0) {
            low = middle + 1;
        } else {
            return 1;
        }
    }
    return tuple_has_label(extra_tlds, kind, data, start, end);
}

/* Scan a host of dot-separated labels starting at `start`, requiring at least
   one dot. With require_tld the final label must be a known TLD (the bare-domain
   rule); a scheme URL passes 0 so a numeric host like 1.2.3.4 is accepted. Returns
   the index past the host on success, or -1. Hyphens are allowed inside a label,
   not at its edges. */
static Py_ssize_t scan_host(int kind, const void *data, Py_ssize_t start, Py_ssize_t len, int require_tld,
                            PyObject *extra_tlds) {
    Py_ssize_t pos = start;
    Py_ssize_t last_label_start = start;
    Py_ssize_t host_end = start;
    int label_len = 0;
    int label_ended_with_hyphen = 0;
    int dots = 0;
    while (pos < len) {
        Py_UCS4 c = READ(pos);
        if (is_label_char(c)) {
            label_len++;
            label_ended_with_hyphen = 0;
            pos++;
            host_end = pos;
        } else if (c == '-') {
            if (label_len == 0) {
                break;
            }
            label_ended_with_hyphen = 1;
            pos++;
        } else if (c == '.' && pos + 1 < len && is_label_char(READ(pos + 1))) {
            if (label_len == 0 || label_ended_with_hyphen) {
                break;
            }
            dots++;
            last_label_start = pos + 1;
            label_len = 0;
            pos++;
        } else {
            break;
        }
    }
    if (dots < 1 || label_ended_with_hyphen) {
        return -1;
    }
    if (require_tld && !is_known_tld(kind, data, last_label_start, host_end, extra_tlds)) {
        return -1;
    }
    return host_end;
}

/* Consume a run of URL tail characters from `begin`, balancing brackets and
   trimming trailing punctuation so a link in prose keeps only what belongs to it.
   Returns the index of the last byte that can legally end the link. Shared by the
   scheme://host path tail and the opaque tail of a registered scheme-less URL. */
static Py_ssize_t scan_balanced(int kind, const void *data, Py_ssize_t begin, Py_ssize_t len) {
    Py_ssize_t pos = begin;
    Py_ssize_t end = begin;
    int round = 0;
    int square = 0;
    int curly = 0;
    while (pos < len) {
        Py_UCS4 c = READ(pos);
        if (!is_url_tail_char(c)) {
            break;
        }
        /* Braces balance like parens and brackets, so a template path keeps its
           ``/{id}`` but a brace-scoped ``{http://...}`` drops the stray closer. */
        if (c == '(') {
            round++;
        } else if (c == ')') {
            if (round == 0) {
                break;
            }
            round--;
        } else if (c == '[') {
            square++;
        } else if (c == ']') {
            if (square == 0) {
                break;
            }
            square--;
        } else if (c == '{') {
            curly++;
        } else if (c == '}') {
            if (curly == 0) {
                break;
            }
            curly--;
        }
        pos++;
        /* a closing bracket or a non-trailing-punctuation byte can end the link;
           trailing . , ! ? : ; and a lone ' (a single-quoted URL's closer) are
           valid inside it but never its last byte. '*' is an RFC 3986 sub-delim
           that bleach and linkify_it keep, so it ends a link. */
        if (round == 0 && square == 0 && curly == 0 && c != '.' && c != ',' && c != '!' && c != '?' && c != ':' &&
            c != ';' && c != '\'') {
            end = pos;
        }
    }
    return end;
}

/* Consume an optional ``:port`` and ``/``-or-``?``-led tail after the host,
   balancing brackets so a link in prose keeps only what belongs to it. Returns
   the index past the link. */
static Py_ssize_t scan_url_tail(int kind, const void *data, Py_ssize_t host_end, Py_ssize_t len) {
    Py_ssize_t pos = host_end;
    if (pos < len && READ(pos) == ':') {
        Py_ssize_t port = pos + 1;
        while (port < len && is_ascii_digit(READ(port))) {
            port++;
        }
        if (port > pos + 1) {
            pos = port;
        }
    }
    if (pos >= len || (READ(pos) != '/' && READ(pos) != '?' && READ(pos) != '#')) {
        return pos;
    }
    return scan_balanced(kind, data, pos, len);
}

/* True when the character before `start` blocks a link there: bleach's
   (?<![@.]) plus a word character, so an email's domain half and a mid-word run
   are not re-matched. */
static int blocked_on_left(int kind, const void *data, Py_ssize_t start) {
    if (start == 0) {
        return 0;
    }
    Py_UCS4 before = READ(start - 1);
    return before == '@' || before == '.' || is_label_char(before);
}

/* Expand left from a scheme's ``:`` over the scheme characters; returns the
   scheme start if one of the autolinked schemes is present, else -1. */
static Py_ssize_t scan_scheme_start(int kind, const void *data, Py_ssize_t colon, Py_ssize_t start) {
    Py_ssize_t pos = colon;
    while (pos > start && th_scheme_char(READ(pos - 1))) {
        pos--;
    }
    if (pos == colon || !is_ascii_alpha(READ(pos))) {
        return -1;
    }
    return pos;
}

/* Try to match a scheme:// URL whose ``:`` is at `colon`. A non-NULL url_schemes
   tuple restricts the scheme to that lowercased allowlist (http/https/ftp plus any
   registered scheme), so a typo like ``hppt://`` or a ``javascript://`` payload is
   not linked; NULL keeps the permissive, any-scheme scan the raw binding exposes. */
static int match_url(int kind, const void *data, Py_ssize_t colon, Py_ssize_t start, Py_ssize_t len,
                     Py_ssize_t *out_start, Py_ssize_t *out_end, PyObject *url_schemes) {
    if (colon + 2 >= len || READ(colon + 1) != '/' || READ(colon + 2) != '/') {
        return 0;
    }
    Py_ssize_t scheme_start = scan_scheme_start(kind, data, colon, start);
    if (scheme_start < 0 || blocked_on_left(kind, data, scheme_start)) {
        return 0;
    }
    if (url_schemes != NULL && !tuple_has_label(url_schemes, kind, data, scheme_start, colon)) {
        return 0;
    }
    /* Most URLs have no userinfo, so scan the host directly and only hunt for a
       user[:password]@ prefix when that host fails or is followed by ':' or '@';
       the common case stays a single host scan. The last '@' before the path wins,
       so http://user:pass@host links the host, not the embedded email. */
    Py_ssize_t host_start = colon + 3;
    Py_ssize_t host_end = scan_host(kind, data, host_start, len, 0, NULL);
    if (host_end < 0 || (host_end < len && (READ(host_end) == ':' || READ(host_end) == '@'))) {
        Py_ssize_t userinfo_end = -1;
        for (Py_ssize_t scan = host_start; scan < len; scan++) {
            Py_UCS4 c = READ(scan);
            if (c == '@') {
                userinfo_end = scan;
            } else if (c == '/' || c == '?' || c == '#' || !is_url_tail_char(c)) {
                break;
            }
        }
        if (userinfo_end >= 0) {
            host_start = userinfo_end + 1;
            host_end = scan_host(kind, data, host_start, len, 0, NULL);
        }
    }
    if (host_end < 0) {
        return 0;
    }
    *out_start = scheme_start;
    *out_end = scan_url_tail(kind, data, host_end, len);
    return 1;
}

/* Try to match a bare domain (no scheme) whose first label dot triggered it; the
   domain starts by expanding left over label characters from `dot`. */
static int match_domain(int kind, const void *data, Py_ssize_t dot, Py_ssize_t start, Py_ssize_t len,
                        Py_ssize_t *out_start, Py_ssize_t *out_end, PyObject *extra_tlds) {
    Py_ssize_t pos = dot;
    while (pos > start) {
        Py_UCS4 before = READ(pos - 1);
        if (is_label_char(before) || before == '-') {
            pos--;
        } else {
            break;
        }
    }
    while (pos < dot && READ(pos) == '-') { /* a label cannot start with a hyphen, so drop any the sweep pulled in */
        pos++;
    }
    if (pos == dot || blocked_on_left(kind, data, pos)) {
        return 0;
    }
    /* A ``://`` immediately left of the host is the authority of a scheme URL that
       match_url declined (an unregistered or typo scheme like ``hppt://``); its host
       is not an independent bare domain, so the whole thing stays plain text. A bare
       ``//`` with no colon (``nothttp//example.com``) is not scheme syntax and links. */
    if (pos >= 3 && READ(pos - 1) == '/' && READ(pos - 2) == '/' && READ(pos - 3) == ':') {
        return 0;
    }
    Py_ssize_t host_end = scan_host(kind, data, pos, len, 1, extra_tlds);
    if (host_end < 0) {
        return 0;
    }
    *out_start = pos;
    *out_end = scan_url_tail(kind, data, host_end, len);
    return 1;
}

/* Try to match an email whose ``@`` is at `at`; expand left over the local part
   and scan the domain to the right. */
static int match_email(int kind, const void *data, Py_ssize_t at, Py_ssize_t start, Py_ssize_t len,
                       Py_ssize_t *out_start, Py_ssize_t *out_end, PyObject *extra_tlds) {
    Py_ssize_t pos = at;
    while (pos > start) {
        Py_UCS4 before = READ(pos - 1);
        /* a dot joins the local part only between two local characters, never at its edge */
        if (is_local_char(before) || (before == '.' && pos - 1 > start && is_local_char(READ(pos - 2)))) {
            pos--;
        } else {
            break;
        }
    }
    if (pos == at || (pos > start && READ(pos - 1) == '@')) {
        return 0;
    }
    Py_ssize_t host_end = scan_host(kind, data, at + 1, len, 1, extra_tlds);
    if (host_end < 0) {
        return 0;
    }
    *out_start = pos;
    *out_end = host_end;
    return 1;
}

/* Try to match a registered scheme-less URL whose ``:`` is at `colon`: a scheme
   like ``tel`` or ``mailto`` that carries an opaque part with no ``//`` authority.
   The scheme left of the colon must be one of the caller's `schemes`; the opaque
   part is one run of URL tail characters, trimmed like a path. Returns the match
   start at the scheme and end past the opaque part. */
static int match_scheme_less(int kind, const void *data, Py_ssize_t colon, Py_ssize_t start, Py_ssize_t len,
                             Py_ssize_t *out_start, Py_ssize_t *out_end, PyObject *schemes) {
    Py_ssize_t scheme_start = scan_scheme_start(kind, data, colon, start);
    if (scheme_start < 0 || blocked_on_left(kind, data, scheme_start)) {
        return 0;
    }
    if (!tuple_has_label(schemes, kind, data, scheme_start, colon)) {
        return 0;
    }
    Py_ssize_t end = scan_balanced(kind, data, colon + 1, len);
    if (end <= colon + 1) {
        return 0;
    }
    *out_start = scheme_start;
    *out_end = end;
    return 1;
}

static int append_span(PyObject *spans, Py_ssize_t start, Py_ssize_t end, enum th_link_kind link_kind) {
    PyObject *span = Py_BuildValue("(nni)", start, end, (int)link_kind);
    if (span == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int rc = PyList_Append(spans, span);
    Py_DECREF(span);
    return rc; /* GCOVR_EXCL_BR_LINE: PyList_Append only fails on allocation failure */
}

static int scan_matches(PyObject *text, int parse_email, int bare_domains, PyObject *extra_tlds, PyObject *schemes,
                        PyObject *url_schemes, PyObject *spans) {
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    Py_ssize_t pos = 0;
    while (pos < len) {
        Py_UCS4 c = READ(pos);
        Py_ssize_t match_start = 0;
        Py_ssize_t match_end = 0;
        enum th_link_kind link_kind = TH_LINK_URL;
        int found = 0;
        if (c == ':') {
            found = match_url(kind, data, pos, 0, len, &match_start, &match_end, url_schemes);
            if (found) {
                link_kind = TH_LINK_HAS_SCHEME; /* a scheme://host URL carries its own scheme, kept verbatim */
            } else if (schemes != NULL) {
                found = match_scheme_less(kind, data, pos, 0, len, &match_start, &match_end, schemes);
                link_kind = TH_LINK_SCHEME;
            }
        } else if (c == '@' && parse_email) {
            found = match_email(kind, data, pos, 0, len, &match_start, &match_end, extra_tlds);
            link_kind = TH_LINK_EMAIL;
        } else if (c == '.' && bare_domains) {
            found = match_domain(kind, data, pos, 0, len, &match_start, &match_end, extra_tlds);
        }
        if (found) {
            if (spans == NULL) {
                return 1;
            }
            if (append_span(spans, match_start, match_end, link_kind) < 0) { /* GCOVR_EXCL_BR_LINE */
                return -1;                                                   /* GCOVR_EXCL_LINE */
            }
            pos = match_end;
        } else {
            pos++;
        }
    }
    return 0;
}

static PyObject *collect_matches(PyObject *text, int parse_email, int bare_domains, PyObject *extra_tlds,
                                 PyObject *schemes, PyObject *url_schemes) {
    PyObject *spans = PyList_New(0);
    if (spans == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int scan_status = scan_matches(text, parse_email, bare_domains, extra_tlds, schemes, url_schemes, spans);
    if (scan_status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(spans);  /* GCOVR_EXCL_LINE */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    return spans;
}

/* _linkify_scan(text, parse_email, bare_domains, extra_tlds=(), url_schemes=None)
   -> list[(start, end, kind)]. extra_tlds is a tuple of lowercased custom TLDs
   extending the built-in table for bare-domain and email host recognition.
   url_schemes, when given, is a tuple of lowercased schemes the rewrite path allows
   for scheme://host URLs; omitting it keeps the permissive any-scheme scan. */
PyObject *turbohtml_linkify_scan(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int parse_email;
    int bare_domains;
    PyObject *extra_tlds = NULL;
    PyObject *url_schemes = NULL;
    if (!PyArg_ParseTuple(args, "Upp|O!O!:_linkify_scan", &text, &parse_email, &bare_domains, &PyTuple_Type,
                          &extra_tlds, &PyTuple_Type, &url_schemes)) {
        return NULL;
    }
    return collect_matches(text, parse_email, bare_domains, extra_tlds, NULL, url_schemes);
}

/* _linkify_find(text, emails, bare_domains, extra_tlds, schemes, url_schemes=None)
   -> list[(start, end, kind)]. extra_tlds and schemes are tuples of lowercased
   names; an empty schemes tuple still enables the scheme-less path (matching
   nothing), so the detector can register zero or more schemes uniformly. url_schemes
   is the lowercased allowlist for scheme://host URLs; omitting it matches any scheme. */
PyObject *turbohtml_linkify_find(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int emails;
    int bare_domains;
    PyObject *extra_tlds;
    PyObject *schemes;
    PyObject *url_schemes = NULL;
    if (!PyArg_ParseTuple(args, "UppO!O!|O!:_linkify_find", &text, &emails, &bare_domains, &PyTuple_Type, &extra_tlds,
                          &PyTuple_Type, &schemes, &PyTuple_Type, &url_schemes)) {
        return NULL;
    }
    return collect_matches(text, emails, bare_domains, extra_tlds, schemes, url_schemes);
}

PyObject *turbohtml_linkify_has(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int emails;
    int bare_domains;
    PyObject *extra_tlds;
    PyObject *schemes;
    PyObject *url_schemes = NULL;
    if (!PyArg_ParseTuple(args, "UppO!O!|O!:_linkify_has", &text, &emails, &bare_domains, &PyTuple_Type, &extra_tlds,
                          &PyTuple_Type, &schemes, &PyTuple_Type, &url_schemes)) {
        return NULL;
    }
    return PyBool_FromLong(scan_matches(text, emails, bare_domains, extra_tlds, schemes, url_schemes, NULL));
}

static int tag_matches_str(const th_node *node, PyObject *tag) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(tag);
    if (node->text_len != len) {
        return 0;
    }
    int kind = PyUnicode_KIND(tag);
    const void *data = PyUnicode_DATA(tag);
    for (Py_ssize_t index = 0; index < len; index++) {
        if (node->text[index] != PyUnicode_READ(kind, data, index)) {
            return 0;
        }
    }
    return 1;
}

static int tag_in_tuple(const th_node *node, PyObject *tags) {
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(tags); index++) {
        if (tag_matches_str(node, PyTuple_GET_ITEM(tags, index))) {
            return 1;
        }
    }
    return 0;
}

static th_node *after_subtree(th_node *node, th_node *root) {
    while (node != root) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
    }
    return NULL;
}

static int append_target(PyObject *targets, PyObject *owner, th_node *node) {
    PyObject *wrapped = turbohtml_node_wrap_in(owner, node);
    if (wrapped == NULL || PyList_Append(targets, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: wrapper/list allocation */
        Py_XDECREF(wrapped);                                      /* GCOVR_EXCL_LINE */
        return -1;                                                /* GCOVR_EXCL_LINE */
    }
    Py_DECREF(wrapped);
    return 0;
}

static PyObject *collect_targets(PyObject *module, PyObject *owner, int process_existing, PyObject *skip_tags) {
    PyObject *targets = PyList_New(0);
    if (targets == NULL) { /* GCOVR_EXCL_BR_LINE: target list allocation cannot be forced from a test */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    int error = 0;
    PyObject *handle = turbohtml_node_handle(owner);
    (void)handle;
    Py_BEGIN_CRITICAL_SECTION(handle);
    th_tree *tree;
    th_node *root;
    (void)turbohtml_node_borrow(module, owner, &tree, &root);
    (void)tree;
    th_node *node = root->first_child;
    while (node != NULL) {
        if (node->type == TH_NODE_TEXT) {
            if (append_target(targets, owner, node) < 0) { /* GCOVR_EXCL_BR_LINE: wrapper/list allocation */
                error = 1;                                 /* GCOVR_EXCL_LINE */
                break;                                     /* GCOVR_EXCL_LINE */
            }
        } else if (node->type == TH_NODE_ELEMENT && (node->atom == TH_TAG_A || node->atom == TH_TAG_SCRIPT ||
                                                     node->atom == TH_TAG_STYLE || tag_in_tuple(node, skip_tags))) {
            if (process_existing && node->atom == TH_TAG_A) {
                if (append_target(targets, owner, node) < 0) { /* GCOVR_EXCL_BR_LINE: wrapper/list allocation */
                    error = 1;                                 /* GCOVR_EXCL_LINE */
                    break;                                     /* GCOVR_EXCL_LINE */
                }
            }
            node = after_subtree(node, root);
            continue;
        }
        node = node->first_child != NULL ? node->first_child : after_subtree(node, root);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {            /* GCOVR_EXCL_BR_LINE: target wrapper/list allocation cannot be forced from a test */
        Py_DECREF(targets); /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    return targets;
}

static PyObject *attr_text(const th_node_attr *attr) {
    return attr == NULL || attr->value == NULL
               ? PyUnicode_FromString("")
               : PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len);
}

static PyObject *anchor_attrs(th_tree *tree, th_node *anchor) {
    PyObject *attrs = PyDict_New();
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: attribute dict allocation cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < anchor->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, anchor->attrs[index].name_atom, &name_len);
        if (name_len == 4 && memcmp(name, "href", 4) == 0) {
            continue;
        }
        PyObject *key = PyUnicode_FromStringAndSize(name, name_len);
        PyObject *value = attr_text(&anchor->attrs[index]);
        if (key == NULL || value == NULL || /* GCOVR_EXCL_BR_LINE: attribute string allocation cannot be forced */
            PyDict_SetItem(attrs, key, value) < 0) { /* GCOVR_EXCL_BR_LINE: attribute dict insertion allocation */
            Py_XDECREF(key);                         /* GCOVR_EXCL_LINE */
            Py_XDECREF(value);                       /* GCOVR_EXCL_LINE */
            Py_DECREF(attrs);                        /* GCOVR_EXCL_LINE */
            return NULL;                             /* GCOVR_EXCL_LINE */
        }
        Py_DECREF(key);
        Py_DECREF(value);
    }
    return attrs;
}

static PyObject *new_candidate(PyObject *candidate_type, PyObject *url, PyObject *text, PyObject *attrs, int existing) {
    PyObject *candidate = PyObject_CallFunctionObjArgs(candidate_type, url, text, attrs, NULL);
    if (candidate != NULL && existing) { /* GCOVR_EXCL_BR_LINE: LinkCandidate allocation failure */
        if (PyObject_SetAttrString(candidate, "existing", Py_True) < 0) { /* GCOVR_EXCL_BR_LINE: fixed writable field */
            Py_CLEAR(candidate);                                          /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
    }
    return candidate;
}

static int run_callbacks(PyObject *callbacks, PyObject **candidate) {
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(callbacks); index++) {
        PyObject *result = PyObject_CallOneArg(PyTuple_GET_ITEM(callbacks, index), *candidate);
        if (result == NULL) {
            return -1;
        }
        Py_SETREF(*candidate, result);
        if (result == Py_None) {
            return 1;
        }
    }
    return 0;
}

typedef struct {
    PyObject *url;
    PyObject *text;
    PyObject *attrs;
    int vetoed;
} candidate_result;

static void clear_candidate_result(candidate_result *result) {
    Py_CLEAR(result->url);
    Py_CLEAR(result->text);
    Py_CLEAR(result->attrs);
}

static int prepare_candidate(PyObject *candidate_type, PyObject *url, PyObject *text, PyObject *attrs, int existing,
                             PyObject *callbacks, candidate_result *result) {
    PyObject *candidate = new_candidate(candidate_type, url, text, attrs, existing);
    if (candidate == NULL) { /* GCOVR_EXCL_BR_LINE: LinkCandidate allocation failure */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    int status = run_callbacks(callbacks, &candidate);
    if (status != 0) {
        Py_DECREF(candidate);
        if (status > 0) {
            result->vetoed = 1;
            return 0;
        }
        return -1;
    }
    result->url = PyObject_GetAttrString(candidate, "url");
    result->text = PyObject_GetAttrString(candidate, "text");
    PyObject *candidate_attrs = PyObject_GetAttrString(candidate, "attrs");
    Py_DECREF(candidate);
    if (result->url == NULL || result->text == NULL || candidate_attrs == NULL) {
        Py_XDECREF(candidate_attrs);
        clear_candidate_result(result);
        return -1;
    }
    if (!PyUnicode_Check(result->url) || !PyUnicode_Check(result->text) || !PyDict_Check(candidate_attrs)) {
        Py_DECREF(candidate_attrs);
        clear_candidate_result(result);
        PyErr_SetString(PyExc_TypeError, "a link callback must return string fields and a dict of attributes");
        return -1;
    }
    result->attrs = PyDict_Copy(candidate_attrs);
    Py_DECREF(candidate_attrs);
    if (result->attrs == NULL) {        /* GCOVR_EXCL_BR_LINE: attribute dict copy cannot be forced from a test */
        clear_candidate_result(result); /* GCOVR_EXCL_LINE */
        return -1;                      /* GCOVR_EXCL_LINE */
    }
    PyObject *name;
    PyObject *value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(result->attrs, &pos, &name, &value)) {
        if (!PyUnicode_Check(name) || !PyUnicode_Check(value)) {
            clear_candidate_result(result);
            PyErr_SetString(PyExc_TypeError, "link attribute names and values must be str");
            return -1;
        }
    }
    return 0;
}

static int set_candidate_attrs(th_tree *tree, th_node *anchor, PyObject *url, PyObject *attrs, int include_empty_url) {
    anchor->attr_count = 0;
    PyObject *name;
    PyObject *value;
    Py_ssize_t pos = 0;
    int status = 0;
    if (include_empty_url || PyUnicode_GET_LENGTH(url) > 0) {
        Py_UCS4 *url_points = PyUnicode_AsUCS4Copy(url);
        if (url_points == NULL) { /* GCOVR_EXCL_BR_LINE: URL UCS4 allocation cannot be forced from a test */
            return -1;            /* GCOVR_EXCL_LINE */
        }
        status = th_node_attr_set(tree, anchor, "href", 4, url_points, PyUnicode_GET_LENGTH(url), 1);
        PyMem_Free(url_points);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: attribute arena allocation cannot be forced from a test */
            return -1;    /* GCOVR_EXCL_LINE */
        }
    }
    while (PyDict_Next(attrs, &pos, &name, &value)) {
        Py_ssize_t name_len;
        const char *name_bytes = PyUnicode_AsUTF8AndSize(name, &name_len);
        Py_UCS4 *value_points = PyUnicode_AsUCS4Copy(value);
        if (name_bytes == NULL || value_points == NULL) { /* GCOVR_EXCL_BR_LINE: UTF-8/UCS4 allocation */
            PyMem_Free(value_points);                     /* GCOVR_EXCL_LINE */
            return -1;                                    /* GCOVR_EXCL_LINE */
        }
        status = th_node_attr_set(tree, anchor, name_bytes, name_len, value_points, PyUnicode_GET_LENGTH(value), 1);
        PyMem_Free(value_points);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: attribute arena allocation cannot be forced from a test */
            return -1;    /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static int replace_anchor_text(th_tree *tree, th_node *anchor, PyObject *text) {
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(text);
    if (points == NULL) { /* GCOVR_EXCL_BR_LINE: text UCS4 allocation cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    while (anchor->first_child != NULL) {
        th_node_remove(anchor->first_child);
    }
    Py_ssize_t text_len = PyUnicode_GET_LENGTH(text);
    th_node *child = text_len > 0 ? th_tree_make_data_node(tree, TH_NODE_TEXT, points, text_len) : NULL;
    PyMem_Free(points);
    if (text_len > 0 && child == NULL) { /* GCOVR_EXCL_BR_LINE: text-node arena allocation */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    if (child != NULL) {
        th_node_append_child(anchor, child);
    }
    return 0;
}

static void unwrap_anchor(th_node *anchor) {
    th_node *parent = anchor->parent;
    while (anchor->first_child != NULL) {
        th_node *child = anchor->first_child;
        th_node_remove(child);
        th_node_insert_before(parent, child, anchor);
    }
    th_node_remove(anchor);
}

static int snapshot_existing(PyObject *module, PyObject *target, PyObject **text_out, PyObject **url_out,
                             PyObject **attrs_out) {
    PyObject *handle = turbohtml_node_handle(target);
    (void)handle;
    th_tree *tree;
    th_node *anchor;
    PyObject *text;
    PyObject *url;
    PyObject *attrs;
    Py_BEGIN_CRITICAL_SECTION(handle);
    (void)turbohtml_node_borrow(module, target, &tree, &anchor);
    Py_ssize_t text_len;
    Py_UCS4 *points = th_node_text(tree, anchor, &text_len);
    if (text_len == 0) {
        text = PyUnicode_FromString("");
    } else if (points == NULL) { /* GCOVR_EXCL_BR_LINE: flattened-text allocation */
        text = NULL;             /* GCOVR_EXCL_LINE */
    } else {                     /* GCOVR_EXCL_LINE: llvm-cov assigns this line to the allocation-failure edge */
        text = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, points, text_len);
    }
    PyMem_Free(points);
    Py_ssize_t href_index = th_node_attr_find(tree, anchor, "href", 4);
    url = attr_text(href_index < 0 ? NULL : &anchor->attrs[href_index]);
    attrs = anchor_attrs(tree, anchor);
    Py_END_CRITICAL_SECTION();
    if (text == NULL || url == NULL || attrs == NULL) { /* GCOVR_EXCL_BR_LINE: text/URL/attribute snapshot allocation */
        Py_XDECREF(text);                               /* GCOVR_EXCL_LINE */
        Py_XDECREF(url);                                /* GCOVR_EXCL_LINE */
        Py_XDECREF(attrs);                              /* GCOVR_EXCL_LINE */
        return -1;                                      /* GCOVR_EXCL_LINE */
    }
    *text_out = text;
    *url_out = url;
    *attrs_out = attrs;
    return 0;
}

static int apply_existing(PyObject *module, PyObject *target, const candidate_result *result, int replace_text) {
    PyObject *handle = turbohtml_node_handle(target);
    (void)handle;
    th_tree *tree;
    th_node *anchor;
    int status = 0;
    Py_BEGIN_CRITICAL_SECTION(handle);
    (void)turbohtml_node_borrow(module, target, &tree, &anchor);
    if (result->vetoed) {
        unwrap_anchor(anchor);
    } else {
        status = set_candidate_attrs(tree, anchor, result->url, result->attrs, 0);
        if (status == 0 && replace_text) { /* GCOVR_EXCL_BR_LINE: attribute encoding/arena allocation */
            status = replace_anchor_text(tree, anchor, result->text);
        }
    }
    Py_END_CRITICAL_SECTION();
    return status;
}

static int process_existing(PyObject *module, PyObject *target, PyObject *callbacks, PyObject *candidate_type) {
    PyObject *text;
    PyObject *url;
    PyObject *attrs;
    if (snapshot_existing(module, target, &text, &url, &attrs) < 0) { /* GCOVR_EXCL_BR_LINE: snapshot allocation */
        return -1;                                                    /* GCOVR_EXCL_LINE */
    }
    candidate_result result = {NULL, NULL, NULL, 0};
    int status = prepare_candidate(candidate_type, url, text, attrs, 1, callbacks, &result);
    Py_DECREF(url);
    Py_DECREF(attrs);
    if (status == 0) {
        int replace_text = 0;
        if (!result.vetoed) {
            int equal = PyObject_RichCompareBool(text, result.text, Py_EQ);
            if (equal < 0) {
                status = -1;
            } else {
                replace_text = equal == 0;
            }
        }
        if (status == 0) {
            status = apply_existing(module, target, &result, replace_text);
        }
    }
    clear_candidate_result(&result);
    Py_DECREF(text);
    return status;
}

static PyObject *matched_url(PyObject *matched, int kind) {
    if (kind == TH_LINK_EMAIL) {
        PyObject *prefix = PyUnicode_FromString("mailto:");
        /* GCOVR_EXCL_BR_START: prefix/concatenation allocation cannot be forced */
        PyObject *url = prefix == NULL ? NULL : PyUnicode_Concat(prefix, matched);
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(prefix);
        return url;
    }
    if (kind == TH_LINK_URL) {
        PyObject *prefix = PyUnicode_FromString("http://");
        /* GCOVR_EXCL_BR_START: prefix/concatenation allocation cannot be forced */
        PyObject *url = prefix == NULL ? NULL : PyUnicode_Concat(prefix, matched);
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(prefix);
        return url;
    }
    return Py_NewRef(matched);
}

static int append_data_slice(th_tree *tree, th_node *fragment, const Py_UCS4 *points, Py_ssize_t start,
                             Py_ssize_t end) {
    if (end <= start) {
        return 0;
    }
    th_node *text = th_tree_make_data_node(tree, TH_NODE_TEXT, points + start, end - start);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: text-node arena allocation cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    th_node_append_child(fragment, text);
    return 0;
}

static int prepare_detected(PyObject *matched, int kind, PyObject *callbacks, PyObject *candidate_type,
                            candidate_result *result) {
    PyObject *url = matched_url(matched, kind);
    PyObject *attrs = PyDict_New();
    if (url == NULL || attrs == NULL) { /* GCOVR_EXCL_BR_LINE: URL/dict allocation cannot be forced from a test */
        Py_XDECREF(url);                /* GCOVR_EXCL_LINE */
        Py_XDECREF(attrs);              /* GCOVR_EXCL_LINE */
        return -1;                      /* GCOVR_EXCL_LINE */
    }
    int status = prepare_candidate(candidate_type, url, matched, attrs, 0, callbacks, result);
    Py_DECREF(url);
    Py_DECREF(attrs);
    return status;
}

static int append_result(th_tree *tree, th_node *fragment, const Py_UCS4 *points, Py_ssize_t start, Py_ssize_t end,
                         const candidate_result *result) {
    if (result->vetoed) {
        return append_data_slice(tree, fragment, points, start, end);
    }
    static const Py_UCS4 anchor_tag[] = {'a'};
    th_node *anchor = th_tree_make_element(tree, anchor_tag, 1, TH_TAG_A, 0);
    /* GCOVR_EXCL_BR_START: anchor arena allocation cannot be forced */
    int status = anchor == NULL ? -1 : set_candidate_attrs(tree, anchor, result->url, result->attrs, 1);
    /* GCOVR_EXCL_BR_STOP */
    if (status == 0) { /* GCOVR_EXCL_BR_LINE: attribute encoding/arena allocation */
        status = replace_anchor_text(tree, anchor, result->text);
    }
    if (status == 0) { /* GCOVR_EXCL_BR_LINE: text encoding/arena allocation */
        th_node_append_child(fragment, anchor);
    }
    return status;
}

static PyObject *snapshot_text(PyObject *module, PyObject *target) {
    PyObject *handle = turbohtml_node_handle(target);
    (void)handle;
    th_tree *tree;
    th_node *node;
    PyObject *text;
    Py_BEGIN_CRITICAL_SECTION(handle);
    (void)turbohtml_node_borrow(module, target, &tree, &node);
    const Py_UCS4 *points = th_node_realize_text(tree, node);
    /* GCOVR_EXCL_BR_START: text realization and Unicode snapshot allocation cannot be forced */
    text = points == NULL ? NULL : PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, points, node->text_len);
    /* GCOVR_EXCL_BR_STOP */
    Py_END_CRITICAL_SECTION();
    return text;
}

static int apply_text(PyObject *module, PyObject *target, PyObject *text, PyObject *spans,
                      const candidate_result *results) {
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(text);
    if (points == NULL) { /* GCOVR_EXCL_BR_LINE: UCS4 input copy cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    PyObject *handle = turbohtml_node_handle(target);
    (void)handle;
    th_tree *tree;
    th_node *node;
    int status = 0;
    Py_BEGIN_CRITICAL_SECTION(handle);
    (void)turbohtml_node_borrow(module, target, &tree, &node);
    th_node *fragment = th_tree_make_fragment(tree);
    if (fragment == NULL) { /* GCOVR_EXCL_BR_LINE: fragment arena allocation cannot be forced from a test */
        status = -1;        /* GCOVR_EXCL_LINE */
    } else {                /* GCOVR_EXCL_LINE: brace of the allocation-failure branch */
        Py_ssize_t cursor = 0;
        /* GCOVR_EXCL_BR_START: child allocation failure short-circuits the loop */
        for (Py_ssize_t index = 0; status == 0 && index < PyList_GET_SIZE(spans); index++) {
            /* GCOVR_EXCL_BR_STOP */
            PyObject *span = PyList_GET_ITEM(spans, index);
            Py_ssize_t start = PyLong_AsSsize_t(PyTuple_GET_ITEM(span, 0));
            Py_ssize_t end = PyLong_AsSsize_t(PyTuple_GET_ITEM(span, 1));
            status = append_data_slice(tree, fragment, points, cursor, start);
            if (status == 0) { /* GCOVR_EXCL_BR_LINE: leading text-node allocation */
                status = append_result(tree, fragment, points, start, end, &results[index]);
            }
            cursor = end;
        }
        if (status == 0) { /* GCOVR_EXCL_BR_LINE: prior child allocation */
            status = append_data_slice(tree, fragment, points, cursor, PyUnicode_GET_LENGTH(text));
        }
        if (status == 0) { /* GCOVR_EXCL_BR_LINE: fragment child allocation */
            th_node *parent = node->parent;
            while (fragment->first_child != NULL) {
                th_node *child = fragment->first_child;
                th_node_remove(child);
                th_node_insert_before(parent, child, node);
            }
            th_node_remove(node);
        }
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(points);
    return status;
}

static int process_text(PyObject *module, PyObject *target, int parse_email, PyObject *extra_tlds,
                        PyObject *url_schemes, PyObject *callbacks, PyObject *candidate_type) {
    PyObject *text = snapshot_text(module, target);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: text snapshot allocation cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    PyObject *spans = collect_matches(text, parse_email, 1, extra_tlds, NULL, url_schemes);
    if (spans == NULL) { /* GCOVR_EXCL_BR_LINE: span list/tuple allocation cannot be forced from a test */
        Py_DECREF(text); /* GCOVR_EXCL_LINE */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    if (PyList_GET_SIZE(spans) == 0) {
        Py_DECREF(spans);
        Py_DECREF(text);
        return 0;
    }
    Py_ssize_t count = PyList_GET_SIZE(spans);
    candidate_result *results = PyMem_Calloc((size_t)count, sizeof(candidate_result));
    if (results == NULL) { /* GCOVR_EXCL_BR_LINE: candidate result array allocation cannot be forced from a test */
        Py_DECREF(spans);  /* GCOVR_EXCL_LINE */
        Py_DECREF(text);   /* GCOVR_EXCL_LINE */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    int status = 0;
    Py_ssize_t prepared = 0;
    for (; prepared < count; prepared++) {
        PyObject *span = PyList_GET_ITEM(spans, prepared);
        Py_ssize_t start = PyLong_AsSsize_t(PyTuple_GET_ITEM(span, 0));
        Py_ssize_t end = PyLong_AsSsize_t(PyTuple_GET_ITEM(span, 1));
        int kind = (int)PyLong_AsLong(PyTuple_GET_ITEM(span, 2));
        PyObject *matched = PyUnicode_Substring(text, start, end);
        if (matched == NULL) { /* GCOVR_EXCL_BR_LINE: matched substring allocation */
            status = -1;       /* GCOVR_EXCL_LINE */
            break;             /* GCOVR_EXCL_LINE */
        }
        if (prepare_detected(matched, kind, callbacks, candidate_type, &results[prepared]) < 0) {
            Py_DECREF(matched);
            status = -1;
            break;
        }
        Py_DECREF(matched);
    }
    if (status == 0) {
        status = apply_text(module, target, text, spans, results);
    }
    for (Py_ssize_t index = 0; index < prepared; index++) {
        clear_candidate_result(&results[index]);
    }
    PyMem_Free(results);
    Py_DECREF(spans);
    Py_DECREF(text);
    return status;
}

PyObject *turbohtml_linkify_apply(PyObject *module, PyObject *args) {
    PyObject *owner;
    PyObject *callbacks;
    int parse_email;
    PyObject *extra_tlds;
    PyObject *url_schemes;
    int process_existing_flag;
    PyObject *skip_tags;
    PyObject *candidate_type;
    /* GCOVR_EXCL_BR_START: Linker passes tuple-backed compiled fields */
    if (!PyArg_ParseTuple(args, "OO!pO!O!pO!O:_linkify_apply", &owner, &PyTuple_Type, &callbacks, &parse_email,
                          &PyTuple_Type, &extra_tlds, &PyTuple_Type, &url_schemes, &process_existing_flag,
                          &PyTuple_Type, &skip_tags, &candidate_type)) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    th_tree *tree;
    th_node *root;
    if (turbohtml_node_borrow(module, owner, &tree, &root) < 0) { /* GCOVR_EXCL_BR_LINE: Linker supplies a fragment */
        return NULL;                                              /* GCOVR_EXCL_LINE */
    }
    (void)tree;
    (void)root;
    PyObject *targets = collect_targets(module, owner, process_existing_flag, skip_tags);
    if (targets == NULL) { /* GCOVR_EXCL_BR_LINE: target list/wrapper allocation */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    int status = 0;
    for (Py_ssize_t index = 0; status == 0 && index < PyList_GET_SIZE(targets); index++) {
        PyObject *target = PyList_GET_ITEM(targets, index);
        PyObject *handle = turbohtml_node_handle(target);
        (void)handle;
        th_node *node;
        th_tree *target_tree;
        int text_target;
        Py_BEGIN_CRITICAL_SECTION(handle);
        (void)turbohtml_node_borrow(module, target, &target_tree, &node);
        (void)target_tree;
        text_target = node->type == TH_NODE_TEXT;
        Py_END_CRITICAL_SECTION();
        if (text_target) {
            status = process_text(module, target, parse_email, extra_tlds, url_schemes, callbacks, candidate_type);
        } else {
            status = process_existing(module, target, callbacks, candidate_type);
        }
    }
    Py_DECREF(targets);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}
