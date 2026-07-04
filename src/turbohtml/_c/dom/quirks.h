/* Quirks-mode doctype detection: the legacy public/system identifier tables and their
   case-insensitive compare the initial insertion mode consults. #included at the top of
   dom/tree.c in the same translation unit. */

#ifndef TURBOHTML_DOM_QUIRKS_H
#define TURBOHTML_DOM_QUIRKS_H

/* Match buf against pat case-insensitively. With whole set, require equal
   length; otherwise accept pat as a prefix of buf. */
static int buf_imatches(const th_buf *buf, const char *pat, int whole) {
    Py_ssize_t plen = (Py_ssize_t)strlen(pat);
    if (buf->len < plen || (whole && buf->len != plen)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < plen; index++) {
        if (lower_ascii(buf_read(buf, index)) != lower_ascii((Py_UCS4)(unsigned char)pat[index])) {
            return 0;
        }
    }
    return 1;
}

/* WHATWG quirks-mode triggers: forced-quirks flag, a non-html name, or a
   legacy public/system identifier. */
static int doctype_is_quirky(const th_token *tok) {
    static const char *const QUIRKY_EXACT[] = {
        "-//W3O//DTD W3 HTML Strict 3.0//EN//",
        "-/W3C/DTD HTML 4.0 Transitional/EN",
        "HTML",
    };
    static const char *const QUIRKY_PREFIXES[] = {
        "+//Silmaril//dtd html Pro v0r11 19970101//",
        "-//AS//DTD HTML 3.0 asWedit + extensions//",
        "-//AdvaSoft Ltd//DTD HTML 3.0 asWedit + extensions//",
        "-//IETF//DTD HTML 2.0 Level 1//",
        "-//IETF//DTD HTML 2.0 Level 2//",
        "-//IETF//DTD HTML 2.0 Strict Level 1//",
        "-//IETF//DTD HTML 2.0 Strict Level 2//",
        "-//IETF//DTD HTML 2.0 Strict//",
        "-//IETF//DTD HTML 2.0//",
        "-//IETF//DTD HTML 2.1E//",
        "-//IETF//DTD HTML 3.0//",
        "-//IETF//DTD HTML 3.2 Final//",
        "-//IETF//DTD HTML 3.2//",
        "-//IETF//DTD HTML 3//",
        "-//IETF//DTD HTML Level 0//",
        "-//IETF//DTD HTML Level 1//",
        "-//IETF//DTD HTML Level 2//",
        "-//IETF//DTD HTML Level 3//",
        "-//IETF//DTD HTML Strict Level 0//",
        "-//IETF//DTD HTML Strict Level 1//",
        "-//IETF//DTD HTML Strict Level 2//",
        "-//IETF//DTD HTML Strict Level 3//",
        "-//IETF//DTD HTML Strict//",
        "-//IETF//DTD HTML//",
        "-//Metrius//DTD Metrius Presentational//",
        "-//Microsoft//DTD Internet Explorer 2.0 HTML Strict//",
        "-//Microsoft//DTD Internet Explorer 2.0 HTML//",
        "-//Microsoft//DTD Internet Explorer 2.0 Tables//",
        "-//Microsoft//DTD Internet Explorer 3.0 HTML Strict//",
        "-//Microsoft//DTD Internet Explorer 3.0 HTML//",
        "-//Microsoft//DTD Internet Explorer 3.0 Tables//",
        "-//Netscape Comm. Corp.//DTD HTML//",
        "-//Netscape Comm. Corp.//DTD Strict HTML//",
        "-//O'Reilly and Associates//DTD HTML 2.0//",
        "-//O'Reilly and Associates//DTD HTML Extended 1.0//",
        "-//O'Reilly and Associates//DTD HTML Extended Relaxed 1.0//",
        "-//SQ//DTD HTML 2.0 HoTMetaL + extensions//",
        "-//SoftQuad Software//DTD HoTMetaL PRO 6.0::19990601::extensions to HTML 4.0//",
        "-//SoftQuad//DTD HoTMetaL PRO 4.0::19971010::extensions to HTML 4.0//",
        "-//Spyglass//DTD HTML 2.0 Extended//",
        "-//Sun Microsystems Corp.//DTD HotJava HTML//",
        "-//Sun Microsystems Corp.//DTD HotJava Strict HTML//",
        "-//W3C//DTD HTML 3 1995-03-24//",
        "-//W3C//DTD HTML 3.2 Draft//",
        "-//W3C//DTD HTML 3.2 Final//",
        "-//W3C//DTD HTML 3.2//",
        "-//W3C//DTD HTML 3.2S Draft//",
        "-//W3C//DTD HTML 4.0 Frameset//",
        "-//W3C//DTD HTML 4.0 Transitional//",
        "-//W3C//DTD HTML Experimental 19960712//",
        "-//W3C//DTD HTML Experimental 970421//",
        "-//W3C//DTD W3 HTML//",
        "-//W3O//DTD W3 HTML 3.0//",
        "-//WebTechs//DTD Mozilla HTML 2.0//",
        "-//WebTechs//DTD Mozilla HTML//",
    };
    if (tok->force_quirks || !buf_imatches(&tok->name, "html", 1)) {
        return 1;
    }
    if (tok->has_public_id) {
        for (size_t index = 0; index < sizeof(QUIRKY_EXACT) / sizeof(QUIRKY_EXACT[0]); index++) {
            if (buf_imatches(&tok->public_id, QUIRKY_EXACT[index], 1)) {
                return 1;
            }
        }
        for (size_t index = 0; index < sizeof(QUIRKY_PREFIXES) / sizeof(QUIRKY_PREFIXES[0]); index++) {
            if (buf_imatches(&tok->public_id, QUIRKY_PREFIXES[index], 0)) {
                return 1;
            }
        }
        /* the spec triggers quirks when the system identifier is "missing or the empty
           string"; only a non-empty system identifier downgrades these two public
           identifiers to limited-quirks (which turbohtml treats as no-quirks) */
        if ((!tok->has_system_id || tok->system_id.len == 0) &&
            (buf_imatches(&tok->public_id, "-//W3C//DTD HTML 4.01 Frameset//", 0) ||
             buf_imatches(&tok->public_id, "-//W3C//DTD HTML 4.01 Transitional//", 0))) {
            return 1;
        }
    }
    if (tok->has_system_id &&
        buf_imatches(&tok->system_id, "http://www.ibm.com/data/dtd/v11/ibmxhtml1-transitional.dtd", 1)) {
        return 1;
    }
    return 0;
}

#endif /* TURBOHTML_DOM_QUIRKS_H */
