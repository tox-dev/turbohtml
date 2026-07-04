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

typedef struct {
    Py_ssize_t user_start, user_end;
    Py_ssize_t host_start, host_end;
    Py_ssize_t port_start, port_end;
    int has_port;
    int kind;
} authority;

/* Decompose the authority work[start,end) into userinfo (before the last '@'), host, and port. A '['-led host is an
   IPv6 literal reported without its brackets; a host of only ASCII digits and dots is an IPv4 literal; anything else is
   a registered name. The kind only tells the shim which hosts skip IDNA, so this is a literal-shape test, not a full
   address parse -- IPv4/IPv6 canonicalization is a later step. */
static void parse_authority(const Py_UCS4 *work, Py_ssize_t start, Py_ssize_t end, authority *out) {
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
    if (len > 0 && ((work[0] >= 'a' && work[0] <= 'z') || (work[0] >= 'A' && work[0] <= 'Z'))) {
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
    authority auth;
    parse_authority(work, netloc_start, netloc_end, &auth);
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
