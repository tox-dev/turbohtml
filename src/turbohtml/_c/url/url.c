/* URL splitting: break a URL into its components and classify the host, the WHATWG basic-URL-parser split step the
   _urls.py cleaner delegates here instead of reaching urllib.parse.urlsplit (tox-dev/turbohtml#478).

   The split follows the URL standard's basic parser as far as component boundaries: it drops the leading C0-control and
   space bytes and every tab, newline, and carriage return (spec 4.4), reads a scheme when a leading letter is followed
   by scheme characters up to a ':', an authority after '//', and the path, query, and fragment at the first '?' and
   '#'. Percent-encoding, dot-segment resolution, IDNA host ToASCII, and relative joining are not this unit's job -- the
   Python shim keeps those for now -- so the host is reported as raw ASCII spans plus a kind tag (IPv6 literal, IPv4
   literal, or registered name), never decoded or lowercased here. An authority whose brackets are unbalanced is the one
   split-time failure, matching the "Invalid IPv6 URL" the shim's normalize_url path documents. */

#include "url/url.h"

/* The characters each URL component keeps raw, complementing the WHATWG percent-encode sets (URL standard 1.3): every
   byte outside its set is UTF-8 percent-encoded. The three sets share the RFC 3986 unreserved run; the query set drops
   ' (special-query set) and keeps `, the path set keeps neither, and the fragment set keeps ' but not `. All three keep
   '%', so an already-encoded %XX passes through the encoder unchanged (only its hex digits are uppercased). */
#define URL_UNRESERVED TH_URL_ALPHA "0123456789-._~"
static const char URL_PATH_KEEP[] = URL_UNRESERVED "!$%&'()*+,/:;=@[\\]^|";
static const char URL_QUERY_KEEP[] = URL_UNRESERVED "!$%&()*+,/:;=?@[\\]^`{|}";
static const char URL_FRAGMENT_KEEP[] = URL_UNRESERVED "!#$%&'()*+,/:;=?@[\\]^{|}";
static const char *const URL_KEEP_SETS[] = {URL_PATH_KEEP, URL_QUERY_KEEP, URL_FRAGMENT_KEEP};
static const size_t URL_KEEP_LENS[] = {sizeof(URL_PATH_KEEP) - 1, sizeof(URL_QUERY_KEEP) - 1,
                                       sizeof(URL_FRAGMENT_KEEP) - 1};
static const char HEX_UPPER[] = "0123456789ABCDEF";

/* The value of a hex digit, or -1 when `ch` is not one; a memchr over the digit set, never a chained range test, so the
   two hex probes an escape needs stay branch-gate stable when clang inlines them. */
static int hex_value(Py_UCS4 ch) {
    static const char HEX_DIGITS[] = "0123456789abcdefABCDEF";
    if (ch > 0x7F) {
        return -1;
    }
    const char *found = memchr(HEX_DIGITS, (char)ch, sizeof(HEX_DIGITS) - 1);
    if (found == NULL) {
        return -1;
    }
    Py_ssize_t offset = found - HEX_DIGITS;
    return offset < 16 ? (int)offset : (int)(offset - 6); /* 'A'..'F' sit past the lowercase run, so fold back by 6 */
}

Py_ssize_t th_url_encode_span(char *out, Py_ssize_t at, const char *bytes, Py_ssize_t start, Py_ssize_t end,
                              int set_id) {
    const char *keep = URL_KEEP_SETS[set_id];
    size_t keep_len = URL_KEEP_LENS[set_id];
    for (Py_ssize_t index = start; index < end; index++) {
        unsigned char byte = (unsigned char)bytes[index];
        if (th_url_in_set(byte, keep, keep_len)) {
            out[at++] = (char)byte;
        } else {
            out[at++] = '%';
            out[at++] = HEX_UPPER[byte >> 4];
            out[at++] = HEX_UPPER[byte & 0x0F];
        }
    }
    return at;
}

/* _url_percent_encode(text, set_id): the shim's per-component encoder, replacing urllib.parse.quote plus a preceding
   uppercase-escape sweep. It UTF-8 encodes the component (a lone surrogate has no encoding, so this raises), then
   rewrites an existing %XX to uppercase hex and percent-encodes every byte outside the set. Since '%' stays in every
   keep set, a valid escape survives; a stray '%' or a truncated %X is a literal byte the set keeps. */
PyObject *turbohtml_url_percent_encode(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int set_id;
    if (!PyArg_ParseTuple(args, "Ui", &text, &set_id)) {
        return NULL;
    }
    Py_ssize_t len;
    /* a lone surrogate has no UTF-8 form; the shim rewraps the UnicodeEncodeError PyUnicode_AsUTF8AndSize sets */
    const char *bytes = PyUnicode_AsUTF8AndSize(text, &len);
    if (bytes == NULL) {
        return NULL;
    }
    char *out = PyMem_Malloc((size_t)len * 3 + 1); /* every byte encodes to at most "%HH" */
    if (out == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    const char *keep = URL_KEEP_SETS[set_id];
    size_t keep_len = URL_KEEP_LENS[set_id];
    Py_ssize_t at = 0;
    Py_ssize_t index = 0;
    while (index < len) {
        unsigned char byte = (unsigned char)bytes[index];
        int high = byte == '%' && index + 2 < len ? hex_value((Py_UCS4)(unsigned char)bytes[index + 1]) : -1;
        int low = high >= 0 ? hex_value((Py_UCS4)(unsigned char)bytes[index + 2]) : -1;
        if (low >= 0) {
            out[at++] = '%';
            out[at++] = HEX_UPPER[high];
            out[at++] = HEX_UPPER[low];
            index += 3;
        } else if (th_url_in_set(byte, keep, keep_len)) {
            out[at++] = (char)byte;
            index++;
        } else {
            out[at++] = '%';
            out[at++] = HEX_UPPER[byte >> 4];
            out[at++] = HEX_UPPER[byte & 0x0F];
            index++;
        }
    }
    PyObject *result = PyUnicode_DecodeUTF8(out, at, "strict"); /* out is pure ASCII by construction, so strict holds */
    PyMem_Free(out);
    return result; /* NULL only on the excluded decode-failure path */
}

/* Flush the decoded byte run to the UCS4 output as UTF-8 with U+FFFD replacement (urllib's errors="replace"), so an
   invalid sequence never fails; returns -1 with an error set only on the excluded allocation-failure path. */
static int decode_flush(const unsigned char *run, Py_ssize_t run_len, Py_UCS4 *out, Py_ssize_t *out_len) {
    if (run_len == 0) {
        return 0;
    }
    PyObject *piece = PyUnicode_DecodeUTF8((const char *)run, run_len, "replace");
    if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: replace never raises on content, only on allocation */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int kind = PyUnicode_KIND(piece);
    const void *data = PyUnicode_DATA(piece);
    Py_ssize_t piece_len = PyUnicode_GET_LENGTH(piece);
    for (Py_ssize_t index = 0; index < piece_len; index++) {
        out[(*out_len)++] = PyUnicode_READ(kind, data, index);
    }
    Py_DECREF(piece);
    return 0;
}

/* _url_percent_decode(text): the shim's per-component decoder, replacing urllib.parse.unquote. It walks the string,
   turning a %XX inside an ASCII run into its byte and keeping any other ASCII char or non-ASCII code point verbatim,
   then UTF-8 decodes each ASCII byte run with U+FFFD replacement. A non-ASCII input char ends the current run, matching
   the ascii/non-ascii split unquote makes, so a raw code point (even a lone surrogate) survives unencoded. */
PyObject *turbohtml_url_percent_decode(PyObject *Py_UNUSED(module), PyObject *arg) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    size_t span = (size_t)(len > 0 ? len : 1);
    Py_UCS4 *out = PyMem_Malloc(span * sizeof(Py_UCS4)); /* decoding never grows the code-point count */
    unsigned char *run = PyMem_Malloc(span);
    if (out == NULL || run == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(run);              /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t out_len = 0;
    Py_ssize_t run_len = 0;
    Py_ssize_t index = 0;
    while (index < len) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        int high = ch == '%' && index + 2 < len ? hex_value(PyUnicode_READ(kind, data, index + 1)) : -1;
        int low = high >= 0 ? hex_value(PyUnicode_READ(kind, data, index + 2)) : -1;
        if (low >= 0) {
            run[run_len++] = (unsigned char)(high * 16 + low);
            index += 3;
        } else if (ch <= 0x7F) {
            run[run_len++] = (unsigned char)ch;
            index++;
        } else {
            if (decode_flush(run, run_len, out, &out_len) < 0) { /* GCOVR_EXCL_BR_LINE: replace only fails on alloc */
                PyMem_Free(out);                                 /* GCOVR_EXCL_LINE: allocation-failure path */
                PyMem_Free(run);                                 /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            run_len = 0;
            out[out_len++] = ch;
            index++;
        }
    }
    PyObject *result = NULL;
    if (decode_flush(run, run_len, out, &out_len) == 0) { /* GCOVR_EXCL_BR_LINE: replace only fails on alloc */
        result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, out, out_len);
    }
    PyMem_Free(out);
    PyMem_Free(run);
    return result; /* NULL only on the excluded flush- or build-failure path */
}

/* The bytes the basic parser removes from anywhere in the input (spec 4.4 step 2). Leading C0-or-space is stripped
   separately; these three are also excised mid-URL. An array probe, not a chained ||, keeps the branch gate stable. */
static int is_removed(Py_UCS4 ch) {
    static const Py_UCS4 removed[] = {'\t', '\n', '\r'};
    for (size_t index = 0; index < sizeof(removed) / sizeof(removed[0]); index++) {
        if (ch == removed[index]) {
            return 1;
        }
    }
    return 0;
}

static PyObject *span_str(const Py_UCS4 *work, Py_ssize_t start, Py_ssize_t end) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, work + start, end - start);
}

/* One code-point read for both preprocessing loops. Routing every read of the input through a single site keeps the
   PyUnicode_READ width switch (its 1/2/4-byte arms) from multiplying across the loops under the -O0 coverage build. */
static Py_UCS4 input_char(int kind, const void *data, Py_ssize_t index) {
    return PyUnicode_READ(kind, data, index);
}

/* Decompose the authority work[start,end) into userinfo (before the last '@'), host, and port. A '['-led host is an
   IPv6 literal reported without its brackets; a host of only ASCII digits and dots is an IPv4 literal; anything else is
   a registered name. The kind tells the shim which hosts skip IDNA and the sanitizer which to reject as a literal, so
   this is a literal-shape test, not a full address parse -- IPv4/IPv6 canonicalization is a later step. */
void th_url_authority(const Py_UCS4 *work, Py_ssize_t start, Py_ssize_t end, th_authority *out) {
    out->user_start = start;
    Py_ssize_t at = -1;
    for (Py_ssize_t index = start; index < end; index++) {
        if (work[index] == '@') {
            at = index;
        }
    }
    Py_ssize_t info = start;
    if (at >= 0) {
        out->user_end = at;
        info = at + 1;
    } else {
        out->user_end = start;
    }
    out->has_port = 0;
    out->port_start = end;
    out->port_end = end;
    if (info < end && work[info] == '[') {
        out->kind = TH_HOST_IPV6;
        out->host_start = info + 1;
        Py_ssize_t close = end;
        for (Py_ssize_t index = info + 1; index < end; index++) {
            if (work[index] == ']') {
                close = index;
                break;
            }
        }
        out->host_end = close;
        if (close + 1 < end && work[close + 1] == ':') {
            out->has_port = 1;
            out->port_start = close + 2;
        }
        return;
    }
    out->host_start = info;
    out->host_end = end;
    for (Py_ssize_t index = info; index < end; index++) {
        if (work[index] == ':') {
            out->host_end = index;
            out->has_port = 1;
            out->port_start = index + 1;
            break;
        }
    }
    int numeric = out->host_end > out->host_start;
    for (Py_ssize_t index = out->host_start; index < out->host_end; index++) {
        Py_UCS4 ch = work[index];
        if (!((ch >= '0' && ch <= '9') || ch == '.')) {
            numeric = 0;
            break;
        }
    }
    out->kind = numeric ? TH_HOST_IPV4 : TH_HOST_REGNAME;
}

/* _url_split(url) -> (scheme, netloc, path, query, fragment, userinfo, host, port, has_port, host_kind). scheme is
   lowercased; the host is the bracket-stripped ASCII span; every other component is the verbatim slice. Raises
   ValueError on an authority with an unbalanced '['/']' pair. The shim guarantees a str argument, as the other _html
   entry points assume. */
PyObject *turbohtml_url_split(PyObject *Py_UNUSED(module), PyObject *arg) {
    Py_ssize_t raw_len = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    Py_UCS4 *work = PyMem_Malloc((size_t)(raw_len + 1) * sizeof(Py_UCS4));
    if (work == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t read = 0;
    while (read < raw_len && input_char(kind, data, read) <= 0x20) {
        read++;
    }
    Py_ssize_t len = 0;
    for (; read < raw_len; read++) {
        Py_UCS4 ch = input_char(kind, data, read);
        if (!is_removed(ch)) {
            work[len++] = ch;
        }
    }
    Py_ssize_t scheme_end = -1;
    if (len > 0 && th_scheme_start(work[0])) {
        for (Py_ssize_t index = 1; index < len; index++) {
            Py_UCS4 ch = work[index];
            if (ch == ':') {
                scheme_end = index;
                break;
            }
            if (!th_scheme_char(ch)) {
                break;
            }
        }
    }
    for (Py_ssize_t index = 0; index < scheme_end; index++) {
        work[index] |= 0x20; /* scheme chars are ASCII; |0x20 lowercases a letter and is identity on a digit or +-. */
    }
    Py_ssize_t body = scheme_end >= 0 ? scheme_end + 1 : 0;
    Py_ssize_t netloc_start = body;
    Py_ssize_t netloc_end = body;
    Py_ssize_t rem = body;
    if (body + 1 < len && work[body] == '/' && work[body + 1] == '/') {
        netloc_start = body + 2;
        netloc_end = len;
        for (Py_ssize_t index = netloc_start; index < len; index++) {
            Py_UCS4 ch = work[index];
            if (ch == '/' || ch == '?' || ch == '#') {
                netloc_end = index;
                break;
            }
        }
        rem = netloc_end;
        int has_open = 0;
        int has_close = 0;
        for (Py_ssize_t index = netloc_start; index < netloc_end; index++) {
            if (work[index] == '[') {
                has_open = 1;
            } else if (work[index] == ']') {
                has_close = 1;
            }
        }
        if (has_open != has_close) {
            PyMem_Free(work);
            PyErr_SetString(PyExc_ValueError, "Invalid IPv6 URL");
            return NULL;
        }
    }
    Py_ssize_t hash = -1;
    for (Py_ssize_t index = rem; index < len; index++) {
        if (work[index] == '#') {
            hash = index;
            break;
        }
    }
    Py_ssize_t url_end = hash >= 0 ? hash : len;
    Py_ssize_t frag_start = hash >= 0 ? hash + 1 : len;
    Py_ssize_t query_at = -1;
    for (Py_ssize_t index = rem; index < url_end; index++) {
        if (work[index] == '?') {
            query_at = index;
            break;
        }
    }
    Py_ssize_t path_end = query_at >= 0 ? query_at : url_end;
    Py_ssize_t query_start = query_at >= 0 ? query_at + 1 : url_end;
    th_authority auth;
    th_url_authority(work, netloc_start, netloc_end, &auth);
    Py_ssize_t spans[8][2] = {
        {0, scheme_end >= 0 ? scheme_end : 0},
        {netloc_start, netloc_end},
        {rem, path_end},
        {query_start, url_end},
        {frag_start, len},
        {auth.user_start, auth.user_end},
        {auth.host_start, auth.host_end},
        {auth.port_start, auth.port_end},
    };
    PyObject *parts[8];
    for (int index = 0; index < 8; index++) {
        parts[index] = span_str(work, spans[index][0], spans[index][1]);
        if (parts[index] == NULL) {      /* GCOVR_EXCL_BR_LINE: only span_str allocation can fail */
            while (index-- > 0) {        /* GCOVR_EXCL_LINE: allocation-failure unwind */
                Py_DECREF(parts[index]); /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            PyMem_Free(work); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
    }
    PyObject *result = Py_BuildValue("(NNNNNNNNNi)", parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
                                     parts[6], parts[7], PyBool_FromLong(auth.has_port), auth.kind);
    PyMem_Free(work);
    return result;
}

/* The schemes RFC 3986 relative resolution applies to, urllib's uses_relative list minus the empty scheme (the caller
   short-circuits that on truthiness). A reference whose scheme equals the base's but is absent from this set is
   returned verbatim, the "opaque" reference urllib's urljoin leaves alone. Membership is a short linear scan; the list
   is tiny and the compared scheme is already lowercased. */
static const char *const URL_RELATIVE_SCHEMES[] = {
    "ftp",  "http",  "gopher", "nntp",     "imap", "wais", "file",    "https", "shttp", "mms",
    "rtsp", "rtsps", "rtspu",  "prospero", "sftp", "svn",  "svn+ssh", "ws",    "wss",
};

/* A URL broken into the five RFC 3986 components with a present/absent flag on each optional one, the split urljoin
   resolves a reference against a base with. Unlike turbohtml_url_split this keeps the authority verbatim (no host
   classification) and records whether the query and fragment delimiters were present, since the empty-path branch
   distinguishes a bare "?" from a missing query. The buffer is the preprocessed code points every span indexes. */
typedef struct {
    Py_UCS4 *buf;
    int has_scheme;
    Py_ssize_t scheme_start, scheme_end;
    int has_netloc;
    Py_ssize_t netloc_start, netloc_end;
    Py_ssize_t path_start, path_end;
    int has_query;
    Py_ssize_t query_start, query_end;
    int has_fragment;
    Py_ssize_t fragment_start, fragment_end;
} url_ref;

/* Split `src` into a url_ref the way urllib's _urlsplit bounds the components: strip the leading C0-or-space and every
   tab, newline, and carriage return, read a scheme when a leading letter runs to the first ':' over scheme characters,
   an authority after '//', and the query and fragment at the first '?' and '#'. Owns a freshly allocated buffer stored
   in `out->buf`. Returns -1 with a ValueError on an authority whose '['/']' pair is unbalanced (the one split-time
   failure urljoin surfaces), matching turbohtml_url_split's shallow host check. */
static int parse_ref(PyObject *src, url_ref *out) {
    Py_ssize_t raw_len = PyUnicode_GET_LENGTH(src);
    int kind = PyUnicode_KIND(src);
    const void *data = PyUnicode_DATA(src);
    Py_UCS4 *buf = PyMem_Malloc((size_t)(raw_len + 1) * sizeof(Py_UCS4));
    if (buf == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t read = 0;
    while (read < raw_len && input_char(kind, data, read) <= 0x20) {
        read++;
    }
    Py_ssize_t len = 0;
    for (; read < raw_len; read++) {
        Py_UCS4 ch = input_char(kind, data, read);
        if (!is_removed(ch)) {
            buf[len++] = ch;
        }
    }
    out->buf = buf;
    out->has_scheme = 0;
    out->scheme_start = out->scheme_end = 0;
    out->netloc_start = out->netloc_end = 0;
    out->query_start = out->query_end = 0;
    out->fragment_start = out->fragment_end = 0;
    Py_ssize_t rest = 0;
    Py_ssize_t colon = -1;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (buf[index] == ':') {
            colon = index;
            break;
        }
    }
    if (colon > 0 && th_scheme_start(buf[0])) {
        int scheme_ok = 1;
        for (Py_ssize_t index = 0; index < colon; index++) {
            if (!th_scheme_char(buf[index])) {
                scheme_ok = 0;
                break;
            }
        }
        if (scheme_ok) {
            for (Py_ssize_t index = 0; index < colon; index++) {
                buf[index] |= 0x20; /* scheme chars are ASCII; |0x20 lowercases a letter, identity on a digit or +-. */
            }
            out->has_scheme = 1;
            out->scheme_start = 0;
            out->scheme_end = colon;
            rest = colon + 1;
        }
    }
    out->has_netloc = 0;
    if (rest + 1 < len && buf[rest] == '/' && buf[rest + 1] == '/') {
        Py_ssize_t netloc_start = rest + 2;
        Py_ssize_t netloc_end = len;
        for (Py_ssize_t index = netloc_start; index < len; index++) {
            Py_UCS4 ch = buf[index];
            if (ch == '/' || ch == '?' || ch == '#') {
                netloc_end = index;
                break;
            }
        }
        int has_open = 0;
        int has_close = 0;
        for (Py_ssize_t index = netloc_start; index < netloc_end; index++) {
            if (buf[index] == '[') {
                has_open = 1;
            } else if (buf[index] == ']') {
                has_close = 1;
            }
        }
        if (has_open != has_close) {
            PyMem_Free(buf);
            PyErr_SetString(PyExc_ValueError, "Invalid IPv6 URL");
            return -1;
        }
        out->has_netloc = 1;
        out->netloc_start = netloc_start;
        out->netloc_end = netloc_end;
        rest = netloc_end;
    }
    Py_ssize_t body_end = len;
    out->has_fragment = 0;
    for (Py_ssize_t index = rest; index < len; index++) {
        if (buf[index] == '#') {
            out->has_fragment = 1;
            out->fragment_start = index + 1;
            out->fragment_end = len;
            body_end = index;
            break;
        }
    }
    out->has_query = 0;
    Py_ssize_t path_end = body_end;
    for (Py_ssize_t index = rest; index < body_end; index++) {
        if (buf[index] == '?') {
            out->has_query = 1;
            out->query_start = index + 1;
            out->query_end = body_end;
            path_end = index;
            break;
        }
    }
    out->path_start = rest;
    out->path_end = path_end;
    return 0;
}

/* Whether the lowercased scheme span equals `scheme`, the two-string equality the reference-vs-base comparison and the
   uses_relative membership scan need. */
static int scheme_eq(const Py_UCS4 *buf, Py_ssize_t start, Py_ssize_t end, const char *scheme) {
    for (Py_ssize_t index = start; index < end; index++) {
        if (scheme[index - start] == '\0' || buf[index] != (Py_UCS4)(unsigned char)scheme[index - start]) {
            return 0;
        }
    }
    return scheme[end - start] == '\0';
}

/* Whether two spans hold the same code points, the reference-scheme-equals-base-scheme test the join guard runs. */
static int spans_equal(const Py_UCS4 *left, Py_ssize_t left_start, Py_ssize_t left_end, const Py_UCS4 *right,
                       Py_ssize_t right_start, Py_ssize_t right_end) {
    if (left_end - left_start != right_end - right_start) {
        return 0;
    }
    for (Py_ssize_t offset = 0; offset < left_end - left_start; offset++) {
        if (left[left_start + offset] != right[right_start + offset]) {
            return 0;
        }
    }
    return 1;
}

static int scheme_is_relative(const Py_UCS4 *buf, Py_ssize_t start, Py_ssize_t end) {
    for (size_t index = 0; index < sizeof(URL_RELATIVE_SCHEMES) / sizeof(URL_RELATIVE_SCHEMES[0]); index++) {
        if (scheme_eq(buf, start, end, URL_RELATIVE_SCHEMES[index])) {
            return 1;
        }
    }
    return 0;
}

/* Whether the segment buf[start,end) is the literal `text`, the "." / ".." test dot-segment resolution runs. */
static int segment_eq(const Py_UCS4 *buf, Py_ssize_t start, Py_ssize_t end, const char *text) {
    Py_ssize_t text_len = (Py_ssize_t)strlen(text);
    if (end - start != text_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < text_len; index++) {
        if (buf[start + index] != (Py_UCS4)(unsigned char)text[index]) {
            return 0;
        }
    }
    return 1;
}

typedef struct {
    const Py_UCS4 *buf;
    Py_ssize_t start, end;
} url_seg;

/* Split buf[start,end) on '/' into `out`, returning the segment count; an empty span is one empty segment, matching
   Python's "".split('/') == [''], so a trailing '/' yields a trailing empty segment. `out` holds at most end-start+1.
 */
static Py_ssize_t split_segments(const Py_UCS4 *buf, Py_ssize_t start, Py_ssize_t end, url_seg *out) {
    Py_ssize_t count = 0;
    Py_ssize_t segment_start = start;
    for (Py_ssize_t index = start; index < end; index++) {
        if (buf[index] == '/') {
            out[count++] = (url_seg){buf, segment_start, index};
            segment_start = index + 1;
        }
    }
    out[count++] = (url_seg){buf, segment_start, end};
    return count;
}

/* Resolve the relative reference path against the base path (RFC 3986 5.2.3 merge plus 5.2.4 remove_dot_segments, in
   urllib's segment-list form) into `out`, returning its length. `scratch` holds the working segment lists, sized for
   both paths' segments. The merge drops the base's trailing non-directory segment, joins on a rooted reference path or
   splices a relative one onto the base directory (collapsing the redundant empty segments urllib's filter removes),
   then walks the segments popping ".." and dropping ".". */
static Py_ssize_t merge_path(const url_ref *base, const url_ref *ref, url_seg *scratch, url_seg *resolved,
                             Py_UCS4 *out) {
    int ref_absolute = ref->buf[ref->path_start] == '/'; /* the caller only merges a non-empty reference path */
    Py_ssize_t total;
    if (ref_absolute) {
        total = split_segments(ref->buf, ref->path_start, ref->path_end, scratch);
    } else {
        total = split_segments(base->buf, base->path_start, base->path_end, scratch);
        if (scratch[total - 1].end > scratch[total - 1].start) {
            total--; /* the base's last segment is a file, not a directory, so the reference replaces it */
        }
        total += split_segments(ref->buf, ref->path_start, ref->path_end, scratch + total);
        if (total > 2) { /* drop the empty interior segments a splice would re-join into redundant slashes */
            Py_ssize_t write = 1;
            for (Py_ssize_t index = 1; index < total - 1; index++) {
                if (scratch[index].end > scratch[index].start) {
                    scratch[write++] = scratch[index];
                }
            }
            scratch[write++] = scratch[total - 1];
            total = write;
        }
    }
    int last_is_dot = segment_eq(scratch[total - 1].buf, scratch[total - 1].start, scratch[total - 1].end, ".") ||
                      segment_eq(scratch[total - 1].buf, scratch[total - 1].start, scratch[total - 1].end, "..");
    Py_ssize_t kept = 0;
    for (Py_ssize_t index = 0; index < total; index++) {
        url_seg segment = scratch[index];
        if (segment_eq(segment.buf, segment.start, segment.end, "..")) {
            if (kept > 0) {
                kept--;
            }
        } else if (!segment_eq(segment.buf, segment.start, segment.end, ".")) {
            resolved[kept++] = segment;
        }
    }
    if (last_is_dot) {
        resolved[kept++] = (url_seg){base->buf, 0, 0}; /* a trailing "." or ".." leaves the resolved path a directory */
    }
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < kept; index++) {
        if (index > 0) {
            out[at++] = '/';
        }
        for (Py_ssize_t cursor = resolved[index].start; cursor < resolved[index].end; cursor++) {
            out[at++] = resolved[index].buf[cursor];
        }
    }
    if (at == 0) {
        out[at++] = '/'; /* an empty join serializes as the root, urllib's "'/'.join(...) or '/'" */
    }
    return at;
}

/* Serialize the resolved components (urllib's _urlunsplit, restricted to the cases reference resolution reaches: a base
   always carries a scheme and, in practice, an authority, so the no-authority path with a "//"-leading body never
   arises). Copies the scheme, "//" + authority, path, "?" + query, and "#" + fragment into one allocation. */
static PyObject *build_url(int scheme_has, const Py_UCS4 *scheme_buf, Py_ssize_t scheme_start, Py_ssize_t scheme_end,
                           int netloc_has, const Py_UCS4 *netloc_buf, Py_ssize_t netloc_start, Py_ssize_t netloc_end,
                           const Py_UCS4 *path_buf, Py_ssize_t path_start, Py_ssize_t path_end, int query_has,
                           const Py_UCS4 *query_buf, Py_ssize_t query_start, Py_ssize_t query_end, int fragment_has,
                           const Py_UCS4 *fragment_buf, Py_ssize_t fragment_start, Py_ssize_t fragment_end) {
    Py_ssize_t bound = (scheme_end - scheme_start) + (netloc_end - netloc_start) + (path_end - path_start) +
                       (query_end - query_start) + (fragment_end - fragment_start) + 8;
    Py_UCS4 *out = PyMem_Malloc((size_t)bound * sizeof(Py_UCS4));
    if (out == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t at = 0;
    if (scheme_has) {
        for (Py_ssize_t index = scheme_start; index < scheme_end; index++) {
            out[at++] = scheme_buf[index];
        }
        out[at++] = ':';
    }
    if (netloc_has) {
        out[at++] = '/';
        out[at++] = '/';
        for (Py_ssize_t index = netloc_start; index < netloc_end; index++) {
            out[at++] = netloc_buf[index];
        }
        if (path_end > path_start && path_buf[path_start] != '/') {
            out[at++] = '/'; /* a ".." that popped the root leaves a rootless path an authority must re-root */
        }
    }
    for (Py_ssize_t index = path_start; index < path_end; index++) {
        out[at++] = path_buf[index];
    }
    if (query_has) {
        out[at++] = '?';
        for (Py_ssize_t index = query_start; index < query_end; index++) {
            out[at++] = query_buf[index];
        }
    }
    if (fragment_has) {
        out[at++] = '#';
        for (Py_ssize_t index = fragment_start; index < fragment_end; index++) {
            out[at++] = fragment_buf[index];
        }
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, out, at);
    PyMem_Free(out);
    return result;
}

/* Join a (possibly relative) reference onto a base URL, the RFC 3986 5.3 reference-transform urllib.parse.urljoin runs,
   ported so base_url(), the extraction methods, and the extract_links shim resolve links in C rather than importing
   urllib.parse. `base` and `target` are borrowed str references. Returns a new reference, or NULL with a ValueError set
   when a component cannot be split (an unbalanced IPv6 bracket). */
PyObject *th_url_join(PyObject *base, PyObject *target) {
    if (PyUnicode_GET_LENGTH(base) == 0) {
        return Py_NewRef(target); /* an empty base leaves the reference as the whole URL */
    }
    if (PyUnicode_GET_LENGTH(target) == 0) {
        return Py_NewRef(base); /* an empty reference resolves to the base unchanged */
    }
    url_ref base_parts;
    url_ref ref_parts;
    if (parse_ref(base, &base_parts) < 0) {
        return NULL;
    }
    if (parse_ref(target, &ref_parts) < 0) {
        PyMem_Free(base_parts.buf);
        return NULL;
    }
    /* The effective scheme is the reference's own or, lacking one, the base's. Relative resolution only runs when that
       scheme is the base's and is one RFC 3986 resolves against; otherwise urljoin returns the reference verbatim
       (leading whitespace and all) -- a reference under a foreign scheme, or any reference under a base whose scheme is
       opaque (mailto:, tel:). */
    const Py_UCS4 *scheme_buf = ref_parts.has_scheme ? ref_parts.buf : base_parts.buf;
    Py_ssize_t scheme_start = ref_parts.has_scheme ? ref_parts.scheme_start : base_parts.scheme_start;
    Py_ssize_t scheme_end = ref_parts.has_scheme ? ref_parts.scheme_end : base_parts.scheme_end;
    int scheme_has = ref_parts.has_scheme ? 1 : base_parts.has_scheme;
    int scheme_differs =
        ref_parts.has_scheme &&
        !(base_parts.has_scheme && spans_equal(base_parts.buf, base_parts.scheme_start, base_parts.scheme_end,
                                               ref_parts.buf, ref_parts.scheme_start, ref_parts.scheme_end));
    if (scheme_differs || (scheme_has && !scheme_is_relative(scheme_buf, scheme_start, scheme_end))) {
        PyMem_Free(base_parts.buf);
        PyMem_Free(ref_parts.buf);
        return Py_NewRef(target);
    }
    PyObject *result;
    if (ref_parts.has_netloc && ref_parts.netloc_end > ref_parts.netloc_start) {
        result = build_url(base_parts.has_scheme, base_parts.buf, base_parts.scheme_start, base_parts.scheme_end, 1,
                           ref_parts.buf, ref_parts.netloc_start, ref_parts.netloc_end, ref_parts.buf,
                           ref_parts.path_start, ref_parts.path_end, ref_parts.has_query, ref_parts.buf,
                           ref_parts.query_start, ref_parts.query_end, ref_parts.has_fragment, ref_parts.buf,
                           ref_parts.fragment_start, ref_parts.fragment_end);
    } else if (ref_parts.path_end == ref_parts.path_start) {
        int query_has = ref_parts.has_query;
        Py_ssize_t query_start = ref_parts.query_start;
        Py_ssize_t query_end = ref_parts.query_end;
        const Py_UCS4 *query_buf = ref_parts.buf;
        int fragment_has = ref_parts.has_fragment;
        Py_ssize_t fragment_start = ref_parts.fragment_start;
        Py_ssize_t fragment_end = ref_parts.fragment_end;
        const Py_UCS4 *fragment_buf = ref_parts.buf;
        if (!ref_parts.has_query) {
            query_has = base_parts.has_query;
            query_start = base_parts.query_start;
            query_end = base_parts.query_end;
            query_buf = base_parts.buf;
            if (!ref_parts.has_fragment) {
                fragment_has = base_parts.has_fragment;
                fragment_start = base_parts.fragment_start;
                fragment_end = base_parts.fragment_end;
                fragment_buf = base_parts.buf;
            }
        }
        result = build_url(base_parts.has_scheme, base_parts.buf, base_parts.scheme_start, base_parts.scheme_end,
                           base_parts.has_netloc, base_parts.buf, base_parts.netloc_start, base_parts.netloc_end,
                           base_parts.buf, base_parts.path_start, base_parts.path_end, query_has, query_buf,
                           query_start, query_end, fragment_has, fragment_buf, fragment_start, fragment_end);
    } else {
        Py_ssize_t base_len = base_parts.path_end - base_parts.path_start;
        Py_ssize_t ref_len = ref_parts.path_end - ref_parts.path_start;
        Py_ssize_t seg_cap = base_len + ref_len + 4;
        url_seg *scratch = PyMem_Malloc((size_t)seg_cap * sizeof(url_seg));
        url_seg *resolved = PyMem_Malloc((size_t)seg_cap * sizeof(url_seg));
        Py_UCS4 *path = PyMem_Malloc((size_t)(base_len + ref_len + 4) * sizeof(Py_UCS4));
        if (scratch == NULL || resolved == NULL || path == NULL) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
            PyMem_Free(scratch);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(resolved);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(path);                                      /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(base_parts.buf);                            /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(ref_parts.buf);                             /* GCOVR_EXCL_LINE: allocation-failure path */
            return PyErr_NoMemory();                               /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t path_len = merge_path(&base_parts, &ref_parts, scratch, resolved, path);
        result = build_url(base_parts.has_scheme, base_parts.buf, base_parts.scheme_start, base_parts.scheme_end,
                           base_parts.has_netloc, base_parts.buf, base_parts.netloc_start, base_parts.netloc_end, path,
                           0, path_len, ref_parts.has_query, ref_parts.buf, ref_parts.query_start, ref_parts.query_end,
                           ref_parts.has_fragment, ref_parts.buf, ref_parts.fragment_start, ref_parts.fragment_end);
        PyMem_Free(scratch);
        PyMem_Free(resolved);
        PyMem_Free(path);
    }
    PyMem_Free(base_parts.buf);
    PyMem_Free(ref_parts.buf);
    return result;
}

/* _url_join(base, target): the shim's relative-reference resolver, replacing urllib.parse.urljoin. Both arguments are
   str; returns the joined URL, or raises ValueError when a component cannot be split. */
PyObject *turbohtml_url_join(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *base;
    PyObject *target;
    if (!PyArg_ParseTuple(args, "UU", &base, &target)) {
        return NULL;
    }
    return th_url_join(base, target);
}
