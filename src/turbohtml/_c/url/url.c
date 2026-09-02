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

/* th_url_percent_encode_obj(text, set_id): the per-component encoder body, exposed so the query normalizer can encode a
   surviving pair without rebuilding an argument tuple. It UTF-8 encodes the component (a lone surrogate has no
   encoding, so this raises the UnicodeEncodeError PyUnicode_AsUTF8AndSize sets), then rewrites an existing %XX to
   uppercase hex and percent-encodes every byte outside the set. Since '%' stays in every keep set, a valid escape
   survives; a stray '%' or a truncated %X is a literal byte the set keeps. */
PyObject *th_url_percent_encode_obj(PyObject *text, int set_id) {
    Py_ssize_t len;
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

/* _url_percent_encode(text, set_id): the shim's per-component encoder, replacing urllib.parse.quote plus a preceding
   uppercase-escape sweep. A lone surrogate has no encoding, so this raises; the shim rewraps that UnicodeEncodeError.
 */
PyObject *turbohtml_url_percent_encode(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int set_id;
    if (!PyArg_ParseTuple(args, "Ui", &text, &set_id)) {
        return NULL;
    }
    return th_url_percent_encode_obj(text, set_id);
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

/* th_url_percent_decode_obj(text): the per-component decoder body, exposed so the query normalizer and language filter
   can decode a key span without an argument tuple. It walks the string, turning a %XX inside an ASCII run into its byte
   and keeping any other ASCII char or non-ASCII code point verbatim, then UTF-8 decodes each ASCII byte run with U+FFFD
   replacement. A non-ASCII input char ends the current run, matching the ascii/non-ascii split unquote makes, so a raw
   code point (even a lone surrogate) survives unencoded. */
PyObject *th_url_percent_decode_obj(PyObject *arg) {
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

/* _url_percent_decode(text): the shim's per-component decoder, replacing urllib.parse.unquote. */
PyObject *turbohtml_url_percent_decode(PyObject *Py_UNUSED(module), PyObject *arg) {
    return th_url_percent_decode_obj(arg);
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
int th_url_split(PyObject *arg, th_url_parts *out) {
    Py_ssize_t raw_len = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    Py_UCS4 *work = PyMem_Malloc((size_t)(raw_len + 1) * sizeof(Py_UCS4));
    if (work == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
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
            return -1;
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
    for (int index = 0; index < TH_URL_PART_COUNT; index++) {
        out->part[index] = span_str(work, spans[index][0], spans[index][1]);
        if (out->part[index] == NULL) {      /* GCOVR_EXCL_BR_LINE: only span_str allocation can fail */
            while (index-- > 0) {            /* GCOVR_EXCL_LINE: allocation-failure unwind */
                Py_DECREF(out->part[index]); /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE */
            PyMem_Free(work); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
    }
    out->has_port = auth.has_port;
    out->kind = auth.kind;
    PyMem_Free(work);
    return 0;
}

void th_url_parts_clear(th_url_parts *parts) {
    for (int index = 0; index < TH_URL_PART_COUNT; index++) {
        Py_DECREF(parts->part[index]);
    }
}

PyObject *turbohtml_url_split(PyObject *Py_UNUSED(module), PyObject *arg) {
    th_url_parts parts;
    if (th_url_split(arg, &parts) < 0) {
        return NULL;
    }
    PyObject *result =
        Py_BuildValue("(OOOOOOOONi)", parts.part[0], parts.part[1], parts.part[2], parts.part[3], parts.part[4],
                      parts.part[5], parts.part[6], parts.part[7], PyBool_FromLong(parts.has_port), parts.kind);
    th_url_parts_clear(&parts);
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

/* The tracking-parameter vocabulary. A crawl-oriented cleaner drops these query keys because they identify the
   referral, not the content, so two URLs differing only in them address the same page. Kept sorted so the exact-name
   test is a binary search. */
static const char *const TRACKER_NAMES[] = {
    "clickid", "dclid",  "efid",   "epik",   "fb_ref",   "fb_source", "fbclid",    "gbraid",
    "gclid",   "gclsrc", "igsh",   "igshid", "mkt_tok",  "msclkid",   "partnerid", "s_cid",
    "sc_cid",  "ttclid", "twclid", "wbraid", "wickedid", "yclid",     "ysclid",
};

static const char *const TRACKER_PREFIXES[] = {
    "ad_", "ads_", "ga_", "gs_", "hsa_", "itm_", "mc_", "mtm_", "oly_", "pk_", "utm_", "vero_",
};

/* The words a tracking key is built from, matched as a whole underscore-delimited word rather than a substring so
   "reference" is not read as "ref". */
static const char *const TRACKER_WORDS[] = {
    "aff", "affi",  "affiliate", "campaign", "cid",     "clid",   "keyword", "kwd",  "medium",
    "ref", "refer", "referer",   "referrer", "session", "source", "uid",     "xtor",
};

static int tracker_name_known(const char *key, size_t len) {
    size_t low = 0;
    size_t high = sizeof(TRACKER_NAMES) / sizeof(TRACKER_NAMES[0]);
    while (low < high) {
        size_t mid = low + (high - low) / 2;
        int order = strncmp(TRACKER_NAMES[mid], key, len);
        if (order == 0 && TRACKER_NAMES[mid][len] != '\0') {
            order = 1;
        }
        if (order == 0) {
            return 1;
        }
        if (order < 0) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    return 0;
}

static int tracker_word_at(const char *key, size_t start, size_t end) {
    size_t len = end - start;
    for (size_t index = 0; index < sizeof(TRACKER_WORDS) / sizeof(TRACKER_WORDS[0]); index++) {
        if (strlen(TRACKER_WORDS[index]) == len && strncmp(TRACKER_WORDS[index], key + start, len) == 0) {
            return 1;
        }
    }
    return 0;
}

/* th_url_is_tracker_obj(key): whether a lowercased query-parameter name identifies a referral rather than content;
   returns 1 for a tracker, 0 otherwise, and -1 with a UnicodeEncodeError set when the key carries a lone surrogate (a
   raw surrogate the caller's URL held, which has no UTF-8 form). Exposed so the query normalizer decides a pair without
   materializing a Py_True/Py_False per key. */
int th_url_is_tracker_obj(PyObject *key_obj) {
    Py_ssize_t size;
    const char *key = PyUnicode_AsUTF8AndSize(key_obj, &size);
    if (key == NULL) {
        return -1;
    }
    size_t len = (size_t)size;
    if (tracker_name_known(key, len)) {
        return 1;
    }
    for (size_t index = 0; index < sizeof(TRACKER_PREFIXES) / sizeof(TRACKER_PREFIXES[0]); index++) {
        size_t plen = strlen(TRACKER_PREFIXES[index]);
        if (len >= plen && strncmp(key, TRACKER_PREFIXES[index], plen) == 0) {
            return 1;
        }
    }
    if (len >= 4 && strncmp(key + len - 4, "clid", 4) == 0) {
        return 1;
    }
    /* each underscore-delimited word, so a tracking word anywhere in the name matches but a longer word containing
       one does not */
    size_t start = 0;
    for (size_t index = 0; index <= len; index++) {
        if (index == len || key[index] == '_') {
            if (tracker_word_at(key, start, index)) {
                return 1;
            }
            start = index + 1;
        }
    }
    return 0;
}

/* _url_is_tracker(key): whether a lowercased query-parameter name identifies a referral rather than content. */
PyObject *turbohtml_url_is_tracker(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "_url_is_tracker() argument must be str");
        return NULL;
    }
    int tracker = th_url_is_tracker_obj(arg);
    if (tracker < 0) { /* a lone-surrogate key has no UTF-8 form, so the UnicodeEncodeError propagates */
        return NULL;
    }
    return PyBool_FromLong(tracker);
}

/* Whether the segment work[start,end) spells "." or ".." once its %2E escapes are read as dots; `dots` receives the
   dot count so the caller can tell the two apart. A segment of anything else answers zero. */
static int dot_segment(const Py_UCS4 *work, Py_ssize_t start, Py_ssize_t end, int *dots) {
    int count = 0;
    for (Py_ssize_t index = start; index < end;) {
        if (work[index] == '.') {
            index += 1;
        } else if (index + 2 < end && work[index] == '%' && work[index + 1] == '2' &&
                   (work[index + 2] == 'E' || work[index + 2] == 'e')) {
            index += 3;
        } else {
            return 0;
        }
        count += 1;
        if (count > 2) {
            return 0;
        }
    }
    *dots = count;
    return count == 1 || count == 2;
}

/* _url_remove_dot_segments(path): resolve the "." and ".." segments of a path, in either their literal or %2E
   spelling, keeping every other segment's encoding verbatim. */
PyObject *turbohtml_url_remove_dot_segments(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "_url_remove_dot_segments() argument must be str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    Py_UCS4 *work = PyMem_Malloc((size_t)(len + 1) * sizeof(Py_UCS4));
    if (work == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int dotted = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        work[index] = PyUnicode_READ(kind, data, index);
        if (work[index] == '.' || work[index] == '%') {
            dotted = 1;
        }
    }
    if (!dotted) { /* no segment can be dotted, so the path resolves to itself */
        PyMem_Free(work);
        return Py_NewRef(arg);
    }
    /* segment starts, so popping a ".." is dropping the last recorded start */
    Py_ssize_t *starts = PyMem_Malloc((size_t)(len + 2) * sizeof(Py_ssize_t));
    Py_ssize_t *ends = PyMem_Malloc((size_t)(len + 2) * sizeof(Py_ssize_t));
    if (starts == NULL || ends == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(work);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(starts);               /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(ends);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t kept = 0;
    Py_ssize_t start = 0;
    int last_dots = 0;
    for (Py_ssize_t index = 0; index <= len; index++) {
        if (index != len && work[index] != '/') {
            continue;
        }
        last_dots = 0;
        int single = dot_segment(work, start, index, &last_dots);
        if (single && last_dots == 1) {
            last_dots = 1;
        } else if (single) {
            /* a ".." drops the previous segment, but never the leading empty one a rooted path opens with */
            if (kept > 1) {
                kept -= 1;
            }
        } else {
            starts[kept] = start;
            ends[kept] = index;
            kept += 1;
            last_dots = 0;
        }
        start = index + 1;
    }
    if (last_dots != 0) { /* a trailing dot segment leaves the path ending in a separator */
        starts[kept] = 0;
        ends[kept] = 0;
        kept += 1;
    }
    Py_ssize_t total = kept - 1; /* kept >= 1: the final segment always records one entry or bumps the trailing dot */
    for (Py_ssize_t index = 0; index < kept; index++) {
        total += ends[index] - starts[index];
    }
    Py_UCS4 *out = PyMem_Malloc((size_t)(total + 1) * sizeof(Py_UCS4));
    if (out == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(work);        /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(starts);      /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(ends);        /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < kept; index++) {
        if (index != 0) {
            out[at++] = '/';
        }
        for (Py_ssize_t scan = starts[index]; scan < ends[index]; scan++) {
            out[at++] = work[scan];
        }
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, out, at);
    PyMem_Free(work);
    PyMem_Free(starts);
    PyMem_Free(ends);
    PyMem_Free(out);
    return result;
}

/* _url_scrub(url): undo the HTML transport damage a scraped URL carries before it is split. It strips the leading and
   trailing C0-control and space bytes the WHATWG basic parser removes (spec 4.4 steps 1-2), drops every whitespace
   character str.split() would so a URL broken across lines rejoins, unwraps a <![CDATA[...]]> wrapper, truncates at the
   first '<', '>', or '"' a stray markup delimiter left, undoes a leftover &amp; escape, and drops a trailing '&' left
   dangling after a '/'. Replaces the Python scrub's strip/split/join, regex split, and replace chain, run once per URL.
 */
PyObject *turbohtml_url_scrub(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "_url_scrub() argument must be str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    /* strip(_C0_AND_SPACE): the edge run of code points at or below U+0020, which includes the non-whitespace C0
       controls str.split() would keep in the middle */
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && PyUnicode_READ(kind, data, start) <= 0x20) {
        start++;
    }
    while (end > start && PyUnicode_READ(kind, data, end - 1) <= 0x20) {
        end--;
    }
    Py_UCS4 *work = PyMem_Malloc((size_t)(end - start + 1) * sizeof(Py_UCS4));
    if (work == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* "".join(...split()): drop every whitespace code point, leaving the non-whitespace runs concatenated */
    Py_ssize_t count = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (!Py_UNICODE_ISSPACE(ch)) {
            work[count++] = ch;
        }
    }
    Py_ssize_t lo = 0;
    Py_ssize_t hi = count;
    static const Py_UCS4 CDATA_OPEN[] = {'<', '!', '[', 'C', 'D', 'A', 'T', 'A', '['};
    Py_ssize_t cdata_len = (Py_ssize_t)(sizeof(CDATA_OPEN) / sizeof(CDATA_OPEN[0]));
    int is_cdata = hi - lo >= cdata_len;
    for (Py_ssize_t index = 0; is_cdata && index < cdata_len; index++) {
        is_cdata = work[lo + index] == CDATA_OPEN[index];
    }
    if (is_cdata) {
        lo += cdata_len;
        if (hi - lo >= 3 && work[hi - 3] == ']' && work[hi - 2] == ']' && work[hi - 1] == '>') {
            hi -= 3; /* removesuffix("]]>") applies only after the prefix was stripped */
        }
    }
    for (Py_ssize_t index = lo; index < hi; index++) { /* truncate at the first markup delimiter, the regex split */
        Py_UCS4 ch = work[index];
        if (ch == '<' || ch == '>' || ch == '"') {
            hi = index;
            break;
        }
    }
    /* replace("&amp;", "&") in place: the write cursor never overtakes the read cursor, so no second buffer is needed
     */
    Py_ssize_t at = 0;
    for (Py_ssize_t index = lo; index < hi;) {
        if (hi - index >= 5 && work[index] == '&' && work[index + 1] == 'a' && work[index + 2] == 'm' &&
            work[index + 3] == 'p' && work[index + 4] == ';') {
            work[at++] = '&';
            index += 5;
        } else {
            work[at++] = work[index];
            index++;
        }
    }
    if (at >= 2 && work[at - 2] == '/' && work[at - 1] == '&') {
        at -= 1; /* removesuffix("&") on a "/&" tail, the dangling separator a stripped &amp; can leave */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, work, at);
    PyMem_Free(work);
    return result;
}

/* _url_variant_key(url): collapse the scheme and a bare trailing slash so the http/https and slash twins of one address
   deduplicate. The key is everything after the first "://" (nothing when the URL carries no authority marker); a key
   holding a '?' or '#' keeps its trailing slashes, since there a slash is content, otherwise the trailing run is
   trimmed. Runs once per surviving link in the extract_links dedup walk. */
PyObject *turbohtml_url_variant_key(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "_url_variant_key() argument must be str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    Py_ssize_t start = len; /* the partition("://")[2] start, or len (an empty remainder) when the marker is absent */
    for (Py_ssize_t index = 0; index + 2 < len; index++) {
        if (PyUnicode_READ(kind, data, index) == ':' && PyUnicode_READ(kind, data, index + 1) == '/' &&
            PyUnicode_READ(kind, data, index + 2) == '/') {
            start = index + 3;
            break;
        }
    }
    Py_ssize_t end = len;
    int keep_slashes = 0;
    for (Py_ssize_t index = start; index < len; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (ch == '?' || ch == '#') {
            keep_slashes = 1;
            break;
        }
    }
    if (!keep_slashes) {
        while (end > start && PyUnicode_READ(kind, data, end - 1) == '/') {
            end--;
        }
    }
    return PyUnicode_Substring(arg, start, end);
}

/* Percent-encode one component for its set, rewrapping the encoder's UnicodeEncodeError as the ValueError
   normalize_url raises, so a lone surrogate reports the "cannot be percent-encoded" message with the component. */
PyObject *th_url_encode_component(PyObject *text, int set_id) {
    PyObject *encoded = th_url_percent_encode_obj(text, set_id);
    if (encoded != NULL) {
        return encoded;
    }
    PyObject *type;
    PyObject *value;
    PyObject *traceback;
    PyErr_Fetch(&type, &value, &traceback);
    PyErr_NormalizeException(&type, &value, &traceback);
    PyObject *reason = PyObject_GetAttrString(value, "reason"); /* UnicodeEncodeError always carries a reason str */
    PyErr_Format(PyExc_ValueError, "URL component %R has a character that cannot be percent-encoded: %U", text, reason);
    Py_XDECREF(reason);
    Py_DECREF(type);
    Py_DECREF(value);
    Py_XDECREF(traceback);
    return NULL;
}

/* Percent-encode the query pair query[start:end) for the query set. */
static PyObject *encode_pair(PyObject *query, Py_ssize_t start, Py_ssize_t end) {
    PyObject *pair = PyUnicode_Substring(query, start, end);
    if (pair == NULL) { /* GCOVR_EXCL_BR_LINE: substring of an existing string cannot fail here */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *encoded = th_url_encode_component(pair, TH_URL_SET_QUERY);
    Py_DECREF(pair);
    return encoded;
}

/* _url_normalize_query(query, allow, deny, strict, content, language): drop denied, tracker, or non-allowlisted query
   parameters, encode each survivor for the query set, and sort them, the per-pair loop the crawl cleaner ran in Python.
   `allow` is a lowercased name set or None (keep only these), `deny` a lowercased name set (always drop); `strict`
   keeps only the `content` and `language` parameter names. Each pair's key is percent-decoded and lowercased for the
   filter, while the kept pair keeps its raw encoding. Runs once per URL query (and per query-shaped fragment). */
PyObject *turbohtml_url_normalize_query(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *query;
    PyObject *allow;
    PyObject *deny;
    int strict;
    PyObject *content;
    PyObject *language;
    if (!PyArg_ParseTuple(args, "UOOpOO", &query, &allow, &deny, &strict, &content, &language)) {
        return NULL;
    }
    return th_url_normalize_query(query, allow, deny, strict, content, language);
}

PyObject *th_url_normalize_query(PyObject *query, PyObject *allow, PyObject *deny, int strict, PyObject *content,
                                 PyObject *language) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(query);
    int kind = PyUnicode_KIND(query);
    const void *data = PyUnicode_DATA(query);
    PyObject *kept = PyList_New(0);
    if (kept == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t pair_start = 0;
    for (Py_ssize_t index = 0; index <= len; index++) {
        if (index != len && PyUnicode_READ(kind, data, index) != '&') {
            continue;
        }
        if (index == pair_start) { /* an empty pair, which "".split("&") drops */
            pair_start = index + 1;
            continue;
        }
        Py_ssize_t key_end = pair_start;
        while (key_end < index && PyUnicode_READ(kind, data, key_end) != '=') {
            key_end++;
        }
        PyObject *raw_key = PyUnicode_Substring(query, pair_start, key_end);
        if (raw_key == NULL) { /* GCOVR_EXCL_BR_LINE: substring of an existing string cannot fail here */
            goto error;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *decoded = th_url_percent_decode_obj(raw_key);
        Py_DECREF(raw_key);
        if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: decode only fails on the excluded allocation path */
            goto error;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *key = PyObject_CallMethod(decoded, "lower", NULL);
        Py_DECREF(decoded);
        if (key == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a decoded key */
            goto error;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int drop = PySet_Contains(deny, key);
        if (drop == 0) {
            if (allow != Py_None) {
                drop = !PySet_Contains(allow, key);
            } else if (strict) {
                drop = !(PySet_Contains(content, key) || PySet_Contains(language, key));
            } else {
                drop = th_url_is_tracker_obj(key);
            }
        }
        if (drop < 0) { /* the tracker test hit a lone-surrogate key with no UTF-8 form; propagate its error */
            Py_DECREF(key);
            goto error;
        }
        if (drop == 0) {
            PyObject *encoded = encode_pair(query, pair_start, index);
            if (encoded == NULL) {
                Py_DECREF(key);
                goto error;
            }
            /* sort each survivor by (lowercased key, encoded pair), the tuple order sorted(kept) compares */
            PyObject *pair = PyTuple_Pack(2, key, encoded);
            Py_DECREF(encoded);
            if (pair == NULL || PyList_Append(kept, pair) < 0) { /* GCOVR_EXCL_BR_LINE: append cannot fail here */
                Py_XDECREF(pair);                                /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(key);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
                goto error;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            Py_DECREF(pair);
        }
        Py_DECREF(key);
        pair_start = index + 1;
    }
    if (PyList_Sort(kept) < 0) { /* GCOVR_EXCL_BR_LINE: a list of (str, str) tuples sorts without error */
        goto error;              /* GCOVR_EXCL_LINE: sort-failure path */
    }
    Py_ssize_t survivors = PyList_GET_SIZE(kept);
    PyObject *values = PyList_New(survivors);
    if (values == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        goto error;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < survivors; index++) {
        PyObject *encoded = PyTuple_GET_ITEM(PyList_GET_ITEM(kept, index), 1);
        Py_INCREF(encoded);
        PyList_SET_ITEM(values, index, encoded);
    }
    Py_DECREF(kept);
    PyObject *separator = PyUnicode_FromString("&");
    if (separator == NULL) { /* GCOVR_EXCL_BR_LINE: interning "&" cannot fail here */
        Py_DECREF(values);   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_Join(separator, values);
    Py_DECREF(separator);
    Py_DECREF(values);
    return result;
error:
    Py_DECREF(kept);
    return NULL;
}

/* An ASCII lowercase letter, the [a-z] the language-segment pattern matches (the leading path segment is already
   lowercased, so an uppercase or non-ASCII code point fails and the segment is not a language marker). */
static int is_ascii_lower(Py_UCS4 ch) {
    return ch >= 'a' && ch <= 'z';
}

/* The two-letter language code the leading path segment carries, or -1 for a segment that is not a language marker.
   Mirrors _LANGUAGE_SEGMENT.fullmatch: exactly two lowercase letters, optionally followed by a '-'/'_' separator and a
   two- or three-letter subtag, with nothing else. `code` receives the first two code points on a match. */
static int language_segment_code(const Py_UCS4 *segment, Py_ssize_t len, Py_UCS4 code[2]) {
    if (len != 2 && len != 5 && len != 6) {
        return -1;
    }
    if (!is_ascii_lower(segment[0]) || !is_ascii_lower(segment[1])) {
        return -1;
    }
    if (len != 2) {
        if (segment[2] != '-' && segment[2] != '_') {
            return -1;
        }
        for (Py_ssize_t index = 3; index < len; index++) {
            if (!is_ascii_lower(segment[index])) {
                return -1;
            }
        }
    }
    code[0] = segment[0];
    code[1] = segment[1];
    return 0;
}

/* Whether the two-letter code[2] is a known ISO 639-1 language that differs from `language`, so a URL marked for it
   points at another language and the filter rejects the URL. Returns 1 to reject, 0 to keep, -1 on error. */
static int language_code_rejects(const Py_UCS4 code[2], PyObject *language, PyObject *iso_639_1) {
    PyObject *code_obj = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, code, 2);
    if (code_obj == NULL) { /* GCOVR_EXCL_BR_LINE: a two-code-point string cannot fail to build */
        return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int known = PySet_Contains(iso_639_1, code_obj);
    int differs = known > 0 ? PyUnicode_Compare(code_obj, language) != 0 : 0;
    Py_DECREF(code_obj);
    if (known < 0) { /* GCOVR_EXCL_BR_LINE: a two-letter str is hashable, so membership cannot error */
        return -1;   /* GCOVR_EXCL_LINE: membership-failure path */
    }
    return known > 0 && differs;
}

/* Whether the query carries a lang/language parameter naming a different language, so the URL is rejected. Returns 1 to
   reject, 0 to keep, -1 on error. */
static int language_query_rejects(PyObject *query, PyObject *language, PyObject *language_params) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(query);
    int kind = PyUnicode_KIND(query);
    const void *data = PyUnicode_DATA(query);
    Py_ssize_t pair_start = 0;
    for (Py_ssize_t index = 0; index <= len; index++) {
        if (index != len && PyUnicode_READ(kind, data, index) != '&') {
            continue;
        }
        Py_ssize_t equals = pair_start;
        while (equals < index && PyUnicode_READ(kind, data, equals) != '=') {
            equals++;
        }
        if (equals < index) { /* a pair with a '=' separator, the only shape carrying a language value */
            PyObject *raw_key = PyUnicode_Substring(query, pair_start, equals);
            if (raw_key == NULL) { /* GCOVR_EXCL_BR_LINE: substring of an existing string cannot fail here */
                return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            PyObject *decoded_key = th_url_percent_decode_obj(raw_key);
            Py_DECREF(raw_key);
            if (decoded_key == NULL) { /* GCOVR_EXCL_BR_LINE: decode only fails on the excluded allocation path */
                return -1;             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            PyObject *key = PyObject_CallMethod(decoded_key, "lower", NULL);
            Py_DECREF(decoded_key);
            if (key == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a decoded key */
                return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int is_language = PySet_Contains(language_params, key);
            Py_DECREF(key);
            if (is_language < 0) { /* GCOVR_EXCL_BR_LINE: a str key is hashable, so membership cannot error */
                return -1;         /* GCOVR_EXCL_LINE: membership-failure path */
            }
            if (is_language) {
                PyObject *raw_value = PyUnicode_Substring(query, equals + 1, index);
                if (raw_value == NULL) { /* GCOVR_EXCL_BR_LINE: substring of an existing string cannot fail here */
                    return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                PyObject *decoded_value = th_url_percent_decode_obj(raw_value);
                Py_DECREF(raw_value);
                if (decoded_value == NULL) { /* GCOVR_EXCL_BR_LINE: decode only fails on the excluded alloc path */
                    return -1;               /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                PyObject *code = PyObject_CallMethod(decoded_value, "lower", NULL);
                Py_DECREF(decoded_value);
                if (code == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a decoded value */
                    return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                Py_ssize_t code_len = PyUnicode_GET_LENGTH(code);
                int mismatched = code_len > 0 && PyUnicode_Tailmatch(code, language, 0, code_len, -1) == 0;
                Py_DECREF(code);
                if (mismatched) {
                    return 1;
                }
            }
        }
        pair_start = index + 1;
    }
    return 0;
}

/* _url_language_matches(query, path, hostname, language, strict, language_params, iso_639_1): judge a URL's own
   language markers against the target `language`, the URL-based heuristics clean_url's language filter runs. A
   lang/language query parameter, a leading path segment that is an ISO 639-1 tag, and -- in strict mode -- a two-letter
   host label each reject the URL when they name a different language. `path` and `query` are raw components; `hostname`
   is already lowercased. Returns True to keep, False to reject. Runs once per URL, only when a language filter is
   active. */
PyObject *turbohtml_url_language_matches(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *query;
    PyObject *path;
    PyObject *hostname;
    PyObject *language;
    int strict;
    PyObject *language_params;
    PyObject *iso_639_1;
    if (!PyArg_ParseTuple(args, "UUUUpOO", &query, &path, &hostname, &language, &strict, &language_params,
                          &iso_639_1)) {
        return NULL;
    }
    int matches = th_url_language_matches(query, path, hostname, language, strict, language_params, iso_639_1);
    if (matches < 0) { /* GCOVR_EXCL_BR_LINE: the filter only fails on the excluded alloc paths */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return PyBool_FromLong(matches);
}

int th_url_language_matches(PyObject *query, PyObject *path, PyObject *hostname, PyObject *language, int strict,
                            PyObject *language_params, PyObject *iso_639_1) {
    int rejected = language_query_rejects(query, language, language_params);
    if (rejected < 0) { /* GCOVR_EXCL_BR_LINE: the decode/lower/membership steps only fail on the excluded alloc path */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (rejected) {
        return 0;
    }
    PyObject *lowered = PyObject_CallMethod(path, "lower", NULL);
    if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a path component */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(lowered);
    int kind = PyUnicode_KIND(lowered);
    const void *data = PyUnicode_DATA(lowered);
    Py_ssize_t leading_start = 0;
    while (leading_start < len && PyUnicode_READ(kind, data, leading_start) == '/') {
        leading_start++; /* skip empty segments so the first non-empty path segment is the language marker */
    }
    Py_ssize_t leading_end = leading_start;
    while (leading_end < len && PyUnicode_READ(kind, data, leading_end) != '/') {
        leading_end++;
    }
    Py_UCS4 segment[6];
    Py_ssize_t leading_len = leading_end - leading_start;
    Py_UCS4 code[2];
    int rejects = 0;
    if (leading_len >= 2 && leading_len <= 6) {
        for (Py_ssize_t index = 0; index < leading_len; index++) {
            segment[index] = PyUnicode_READ(kind, data, leading_start + index);
        }
        if (language_segment_code(segment, leading_len, code) == 0) {
            rejects = language_code_rejects(code, language, iso_639_1);
        }
    }
    Py_DECREF(lowered);
    if (rejects < 0) { /* GCOVR_EXCL_BR_LINE: building a two-letter code and testing membership cannot error */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (rejects) {
        return 0;
    }
    if (strict) {
        Py_ssize_t host_len = PyUnicode_GET_LENGTH(hostname);
        int host_kind = PyUnicode_KIND(hostname);
        const void *host_data = PyUnicode_DATA(hostname);
        Py_ssize_t label_end = 0;
        while (label_end < host_len && PyUnicode_READ(host_kind, host_data, label_end) != '.') {
            label_end++;
        }
        if (label_end == 2) {
            code[0] = PyUnicode_READ(host_kind, host_data, 0);
            code[1] = PyUnicode_READ(host_kind, host_data, 1);
            int label_rejects = language_code_rejects(code, language, iso_639_1);
            if (label_rejects < 0) { /* GCOVR_EXCL_BR_LINE: a two-letter code test cannot error */
                return -1;           /* GCOVR_EXCL_LINE: membership-failure path */
            }
            if (label_rejects) {
                return 0;
            }
        }
    }
    return 1;
}
