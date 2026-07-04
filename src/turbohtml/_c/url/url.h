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

/* A scheme character after the leading letter (RFC 3986 scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )); the one
   definition the url split, linkify, and sanitize scheme scanners share. The leading-letter rule is the caller's. */
static inline int th_scheme_char(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '+' || ch == '-' ||
           ch == '.';
}

#endif /* TURBOHTML_URL_H */
