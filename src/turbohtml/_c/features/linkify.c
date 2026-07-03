/* URL and email detection for linkify, the hot scan kept in C.

   The Python layer (turbohtml/linkify.py) walks the parsed tree and hands each
   eligible text run here; this file finds the link spans in it. The algorithm
   is the trigger-then-expand model the Rust `linkify` crate uses, not a regex:
   scan for the few bytes that can begin a link (`:` for a scheme, `@` for an
   email, `.` for a bare domain), then expand left and right from the trigger to
   the link's bounds. A bare domain counts as a link only when its last label is
   a real TLD, matched case-insensitively against the generated tld_table.h, the
   same rule bleach used. Matches are returned as (start, end, kind) spans into
   the input; the scan never allocates per match. */

#include "core/common.h"

#include "data/tld_table.h"

#include <stdint.h>

enum th_link_kind {
    TH_LINK_URL = 0,
    TH_LINK_EMAIL = 1,
    TH_LINK_SCHEME = 2,
};

static inline Py_UCS4 ascii_lower(Py_UCS4 c) {
    return (c >= 'A' && c <= 'Z') ? c + ('a' - 'A') : c;
}

static inline int is_ascii_alpha(Py_UCS4 c) {
    Py_UCS4 lower = c | 0x20;
    return lower >= 'a' && lower <= 'z';
}

static inline int is_ascii_digit(Py_UCS4 c) {
    return c >= '0' && c <= '9';
}

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

/* A scheme character left of the ``:`` (RFC 3986 scheme grammar after the first
   letter): letters, digits, and the +-. separators. */
static inline int is_scheme_char(Py_UCS4 c) {
    return is_ascii_alpha(c) || is_ascii_digit(c) || c == '+' || c == '-' || c == '.';
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
            if (PyUnicode_READ(candidate_kind, candidate_data, offset) != ascii_lower(READ(start + offset))) {
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
    Py_UCS4 first = ascii_lower(READ(start));
    if (first < 'a' || first > 'z') {
        return tuple_has_label(extra_tlds, kind, data, start, end);
    }
    for (int index = th_tld_first[first]; index < th_tld_first[first + 1]; index++) {
        if (th_tld_table[index].name_len != length) {
            continue;
        }
        int matched = 1;
        for (Py_ssize_t offset = 0; offset < length; offset++) {
            if ((Py_UCS4)(unsigned char)th_tld_table[index].name[offset] != ascii_lower(READ(start + offset))) {
                matched = 0;
                break;
            }
        }
        if (matched) {
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
    while (pos > start && is_scheme_char(READ(pos - 1))) {
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

/* The shared scan: trigger on the few bytes that can begin a link and expand to
   its bounds, appending each (start, end, kind) span. extra_tlds extends the
   bare-domain/email TLD rule; schemes (NULL in the rewrite path) enables
   registered scheme-less URLs; url_schemes (NULL for the permissive raw scan)
   restricts which scheme://host schemes autolink. Returns the span list, or NULL. */
static PyObject *do_scan(PyObject *text, int parse_email, int bare_domains, PyObject *extra_tlds, PyObject *schemes,
                         PyObject *url_schemes) {
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);

    PyObject *spans = PyList_New(0);
    if (spans == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }

    Py_ssize_t pos = 0;
    while (pos < len) {
        Py_UCS4 c = READ(pos);
        Py_ssize_t match_start = 0;
        Py_ssize_t match_end = 0;
        enum th_link_kind link_kind = TH_LINK_URL;
        int found = 0;
        if (c == ':') {
            found = match_url(kind, data, pos, 0, len, &match_start, &match_end, url_schemes);
            if (!found && schemes != NULL) {
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
            if (append_span(spans, match_start, match_end, link_kind) < 0) { /* GCOVR_EXCL_BR_LINE */
                Py_DECREF(spans);                                            /* GCOVR_EXCL_LINE */
                return NULL;                                                 /* GCOVR_EXCL_LINE */
            }
            pos = match_end;
        } else {
            pos++;
        }
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
    return do_scan(text, parse_email, bare_domains, extra_tlds, NULL, url_schemes);
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
    return do_scan(text, emails, bare_domains, extra_tlds, schemes, url_schemes);
}
