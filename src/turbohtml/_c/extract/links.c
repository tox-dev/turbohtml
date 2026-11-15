/* Link enumeration and rewriting over a parsed tree, the C engine behind the Node.links()/rewrite_links()/
   resolve_links() methods (wired in dom/node.c).

   This file is the walk that locates every link-bearing location and, for the rewrite path, splices a replacement back
   in place. The genuinely new capability over iterating <a href> by hand is the URLs embedded in CSS url()/@import (in
   a style attribute and in <style> text), in a <meta http-equiv=refresh> content value, and in the srcset/ping/archive
   list attributes. URL resolution itself (resolve_links) is the C _url_join_link, bound through functools.partial here,
   so RFC 3986 resolution is not reinvented and a reference's host matches the one a browser resolves. */

#include "core/ascii.h"
#include "core/common.h"

#include "core/refresh.h"
#include "core/srcset.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "dom/nodes.h"
#include "dom/tree.h"

#include <string.h>

/* A scanner reports each URL span [start, end) inside a value buffer through this callback; returning -1 aborts the
   walk with a Python error already set. Keeping the scanners callback-driven means none of them allocates. `css` says
   how the span's text spells its URL: 0 when the text is the URL (an attribute value, which the HTML parser already
   decoded), and for CSS source the quote of the string that holds it or CSS_BARE_URL for an unquoted url() token. A CSS
   span is the URL only once its escapes are decoded (span_url), and takes a replacement only with the code points its
   syntax reserves escaped again (spell_url). */
typedef int (*link_emit)(void *ctx, Py_ssize_t start, Py_ssize_t end, Py_UCS4 css);
typedef int (*scanner_fn)(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx);

/* An unquoted url() token goes by the parenthesis that opens it, which like the quote of a string is a code point the
   token cannot hold unescaped. */
#define CSS_BARE_URL ((Py_UCS4)'(')
#define CSS_NO_CODE_POINT ((Py_UCS4) - 1) /* what css_escape_end decodes a backslash that escapes nothing to */

/* A CSS/identifier byte, used to reject url(/@import matches that are only the tail of a longer identifier (e.g.
   "blur(" must not match "url("). */
static int is_ident(Py_UCS4 c) {
    return is_ascii_alpha(c) || is_ascii_digit(c) || c == '_' || c == '-' || c >= 0x80;
}

/* Case-insensitive (ASCII) match of the literal `lit` at offset `pos`; letters compare case-folded, every other byte
   exactly, so "(", "=", and "@" match literally. */
static int imatch(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t pos, const char *lit, Py_ssize_t lit_len) {
    if (pos + lit_len > len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < lit_len; index++) {
        Py_UCS4 c = value[pos + index];
        Py_UCS4 folded = lower_ascii(c);
        if (folded != (Py_UCS4)(unsigned char)lit[index]) {
            return 0;
        }
    }
    return 1;
}

/* A whole single-URL attribute value (href, src, ...), with surrounding whitespace trimmed off the reported span. */
static int scan_single(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(value[start])) {
        start++;
    }
    while (end > start && is_space(value[end - 1])) {
        end--;
    }
    if (end > start) {
        return emit(ctx, start, end, 0);
    }
    return 0;
}

/* A whitespace-separated URL list (a/area ping, object/applet archive): each non-empty token is a URL. */
static int scan_space_list(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && is_space(value[pos])) {
            pos++;
        }
        Py_ssize_t start = pos;
        while (pos < len && !is_space(value[pos])) {
            pos++;
        }
        if (pos > start) {
            if (emit(ctx, start, pos, 0) < 0) { /* GCOVR_EXCL_BR_LINE: emit fails only on collect_span's realloc */
                return -1;                      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    return 0;
}

/* A comma inside a srcset URL belongs to the URL; only trailing URL commas and descriptor commas separate candidates.
   A candidate whose descriptors are invalid is not one the browser keeps, so it is not reported (core/srcset.h). */
static int scan_srcset(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && (value[pos] == ',' || is_space(value[pos]))) {
            pos++;
        }
        if (pos == len) {
            break;
        }
        Py_ssize_t start = pos;
        while (pos < len && !is_space(value[pos])) {
            pos++;
        }
        Py_ssize_t end = pos;
        while (value[end - 1] == ',') {
            end--;
        }
        int valid = end < pos || srcset_descriptors_valid(value, len, &pos);
        if (valid && emit(ctx, start, end, 0) < 0) { /* GCOVR_EXCL_BR_LINE: emit fails only on collect_span's realloc */
            return -1;                               /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* The end of the CSS escape whose backslash sits at `pos`, and in *decoded the code point it stands for (CSS Syntax 3
   section 4.3.7): one to six hexadecimal digits and one whitespace after them, a CRLF counting as one, or the code
   point after the backslash. A backslash before a newline, which continues a string onto the next line, and one that
   ends the input stand for nothing: *decoded is CSS_NO_CODE_POINT. */
static Py_ssize_t css_escape_end(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t pos, Py_UCS4 *decoded) {
    Py_ssize_t index = pos + 1;
    *decoded = CSS_NO_CODE_POINT;
    if (index == len) {
        return index;
    }
    if (!is_ascii_hexdigit(value[index])) {
        if (value[index] == '\r' && index + 1 < len && value[index + 1] == '\n') {
            return index + 2;
        }
        if (value[index] != '\n' && value[index] != '\r' && value[index] != '\f') {
            *decoded = value[index];
        }
        return index + 1;
    }
    Py_UCS4 point = 0;
    for (int digits = 0; digits < 6 && index < len && is_ascii_hexdigit(value[index]); digits++) {
        Py_UCS4 digit = value[index++] | 0x20;
        point = (point << 4) + (digit <= '9' ? digit - '0' : digit - 'a' + 10);
    }
    if (index < len && is_space(value[index])) {
        index += value[index] == '\r' && index + 1 < len && value[index + 1] == '\n' ? 2 : 1;
    }
    *decoded = point == 0 || point > 0x10FFFF || (point >= 0xD800 && point <= 0xDFFF) ? 0xFFFD : point;
    return index;
}

static Py_ssize_t css_string_end(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t pos, Py_UCS4 quote) {
    while (pos < len && value[pos] != quote) {
        if (value[pos] == '\\') {
            Py_UCS4 decoded;
            pos = css_escape_end(value, len, pos, &decoded);
        } else if (value[pos] == '\n' || value[pos] == '\r' || value[pos] == '\f') {
            break;
        } else {
            pos++;
        }
    }
    return pos;
}

/* The URL inside a url(...) function: skip whitespace and an optional quote after the '(', then read to the matching
   quote, or as an unquoted url token (CSS Syntax 3 section 4.3.6). Emits the span between, or nothing for an empty
   url() and for a bad url: whitespace before anything but the ')', a quote, a '(' or a non-printable code point makes
   the token one CSS discards up to the next ')', and text rewritten inside it would move where it ends. */
static int css_url_span(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t open, link_emit emit, void *ctx,
                        Py_ssize_t *resume) {
    Py_ssize_t pos = open;
    while (pos < len && is_space(value[pos])) {
        pos++;
    }
    Py_UCS4 quote = 0;
    if (pos < len && (value[pos] == '"' || value[pos] == '\'')) {
        quote = value[pos++];
    }
    Py_ssize_t start = pos;
    if (quote) {
        pos = css_string_end(value, len, pos, quote);
        if (pos < len && value[pos] != quote) {
            *resume = pos;
            return 0;
        }
        *resume = pos < len ? pos + 1 : pos;
    } else {
        Py_ssize_t end = pos;
        int bad = 0;
        while (pos < len && value[pos] != ')') {
            Py_UCS4 current = value[pos];
            if (is_space(current)) {
                while (pos < len && is_space(value[pos])) {
                    pos++;
                }
                bad = pos < len && value[pos] != ')';
                break;
            }
            Py_UCS4 decoded = 0;
            Py_ssize_t after = current == '\\' ? css_escape_end(value, len, pos, &decoded) : pos + 1;
            /* a url token has no line continuation, so a backslash that escapes nothing is not an escape, except the
               one ending the input, which CSS Syntax 3 decodes to U+FFFD */
            if (current == '\\' && pos + 1 == len) {
                decoded = 0xFFFD;
            }
            if (current == '"' || current == '\'' || current == '(' || current < 0x20 || current == 0x7F ||
                decoded == CSS_NO_CODE_POINT) {
                bad = 1;
                break;
            }
            pos = end = after;
        }
        if (bad) {
            while (pos < len && value[pos] != ')') {
                Py_UCS4 decoded;
                pos = value[pos] == '\\' ? css_escape_end(value, len, pos, &decoded) : pos + 1;
            }
            *resume = pos < len ? pos + 1 : pos;
            return 0;
        }
        *resume = pos;
        pos = end;
    }
    if (pos > start) {
        return emit(ctx, start, pos, quote ? quote : CSS_BARE_URL);
    }
    return 0;
}

/* The URL inside an unguarded @import "..."/@import '...' string (the @import url(...) form is caught by the url()
   scan). Emits the quoted string's contents. */
static int css_import_span(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t after, link_emit emit, void *ctx,
                           Py_ssize_t *resume) {
    Py_ssize_t pos = after;
    while (pos < len && is_space(value[pos])) {
        pos++;
    }
    if (pos < len && (value[pos] == '"' || value[pos] == '\'')) {
        Py_UCS4 quote = value[pos++];
        Py_ssize_t start = pos;
        pos = css_string_end(value, len, pos, quote);
        *resume = pos < len && value[pos] == quote ? pos + 1 : pos;
        if (pos < len && value[pos] != quote) {
            return 0;
        }
        if (pos > start) {
            return emit(ctx, start, pos, quote);
        }
    }
    return 0;
}

/* Whether the first `count` code points of an identifier, lowercased, spell the ASCII `word`. */
static int decodes_to(const Py_UCS4 *spelled, Py_ssize_t count, const char *word) {
    if (count != (Py_ssize_t)strlen(word)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        if (spelled[index] != (Py_UCS4)word[index]) {
            return 0;
        }
    }
    return 1;
}

/* CSS text (a style attribute or <style> content): every url(...) target and every @import string. */
static int scan_css(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        Py_UCS4 current = value[pos];
        switch (current) {
        case '/':
            if (pos + 1 < len && value[pos + 1] == '*') {
                pos += 2;
                while (pos + 1 < len && (value[pos] != '*' || value[pos + 1] != '/')) {
                    pos++;
                }
                pos = pos + 1 < len ? pos + 2 : len;
                continue;
            }
            break;
        case '"':
        case '\'':
            pos = css_string_end(value, len, pos + 1, current);
            if (pos < len && value[pos] == current) {
                pos++;
            }
            continue;
        case '\\': {
            /* An escape is part of an identifier, which may have started before it. Spelled with escapes the name is
               still the url function when it decodes to "url", and the at-keyword when an at-sign precedes it and it
               decodes to "import"; any other name that ends in url( is a function of its own, whose argument is not a
               URL token. */
            Py_ssize_t name = pos;
            while (name > 0 && is_ident(value[name - 1])) {
                name--;
            }
            Py_UCS4 spelled[6] = {0, 0, 0, 0, 0, 0};
            Py_ssize_t count = 0;
            Py_ssize_t cursor = name;
            while (cursor < len) {
                Py_UCS4 decoded = value[cursor];
                Py_ssize_t after = cursor + 1;
                if (decoded == '\\') {
                    after = css_escape_end(value, len, cursor, &decoded);
                    if (decoded == CSS_NO_CODE_POINT) {
                        break;
                    }
                } else if (!is_ident(decoded)) {
                    break;
                }
                if (count < 6) {
                    spelled[count] = lower_ascii(decoded);
                }
                count++;
                cursor = after;
            }
            if (cursor <= pos) { /* a backslash before a newline or at the end of the input escapes nothing */
                Py_UCS4 decoded;
                pos = css_escape_end(value, len, pos, &decoded);
                continue;
            }
            pos = cursor;
            if (name > 0 && value[name - 1] == '@' && decodes_to(spelled, count, "import")) {
                Py_ssize_t resume = pos;
                if (css_import_span(value, len, pos, emit, ctx, &resume) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                                                  /* GCOVR_EXCL_LINE: alloc-failure */
                }
                pos = resume;
                continue;
            }
            int function = name == 0 || (value[name - 1] != '#' && value[name - 1] != '@');
            if (function && decodes_to(spelled, count, "url") && pos < len && value[pos] == '(') {
                Py_ssize_t resume = pos + 1;
                if (css_url_span(value, len, pos + 1, emit, ctx, &resume) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                                                   /* GCOVR_EXCL_LINE: alloc-failure */
                }
                pos = resume;
            }
            continue;
        }
        case 'u':
        case 'U':
            /* "#url(" is a hash token and "@url(" an at-keyword, each followed by a plain parenthesis block */
            if ((pos == 0 || (!is_ident(value[pos - 1]) && value[pos - 1] != '#' && value[pos - 1] != '@')) &&
                imatch(value, len, pos, "url(", 4)) {
                Py_ssize_t resume = pos + 4;
                if (css_url_span(value, len, pos + 4, emit, ctx, &resume) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                                                   /* GCOVR_EXCL_LINE: alloc-failure */
                }
                pos = resume;
                continue;
            }
            break;
        case '@':
            if ((pos == 0 || !is_ident(value[pos - 1])) && imatch(value, len, pos, "@import", 7)) {
                Py_ssize_t resume = pos + 7;
                if (css_import_span(value, len, pos + 7, emit, ctx, &resume) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    return -1;                                                      /* GCOVR_EXCL_LINE: alloc-failure */
                }
                pos = resume;
                continue;
            }
            break;
        default:
            break;
        }
        pos++;
    }
    return 0;
}

/* A <meta http-equiv=refresh> target, read by the shared refresh steps that Document.meta_refresh also uses. */
static int scan_meta_refresh(const Py_UCS4 *value, Py_ssize_t len, link_emit emit, void *ctx) {
    refresh_span span;
    if (!refresh_read(value, len, &span) || span.target_end == span.target_start) {
        return 0;
    }
    return emit(ctx, span.target_start, span.target_end, 0);
}

typedef struct {
    Py_ssize_t start;
    Py_ssize_t end;
    Py_UCS4 css; /* how the text spells the URL, as link_emit describes */
} link_span;

/* What the walk made of one attribute value, for the other elements that hold the same buffer. The tree builder
   reopens an unclosed formatting element in every block that follows it, and each of those clones borrows the value
   buffers of the one start tag (node_clone). A str per link per clone, or a rewritten copy of the value per clone, then
   costs value length x clones for an input of value length + clones, so the clones share what the first of them got
   as they share the source. A value is never written in place and the arena keeps it until the tree is freed, so its
   address identifies its content. */
typedef struct {
    const Py_UCS4 *from; /* the value, by its address in the tree's arena; NULL marks a free slot */
    PyObject *urls;      /* links(): a tuple of the str built for each link */
    const Py_UCS4 *to;   /* a rewrite: the value the attribute held afterwards, `from` itself when no link changed */
    Py_ssize_t to_len;
} shared_value;

/* The walk's shared state. Exactly one of `result` (enumerate) and `replace` (rewrite) is set. `spans` is reused across
   values to avoid a per-value allocation. */
typedef struct {
    th_tree *tree;
    module_state *state; /* the module's per-interpreter state, for the Link record type */
    PyObject *handle;    /* the per-tree handle, owned: it is the walk's lock and keeps the snapshot's tree alive */
    PyObject *owner;     /* the element/document the public call was made on, for wrapping enumerated nodes */
    PyObject *result;    /* list[Link] for enumerate, else NULL */
    PyObject *replace;   /* callable (str) -> str | None for rewrite, else NULL */
    /* resolve_links only: the base URL's lowercased scheme, or NULL for the enumerate/rewrite paths. When set, a link
       that urljoin(base, link) would return byte-for-byte (an absolute URL) is left untouched without the call. */
    const char *base_scheme;
    Py_ssize_t base_scheme_len;
    int base_netloc_scheme; /* the base scheme reserializes an absolute same-scheme URL unchanged (http/https) */
    shared_value *shared;   /* open addressing at no more than half load, keyed by `from` */
    size_t shared_cap;
    size_t shared_count;
    link_span *spans;
    Py_ssize_t span_count;
    Py_ssize_t span_cap;
    th_node **nodes;       /* a pure-C snapshot of the elements to process, so no structural pointer */
    Py_ssize_t node_count; /* is dereferenced across a Python call that could suspend the critical section */
    Py_ssize_t node_cap;
} link_walk;

/* Append a node to a growable pointer array (the element snapshot, or a <style>'s text children). */
static int collect_node(th_node ***array, Py_ssize_t *count, Py_ssize_t *cap, th_node *node) {
    if (*count == *cap) {
        size_t new_cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(*count + 1), (size_t)*cap, 16, sizeof(th_node *), &new_cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        th_node **grown = PyMem_Realloc(*array, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        *array = grown;
        *cap = (Py_ssize_t)new_cap;
    }
    (*array)[(*count)++] = node;
    return 0;
}

static int collect_span(void *ctx, Py_ssize_t start, Py_ssize_t end, Py_UCS4 css) {
    link_walk *walk = ctx;
    if (walk->span_count == walk->span_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)(walk->span_count + 1), (size_t)walk->span_cap, 8, sizeof(link_span), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        link_span *grown = PyMem_Realloc(walk->spans, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        walk->spans = grown;
        walk->span_cap = (Py_ssize_t)cap;
    }
    walk->spans[walk->span_count].start = start;
    walk->spans[walk->span_count].end = end;
    walk->spans[walk->span_count].css = css;
    walk->span_count++;
    return 0;
}

static PyObject *ucs4_slice(const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value + start, end - start);
}

/* Whether a CSS span spells its URL with an escape, so that its text is not the URL itself. */
static int span_is_escaped(const Py_UCS4 *value, const link_span *span) {
    for (Py_ssize_t index = span->start; span->css && index < span->end; index++) {
        if (value[index] == '\\') {
            return 1;
        }
    }
    return 0;
}

/* The URL a span holds, as a new str: its text, with the escapes of a CSS span decoded as a browser decodes them
   before it resolves the URL. Taking the source text for the URL names a host the browser does not contact
   ("good.example\40 evil.example" is good.example@evil.example), and resolving it lets a character the URL parser
   drops or rewrites take the escape's backslash apart from what it escaped. */
static PyObject *span_url(const Py_UCS4 *value, const link_span *span) {
    if (!span_is_escaped(value, span)) {
        return ucs4_slice(value, span->start, span->end);
    }
    /* an escape never decodes longer than it is written */
    Py_UCS4 *url = PyMem_Malloc((size_t)(span->end - span->start) * sizeof(Py_UCS4));
    if (url == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t url_len = 0;
    for (Py_ssize_t pos = span->start; pos < span->end;) {
        Py_UCS4 decoded = value[pos];
        pos = decoded == '\\' ? css_escape_end(value, span->end, pos, &decoded) : pos + 1;
        if (decoded == CSS_NO_CODE_POINT && span->css == CSS_BARE_URL) {
            decoded = 0xFFFD;
        }
        if (decoded != CSS_NO_CODE_POINT) {
            url[url_len++] = decoded;
        }
    }
    PyObject *result = ucs4_slice(url, 0, url_len);
    PyMem_Free(url);
    return result;
}

/* Write `url` at `out` as the text of a span of kind `css` and return the length written; a NULL `out` only measures.
   The inverse of span_url: in CSS source a backslash, the quote that ends the string and a control character (a
   newline ends a string early) are written as escapes, as are the quotes, parentheses and space that end or void an
   unquoted url() token. The solidus of "</" is escaped too, so that a URL cannot write the end tag of the <style> whose
   text it is spliced into. */
static Py_ssize_t spell_url(PyObject *url, Py_UCS4 css, Py_UCS4 *out) {
    static const char hex[] = "0123456789abcdef";
    Py_ssize_t len = PyUnicode_GET_LENGTH(url);
    if (css == 0) {
        for (Py_ssize_t at = 0; out != NULL && at < len; at++) {
            out[at] = PyUnicode_READ_CHAR(url, at);
        }
        return len;
    }
    int bare = css == CSS_BARE_URL;
    Py_ssize_t written = 0;
    Py_UCS4 previous = 0;
    for (Py_ssize_t at = 0; at < len; at++) {
        Py_UCS4 codepoint = PyUnicode_READ_CHAR(url, at);
        Py_UCS4 spelled[4];
        int count = 0;
        if (codepoint < 0x20 || codepoint == 0x7F || (bare && codepoint == ' ')) {
            spelled[count++] = '\\';
            if (codepoint >= 0x10) {
                spelled[count++] = (Py_UCS4)hex[codepoint >> 4];
            }
            spelled[count++] = (Py_UCS4)hex[codepoint & 0xF];
            spelled[count++] = ' '; /* ends the escape, so a hexadecimal digit after it is not read into it */
        } else {
            int reserved = codepoint == '\\' || codepoint == css || (codepoint == '/' && previous == '<') ||
                           (bare && (codepoint == '"' || codepoint == '\'' || codepoint == ')'));
            if (reserved) {
                spelled[count++] = '\\';
            }
            spelled[count++] = codepoint;
        }
        for (int index = 0; out != NULL && index < count; index++) {
            out[written + index] = spelled[index];
        }
        written += count;
        previous = codepoint;
    }
    return written;
}

static shared_value *shared_slot(shared_value *entries, size_t cap, const Py_UCS4 *from) {
    size_t slot = (size_t)(((uint64_t)(uintptr_t)from * UINT64_C(0x9E3779B97F4A7C15)) >> 32) & (cap - 1);
    while (entries[slot].from != NULL && entries[slot].from != from) {
        slot = (slot + 1) & (cap - 1);
    }
    return &entries[slot];
}

/* What the walk recorded for `from`, or NULL. */
static const shared_value *shared_find(const link_walk *walk, const Py_UCS4 *from) {
    if (walk->shared_cap == 0) {
        return NULL;
    }
    const shared_value *found = shared_slot(walk->shared, walk->shared_cap, from);
    return found->from != NULL ? found : NULL;
}

/* Record what became of `from`, which shared_find does not know. The table takes over the `urls` reference unless it
   fails to grow. */
static int shared_add(link_walk *walk, const Py_UCS4 *from, PyObject *urls, const Py_UCS4 *to, Py_ssize_t to_len) {
    if (walk->shared_count >= walk->shared_cap / 2) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((walk->shared_count + 1) * 2, walk->shared_cap, 16, sizeof(shared_value), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        shared_value *grown = PyMem_Calloc(1, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        for (size_t slot = 0; slot < walk->shared_cap; slot++) {
            if (walk->shared[slot].from != NULL) {
                const shared_value *entry = &walk->shared[slot];
                *shared_slot(grown, cap, entry->from) = *entry;
            }
        }
        PyMem_Free(walk->shared);
        walk->shared = grown;
        walk->shared_cap = cap;
    }
    *shared_slot(walk->shared, walk->shared_cap, from) = (shared_value){from, urls, to, to_len};
    walk->shared_count++;
    return 0;
}

/* A tuple of the URL (span_url) of each span just scanned in `value`, for the elements that hold the value to share. */
static PyObject *span_urls(const link_walk *walk, const Py_UCS4 *value) {
    PyObject *urls = PyTuple_New(walk->span_count);
    if (urls == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        PyObject *url = span_url(value, &walk->spans[index]);
        if (url == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(urls); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE */
        }
        PyTuple_SET_ITEM(urls, index, url);
    }
    return urls;
}

/* Append a Link(element, attr, url) for each link to the result list: each str of `urls` when the value's links are
   shared (span_urls), else each span cut from `value`. The Element wrapper for `report_node` is minted on the first
   link and cached in *report_element, so an element with no link (the vast majority on a real page) is never wrapped.
   `report_attr` is the attribute name (or None for <style> text), borrowed for the call's duration. */
static int emit_enumerated(link_walk *walk, PyObject **report_element, th_node *report_node, PyObject *report_attr,
                           const Py_UCS4 *value, PyObject *urls) {
    Py_ssize_t count = urls != NULL ? PyTuple_GET_SIZE(urls) : walk->span_count;
    if (count == 0) {
        return 0;
    }
    if (*report_element == NULL) {
        *report_element = turbohtml_node_wrap_in(walk->owner, report_node);
        if (*report_element == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *url = urls != NULL ? Py_NewRef(PyTuple_GET_ITEM(urls, index)) : span_url(value, &walk->spans[index]);
        if (url == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyUnicode_GET_LENGTH(url) == 0) { /* escapes that decode to nothing: an empty url(), which holds no link */
            Py_DECREF(url);
            continue;
        }
        PyObject *record = PyObject_CallFunction(walk->state->link_type, "OOO", *report_element, report_attr, url);
        Py_DECREF(url);
        if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int failed = PyList_Append(walk->result, record);
        Py_DECREF(record);
        if (failed < 0) { /* GCOVR_EXCL_BR_LINE: list append fails only on allocation failure */
            return -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* Build the value with each recorded replacement spliced over its span and write it back, to the named attribute or
   (attr_name == NULL) to the target text node. Returns the tree edit's status: 0, or -1 on allocation failure. */
static int splice_value(link_walk *walk, th_node *target, const char *attr_name, Py_ssize_t attr_len,
                        const Py_UCS4 *value, Py_ssize_t len, PyObject **replacements, Py_ssize_t new_len) {
    Py_UCS4 *out = PyMem_Malloc((size_t)new_len * sizeof(Py_UCS4) + 1);
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t out_len = 0;
    Py_ssize_t cursor = 0;
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        for (; cursor < walk->spans[index].start; cursor++) {
            out[out_len++] = value[cursor];
        }
        if (replacements[index] != NULL) {
            out_len += spell_url(replacements[index], walk->spans[index].css, out + out_len);
        } else {
            for (; cursor < walk->spans[index].end; cursor++) {
                out[out_len++] = value[cursor];
            }
        }
        cursor = walk->spans[index].end;
    }
    for (; cursor < len; cursor++) {
        out[out_len++] = value[cursor];
    }
    TH_WORK_STEPS_ADD(out_len);
    int status;
    if (attr_name != NULL) {
        status = th_node_attr_set(walk->tree, target, attr_name, attr_len, out, out_len, 1);
    } else {
        status = th_node_set_data(walk->tree, target, out, out_len);
        /* the text node is a <style>'s, and computed_style() keeps that sheet compiled across edits */
        handle_clear_css_cache((HandleObject *)walk->handle);
    }
    PyMem_Free(out);
    return status;
}

/* True when resolve_links can prove urljoin(base, [start, end)) returns the URL byte-for-byte, so the urljoin call (the
   bulk of resolve's cost, stdlib Python) is skipped for an already-absolute link. Mirrors CPython urllib.parse.urljoin:
   a reference with a scheme differing from the base's (case-insensitively) is returned verbatim; a same-scheme absolute
   reference (scheme://netloc...) is reserialized, which reproduces the input only when the scheme is already lowercase,
   an authority is present, and no tab/CR/LF (which urlsplit strips) appears. The same-scheme case is enabled only for
   an http/https base, whose scheme reserializes through urlunsplit's netloc branch. */
static int url_resolves_to_itself(const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end, const char *base_scheme,
                                  Py_ssize_t base_scheme_len, int base_netloc_scheme) {
    if (!is_ascii_alpha(value[start])) { /* a scheme starts with a letter; a span is never empty (start < end) */
        return 0;
    }
    Py_ssize_t colon = start + 1;
    while (colon < end && value[colon] != ':') {
        Py_UCS4 c = value[colon];
        if (!is_ascii_alpha(c) && !is_ascii_digit(c) && c != '+' && c != '-' && c != '.') {
            return 0;
        }
        colon++;
    }
    if (colon >= end) {
        return 0;
    }
    Py_ssize_t scheme_len = colon - start;
    int same_scheme = scheme_len == base_scheme_len;
    for (Py_ssize_t index = 0; same_scheme && index < scheme_len; index++) {
        same_scheme = lower_ascii(value[start + index]) == (Py_UCS4)(unsigned char)base_scheme[index];
    }
    if (!same_scheme) {
        return 1;
    }
    if (!base_netloc_scheme) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < scheme_len; index++) {
        if (value[start + index] != lower_ascii(value[start + index])) {
            return 0;
        }
    }
    if (colon + 2 >= end || value[colon + 1] != '/' || value[colon + 2] != '/') {
        return 0;
    }
    Py_ssize_t netloc = colon + 3;
    /* '\\' too: the join skips it like a third '/' before it reads the host, so the link does not come back verbatim */
    if (netloc >= end || value[netloc] == '/' || value[netloc] == '\\' || value[netloc] == '?' ||
        value[netloc] == '#') {
        return 0;
    }
    for (Py_ssize_t index = start; index < end; index++) {
        if (value[index] == '\t' || value[index] == '\r' || value[index] == '\n') {
            return 0;
        }
    }
    return 1;
}

/* Call replace(url) for each span and build a rewritten value, or leave the value untouched when every replacement is
   None or unchanged. */
static int rewrite_value(link_walk *walk, th_node *target, const char *attr_name, Py_ssize_t attr_len,
                         const Py_UCS4 *value, Py_ssize_t len) {
    PyObject **replacements = PyMem_Calloc((size_t)walk->span_count, sizeof(PyObject *));
    if (replacements == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();       /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;              /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t new_len = len;
    int changed = 0;
    int failed = 0;
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        const link_span *span = &walk->spans[index];
        /* the text of an escaped CSS span is not the URL the check below reads, so that one always takes the call */
        if (walk->base_scheme != NULL && !span_is_escaped(value, span) &&
            url_resolves_to_itself(value, span->start, span->end, walk->base_scheme, walk->base_scheme_len,
                                   walk->base_netloc_scheme)) {
            continue; /* an absolute URL resolve_links would leave unchanged; skip the urljoin call */
        }
        PyObject *url = span_url(value, span);
        if (url == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
            break;         /* GCOVR_EXCL_LINE */
        }
        if (PyUnicode_GET_LENGTH(url) == 0) { /* an empty url(), as in emit_enumerated */
            Py_DECREF(url);
            continue;
        }
        PyObject *out = PyObject_CallFunctionObjArgs(walk->replace, url, NULL);
        if (out == NULL) {
            Py_DECREF(url);
            failed = 1;
            break;
        }
        if (out == Py_None || PyUnicode_Check(out)) {
            if (out != Py_None && PyUnicode_Compare(out, url) != 0) {
                replacements[index] = Py_NewRef(out);
                new_len += spell_url(out, span->css, NULL) - (span->end - span->start);
                changed = 1;
            }
        } else {
            PyErr_SetString(PyExc_TypeError, "a link rewrite must return str or None");
            failed = 1;
        }
        Py_DECREF(out);
        Py_DECREF(url);
        if (failed) {
            break;
        }
    }
    if (!failed && changed) {
        /* splice_value returns -1 only on an unforceable allocation failure; the branchless assignment keeps that
           dead path off the branch count without an excluded fall-through brace */
        failed = splice_value(walk, target, attr_name, attr_len, value, len, replacements, new_len) < 0;
    }
    for (Py_ssize_t index = 0; index < walk->span_count; index++) {
        Py_XDECREF(replacements[index]);
    }
    PyMem_Free(replacements);
    return failed ? -1 : 0;
}

/* Scan one value with `scan`, then either record the spans or rewrite them. `report_element` (the enumerate path's
   lazily-wrapped element slot), `report_node`, and `report_attr` describe the enumeration location;
   `target`/`attr_name`/ `attr_len` describe where a rewrite writes back. */
static int handle_value(link_walk *walk, PyObject **report_element, th_node *report_node, PyObject *report_attr,
                        th_node *target, const char *attr_name, Py_ssize_t attr_len, const Py_UCS4 *value,
                        Py_ssize_t len, scanner_fn scan) {
    walk->span_count = 0;
    if (scan(value, len, collect_span, walk) < 0) { /* GCOVR_EXCL_BR_LINE: scan alloc only (collect_span realloc) */
        return -1;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (walk->span_count == 0) {
        return 0;
    }
    if (walk->result != NULL) {
        return emit_enumerated(walk, report_element, report_node, report_attr, value, NULL);
    }
    return rewrite_value(walk, target, attr_name, attr_len, value, len);
}

/* The attribute value `name` on `element`, or NULL with *len=0 when absent or valueless. */
static const Py_UCS4 *attr_value(link_walk *walk, th_node *element, const char *name, Py_ssize_t name_len,
                                 Py_ssize_t *len) {
    Py_ssize_t index = th_node_attr_find(walk->tree, element, name, name_len);
    if (index < 0 || element->attrs[index].value == NULL) {
        *len = 0;
        return NULL;
    }
    *len = element->attrs[index].value_len;
    return element->attrs[index].value;
}

/* handle_value for an attribute of an element without a start tag of its own, which may hold `value` with others (see
   shared_value). The first such element does the work; the others adopt its result, so the callback runs once per
   link of each distinct value rather than once per clone. */
static int handle_shared_value(link_walk *walk, PyObject **report_element, th_node *element, PyObject *report_attr,
                               const char *name, Py_ssize_t name_len, const Py_UCS4 *value, Py_ssize_t len,
                               scanner_fn scan) {
    const shared_value *known = shared_find(walk, value);
    if (known != NULL && walk->result != NULL) {
        return emit_enumerated(walk, report_element, element, report_attr, value, known->urls);
    }
    if (known != NULL) {
        if (known->to == value) {
            return 0;
        }
        return th_node_attr_set_shared(walk->tree, element, name, name_len, known->to, known->to_len);
    }
    walk->span_count = 0;
    if (scan(value, len, collect_span, walk) < 0) { /* GCOVR_EXCL_BR_LINE: scan alloc only (collect_span realloc) */
        return -1;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (walk->result != NULL) {
        PyObject *urls = span_urls(walk, value);
        /* GCOVR_EXCL_BR_START: the tuple and its table entry fail only on allocation */
        if (urls == NULL || shared_add(walk, value, urls, NULL, 0) < 0) {
            Py_XDECREF(urls); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
        return emit_enumerated(walk, report_element, element, report_attr, value, urls);
    }
    int status = walk->span_count > 0 ? rewrite_value(walk, element, name, name_len, value, len) : 0;
    if (status == 0) {
        Py_ssize_t to_len = 0;
        const Py_UCS4 *to = attr_value(walk, element, name, name_len, &to_len);
        if (to != NULL) { /* a rewrite_links callback may have removed the attribute */
            status = shared_add(walk, value, NULL, to, to_len);
        }
    }
    return status;
}

static int value_imatch(const Py_UCS4 *value, Py_ssize_t len, const char *lit, Py_ssize_t lit_len) {
    return len == lit_len && imatch(value, len, 0, lit, lit_len);
}

/* Single-URL attribute names (ping/archive are whitespace lists and srcset a candidate list, handled separately). */
static int is_single_url_attr(const char *name, Py_ssize_t len) {
    switch (len) {
    case 3:
        return memcmp(name, "src", 3) == 0;
    case 4:
        return memcmp(name, "href", 4) == 0 || memcmp(name, "cite", 4) == 0 || memcmp(name, "data", 4) == 0;
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

static int is_srcset_attr(const char *name, Py_ssize_t len) {
    return (len == 6 && memcmp(name, "srcset", 6) == 0) || (len == 11 && memcmp(name, "imagesrcset", 11) == 0);
}

/* Pick the scanner for attribute `name` on an element with tag `atom`, or NULL if the attribute holds no link. */
static scanner_fn scanner_for_attr(uint16_t atom, const char *name, Py_ssize_t len) {
    if (is_srcset_attr(name, len)) {
        return scan_srcset;
    }
    if (len == 5 && memcmp(name, "style", 5) == 0) {
        return scan_css;
    }
    if (len == 4 && memcmp(name, "ping", 4) == 0 && (atom == TH_TAG_A || atom == TH_TAG_AREA)) {
        return scan_space_list;
    }
    if (len == 7 && memcmp(name, "archive", 7) == 0 && (atom == TH_TAG_OBJECT || atom == TH_TAG_APPLET)) {
        return scan_space_list;
    }
    if (is_single_url_attr(name, len)) {
        return scan_single;
    }
    return NULL;
}

/* Does <meta> declare http-equiv=refresh (the only meta whose content carries a URL)? */
static int is_meta_refresh(link_walk *walk, th_node *element) {
    Py_ssize_t len = 0;
    const Py_UCS4 *equiv = attr_value(walk, element, "http-equiv", 10, &len);
    return equiv != NULL && value_imatch(equiv, len, "refresh", 7);
}

static int process_element(link_walk *walk, th_node *element) {
    PyObject *report_element = NULL; /* the enumerate path wraps `element` lazily, on its first emitted link */
    int failed = 0;
    /* a <style>'s text children are a stylesheet, scanned for url()/@import like a style attribute. A parsed text node
       holds a zero-copy span, not a ready buffer, so its data is materialized (and freed) per child. The children are
       snapshotted in a pure-C pass first so a concurrent mutation cannot relink the child list while handle_value runs
       a Python callback (which can suspend the per-tree critical section) below. */
    if (element->atom == TH_TAG_STYLE) {
        th_node **children = NULL;
        Py_ssize_t child_count = 0, child_cap = 0;
        for (th_node *child = element->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_TEXT) {
                continue;
            }
            if (collect_node(&children, &child_count, &child_cap, child) < 0) { /* GCOVR_EXCL_BR_LINE: alloc only */
                failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;      /* GCOVR_EXCL_LINE */
            }
        }
        for (Py_ssize_t index = 0; index < child_count && !failed; index++) {
            Py_ssize_t text_len = 0;
            Py_UCS4 *text = th_node_data(walk->tree, children[index], &text_len);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                failed = 1;     /* GCOVR_EXCL_LINE: allocation-failure path */
                break;          /* GCOVR_EXCL_LINE */
            }
            failed = handle_value(walk, &report_element, element, Py_None, children[index], NULL, 0, text, text_len,
                                  scan_css) < 0;
            PyMem_Free(text);
        }
        PyMem_Free(children);
    }
    /* an element is either a <style> or a <meta>, never both, so the style block above cannot have set failed here */
    if (element->atom == TH_TAG_META && is_meta_refresh(walk, element)) {
        Py_ssize_t len = 0;
        const Py_UCS4 *content = attr_value(walk, element, "content", 7, &len);
        if (content != NULL) {
            PyObject *report_attr = NULL;
            if (walk->result != NULL) {
                report_attr = PyUnicode_FromString("content");
                if (report_attr == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
                    Py_XDECREF(report_element); /* GCOVR_EXCL_LINE: allocation-failure path */
                    return -1;                  /* GCOVR_EXCL_LINE */
                }
            }
            failed = handle_value(walk, &report_element, element, report_attr, element, "content", 7, content, len,
                                  scan_meta_refresh) < 0;
            Py_XDECREF(report_attr);
        }
    }
    for (Py_ssize_t index = 0; index < element->attr_count && !failed; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(walk->tree, element->attrs[index].name_atom, &name_len);
        scanner_fn scan = scanner_for_attr(element->atom, name, name_len);
        if (scan == NULL || element->attrs[index].value == NULL) {
            continue;
        }
        PyObject *report_attr = NULL;
        if (walk->result != NULL) {
            report_attr = PyUnicode_FromStringAndSize(name, name_len);
            if (report_attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                failed = 1;            /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                 /* GCOVR_EXCL_LINE */
            }
        }
        if (element->tag_flags & TH_ELEM_IMPLIED) {
            /* only an element without a start tag of its own, a reopened formatting element, borrows its values */
            failed = handle_shared_value(walk, &report_element, element, report_attr, name, name_len,
                                         element->attrs[index].value, element->attrs[index].value_len, scan) < 0;
        } else {
            failed = handle_value(walk, &report_element, element, report_attr, element, name, name_len,
                                  element->attrs[index].value, element->attrs[index].value_len, scan) < 0;
        }
        Py_XDECREF(report_attr);
    }
    Py_XDECREF(report_element);
    return failed ? -1 : 0;
}

/* Pre-order snapshot of every element under `root` (not its siblings) into walk->nodes. This runs no Python API, so the
   per-tree critical section cannot be suspended here and a concurrent structural mutation cannot relink a node while we
   follow first_child/next_sibling/parent. process_element then works off the snapshot, never re-deriving the structure
   across the Python callbacks/allocations it performs (which may suspend the lock). */
static int collect_subtree(link_walk *walk, th_node *root) {
    th_node *current = root;
    while (current != NULL) {
        if (current->type == TH_NODE_ELEMENT) {
            if (collect_node(&walk->nodes, &walk->node_count, &walk->node_cap, current) < 0) { /* GCOVR_EXCL_BR_LINE */
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        if (current->first_child != NULL) {
            current = current->first_child;
            continue;
        }
        while (current != root && current->next_sibling == NULL) {
            current = current->parent;
        }
        current = current == root ? NULL : current->next_sibling;
    }
    return 0;
}

/* Shared engine: run the walk over `root` under `owner`'s per-tree critical section. The thin Node methods in
   dom/node.c derive (owner, tree, root) straight from the node and hand them here; the module state for the Link
   record type and the per-tree handle for the lock come off `owner`. */
static PyObject *run_walk(PyObject *owner, th_tree *tree, th_node *root, PyObject *result, PyObject *replace,
                          const char *base_scheme, Py_ssize_t base_scheme_len, int base_netloc_scheme) {
    link_walk walk = {.tree = tree,
                      .owner = owner,
                      .result = result,
                      .replace = replace,
                      .base_scheme = base_scheme,
                      .base_scheme_len = base_scheme_len,
                      .base_netloc_scheme = base_netloc_scheme};
    walk.state = PyType_GetModuleState(Py_TYPE(owner));
    /* read the handle into the struct (always, so it is covered) rather than into the macro argument, which the GIL
       build's no-op Py_BEGIN_CRITICAL_SECTION does not evaluate. The reference is the walk's own: replace can move
       owner into another tree, which drops the one owner holds, and the walk finishes on the tree it snapshotted */
    walk.handle = Py_NewRef(turbohtml_node_handle(owner));
    int failed;
    Py_BEGIN_CRITICAL_SECTION(walk.handle); /* per-tree lock: no concurrent mutation mid-walk */
    failed = collect_subtree(&walk, root) < 0;
    for (Py_ssize_t index = 0; !failed && index < walk.node_count; index++) {
        failed = process_element(&walk, walk.nodes[index]) < 0;
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(walk.handle);
    for (size_t slot = 0; slot < walk.shared_cap; slot++) {
        Py_XDECREF(walk.shared[slot].urls);
    }
    PyMem_Free(walk.shared);
    PyMem_Free(walk.spans);
    PyMem_Free(walk.nodes);
    if (failed) {
        Py_XDECREF(result);
        return NULL;
    }
    return result != NULL ? result : Py_NewRef(Py_None);
}

/* Store the Link record type the C core constructs for each enumerated link; turbohtml._links registers it on import.
 */
PyObject *turbohtml_register_links(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->link_type, Py_NewRef(type));
    Py_RETURN_NONE;
}

/* Node.links() -> list[Link]. Enumerates every link-bearing location under the node in document order. */
PyObject *turbohtml_node_links(PyObject *owner, th_tree *tree, th_node *root) {
    PyObject *result = PyList_New(0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return run_walk(owner, tree, root, result, NULL, NULL, 0, 0);
}

/* Node.rewrite_links(replace) -> None. Calls replace(url) for every link, except that the copies of a reopened element
   share the first copy's result; a str result replaces it, None leaves it. */
PyObject *turbohtml_node_rewrite_links(PyObject *owner, th_tree *tree, th_node *root, PyObject *replace) {
    if (!PyCallable_Check(replace)) {
        PyErr_SetString(PyExc_TypeError, "rewrite_links expected a callable");
        return NULL;
    }
    return run_walk(owner, tree, root, NULL, replace, NULL, 0, 0);
}

/* base_url's scheme, lowercased into `out` (capacity `cap`), or 0 when it carries no parseable RFC 3986 scheme (alpha
 *(alpha / digit / "+" / "-" / ".") ":") or one too long to be one, in which case the absolute-URL skip stays off. */
static Py_ssize_t base_scheme_of(PyObject *base_url, char *out, Py_ssize_t cap) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(base_url);
    if (len == 0 || !is_ascii_alpha(PyUnicode_READ_CHAR(base_url, 0))) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len && index < cap; index++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(base_url, index);
        if (c == ':') {
            return index;
        }
        if (!is_ascii_alpha(c) && !is_ascii_digit(c) && c != '+' && c != '-' && c != '.') {
            return 0;
        }
        out[index] = (char)lower_ascii(c);
    }
    return 0;
}

/* Node.resolve_links(base_url) -> None. Rewrites every link absolute against base_url with functools.partial bound over
   the C _url_join_link, so a reference's host matches the one a browser resolves -- including the WHATWG backslash and
   extra-slash authority forms stdlib urllib.parse.urljoin misattributes -- without reinventing RFC 3986 resolution. */
PyObject *turbohtml_node_resolve_links(PyObject *owner, th_tree *tree, th_node *root, PyObject *base_url) {
    if (!PyUnicode_Check(base_url)) {
        PyErr_SetString(PyExc_TypeError, "resolve_links expected a base URL string");
        return NULL;
    }
    char base_scheme[16];
    Py_ssize_t base_scheme_len = base_scheme_of(base_url, base_scheme, (Py_ssize_t)sizeof(base_scheme));
    int base_netloc_scheme = (base_scheme_len == 4 && memcmp(base_scheme, "http", 4) == 0) ||
                             (base_scheme_len == 5 && memcmp(base_scheme, "https", 5) == 0);
    PyObject *html_module = PyImport_ImportModule("turbohtml._html");
    if (html_module == NULL) { /* GCOVR_EXCL_BR_LINE: the extension's own module is already imported */
        return NULL;           /* GCOVR_EXCL_LINE: import-failure path */
    }
    PyObject *urljoin = PyObject_GetAttrString(html_module, "_url_join_link");
    Py_DECREF(html_module);
    if (urljoin == NULL) { /* GCOVR_EXCL_BR_LINE: _url_join_link is always registered on the module */
        return NULL;       /* GCOVR_EXCL_LINE: attribute-failure path */
    }
    PyObject *functools_module = PyImport_ImportModule("functools");
    if (functools_module == NULL) { /* GCOVR_EXCL_BR_LINE: a stdlib import cannot be forced to fail from a test */
        Py_DECREF(urljoin);         /* GCOVR_EXCL_LINE: import-failure path */
        return NULL;                /* GCOVR_EXCL_LINE */
    }
    PyObject *partial = PyObject_GetAttrString(functools_module, "partial");
    Py_DECREF(functools_module);
    if (partial == NULL) {  /* GCOVR_EXCL_BR_LINE: functools.partial always exists */
        Py_DECREF(urljoin); /* GCOVR_EXCL_LINE: attribute-failure path */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    PyObject *bound = PyObject_CallFunctionObjArgs(partial, urljoin, base_url, NULL);
    Py_DECREF(partial);
    Py_DECREF(urljoin);
    if (bound == NULL) { /* GCOVR_EXCL_BR_LINE: binding a partial cannot be forced to fail from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = run_walk(owner, tree, root, NULL, bound, base_scheme_len > 0 ? base_scheme : NULL,
                                base_scheme_len, base_netloc_scheme);
    Py_DECREF(bound);
    return result;
}
