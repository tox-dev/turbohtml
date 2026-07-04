/* URL splitting and host classification, the WHATWG basic-URL-parser primitives the thin _urls.py shim delegates to.

   _urls.py used to reach urllib.parse.urlsplit to break a URL into its components; that split is the hot step of the
   crawl-oriented cleaner, so it lives in C now (tox-dev/turbohtml#478). This header carries the two pieces more than
   one translation unit needs: the scheme-grammar predicate (RFC 3986 scheme production), which linkify.c and sanitize.c
   scan a scheme with too and used to each redefine, and the host-classification tags url_split reports for each
   authority. The percent-encoding, relative join, IDNA host ToASCII, and registrable-domain fold stay elsewhere; this
   unit only splits and classifies ASCII authorities. */

#ifndef TURBOHTML_URL_H
#define TURBOHTML_URL_H

#include "core/common.h"

/* The kind of host url_split found inside an authority, so the shim skips IDNA for the two literal forms a domain
   name never takes: a bracketed IPv6 literal, a dotted-decimal IPv4 literal, or an ASCII/Unicode registered name. */
enum { TH_HOST_REGNAME = 0, TH_HOST_IPV4 = 1, TH_HOST_IPV6 = 2 };

/* The WHATWG component percent-encode sets (URL standard 1.3), the safe run each encoder keeps raw; every other byte is
   UTF-8 percent-encoded. th_url_encode_span and turbohtml_url_percent_encode take one of these tags. */
enum { TH_URL_SET_PATH = 0, TH_URL_SET_QUERY = 1, TH_URL_SET_FRAGMENT = 2 };

/* The RFC 3986 ALPHA run, shared by the percent-encode keep sets in url.c and the scheme scanners that build on it. */
#define TH_URL_ALPHA "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

/* A scheme character after the leading letter (RFC 3986 scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )); the one
   definition the url split, linkify, and sanitize scheme scanners share. The leading-letter rule is the caller's. */
static inline int th_scheme_char(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '+' || ch == '-' ||
           ch == '.';
}

/* memchr against a literal set, never a chained ||: clang inlines this into the encode loop, where an ``a || b || c``
   of byte ranges fractures the macOS branch gate (a NUL byte or the terminator never matches). */
static inline int th_url_in_set(unsigned char byte, const char *set, size_t set_len) {
    return memchr(set, byte, set_len) != NULL;
}

/* Append bytes[start:end] to out at offset `at`, percent-encoding every byte outside `set_id`'s keep run with uppercase
   hex; returns the new write offset. The one component-encode primitive url_split's cleaner and base_url() both reach.
 */
Py_ssize_t th_url_encode_span(char *out, Py_ssize_t at, const char *bytes, Py_ssize_t start, Py_ssize_t end,
                              int set_id);

#endif /* TURBOHTML_URL_H */
