/* The Document root, the tree-owning handle, the push IncrementalParser, the parse()/parse_fragment()
   entry points, and the type registration that wires every node type onto the module. */

#include "dom/nodes.h"

#include <stdbool.h>

#include "encoding/encoding.h"
#include "encoding/decode.h"
#include "encoding/detect.h"
#include "encoding/language.h"
#include "url/url.h"

TH_NODE_API(static, PyObject *, document_get_root, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    NodeObject *node = (NodeObject *)self;
    /* a parsed document always has an html element child, so the loop never exhausts */
    th_node *child = node->node->first_child;
    /* allocation failure cannot be forced from a test */
    for (; child != NULL; child = child->next_sibling) { /* GCOVR_EXCL_BR_LINE */
        if (child->type == TH_NODE_ELEMENT) {
            return node_wrap(state_of(self), node->handle, child);
        }
    }
    Py_RETURN_NONE; /* GCOVR_EXCL_LINE: a parsed document always has an <html> element child */
}

TH_NODE_API(static, PyObject *, document_get_encoding, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    return Py_NewRef(((HandleObject *)((NodeObject *)self)->handle)->encoding);
}

/* The WHATWG confidence in Document.encoding. "certain" means the document said so: a
   byte-order mark, the encoding argument, or a <meta> declaration. "tentative" means the
   sniff guessed -- a structural UTF-8 read, the opt-in detector, or the windows-1252
   fallback -- and a scraper may want to second-guess it. None for str input, which was
   never decoded. */
TH_NODE_API(static, PyObject *, document_get_encoding_confidence, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    const HandleObject *handle = (HandleObject *)((NodeObject *)self)->handle;
    if (handle->encoding == Py_None) {
        Py_RETURN_NONE;
    }
    return PyUnicode_FromString(handle->encoding_certain ? "certain" : "tentative");
}

/* The scheme scanner's alphabets, complementing the WHATWG component percent-encode sets that now live in url.c: a
   scheme leads with a letter (URL_ALPHABET) and continues over letters, digits, and "+-." (URL_SCHEME_TAIL). */
static const char URL_ALPHABET[] = TH_URL_ALPHA;
static const char URL_SCHEME_TAIL[] = TH_URL_ALPHA "0123456789+-.";

/* Replace any lone surrogate with U+FFFD, the scalar-value substitution the URL parser's input goes through (Web IDL
   USVString), so the UTF-8 encode below cannot fail. Returns a new reference, the original when it is already scalar.
 */
static PyObject *url_scalarize(PyObject *url) {
    if (PyUnicode_IS_ASCII(url)) {
        return Py_NewRef(url);
    }
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    Py_ssize_t len = PyUnicode_GET_LENGTH(url);
    Py_ssize_t index = 0;
    while (index < len) {
        Py_UCS4 point = PyUnicode_READ(kind, data, index);
        if (point >= 0xD800 && point <= 0xDFFF) {
            break;
        }
        index++;
    }
    if (index == len) {
        return Py_NewRef(url); /* the common case: no surrogate, so no copy */
    }
    Py_UCS4 *buffer = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t at = 0; at < len; at++) {
        Py_UCS4 point = PyUnicode_READ(kind, data, at);
        buffer[at] = (point >= 0xD800 && point <= 0xDFFF) ? 0xFFFD : point;
    }
    PyObject *scalar = ucs4_to_str(buffer, len);
    PyMem_Free(buffer);
    return scalar; /* NULL on the excluded allocation-failure path */
}

static int url_has_encoded_dot(const char *bytes, Py_ssize_t length) {
    const char *end = bytes + length;
    while (bytes < end && (bytes = memchr(bytes, '%', (size_t)(end - bytes))) != NULL) {
        if (end - bytes >= 3 && bytes[1] == '2' && (bytes[2] | 0x20) == 'e') {
            return 1;
        }
        bytes++;
    }
    return 0;
}

static int url_has_literal_dot_path(const char *bytes, Py_ssize_t length) {
    const char *end = bytes + length;
    while (bytes < end && (bytes = memchr(bytes, '/', (size_t)(end - bytes))) != NULL) {
        bytes++;
        if (bytes < end && *bytes == '.') {
            return 1;
        }
    }
    return 0;
}

static inline int url_has_literal_dot_unicode(PyObject *url) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(url);
    if (length < 2) {
        return 0;
    }
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    for (Py_ssize_t index = 0; index + 1 < length; index++) {
        if (PyUnicode_READ(kind, data, index) == '/' && PyUnicode_READ(kind, data, index + 1) == '.') {
            return 1;
        }
    }
    return 0;
}

static Py_ssize_t url_find_encoded_dot_unicode(PyObject *url, Py_ssize_t length) {
    if (PyUnicode_IS_ASCII(url)) {
        const char *bytes = (const char *)PyUnicode_1BYTE_DATA(url);
        for (const char *found = bytes; found < bytes + length;) {
            found = memchr(found, '%', (size_t)(bytes + length - found));
            if (found == NULL) {
                break;
            }
            if (bytes + length - found >= 3 && found[1] == '2' && (found[2] | 0x20) == 'e') {
                return found - bytes;
            }
            found++;
        }
        return -1;
    }
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    for (Py_ssize_t index = 0; index + 2 < length; index++) {
        if (PyUnicode_READ(kind, data, index) == '%' && PyUnicode_READ(kind, data, index + 1) == '2' &&
            (PyUnicode_READ(kind, data, index + 2) | 0x20) == 'e') {
            return index;
        }
    }
    return -1;
}

static int url_special_scheme_bytes(const char *bytes, Py_ssize_t length) {
    static const char *const SPECIAL[] = {"http", "https", "ws", "wss", "ftp", "file"};
    for (size_t choice = 0; choice < sizeof(SPECIAL) / sizeof(SPECIAL[0]); choice++) {
        const char *scheme = SPECIAL[choice];
        if ((Py_ssize_t)strlen(scheme) != length) {
            continue;
        }
        Py_ssize_t index = 0;
        while (index < length && lower_ascii((unsigned char)bytes[index]) == (unsigned char)scheme[index]) {
            index++;
        }
        if (index == length) {
            return 1;
        }
    }
    return 0;
}

static int url_web_special_base(PyObject *base) {
    static const char *const SPECIAL[] = {"http", "https", "ws", "wss", "ftp"};
    Py_ssize_t length = PyUnicode_GET_LENGTH(base);
    int kind = PyUnicode_KIND(base);
    const void *data = PyUnicode_DATA(base);
    for (size_t choice = 0; choice < sizeof(SPECIAL) / sizeof(SPECIAL[0]); choice++) {
        const char *scheme = SPECIAL[choice];
        Py_ssize_t scheme_length = (Py_ssize_t)strlen(scheme);
        if (length <= scheme_length || PyUnicode_READ(kind, data, scheme_length) != ':') {
            continue;
        }
        Py_ssize_t index = 0;
        while (index < scheme_length &&
               lower_ascii(PyUnicode_READ(kind, data, index)) == (unsigned char)scheme[index]) {
            index++;
        }
        if (index == scheme_length) {
            return 1;
        }
    }
    return 0;
}

/* Encode the resolved URL's path, query, fragment, and userinfo brackets, preserving the raw host. */
static PyObject *url_percent_encode_slow(PyObject *url, int check_encoded_dot);

static PyObject *url_percent_encode(PyObject *url, int check_encoded_dot) {
    static const char ASCII_SAFE[] = TH_URL_ALPHA "0123456789-._~:/?&=#%";
    if (check_encoded_dot == 0 && PyUnicode_IS_ASCII(url) &&
        (Py_ssize_t)strspn((const char *)PyUnicode_1BYTE_DATA(url), ASCII_SAFE) == PyUnicode_GET_LENGTH(url)) {
        return Py_NewRef(url);
    }
    return url_percent_encode_slow(url, check_encoded_dot);
}

static PyObject *url_percent_encode_slow(PyObject *url, int check_encoded_dot) {
    PyObject *scalar = url_scalarize(url);
    if (scalar == NULL) { /* GCOVR_EXCL_BR_LINE: url_scalarize only fails on allocation */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t len;
    const char *bytes = PyUnicode_AsUTF8AndSize(scalar, &len);
    if (bytes == NULL) {   /* GCOVR_EXCL_BR_LINE: a scalarized string always UTF-8 encodes */
        Py_DECREF(scalar); /* GCOVR_EXCL_LINE: encode-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: encode-failure path */
    }
    Py_ssize_t cursor = 0;
    /* bytes is NUL-terminated, so bytes[0] on an empty URL reads the terminator and matches nothing */
    if (th_url_in_set((unsigned char)bytes[0], URL_ALPHABET, sizeof(URL_ALPHABET) - 1)) {
        Py_ssize_t scan = 1;
        while (scan < len && th_url_in_set((unsigned char)bytes[scan], URL_SCHEME_TAIL, sizeof(URL_SCHEME_TAIL) - 1)) {
            scan++;
        }
        if (scan < len && bytes[scan] == ':') {
            cursor = scan + 1; /* a scheme runs from an alpha up to its ':' */
        }
    }
    int special_query = cursor > 0 && url_special_scheme_bytes(bytes, cursor - 1);
    int hierarchical = bytes[cursor] == '/';
    int has_authority = cursor + 1 < len && bytes[cursor] == '/' && bytes[cursor + 1] == '/';
    int opaque_path = cursor > 0 && !hierarchical && !url_special_scheme_bytes(bytes, cursor - 1);
    Py_ssize_t authority_start = cursor;
    if (has_authority) {
        cursor += 2;
        authority_start = cursor;
        while (cursor < len && bytes[cursor] != '/' && bytes[cursor] != '?' && bytes[cursor] != '#') {
            cursor++;
        }
    }
    Py_ssize_t prefix_end = cursor;
    while (cursor < len && bytes[cursor] != '?' && bytes[cursor] != '#') {
        cursor++;
    }
    Py_ssize_t path_end = cursor;
    Py_ssize_t query_start = -1;
    if (cursor < len && bytes[cursor] == '?') {
        query_start = ++cursor;
        while (cursor < len && bytes[cursor] != '#') {
            cursor++;
        }
    }
    Py_ssize_t query_end = cursor;
    Py_ssize_t fragment_start = cursor < len ? cursor + 1 : -1;
    char *out = PyMem_Malloc((size_t)len * 3 + 1); /* every byte encodes to at most "%HH" */
    if (out == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(scalar); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t userinfo_end = -1;
    for (Py_ssize_t index = authority_start; index < prefix_end; index++) {
        if (bytes[index] == '@') {
            userinfo_end = index;
        }
    }
    if (userinfo_end >= 0) {
        int has_open = 0;
        int has_close = 0;
        for (Py_ssize_t index = userinfo_end + 1; index < prefix_end; index++) {
            has_open |= bytes[index] == '[';
            has_close |= bytes[index] == ']';
        }
        if (has_open != has_close) {
            PyMem_Free(out);
            Py_DECREF(scalar);
            PyErr_SetString(PyExc_ValueError, "Invalid IPv6 URL");
            return NULL;
        }
    }
    Py_ssize_t prefix_written = prefix_end;
    if (userinfo_end < 0) {
        memcpy(out, bytes, (size_t)prefix_end);
    } else {
        memcpy(out, bytes, (size_t)authority_start);
        prefix_written = authority_start;
        for (Py_ssize_t index = authority_start; index < userinfo_end; index++) {
            unsigned char byte = (unsigned char)bytes[index];
            if (byte == '[' || byte == ']') {
                out[prefix_written++] = '%';
                out[prefix_written++] = '5';
                out[prefix_written++] = byte == '[' ? 'B' : 'D';
            } else {
                out[prefix_written++] = (char)byte;
            }
        }
        memcpy(out + prefix_written, bytes + userinfo_end, (size_t)(prefix_end - userinfo_end));
        prefix_written += prefix_end - userinfo_end;
    }
    Py_ssize_t opaque_end = path_end;
    /* The URL parser encodes only the final opaque-path space before '?' or '#'. */
    if (opaque_path && (query_start >= 0 || fragment_start >= 0) && opaque_end > prefix_end &&
        bytes[opaque_end - 1] == ' ') {
        opaque_end--;
    }
    Py_ssize_t written = th_url_encode_span(out, prefix_written, bytes, prefix_end, opaque_end,
                                            opaque_path ? TH_URL_SET_OPAQUE_PATH : TH_URL_SET_PATH);
    for (Py_ssize_t index = opaque_end; index < path_end; index++) {
        out[written++] = '%';
        out[written++] = '2';
        out[written++] = '0';
    }
    int backslash_path = 0;
    if (memchr(bytes + prefix_end, '\\', (size_t)(path_end - prefix_end)) != NULL && url_web_special_base(scalar)) {
        for (Py_ssize_t index = prefix_written; index < written; index++) {
            if (out[index] == '\\') {
                out[index] = '/';
                backslash_path = 1;
            }
        }
    }
    if (hierarchical &&
        (check_encoded_dot == 2 ||
         (check_encoded_dot && (url_has_literal_dot_path(out + prefix_written, written - prefix_written) ||
                                url_has_encoded_dot(out + prefix_written, written - prefix_written))) ||
         (backslash_path && (memchr(out + prefix_written, '.', (size_t)(written - prefix_written)) != NULL ||
                             memchr(out + prefix_written, '%', (size_t)(written - prefix_written)) != NULL)))) {
        PyObject *path = PyUnicode_DecodeASCII(out + prefix_written, written - prefix_written, "strict");
        if (path == NULL) {    /* GCOVR_EXCL_BR_LINE: encoded path contains ASCII only */
            PyMem_Free(out);   /* GCOVR_EXCL_LINE: decode-failure path */
            Py_DECREF(scalar); /* GCOVR_EXCL_LINE: decode-failure path */
            return NULL;       /* GCOVR_EXCL_LINE: decode-failure path */
        }
        PyObject *normalized = turbohtml_url_remove_dot_segments(NULL, path);
        Py_DECREF(path);
        if (normalized == NULL) { /* GCOVR_EXCL_BR_LINE: only allocation can fail */
            PyMem_Free(out);      /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(scalar);    /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t normalized_length;
        const char *normalized_bytes = PyUnicode_AsUTF8AndSize(normalized, &normalized_length);
        memcpy(out + prefix_written, normalized_bytes, (size_t)normalized_length);
        written = prefix_written + normalized_length;
        Py_DECREF(normalized);
    }
    if (!special_query && !has_authority && authority_start > 0 && written - prefix_written >= 2 &&
        out[prefix_written] == '/' && out[prefix_written + 1] == '/') {
        memmove(out + prefix_written + 2, out + prefix_written, (size_t)(written - prefix_written));
        out[prefix_written] = '/';
        out[prefix_written + 1] = '.';
        written += 2;
    }
    if (query_start >= 0) {
        out[written++] = '?';
        written = th_url_encode_span(out, written, bytes, query_start, query_end,
                                     special_query ? TH_URL_SET_QUERY : TH_URL_SET_QUERY_NON_SPECIAL);
    }
    if (fragment_start >= 0) {
        out[written++] = '#';
        written = th_url_encode_span(out, written, bytes, fragment_start, len, TH_URL_SET_FRAGMENT);
    }
    PyObject *result = written == len && memcmp(out, bytes, (size_t)len) == 0
                           ? Py_NewRef(scalar)
                           : PyUnicode_DecodeUTF8(out, written, "strict");
    PyMem_Free(out);
    Py_DECREF(scalar);
    return result; /* NULL on the excluded decode-failure path */
}

static int url_has_file_scheme(PyObject *url);

static PyObject *url_join_opaque_fragment(PyObject *base, PyObject *target) {
    if (PyUnicode_GET_LENGTH(target) == 0 || PyUnicode_READ_CHAR(target, 0) != '#') {
        return NULL;
    }
    Py_ssize_t length = PyUnicode_GET_LENGTH(base);
    if (length < 2 || !th_scheme_start(PyUnicode_READ_CHAR(base, 0))) {
        return NULL;
    }
    Py_ssize_t colon = 1;
    while (colon < length && th_scheme_char(PyUnicode_READ_CHAR(base, colon))) {
        colon++;
    }
    if (colon == length || PyUnicode_READ_CHAR(base, colon) != ':' ||
        (colon + 1 < length && PyUnicode_READ_CHAR(base, colon + 1) == '/') || url_web_special_base(base) ||
        url_has_file_scheme(base)) {
        return NULL;
    }
    Py_ssize_t hash = PyUnicode_FindChar(base, '#', colon + 1, length, 1);
    PyObject *prefix = PyUnicode_Substring(base, 0, hash < 0 ? length : hash);
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: substring only fails on allocation */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *joined = PyUnicode_Concat(prefix, target);
    Py_DECREF(prefix);
    return joined;
}

static int url_has_file_scheme(PyObject *url) {
    if (PyUnicode_GET_LENGTH(url) < 5) {
        return 0;
    }
    static const char SCHEME[] = "file:";
    for (Py_ssize_t index = 0; index < 5; index++) {
        if (lower_ascii(PyUnicode_READ_CHAR(url, index)) != (Py_UCS4)(unsigned char)SCHEME[index]) {
            return 0;
        }
    }
    return 1;
}

static PyObject *url_join_document(PyObject *base, PyObject *target) {
    if (url_has_file_scheme(base) || url_has_file_scheme(target)) {
        return th_url_file_join(base, target);
    }
    return th_url_document_join(base, target);
}

static Py_ssize_t url_same_scheme_colon(PyObject *base, PyObject *target) {
    Py_ssize_t colon = PyUnicode_FindChar(base, ':', 0, PyUnicode_GET_LENGTH(base), 1);
    if (colon < 0 || PyUnicode_GET_LENGTH(target) <= colon || PyUnicode_READ_CHAR(target, colon) != ':') {
        return -1;
    }
    for (Py_ssize_t index = 0; index < colon; index++) {
        if (lower_ascii(PyUnicode_READ_CHAR(base, index)) != lower_ascii(PyUnicode_READ_CHAR(target, index))) {
            return -1;
        }
    }
    return colon;
}

static int url_has_backslash_dot_path(PyObject *target) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(target);
    if (PyUnicode_IS_ASCII(target) ? memchr(PyUnicode_1BYTE_DATA(target), '\\', (size_t)length) == NULL
                                   : PyUnicode_FindChar(target, '\\', 0, length, 1) < 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index + 1 < length; index++) {
        Py_UCS4 point = PyUnicode_READ_CHAR(target, index);
        if (point == '?' || point == '#') {
            break;
        }
        if (point == '\\' && PyUnicode_READ_CHAR(target, index + 1) == '.') {
            return 1;
        }
    }
    return 0;
}

static PyObject *url_join_web_special_slashes(PyObject *base, PyObject *target, int *literalized);

static PyObject *url_join_encoded_dot_segments(PyObject *base, PyObject *target, int *literalized) {
    PyObject *opaque_fragment = url_join_opaque_fragment(base, target);
    if (opaque_fragment != NULL) {
        *literalized = 0;
        return opaque_fragment;
    }
    if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the fragment prefix only fails on allocation */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *normalized_base = NULL;
    if (PyUnicode_GET_LENGTH(target) == 0 || PyUnicode_READ_CHAR(target, 0) != '/') {
        if (url_find_encoded_dot_unicode(base, PyUnicode_GET_LENGTH(base)) >= 0) {
            normalized_base = url_percent_encode(base, 1);
            if (normalized_base == NULL) { /* GCOVR_EXCL_BR_LINE: only allocation can fail */
                return NULL;               /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    PyObject *joined;
    *literalized = 0;
    Py_ssize_t target_len = PyUnicode_GET_LENGTH(target);
    Py_ssize_t encoded_dot = url_find_encoded_dot_unicode(target, target_len);
    int special =
        encoded_dot >= 0 &&
        (lower_ascii(PyUnicode_READ_CHAR(target, 0)) == 'h' || lower_ascii(PyUnicode_READ_CHAR(target, 0)) == 'f' ||
         lower_ascii(PyUnicode_READ_CHAR(target, 0)) == 'w') &&
        url_web_special_base(target);
    int path_encoded_dot = special && PyUnicode_FindChar(target, '?', 0, encoded_dot, 1) < 0 &&
                           PyUnicode_FindChar(target, '#', 0, encoded_dot, 1) < 0;
    Py_ssize_t colon = path_encoded_dot ? url_same_scheme_colon(base, target) : -1;
    if (colon >= 0 && th_url_web_authority(target) == TH_URL_WEB_AUTHORITY_NONE) {
        PyObject *relative = PyUnicode_Substring(target, colon + 1, target_len);
        if (relative == NULL) {          /* GCOVR_EXCL_BR_LINE: only allocation can fail */
            Py_XDECREF(normalized_base); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *resolved =
            PyUnicode_READ_CHAR(relative, 0) == '\\'
                ? url_join_web_special_slashes(normalized_base == NULL ? base : normalized_base, relative, literalized)
                : url_join_encoded_dot_segments(normalized_base == NULL ? base : normalized_base, relative,
                                                literalized);
        Py_XDECREF(normalized_base);
        Py_DECREF(relative);
        return resolved;
    }
    if (encoded_dot >= 0 && PyUnicode_READ_CHAR(target, 0) != '?' && PyUnicode_READ_CHAR(target, 0) != '#' &&
        (!special || path_encoded_dot)) {
        PyObject *literal_target = th_url_literalize_dot_segments(target);
        if (literal_target == NULL) {
            Py_XDECREF(normalized_base);
            return NULL;
        }
        *literalized = literal_target != target;
        if (!*literalized && special) {
            *literalized = 1;
        }
        joined = url_join_document(normalized_base == NULL ? base : normalized_base, literal_target);
        Py_DECREF(literal_target);
    } else {
        joined = url_join_document(normalized_base == NULL ? base : normalized_base, target);
    }
    Py_XDECREF(normalized_base);
    return joined;
}

static PyObject *url_join_web_special_slashes(PyObject *base, PyObject *target, int *literalized) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(target);
    if (!url_web_special_base(base) && !url_web_special_base(target)) {
        return url_join_encoded_dot_segments(base, target, literalized);
    }
    Py_ssize_t query = PyUnicode_FindChar(target, '?', 0, length, 1);
    Py_ssize_t fragment = PyUnicode_FindChar(target, '#', 0, length, 1);
    Py_ssize_t path_end = length;
    if (query >= 0) {
        path_end = query;
    }
    if (fragment >= 0 && fragment < path_end) {
        path_end = fragment;
    }
    int source_kind = PyUnicode_KIND(target);
    const void *source = PyUnicode_DATA(target);
    Py_ssize_t leading = 0;
    while (leading < path_end) {
        Py_UCS4 point = PyUnicode_READ(source_kind, source, leading);
        if (point != '/' && point != '\\') {
            break;
        }
        leading++;
    }
    Py_ssize_t skipped = leading > 2 ? leading - 2 : 0;
    PyObject *normalized = PyUnicode_New(length - skipped, PyUnicode_MAX_CHAR_VALUE(target));
    if (normalized == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int result_kind = PyUnicode_KIND(normalized);
    void *result = PyUnicode_DATA(normalized);
    int backslash_dot = 0;
    for (Py_ssize_t index = 0; index < length - skipped; index++) {
        Py_ssize_t source_index = index < 2 ? index : index + skipped;
        Py_UCS4 point = PyUnicode_READ(source_kind, source, source_index);
        backslash_dot |= source_index + 1 < path_end && point == '\\' &&
                         PyUnicode_READ(source_kind, source, source_index + 1) == '.';
        PyUnicode_WRITE(result_kind, result, index, source_index < path_end && point == '\\' ? '/' : point);
    }
    PyObject *joined = url_join_encoded_dot_segments(base, normalized, literalized);
    if (joined != NULL && backslash_dot) {
        *literalized = 1;
    }
    Py_DECREF(normalized);
    return joined;
}

/* Resolved URLs trim trailing C0 controls per WHATWG; th_url_join retains urllib's raw-reference contract. */
static int url_ends_in_c0(PyObject *url) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(url);
    if (length == 0) {
        return 0;
    }
    if (PyUnicode_IS_ASCII(url)) {
        return PyUnicode_1BYTE_DATA(url)[length - 1] <= 0x20;
    }
    return PyUnicode_READ(PyUnicode_KIND(url), PyUnicode_DATA(url), length - 1) <= 0x20;
}

static int url_needs_dot_normalization(PyObject *joined, int literalized, int raw_dot) {
    if (!(literalized || raw_dot) || !url_web_special_base(joined) ||
        (!url_has_literal_dot_unicode(joined) &&
         url_find_encoded_dot_unicode(joined, PyUnicode_GET_LENGTH(joined)) < 0)) {
        return 0;
    }
    Py_ssize_t length = PyUnicode_GET_LENGTH(joined);
    Py_ssize_t colon = PyUnicode_FindChar(joined, ':', 0, length, 1);
    return ((PyUnicode_READ_CHAR(joined, colon + 1) ^ '/') | (PyUnicode_READ_CHAR(joined, colon + 2) ^ '/')) != 0;
}

static inline int url_target_flags(PyObject *target) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(target);
    if (length == 0) {
        return 0;
    }
    if (PyUnicode_IS_ASCII(target)) {
        const unsigned char *bytes = PyUnicode_1BYTE_DATA(target);
        int leading = bytes[0] == '\\' ||
                      (bytes[0] == '/' && length > 1 &&
                       (bytes[1] == '\\' || (bytes[1] == '/' && length > 2 && (bytes[2] == '/' || bytes[2] == '\\'))));
        return (bytes[length - 1] <= 0x20) | (leading << 1);
    }
    int kind = PyUnicode_KIND(target);
    const void *data = PyUnicode_DATA(target);
    Py_UCS4 first = PyUnicode_READ(kind, data, 0);
    int leading = first == '\\' ||
                  (first == '/' && (PyUnicode_READ(kind, data, 1) == '\\' ||
                                    (PyUnicode_READ(kind, data, 1) == '/' &&
                                     (PyUnicode_READ(kind, data, 2) == '/' || PyUnicode_READ(kind, data, 2) == '\\'))));
    return (PyUnicode_READ(kind, data, length - 1) <= 0x20) | (leading << 1);
}

static PyObject *trim_url_trailing_c0(PyObject *url) {
    Py_ssize_t end = PyUnicode_GET_LENGTH(url);
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    while (end > 0 && PyUnicode_READ(kind, data, end - 1) <= 0x20) {
        end--;
    }
    return end == PyUnicode_GET_LENGTH(url) ? Py_NewRef(url) : PyUnicode_Substring(url, 0, end);
}

static PyObject *url_join_absolute_web(PyObject *target, int *literalized) {
    PyObject *empty = PyUnicode_FromStringAndSize("", 0);
    if (empty == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *joined = url_join_encoded_dot_segments(empty, target, literalized);
    Py_DECREF(empty);
    return joined;
}

/* Resolve `target` against `base` and percent-encode the result the way base_url() does, the URL-resolution routine the
   extraction methods reuse rather than reinventing. A relative target absolutizes against base; an absolute one wins.
   NULL with a ValueError set when base or target cannot be split (e.g. an unclosed IPv6 bracket). */
static int url_simple_path_byte(unsigned char ch) {
    return (unsigned)((ch | 0x20) - 'a') < 26 || (unsigned)(ch - '0') < 10 || ch == '-' || ch == '_';
}

static PyObject *url_resolve_simple_web(PyObject *base, PyObject *target) {
    if (!PyUnicode_IS_ASCII(base) || !PyUnicode_IS_ASCII(target) || PyUnicode_GET_LENGTH(target) == 0) {
        return NULL;
    }
    Py_ssize_t authority_end = th_url_document_web_authority(base);
    if (authority_end < 0) {
        return NULL;
    }
    const char *base_bytes = (const char *)PyUnicode_1BYTE_DATA(base);
    Py_ssize_t base_len = PyUnicode_GET_LENGTH(base);
    Py_ssize_t last_slash = authority_end;
    for (Py_ssize_t index = authority_end + 1; index < base_len; index++) {
        unsigned char ch = (unsigned char)base_bytes[index];
        if (ch != '/' && !url_simple_path_byte(ch)) {
            return NULL;
        }
        if (ch == '/') {
            last_slash = index;
        }
    }
    const char *target_bytes = (const char *)PyUnicode_1BYTE_DATA(target);
    Py_ssize_t target_len = PyUnicode_GET_LENGTH(target);
    int rooted = target_bytes[0] == '/';
    for (Py_ssize_t index = rooted; index < target_len; index++) {
        unsigned char ch = (unsigned char)target_bytes[index];
        if (!url_simple_path_byte(ch)) {
            return NULL;
        }
    }
    Py_ssize_t prefix_len = rooted ? authority_end : last_slash + 1;
    PyObject *result = PyUnicode_New(prefix_len + target_len, 127);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    char *out = (char *)PyUnicode_1BYTE_DATA(result);
    memcpy(out, base_bytes, (size_t)prefix_len);
    memcpy(out + prefix_len, target_bytes, (size_t)target_len);
    return result;
}

static PyObject *url_special_extra_authority_slashes(PyObject *target) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(target);
    if (length < 8) {
        return NULL;
    }
    Py_UCS4 first = lower_ascii(PyUnicode_READ_CHAR(target, 0));
    if (first != 'h' && first != 'f' && first != 'w') {
        return NULL;
    }
    Py_ssize_t colon = first == 'h'   ? (PyUnicode_READ_CHAR(target, 4) == ':' ? 4 : 5)
                       : first == 'f' ? 3
                                      : (PyUnicode_READ_CHAR(target, 2) == ':' ? 2 : 3);
    if (colon + 3 >= length || PyUnicode_READ_CHAR(target, colon) != ':' ||
        (PyUnicode_READ_CHAR(target, colon + 3) != '/' && PyUnicode_READ_CHAR(target, colon + 3) != '\\')) {
        return NULL;
    }
    Py_ssize_t host = colon + 1;
    for (int separator = 0; separator < 3; separator++, host++) {
        Py_UCS4 point = PyUnicode_READ_CHAR(target, host);
        if (point != '/' && point != '\\') {
            return NULL;
        }
    }
    if (!url_web_special_base(target)) {
        return NULL;
    }
    while (host < length) {
        Py_UCS4 point = PyUnicode_READ_CHAR(target, host);
        if (point != '/' && point != '\\') {
            break;
        }
        host++;
    }
    if (host == length || PyUnicode_READ_CHAR(target, host) == '?' || PyUnicode_READ_CHAR(target, host) == '#') {
        PyErr_SetString(PyExc_ValueError, "special URL requires a host");
        return NULL;
    }
    PyObject *normalized = PyUnicode_New(length + colon + 3 - host, PyUnicode_MAX_CHAR_VALUE(target));
    if (normalized == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int source_kind = PyUnicode_KIND(target);
    const void *source = PyUnicode_DATA(target);
    int result_kind = PyUnicode_KIND(normalized);
    void *result = PyUnicode_DATA(normalized);
    for (Py_ssize_t index = 0; index <= colon; index++) {
        PyUnicode_WRITE(result_kind, result, index, PyUnicode_READ(source_kind, source, index));
    }
    PyUnicode_WRITE(result_kind, result, colon + 1, '/');
    PyUnicode_WRITE(result_kind, result, colon + 2, '/');
    for (Py_ssize_t index = host; index < length; index++) {
        PyUnicode_WRITE(result_kind, result, index + colon + 3 - host, PyUnicode_READ(source_kind, source, index));
    }
    return normalized;
}

PyObject *th_url_resolve(PyObject *base, PyObject *target) {
    PyObject *simple = url_resolve_simple_web(base, target);
    if (simple != NULL) {
        return simple;
    }
    if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the fast path only raises on allocation */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *normalized = url_special_extra_authority_slashes(target);
    if (normalized != NULL) {
        PyObject *resolved = th_url_resolve(base, normalized);
        Py_DECREF(normalized);
        return resolved;
    }
    if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: only normalization allocation can fail */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int literalized;
    PyObject *joined;
    Py_ssize_t target_len = PyUnicode_GET_LENGTH(target);
    int raw_dot =
        url_has_literal_dot_unicode(target) ||
        ((target_len == 0 || PyUnicode_READ_CHAR(target, 0) == '?' || PyUnicode_READ_CHAR(target, 0) == '#') &&
         url_has_literal_dot_unicode(base));
    int target_flags = url_target_flags(target);
    int web_slashes = target_flags & 2;
    int absolute_web = th_url_web_authority(target);
    if (!web_slashes && absolute_web == TH_URL_WEB_AUTHORITY_NONE && target_len >= 5 &&
        (lower_ascii(PyUnicode_READ_CHAR(target, 0)) == 'h' || lower_ascii(PyUnicode_READ_CHAR(target, 0)) == 'f' ||
         lower_ascii(PyUnicode_READ_CHAR(target, 0)) == 'w') &&
        url_has_backslash_dot_path(target) && url_web_special_base(target)) {
        Py_ssize_t colon = PyUnicode_FindChar(target, ':', 0, target_len, 1);
        if (PyUnicode_READ_CHAR(target, colon + 1) != '/' && PyUnicode_READ_CHAR(target, colon + 1) != '\\') {
            web_slashes = 2;
        }
    }
    if (!url_ends_in_c0(base) && !(target_flags & 1)) {
        if (absolute_web == TH_URL_WEB_AUTHORITY_BACKSLASH) {
            joined = url_join_absolute_web(target, &literalized);
        } else {
            joined = web_slashes ? url_join_web_special_slashes(base, target, &literalized)
                                 : url_join_encoded_dot_segments(base, target, &literalized);
        }
    } else {
        PyObject *trimmed_base = trim_url_trailing_c0(base);
        if (trimmed_base == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *trimmed_target = trim_url_trailing_c0(target);
        if (trimmed_target == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(trimmed_base);  /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (absolute_web == TH_URL_WEB_AUTHORITY_BACKSLASH) {
            joined = url_join_absolute_web(trimmed_target, &literalized);
        } else {
            joined = web_slashes ? url_join_web_special_slashes(trimmed_base, trimmed_target, &literalized)
                                 : url_join_encoded_dot_segments(trimmed_base, trimmed_target, &literalized);
        }
        Py_DECREF(trimmed_base);
        Py_DECREF(trimmed_target);
    }
    if (joined == NULL) {
        return NULL;
    }
    int normalize_dot = url_needs_dot_normalization(joined, literalized, raw_dot) ||
                        (url_has_backslash_dot_path(target) && url_web_special_base(target));
    PyObject *encoded = url_percent_encode(joined, literalized ? 2 : raw_dot);
    Py_DECREF(joined);
    if (encoded == NULL) {
        return NULL;
    }
    PyObject *canonical = th_url_document_canonical(encoded, normalize_dot);
    Py_DECREF(encoded);
    return canonical;
}

/* The value of `node`'s `name` attribute as a new str reference (the empty string when valueless), or NULL when the
   attribute is absent (or, on the excluded allocation-failure path, with an error set). */
static PyObject *element_attr_str(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = find_attr_index(tree, node, name, name_len);
    if (index < 0) {
        return NULL;
    }
    return attr_value_obj(&node->attrs[index]);
}

/* The ASCII whitespace HTML trims around these values, minus CR (the input preprocessor turns it into LF). An array
   keeps the branch count stable when clang inlines this into several call sites. */
static int meta_is_space(Py_UCS4 c) {
    static const Py_UCS4 whitespace[] = {' ', '\t', '\n', '\f'};
    for (size_t index = 0; index < sizeof(whitespace) / sizeof(whitespace[0]); index++) {
        if (c == whitespace[index]) {
            return 1;
        }
    }
    return 0;
}

static int meta_is_digit(Py_UCS4 c) {
    return c >= '0' && c <= '9';
}

/* Parse a meta-refresh `content` ("<delay>" or "<delay>;url=<url>") into ``(delay, url)``, resolving the url against
   `fallback` and using `fallback` itself when the directive has none. Returns the tuple, None when there is no leading
   delay, or NULL on error. */
static PyObject *parse_meta_refresh(PyObject *content, PyObject *fallback) {
    int kind = PyUnicode_KIND(content);
    const void *data = PyUnicode_DATA(content);
    Py_ssize_t len = PyUnicode_GET_LENGTH(content);
    Py_ssize_t pos = 0;
    while (pos < len && meta_is_space(PyUnicode_READ(kind, data, pos))) {
        pos++;
    }
    Py_ssize_t number_start = pos;
    while (pos < len && meta_is_digit(PyUnicode_READ(kind, data, pos))) {
        pos++;
    }
    if (pos < len && PyUnicode_READ(kind, data, pos) == '.') {
        pos++;
        while (pos < len && meta_is_digit(PyUnicode_READ(kind, data, pos))) {
            pos++;
        }
    }
    if (pos == number_start) {
        Py_RETURN_NONE; /* no leading number, so not a refresh directive */
    }
    PyObject *number = PyUnicode_Substring(content, number_start, pos);
    if (number == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *delay = PyFloat_FromString(number);
    Py_DECREF(number);
    if (delay == NULL) { /* GCOVR_EXCL_BR_LINE: the substring is all digits and at most one dot */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *url = NULL;
    while (pos < len && PyUnicode_READ(kind, data, pos) != ';' && PyUnicode_READ(kind, data, pos) != ',') {
        pos++;
    }
    if (pos < len) {
        pos++; /* step past the ';' or ',' */
        while (pos < len && meta_is_space(PyUnicode_READ(kind, data, pos))) {
            pos++;
        }
        /* an optional "url" "=" prefix, e.g. "url=next" or "URL = next" */
        if (pos + 2 < len && (PyUnicode_READ(kind, data, pos) | 0x20) == 'u' &&
            (PyUnicode_READ(kind, data, pos + 1) | 0x20) == 'r' &&
            (PyUnicode_READ(kind, data, pos + 2) | 0x20) == 'l') {
            Py_ssize_t after = pos + 3;
            while (after < len && meta_is_space(PyUnicode_READ(kind, data, after))) {
                after++;
            }
            if (after < len && PyUnicode_READ(kind, data, after) == '=') {
                after++;
                while (after < len && meta_is_space(PyUnicode_READ(kind, data, after))) {
                    after++;
                }
                pos = after;
            }
        }
        Py_ssize_t url_end = len;
        while (url_end > pos && meta_is_space(PyUnicode_READ(kind, data, url_end - 1))) {
            url_end--;
        }
        if (url_end > pos) {
            Py_UCS4 quote = PyUnicode_READ(kind, data, pos);
            if ((quote == '"' || quote == '\'') && url_end - 1 > pos &&
                PyUnicode_READ(kind, data, url_end - 1) == quote) {
                pos++;
                url_end--;
            }
        }
        if (url_end > pos) {
            PyObject *raw = PyUnicode_Substring(content, pos, url_end);
            if (raw == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(delay); /* GCOVR_EXCL_LINE */
                return NULL;      /* GCOVR_EXCL_LINE */
            }
            url = th_url_resolve(fallback, raw);
            Py_DECREF(raw);
            if (url == NULL) { /* a refresh target with an unbalanced IPv6 bracket cannot be resolved */
                Py_DECREF(delay);
                return NULL;
            }
        }
    }
    if (url == NULL) {
        url = Py_NewRef(fallback);
    }
    PyObject *result = PyTuple_Pack(2, delay, url);
    Py_DECREF(delay);
    Py_DECREF(url);
    return result; /* NULL on the excluded allocation-failure path */
}

/* Hold a borrowed fallback as an owned str, defaulting to "" when None was passed in. */
static PyObject *refresh_fallback(PyObject *fallback) {
    if (fallback == NULL) {
        return PyUnicode_FromStringAndSize("", 0);
    }
    return Py_NewRef(fallback);
}

PyDoc_STRVAR(base_url_doc, "base_url(fallback='')\n--\n\n"
                           "Resolve this document's base URL from its first <base href>.\n\n"
                           ":param fallback: the URL the <base href> is resolved against, and the result\n"
                           "    itself when the document has no <base href>.\n"
                           ":returns: the document's base URL.");

static bool base_has_forbidden_scheme(PyObject *url) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(url);
    if (length < 5) {
        return false;
    }
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    Py_UCS4 first = lower_ascii(PyUnicode_READ(kind, data, 0));
    if (first != 'd' && first != 'j') {
        return false;
    }
    const char *scheme = first == 'd' ? "data:" : "javascript:";
    size_t scheme_length = strlen(scheme);
    if (length < (Py_ssize_t)scheme_length) {
        return false;
    }
    for (size_t offset = 1; offset < scheme_length; offset++) {
        if (lower_ascii(PyUnicode_READ(kind, data, offset)) != (unsigned char)scheme[offset]) {
            return false;
        }
    }
    return true;
}

/* th_url_join preserves urllib's raw references, while HTML base href uses URL parser input cleanup. */
static int base_url_removed(Py_UCS4 point) {
    return point == '\t' || point == '\n' || point == '\r';
}

static PyObject *base_url_input(PyObject *href) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(href);
    int kind = PyUnicode_KIND(href);
    const void *data = PyUnicode_DATA(href);
    Py_ssize_t start = 0;
    Py_ssize_t end = length;
    while (start < end && PyUnicode_READ(kind, data, start) <= 0x20) {
        start++;
    }
    while (end > start && PyUnicode_READ(kind, data, end - 1) <= 0x20) {
        end--;
    }
    Py_ssize_t removed = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        removed += base_url_removed(PyUnicode_READ(kind, data, index));
    }
    if (removed == 0) {
        return start == 0 && end == length ? Py_NewRef(href) : PyUnicode_Substring(href, start, end);
    }
    PyObject *input = PyUnicode_New(end - start - removed, PyUnicode_MAX_CHAR_VALUE(href));
    if (input == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t written = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        Py_UCS4 point = PyUnicode_READ(kind, data, index);
        if (!base_url_removed(point)) {
            PyUnicode_WRITE(PyUnicode_KIND(input), PyUnicode_DATA(input), written++, point);
        }
    }
    return input;
}

static int base_url_missing_special_host(PyObject *fallback, PyObject *input) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(input);
    if (length < 2) {
        return 0;
    }
    Py_UCS4 first = PyUnicode_READ_CHAR(input, 0);
    Py_ssize_t index;
    int absolute = first != '/' && first != '\\';
    if (absolute) {
        if (length < 5 || (lower_ascii(first) != 'h' && lower_ascii(first) != 'f' && lower_ascii(first) != 'w')) {
            return 0;
        }
        if (PyUnicode_IS_ASCII(input)) {
            const char *bytes = (const char *)PyUnicode_1BYTE_DATA(input);
            if (first == 'h' && length > 6 && memcmp(bytes, "http:/", 6) == 0 && bytes[6] != '/' && bytes[6] != '\\') {
                return 0;
            }
            Py_ssize_t authority = first == 'h' && length > 7 && memcmp(bytes, "http://", 7) == 0    ? 7
                                   : first == 'h' && length > 8 && memcmp(bytes, "https://", 8) == 0 ? 8
                                   : first == 'f' && length > 6 && memcmp(bytes, "ftp://", 6) == 0   ? 6
                                   : first == 'w' && length > 5 && memcmp(bytes, "ws://", 5) == 0    ? 5
                                   : first == 'w' && length > 6 && memcmp(bytes, "wss://", 6) == 0   ? 6
                                                                                                     : 0;
            if (authority && bytes[authority] != '/' && bytes[authority] != '\\' && bytes[authority] != '?' &&
                bytes[authority] != '#') {
                return 0;
            }
        }
        Py_ssize_t colon = PyUnicode_FindChar(input, ':', 0, length, 1);
        if (colon < 0 || colon + 2 >= length ||
            (PyUnicode_READ_CHAR(input, colon + 1) != '/' && PyUnicode_READ_CHAR(input, colon + 1) != '\\') ||
            (PyUnicode_READ_CHAR(input, colon + 2) != '/' && PyUnicode_READ_CHAR(input, colon + 2) != '\\')) {
            return 0;
        }
        index = colon + 3;
    } else {
        Py_UCS4 second = PyUnicode_READ_CHAR(input, 1);
        if (second != '/' && second != '\\') {
            return 0;
        }
        index = 2;
    }
    while (index < length && (PyUnicode_READ_CHAR(input, index) == '/' || PyUnicode_READ_CHAR(input, index) == '\\')) {
        index++;
    }
    return (index == length || PyUnicode_READ_CHAR(input, index) == '?' || PyUnicode_READ_CHAR(input, index) == '#') &&
           url_web_special_base(absolute ? input : fallback);
}

/* The document's base URL: its first <base href> resolved against `fallback` (a borrowed str), or `fallback` itself
   when there is none or the href fails URL parsing (HTML spec 2.4.3). The extraction methods share it. */
PyObject *th_document_base_url(PyObject *self, PyObject *fallback) {
    PyObject *href = NULL;
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_ELEMENT && node->atom == TH_TAG_BASE &&
            (href = element_attr_str(tree, node, "href", 4)) != NULL) {
            break;
        }
    }
    Py_END_CRITICAL_SECTION();
    if (href == NULL) {
        return Py_NewRef(fallback); /* no <base href>: the fallback is the base */
    }
    PyObject *input = base_url_input(href);
    Py_DECREF(href);
    if (input == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *result =
        base_url_missing_special_host(fallback, input) ? Py_NewRef(fallback) : th_url_resolve(fallback, input);
    if (result == NULL) {
        if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
            Py_DECREF(input);                            /* GCOVR_EXCL_LINE */
            return NULL;                                 /* GCOVR_EXCL_LINE */
        }
        PyErr_Clear();
        result = Py_NewRef(fallback);
    }
    if (base_has_forbidden_scheme(result)) {
        Py_SETREF(result, Py_NewRef(fallback));
    }
    Py_DECREF(input);
    return result;
}

TH_NODE_API(static, PyObject *, document_base_url, (PyObject * self, PyObject *args, PyObject *kwargs),
            (self, args, kwargs), (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    static char *keywords[] = {"fallback", NULL};
    PyObject *fallback = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|U:base_url", keywords, &fallback)) {
        return NULL;
    }
    fallback = refresh_fallback(fallback);
    if (fallback == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = th_document_base_url(self, fallback);
    Py_DECREF(fallback);
    return result;
}

PyDoc_STRVAR(meta_refresh_doc, "meta_refresh(fallback='')\n--\n\n"
                               "Read the document's <meta http-equiv=refresh> redirect. A refresh tag inside\n"
                               "<noscript> is ignored.\n\n"
                               ":param fallback: the URL the directive's target is resolved against, and the\n"
                               "    target itself when the directive omits one.\n"
                               ":returns: a (delay_seconds, url) pair, or None when the document has no\n"
                               "    refresh directive.");

/* A <meta> nested in <noscript> is ignored, as w3lib does; <script> needs no entry because it is a raw-text element, so
   the parser never nests a <meta> element inside one. */
static int under_noscript(th_node *node) {
    for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->atom == TH_TAG_NOSCRIPT) {
            return 1;
        }
    }
    return 0;
}

/* Whether `equiv` is "refresh" once surrounding whitespace and case are ignored, matched in C to avoid Python calls
   inside the tree walk. */
static int equiv_is_refresh(PyObject *equiv) {
    int kind = PyUnicode_KIND(equiv);
    const void *data = PyUnicode_DATA(equiv);
    Py_ssize_t start = 0;
    Py_ssize_t end = PyUnicode_GET_LENGTH(equiv);
    while (start < end && meta_is_space(PyUnicode_READ(kind, data, start))) {
        start++;
    }
    while (end > start && meta_is_space(PyUnicode_READ(kind, data, end - 1))) {
        end--;
    }
    static const char target[] = "refresh";
    if (end - start != (Py_ssize_t)(sizeof(target) - 1)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < end - start; index++) {
        if ((PyUnicode_READ(kind, data, start + index) | 0x20) != (Py_UCS4)target[index]) {
            return 0;
        }
    }
    return 1;
}

TH_NODE_API(static, PyObject *, document_meta_refresh, (PyObject * self, PyObject *args, PyObject *kwargs),
            (self, args, kwargs), (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    static char *keywords[] = {"fallback", NULL};
    PyObject *fallback = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|U:meta_refresh", keywords, &fallback)) {
        return NULL;
    }
    fallback = refresh_fallback(fallback);
    if (fallback == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *content = NULL;
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_META || under_noscript(node)) {
            continue;
        }
        PyObject *equiv = element_attr_str(tree, node, "http-equiv", 10);
        if (equiv == NULL) {
            continue;
        }
        int refresh = equiv_is_refresh(equiv);
        Py_DECREF(equiv);
        if (refresh) {
            content = element_attr_str(tree, node, "content", 7);
            break;
        }
    }
    Py_END_CRITICAL_SECTION();
    PyObject *result;
    if (content == NULL) {
        result = Py_NewRef(Py_None);
    } else {
        result = parse_meta_refresh(content, fallback);
        Py_DECREF(content);
    }
    Py_DECREF(fallback);
    return result;
}

PyDoc_STRVAR(structured_data_doc,
             "structured_data(base_url=None)\n--\n\n"
             "Extract every supported structured-data format from the document, a successor to\n"
             "extruct. Returns a StructuredData record with .json_ld (list of parsed JSON-LD\n"
             "values), .microdata (list of MicrodataItem), .opengraph (dict of og:/twitter: keys\n"
             "to their content), .rdfa (list of RdfaItem), .dublin_core (dict of dc.*/dcterms.*\n"
             "names to their content), and .microformats (an empty list, a later phase). Every field\n"
             "comes from one document snapshot.\n\n"
             ":param base_url: when given, the URL each relative URL-valued microdata, opengraph, or\n"
             "    RDFa field is resolved against (a <base href> refines it, HTML spec 4.2.3); json_ld\n"
             "    and Dublin Core are left verbatim. None (the default) returns every value verbatim.\n"
             ":raises ValueError: if base_url is not a valid absolute URL.\n"
             ":raises RecursionError: if Microdata or RDFa records nest more than 400 levels, or if\n"
             "    Microdata itemref links form a nested-item cycle.");

PyDoc_STRVAR(json_ld_doc, "json_ld()\n--\n\n"
                          "Parse every <script type=\"application/ld+json\"> block in the document with the\n"
                          "standard library json module, returning the list of decoded values in document order.\n"
                          "A block that is not valid JSON, or that nests arrays or objects more than 400 levels\n"
                          "deep, is skipped.");

PyDoc_STRVAR(opengraph_doc, "opengraph(base_url=None)\n--\n\n"
                            "Return an OpenGraph record of the page's Open Graph metadata, a successor to the\n"
                            "opengraph library. Each <meta property=\"og:...\"> key is read with the og: prefix\n"
                            "stripped (og:title reads as og[\"title\"]) and the twitter: keys are dropped; when a\n"
                            "key repeats, the last occurrence wins.\n\n"
                            ":param base_url: when given, the URL each relative URL-valued key (og:url, og:image,\n"
                            "    og:video, ...) is resolved against; a <base href> refines it. None (the default)\n"
                            "    returns every value verbatim.\n"
                            ":raises ValueError: if base_url is not a valid absolute URL.");

PyDoc_STRVAR(microdata_doc,
             "microdata(base_url=None)\n--\n\n"
             "Extract HTML Microdata as a list of MicrodataItem, one per top-level itemscope. Each item\n"
             "has .properties (a dict mapping each itemprop name to its list of values), plus .type and\n"
             ".id carrying the itemtype/itemid attribute or None. A property value is a nested\n"
             "MicrodataItem for an itemscope, otherwise the element's microdata value.\n\n"
             ":param base_url: when given, the URL each relative URL-valued property (an a/area/link\n"
             "    href, a media src, an object data) is resolved against; a <base href> refines it.\n"
             "    None (the default) returns every value verbatim.\n"
             ":raises ValueError: if base_url is not a valid absolute URL.\n"
             ":raises RecursionError: if records nest more than 400 levels, or if itemref links form\n"
             "    a nested-item cycle.");

PyDoc_STRVAR(rdfa_doc, "rdfa(base_url=None)\n--\n\n"
                       "Extract RDFa as a list of RdfaItem, one per top-level typeof resource. Each item has\n"
                       ".properties (a dict mapping each expanded property IRI to its list of values), plus\n"
                       ".type (the expanded typeof IRIs), .resource (the subject), and .vocab (the in-scope\n"
                       "@vocab). property/typeof tokens expand against @vocab and @prefix (the RDFa 1.1 initial\n"
                       "context seeds the common prefixes); an undeclared prefix stays verbatim.\n\n"
                       ":param base_url: when given, the URL each resource/href/src IRI is resolved against; a\n"
                       "    <base href> refines it. None (the default) returns every value verbatim.\n"
                       ":raises ValueError: if base_url is not a valid absolute URL.\n"
                       ":raises RecursionError: if records nest more than 400 levels.");

PyDoc_STRVAR(dublin_core_doc, "dublin_core()\n--\n\n"
                              "Return a dict mapping each <meta name=\"dc.*\"> or <meta name=\"dcterms.*\"> name\n"
                              "(lower-cased) to its content value. When a name repeats, the last occurrence wins.");

PyDoc_STRVAR(feed_doc, "feed()\n--\n\n"
                       "Normalize an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one Feed record, or\n"
                       "None when the document carries no feed root. Feed has .type (\"rss\", \"atom\", or\n"
                       "\"rdf\"), .title, .link, .description, .updated, and .entries (a tuple of Entry). Each\n"
                       "Entry has .title, .link, .id, .updated, .published, .summary, .content, and .author,\n"
                       "each the first present value across the RSS/Atom/RDF spellings of the field.");

static PyMethodDef document_methods[] = {
    {"base_url", (PyCFunction)(void (*)(void))document_base_url, METH_VARARGS | METH_KEYWORDS, base_url_doc},
    {"meta_refresh", (PyCFunction)(void (*)(void))document_meta_refresh, METH_VARARGS | METH_KEYWORDS,
     meta_refresh_doc},
    {"structured_data", (PyCFunction)(void (*)(void))turbohtml_document_structured_data, METH_VARARGS | METH_KEYWORDS,
     structured_data_doc},
    {"json_ld", turbohtml_document_json_ld, METH_NOARGS, json_ld_doc},
    {"opengraph", (PyCFunction)(void (*)(void))turbohtml_document_opengraph, METH_VARARGS | METH_KEYWORDS,
     opengraph_doc},
    {"microdata", (PyCFunction)(void (*)(void))turbohtml_document_microdata, METH_VARARGS | METH_KEYWORDS,
     microdata_doc},
    {"rdfa", (PyCFunction)(void (*)(void))turbohtml_document_rdfa, METH_VARARGS | METH_KEYWORDS, rdfa_doc},
    {"dublin_core", turbohtml_document_dublin_core, METH_NOARGS, dublin_core_doc},
    {"_dates", turbohtml_document_dates, METH_VARARGS, NULL},
    {"_extract_links", turbohtml_document_extract_links, METH_VARARGS, NULL},
    {"feed", turbohtml_document_feed, METH_NOARGS, feed_doc},
    {NULL, NULL, 0, NULL},
};

/* The parse errors are immutable once the parse returns, so this reads them
   lock-free (like the other accessors) and materializes a fresh list each call. */
TH_NODE_API(static, PyObject *, document_get_errors, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    HandleObject *handle = (HandleObject *)((NodeObject *)self)->handle;
    th_tree *tree = handle->tree;
    /* the first read folds the preprocessing errors into the tree, so two threads reading
       Document.errors at once must not both do it; a streamed or hand-built tree kept no
       source, and its input raises no error to find */
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(handle);
    if (PyUnicode_Check(handle->source)) {
        failed = th_tree_ensure_input_errors(tree, PyUnicode_KIND(handle->source), PyUnicode_DATA(handle->source),
                                             PyUnicode_GET_LENGTH(handle->source));
    }
    Py_END_CRITICAL_SECTION();
    if (failed < 0) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count;
    const th_parse_error *errors = th_tree_errors(tree, &count);
    PyObject *list = PyList_New(count);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    module_state *state = state_of(self);
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *error = parse_error_new(state, &errors[index]);
        if (error == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyList_SET_ITEM(list, index, error);
    }
    return list;
}

static PyGetSetDef document_getset[] = {
    {"root", document_get_root, NULL, "the root <html> element, or None", NULL},
    {"encoding", document_get_encoding, NULL, "the resolved encoding name for bytes input, or None for str", NULL},
    {"encoding_confidence", document_get_encoding_confidence, NULL,
     "'certain' when a byte-order mark, the encoding argument, or a <meta> named the encoding, 'tentative' when the "
     "sniff guessed it, or None for str input",
     NULL},
    {"errors", document_get_errors, NULL, "the WHATWG parse errors detected, as a list of ParseError in document order",
     NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(document_doc, "A parsed document: the root of the tree returned by parse().");

static PyType_Slot document_slots[] = {
    {Py_tp_doc, (void *)document_doc},
    {Py_tp_getset, document_getset},
    {Py_tp_methods, document_methods},
    TH_SEALED_END,
};

static PyType_Spec document_spec = {
    .name = "turbohtml._html.Document",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | TH_SEALED,
    .slots = document_slots,
};

static void handle_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    HandleObject *handle = (HandleObject *)self;
    handle_clear_caches(handle);
    PyMem_Free(handle->index_offsets);
    PyMem_Free(handle->index_nodes);
    PyMem_Free(handle->hash_overrides);
    if (handle->bindings != handle->inline_bindings) {
        PyMem_Free(handle->bindings);
    }
    PyMem_Free(handle->binding_overflow);
    path_id_map_free(handle->path_ids);
    path_positions_free(handle->path_positions);
    th_tree_free(handle->tree);
    Py_XDECREF(handle->source);
    Py_XDECREF(handle->encoding);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyType_Slot handle_slots[] = {
    {Py_tp_dealloc, handle_dealloc},
    TH_SEALED_END,
};

static PyType_Spec handle_spec = {
    .name = "turbohtml._html._TreeHandle",
    .basicsize = sizeof(HandleObject),
    .flags = Py_TPFLAGS_DEFAULT | TH_SEALED,
    .slots = handle_slots,
};

PyObject *handle_new(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding, int encoding_certain) {
    PyTypeObject *type = (PyTypeObject *)state->handle_type;
    HandleObject *self = (HandleObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->tree = tree;
#ifndef Py_GIL_DISABLED
    self->state = state;
#endif
    self->bindings = self->inline_bindings;
    self->binding_capacity = sizeof(self->inline_bindings) / sizeof(*self->inline_bindings);
    self->binding_next = 1;
    self->source = Py_NewRef(source);
    self->encoding = Py_NewRef(encoding);
    self->encoding_certain = encoding_certain;
    return (PyObject *)self;
}

/* Wrap a freshly built tree (which borrows source's storage) and return its
   document/context node. Frees the tree on wrapping failure. */
static PyObject *tree_to_node(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding,
                              int encoding_certain) {
    PyObject *handle = handle_new(state, tree, source, encoding, encoding_certain);
    if (handle == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = node_wrap(state, handle, th_tree_document(tree));
    Py_DECREF(handle);
    return node;
}

/* In strict mode, raise HTMLParseError carrying the first parse error and free
   the tree, returning -1; otherwise return 0 and leave the tree to be wrapped.
   The exception's str is a readable summary and its .error is the ParseError. */
/* source is the str the tree was parsed from, or NULL for the XML parser, whose
   well-formedness errors are its own and carry no preprocessing step. */
static int strict_raise(module_state *state, th_tree *tree, int strict, PyObject *source) {
    if (!strict) {
        return 0;
    }
    if (source != NULL) {
        int failed = th_tree_ensure_input_errors(tree, PyUnicode_KIND(source), PyUnicode_DATA(source),
                                                 PyUnicode_GET_LENGTH(source));
        if (failed < 0) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_ssize_t count;
    const th_parse_error *errors = th_tree_errors(tree, &count);
    if (count == 0) {
        return 0;
    }
    PyObject *error = parse_error_new(state, &errors[0]);
    PyObject *message = th_str_format("%s at line %zd, column %zd", errors[0].code, errors[0].line, errors[0].col);
    /* allocation failure cannot be forced from a test */
    if (error == NULL || message == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(error);                  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(message);                /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tree_free(tree);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *exc = PyObject_CallOneArg(state->parse_error_exc, message);
    Py_DECREF(message);
    /* allocation failure cannot be forced from a test */
    if (exc == NULL || PyObject_SetAttrString(exc, "error", error) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(exc);                                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(error);                                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tree_free(tree);                                               /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                                                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_DECREF(error);
    PyErr_SetObject(state->parse_error_exc, exc);
    Py_DECREF(exc);
    th_tree_free(tree);
    return -1;
}

int raise_xml_fragment_error(module_state *state, th_tree *tree, Py_ssize_t start_len) {
    Py_ssize_t count;
    th_parse_error *errors = (th_parse_error *)th_tree_errors(tree, &count);
    if (count > 0 && errors[0].line == 1) {
        errors[0].col -= start_len;
    }
    return strict_raise(state, tree, 1, NULL);
}

/* The encoding the document's <meta> elements declare, or NULL when none of them resolves.
   The first label naming a supported encoding decides; an unsupported one falls through to
   the next, the way the prescan's own lookup does. */
static const th_encoding_entry *meta_declared_encoding(const th_tree *tree) {
    Py_ssize_t count;
    const th_meta_label *labels = th_tree_meta_labels(tree, &count);
    for (Py_ssize_t index = 0; index < count; index++) {
        char extracted[64]; /* label may point into it, so it outlives the branch below */
        const char *label = labels[index].text;
        if (labels[index].from_content) {
            label = prescan_charset_in_content(label, extracted, sizeof(extracted));
            if (label == NULL) {
                continue;
            }
        }
        const th_encoding_entry *entry = th_encoding_lookup(label, (Py_ssize_t)strlen(label));
        if (entry != NULL) {
            return th_encoding_declared(entry);
        }
    }
    return NULL;
}

/* Parse bytes: sniff the encoding (BOM, then the encoding argument, then a <meta> prescan, then a structural UTF-8
   check, then windows-1252), decode it with the WHATWG decoder in encoding/decode.h, which turns every malformed
   sequence into U+FFFD, and parse the resulting str. When the sniff was only tentative and the built tree turns out to
   declare a different encoding in a <meta> the 1024-byte prescan could not reach, the parse is redone once against the
   declared encoding -- the WHATWG "changing the encoding while parsing" step. The decoded str is retained as the tree's
   source. */
static PyObject *parse_bytes(module_state *state, PyObject *markup, const char *enc_arg, Py_ssize_t enc_len, int strict,
                             int detect, int positions, int locations, int scripting, int declarative) {
    Py_buffer view;
    if (PyObject_GetBuffer(markup, &view, PyBUF_SIMPLE) < 0) { /* GCOVR_EXCL_BR_LINE: bytes expose a simple buffer */
        return NULL;                                           /* GCOVR_EXCL_LINE: buffer-acquisition failure */
    }
    const unsigned char *bytes = view.buf;
    Py_ssize_t len = view.len;
    const th_encoding_entry *entry = NULL;
    Py_ssize_t skip = th_encoding_bom(bytes, len, &entry);
    if (enc_arg != NULL) {
        /* validate the label even when a BOM already won, so a bogus encoding is an
           error rather than a silent fallback, matching Document.encode's LookupError */
        const th_encoding_entry *labeled = th_encoding_lookup(enc_arg, enc_len);
        if (labeled == NULL) {
            PyBuffer_Release(&view);
            PyErr_Format(PyExc_LookupError, "unknown encoding: %s", enc_arg);
            return NULL;
        }
        if (entry == NULL) {
            entry = labeled;
        }
    }
    /* a byte-order mark and the transport-layer argument are the spec's two certain
       sources; everything below them is tentative, so a <meta> may still overrule it */
    int certain = entry != NULL;
    if (entry == NULL) {
        entry = th_encoding_prescan(bytes, len);
    }
    if (entry == NULL) {
        if (detect) {
            /* opt-in content-based detection, strictly after the spec sniffing steps. parse() takes
               bytes, never the URL they came from, so no TLD is available to hint with. */
            th_detect_scores scores;
            entry = th_encoding_detect(bytes, len, TH_TLD_GENERIC, &scores);
        } else {
            /* The detector's first step on its own: UTF-8 validity is a structural proof,
               not a frequency guess, so it costs none of the opt-in model's candidate
               scoring, and an undeclared UTF-8 document never reaches the windows-1252
               fallback. Pure ASCII is left to that fallback, which decodes it identically. */
            int has_non_ascii;
            if (th_detect_is_utf8(bytes, len, &has_non_ascii) && has_non_ascii) {
                entry = th_encoding_lookup("utf-8", 5);
            }
        }
    }
    if (entry == NULL) {
        entry = th_encoding_lookup("windows-1252", 12);
    }
    /* Decode and parse, then -- while the encoding is still tentative -- let a <meta> the
       prescan's 1024-byte window could not reach redo the parse against what it declares.
       Only a real <meta> element counts, so a charset written inside a <script> string
       cannot fool it the way an unbounded byte scan would. At most one redo: the second
       pass runs with the declared encoding, which the spec then calls certain. */
    PyObject *decoded = NULL;
    th_tree *tree = NULL;
    for (int redone = 0;; redone = 1) {
        decoded = th_decode(entry, bytes + skip, len - skip);
        if (decoded == NULL) {       /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
            PyBuffer_Release(&view); /* GCOVR_EXCL_LINE: decode failure */
            return NULL;             /* GCOVR_EXCL_LINE: decode failure */
        }
        tree = th_tree_parse(PyUnicode_KIND(decoded), PyUnicode_DATA(decoded), PyUnicode_GET_LENGTH(decoded), positions,
                             locations, scripting, declarative);
        if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
            Py_DECREF(decoded);      /* GCOVR_EXCL_LINE: allocation-failure path */
            PyBuffer_Release(&view); /* GCOVR_EXCL_LINE: allocation-failure path */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (certain) {
            break;
        }
        if (redone) { /* this pass already ran with what the document declares */
            certain = 1;
            break;
        }
        const th_encoding_entry *declared = meta_declared_encoding(tree);
        if (declared == NULL) { /* nothing declares an encoding: the sniff stays a guess */
            break;
        }
        if (strcmp(declared->canonical, entry->canonical) == 0) {
            certain = 1; /* the declaration confirms the sniff, so no redo is needed */
            break;
        }
        entry = declared;
        th_tree_free(tree); /* the tree spans the decoded str, so it goes first */
        Py_DECREF(decoded);
    }
    PyBuffer_Release(&view);
    if (strict_raise(state, tree, strict, decoded) < 0) {
        Py_DECREF(decoded);
        return NULL;
    }
    PyObject *canonical = PyUnicode_FromString(entry->canonical);
    if (canonical == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(decoded);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = tree_to_node(state, tree, decoded, canonical, certain);
    Py_DECREF(canonical);
    Py_DECREF(decoded);
    return node;
}

/* The standalone encoding-detection binding behind turbohtml.detect: run the same
   sniffing pipeline as parse_bytes (BOM, <meta> prescan, then the content detector)
   without decoding or parsing. Returns (winner, certain, ranked, bom): the winner's
   canonical name or None for pure ASCII, whether it came from a declaration or a
   structural proof rather than frequency scoring, every surviving scored candidate as
   (canonical name, raw score) pairs, and whether a leading byte-order mark decided it.
   The BOM step uses th_detect_bom, which reports UTF-8-SIG where the spec-locked parse
   path reports UTF-8. */
/* _decode(data, label) -> str: decode bytes with the WHATWG decoder the label names, the way parse(bytes) would. A
   byte-order mark is not stripped; the label decides, as the spec's "decode" entry point does. */
PyObject *turbohtml_decode(PyObject *module, PyObject *args) {
    (void)module;
    Py_buffer view;
    const char *label = NULL;
    Py_ssize_t label_len = 0;
    if (!PyArg_ParseTuple(args, "y*s#", &view, &label, &label_len)) {
        return NULL;
    }
    const th_encoding_entry *entry = th_encoding_lookup(label, label_len);
    if (entry == NULL) {
        PyBuffer_Release(&view);
        PyErr_Format(PyExc_LookupError, "unknown encoding: %s", label);
        return NULL;
    }
    PyObject *decoded = th_decode(entry, view.buf, view.len);
    PyBuffer_Release(&view);
    return decoded;
}

/* Build the (winner, certain, ranked, bom) tuple both the one-shot detect() and the streaming
   detector answer with, so the two cannot describe the same bytes differently. */
static PyObject *detect_result(const char *winner, int certain, const th_detect_scores *scores, int bom) {
    PyObject *ranked = PyList_New(scores->count);
    if (ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < scores->count; index++) {
        const char *label = scores->items[index].label;
        const th_encoding_entry *item = th_encoding_lookup(label, (Py_ssize_t)strlen(label));
        PyObject *pair = Py_BuildValue("(sL)", item->canonical, (long long)scores->items[index].score);
        if (pair == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(ranked); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyList_SET_ITEM(ranked, index, pair);
    }
    return Py_BuildValue("(zONO)", winner, certain ? Py_True : Py_False, ranked, bom ? Py_True : Py_False);
}

/* One shaped candidate: a canonical encoding name and its share of the positive score total. */
typedef struct {
    PyObject *name; /* borrowed from the scored list the detector returned */
    double confidence;
} detect_candidate;

/* Is `name`, case-folded, in `names`? A NULL set answers 0, so an absent option filters nothing. */
static int detect_name_listed(PyObject *names, PyObject *name) {
    if (names == NULL) {
        return 0;
    }
    PyObject *folded = PyObject_CallMethod(name, "casefold", NULL);
    if (folded == NULL) { /* GCOVR_EXCL_BR_LINE: casefold cannot fail for a str */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    int listed = PySequence_Contains(names, folded);
    Py_DECREF(folded);
    return listed; /* GCOVR_EXCL_BR_LINE: membership cannot fail for a str in a set */
}

/* The case-folded members of `names` as a set, or NULL when there are none to match against. */
static PyObject *detect_folded_set(PyObject *names) {
    if (names == Py_None) {
        return NULL;
    }
    PyObject *folded = PySet_New(NULL);
    /* GCOVR_EXCL_BR_START: allocation and iteration over a validated tuple cannot fail */
    if (folded == NULL) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    PyObject *iterator = PyObject_GetIter(names);
    if (iterator == NULL) {
        Py_DECREF(folded); /* GCOVR_EXCL_LINE */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    PyObject *name;
    while ((name = PyIter_Next(iterator)) != NULL) {
        PyObject *lower = PyObject_CallMethod(name, "casefold", NULL);
        int added = lower == NULL ? -1 : PySet_Add(folded, lower);
        Py_XDECREF(lower);
        Py_DECREF(name);
        if (added < 0) {
            Py_DECREF(iterator); /* GCOVR_EXCL_LINE */
            Py_DECREF(folded);   /* GCOVR_EXCL_LINE */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(iterator);
    /* GCOVR_EXCL_BR_STOP */
    if (PySet_GET_SIZE(folded) == 0) { /* an empty option list filters nothing, so skip the lookups entirely */
        Py_DECREF(folded);
        return NULL;
    }
    return folded;
}

/* Shape the detector's scored candidates into `out`, returning how many it wrote or -1.

   A certain result -- a byte-order mark, a declaration, a structural proof, or pure ASCII -- is one candidate at
   confidence 1.0. Otherwise each candidate's raw score becomes its share of the positive total, the best-scored entry
   per name wins (the windows-1252 model runs once per language family, so one encoding can appear twice), and the
   detector's own winner moves to the front so index 0 matches what a decode would use. */
static Py_ssize_t detect_shape(PyObject *winner, int certain, PyObject *scored, PyObject *fallback,
                               detect_candidate *out) {
    if (certain || winner == Py_None) {
        /* no winner means the stream carried no non-ASCII byte, which takes the spec's windows-1252 fallback */
        out[0].name = winner == Py_None ? fallback : winner;
        out[0].confidence = 1.0;
        return 1;
    }
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(scored); index++) {
        PyObject *pair = PyList_GET_ITEM(scored, index);
        PyObject *name = PyTuple_GET_ITEM(pair, 0);
        double score = (double)PyLong_AsLongLong(PyTuple_GET_ITEM(pair, 1));
        Py_ssize_t at = 0;
        while (at < count && PyUnicode_Compare(out[at].name, name) != 0) {
            at++;
        }
        if (at < count) { /* one encoding can be scored twice, since a model runs per language family */
            out[at].confidence = score > out[at].confidence ? score : out[at].confidence;
            continue;
        }
        out[count].name = name;
        out[count].confidence = score;
        count++;
    }
    /* score-descending, with the detector's emission order breaking ties: a stable insertion sort, which is what
       Python's sorted() gave, and the candidate count is small enough that nothing cleverer pays for itself */
    for (Py_ssize_t index = 1; index < count; index++) {
        detect_candidate held = out[index];
        Py_ssize_t at = index;
        while (at > 0 && out[at - 1].confidence < held.confidence) {
            out[at] = out[at - 1];
            at--;
        }
        out[at] = held;
    }
    double total = 0.0;
    for (Py_ssize_t index = 0; index < count; index++) {
        total += out[index].confidence > 0.0 ? out[index].confidence : 0.0;
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        out[index].confidence = out[index].confidence > 0.0 ? out[index].confidence / total : 0.0;
    }
    Py_ssize_t at = 0;
    while (at < count && PyUnicode_Compare(out[at].name, winner) != 0) {
        at++;
    }
    if (at == count) { /* the windows-1252 fallback won without surviving as a candidate itself */
        memmove(&out[1], &out[0], (size_t)count * sizeof(*out));
        out[0].name = winner;
        out[0].confidence = 0.0;
        return count + 1;
    }
    detect_candidate first = out[at];
    memmove(&out[1], &out[0], (size_t)at * sizeof(*out));
    out[0] = first;
    return count;
}

/* Build one EncodingMatch row: (name, confidence, language, had_bom, codec). */
static PyObject *detect_row(PyObject *name, double confidence, PyObject *languages, int bom) {
    PyObject *folded = PyObject_CallMethod(name, "casefold", NULL);
    if (folded == NULL) { /* GCOVR_EXCL_BR_LINE: casefold cannot fail for a str */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    PyObject *language = PyDict_GetItem(languages, folded); /* borrowed; NULL when the encoding names no language */
    PyObject *codec = th_str_format("whatwg-%U", folded);
    Py_DECREF(folded);
    if (codec == NULL) { /* GCOVR_EXCL_BR_LINE: string allocation cannot be forced to fail */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    /* the language is borrowed and absent for an encoding that names none */
    return Py_BuildValue("(OdOON)", name, confidence, language == NULL ? Py_None : language, bom ? Py_True : Py_False,
                         codec);
}

/* The row of the no-match sentinel: no encoding, no confidence, no language, no mark, no codec. */
static PyObject *detect_no_match(void) {
    return Py_BuildValue("[(OdOOO)]", Py_None, 0.0, Py_None, Py_False, Py_None);
}

/* _detect_rank(result, allowed, excluded, language, threshold, languages) -> list[tuple]

   Shape one detector result and apply the Detection options to it: the allowlist, the exclusions, the language
   preference that floats a hinted encoding to the front, and the confidence floor. Each row carries what an
   EncodingMatch holds, so the typed layer only constructs them. A None result (no bytes were ever seen) and a
   ranking every candidate was filtered out of both yield the one no-match row. */
PyObject *turbohtml_detect_rank(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *result;
    PyObject *allowed;
    PyObject *excluded;
    PyObject *language;
    double threshold;
    PyObject *languages;
    if (!PyArg_ParseTuple(args, "OOOOdO!:_detect_rank", &result, &allowed, &excluded, &language, &threshold,
                          &PyDict_Type, &languages)) {
        return NULL;
    }
    if (result == Py_None) {
        return detect_no_match();
    }
    if (!PyTuple_Check(result)) {
        PyErr_SetString(PyExc_TypeError, "_detect_rank() result must be a tuple or None");
        return NULL;
    }
    PyObject *winner = PyTuple_GET_ITEM(result, 0);
    int certain = PyObject_IsTrue(PyTuple_GET_ITEM(result, 1));
    PyObject *scored = PyTuple_GET_ITEM(result, 2);
    int bom = PyObject_IsTrue(PyTuple_GET_ITEM(result, 3));
    Py_ssize_t room = PyList_GET_SIZE(scored) + 1;
    detect_candidate *shaped = PyMem_Malloc((size_t)room * sizeof(*shaped));
    PyObject *out = PyList_New(0);
    PyObject *permitted = detect_folded_set(allowed);
    PyObject *blocked = detect_folded_set(excluded);
    /* GCOVR_EXCL_BR_START: allocation cannot be forced to fail */
    if (shaped == NULL || out == NULL || PyErr_Occurred() != NULL) {
        PyMem_Free(shaped);    /* GCOVR_EXCL_LINE */
        Py_XDECREF(out);       /* GCOVR_EXCL_LINE */
        Py_XDECREF(permitted); /* GCOVR_EXCL_LINE */
        Py_XDECREF(blocked);   /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    PyObject *fallback = PyUnicode_FromString("windows-1252");
    if (fallback == NULL) {    /* GCOVR_EXCL_BR_LINE: string allocation cannot be forced to fail */
        PyMem_Free(shaped);    /* GCOVR_EXCL_LINE */
        Py_DECREF(out);        /* GCOVR_EXCL_LINE */
        Py_XDECREF(permitted); /* GCOVR_EXCL_LINE */
        Py_XDECREF(blocked);   /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = detect_shape(winner, certain, scored, fallback, shaped);
    int status = 0;
    /* the hinted language runs first, so a preferred candidate leads without disturbing the rest of the order */
    for (int pass = 0; pass < (language == Py_None ? 1 : 2); pass++) {
        for (Py_ssize_t index = 0; index < count; index++) {
            PyObject *name = shaped[index].name;
            double confidence = shaped[index].confidence;
            if (confidence < threshold || (allowed != Py_None && detect_name_listed(permitted, name) != 1) ||
                detect_name_listed(blocked, name) == 1) {
                continue;
            }
            if (language != Py_None) {
                PyObject *folded = PyObject_CallMethod(name, "casefold", NULL);
                /* borrowed; NULL when the encoding names no language, which can never be the hinted one */
                PyObject *named = PyDict_GetItem(languages, folded);
                int preferred = confidence > 0.0 && named != NULL && PyObject_RichCompareBool(named, language, Py_EQ);
                Py_DECREF(folded);
                if (preferred != (pass == 0)) {
                    continue;
                }
            }
            PyObject *row = detect_row(name, confidence, languages, bom);
            status = row == NULL ? -1 : PyList_Append(out, row); /* GCOVR_EXCL_BR_LINE: allocation */
            Py_XDECREF(row);
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: only an allocation failure sets it */
                break;        /* GCOVR_EXCL_LINE */
            }
        }
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: only an allocation failure sets it */
            break;        /* GCOVR_EXCL_LINE */
        }
    }
    PyMem_Free(shaped);
    Py_DECREF(fallback);
    Py_XDECREF(permitted);
    Py_XDECREF(blocked);
    if (status < 0) {   /* GCOVR_EXCL_BR_LINE: every failure above is an allocation failure */
        Py_DECREF(out); /* GCOVR_EXCL_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    if (PyList_GET_SIZE(out) == 0) {
        Py_DECREF(out);
        return detect_no_match(); /* every candidate was filtered out */
    }
    return out;
}

/* Resolve a byte-order mark, then a <meta> prescan, over the stream's leading bytes; the
   caller supplies the detector's answer for when neither decides. */
static const char *detect_declared(const unsigned char *prefix, Py_ssize_t prefix_len, int *certain, int *bom) {
    const char *mark = th_detect_bom(prefix, prefix_len);
    *bom = mark != NULL;
    if (mark != NULL) {
        *certain = 1;
        return mark;
    }
    const th_encoding_entry *entry = th_encoding_prescan(prefix, prefix_len);
    if (entry != NULL) {
        *certain = 1;
        return entry->canonical;
    }
    *certain = 0;
    return NULL;
}

/* The rightmost DNS label of the host the bytes came from, or NULL for no hint. The shim has already
   checked the label is lower-case ASCII, the form chardetng's key tables hold. */
static th_tld tld_argument(const char *label) {
    return label == NULL ? TH_TLD_GENERIC : th_tld_classify(label, (Py_ssize_t)strlen(label));
}

PyObject *turbohtml_detect_encoding(PyObject *module, PyObject *args) {
    (void)module;
    const char *label = NULL;
    PyObject *data;
    if (!PyArg_ParseTuple(args, "Oz:_detect", &data, &label)) {
        return NULL;
    }
    Py_buffer view;
    if (PyObject_GetBuffer(data, &view, PyBUF_SIMPLE) < 0) {
        return NULL;
    }
    if (view.len == 0) {
        PyBuffer_Release(&view);
        Py_RETURN_NONE; /* nothing to detect: the ranker answers with the no-match row */
    }
    const unsigned char *bytes = view.buf;
    Py_ssize_t len = view.len;
    int certain, bom;
    const char *winner = detect_declared(bytes, len, &certain, &bom);
    th_detect_scores scores = {.count = 0, .structural = 0};
    if (winner == NULL) {
        const th_encoding_entry *entry = th_encoding_detect(bytes, len, tld_argument(label), &scores);
        certain = scores.structural;
        winner = entry == NULL ? NULL : entry->canonical;
    }
    PyBuffer_Release(&view);
    return detect_result(winner, certain, &scores, bom);
}

/* The streaming detector behind turbohtml.detect.EncodingDetector. It holds one
   th_detect_stream, whose candidates carry their own state between feeds, plus the leading
   bytes the byte-order-mark check and the <meta> prescan need -- the spec bounds that window
   at 1024 bytes, so the object's memory does not grow with the stream. */
typedef struct {
    PyObject_HEAD th_detect_stream stream;
    unsigned char prefix[1024];
    Py_ssize_t prefix_len;
    th_tld tld;
    int closed;
    int fed; /* whether any bytes arrived: an unfed stream has no answer */
} DetectStreamObject;

static PyObject *detect_stream_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"", NULL};
    const char *label = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "z:_DetectStream", keywords, &label)) {
        return NULL;
    }
    DetectStreamObject *self = (DetectStreamObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_detect_stream_init(&self->stream);
    self->prefix_len = 0;
    self->tld = tld_argument(label);
    self->closed = 0;
    self->fed = 0;
    return (PyObject *)self;
}

static void detect_stream_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *detect_stream_feed(PyObject *self, PyObject *arg) {
    DetectStreamObject *detector = (DetectStreamObject *)self;
    if (detector->closed) {
        PyErr_SetString(PyExc_ValueError, "the detector is closed");
        return NULL;
    }
    Py_buffer view;
    if (PyObject_GetBuffer(arg, &view, PyBUF_SIMPLE) < 0) {
        return NULL;
    }
    if (view.len == 0) {
        PyBuffer_Release(&view);
        Py_RETURN_FALSE; /* an empty chunk changes nothing */
    }
    detector->fed = 1;
    Py_ssize_t room = (Py_ssize_t)sizeof(detector->prefix) - detector->prefix_len;
    Py_ssize_t take = view.len < room ? view.len : room;
    memcpy(detector->prefix + detector->prefix_len, view.buf, (size_t)take);
    detector->prefix_len += take;
    th_detect_stream_feed(&detector->stream, view.buf, view.len, 0);
    PyBuffer_Release(&view);
    /* a byte-order mark settles the stream whatever follows */
    return PyBool_FromLong(th_detect_bom(detector->prefix, detector->prefix_len) != NULL);
}

static PyObject *detect_stream_close(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    DetectStreamObject *detector = (DetectStreamObject *)self;
    if (detector->closed) {
        PyErr_SetString(PyExc_ValueError, "the detector is closed");
        return NULL;
    }
    detector->closed = 1;
    if (!detector->fed) {
        Py_RETURN_NONE; /* no bytes were ever seen: the ranker answers with the no-match row */
    }
    /* the scanners pass the chunk to memchr and memcpy, which take no NULL pointer even at length 0 */
    th_detect_stream_feed(&detector->stream, (const unsigned char *)"", 0, 1);
    int certain, bom;
    const char *winner = detect_declared(detector->prefix, detector->prefix_len, &certain, &bom);
    th_detect_scores scores = {.count = 0, .structural = 0};
    if (winner == NULL) {
        const th_encoding_entry *entry = th_detect_stream_finish(&detector->stream, detector->tld, &scores);
        certain = scores.structural;
        winner = entry == NULL ? NULL : entry->canonical;
    }
    return detect_result(winner, certain, &scores, bom);
}

PyDoc_STRVAR(detect_stream_doc, "_DetectStream(tld, /)\n--\n\n"
                                "Score a byte stream chunk by chunk.\n\n"
                                ":param tld: the rightmost DNS label of the host, lower-case ASCII, or None.");

PyDoc_STRVAR(detect_stream_feed_doc, "feed(data, /)\n--\n\n"
                                     "Score the next chunk of the stream and report whether a leading byte-order\n"
                                     "mark has settled it.\n\n"
                                     ":param data: the next bytes.");

PyDoc_STRVAR(detect_stream_close_doc, "close()\n--\n\n"
                                      "End the stream and return (encoding, certain, ranked, bom), or None when\n"
                                      "no bytes were ever fed.\n\n"
                                      ":raises ValueError: if the detector is already closed.");

static PyMethodDef detect_stream_methods[] = {
    {"feed", detect_stream_feed, METH_O, detect_stream_feed_doc},
    {"close", detect_stream_close, METH_NOARGS, detect_stream_close_doc},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot detect_stream_slots[] = {
    {Py_tp_doc, (void *)detect_stream_doc},
    {Py_tp_new, detect_stream_new},
    {Py_tp_dealloc, detect_stream_dealloc},
    {Py_tp_methods, detect_stream_methods},
    {0, NULL},
};

PyType_Spec detect_stream_spec = {
    .name = "turbohtml._html._DetectStream",
    .basicsize = sizeof(DetectStreamObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = detect_stream_slots,
};

/* Resolve the caller's language constraints (frozensets of ISO 639-3 codes, or None for allowed)
   into a per-language allow flag. The shim always passes sets, so membership never raises. */
static int th_lang_build_allow(PyObject *allowed, PyObject *excluded, uint8_t *allow) {
    for (int lang = 0; lang < (int)(sizeof(th_lang_meta_table) / sizeof(th_lang_meta_table[0])); lang++) {
        PyObject *code = PyUnicode_FromString(th_lang_meta_table[lang].code);
        if (code == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int included = allowed == Py_None ? 1 : PySet_Contains(allowed, code);
        int denied = PySet_Contains(excluded, code);
        Py_DECREF(code);
        if (included < 0 || denied < 0) { /* GCOVR_EXCL_BR_LINE: set membership on a str never raises */
            return -1;                    /* GCOVR_EXCL_LINE: unreachable membership error */
        }
        allow[lang] = (uint8_t)(included && !denied);
    }
    return 0;
}

PyObject *turbohtml_detect_language(PyObject *module, PyObject *args) {
    (void)module;
    PyObject *text;
    PyObject *allowed;
    PyObject *excluded;
    double threshold;
    if (!PyArg_ParseTuple(args, "UOOd", &text, &allowed, &excluded, &threshold)) {
        return NULL;
    }
    uint8_t allow[sizeof(th_lang_meta_table) / sizeof(th_lang_meta_table[0])];
    if (th_lang_build_allow(allowed, excluded, allow) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return NULL;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *lowered = PyObject_CallMethod(text, "lower", NULL);
    if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower on a valid str never fails */
        return NULL;       /* GCOVR_EXCL_LINE: unreachable lowercase error */
    }
    th_lang_result result;
    int failed =
        th_lang_detect(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text), PyUnicode_KIND(lowered),
                       PyUnicode_DATA(lowered), PyUnicode_GET_LENGTH(lowered), allow, &result);
    Py_DECREF(lowered);
    if (failed < 0) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (result.lang < 0 || result.confidence < threshold) {
        return Py_BuildValue("(OdOO)", Py_None, 0.0, Py_None, Py_None); /* no language, or too faint a signal */
    }
    /* a detected language always sits on a detected script: the scan gives up before scoring languages otherwise */
    return Py_BuildValue("(sdss)", th_lang_meta_table[result.lang].code, result.confidence,
                         th_lang_script_names[result.script], th_lang_meta_table[result.lang].name);
}

/* The names a byte-order mark reports, keyed the way CPython normalizes a codec name (lowercased, every
   non-alphanumeric byte an underscore). The spec's label table does not carry them, and CPython's UTF-8 and UTF-16
   decoders already agree with it byte for byte, so these delegate rather than going through the native decoders. */
static const struct {
    const char *normalized;
    const char *codec;
} CODEC_DELEGATES[] = {{"utf_8_sig", "utf-8-sig"},
                       {"utf_16le", "utf-16-le"},
                       {"utf_16be", "utf-16-be"},
                       {"utf_32le", "utf-32-le"},
                       {"utf_32be", "utf-32-be"}};

/* _codec_label(name) -> (delegate, label) | None: resolve a whatwg-* codec name for the codecs registry. The name
   arrives lowercased and underscored up to Python 3.14 and verbatim from 3.15 on, so it is normalized here.
   Underscoring is lossy (shift_jis and shift-jis collapse), so both spellings are offered to the label table. A
   delegate names the CPython codec a byte-order-mark encoding decodes through; otherwise the label is the spec
   label the native decoder takes. */
PyObject *turbohtml_codec_label(PyObject *Py_UNUSED(module), PyObject *name) {
    if (!PyUnicode_Check(name)) {
        PyErr_SetString(PyExc_TypeError, "a codec name must be a str");
        return NULL;
    }
    PyObject *lowered = PyObject_CallMethod(name, "lower", NULL);
    if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a str */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t len;
    const char *utf8 = PyUnicode_AsUTF8AndSize(lowered, &len);
    if (utf8 == NULL) {     /* GCOVR_EXCL_BR_LINE: a lone surrogate cannot reach a codec name */
        Py_DECREF(lowered); /* GCOVR_EXCL_LINE */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    char folded[64];
    static const char prefix[] = "whatwg_";
    Py_ssize_t prefix_len = (Py_ssize_t)sizeof(prefix) - 1;
    if (len >= (Py_ssize_t)sizeof(folded) || len <= prefix_len) {
        Py_DECREF(lowered);
        Py_RETURN_NONE; /* too long for any label, or no room for a label after the prefix */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        folded[index] = utf8[index] == '-' ? '_' : utf8[index];
    }
    folded[len] = '\0';
    Py_DECREF(lowered);
    if (strncmp(folded, prefix, (size_t)prefix_len) != 0) {
        Py_RETURN_NONE;
    }
    const char *rest = folded + prefix_len;
    Py_ssize_t rest_len = len - prefix_len;
    for (size_t index = 0; index < sizeof(CODEC_DELEGATES) / sizeof(*CODEC_DELEGATES); index++) {
        if (strcmp(rest, CODEC_DELEGATES[index].normalized) == 0) {
            return Py_BuildValue("(Os)", Py_True, CODEC_DELEGATES[index].codec);
        }
    }
    if (th_encoding_lookup(rest, rest_len) != NULL) {
        return Py_BuildValue("(Os)", Py_False, rest);
    }
    char dashed[64];
    for (Py_ssize_t index = 0; index <= rest_len; index++) {
        dashed[index] = rest[index] == '_' ? '-' : rest[index];
    }
    if (th_encoding_lookup(dashed, rest_len) != NULL) {
        return Py_BuildValue("(Os)", Py_False, dashed);
    }
    Py_RETURN_NONE;
}

PyObject *turbohtml_parse(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"markup",    "encoding",         "strict",    "detect_encoding",
                               "positions", "source_locations", "scripting", "allow_declarative_shadow_roots",
                               NULL};
    PyObject *markup;
    const char *enc_arg = NULL;
    Py_ssize_t enc_len = 0;
    int strict = 0;
    int detect = 0;
    int positions = 1;
    int locations = 0;
    int scripting = 0;
    int declarative = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|$z#pppppp:parse", keywords, &markup, &enc_arg, &enc_len, &strict,
                                     &detect, &positions, &locations, &scripting, &declarative)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    if (PyUnicode_Check(markup)) {
        th_tree *tree = th_tree_parse(PyUnicode_KIND(markup), PyUnicode_DATA(markup), PyUnicode_GET_LENGTH(markup),
                                      positions, locations, scripting, declarative);
        if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (strict_raise(state, tree, strict, markup) < 0) {
            return NULL;
        }
        return tree_to_node(state, tree, markup, Py_None, 0);
    }
    if (!PyObject_CheckBuffer(markup)) {
        PyErr_SetString(PyExc_TypeError, "parse() argument must be str or a bytes-like object");
        return NULL;
    }
    return parse_bytes(state, markup, enc_arg, enc_len, strict, detect, positions, locations, scripting, declarative);
}

PyObject *turbohtml_parse_xml(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"markup", NULL};
    PyObject *markup;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "U:parse_xml", keywords, &markup)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    th_tree *tree = th_tree_parse_xml(PyUnicode_KIND(markup), PyUnicode_DATA(markup), PyUnicode_GET_LENGTH(markup));
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (strict_raise(state, tree, 1, NULL) < 0) { /* XML always raises the first well-formedness error */
        return NULL;
    }
    return tree_to_node(state, tree, markup, Py_None, 0);
}

PyObject *turbohtml_tree_parse_fragment(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {
        "html", "context", "positions", "source_locations", "scripting", "allow_declarative_shadow_roots", NULL};
    PyObject *text;
    PyObject *context_obj = NULL;
    int positions = 1;
    int locations = 0;
    int scripting = 0;
    int declarative = 0;
    /* require str: the "s#" format also accepts bytes, which would decode as latin-1 garbage */
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "U|U$pppp:parse_fragment", keywords, &text, &context_obj, &positions,
                                     &locations, &scripting, &declarative)) {
        return NULL;
    }
    const char *context = "div";
    Py_ssize_t context_len = 3;
    if (context_obj != NULL) {
        context = PyUnicode_AsUTF8AndSize(context_obj, &context_len);
        if (context == NULL) { /* a lone-surrogate context has no UTF-8 form */
            return NULL;
        }
        if (!th_tree_fragment_context_known(context, context_len)) {
            PyErr_Format(PyExc_ValueError,
                         "context must be a known element tag (optionally namespaced, e.g. 'svg path'), not %R",
                         context_obj);
            return NULL;
        }
    }
    th_tree *tree = th_tree_parse_fragment(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text),
                                           context, context_len, positions, locations, scripting, declarative);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree_to_node(PyModule_GetState(module), tree, text, Py_None, 0);
}

/* The public IncrementalParser: a push parser that owns a C th_stream plus, once
   the first bytes chunk arrives, a stateful incremental decoder for the chosen
   encoding so a multi-byte character split across two chunks decodes correctly.
   stream is NULL once close() has handed the tree off (or __exit__ discarded it),
   marking the parser spent. */
typedef struct {
    PyObject_HEAD th_stream *stream;
    PyObject *encoding;               /* the encoding label the caller gave, reported back as the tree's encoding */
    PyObject *unicode_decoder;        /* a CPython incremental decoder for UTF-8 and UTF-16; NULL for the rest */
    const th_encoding_entry *labeled; /* the encoding argument, resolved on the first bytes feed */
    const th_encoding_entry *entry;   /* the decoding encoding, set once the byte-order-mark sniff settles */
    unsigned char head[3];            /* the leading bytes held back while they could still be a byte-order mark */
    Py_ssize_t head_len;
    th_decoder decoder; /* carries ISO-2022-JP's mode across a chunk boundary */
    Py_UCS4 *scratch;   /* grown to the largest chunk seen, so a chunked decode allocates once */
    Py_ssize_t scratch_len;
    unsigned char tail[4]; /* the incomplete sequence a chunk ended on; gb18030 needs the most, four bytes */
    Py_ssize_t tail_len;
    int replaced; /* the replacement encoding owes the stream exactly one U+FFFD, however many chunks arrive */
} StreamObject;

static PyObject *stream_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"encoding", "positions", "source_locations", NULL};
    PyObject *encoding = NULL;
    int positions = 1;
    int locations = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$Upp:IncrementalParser", keywords, &encoding, &positions,
                                     &locations)) {
        return NULL;
    }
    StreamObject *self = (StreamObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->labeled = NULL;
    self->entry = NULL;
    self->head_len = 0;
    self->unicode_decoder = NULL;
    self->scratch = NULL;
    self->scratch_len = 0;
    self->tail_len = 0;
    self->replaced = 0;
    self->encoding = encoding != NULL ? Py_NewRef(encoding) : PyUnicode_FromString("utf-8");
    if (self->encoding == NULL) { /* GCOVR_EXCL_BR_LINE: the literal "utf-8" always builds */
        Py_DECREF(self);          /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->stream = th_stream_new(positions, locations);
    if (self->stream == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(self);         /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return (PyObject *)self;
}

static void stream_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    StreamObject *parser = (StreamObject *)self;
    if (parser->stream != NULL) {
        th_stream_free(parser->stream);
    }
    Py_XDECREF(parser->encoding);
    Py_XDECREF(parser->unicode_decoder);
    PyMem_Free(parser->scratch);
    type->tp_free(self);
    Py_DECREF(type);
}

/* Decode the held-back tail plus this chunk with the native decoder, stashing whatever it could not finish. */
static PyObject *stream_decode_legacy(StreamObject *parser, const unsigned char *chunk, Py_ssize_t chunk_len,
                                      int final) {
    Py_ssize_t len = parser->tail_len + chunk_len;
    /* the previous chunk usually ended on a sequence boundary, and then the chunk decodes where it lies */
    const unsigned char *joined = chunk;
    unsigned char *spliced = NULL;
    if (parser->tail_len > 0) {
        spliced = PyMem_Malloc((size_t)len);
        if (spliced == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(spliced, parser->tail, (size_t)parser->tail_len);
        memcpy(spliced + parser->tail_len, chunk, (size_t)chunk_len);
        joined = spliced;
    }
    if (len > parser->scratch_len) {
        /* the held-back tail makes every chunk a byte or two longer than the last, so grow geometrically rather than
           reallocate on each feed */
        Py_ssize_t capacity = parser->scratch_len > 0 ? parser->scratch_len : 64;
        while (capacity < len) {
            capacity *= 2;
        }
        Py_UCS4 *grown = PyMem_Realloc(parser->scratch, (size_t)capacity * sizeof(Py_UCS4));
        if (grown == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(spliced);     /* GCOVR_EXCL_LINE: allocation-failure path */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        parser->scratch = grown;
        parser->scratch_len = capacity;
    }
    Py_UCS4 *scratch = parser->scratch;
    parser->decoder.buf = joined;
    parser->decoder.len = len;
    parser->decoder.pos = 0;
    Py_ssize_t consumed = 0;
    Py_UCS4 maxchar = 0;
    Py_ssize_t count = th_decode_chunk(&parser->decoder, scratch, final, &consumed, &maxchar);
    parser->tail_len = len - consumed;
    memcpy(parser->tail, joined + consumed, (size_t)parser->tail_len);
    PyMem_Free(spliced);
    return th_points_to_str(scratch, count, maxchar);
}

/* Resolve the label on the first bytes feed, so an unsupported one raises LookupError there rather than at close. */
static int stream_resolve_label(StreamObject *parser) {
    Py_ssize_t label_len = 0;
    const char *label = PyUnicode_AsUTF8AndSize(parser->encoding, &label_len);
    if (label == NULL) { /* a lone-surrogate encoding name has no UTF-8 form */
        return -1;
    }
    parser->labeled = th_encoding_lookup(label, label_len);
    if (parser->labeled == NULL) {
        PyErr_Format(PyExc_LookupError, "unknown encoding: %s", label);
        return -1;
    }
    return 0;
}

/* Set up the decoder for the encoding the byte-order-mark sniff settled on. */
static int stream_start_decoder(StreamObject *parser, const th_encoding_entry *entry) {
    if (entry->kind == TH_DEC_UTF8 || entry->kind == TH_DEC_UTF16LE || entry->kind == TH_DEC_UTF16BE) {
        /* CPython's UTF-8 and UTF-16 decoders match the spec, and their incremental form already carries a sequence
           split across chunks, so there is nothing for a native chunk decoder to add */
        const char *codec = entry->kind == TH_DEC_UTF8      ? "utf-8"
                            : entry->kind == TH_DEC_UTF16LE ? "utf-16-le"
                                                            : "utf-16-be";
        parser->unicode_decoder = PyCodec_IncrementalDecoder(codec, "replace");
        if (parser->unicode_decoder == NULL) { /* GCOVR_EXCL_BR_LINE: all three are built-in codecs */
            return -1;                         /* GCOVR_EXCL_LINE: codec-lookup failure */
        }
    }
    th_decode_init(&parser->decoder, entry, NULL, 0);
    parser->entry = entry;
    return 0;
}

/* Decode a chunk with the settled encoding's decoder. */
static PyObject *stream_decode_settled(StreamObject *parser, const unsigned char *chunk, Py_ssize_t chunk_len,
                                       int final) {
    if (parser->unicode_decoder != NULL) {
        return PyObject_CallMethod(parser->unicode_decoder, "decode", "y#i", (const char *)chunk, chunk_len, final);
    }
    if (parser->entry->kind == TH_DEC_REPLACEMENT) {
        /* the whole stream decodes to one U+FFFD however it is chunked, so only the first non-empty chunk emits it */
        if (chunk_len == 0 || parser->replaced) {
            return PyUnicode_New(0, 0);
        }
        parser->replaced = 1;
        return PyUnicode_FromOrdinal(0xFFFD);
    }
    if (parser->entry->kind == TH_DEC_SINGLE_BYTE || parser->entry->kind == TH_DEC_X_USER_DEFINED) {
        /* one code point per byte and no state, so a chunk decodes on its own with nothing held back */
        return th_decode(parser->entry, chunk, chunk_len);
    }
    return stream_decode_legacy(parser, chunk, chunk_len, final);
}

/* Whether the bytes held so far are a proper prefix of a byte-order mark, so the next byte decides it. */
static int stream_bom_pending(const unsigned char *head, Py_ssize_t len) {
    static const unsigned char utf8_bom[] = {0xEF, 0xBB};
    return len == 0 || (len < 3 && memcmp(head, utf8_bom, (size_t)len) == 0) ||
           (len == 1 && (head[0] == 0xFE || head[0] == 0xFF));
}

/* Hold the leading bytes until the byte-order-mark sniff settles, then decode them and the rest of the chunk with the
   encoding a mark names, or the parser's encoding when there is none. Per the Encoding standard's decode, a mark
   outranks the encoding argument and never reaches the document. */
static PyObject *stream_sniff(StreamObject *parser, const unsigned char *chunk, Py_ssize_t chunk_len, int final) {
    Py_ssize_t take = chunk_len < 3 - parser->head_len ? chunk_len : 3 - parser->head_len;
    memcpy(parser->head + parser->head_len, chunk, (size_t)take);
    parser->head_len += take;
    if (!final && stream_bom_pending(parser->head, parser->head_len)) {
        return PyUnicode_New(0, 0);
    }
    const th_encoding_entry *marked = NULL;
    Py_ssize_t skip = th_encoding_bom(parser->head, parser->head_len, &marked);
    if (stream_start_decoder(parser, marked != NULL ? marked : parser->labeled) < 0) { /* GCOVR_EXCL_BR_LINE: OOM */
        return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *head = stream_decode_settled(parser, parser->head + skip, parser->head_len - skip, 0);
    if (head == NULL) { /* GCOVR_EXCL_BR_LINE: replace-mode decoding fails only on allocation */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *rest = stream_decode_settled(parser, chunk + take, chunk_len - take, final);
    if (rest == NULL) {  /* GCOVR_EXCL_BR_LINE: replace-mode decoding fails only on allocation */
        Py_DECREF(head); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *joined = PyUnicode_Concat(head, rest);
    Py_DECREF(head);
    Py_DECREF(rest);
    return joined;
}

/* Decode a bytes chunk with the same WHATWG decoder parse(bytes) uses. final flushes whatever the last chunk boundary
   held back. */
static PyObject *stream_decode(StreamObject *parser, const unsigned char *chunk, Py_ssize_t chunk_len, int final) {
    if (parser->labeled == NULL && stream_resolve_label(parser) < 0) {
        return NULL;
    }
    if (parser->entry == NULL) {
        return stream_sniff(parser, chunk, chunk_len, final);
    }
    return stream_decode_settled(parser, chunk, chunk_len, final);
}

/* Feed already-decoded code points to the C stream; -1 with an exception set on
   allocation failure. */
static int stream_feed_str(StreamObject *parser, PyObject *text) {
    int rc = th_stream_feed(parser->stream, PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text));
    if (rc < 0) {         /* GCOVR_EXCL_BR_LINE: only an allocation failure returns -1 */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

static PyObject *stream_feed_locked(StreamObject *parser, PyObject *data) {
    if (parser->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "cannot feed a closed IncrementalParser");
        return NULL;
    }
    if (PyUnicode_Check(data)) {
        if (stream_feed_str(parser, data) < 0) { /* GCOVR_EXCL_BR_LINE: only an allocation failure returns -1 */
            return NULL;                         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_RETURN_NONE;
    }
    if (!PyObject_CheckBuffer(data)) {
        PyErr_SetString(PyExc_TypeError, "feed() argument must be str or a bytes-like object");
        return NULL;
    }
    Py_buffer view;
    if (PyObject_GetBuffer(data, &view, PyBUF_SIMPLE) < 0) { /* GCOVR_EXCL_BR_LINE: bytes expose a simple buffer */
        return NULL;                                         /* GCOVR_EXCL_LINE: buffer-acquisition failure */
    }
    PyObject *decoded = stream_decode(parser, view.buf, view.len, 0);
    PyBuffer_Release(&view);
    if (decoded == NULL) {
        return NULL;
    }
    int failed = stream_feed_str(parser, decoded);
    Py_DECREF(decoded);
    if (failed < 0) { /* GCOVR_EXCL_BR_LINE: only an allocation failure returns -1 */
        return NULL;  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(stream_feed_doc, "feed(data)\n--\n\n"
                              "Push a chunk of markup, so the source need never be held whole in memory.\n"
                              "Raises ValueError once the parser is closed.\n\n"
                              ":param data: str (fed as code points) or a bytes-like object decoded with\n"
                              "    the parser's encoding, an incomplete trailing multi-byte sequence held\n"
                              "    back until the next chunk.\n"
                              ":raises TypeError: if data is neither a str nor a bytes-like object.\n"
                              ":raises ValueError: if the parser is already closed.\n"
                              ":raises LookupError: if the parser's encoding names a codec Python does not\n"
                              "    know (raised on the first bytes chunk).");

static PyObject *stream_feed(PyObject *self, PyObject *data) {
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(self); /* the C stream is mutated as the chunk is consumed */
    result = stream_feed_locked((StreamObject *)self, data);
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *stream_close_locked(StreamObject *parser, module_state *state) {
    if (parser->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "IncrementalParser is already closed");
        return NULL;
    }
    if (parser->labeled != NULL) {
        /* flush any bytes the decoder or the byte-order-mark sniff held back at the last chunk boundary */
        PyObject *tail = stream_decode(parser, (const unsigned char *)"", 0, 1);
        if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: a final empty decode cannot fail once the label resolved */
            return NULL;    /* GCOVR_EXCL_LINE: decode-failure path */
        }
        int failed = stream_feed_str(parser, tail);
        Py_DECREF(tail);
        if (failed < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    th_tree *tree = th_stream_finish(parser->stream);
    if (tree == NULL) {                 /* GCOVR_EXCL_BR_LINE: finish only fails on allocation */
        th_stream_free(parser->stream); /* GCOVR_EXCL_LINE: allocation-failure path */
        parser->stream = NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_stream_free(parser->stream); /* frees the tokenizer; the tree is the caller's now */
    parser->stream = NULL;
    if (parser->entry == NULL) { /* only str chunks were fed, so the tree has no source encoding */
        return tree_to_node(state, tree, Py_None, Py_None, 0);
    }
    /* report the canonical name, as parse(bytes) does; the label the caller passed may be one of its many aliases */
    PyObject *canonical = PyUnicode_FromString(parser->entry->canonical);
    if (canonical == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = tree_to_node(state, tree, Py_None, canonical, 1);
    Py_DECREF(canonical);
    return node;
}

PyDoc_STRVAR(stream_close_doc, "close()\n--\n\n"
                               "Signal end of input, flushing the decoder and tokenizer and applying the\n"
                               "missing html/head/body rules. Raises ValueError if the parser is already\n"
                               "closed.\n\n"
                               ":returns: the finished Document.");

static PyObject *stream_close(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    module_state *state = PyType_GetModuleState(Py_TYPE(self));
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(self); /* the C stream is finished and handed off */
    result = stream_close_locked((StreamObject *)self, state);
    Py_END_CRITICAL_SECTION();
    return result;
}

PyDoc_STRVAR(stream_enter_doc, "__enter__()\n--\n\nEnter a with block; returns the parser itself.");

static PyObject *stream_enter(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return Py_NewRef(self);
}

PyDoc_STRVAR(stream_exit_doc, "__exit__(*exc)\n--\n\n"
                              "Leave a with block. If close() was not called the in-progress parse is\n"
                              "discarded and its memory released, so abandoning a stream early is cheap.");

static PyObject *stream_exit(PyObject *self, PyObject *Py_UNUSED(args)) {
    StreamObject *parser = (StreamObject *)self;
    Py_BEGIN_CRITICAL_SECTION(self); /* the C stream is released if still open */
    if (parser->stream != NULL) {
        th_stream_free(parser->stream);
        parser->stream = NULL;
    }
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

static PyMethodDef stream_methods[] = {
    {"feed", stream_feed, METH_O, stream_feed_doc},
    {"close", stream_close, METH_NOARGS, stream_close_doc},
    {"__enter__", stream_enter, METH_NOARGS, stream_enter_doc},
    {"__exit__", stream_exit, METH_VARARGS, stream_exit_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(stream_doc, "IncrementalParser(*, encoding='utf-8', positions=True, source_locations=False)\n--\n\n"
                         "A push parser that builds a Document from chunks fed with feed(), so a\n"
                         "document arriving over a stream never has to be held whole in memory. Feed\n"
                         "str or bytes chunks in any size, then call close() for the finished\n"
                         "Document. For a whole string or bytes at once use parse().\n\n"
                         ":param encoding: codec used to decode any bytes chunks.\n"
                         ":param positions: whether to record each element's source line and column;\n"
                         "    pass False to skip it when memory or speed matters more.\n"
                         ":param source_locations: whether to record each element's granular start-\n"
                         "    and end-tag and per-attribute spans, read via Element.source_location.\n"
                         "    The spans index the whole source, so the parser keeps every chunk for\n"
                         "    Node.to_source() instead of discarding what it has consumed.");

static PyType_Slot stream_slots[] = {
    {Py_tp_doc, (void *)stream_doc},
    {Py_tp_new, stream_new},
    {Py_tp_dealloc, stream_dealloc},
    {Py_tp_methods, stream_methods},
    {0, NULL},
};

static PyType_Spec stream_spec = {
    .name = "turbohtml._html.IncrementalParser",
    .basicsize = sizeof(StreamObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = stream_slots,
};

/* This node's children as a fresh list of wrappers, so pickling an element
   recurses into each child. */
static PyObject *node_children_list(PyObject *self) {
    NodeObject *node = (NodeObject *)self;
    module_state *state = state_of(self);
    PyObject *list = PyList_New(0);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (th_node *child = node->node->first_child; child != NULL; child = child->next_sibling) {
        PyObject *wrapped = node_wrap(state, node->handle, child);
        if (wrapped == NULL || PyList_Append(list, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
            Py_XDECREF(wrapped);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(list);                                       /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(wrapped);
    }
    return list;
}

/* The pickle payload for this node: the leaf data, the element tag and attribute
   dict, the doctype identifiers, the document's quirks mode, None for a fragment, or
   (host, mode) for a shadow root. A document or fragment carries its children like an
   element does, so the round-trip rebuilds the structure itself (adjacent Text nodes,
   template contents, a doctype's identifiers) rather than reparsing serialized markup. */
static PyObject *node_pickle_data(PyObject *self, th_node *node) {
    switch ((enum th_node_type)node->type) { /* GCOVR_EXCL_BR_LINE: node types are exhaustive */
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        return str_from_accessor(th_node_data, tree_of(self), node);
    case TH_NODE_PI:
        return Py_BuildValue("(NN)", pi_get_target(self, NULL), pi_get_data(self, NULL));
    case TH_NODE_DOCTYPE:
        return Py_BuildValue("(NNN)", doctype_get_name(self, NULL), doctype_get_public_id(self, NULL),
                             doctype_get_system_id(self, NULL));
    case TH_NODE_ELEMENT: {
        PyObject *view = element_get_attrs(self, NULL);
        if (view == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *attrs = PyObject_CallFunctionObjArgs((PyObject *)&PyDict_Type, view, NULL);
        Py_DECREF(view);
        if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (node->ns == TH_NS_HTML) {
            return Py_BuildValue("(NN)", element_get_tag(self, NULL), attrs);
        }
        /* a foreign (SVG/MathML) element carries its namespace so the round-trip keeps it */
        return Py_BuildValue("(NNi)", element_get_tag(self, NULL), attrs, (int)node->ns);
    }
    case TH_NODE_DOCUMENT:
        return PyLong_FromLong(th_tree_quirks(tree_of(self)));
    case TH_NODE_CONTENT:
        if (th_node_is_shadow_root(node)) {
            PyObject *host =
                node_wrap(state_of(self), ((NodeObject *)self)->handle, th_shadow_host(tree_of(self), node));
            return host == NULL ? NULL : Py_BuildValue("(Ni)", host, th_shadow_mode(node)); /* GCOVR_EXCL_BR_LINE */
        }
        Py_RETURN_NONE;
    }
    Py_RETURN_NONE; /* GCOVR_EXCL_LINE: unreachable, the switch is exhaustive */
}

TH_NODE_API(, PyObject *, node_reduce, (PyObject * self, PyObject *ignored), (self, ignored),
            (PyObject * self, PyObject *Py_UNUSED(ignored)), (NodeObject *)self, NULL) {
    th_node *node = ((NodeObject *)self)->node;
    PyObject *reconstruct = PyObject_GetAttrString(PyType_GetModule(Py_TYPE(self)), "_reconstruct");
    if (reconstruct == NULL) { /* GCOVR_EXCL_BR_LINE: the module always carries _reconstruct */
        return NULL;           /* GCOVR_EXCL_LINE: unreachable */
    }
    PyObject *data = node_pickle_data(self, node);
    int has_children = node->type == TH_NODE_ELEMENT || node->type == TH_NODE_DOCUMENT || node->type == TH_NODE_CONTENT;
    PyObject *children = has_children ? node_children_list(self) : PyList_New(0);
    if (data == NULL || children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_XDECREF(data);                   /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(children);               /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(reconstruct);             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the xml flag rebuilds a document, fragment, or element under XML naming rules */
    PyObject *result =
        Py_BuildValue("(O(iNNi))", reconstruct, (int)node->type, data, children, th_tree_is_xml(tree_of(self)));
    Py_DECREF(reconstruct);
    return result;
}

/* Rebuild a doctype from its (name, public_id, system_id) triple, repacking the
   identifiers into the node's stored "name \"public\" \"system\"" form. Either
   identifier may be None (not supplied); a missing one is written as an empty
   string in the text but recorded as absent in tag_flags, so the round-trip
   keeps missing distinct from present-but-empty (issue #68). */
static PyObject *reconstruct_doctype(module_state *state, PyObject *data) {
    PyObject *name;
    PyObject *public_id;
    PyObject *system_id;
    if (!PyArg_ParseTuple(data, "OOO", &name, &public_id, &system_id)) { /* GCOVR_EXCL_BR_LINE: we build this tuple */
        return NULL;                                                     /* GCOVR_EXCL_LINE: unreachable */
    }
    int has_public = public_id != Py_None;
    int has_system = system_id != Py_None;
    PyObject *packed;
    if (!has_public && !has_system) {
        packed = Py_NewRef(name);
    } else {
        PyObject *empty = PyUnicode_New(0, 0);
        if (empty == NULL) { /* GCOVR_EXCL_BR_LINE: the empty string is interned and cannot fail */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        packed =
            th_str_format("%U \"%U\" \"%U\"", name, has_public ? public_id : empty, has_system ? system_id : empty);
        Py_DECREF(empty);
    }
    if (packed == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = data_node_in_fresh_tree(state, TH_NODE_DOCTYPE, packed);
    Py_DECREF(packed);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *raw = ((NodeObject *)node)->node;
    raw->attr_count = has_public ? PyUnicode_GET_LENGTH(public_id) : 0; /* public-id split point */
    raw->tag_flags = (uint8_t)((has_public ? TH_DOCTYPE_HAS_PUBLIC : 0) | (has_system ? TH_DOCTYPE_HAS_SYSTEM : 0));
    return node;
}

/* A fresh document or fragment wrapper for a pickled one: a document carries its quirks mode as data. A pickle from
   before the structural format carried the serialized markup instead, which still reparses. */
static PyObject *reconstruct_root(PyObject *module, module_state *state, int kind, PyObject *data, int xml) {
    if (PyUnicode_Check(data)) {
        PyObject *call_args = PyTuple_Pack(1, data);
        if (call_args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *node = xml ? turbohtml_parse_xml(module, call_args, NULL) : turbohtml_parse(module, call_args, NULL);
        Py_DECREF(call_args);
        return node;
    }
    int quirks = kind == TH_NODE_DOCUMENT ? PyObject_IsTrue(data) : 0;
    if (quirks < 0) {
        return NULL;
    }
    th_tree *tree = th_tree_new_rooted((enum th_node_type)kind, xml, quirks);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *empty = PyUnicode_New(0, 0);
    if (empty == NULL) {    /* GCOVR_EXCL_BR_LINE: the empty string is interned and cannot fail */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = tree_to_node(state, tree, empty, Py_None, 0);
    Py_DECREF(empty);
    return node;
}

/* A pickled shadow root: attach one of the pickled mode to the rebuilt host and return it. */
static PyObject *reconstruct_shadow_root(module_state *state, PyObject *data) {
    PyObject *host;
    int mode;
    if (!PyArg_ParseTuple(data, "O!i", (PyTypeObject *)state->element_type, &host, &mode)) {
        return NULL;
    }
    if (mode != 0 && mode != 1) { /* guard against a crafted payload */
        PyErr_SetString(PyExc_ValueError, "shadow root mode out of range");
        return NULL;
    }
    th_node *host_node = ((NodeObject *)host)->node;
    if (th_element_shadow_root(tree_of(host), host_node) != NULL) {
        PyErr_SetString(PyExc_ValueError, "the host already has a shadow root");
        return NULL;
    }
    th_node *root = th_element_attach_shadow(tree_of(host), host_node, mode);
    if (root == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return node_wrap(state, ((NodeObject *)host)->handle, root);
}

PyObject *turbohtml_reconstruct(PyObject *module, PyObject *args) {
    int kind;
    PyObject *data;
    PyObject *children;
    int xml = 0; /* set for a node pickled from a parse_xml tree */
    if (!PyArg_ParseTuple(args, "iOO|i", &kind, &data, &children, &xml)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *node;
    switch (kind) {
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        node = data_node_in_fresh_tree(state, kind, data);
        break;
    case TH_NODE_PI:
        node = PyObject_CallObject(state->pi_type, data);
        break;
    case TH_NODE_ELEMENT: {
        /* the parser keeps characters Element() rejects (e.g. "a<b"), so reconstruct
           through the trusted builder rather than the validating public constructor */
        PyObject *tag;
        PyObject *element_attrs;
        int ns = TH_NS_HTML; /* an HTML element omits the namespace; a foreign one appends it */
        /* GCOVR_EXCL_START: node_reduce builds this (tag, attrs[, ns]) tuple, so the parse never fails */
        if (!PyArg_ParseTuple(data, "UO|i", &tag, &element_attrs, &ns)) {
            return NULL;
        }
        /* GCOVR_EXCL_STOP */
        if (ns < TH_NS_HTML || ns > TH_NS_MATHML) { /* guard the namespaces[] index against a crafted payload */
            PyErr_SetString(PyExc_ValueError, "namespace out of range");
            return NULL;
        }
        node = make_element((PyTypeObject *)state->element_type, tag, element_attrs, xml, ns != TH_NS_HTML);
        if (node != NULL) {
            ((NodeObject *)node)->node->ns = (uint8_t)ns;
        }
        break;
    }
    case TH_NODE_DOCTYPE:
        node = reconstruct_doctype(state, data);
        break;
    case TH_NODE_CONTENT:
        node = PyTuple_Check(data) ? reconstruct_shadow_root(state, data)
                                   : reconstruct_root(module, state, kind, data, xml);
        break;
    default: /* TH_NODE_DOCUMENT */
        node = reconstruct_root(module, state, kind, data, xml);
        break;
    }
    if (node == NULL) {
        return NULL;
    }
    /* link each rebuilt child straight into the node: a document has no append(), and a fragment child (a template's
       contents) must land as a child rather than be spliced */
    th_node *parent = ((NodeObject *)node)->node;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(children); index++) {
        th_node *child = adopt_child((NodeObject *)node, parent, PyList_GET_ITEM(children, index));
        if (child == NULL) {
            Py_DECREF(node);
            return NULL;
        }
        th_node_append_child(parent, child);
    }
    return node;
}

/* Build one public enum named qualname with count members, register it on the
   module, cache each member into cached_out, and store the enum object in
   *enum_out. base_is_int_enum picks IntEnum over Enum; string_values, when not
   NULL, gives each member a str value, otherwise members take their index. doc,
   when not NULL, becomes the enum class docstring so the reference is not blank. */
static int build_enum(PyObject *module, const char *qualname, int base_is_int_enum, const char *const *names, int count,
                      const char *const *string_values, const char *doc, PyObject **cached_out, PyObject **enum_out) {
    PyObject *members = PyDict_New();
    if (members == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < count; index++) {
        PyObject *value = string_values != NULL ? PyUnicode_FromString(string_values[index]) : PyLong_FromLong(index);
        /* allocation failure cannot be forced from a test */
        if (value == NULL || PyDict_SetItemString(members, names[index], value) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(value);  /* GCOVR_EXCL_LINE: alloc-failure path */
            Py_DECREF(members); /* GCOVR_EXCL_LINE: alloc-failure path */
            return -1;          /* GCOVR_EXCL_LINE: alloc-failure path */
        }
        Py_DECREF(value);
    }
    PyObject *enum_module = PyImport_ImportModule("enum");
    if (enum_module == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(members);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *enum_type = PyObject_GetAttrString(enum_module, base_is_int_enum ? "IntEnum" : "Enum");
    Py_DECREF(enum_module);
    if (enum_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(members);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *args = Py_BuildValue("(sO)", qualname, members);
    Py_DECREF(members);
    PyObject *kwargs = Py_BuildValue("{s:s,s:s}", "module", "turbohtml", "qualname", qualname);
    PyObject *built = NULL;
    if (args != NULL && kwargs != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        built = PyObject_Call(enum_type, args, kwargs);
    }
    Py_DECREF(enum_type);
    Py_XDECREF(args);
    Py_XDECREF(kwargs);
    if (built == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *docstring = PyUnicode_FromString(doc);
    /* allocation failure cannot be forced from a test */
    if (docstring == NULL || PyObject_SetAttrString(built, "__doc__", docstring) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(docstring); /* GCOVR_EXCL_LINE: alloc-failure path */
        Py_DECREF(built);      /* GCOVR_EXCL_LINE: alloc-failure path */
        return -1;             /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    Py_DECREF(docstring);
    for (int index = 0; index < count; index++) {
        cached_out[index] = PyObject_GetAttrString(built, names[index]);
        if (cached_out[index] == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(built);            /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    *enum_out = built;
    return PyModule_AddObjectRef(module, qualname, built);
}

static int build_namespace_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_NAMESPACE_COUNT] = {"HTML", "SVG", "MATHML"};
    static const char *const values[TH_NAMESPACE_COUNT] = {"html", "svg", "math"};
    static const char doc[] = "The XML namespace an Element belongs to, as reported by Element.namespace.\n\n"
                              "HTML is the ordinary HTML namespace; SVG is inline <svg> content; MATHML is\n"
                              "inline <math> content. Each member's value is the namespace short name "
                              "(\"html\", \"svg\", \"math\").";
    return build_enum(module, "Namespace", 0, names, TH_NAMESPACE_COUNT, values, doc, state->namespaces,
                      &state->namespace_enum);
}

static int build_axis_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_AXIS_COUNT] = {"DESCENDANTS",       "CHILDREN",  "ANCESTORS", "NEXT_SIBLINGS",
                                                     "PREVIOUS_SIBLINGS", "FOLLOWING", "PRECEDING"};
    static const char doc[] = "The direction find()/find_all() walk from a node, passed as their axis argument.\n\n"
                              "DESCENDANTS visits every node in the subtree in document order (the default);\n"
                              "CHILDREN only the direct children; ANCESTORS the parent chain up to the document;\n"
                              "NEXT_SIBLINGS the following siblings and PREVIOUS_SIBLINGS the preceding ones;\n"
                              "FOLLOWING every node after this one in document order (skipping its descendants)\n"
                              "and PRECEDING every node before it (skipping its ancestors).";
    return build_enum(module, "Axis", 1, names, TH_AXIS_COUNT, NULL, doc, state->axes, &state->axis_enum);
}

static int build_formatter_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_FORMATTER_COUNT] = {"WHATWG", "MINIMAL", "NAMED_ENTITIES"};
    static const char *const values[TH_FORMATTER_COUNT] = {"whatwg", "minimal", "named"};
    static const char doc[] = "The character-escaping policy serialize()/encode() apply, passed as formatter.\n\n"
                              "WHATWG escapes exactly what the HTML serialization algorithm requires (the\n"
                              "default); MINIMAL escapes &, < and > everywhere and \" in attribute values,\n"
                              "which every formatter writes inside double quotes; NAMED_ENTITIES prefers named\n"
                              "character references where one exists.\n\n"
                              "Every formatter escapes < and > in attribute values as well as in text.";
    return build_enum(module, "Formatter", 0, names, TH_FORMATTER_COUNT, values, doc, state->formatters,
                      &state->formatter_enum);
}

static int cache_pattern_type(module_state *state) {
    PyObject *re_module = PyImport_ImportModule("re");
    if (re_module == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->pattern_type = PyObject_GetAttrString(re_module, "Pattern");
    if (state->pattern_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(re_module);          /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->re_compile = PyObject_GetAttrString(re_module, "compile");
    Py_DECREF(re_module);
    if (state->re_compile == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

/* Create a heap type subclassing Node, register it on the module, and stamp its
   single-field __match_args__ for structural pattern use. */
static PyObject *register_subtype(PyObject *module, PyType_Spec *spec, PyObject *base, const char *name,
                                  const char *match_arg1, const char *match_arg2) {
    PyObject *bases = PyTuple_Pack(1, base);
    if (bases == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *type = PyType_FromModuleAndSpec(module, spec, bases);
    Py_DECREF(bases);
    if (type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *tuple = match_arg1 == NULL   ? PyTuple_New(0)
                      : match_arg2 != NULL ? Py_BuildValue("(ss)", match_arg1, match_arg2)
                                           : Py_BuildValue("(s)", match_arg1);
    /* allocation failure cannot be forced from a test */
    if (tuple == NULL || PyObject_SetAttrString(type, "__match_args__", tuple) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(tuple); /* GCOVR_EXCL_LINE: alloc-failure path */
        Py_DECREF(type);   /* GCOVR_EXCL_LINE: alloc-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    Py_DECREF(tuple);
    /* allocation failure cannot be forced from a test */
    if (PyModule_AddObjectRef(module, name, type) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_DECREF(type);                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return type;
}

/* Register type as a collections.abc.MutableMapping: the attrs view implements the whole interface, so
   isinstance(element.attrs, MutableMapping) holds. collections.abc always imports and provides the class, so the
   failure paths are unreachable. */
static int register_mutable_mapping(PyObject *type) {
    PyObject *abc = PyImport_ImportModule("collections.abc");
    if (abc == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    PyObject *mutable_mapping = PyObject_GetAttrString(abc, "MutableMapping");
    Py_DECREF(abc);
    if (mutable_mapping == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    PyObject *registered = PyObject_CallMethod(mutable_mapping, "register", "O", type);
    Py_DECREF(mutable_mapping);
    if (registered == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;            /* GCOVR_EXCL_LINE */
    }
    Py_DECREF(registered);
    return 0;
}

int tree_register(PyObject *module, module_state *state) {
    /* allocation failure cannot be forced from a test */
    if (build_namespace_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (build_axis_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (build_formatter_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (cache_pattern_type(state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->minify_type = PyType_FromModuleAndSpec(module, &minify_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->minify_type == NULL ||                                      /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Minify", state->minify_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->indent_type = PyType_FromModuleAndSpec(module, &indent_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->indent_type == NULL ||                                      /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Indent", state->indent_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->xpath_type = PyType_FromModuleAndSpec(module, &xpath_compiled_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->xpath_type == NULL ||                                     /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "XPath", state->xpath_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->walker_type = PyType_FromModuleAndSpec(module, &walker_spec, NULL);
    state->element_walker_type = PyType_FromModuleAndSpec(module, &element_walker_spec, NULL);
    state->tree_walker_type = PyType_FromModuleAndSpec(module, &tree_walker_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->tree_walker_type == NULL ||                                          /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "TreeWalker", state->tree_walker_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                  /* GCOVR_EXCL_LINE: alloc-fail */
    }
    state->node_iterator_type = PyType_FromModuleAndSpec(module, &node_iterator_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->node_iterator_type == NULL ||                                            /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "NodeIterator", state->node_iterator_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: alloc-fail */
    }
    state->string_walker_type = PyType_FromModuleAndSpec(module, &string_walker_spec, NULL);
    state->serialize_iter_type = PyType_FromModuleAndSpec(module, &serialize_iter_spec, NULL);
    state->handle_type = PyType_FromModuleAndSpec(module, &handle_spec, NULL);
    state->detect_stream_type = PyType_FromModuleAndSpec(module, &detect_stream_spec, NULL);
    if (state->detect_stream_type == NULL ||                                             /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "_DetectStream", state->detect_stream_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->attrs_type = PyType_FromModuleAndSpec(module, &attrs_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->walker_type == NULL || state->element_walker_type == NULL || /* GCOVR_EXCL_BR_LINE */
        state->string_walker_type == NULL ||                                /* GCOVR_EXCL_BR_LINE */
        state->serialize_iter_type == NULL ||                               /* GCOVR_EXCL_BR_LINE */
        state->handle_type == NULL || state->attrs_type == NULL) {          /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyModule_AddObjectRef(module, "_Attrs", state->attrs_type) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
        return -1;                                                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (register_mutable_mapping(state->attrs_type) < 0) { /* GCOVR_EXCL_BR_LINE: see the helper */
        return -1;                                         /* GCOVR_EXCL_LINE: import-failure path */
    }
    state->node_type = PyType_FromModuleAndSpec(module, &node_spec, NULL);
    if (state->node_type == NULL || PyModule_AddObjectRef(module, "Node", state->node_type) < 0) { /* GCOVR_EXCL_BR_LINE
                                                                                                    */
        return -1; /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    state->element_type = register_subtype(module, &element_spec, state->node_type, "Element", "tag", NULL);
    state->text_type = register_subtype(module, &text_spec, state->node_type, "Text", "data", NULL);
    state->comment_type = register_subtype(module, &comment_spec, state->node_type, "Comment", "data", NULL);
    state->doctype_type = register_subtype(module, &doctype_spec, state->node_type, "Doctype", "name", NULL);
    state->pi_type = register_subtype(module, &pi_spec, state->node_type, "ProcessingInstruction", "target", "data");
    state->cdata_type = register_subtype(module, &cdata_spec, state->node_type, "CData", "data", NULL);
    state->document_type = register_subtype(module, &document_spec, state->node_type, "Document", "root", NULL);
    state->document_fragment_type =
        register_subtype(module, &document_fragment_spec, state->node_type, "DocumentFragment", NULL, NULL);
    if (state->document_fragment_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                               /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* a ShadowRoot is a DocumentFragment with a host (DOM), so it inherits append() and the fragment insertion */
    state->shadow_root_type =
        register_subtype(module, &shadow_root_spec, state->document_fragment_type, "ShadowRoot", "mode", NULL);
    /* allocation failure cannot be forced from a test */
    if (state->element_type == NULL || state->text_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->comment_type == NULL || state->doctype_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->pi_type == NULL || state->cdata_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->document_type == NULL || state->shadow_root_type == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->parser_type = PyType_FromModuleAndSpec(module, &stream_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->parser_type == NULL ||                                                 /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "IncrementalParser", state->parser_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->parse_error_type = PyType_FromModuleAndSpec(module, &parse_error_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->parse_error_type == NULL ||                                          /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "ParseError", state->parse_error_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                  /* GCOVR_EXCL_LINE: alloc-fail */
    }
    state->parse_error_exc = PyErr_NewExceptionWithDoc(
        "turbohtml.HTMLParseError", "Raised by parse(strict=True) on the first parse error; .error is the ParseError.",
        NULL, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->parse_error_exc == NULL ||                                              /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "HTMLParseError", state->parse_error_exc) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                     /* GCOVR_EXCL_LINE: alloc-fail */
    }
    return 0;
}
