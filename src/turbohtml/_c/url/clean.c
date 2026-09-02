/* The crawl-oriented URL cleaner behind turbohtml.extract.clean_url, normalize_url and extract_links.

   url.c holds the WHATWG primitives (split, percent-encode, dot-segment removal, relative join, the query and language
   filters); this unit is the pipeline that runs them in the order the spec and courlan/w3lib users expect, and the
   page walk that feeds anchors into it. The shim keeps only the option record: the knobs arrive here as plain flags
   and name sets, and every decision about what a caller gets back -- which URLs survive, in which spelling, and which
   anchors count -- is made here.

   Normalization: lowercase scheme and host, domain-to-ASCII for a registered name (host parsing, URL standard 3.5),
   default-port removal (port state), dot-segment resolution (path state), the component percent-encode sets (1.3), the
   empty-path-to-"/" serialization of a special URL (4.5), then the beyond-spec query sort and tracker removal and the
   fragment scrub. Cleaning wraps that in the scrub of markup damage and the web-scheme, host and language gates.
   Extraction walks the anchors, resolves each against the document base, cleans it, and deduplicates the http/https
   and trailing-slash twins, first in document order winning. */

#include "core/common.h"
#include "dom/nodes.h"
#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "url/url.h"

#include <string.h>

/* The knobs of turbohtml.extract.UrlCleaning, plus the two vocabularies the shim holds as configuration. `allow` is a
   lowercased name set or None, `deny` a lowercased name set; `language` is an ISO 639-1 code or None. */
typedef struct {
    int strict;
    int trailing_slash;
    int strip_fragment;
    PyObject *allow;
    PyObject *deny;
    PyObject *content;
    PyObject *language_params;
    PyObject *language;
    PyObject *iso_639_1;
} clean_options;

/* A new set holding the lowercased spelling of every name in `names`, or None when `names` is None: the query filter
   compares decoded, lowercased parameter names, so the caller's allow and deny lists fold once per call. */
static PyObject *lowered_names(PyObject *names) {
    if (names == Py_None) {
        return Py_NewRef(Py_None);
    }
    PyObject *iterator = PyObject_GetIter(names);
    if (iterator == NULL) {
        return NULL;
    }
    PyObject *lowered = PySet_New(NULL);
    if (lowered == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(iterator); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    PyObject *name;
    while ((name = PyIter_Next(iterator)) != NULL) {
        if (!PyUnicode_Check(name)) {
            PyErr_Format(PyExc_TypeError, "query parameter names must be str, got %s", Py_TYPE(name)->tp_name);
            Py_DECREF(name);
            break;
        }
        PyObject *folded = PyObject_CallMethod(name, "lower", NULL);
        Py_DECREF(name);
        if (folded == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower only fails on allocation failure */
            break;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int added = PySet_Add(lowered, folded);
        Py_DECREF(folded);
        if (added < 0) { /* GCOVR_EXCL_BR_LINE: a str insert only fails on allocation failure */
            break;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_DECREF(iterator);
    if (PyErr_Occurred()) {
        Py_DECREF(lowered);
        return NULL;
    }
    return lowered;
}

/* Fill the options from the trailing arguments every entry point shares; returns -1 with an error set. `allow` and
   `deny` are owned by the options afterwards, so clean_options_clear must run. */
static int clean_options_take(clean_options *options, PyObject *allow, PyObject *deny) {
    options->allow = lowered_names(allow);
    if (options->allow == NULL) {
        return -1;
    }
    options->deny = lowered_names(deny);
    return options->deny == NULL ? -1 : 0;
}

static void clean_options_clear(clean_options *options) {
    Py_XDECREF(options->allow);
    Py_XDECREF(options->deny);
}

static int str_equals(PyObject *text, const char *literal) {
    return PyUnicode_CompareWithASCIIString(text, literal) == 0;
}

/* Whether `text` starts with `literal`, without materializing a str for the literal. */
static int str_has_prefix(PyObject *text, const char *literal) {
    Py_ssize_t width = (Py_ssize_t)strlen(literal);
    if (PyUnicode_GET_LENGTH(text) < width) {
        return 0;
    }
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    for (Py_ssize_t index = 0; index < width; index++) {
        if (PyUnicode_READ(kind, data, index) != (Py_UCS4)(unsigned char)literal[index]) {
            return 0;
        }
    }
    return 1;
}

static int str_holds(PyObject *text, Py_UCS4 needle) {
    return PyUnicode_FindChar(text, needle, 0, PyUnicode_GET_LENGTH(text), 1) >= 0;
}

static int str_is_ascii(PyObject *text) {
    if (PyUnicode_KIND(text) != PyUnicode_1BYTE_KIND) {
        return 0;
    }
    const unsigned char *data = PyUnicode_1BYTE_DATA(text);
    for (Py_ssize_t index = 0; index < PyUnicode_GET_LENGTH(text); index++) {
        if (data[index] >= 0x80) {
            return 0;
        }
    }
    return 1;
}

/* The ASCII (punycode) form of a registered name the way the URL standard's host parser produces it (spec 3.5): the
   lowercased host when it is already ASCII, else UTS #46 ToASCII in C; a label punycode cannot encode (an unpaired
   surrogate) leaves the lowercased host as it is, which the later encode step then rejects. */
static PyObject *ascii_host(PyObject *host) {
    PyObject *lowered = PyObject_CallMethod(host, "lower", NULL);
    if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a host */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (str_is_ascii(lowered)) {
        return lowered;
    }
    PyObject *encoded = th_url_to_ascii(lowered);
    if (encoded != NULL) {
        Py_DECREF(lowered);
        return encoded;
    }
    if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: ToASCII raises nothing else */
        Py_DECREF(lowered);                          /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                 /* GCOVR_EXCL_LINE */
    }
    PyErr_Clear();
    return lowered;
}

/* The ":port" suffix, or "" for an absent, empty, or scheme-default port (port state, URL standard 4.4). A port of
   digits is read as the integer it spells, so leading zeros fall away and "0080" is the http default. */
static PyObject *port_suffix(const th_url_parts *parts) {
    PyObject *port = parts->part[TH_URL_PORT];
    Py_ssize_t len = PyUnicode_GET_LENGTH(port);
    if (!parts->has_port || len == 0) {
        return PyUnicode_FromString("");
    }
    int kind = PyUnicode_KIND(port);
    const void *data = PyUnicode_DATA(port);
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!Py_UNICODE_ISDIGIT(PyUnicode_READ(kind, data, index))) {
            return th_str_format(":%U", port);
        }
    }
    PyObject *number = PyLong_FromUnicodeObject(port, 10);
    if (number == NULL) { /* GCOVR_EXCL_BR_LINE: a digit-only string always parses */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    static const struct {
        const char *scheme;
        long port;
    } defaults[] = {{"ftp", 21}, {"http", 80}, {"ws", 80}, {"https", 443}, {"wss", 443}};
    int overflow;
    long value = PyLong_AsLongAndOverflow(number, &overflow);
    PyObject *scheme = parts->part[TH_URL_SCHEME];
    for (size_t index = 0; index < sizeof(defaults) / sizeof(*defaults); index++) {
        if (!overflow && value == defaults[index].port && str_equals(scheme, defaults[index].scheme)) {
            Py_DECREF(number);
            return PyUnicode_FromString("");
        }
    }
    PyObject *suffix = th_str_format(":%S", number);
    Py_DECREF(number);
    return suffix;
}

/* The authority rebuilt from its normalized host and port, keeping userinfo verbatim: a registered name goes through
   domain-to-ASCII, an IPv4/IPv6 literal is already ASCII and only lowercases (IPv6 keeping its brackets). */
static PyObject *normalize_netloc(const th_url_parts *parts) {
    PyObject *host;
    if (parts->kind == TH_HOST_REGNAME) {
        host = ascii_host(parts->part[TH_URL_HOST]);
    } else {
        PyObject *lowered = PyObject_CallMethod(parts->part[TH_URL_HOST], "lower", NULL);
        if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a host */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        host = parts->kind == TH_HOST_IPV6 ? th_str_format("[%U]", lowered) : Py_NewRef(lowered);
        Py_DECREF(lowered);
    }
    if (host == NULL) { /* GCOVR_EXCL_BR_LINE: the host fold only fails on allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *suffix = port_suffix(parts);
    if (suffix == NULL) { /* GCOVR_EXCL_BR_LINE: the suffix only fails on allocation failure */
        Py_DECREF(host);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    PyObject *userinfo = parts->part[TH_URL_USERINFO];
    PyObject *netloc = PyUnicode_GET_LENGTH(userinfo) > 0 ? th_str_format("%U@%U%U", userinfo, host, suffix)
                                                          : th_str_format("%U%U", host, suffix);
    Py_DECREF(host);
    Py_DECREF(suffix);
    return netloc;
}

/* The query with denied, tracker, or non-allowlisted parameters dropped and the rest sorted. */
static PyObject *normalize_query(PyObject *query, const clean_options *options) {
    return th_url_normalize_query(query, options->allow, options->deny, options->strict, options->content,
                                  options->language_params);
}

/* The fragment percent-encoded, after scrubbing one shaped like a query string the way the query is: a `&`-joined
   list goes through the query filter, a lone `key=value` is dropped when the key is a tracker. */
static PyObject *normalize_fragment(PyObject *fragment, const clean_options *options) {
    if (!str_holds(fragment, '=')) {
        return th_url_encode_component(fragment, TH_URL_SET_FRAGMENT);
    }
    if (str_holds(fragment, '&')) {
        return normalize_query(fragment, options);
    }
    Py_ssize_t equals = PyUnicode_FindChar(fragment, '=', 0, PyUnicode_GET_LENGTH(fragment), 1);
    PyObject *key = PyUnicode_Substring(fragment, 0, equals);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: a substring only fails on allocation failure */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *decoded = th_url_percent_decode_obj(key);
    Py_DECREF(key);
    if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: the decode only fails on allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *lowered = PyObject_CallMethod(decoded, "lower", NULL);
    Py_DECREF(decoded);
    if (lowered == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower only fails on allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int tracker = th_url_is_tracker_obj(lowered);
    Py_DECREF(lowered);
    if (tracker < 0) { /* a key holding a lone surrogate has no UTF-8 form, the encoder's UnicodeEncodeError */
        return NULL;
    }
    return tracker ? PyUnicode_FromString("") : th_url_encode_component(fragment, TH_URL_SET_FRAGMENT);
}

/* The schemes urllib.parse.uses_netloc lists, the ones whose serialization always carries the "//" authority marker
   before an absolute path even when the authority is empty. */
static const char *const NETLOC_SCHEMES[] = {"ftp",   "http",    "gopher", "nntp",  "telnet",       "imap",     "wais",
                                             "file",  "mms",     "https",  "shttp", "snews",        "prospero", "rtsp",
                                             "rtsps", "rtspu",   "rsync",  "svn",   "svn+ssh",      "sftp",     "nfs",
                                             "git",   "git+ssh", "ws",     "wss",   "itms-services"};

static int scheme_uses_netloc(PyObject *scheme) {
    for (size_t index = 0; index < sizeof(NETLOC_SCHEMES) / sizeof(*NETLOC_SCHEMES); index++) {
        if (str_equals(scheme, NETLOC_SCHEMES[index])) {
            return 1;
        }
    }
    return 0;
}

/* Reassemble the components the way urllib.parse.urlunsplit does: an authority (or, for a netloc scheme, an empty
   one before an absolute path) is introduced by "//"; an empty query or fragment is dropped with its delimiter. The
   split never leaves a relative path behind an authority, so the path needs no leading slash added. */
static PyObject *unsplit(PyObject *scheme, PyObject *netloc, PyObject *path, PyObject *query, PyObject *fragment) {
    int has_scheme = PyUnicode_GET_LENGTH(scheme) > 0;
    int has_netloc = PyUnicode_GET_LENGTH(netloc) > 0;
    if (!has_netloc && has_scheme && scheme_uses_netloc(scheme) &&
        (PyUnicode_GET_LENGTH(path) == 0 || str_has_prefix(path, "/"))) {
        has_netloc = 1;
    }
    const char *marker = has_netloc || str_has_prefix(path, "//") ? "//" : "";
    return th_str_format("%U%s%s%U%U%s%U%s%U", scheme, has_scheme ? ":" : "", marker, netloc, path,
                         PyUnicode_GET_LENGTH(query) > 0 ? "?" : "", query,
                         PyUnicode_GET_LENGTH(fragment) > 0 ? "#" : "", fragment);
}

/* The path with a trailing slash run trimmed (any path but the root, and only without a query), the fold of
   "/dir/" and "/dir" into one form that trailing_slash=False asks for. */
static PyObject *trim_trailing_slash(PyObject *path) {
    Py_ssize_t end = PyUnicode_GET_LENGTH(path);
    while (end > 0 && PyUnicode_READ_CHAR(path, end - 1) == '/') {
        end--;
    }
    return PyUnicode_Substring(path, 0, end);
}

/* Rebuild the URL from spec-normalized components plus the beyond-spec query/fragment canonicalization. Returns NULL
   with a ValueError when a component cannot be percent-encoded (a lone surrogate). */
static PyObject *normalize_parts(const th_url_parts *parts, const clean_options *options) {
    PyObject *scheme = parts->part[TH_URL_SCHEME];
    int has_netloc = PyUnicode_GET_LENGTH(parts->part[TH_URL_NETLOC]) > 0;
    PyObject *netloc = has_netloc ? normalize_netloc(parts) : PyUnicode_FromString("");
    PyObject *path = NULL;
    PyObject *query = NULL;
    PyObject *fragment = NULL;
    PyObject *result = NULL;
    if (netloc == NULL) { /* GCOVR_EXCL_BR_LINE: the authority rebuild only fails on allocation failure */
        goto done;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    path = th_url_encode_component(parts->part[TH_URL_PATH], TH_URL_SET_PATH);
    if (path == NULL) {
        goto done;
    }
    if (has_netloc) {
        Py_SETREF(path, turbohtml_url_remove_dot_segments(NULL, path));
        if (path == NULL) { /* GCOVR_EXCL_BR_LINE: dot-segment removal only fails on allocation failure */
            goto done;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    query = normalize_query(parts->part[TH_URL_QUERY], options);
    if (query == NULL) {
        goto done;
    }
    int web = str_equals(scheme, "http") || str_equals(scheme, "https");
    if (has_netloc && web && PyUnicode_GET_LENGTH(path) == 0) {
        Py_SETREF(path, PyUnicode_FromString("/")); /* a special URL with a host never serializes an empty path */
    }
    if (!options->trailing_slash && PyUnicode_GET_LENGTH(query) == 0 && PyUnicode_GET_LENGTH(path) > 1 &&
        PyUnicode_READ_CHAR(path, PyUnicode_GET_LENGTH(path) - 1) == '/') {
        Py_SETREF(path, trim_trailing_slash(path));
    }
    if (path == NULL) { /* GCOVR_EXCL_BR_LINE: the path rewrites only fail on allocation failure */
        goto done;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    fragment = options->strict || options->strip_fragment ? PyUnicode_FromString("")
                                                          : normalize_fragment(parts->part[TH_URL_FRAGMENT], options);
    if (fragment == NULL) {
        goto done;
    }
    result = unsplit(scheme, netloc, path, query, fragment);
done:
    Py_XDECREF(netloc);
    Py_XDECREF(path);
    Py_XDECREF(query);
    Py_XDECREF(fragment);
    return result;
}

/* Reject anything but a str with the message the shim raised. */
static int require_str(PyObject *url) {
    if (PyUnicode_Check(url)) {
        return 0;
    }
    PyErr_Format(PyExc_TypeError, "url must be a str, got %s", Py_TYPE(url)->tp_name);
    return -1;
}

/* _url_normalize(url, strict, trailing_slash, strip_fragment, allow, deny, content, language_params) -> str: the
   canonical form of a URL, so that two spellings of the same resource compare equal. Raises TypeError for a non-str
   and ValueError when the URL cannot be split or a component cannot be percent-encoded. */
PyObject *turbohtml_url_normalize(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *url, *allow, *deny;
    clean_options options = {0};
    if (!PyArg_ParseTuple(args, "OpppOOOO:_url_normalize", &url, &options.strict, &options.trailing_slash,
                          &options.strip_fragment, &allow, &deny, &options.content, &options.language_params)) {
        return NULL;
    }
    th_url_parts parts;
    if (require_str(url) < 0 || th_url_split(url, &parts) < 0) {
        return NULL;
    }
    PyObject *result = NULL;
    if (clean_options_take(&options, allow, deny) == 0) {
        result = normalize_parts(&parts, &options);
    }
    clean_options_clear(&options);
    th_url_parts_clear(&parts);
    return result;
}

/* Whether the scrubbed, split URL is a fetchable web address: an http/https scheme and a host that is either dotted
   or carries a port, the shape gate clean_url applies before the language filter and normalization. */
static int is_web_url(const th_url_parts *parts, PyObject *host) {
    PyObject *scheme = parts->part[TH_URL_SCHEME];
    if (!str_equals(scheme, "http") && !str_equals(scheme, "https")) {
        return 0;
    }
    if (PyUnicode_GET_LENGTH(host) == 0) {
        return 0;
    }
    return str_holds(host, '.') || str_holds(parts->part[TH_URL_NETLOC], ':');
}

/* clean_url's pipeline over one str: scrub, split, the web gate, the language gate, then normalization. Returns the
   cleaned URL, None for anything that is not a fetchable web URL, or NULL on an error that is not a rejection. */
static PyObject *clean(PyObject *url, const clean_options *options) {
    PyObject *scrubbed = turbohtml_url_scrub(NULL, url);
    if (scrubbed == NULL) { /* GCOVR_EXCL_BR_LINE: the scrub of a str only fails on allocation failure */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_url_parts parts;
    int split = th_url_split(scrubbed, &parts);
    Py_DECREF(scrubbed);
    if (split < 0) {
        if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: the split raises nothing else */
            return NULL;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyErr_Clear();
        Py_RETURN_NONE;
    }
    PyObject *host = PyObject_CallMethod(parts.part[TH_URL_HOST], "lower", NULL);
    PyObject *result = NULL;
    if (host == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower cannot fail on a host */
        goto done;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!is_web_url(&parts, host)) {
        result = Py_NewRef(Py_None);
        goto done;
    }
    if (options->language != Py_None) {
        int matches =
            th_url_language_matches(parts.part[TH_URL_QUERY], parts.part[TH_URL_PATH], host, options->language,
                                    options->strict, options->language_params, options->iso_639_1);
        if (matches < 0) { /* GCOVR_EXCL_BR_LINE: the language filter only fails on allocation failure */
            goto done;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (!matches) {
            result = Py_NewRef(Py_None);
            goto done;
        }
    }
    result = normalize_parts(&parts, options);
    if (result == NULL) {
        if (!PyErr_ExceptionMatches(PyExc_ValueError)) { /* GCOVR_EXCL_BR_LINE: normalization raises nothing else */
            goto done;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* an unencodable component (a lone surrogate) is not a fetchable URL, as courlan also decides */
        PyErr_Clear();
        result = Py_NewRef(Py_None);
    }
done:
    Py_XDECREF(host);
    th_url_parts_clear(&parts);
    return result;
}

/* _url_clean(url, strict, trailing_slash, strip_fragment, allow, deny, content, language_params, language, iso_639_1)
   -> str | None: scrub a URL scraped from markup and normalize it, or None when nothing usable remains. */
PyObject *turbohtml_url_clean(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *url, *allow, *deny;
    clean_options options = {0};
    if (!PyArg_ParseTuple(args, "OpppOOOOOO:_url_clean", &url, &options.strict, &options.trailing_slash,
                          &options.strip_fragment, &allow, &deny, &options.content, &options.language_params,
                          &options.language, &options.iso_639_1)) {
        return NULL;
    }
    if (require_str(url) < 0) {
        return NULL;
    }
    PyObject *result = NULL;
    if (clean_options_take(&options, allow, deny) == 0) {
        result = clean(url, &options);
    }
    clean_options_clear(&options);
    return result;
}

/* The registrable domain (eTLD+1) that defines a URL's external_only site, or "" for no host. The host is punycoded
   first so a Unicode base compares against the ASCII hosts clean emits. Raises ValueError when the URL cannot be
   split. */
static PyObject *site_of(PyObject *url) {
    th_url_parts parts;
    if (th_url_split(url, &parts) < 0) {
        return NULL;
    }
    PyObject *host = ascii_host(parts.part[TH_URL_HOST]);
    th_url_parts_clear(&parts);
    if (host == NULL) { /* GCOVR_EXCL_BR_LINE: the host fold only fails on allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *site = turbohtml_registrable_domain(NULL, host);
    Py_DECREF(host);
    return site;
}

/* The whitespace str.split() splits on: Unicode White_Space, not only the ASCII set. Kept out of line so the
   macro's ASCII fast path is one branch site rather than one per scanning loop. */
static int rel_space(Py_UCS4 ch) {
    return Py_UNICODE_ISSPACE(ch) != 0;
}

/* Whether a rel attribute carries the nofollow token: the value split on whitespace the way str.split() does, each
   token compared to "nofollow" without regard to ASCII case. */
static int rel_has_nofollow(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && rel_space(value[pos])) {
            pos++;
        }
        Py_ssize_t start = pos;
        while (pos < len && !rel_space(value[pos])) {
            pos++;
        }
        if (pos - start != 8) {
            continue;
        }
        int matched = 1;
        for (Py_ssize_t offset = 0; offset < 8; offset++) {
            if (lower_ascii(value[start + offset]) != (Py_UCS4)(unsigned char)"nofollow"[offset]) {
                matched = 0;
            }
        }
        if (matched) {
            return 1;
        }
    }
    return 0;
}

/* Whether an anchor's hreflang names the target language: a missing or empty value always does, so does x-default,
   otherwise the primary subtag (before the first '-') must equal the code. Returns -1 on allocation failure. */
static int hreflang_matches(const Py_UCS4 *value, Py_ssize_t len, PyObject *language) {
    if (value == NULL) {
        return 1; /* absent, or empty: the parser stores an empty value as no value */
    }
    PyObject *raw = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value, len);
    if (raw == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *code = PyObject_CallMethod(raw, "lower", NULL);
    Py_DECREF(raw);
    if (code == NULL) { /* GCOVR_EXCL_BR_LINE: str.lower only fails on allocation failure */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int matches = str_equals(code, "x-default");
    if (!matches) {
        Py_ssize_t dash = PyUnicode_FindChar(code, '-', 0, PyUnicode_GET_LENGTH(code), 1);
        Py_ssize_t primary_len = dash < 0 ? PyUnicode_GET_LENGTH(code) : dash;
        matches = primary_len == PyUnicode_GET_LENGTH(language) &&
                  PyUnicode_Tailmatch(code, language, 0, primary_len, -1) == 1;
    }
    Py_DECREF(code);
    return matches;
}

/* The value of a by-name attribute, or NULL when the element has none (or it is valueless). */
static const Py_UCS4 *anchor_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t *out_len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, (Py_ssize_t)strlen(name));
    if (index < 0 || node->attrs[index].value == NULL) {
        *out_len = 0;
        return NULL;
    }
    *out_len = node->attrs[index].value_len;
    return node->attrs[index].value;
}

/* Collect the href of every <a>/<area> that is neither rel=nofollow nor, under a language filter, hreflang'd to
   another language, in document order, trimmed the way Node.links() reports a URL attribute. Pure tree reads plus
   str allocation, so it runs under the document's critical section. Returns -1 on allocation failure. */
static int collect_hrefs(th_tree *tree, th_node *root, PyObject *language, PyObject *hrefs) {
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || (node->atom != TH_TAG_A && node->atom != TH_TAG_AREA)) {
            continue;
        }
        Py_ssize_t len = 0;
        const Py_UCS4 *href = anchor_attr(tree, node, "href", &len);
        Py_ssize_t start = 0;
        Py_ssize_t end = len;
        while (start < end && is_space(href[start])) {
            start++;
        }
        while (end > start && is_space(href[end - 1])) {
            end--;
        }
        if (end == start) {
            continue;
        }
        Py_ssize_t rel_len = 0;
        const Py_UCS4 *rel = anchor_attr(tree, node, "rel", &rel_len);
        if (rel != NULL && rel_has_nofollow(rel, rel_len)) {
            continue;
        }
        if (language != Py_None) {
            Py_ssize_t hreflang_len = 0;
            const Py_UCS4 *hreflang = anchor_attr(tree, node, "hreflang", &hreflang_len);
            int matches = hreflang_matches(hreflang, hreflang_len, language);
            if (matches < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (!matches) {
                continue;
            }
        }
        PyObject *url = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, href + start, end - start);
        if (url == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int appended = PyList_Append(hrefs, url);
        Py_DECREF(url);
        if (appended < 0) { /* GCOVR_EXCL_BR_LINE: a list append only fails on allocation failure */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* The cleaned form of one href, resolved against `base` when it is relative, memoized in `cleaned_of` because pages
   repeat hrefs (navigation, pagination). A borrowed reference into the memo, or NULL on error. */
static PyObject *cleaned_href(PyObject *href, PyObject *base, const clean_options *options, PyObject *cleaned_of) {
    PyObject *cached = PyDict_GetItemWithError(cleaned_of, href);
    if (cached != NULL || PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a str key lookup cannot raise */
        return cached;
    }
    PyObject *candidate;
    if (base == Py_None || str_has_prefix(href, "http://") || str_has_prefix(href, "https://")) {
        candidate = Py_NewRef(href);
    } else {
        candidate = th_url_join(base, href);
        if (candidate == NULL) {
            return NULL; /* the document base cannot be split (an unbalanced IPv6 bracket) */
        }
    }
    PyObject *cleaned = clean(candidate, options);
    Py_DECREF(candidate);
    if (cleaned == NULL) { /* GCOVR_EXCL_BR_LINE: cleaning a joined URL only fails on allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int stored = PyDict_SetItem(cleaned_of, href, cleaned);
    Py_DECREF(cleaned);
    return stored < 0 ? NULL : cleaned; /* GCOVR_EXCL_BR_LINE: a dict insert only fails on allocation failure */
}

/* Document._extract_links(base_url, external_only, strict, trailing_slash, strip_fragment, allow, deny, content,
   language_params, language, iso_639_1) -> set[str]: the cleaned page links, the courlan.extract_links counterpart.
   Each anchor href resolves against the document base (a <base href> wins over base_url, HTML spec 4.2.3), is cleaned,
   and is deduplicated across the http/https and trailing-slash twins, the first in document order winning. With
   external_only only links leaving base_url's registrable domain survive. */
PyObject *turbohtml_document_extract_links(PyObject *self, PyObject *args) {
    PyObject *base_url, *allow, *deny;
    int external_only;
    clean_options options = {0};
    if (!PyArg_ParseTuple(args, "OppppOOOOOO:_extract_links", &base_url, &external_only, &options.strict,
                          &options.trailing_slash, &options.strip_fragment, &allow, &deny, &options.content,
                          &options.language_params, &options.language, &options.iso_639_1)) {
        return NULL;
    }
    if (external_only && base_url == Py_None) {
        PyErr_SetString(PyExc_ValueError, "external_only requires a base_url to compare hosts against");
        return NULL;
    }
    PyObject *site = base_url == Py_None ? PyUnicode_FromString("") : site_of(base_url);
    if (site == NULL) {
        return NULL;
    }
    PyObject *fallback = base_url == Py_None ? PyUnicode_FromString("") : Py_NewRef(base_url);
    PyObject *base = th_document_base_url(self, fallback);
    Py_DECREF(fallback);
    PyObject *hrefs = NULL;
    PyObject *found = NULL;
    PyObject *seen = NULL;
    PyObject *cleaned_of = NULL;
    if (base == NULL) {
        goto done; /* the <base href> cannot be resolved against base_url */
    }
    hrefs = PyList_New(0);
    if (hrefs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        goto done;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (PyUnicode_GET_LENGTH(base) == 0) {
        Py_SETREF(base, Py_NewRef(Py_None)); /* no fetch URL and no <base href>: relative links cannot resolve */
    }
    int collected;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    collected = collect_hrefs(tree_of(self), ((NodeObject *)self)->node, options.language, hrefs);
    Py_END_CRITICAL_SECTION();
    if (collected < 0) { /* GCOVR_EXCL_BR_LINE: the walk only fails on allocation failure */
        goto done;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (clean_options_take(&options, allow, deny) < 0) {
        goto done;
    }
    PyObject *links = PySet_New(NULL);
    seen = PySet_New(NULL);
    cleaned_of = PyDict_New();
    if (links == NULL || seen == NULL || cleaned_of == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        Py_XDECREF(links);                                     /* GCOVR_EXCL_LINE: allocation-failure path */
        goto done;                                             /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(hrefs); index++) {
        PyObject *cleaned = cleaned_href(PyList_GET_ITEM(hrefs, index), base, &options, cleaned_of);
        if (cleaned == NULL) { /* the base cannot be split, or a parameter name is not a str */
            Py_DECREF(links);
            goto done;
        }
        if (cleaned == Py_None) {
            continue;
        }
        if (external_only) {
            PyObject *link_site = site_of(cleaned);
            if (link_site == NULL) { /* GCOVR_EXCL_BR_LINE: a cleaned URL always splits */
                Py_DECREF(links);    /* GCOVR_EXCL_LINE: allocation-failure path */
                goto done;           /* GCOVR_EXCL_LINE */
            }
            int internal = PyUnicode_Compare(link_site, site) == 0;
            Py_DECREF(link_site);
            if (internal) {
                continue;
            }
        }
        PyObject *key = turbohtml_url_variant_key(NULL, cleaned);
        if (key == NULL) {    /* GCOVR_EXCL_BR_LINE: the key of a str only fails on allocation failure */
            Py_DECREF(links); /* GCOVR_EXCL_LINE: allocation-failure path */
            goto done;        /* GCOVR_EXCL_LINE */
        }
        Py_ssize_t before = PySet_GET_SIZE(seen);
        int added = PySet_Add(seen, key);
        Py_DECREF(key);
        if (added < 0) {      /* GCOVR_EXCL_BR_LINE: a str insert only fails on allocation failure */
            Py_DECREF(links); /* GCOVR_EXCL_LINE: allocation-failure path */
            goto done;        /* GCOVR_EXCL_LINE */
        }
        if (PySet_GET_SIZE(seen) == before) {
            continue; /* a scheme or trailing-slash twin of a link already kept */
        }
        if (PySet_Add(links, cleaned) < 0) { /* GCOVR_EXCL_BR_LINE: a str insert only fails on allocation failure */
            Py_DECREF(links);                /* GCOVR_EXCL_LINE: allocation-failure path */
            goto done;                       /* GCOVR_EXCL_LINE */
        }
    }
    found = links;
done:
    clean_options_clear(&options);
    Py_XDECREF(cleaned_of);
    Py_XDECREF(seen);
    Py_XDECREF(hrefs);
    Py_XDECREF(base);
    Py_DECREF(site);
    return found;
}
