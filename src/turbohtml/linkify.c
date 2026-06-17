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

#include "turbohtml.h"

#include "tld_table.h"

#include <stdint.h>

enum th_link_kind {
    TH_LINK_URL = 0,
    TH_LINK_EMAIL = 1,
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

/* A host label character: ASCII alphanumeric, or any non-ASCII code point so an
   internationalized domain stays in one piece (the IRI case). */
static inline int is_label_char(Py_UCS4 c) {
    return is_ascii_alpha(c) || is_ascii_digit(c) || c >= 0x80;
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
        return is_ascii_alpha(c) || is_ascii_digit(c) || c >= 0x80;
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
    if (c <= 0x20 || c == 0x7F) {
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

/* Is the label [start, end) a known TLD? Matched case-insensitively in the
   first-byte bucket of the generated table, which includes the xn-- punycode
   TLDs, so a real xn--p1ai matches and an invented xn--whatever does not. */
static int is_known_tld(int kind, const void *data, Py_ssize_t start, Py_ssize_t end) {
    Py_ssize_t length = end - start;
    if (length < 2) {
        return 0;
    }
    Py_UCS4 first = ascii_lower(READ(start));
    if (first < 'a' || first > 'z') {
        return 0;
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
    return 0;
}

/* Scan a host of dot-separated labels starting at `start`, requiring at least
   one dot and a final label that is a known TLD. Returns the index past the host
   on success, or -1. Hyphens are allowed inside a label, not at its edges. */
static Py_ssize_t scan_host(int kind, const void *data, Py_ssize_t start, Py_ssize_t len) {
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
    if (!is_known_tld(kind, data, last_label_start, host_end)) {
        return -1;
    }
    return host_end;
}

/* Consume an optional ``:port`` and ``/``-or-``?``-led tail after the host,
   trimming trailing punctuation and balancing brackets so a link in prose keeps
   only what belongs to it. Returns the index past the link. */
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
    if (pos >= len || (READ(pos) != '/' && READ(pos) != '?')) {
        return pos;
    }
    Py_ssize_t end = pos;
    int round = 0;
    int square = 0;
    while (pos < len) {
        Py_UCS4 c = READ(pos);
        if (!is_url_tail_char(c)) {
            break;
        }
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
        }
        pos++;
        /* a closing bracket or a non-trailing-punctuation byte can end the link;
           trailing . , ! ? : ; * are valid inside it but never its last byte */
        if (round == 0 && square == 0 && c != '.' && c != ',' && c != '!' && c != '?' && c != ':' && c != ';' &&
            c != '*') {
            end = pos;
        }
    }
    return end;
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

/* Try to match a scheme:// URL whose ``:`` is at `colon`. */
static int match_url(int kind, const void *data, Py_ssize_t colon, Py_ssize_t start, Py_ssize_t len,
                     Py_ssize_t *out_start, Py_ssize_t *out_end) {
    if (colon + 2 >= len || READ(colon + 1) != '/' || READ(colon + 2) != '/') {
        return 0;
    }
    Py_ssize_t scheme_start = scan_scheme_start(kind, data, colon, start);
    if (scheme_start < 0 || blocked_on_left(kind, data, scheme_start)) {
        return 0;
    }
    Py_ssize_t host_end = scan_host(kind, data, colon + 3, len);
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
                        Py_ssize_t *out_start, Py_ssize_t *out_end) {
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
    Py_ssize_t host_end = scan_host(kind, data, pos, len);
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
                       Py_ssize_t *out_start, Py_ssize_t *out_end) {
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
    Py_ssize_t host_end = scan_host(kind, data, at + 1, len);
    if (host_end < 0) {
        return 0;
    }
    *out_start = pos;
    *out_end = host_end;
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

/* _linkify_scan(text, parse_email, bare_domains) -> list[(start, end, kind)] */
PyObject *turbohtml_linkify_scan(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int parse_email;
    int bare_domains;
    if (!PyArg_ParseTuple(args, "Upp:_linkify_scan", &text, &parse_email, &bare_domains)) {
        return NULL;
    }
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
            found = match_url(kind, data, pos, 0, len, &match_start, &match_end);
        } else if (c == '@' && parse_email) {
            found = match_email(kind, data, pos, 0, len, &match_start, &match_end);
            link_kind = TH_LINK_EMAIL;
        } else if (c == '.' && bare_domains) {
            found = match_domain(kind, data, pos, 0, len, &match_start, &match_end);
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
