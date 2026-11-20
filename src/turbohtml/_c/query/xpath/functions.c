/* The XPath 1.0 core function library plus the EXSLT regular-expression extensions
   parsel and scrapy lean on, and the Python hook the parser conformance tests call. */

#include "core/common.h"
#include "core/hash.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "dom/tree.h"
#include "query/xpath/internal.h"
#include "query/xpath/regex.h"
#include "query/xpath/xpath.h"
#include "tokenizer/binding.h"

#include <math.h>
#include <string.h>

#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

/* XPath 1.0 round(): the integer closest to the argument, ties resolved toward
   positive infinity (§4.4, so round(-2.5) is -2, not the -3 that C round() gives
   by rounding half away from zero), and [-0.5, 0) rounds to negative zero. The
   fraction value - floor(value) is exact, where floor(value + 0.5) rounds the sum
   first: 0.49999999999999994 + 0.5 is 1.0, and 2^52 + 1 + 0.5 is 2^52 + 2. NaN, the
   infinities, and both zeros pass through, as the comparisons fail for them. */
static double xp_round(double value) {
    if (value < 0 && value >= -0.5) {
        return -0.0;
    }
    double floored = floor(value);
    return value - floored >= 0.5 ? floored + 1 : floored;
}

/* number() with no argument: the context node's string-value parsed as first number. */
static double context_node_number(xp_ctx *ctx) {
    xp_item item = {ctx->node, ctx->attr};
    Py_ssize_t length;
    Py_UCS4 *text = item_string(ctx->tree, item, &length);
    if (text == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return (double)NAN; /* GCOVR_EXCL_LINE */
    }
    double value = parse_number(text, length);
    PyMem_Free(text);
    return value;
}

static int func_is(const xn *fn, const char *kw) {
    Py_ssize_t index = 0;
    for (; kw[index] != '\0'; index++) {
        if (index >= fn->str_len || fn->str[index] != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return index == fn->str_len;
}

static Py_ssize_t ucs4_find_kmp(const Py_UCS4 *hay, Py_ssize_t hlen, const Py_UCS4 *needle, Py_ssize_t nlen,
                                Py_ssize_t offset) {
    Py_ssize_t *prefix = PyMem_Malloc((size_t)nlen * sizeof(*prefix));
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -2;        /* GCOVR_EXCL_LINE */
    }
    prefix[0] = 0;
    for (Py_ssize_t index = 1, matched = 0; index < nlen; index++) {
        while (matched > 0 && needle[index] != needle[matched]) {
            matched = prefix[matched - 1];
        }
        if (needle[index] == needle[matched]) {
            matched++;
        }
        prefix[index] = matched;
    }
    Py_ssize_t matched = 0;
    for (Py_ssize_t index = offset; index < hlen; index++) {
        while (matched > 0 && hay[index] != needle[matched]) {
            matched = prefix[matched - 1];
        }
        if (hay[index] == needle[matched]) {
            matched++;
            if (matched == nlen) {
                PyMem_Free(prefix);
                return index - nlen + 1;
            }
        }
    }
    PyMem_Free(prefix);
    return -1;
}

static Py_ssize_t ucs4_find(const Py_UCS4 *hay, Py_ssize_t hlen, const Py_UCS4 *needle, Py_ssize_t nlen) {
    if (nlen == 0) {
        return 0;
    }
    if (nlen > hlen) {
        return -1;
    }
    if (memcmp(hay, needle, (size_t)nlen * sizeof(Py_UCS4)) == 0) {
        return 0;
    }
    size_t candidates = nlen > 64 && hlen >= 256 ? 64 : SIZE_MAX;
    Py_ssize_t last = nlen - 1;
    for (Py_ssize_t index = 1; index + nlen <= hlen; index++) {
        if (hay[index + last] != needle[last]) {
            continue;
        }
        if (memcmp(hay + index, needle, (size_t)nlen * sizeof(Py_UCS4)) == 0) {
            return index;
        }
        if (candidates != SIZE_MAX) {
            candidates--;
            if (candidates == 0) {
                Py_ssize_t found = ucs4_find_kmp(hay, hlen, needle, nlen, index + 1);
                if (found != -2) { /* GCOVR_EXCL_BR_LINE: -2 only after unforceable allocation failure */
                    return found;
                }
                candidates = SIZE_MAX; /* GCOVR_EXCL_LINE: retain the allocation-free scan */
            } /* GCOVR_EXCL_LINE: brace of the allocation-failure fallback */
        }
    }
    return -1;
}

/* The string-value to operate on: the first argument's, or the context node's when
   the function was called with no arguments. */
static Py_UCS4 *arg_or_context_string(xp_ctx *ctx, xp_result *args, int argc, Py_ssize_t *len) {
    if (argc >= 1) {
        return to_string(ctx->tree, &args[0], len);
    }
    xp_item item = {ctx->node, ctx->attr};
    return item_string(ctx->tree, item, len);
}

/* The qualified name of a node-set's first node (or the context node), as a fresh
   string; empty for a non-named node or an empty node-set. */
static Py_UCS4 *node_name_string(xp_ctx *ctx, xp_result *args, int argc, Py_ssize_t *len) {
    struct th_node *node = ctx->node;
    Py_ssize_t attr = ctx->attr;
    if (argc >= 1) {
        if (args[0].kind != XP_NODESET || args[0].nodes.len == 0) {
            *len = 0;
            return ucs4_dup(NULL, 0);
        }
        node = args[0].nodes.items[0].node;
        attr = args[0].nodes.items[0].attr;
    }
    if (attr == -2) {
        return ucs4_from_ascii(XP_XML_NS_PREFIX, sizeof(XP_XML_NS_PREFIX) - 1, len);
    }
    if (xp_is_attribute(attr)) {
        Py_ssize_t blen;
        const char *bytes = xp_item_attr_name(ctx->tree, (xp_item){node, attr}, &blen);
        Py_UCS4 *buffer = ucs4_from_utf8(bytes, blen, len);
        if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        return buffer;
    }
    if (node->type == TH_NODE_ELEMENT) {
        *len = node->text_len;
        return ucs4_dup(node->text, node->text_len);
    }
    *len = 0;
    return ucs4_dup(NULL, 0);
}

/* normalize-space: trim ends, collapse internal whitespace runs to one space. */
static int normalize_space(const Py_UCS4 *text, Py_ssize_t len, xp_result *out) {
    Py_UCS4 *buf = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t write_pos = 0;
    int in_space = 1; /* leading whitespace is dropped */
    for (Py_ssize_t index = 0; index < len; index++) {
        if (xp_is_space(text[index])) {
            in_space = 1;
        } else {
            if (in_space && write_pos > 0) {
                buf[write_pos++] = ' ';
            }
            buf[write_pos++] = text[index];
            in_space = 0;
        }
    }
    result_string(out, buf, write_pos);
    return 0;
}

typedef struct {
    Py_UCS4 character;
    Py_ssize_t position;
} translate_entry;

/* A run longer than TH_FAST_PROBE_LIMIT covers a whole aligned block of TRANSLATE_BLOCK slots, of which
   translate_runs_long reads every TRANSLATE_SAMPLE-th; translate_index walks at most TRANSLATE_WALKS steps per
   character on the multiplicative hash. */
#define TRANSLATE_BLOCK 32
#define TRANSLATE_SAMPLE 8
#define TRANSLATE_WALKS 4

static size_t translate_slot(const translate_entry *entries, size_t mask, Py_UCS4 character, int keyed);
static int translate_index(translate_entry *entries, size_t capacity, const Py_UCS4 *from, Py_ssize_t flen);
static inline Py_ssize_t translate_rest(const translate_entry *entries, size_t mask, int keyed, const Py_UCS4 *text,
                                        Py_ssize_t index, Py_ssize_t slen, const Py_UCS4 *to, Py_ssize_t tlen,
                                        Py_UCS4 *buf, Py_ssize_t write_pos);
static TH_NOINLINE Py_ssize_t translate_rest_keyed(const translate_entry *entries, size_t mask, const Py_UCS4 *text,
                                                   Py_ssize_t index, Py_ssize_t slen, const Py_UCS4 *to,
                                                   Py_ssize_t tlen, Py_UCS4 *buf, Py_ssize_t write_pos);

static int string_arg(struct th_tree *tree, const xp_result *value, const Py_UCS4 **text, Py_ssize_t *len,
                      Py_UCS4 **owned) {
    if (value->kind == XP_STRING) {
        *text = value->string;
        *len = value->string_len;
        *owned = NULL;
        return 0;
    }
    *owned = to_string(tree, value, len);
    *text = *owned;
    return *owned == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
}

/* Out of line, so its scan loops keep their registers instead of sharing eval_function's. */
static TH_NOINLINE int translate(const Py_UCS4 *text, Py_ssize_t slen, const Py_UCS4 *from, Py_ssize_t flen,
                                 const Py_UCS4 *to, Py_ssize_t tlen, xp_result *out) {
    Py_UCS4 *buf = PyMem_Malloc((size_t)slen * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    if (slen < 64) {
        Py_ssize_t write_pos = 0;
        for (Py_ssize_t index = 0; index < slen; index++) {
            Py_ssize_t from_index = 0;
            while (from_index < flen && from[from_index] != text[index]) {
                from_index++;
            }
            if (from_index >= flen) {
                buf[write_pos++] = text[index];
            } else if (from_index < tlen) {
                buf[write_pos++] = to[from_index];
            }
        }
        result_string(out, buf, write_pos);
        return 0;
    }
    translate_entry *entries = NULL;
    size_t capacity = 0;
    int keyed = 0;
    size_t scan_budget = (size_t)flen * 2;
    Py_ssize_t write_pos = 0;
    Py_ssize_t index = 0;
    Py_UCS4 previous = 0xFFFFFFFF;
    Py_UCS4 replacement = 0;
    int emit_previous = 0;
    for (; index < slen; index++) {
        if (text[index] == previous) {
            if (emit_previous) {
                buf[write_pos++] = replacement;
            }
            continue;
        }
        Py_ssize_t from_index = 0;
        while (from_index < flen && from[from_index] != text[index]) {
            from_index++;
        }
        if (from_index >= 8 && flen >= 16 && slen - index > 16) {
            if ((size_t)from_index < scan_budget) {
                scan_budget -= (size_t)from_index;
            } else {
                size_t bytes;
                const int fits = th_grow_cap((size_t)flen * 2, 0, 32, sizeof(*entries), &capacity, &bytes);
                if (!fits) {         /* GCOVR_EXCL_BR_LINE: allocation size overflow */
                    PyMem_Free(buf); /* GCOVR_EXCL_LINE: allocation size overflow */
                    return -1;       /* GCOVR_EXCL_LINE: allocation size overflow */
                }
                entries = PyMem_Calloc(1, bytes);
                if (entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    PyMem_Free(buf);   /* GCOVR_EXCL_LINE: allocation failure */
                    return -1;         /* GCOVR_EXCL_LINE: allocation failure */
                }
                keyed = translate_index(entries, capacity, from, flen);
                break;
            }
        }
        previous = text[index];
        emit_previous = from_index >= flen || from_index < tlen;
        if (emit_previous) {
            replacement = from_index >= flen ? text[index] : to[from_index];
            buf[write_pos++] = replacement;
        }
    }
    if (TH_UNLIKELY(keyed)) {
        write_pos = translate_rest_keyed(entries, capacity - 1, text, index, slen, to, tlen, buf, write_pos);
    } else {
        write_pos = translate_rest(entries, capacity - 1, 0, text, index, slen, to, tlen, buf, write_pos);
    }
    PyMem_Free(entries);
    result_string(out, buf, write_pos);
    return 0;
}

/* The multiplicative hash keeps only the low bits of the product, which code points that agree in their low bits
   share, so a translate() map can be given characters that all land on one home. Past TH_FAST_PROBE_LIMIT the table
   moves to th_key_mix, which a document cannot steer (core/hash.h). */
static size_t translate_slot(const translate_entry *entries, size_t mask, Py_UCS4 character, int keyed) {
    const size_t home = (keyed ? (size_t)th_key_mix(character) : (size_t)character * 2654435761u) & mask;
    const translate_entry *entry = &entries[home];
    TH_WORK_STEP();
    /* the walk steps a pointer, wrapping at the last slot, so each lookup keeps the slot's address alone rather than an
       index and the address computed from it */
    while (entry->position != 0 && entry->character != character) {
        TH_WORK_STEP();
        entry = entry == &entries[mask] ? entries : entry + 1;
    }
    return (size_t)(entry - entries);
}

/* 1 when a run of filled slots is longer than TH_FAST_PROBE_LIMIT. Such a run covers a whole aligned block of
   TRANSLATE_BLOCK slots, and so the slot that starts it, so the walk reads every TRANSLATE_SAMPLE-th slot and measures
   the run through the start of a block only when all its samples are filled, which they rarely are at half load. Each
   measure stops past the limit and the samples are a fraction of the slots, so none is a counted work step. */
static TH_NOINLINE int translate_runs_long(const translate_entry *entries, size_t capacity) {
    size_t mask = capacity - 1;
    for (size_t block = 0; block < capacity; block += TRANSLATE_BLOCK) {
        size_t sample = block;
        while (sample < block + TRANSLATE_BLOCK && entries[sample].position != 0) {
            sample += TRANSLATE_SAMPLE;
        }
        if (sample < block + TRANSLATE_BLOCK) {
            continue;
        }
        size_t length = 1;
        for (size_t probe = (block - 1) & mask; length <= TH_FAST_PROBE_LIMIT && entries[probe].position != 0;
             probe = (probe - 1) & mask) {
            length++;
        }
        for (size_t probe = (block + 1) & mask; length <= TH_FAST_PROBE_LIMIT && entries[probe].position != 0;
             probe = (probe + 1) & mask) {
            length++;
        }
        if (length > TH_FAST_PROBE_LIMIT) {
            return 1;
        }
    }
    return 0;
}

/* Out of line, so th_key_mix stays out of the multiplicative fill. */
static TH_NOINLINE void translate_index_keyed(translate_entry *entries, size_t capacity, const Py_UCS4 *from,
                                              Py_ssize_t flen) {
    memset(entries, 0, capacity * sizeof(*entries));
    for (Py_ssize_t position = 0; position < flen; position++) {
        const size_t slot = translate_slot(entries, capacity - 1, from[position], 1);
        if (entries[slot].position == 0) {
            entries[slot] = (translate_entry){from[position], position + 1};
        }
    }
}

/* Fill the zeroed table with the first position of each from character, and return 1 when it ended on th_key_mix. The
   multiplicative hash lets a map put its characters on one home, which makes the fill quadratic, or line them up into
   one long run, which every lookup that misses walks to its end. So the fill starts over on th_key_mix (core/hash.h)
   once its walks pass TRANSLATE_WALKS steps per character, or when it leaves a run longer than TH_FAST_PROBE_LIMIT. A
   fill that seats each character at or next to its home, as ordinary maps do, takes no step of either. */
static int translate_index(translate_entry *entries, size_t capacity, const Py_UCS4 *from, Py_ssize_t flen) {
    size_t mask = capacity - 1;
    size_t budget = (size_t)flen * TRANSLATE_WALKS;
    for (Py_ssize_t position = 0; position < flen; position++) {
        size_t slot = ((size_t)from[position] * 2654435761u) & mask;
        TH_WORK_STEP();
        while (entries[slot].position != 0 && entries[slot].character != from[position]) {
            TH_WORK_STEP();
            if (--budget == 0) {
                translate_index_keyed(entries, capacity, from, flen);
                return 1;
            }
            slot = (slot + 1) & mask;
        }
        if (entries[slot].position == 0) {
            entries[slot] = (translate_entry){from[position], position + 1};
        }
    }
    if (flen > TH_FAST_PROBE_LIMIT && translate_runs_long(entries, capacity)) {
        translate_index_keyed(entries, capacity, from, flen);
        return 1;
    }
    return 0;
}

/* Translate text[index, slen) through the table into buf from write_pos, returning the new write position. keyed is a
   constant at each call, so each copy of the loop computes one hash. */
static inline Py_ssize_t translate_rest(const translate_entry *entries, size_t mask, int keyed, const Py_UCS4 *text,
                                        Py_ssize_t index, Py_ssize_t slen, const Py_UCS4 *to, Py_ssize_t tlen,
                                        Py_UCS4 *buf, Py_ssize_t write_pos) {
    for (; index < slen; index++) {
        const Py_ssize_t position = entries[translate_slot(entries, mask, text[index], keyed)].position;
        if (position == 0) {
            buf[write_pos++] = text[index];
        } else if (position <= tlen) {
            buf[write_pos++] = to[position - 1];
        }
    }
    return write_pos;
}

/* translate_rest on th_key_mix. Out of line, so the multiplicative copy keeps translate small. */
static TH_NOINLINE Py_ssize_t translate_rest_keyed(const translate_entry *entries, size_t mask, const Py_UCS4 *text,
                                                   Py_ssize_t index, Py_ssize_t slen, const Py_UCS4 *to,
                                                   Py_ssize_t tlen, Py_UCS4 *buf, Py_ssize_t write_pos) {
    return translate_rest(entries, mask, 1, text, index, slen, to, tlen, buf, write_pos);
}

static int substring(struct th_tree *tree, xp_result *args, int argc, xp_result *out) {
    Py_ssize_t slen;
    Py_UCS4 *text = to_string(tree, &args[0], &slen);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    double start = xp_round(to_number(tree, &args[1]));
    double last = argc >= 3 ? start + xp_round(to_number(tree, &args[2])) : (double)slen + 1;
    Py_ssize_t lo = start < 1 ? 0 : (start > (double)slen + 1 ? slen : (Py_ssize_t)start - 1);
    Py_ssize_t hi = last < 1 ? 0 : (last > (double)slen + 1 ? slen : (Py_ssize_t)last - 1);
    if (hi < lo) {
        hi = lo;
    }
    Py_UCS4 *result_text = ucs4_dup(text + lo, hi - lo);
    PyMem_Free(text);
    if (result_text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;             /* GCOVR_EXCL_LINE */
    }
    result_string(out, result_text, hi - lo);
    return 0;
}

/* concat(s, s, s*): the string-values of every argument joined. Variadic (two or more
   arguments), so the per-argument strings are held in a heap array sized to the call. */
static int concat(struct th_tree *tree, xp_result *args, int argc, xp_result *out) {
    Py_UCS4 **parts = PyMem_Calloc((size_t)argc, sizeof(Py_UCS4 *));
    Py_ssize_t *lens = PyMem_Calloc((size_t)argc, sizeof(Py_ssize_t));
    if (parts == NULL || lens == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(parts);               /* GCOVR_EXCL_LINE */
        PyMem_Free(lens);                /* GCOVR_EXCL_LINE */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    int rc = 0;
    Py_ssize_t total = 0;
    for (int index = 0; index < argc; index++) {
        parts[index] = to_string(tree, &args[index], &lens[index]);
        if (parts[index] == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        total += lens[index];
    }
    if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        Py_UCS4 *buf = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;       /* GCOVR_EXCL_LINE */
        } else {           /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            Py_ssize_t write_pos = 0;
            for (int index = 0; index < argc; index++) {
                memcpy(buf + write_pos, parts[index], (size_t)lens[index] * sizeof(Py_UCS4));
                write_pos += lens[index];
            }
            result_string(out, buf, total);
        }
    }
    for (int index = 0; index < argc; index++) {
        PyMem_Free(parts[index]);
    }
    PyMem_Free(parts);
    PyMem_Free(lens);
    return rc;
}

/* namespace-uri(): empty for HTML elements, attributes, namespace nodes, and
   non-elements; the foreign-content URI for an SVG or MathML element. */
static Py_UCS4 *node_namespace_uri(xp_ctx *ctx, xp_result *args, int argc, Py_ssize_t *len) {
    struct th_node *node = ctx->node;
    Py_ssize_t attr = ctx->attr;
    if (argc >= 1) {
        if (args[0].kind != XP_NODESET || args[0].nodes.len == 0) {
            *len = 0;
            return ucs4_dup(NULL, 0);
        }
        node = args[0].nodes.items[0].node;
        attr = args[0].nodes.items[0].attr;
    }
    const char *uri = "";
    Py_ssize_t uri_len = 0;
    if (attr == -1 && node->type == TH_NODE_ELEMENT) {
        if (node->ns == TH_NS_SVG) {
            uri = XP_SVG_NS_URI;
            uri_len = sizeof(XP_SVG_NS_URI) - 1;
        } else if (node->ns == TH_NS_MATHML) {
            uri = XP_MATHML_NS_URI;
            uri_len = sizeof(XP_MATHML_NS_URI) - 1;
        }
    }
    return ucs4_from_ascii(uri, uri_len, len);
}

/* RFC 4647 prefix match, ASCII case-insensitive: the tag equals the wanted code
   or extends it with a "-subtag" suffix (so "en" matches "en" and "en-US"). */
static int lang_tag_matches(const Py_UCS4 *tag, Py_ssize_t tag_len, const Py_UCS4 *want, Py_ssize_t want_len) {
    if (tag_len < want_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < want_len; index++) {
        Py_UCS4 in_tag = tag[index] >= 'A' && tag[index] <= 'Z' ? tag[index] + 32 : tag[index];
        Py_UCS4 in_want = want[index] >= 'A' && want[index] <= 'Z' ? want[index] + 32 : want[index];
        if (in_tag != in_want) {
            return 0;
        }
    }
    return tag_len == want_len || tag[want_len] == '-';
}

/* The attribute declaring a node's language, or NULL. XPath 1.0 §4.3 reads xml:lang,
   the only form an XML tree honors. An HTML tree follows HTML "the lang and xml:lang
   attributes": xml:lang in the XML namespace wins, and only a foreign element carries
   one (the HTML parser leaves xml:lang on an HTML element in no namespace); then lang,
   which counts on an HTML or SVG element. */
const th_node_attr *xp_node_lang_attr(struct th_tree *tree, struct th_node *node, uint32_t xml_lang_atom) {
    int xml = th_tree_is_xml(tree);
    const th_node_attr *lang_attr = NULL;
    th_node_attr *attrs;
    Py_ssize_t attr_count = th_node_attributes(node, &attrs);
    for (Py_ssize_t index = 0; index < attr_count; index++) {
        TH_WORK_STEP();
        if (attrs[index].name_atom == xml_lang_atom && (xml || node->ns != TH_NS_HTML)) {
            return &attrs[index];
        }
        if (attrs[index].name_atom == TH_ATTR_LANG && !xml && node->ns != TH_NS_MATHML) {
            lang_attr = &attrs[index];
        }
    }
    return lang_attr;
}

/* Nodes and attributes lang() reads before it climbs through the memo: ordinary markup declares its language within
   them and never pays for a hash probe. */
#define XP_LANG_CLIMB 64

/* lang(): true when the language the nearest self-or-ancestor element declares
   names a language the wanted code is a prefix of. */
static int node_lang(xp_ctx *ctx, xp_result *arg) {
    Py_ssize_t want_len;
    Py_UCS4 *want = to_string(ctx->tree, arg, &want_len);
    if (want == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    uint32_t xml_lang_atom = th_attr_lookup(ctx->tree, "xml:lang", 8);
    const th_node_attr *lang_attr = NULL;
    Py_ssize_t budget = XP_LANG_CLIMB;
    for (struct th_node *node = ctx->node; node != NULL; node = node->parent) {
        TH_WORK_STEP();
        th_node_attr *attrs;
        budget -= 1 + th_node_attributes(node, &attrs);
        if (budget < 0) {
            lang_attr = xp_lang_memoized(ctx, node, xml_lang_atom);
            break;
        }
        lang_attr = xp_node_lang_attr(ctx->tree, node, xml_lang_atom);
        if (lang_attr != NULL) {
            break;
        }
    }
    int result = lang_attr != NULL && lang_tag_matches(lang_attr->value, lang_attr->value_len, want, want_len);
    PyMem_Free(want);
    return result;
}

typedef struct {
    const Py_UCS4 *value;
    Py_ssize_t len;
    int claimed; /* the walk in eval_id has met the first element with this id */
} xp_id_token;

/* FNV-1a over the code points, the hash the token set starts on. The document picks the tokens, and FNV-1a lets it
   pick ones that share a home, so a set that walks past TH_FAST_PROBE_LIMIT slots is built again under th_name_hash
   (core/hash.h). */
static uint64_t id_token_hash(const Py_UCS4 *value, Py_ssize_t len, int keyed) {
    if (keyed) {
        return th_name_hash(value, len * (Py_ssize_t)sizeof(Py_UCS4));
    }
    uint64_t hash = 14695981039346656037u;
    for (Py_ssize_t index = 0; index < len; index++) {
        hash ^= value[index];
        hash *= 1099511628211u;
    }
    return hash;
}

/* 1 when the TH_FAST_PROBE_LIMIT slots before probe are all filled. Out of line, so the loops that inline the test
   below keep their registers. The walk stops at the limit, so it is no counted work step. */
static TH_NOINLINE int id_token_run_full(const xp_id_token *slots, size_t mask, size_t probe) {
    for (size_t back = 1; back <= TH_FAST_PROBE_LIMIT; back++) {
        if (slots[(probe - back) & mask].value == NULL) {
            return 0;
        }
    }
    return 1;
}

/* 1 when the TH_FAST_PROBE_LIMIT + 1 slots before probe are all filled, which a walk that ended at probe more than
   TH_FAST_PROBE_LIMIT slots from its home must have crossed. Testing the farthest slot first settles most calls with
   one load, and the fill's walk needs no home kept across its comparisons. */
static inline int id_token_run_behind(const xp_id_token *slots, size_t mask, size_t probe) {
    if (slots[(probe - TH_FAST_PROBE_LIMIT - 1) & mask].value == NULL) {
        return 0;
    }
    return id_token_run_full(slots, mask, probe);
}

/* Seat the whitespace-separated tokens of list in the zeroed slots. On FNV-1a it stops with 1 once a token would sit
   behind a run of more than TH_FAST_PROBE_LIMIT filled slots; a token walks only through the run behind its seat, and a
   repeated one no further than its first copy sat, so every walk of the fill stays within the limit. */
static inline int id_token_set_fill(const Py_UCS4 *list, Py_ssize_t list_len, xp_id_token *slots, size_t mask,
                                    int keyed) {
    Py_ssize_t index = 0;
    while (index < list_len) {
        while (index < list_len && xp_is_space(list[index])) {
            index++;
        }
        if (index == list_len) {
            break;
        }
        /* start < list_len here, so the token holds at least one character */
        Py_ssize_t start = index;
        uint64_t hash = 14695981039346656037u;
        while (index < list_len && !xp_is_space(list[index])) {
            hash ^= list[index++];
            hash *= 1099511628211u;
        }
        Py_ssize_t len = index - start;
        if (keyed) {
            hash = id_token_hash(list + start, len, 1);
        }
        size_t probe = hash & mask;
        while (slots[probe].value != NULL && (slots[probe].len != len || memcmp(slots[probe].value, list + start,
                                                                                (size_t)len * sizeof(Py_UCS4)) != 0)) {
            TH_WORK_STEP();
            probe = (probe + 1) & mask;
        }
        if (slots[probe].value == NULL) {
            if (!keyed && id_token_run_behind(slots, mask, probe)) {
                return 1;
            }
            slots[probe] = (xp_id_token){list + start, len, 0};
        }
    }
    return 0;
}

/* Clear the slots and seat the tokens again under th_name_hash. Out of line, so the FNV-1a fill and lookups keep their
   registers. */
static TH_NOINLINE void id_token_set_rekey(const Py_UCS4 *list, Py_ssize_t list_len, xp_id_token *slots, size_t mask) {
    memset(slots, 0, (mask + 1) * sizeof(xp_id_token));
    id_token_set_fill(list, list_len, slots, mask, 1);
}

static int id_token_set_build(const Py_UCS4 *list, Py_ssize_t list_len, xp_id_token **slots, size_t *mask, int *keyed) {
    Py_ssize_t count = 0;
    Py_ssize_t index = 0;
    while (index < list_len) {
        while (index < list_len && xp_is_space(list[index])) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < list_len && !xp_is_space(list[index])) {
            index++;
        }
        count += index > start;
    }
    *keyed = 0;
    if (count == 0) {
        *slots = NULL;
        *mask = 0;
        return 0;
    }
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)count * 2, 0, 8, sizeof(xp_id_token), &cap, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: the string cannot hold enough tokens to overflow */
        return -1; /* GCOVR_EXCL_LINE: unreachable size-overflow path */
    }
    *slots = PyMem_Calloc(1, bytes);
    if (*slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *mask = cap - 1;
    if (id_token_set_fill(list, list_len, *slots, *mask, 0)) {
        *keyed = 1;
        id_token_set_rekey(list, list_len, *slots, *mask);
    }
    return 0;
}

/* 1 when value is in the set and no earlier element claimed it, which claims it; 0 when it is not in the set or was
   claimed; 2 when a miss on FNV-1a walked past TH_FAST_PROBE_LIMIT slots: a hit walks no further than its token did in
   the fill, but a miss walks to the end of the run its home falls in, and the document can line up homes into one long
   run without any token walking far. Of the elements that share an id only the first in document order has it (XPath
   1.0 section 5.2.1), so each token selects at most one element. */
static inline int id_token_set_claim(xp_id_token *slots, size_t mask, int keyed, const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    size_t hash = id_token_hash(value, len, keyed);
    size_t probe = hash & mask;
    if (slots[probe].value == NULL) {
        return 0;
    }
    do {
        TH_WORK_STEP();
        if (slots[probe].len == len && memcmp(slots[probe].value, value, (size_t)len * sizeof(Py_UCS4)) == 0) {
            int first = !slots[probe].claimed;
            slots[probe].claimed = 1;
            return first;
        }
        probe = (probe + 1) & mask;
    } while (slots[probe].value != NULL);
    return !keyed && ((probe - hash) & mask) > TH_FAST_PROBE_LIMIT ? 2 : 0;
}

/* The element's id attribute, or NULL. */
static inline const th_node_attr *node_id_attr(const struct th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == TH_ATTR_ID) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

/* Collect the elements from node on whose id is in the set and not yet claimed. On FNV-1a it stops at the element
   whose lookup walked past TH_FAST_PROBE_LIMIT slots and returns it, unmatched, for the caller to scan again from under
   th_name_hash. */
static inline struct th_node *id_token_scan(xp_id_token *slots, size_t mask, int keyed, struct th_node *node,
                                            xp_nodeset *found, int *rc) {
    for (; node != NULL; node = document_next(node)) {
        TH_WORK_STEP();
        const th_node_attr *id = node->type == TH_NODE_ELEMENT ? node_id_attr(node) : NULL;
        if (id == NULL) {
            continue;
        }
        int hit = id_token_set_claim(slots, mask, keyed, id->value, id->value_len);
        if (!keyed && hit == 2) {
            return node;
        }
        if (hit && ns_push(found, node, -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            *rc = -1;                              /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    }
    return NULL;
}

/* Seat the tokens again under th_name_hash and claim those of the elements the FNV-1a scan already collected, so the
   scan that goes on from where it stopped selects none of their ids again. */
static TH_NOINLINE void id_token_set_rekey_claimed(const Py_UCS4 *list, Py_ssize_t list_len, xp_id_token *slots,
                                                   size_t mask, const xp_nodeset *found) {
    id_token_set_rekey(list, list_len, slots, mask);
    for (Py_ssize_t index = 0; index < found->len; index++) {
        const th_node_attr *id = node_id_attr(found->items[index].node);
        (void)id_token_set_claim(slots, mask, 1, id->value, id->value_len);
    }
}

/* One element under the indexed root that carries an id, with the value borrowed from its attribute. */
typedef struct {
    struct th_node *node;
    const Py_UCS4 *value;
    Py_ssize_t len;
} xp_id_entry;

/* Every element under one root that carries a non-empty id. A lookup reads its tokens' elements off the table where the
   walk in eval_id visits the whole tree, so one lookup per node, as in //ref[id(@to)], costs time linear rather than
   quadratic in the document. id_version is the tree's when it was built: every edit through the mutation API that can
   change which element an id names bumps it, so the table is rebuilt after such an edit and kept across Python code
   that makes none. */
struct xp_id_index {
    struct th_node *root;
    uint64_t id_version;
    xp_id_entry *entries; /* in document order */
    size_t count;
    size_t *slots; /* index + 1 of the first entry with an id; 0 marks a free slot */
    size_t mask;
};

static void id_index_free(xp_id_index *index) {
    if (index != NULL) {
        PyMem_Free(index->entries);
        PyMem_Free(index->slots);
        PyMem_Free(index);
    }
}

void xp_id_cache_clear(xp_id_cache *cache) {
    id_index_free(cache->index);
    *cache = (xp_id_cache){0};
}

/* The slot naming the first entry whose id is [value, value + len), or the free slot that id would take. The table
   hashes under th_name_hash from the start: the document picks every id in it. */
static size_t *id_index_slot(const xp_id_index *index, const Py_UCS4 *value, Py_ssize_t len) {
    size_t slot = th_name_hash(value, len * (Py_ssize_t)sizeof(Py_UCS4)) & index->mask;
    while (index->slots[slot] != 0) {
        TH_WORK_STEP();
        const xp_id_entry *entry = &index->entries[index->slots[slot] - 1];
        if (entry->len == len && memcmp(entry->value, value, (size_t)len * sizeof(Py_UCS4)) == 0) {
            break;
        }
        slot = (slot + 1) & index->mask;
    }
    return &index->slots[slot];
}

static int id_index_fill(xp_id_index *index) {
    size_t entry_cap = 0;
    for (struct th_node *node = index->root; node != NULL; node = document_next(node)) {
        TH_WORK_STEP();
        const th_node_attr *id = node->type == TH_NODE_ELEMENT ? node_id_attr(node) : NULL;
        if (id == NULL || id->value_len == 0) {
            continue;
        }
        if (index->count == entry_cap) {
            size_t bytes;
            /* GCOVR_EXCL_BR_START: alloc */
            if (!th_grow_cap(index->count + 1, entry_cap, 16, sizeof(xp_id_entry), &entry_cap, &bytes)) {
                return -1; /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            xp_id_entry *grown = PyMem_Realloc(index->entries, bytes);
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                return -1;       /* GCOVR_EXCL_LINE */
            }
            index->entries = grown;
        }
        index->entries[index->count++] = (xp_id_entry){node, id->value, id->value_len};
    }
    size_t capacity;
    size_t bytes;
    /* GCOVR_EXCL_BR_START: alloc */
    if (index->count > SIZE_MAX / 2 || !th_grow_cap(index->count * 2, 0, 8, sizeof(size_t), &capacity, &bytes)) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    index->slots = PyMem_Calloc(1, bytes);
    if (index->slots == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;              /* GCOVR_EXCL_LINE */
    }
    index->mask = capacity - 1;
    /* in document order, so a slot keeps the first element of its id and the later ones stay out of the table */
    for (size_t position = 0; position < index->count; position++) {
        size_t *slot = id_index_slot(index, index->entries[position].value, index->entries[position].len);
        if (*slot == 0) {
            *slot = position + 1;
        }
    }
    return 0;
}

/* Point *out at the index of root, or at NULL for the caller to walk the tree instead. The first lookup under a root
   walks, which is all a lone id() costs; the next one there indexes it, and later ones reuse that index until an edit
   moves the tree's id version. Returns 0, or -1 with MemoryError set. */
static int id_cache_index(struct th_tree *tree, xp_id_cache *cache, struct th_node *root, xp_id_index **out) {
    const uint64_t id_version = th_tree_id_version(tree);
    xp_id_index *index = cache->index;
    if (index != NULL && index->root == root && index->id_version == id_version) {
        *out = index;
        return 0;
    }
    *out = NULL;
    if (cache->walked != root) {
        cache->walked = root;
        return 0;
    }
    xp_id_cache_clear(cache);
    cache->walked = root;
    index = PyMem_Calloc(1, sizeof(*index));
    if (index == NULL) {  /* GCOVR_EXCL_BR_LINE: alloc */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    *index = (xp_id_index){.root = root, .id_version = id_version};
    if (id_index_fill(index) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        id_index_free(index);       /* GCOVR_EXCL_LINE */
        PyErr_NoMemory();           /* GCOVR_EXCL_LINE */
        return -1;                  /* GCOVR_EXCL_LINE */
    }
    cache->index = index;
    *out = index;
    return 0;
}

/* Every lookup in a tree of at most this many nodes walks it: such a walk costs less than building the index, and as
   every node of the evaluation lies in its tree, it bounds each lookup. */
#define XP_ID_INDEX_MIN_NODES 256

static int id_position_order(const void *left, const void *right) {
    const size_t first = *(const size_t *)left;
    const size_t second = *(const size_t *)right;
    return (first > second) - (first < second);
}

/* Append to found, in document order, the indexed element each token names. */
static int id_index_select(const xp_id_index *index, const xp_id_token *slots, size_t mask, xp_nodeset *found) {
    size_t *positions = PyMem_Malloc((mask + 1) * sizeof(size_t));
    if (positions == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyErr_NoMemory();    /* GCOVR_EXCL_LINE */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    size_t count = 0;
    for (size_t token = 0; token <= mask; token++) {
        TH_WORK_STEP();
        if (slots[token].value == NULL) {
            continue;
        }
        size_t entry = *id_index_slot(index, slots[token].value, slots[token].len);
        if (entry != 0) {
            positions[count++] = entry - 1;
        }
    }
    if (count > 1) {
        qsort(positions, count, sizeof(size_t), id_position_order);
    }
    int rc = 0;
    for (size_t position = 0; position < count; position++) {
        if (ns_push(found, index->entries[positions[position]].node, -1) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                                                            /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    }
    PyMem_Free(positions);
    return rc;
}

/* How far a function that gathers the string-value of each member has read its node-set. */
typedef struct {
    xp_caches **caches;
    const xp_nodeset *set;
    Py_ssize_t budget;      /* the steps comparing positions may still walk, as many as a sort of the set may */
    th_node *top;           /* the last member that no earlier member contains */
    th_node *at;            /* how far the walk of top's subtree has come: the last member met, NULL before the first */
    Py_ssize_t last;        /* the index of the last member the walk met */
    Py_ssize_t reach;       /* how far past top's subtree to look for the next member before comparing positions */
    Py_ssize_t settled;     /* once the set is ranked, the index of its first member out of place; 0 before */
    int unordered;          /* a member came before the one ahead of it, so the walk cannot tell what the rest repeat */
    Py_ssize_t repeated;    /* characters the members inside one another have repeated so far */
    Py_ssize_t next;        /* the index of the first member the walk has not read */
    const xp_nodeset *kept; /* the members whose text the caller kept, when it keeps only some */
    Py_ssize_t kept_at;     /* how many of the kept members the walk has read */
} xp_gather;

/* Whether the member at index lies past the subtree of top, which the walk left at end: the place of the next member
   of a set in document order that is not inside top; -1 with MemoryError. It is mostly a few nodes on, so look ahead
   before comparing its position with that of the member before it, since that climbs from both. Looking as far as the
   deepest climb keeps the cost of the two in step where the tree is deep and the members lie far apart. The
   comparisons share the budget a sort of the set has, and once they spend it the set is ranked once, as the sort
   does, which settles the rest. */
static int gather_follows(xp_gather *seen, th_node *end, Py_ssize_t index) {
    if (seen->settled > 0) {
        return index < seen->settled;
    }
    th_node *node = seen->set->items[index].node;
    th_node *ahead = preorder_next(end, NULL);
    for (Py_ssize_t steps = 0; ahead != NULL && ahead != node && steps < seen->reach; steps++) {
        TH_WORK_STEP();
        ahead = preorder_next(ahead, NULL);
    }
    if (ahead == NULL || ahead == node) {
        return ahead == node;
    }
    Py_ssize_t depth = 0;
    for (th_node *parent = node->parent; parent != NULL; parent = parent->parent) {
        TH_WORK_STEP();
        depth++;
    }
    if (depth > seen->reach) {
        seen->reach = depth;
    }
    int order = xp_node_order(seen->caches, seen->set->items[seen->last].node, node, &seen->budget);
    if (order != XP_ORDER_UNKNOWN) {
        return order < 0;
    }
    seen->settled = xp_first_out_of_order(seen->caches, seen->set, seen->last);
    if (seen->settled < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    return index < seen->settled;
}

/* Whether an earlier member's string-value already holds the text of the member at index, as it does for a member
   inside it; -1 with MemoryError. An element's string-value is the text of its whole subtree, so the values of nested
   elements add up to the depth times the text, and the document chooses both: the callers charge what repeats to the
   call's allowance. A node-set is in document order, so the members inside one follow it directly, and one walk of its
   subtree, which reading its string-value makes as well, meets them all. The result of an extension function keeps the
   order the function gave it; from the first member out of place on, every member counts as repeating. */
static int gather_repeats(xp_gather *seen, Py_ssize_t index) {
    if (seen->unordered) {
        return 1;
    }
    xp_item item = seen->set->items[index];
    if (item.attr != -1) {
        return 0; /* no element's string-value holds an attribute or a namespace node */
    }
    th_node *end = seen->at;
    while (seen->at != NULL && seen->at != item.node) {
        TH_WORK_STEP();
        end = seen->at;
        seen->at = preorder_next(seen->at, seen->top);
    }
    if (seen->at != NULL) {
        seen->last = index;
        return item.node->type != TH_NODE_COMMENT; /* nor a comment */
    }
    int follows = end == NULL ? 1 : gather_follows(seen, end, index);
    if (follows < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    if (!follows) {
        seen->unordered = 1;
        return 1;
    }
    seen->top = item.node;
    seen->at = item.node;
    seen->last = index;
    return 0;
}

/* What one call may gather of the text its members repeat: 10 MiB, the ceiling the regex engine puts on its buffers.
   The text that the members of a set do not share is no longer than the document, so a call holds at most that plus
   the allowance. The allowance is per call and not per evaluation: the result is a string or a set of existing nodes,
   never members for another gathering call, so calls cannot multiply one another, and a predicate that gathers a
   little for each of many candidates frees it between them. */
#define XP_GATHER_ALLOWANCE (((Py_ssize_t)10 << 20) / (Py_ssize_t)sizeof(Py_UCS4))

/* Count len characters against the allowance when the member at index of set repeats text; -1 with ValueError past
   it, or with MemoryError. A caller only starts once it holds more than the allowance in all, since members cannot
   repeat more than the call holds, which keeps the walk off every call that gathers less, and from then on calls for
   each member it keeps. The first of those calls passes start, which sets seen up, so the callers' loops neither set
   it up nor test whether it is. The members up to index the walk has not read yet are read here first, each
   repeating one charged its string-value; when kept is not NULL it lists the members whose text the caller kept,
   ending with the one at index, as set:distinct keeps only the first of equal values, and only those are charged. */
static TH_NOINLINE int charge_repeated(xp_gather *seen, xp_ctx *ctx, const xp_nodeset *set, const xp_nodeset *kept,
                                       Py_ssize_t index, Py_ssize_t len, int start) {
    if (start) {
        *seen = (xp_gather){.caches = ctx->caches, .set = set, .budget = xp_order_budget(set->len), .kept = kept};
    }
    struct th_tree *tree = ctx->tree;
    for (; seen->next <= index; seen->next++) {
        xp_item item = seen->set->items[seen->next];
        int counted = seen->kept == NULL || xp_item_equal(seen->kept->items[seen->kept_at], item);
        seen->kept_at += counted; /* unread when kept is NULL */
        int repeats = gather_repeats(seen, seen->next);
        if (repeats < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;     /* GCOVR_EXCL_LINE */
        }
        if (!repeats || !counted) {
            continue;
        }
        Py_ssize_t each = len;
        if (seen->next < index) {
            Py_UCS4 *text = item_string(tree, item, &each);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;      /* GCOVR_EXCL_LINE */
            }
            PyMem_Free(text);
        }
        if (each > XP_GATHER_ALLOWANCE - seen->repeated) {
            PyErr_Format(PyExc_ValueError,
                         "xpath: node-set members inside one another repeat more than %zd characters (10 MiB) of text; "
                         "select nodes that do not contain one another",
                         XP_GATHER_ALLOWANCE);
            return -1;
        }
        seen->repeated += each;
    }
    return 0;
}

/* id(object): for each whitespace-separated token in the argument's string-value (a node-set argument contributes the
   string-value of each member), the first element in document order whose id attribute is that token. Out of line, so
   its lookup loop keeps its registers instead of sharing eval_function's. */
static TH_NOINLINE int eval_id(xp_ctx *ctx, xp_result *arg, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    Py_UCS4 *list = NULL;
    Py_ssize_t list_len = 0;
    size_t capacity = 0;
    if (arg->kind == XP_NODESET) {
        xp_gather seen;
        for (Py_ssize_t index = 0; index < arg->nodes.len; index++) {
            Py_ssize_t each;
            Py_UCS4 *text = item_string(ctx->tree, arg->nodes.items[index], &each);
            if (text == NULL) {   /* GCOVR_EXCL_BR_LINE: alloc */
                PyMem_Free(list); /* GCOVR_EXCL_LINE */
                return -1;        /* GCOVR_EXCL_LINE */
            }
            /* an overflow is past the allowance too, so the common short list checks once; >= counts the separator */
            if (each >= XP_GATHER_ALLOWANCE - list_len) {
                if (charge_repeated(&seen, ctx, &arg->nodes, NULL, index, each, list_len <= XP_GATHER_ALLOWANCE) < 0) {
                    PyMem_Free(text);
                    PyMem_Free(list);
                    return -1;
                }
                const size_t limit = (size_t)PY_SSIZE_T_MAX / sizeof(Py_UCS4);
                if ((size_t)each + 1 > limit - (size_t)list_len) { /* GCOVR_EXCL_BR_LINE: alloc */
                    PyMem_Free(text);                              /* GCOVR_EXCL_LINE */
                    PyMem_Free(list);                              /* GCOVR_EXCL_LINE */
                    return -1;                                     /* GCOVR_EXCL_LINE */
                }
            }
            const size_t needed = (size_t)list_len + (size_t)each + 1;
            if (needed > capacity) {
                size_t bytes;
                /* GCOVR_EXCL_BR_START: alloc */
                if (!th_grow_cap(needed, capacity, 64, sizeof(Py_UCS4), &capacity, &bytes)) {
                    PyMem_Free(text); /* GCOVR_EXCL_LINE */
                    PyMem_Free(list); /* GCOVR_EXCL_LINE */
                    return -1;        /* GCOVR_EXCL_LINE */
                }
                /* GCOVR_EXCL_BR_STOP */
                Py_UCS4 *grown = PyMem_Realloc(list, bytes);
                if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: alloc */
                    PyMem_Free(text); /* GCOVR_EXCL_LINE */
                    PyMem_Free(list); /* GCOVR_EXCL_LINE */
                    return -1;        /* GCOVR_EXCL_LINE */
                }
                list = grown;
            }
            list[list_len++] = ' ';
            memcpy(list + list_len, text, (size_t)each * sizeof(Py_UCS4));
            list_len += each;
            PyMem_Free(text);
        }
    } else {
        list = to_string(ctx->tree, arg, &list_len);
        if (list == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;      /* GCOVR_EXCL_LINE */
        }
    }
    xp_id_token *ids;
    size_t id_mask;
    int keyed;
    if (id_token_set_build(list, list_len, &ids, &id_mask, &keyed) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(list);                                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                                                        /* GCOVR_EXCL_LINE */
    }
    int rc = 0;
    if (ids != NULL) {
        struct th_node *node = tree_root(ctx);
        xp_id_index *index = NULL;
        if (th_tree_node_count(ctx->tree) > XP_ID_INDEX_MIN_NODES) {
            rc = id_cache_index(ctx->tree, ctx->root->ids != NULL ? ctx->root->ids : &ctx->root->own, node, &index);
        }
        if (index != NULL) {
            rc = id_index_select(index, ids, id_mask, &out->nodes);
        } else if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            if (!keyed) {
                node = id_token_scan(ids, id_mask, 0, node, &out->nodes, &rc);
                if (node != NULL) {
                    id_token_set_rekey_claimed(list, list_len, ids, id_mask, &out->nodes);
                }
            }
            id_token_scan(ids, id_mask, 1, node, &out->nodes, &rc);
        }
    }
    PyMem_Free(ids);
    PyMem_Free(list);
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* Unknown flag letters stay ignored, as before this engine. */
static int regex_flags(struct th_tree *tree, xp_result *flags_arg, int *flags, int *global) {
    *flags = 0;
    *global = 0;
    if (flags_arg == NULL) {
        return 0;
    }
    Py_ssize_t len;
    const Py_UCS4 *letters;
    Py_UCS4 *owned;
    if (string_arg(tree, flags_arg, &letters, &len, &owned) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                 /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        switch (letters[index]) {
        case 'g':
            *global = 1;
            break;
        case 'i':
            *flags |= XR_IGNORECASE;
            break;
        case 'm':
            *flags |= XR_MULTILINE;
            break;
        case 's':
            *flags |= XR_DOTALL;
            break;
        case 'x':
            *flags |= XR_VERBOSE;
            break;
        default:
            break;
        }
    }
    PyMem_Free(owned);
    return 0;
}

/* re.error is the class these functions raised before this engine; other failures already carry their exception. */
static int regex_error(xp_ctx *ctx, const xr_error *error) {
    if (!error->pattern_error) {
        return -1;
    }
    if (xp_before_python(ctx) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                   /* GCOVR_EXCL_LINE */
    }
    PyObject *re_module = PyImport_ImportModule("re");
    PyObject *error_type =                                                     /* GCOVR_EXCL_LINE: stdlib import */
        re_module == NULL ? NULL : PyObject_GetAttrString(re_module, "error"); /* GCOVR_EXCL_BR_LINE: stdlib import */
    Py_XDECREF(re_module);
    if (error_type != NULL) { /* GCOVR_EXCL_BR_LINE: stdlib import */
        PyErr_SetString(error_type, error->message);
        Py_DECREF(error_type);
    } /* GCOVR_EXCL_LINE: brace of the never-taken import-failure branch */
    return -1;
}

/* re:test(input, regex, flags?) and matches(input, pattern, flags?). */
static int regex_test(xp_ctx *ctx, xp_result *args, int argc, xp_result *out) {
    int flags;
    int global;
    Py_ssize_t pattern_len;
    Py_ssize_t input_len;
    const Py_UCS4 *pattern;
    Py_UCS4 *owned_pattern;
    int failed = string_arg(ctx->tree, &args[1], &pattern, &pattern_len, &owned_pattern);
    Py_UCS4 *input = to_string(ctx->tree, &args[0], &input_len);
    xr_cache *cache = NULL;
    /* GCOVR_EXCL_BR_START: alloc */
    if (failed < 0 || input == NULL || regex_flags(ctx->tree, argc >= 3 ? &args[2] : NULL, &flags, &global) < 0 ||
        (cache = xp_regex_cache(ctx)) == NULL) {
        PyMem_Free(owned_pattern); /* GCOVR_EXCL_LINE */
        PyMem_Free(input);         /* GCOVR_EXCL_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xr_error error;
    /* An empty computed string can have a null buffer; the regex cache compares pattern buffers with memcmp. */
    const Py_UCS4 empty_pattern = 0;
    int found = xr_test(cache, pattern_len ? pattern : &empty_pattern, pattern_len, flags, input, input_len, &error);
    PyMem_Free(owned_pattern);
    PyMem_Free(input);
    if (found < 0) {
        return regex_error(ctx, &error);
    }
    result_bool(out, found);
    return 0;
}

/* re:replace(input, regex, flags, replacement) rewrites the first match, or every match under 'g', reading re.sub
   template syntax. replace(input, pattern, replacement, flags?) rewrites every match and reads $N references. */
static int regex_replace(xp_ctx *ctx, xp_result *args, int argc, int exslt, xp_result *out) {
    int flags;
    int global;
    Py_ssize_t pattern_len;
    Py_ssize_t input_len;
    Py_ssize_t replacement_len;
    xp_result *flags_arg = exslt ? &args[2] : (argc >= 4 ? &args[3] : NULL);
    Py_UCS4 *pattern = to_string(ctx->tree, &args[1], &pattern_len);
    Py_UCS4 *input = to_string(ctx->tree, &args[0], &input_len);
    Py_UCS4 *replacement = to_string(ctx->tree, &args[exslt ? 3 : 2], &replacement_len);
    xr_cache *cache = NULL;
    /* GCOVR_EXCL_BR_START: alloc */
    if (pattern == NULL || input == NULL || replacement == NULL ||
        regex_flags(ctx->tree, flags_arg, &flags, &global) < 0 || (cache = xp_regex_cache(ctx)) == NULL) {
        PyMem_Free(pattern);     /* GCOVR_EXCL_LINE */
        PyMem_Free(input);       /* GCOVR_EXCL_LINE */
        PyMem_Free(replacement); /* GCOVR_EXCL_LINE */
        return -1;               /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    xr_error error;
    Py_UCS4 *result;
    Py_ssize_t result_len;
    int rc = xr_replace(cache, pattern, pattern_len, flags, input, input_len, replacement, replacement_len, !exslt,
                        exslt && !global, &result, &result_len, &error);
    PyMem_Free(pattern);
    PyMem_Free(input);
    PyMem_Free(replacement);
    if (rc < 0) {
        return regex_error(ctx, &error);
    }
    result_string(out, result, result_len);
    return 0;
}

/* str.lower()/str.upper() map non-ASCII letters too. */
static int case_convert(struct th_tree *tree, xp_result *arg, int to_upper, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, arg, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    PyObject *str = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, text, len);
    PyMem_Free(text);
    if (str == NULL) { /* GCOVR_EXCL_BR_LINE: a UCS-4 alloc cannot be forced */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    PyObject *mapped = PyObject_CallMethod(str, to_upper ? "upper" : "lower", NULL);
    Py_DECREF(str);
    if (mapped == NULL) { /* GCOVR_EXCL_BR_LINE: str.upper/str.lower cannot fail here */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t result_len = PyUnicode_GET_LENGTH(mapped);
    Py_UCS4 *buf = PyUnicode_AsUCS4Copy(mapped);
    Py_DECREF(mapped);
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    result_string(out, buf, result_len);
    return 0;
}

/* Charge `copies` strings of `length` characters that a replace, join or padding adds beyond its inputs. */
static int charge_growth(xp_ctx *ctx, Py_ssize_t length, Py_ssize_t copies) {
    xr_cache *cache = xp_regex_cache(ctx);
    if (cache == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    return xr_charge_growth(cache, length, copies);
}

/* string-join(seq, sep): the string-values of the node-set members joined by the
   separator -- the XPath-1.0-engine reading of fn:string-join, where a node-set is the
   sequence (antchfx does the same). A non-node-set first argument is the lone item, so
   its string-value passes through unseparated. */
static int string_join(xp_ctx *ctx, xp_result *args, xp_result *out) {
    struct th_tree *tree = ctx->tree;
    Py_ssize_t sep_len;
    Py_UCS4 *sep = to_string(tree, &args[1], &sep_len);
    if (sep == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    int is_set = args[0].kind == XP_NODESET;
    Py_ssize_t count = is_set ? args[0].nodes.len : 1;
    /* the first copy is the separator itself, an input already; the copies after it can come from empty members */
    if (count > 2 && sep_len > 0 && charge_growth(ctx, sep_len, count - 2) < 0) {
        PyMem_Free(sep);
        return -1;
    }
    Py_UCS4 **parts = PyMem_Calloc((size_t)count, sizeof(Py_UCS4 *));
    Py_ssize_t *lens = PyMem_Calloc((size_t)count, sizeof(Py_ssize_t));
    if (parts == NULL || lens == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(parts);               /* GCOVR_EXCL_LINE */
        PyMem_Free(lens);                /* GCOVR_EXCL_LINE */
        PyMem_Free(sep);                 /* GCOVR_EXCL_LINE */
        return -1;                       /* GCOVR_EXCL_LINE */
    }
    int rc = 0;
    Py_ssize_t total = count > 0 ? sep_len * (count - 1) : 0;
    Py_ssize_t text_total = 0;
    xp_gather seen;
    for (Py_ssize_t index = 0; index < count; index++) {
        parts[index] = is_set ? item_string(tree, args[0].nodes.items[index], &lens[index])
                              : to_string(tree, &args[0], &lens[index]);
        if (parts[index] == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        total += lens[index];
        text_total += lens[index];
        if (is_set && text_total > XP_GATHER_ALLOWANCE &&
            charge_repeated(&seen, ctx, &args[0].nodes, NULL, index, lens[index],
                            text_total - lens[index] <= XP_GATHER_ALLOWANCE) < 0) {
            rc = -1;
            break;
        }
    }
    if (rc == 0) {
        Py_UCS4 *buf = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;       /* GCOVR_EXCL_LINE */
        } else {           /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            Py_ssize_t write_pos = 0;
            for (Py_ssize_t index = 0; index < count; index++) {
                if (index > 0) {
                    memcpy(buf + write_pos, sep, (size_t)sep_len * sizeof(Py_UCS4));
                    write_pos += sep_len;
                }
                memcpy(buf + write_pos, parts[index], (size_t)lens[index] * sizeof(Py_UCS4));
                write_pos += lens[index];
            }
            result_string(out, buf, total);
        }
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyMem_Free(parts[index]);
    }
    PyMem_Free(parts);
    PyMem_Free(lens);
    PyMem_Free(sep);
    return rc;
}

typedef struct {
    const xp_nodeset *nodes;
    xp_item *slots;
    size_t mask;
} xp_membership;

static size_t membership_slot(const xp_membership *table, xp_item item) {
    size_t slot = xp_item_hash(item) & table->mask;
    while (table->slots[slot].node != NULL && !xp_item_equal(table->slots[slot], item)) {
        slot = (slot + 1) & table->mask;
    }
    return slot;
}

static int membership_build(xp_membership *table, Py_ssize_t probes) {
    if (probes < 16 || table->nodes->len < 16) {
        return 0;
    }
    size_t capacity, bytes;
    const int fits = th_grow_cap((size_t)table->nodes->len * 2, 0, 32, sizeof(xp_item), &capacity, &bytes);
    if (!fits) {   /* GCOVR_EXCL_BR_LINE: allocation size overflow */
        return -1; /* GCOVR_EXCL_LINE: allocation size overflow */
    }
    table->slots = PyMem_Calloc(1, bytes);
    if (table->slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;              /* GCOVR_EXCL_LINE: allocation failure */
    }
    table->mask = capacity - 1;
    for (Py_ssize_t index = 0; index < table->nodes->len; index++) {
        const xp_item item = table->nodes->items[index];
        table->slots[membership_slot(table, item)] = item;
    }
    return 0;
}

static int item_in_nodeset(const xp_membership *table, xp_item probe) {
    if (table->slots != NULL) {
        return table->slots[membership_slot(table, probe)].node != NULL;
    }
    for (Py_ssize_t index = 0; index < table->nodes->len; index++) {
        if (xp_item_equal(table->nodes->items[index], probe)) {
            return 1;
        }
    }
    return 0;
}

/* set:difference / set:intersection: members of the first node-set kept when their
   presence in the second equals `want_present` (0 for difference, 1 for
   intersection). */
static int set_filter(const xp_result *args, int want_present, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *first = &args[0].nodes;
    xp_membership table = {&args[1].nodes, NULL, 0};
    if (membership_build(&table, first->len) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                  /* GCOVR_EXCL_LINE: allocation failure */
    }
    int rc = 0;
    for (Py_ssize_t index = 0; index < first->len; index++) {
        xp_item member = first->items[index];
        if (item_in_nodeset(&table, member) == want_present) {
            if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                rc = -1;                                              /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        }
    }
    PyMem_Free(table.slots);
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* set:has-same-node: true when any member of the first node-set is also a member of
   the second. */
static int set_has_same_node(const xp_result *args, xp_result *out) {
    const xp_nodeset *first = &args[0].nodes;
    xp_membership table = {&args[1].nodes, NULL, 0};
    if (first->len > 0 && item_in_nodeset(&table, first->items[0])) {
        result_bool(out, 1);
        return 0;
    }
    if (membership_build(&table, first->len) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                  /* GCOVR_EXCL_LINE: allocation failure */
    }
    for (Py_ssize_t index = 1; index < first->len; index++) {
        if (item_in_nodeset(&table, first->items[index])) {
            PyMem_Free(table.slots);
            result_bool(out, 1);
            return 0;
        }
    }
    PyMem_Free(table.slots);
    result_bool(out, 0);
    return 0;
}

static int concat_item_view(th_tree *tree, xp_item item, const Py_UCS4 **part, Py_ssize_t *len, Py_UCS4 **owned);

/* The distinct string-values of set:distinct, each kept once in one arena at its narrowest width (1, 2 or 4 bytes a
   code point), found through an open-addressing table. The table is keyed on th_name_hash (core/hash.h) from its first
   fill, since the document picks the values and a fixed hash would let it choose ones that share a home. A value's
   width follows from the bits of its code points, so equal values always share a width and their bytes compare equal.
 */
typedef struct {
    size_t offset;
    Py_ssize_t len;
    uint32_t hash;
    unsigned char width;
} distinct_slot;

typedef struct {
    distinct_slot *slots;
    size_t capacity;
    size_t count;
    unsigned char *arena;
    size_t arena_len;
    size_t arena_cap;
} distinct_set;

/* The bytes per code point of text: 1 when every code point fits in a byte, 2 when each fits in 16 bits, else 4.
   The OR of the code points reaches 0x100 (or 0x10000) exactly when some code point does. */
static unsigned char distinct_width(const Py_UCS4 *text, Py_ssize_t len) {
    Py_UCS4 bits = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        bits |= text[index];
    }
    return bits < 0x100 ? 1 : bits < 0x10000 ? 2 : 4;
}

static void distinct_narrow(unsigned char *dest, const Py_UCS4 *text, Py_ssize_t len, unsigned char width) {
    switch (width) {
    case 1:
        for (Py_ssize_t index = 0; index < len; index++) {
            dest[index] = (unsigned char)text[index];
        }
        break;
    case 2:
        for (Py_ssize_t index = 0; index < len; index++) {
            const uint16_t narrow = (uint16_t)text[index];
            memcpy(dest + 2 * index, &narrow, sizeof(narrow));
        }
        break;
    default:
        memcpy(dest, text, (size_t)len * sizeof(Py_UCS4));
        break;
    }
}

/* Whether the slot holds the value just written at candidate. The hash settles most probes, and the bytes are compared
   only once it agrees: a 32-bit collision between distinct values cannot be forced from a test. */
static int distinct_matches(const distinct_set *set, const distinct_slot *slot, const unsigned char *candidate,
                            uint32_t hash, unsigned char width, Py_ssize_t len) {
    if (slot->hash != hash) {
        return 0;
    }
    /* GCOVR_EXCL_BR_START: a 32-bit collision between distinct values cannot be forced from a test */
    const int same = slot->width == width && slot->len == len &&
                     (len == 0 || memcmp(set->arena + slot->offset, candidate, (size_t)len * width) == 0);
    /* GCOVR_EXCL_BR_STOP */
    return same;
}

/* Double the table and seat every value again under its stored hash. */
static int distinct_grow(distinct_set *set) {
    size_t cap;
    size_t bytes;
    const int grew = th_grow_cap((set->count + 1) * 2, set->capacity, 16, sizeof(distinct_slot), &cap, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: the count cannot overflow */
        return -1; /* GCOVR_EXCL_LINE */
    }
    distinct_slot *slots = PyMem_Calloc(1, bytes);
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    const size_t mask = cap - 1;
    for (size_t index = 0; index < set->capacity; index++) {
        const distinct_slot *entry = &set->slots[index];
        if (entry->width == 0) {
            continue;
        }
        size_t slot = entry->hash & mask;
        while (slots[slot].width != 0) {
            slot = (slot + 1) & mask;
        }
        slots[slot] = *entry;
    }
    PyMem_Free(set->slots);
    set->slots = slots;
    set->capacity = cap;
    return 0;
}

/* 1 when text joins the set, 0 when an equal value is already in it, -1 on allocation failure. The value is written to
   the end of the arena first, hashed there, and left out of the set when an equal value is found. */
static int distinct_add(distinct_set *set, const Py_UCS4 *text, Py_ssize_t len) {
    if ((set->count + 1) * 2 > set->capacity && distinct_grow(set) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                        /* GCOVR_EXCL_LINE: allocation failure */
    }
    const unsigned char width = distinct_width(text, len);
    const size_t bytes = (size_t)len * width;
    if (set->arena_len + bytes > set->arena_cap) {
        size_t cap;
        size_t arena_bytes;
        const int grew = th_grow_cap(set->arena_len + bytes, set->arena_cap, 1024, 1, &cap, &arena_bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: the arena cannot overflow */
            return -1; /* GCOVR_EXCL_LINE */
        }
        unsigned char *arena = PyMem_Realloc(set->arena, arena_bytes);
        if (arena == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            return -1;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        set->arena = arena;
        set->arena_cap = cap;
    }
    const size_t at = set->arena_len;
    if (len > 0) {
        distinct_narrow(set->arena + at, text, len, width);
    }
    const unsigned char *key = len > 0 ? set->arena + at : NULL;
    const uint32_t hash = th_name_hash(key, (Py_ssize_t)bytes);
    const size_t mask = set->capacity - 1;
    size_t slot = hash & mask;
    while (set->slots[slot].width != 0 && !distinct_matches(set, &set->slots[slot], key, hash, width, len)) {
        slot = (slot + 1) & mask;
    }
    if (set->slots[slot].width != 0) {
        return 0;
    }
    set->slots[slot] = (distinct_slot){at, len, hash, width};
    set->arena_len += bytes;
    set->count++;
    return 1;
}

/* set:distinct: the first member, in document order, of every distinct string-value. */
static int set_distinct(xp_ctx *ctx, const xp_result *arg, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *nodes = &arg->nodes;
    if (nodes->len == 0) {
        return 0;
    }
    if (nodes->len == 1) {
        return ns_push(&out->nodes, nodes->items[0].node, nodes->items[0].attr);
    }
    distinct_set seen = {0};
    struct th_tree *tree = ctx->tree;
    int rc = 0;
    xp_gather walk;
    Py_ssize_t spare = XP_GATHER_ALLOWANCE;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t candidate_len;
        Py_UCS4 *owned;
        const Py_UCS4 *candidate;
        /* GCOVR_EXCL_BR_START: allocation failure cannot be forced */
        if (concat_item_view(tree, nodes->items[index], &candidate, &candidate_len, &owned) < 0) {
            rc = -1; /* GCOVR_EXCL_LINE: allocation failure */
            break;   /* GCOVR_EXCL_LINE: allocation failure */
        }
        /* GCOVR_EXCL_BR_STOP */
        const int added = distinct_add(&seen, candidate, candidate_len);
        PyMem_Free(owned);
        if (added < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = -1;     /* GCOVR_EXCL_LINE: allocation failure */
            break;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (added) {
            xp_item member = nodes->items[index];
            if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                rc = -1;                                              /* GCOVR_EXCL_LINE */
                break;                                                /* GCOVR_EXCL_LINE: allocation failure */
            } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            /* the set keeps each distinct value until the end, so those of nested members add up as well */
            spare -= candidate_len;
            if (spare < 0 &&
                charge_repeated(&walk, ctx, nodes, &out->nodes, index, candidate_len, spare + candidate_len >= 0) < 0) {
                rc = -1;
                break;
            }
        }
    }
    PyMem_Free(seen.slots);
    PyMem_Free(seen.arena);
    if (rc < 0) {
        xp_nodeset_free(&out->nodes);
    }
    return rc;
}

/* set:leading / set:trailing: the members of the first node-set that fall before
   (`want_before`) or after the first node of the second in document order. An empty
   second node-set yields the first unchanged; a pivot absent from the first yields
   nothing -- both per the EXSLT contract. */
static int set_split(const xp_result *args, int want_before, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *first = &args[0].nodes;
    const xp_nodeset *second = &args[1].nodes;
    Py_ssize_t lo = 0;
    Py_ssize_t hi = 0;
    if (second->len == 0) {
        lo = 0;
        hi = first->len; /* the whole first node-set */
    } else {
        Py_ssize_t pivot = -1;
        for (Py_ssize_t index = 0; index < first->len; index++) {
            if (xp_item_equal(first->items[index], second->items[0])) {
                pivot = index;
                break;
            }
        }
        if (pivot < 0) {
            return 0; /* the pivot is not in the first node-set */
        }
        lo = want_before ? 0 : pivot + 1;
        hi = want_before ? pivot : first->len;
    }
    int rc = 0;
    for (Py_ssize_t index = lo; index < hi; index++) {
        xp_item member = first->items[index];
        if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                                              /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    }
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* The EXSLT string functions (str:). */

/* The text node ending the single-child chain below node, or NULL when that chain ends in another node. Out of line:
   only nested chains reach it, and inlining it would change the code of the flat case. */
static TH_NOINLINE th_node *concat_chain_text(th_node *node) {
    while (node->type == TH_NODE_ELEMENT && node->first_child != NULL && node->first_child->next_sibling == NULL) {
        node = node->first_child;
    }
    return node->type == TH_NODE_TEXT ? node : NULL;
}

static inline int concat_text_view(th_tree *tree, th_node *node, const Py_UCS4 **part, Py_ssize_t *len) {
    *len = node->text_len;
    *part = *len == 0 ? NULL : th_node_realize_text(tree, node);
    if (*len != 0 && *part == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                    /* GCOVR_EXCL_LINE: allocation failure */
    }
    return 0;
}

static int concat_item_view(th_tree *tree, xp_item item, const Py_UCS4 **part, Py_ssize_t *len, Py_UCS4 **owned) {
    *owned = NULL;
    if (item.attr == -2) {
        *owned = item_string(tree, item, len);
    } else if (xp_is_attribute(item.attr)) {
        const th_node_attr *attr = xp_item_attribute(tree, item);
        *part = attr->value;
        *len = attr->value == NULL ? 0 : attr->value_len;
        return 0;
    } else {
        th_node *node = item.node;
        if (node->type == TH_NODE_ELEMENT && node->first_child != NULL && node->first_child->next_sibling == NULL &&
            node->first_child->type == TH_NODE_TEXT) {
            node = node->first_child;
        }
        if (node->type == TH_NODE_TEXT || node->type == TH_NODE_COMMENT) {
            return concat_text_view(tree, node, part, len);
        }
        th_node *leaf = concat_chain_text(node);
        if (leaf != NULL) {
            return concat_text_view(tree, leaf, part, len);
        }
        *owned = th_node_text(tree, node, len);
    }
    *part = *owned;
    return *owned == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
}

/* str:concat(node-set): the string-values of every member joined in document order. */
static int str_concat(xp_ctx *ctx, const xp_result *arg, xp_result *out) {
    struct th_tree *tree = ctx->tree;
    const xp_nodeset *nodes = &arg->nodes;
    Py_UCS4 *buf = NULL;
    Py_ssize_t total = 0;
    size_t capacity = 0;
    xp_gather seen;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t part_len;
        Py_UCS4 *owned;
        const Py_UCS4 *part;
        if (concat_item_view(tree, nodes->items[index], &part, &part_len, &owned) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
            PyMem_Free(buf);                                                             /* GCOVR_EXCL_LINE */
            return -1;                                                                   /* GCOVR_EXCL_LINE */
        }
        /* an overflow is past the allowance too, so the common short total checks once */
        if (part_len > XP_GATHER_ALLOWANCE - total) {
            if (charge_repeated(&seen, ctx, nodes, NULL, index, part_len, total <= XP_GATHER_ALLOWANCE) < 0) {
                PyMem_Free(owned);
                PyMem_Free(buf);
                return -1;
            }
            const size_t limit = (size_t)PY_SSIZE_T_MAX / sizeof(Py_UCS4);
            if ((size_t)part_len > limit - (size_t)total) { /* GCOVR_EXCL_BR_LINE: alloc */
                PyMem_Free(owned);                          /* GCOVR_EXCL_LINE */
                PyMem_Free(buf);                            /* GCOVR_EXCL_LINE */
                return -1;                                  /* GCOVR_EXCL_LINE */
            }
        }
        const size_t needed = (size_t)total + (size_t)part_len;
        if (needed > capacity) {
            size_t bytes;
            /* GCOVR_EXCL_BR_START: allocation sizes cannot reach the overflow limit */
            if (!th_grow_cap(needed, capacity, 64, sizeof(Py_UCS4), &capacity, &bytes)) {
                PyMem_Free(owned); /* GCOVR_EXCL_LINE */
                PyMem_Free(buf);   /* GCOVR_EXCL_LINE */
                return -1;         /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            Py_UCS4 *grown = PyMem_Realloc(buf, bytes);
            if (grown == NULL) {   /* GCOVR_EXCL_BR_LINE: alloc */
                PyMem_Free(owned); /* GCOVR_EXCL_LINE */
                PyMem_Free(buf);   /* GCOVR_EXCL_LINE */
                return -1;         /* GCOVR_EXCL_LINE */
            }
            buf = grown;
        }
        if (part_len > 0) {
            memcpy(buf + total, part, (size_t)part_len * sizeof(Py_UCS4));
        }
        total += part_len;
        PyMem_Free(owned);
    }
    if (buf == NULL) { /* an empty node-set, or only empty string-values */
        buf = ucs4_dup(NULL, 0);
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;     /* GCOVR_EXCL_LINE */
        }
    }
    result_string(out, buf, total);
    return 0;
}

/* A member's string-value where it already sits, in the width it is stored at: a text or comment, the text a
   single-child chain of elements ends in, or an attribute value. 0 when it has to be built. */
static inline int concat_item_chars(th_tree *tree, xp_item item, const void **chars, int *kind, Py_ssize_t *len) {
    *chars = NULL;
    *kind = PyUnicode_4BYTE_KIND;
    *len = 0;
    if (item.attr == -2) {
        return 0;
    }
    if (xp_is_attribute(item.attr)) {
        const th_node_attr *attr = xp_item_attribute(tree, item);
        *chars = attr->value;
        *len = attr->value == NULL ? 0 : attr->value_len;
        return 1;
    }
    th_node *leaf = item.node;
    if (leaf->type == TH_NODE_ELEMENT) {
        th_node *child = leaf->first_child;
        leaf = child != NULL && child->next_sibling == NULL && child->type == TH_NODE_TEXT ? child
                                                                                           : concat_chain_text(leaf);
        if (leaf == NULL) {
            return 0;
        }
    } else if (leaf->type != TH_NODE_TEXT && leaf->type != TH_NODE_COMMENT) {
        return 0;
    }
    *len = leaf->text_len;
    *chars = *len == 0 ? NULL : th_node_chars(tree, leaf, kind);
    return 1;
}

/* str:concat at the root of an evaluation for Python, when every member's string-value sits in place: the str is
   sized, its width found, and the characters copied in, each pass reading the members where they sit, so the text
   is neither widened to a UCS-4 buffer nor scanned and narrowed back, and source spans stay unrealized. Spans of an
   ASCII input skip the width scan. 1 without touching out when a member's string-value has to be built, which
   str_concat then does. */
static int concat_object(xp_ctx *ctx, const xp_nodeset *nodes, xp_result *out) {
    struct th_tree *tree = ctx->tree;
    const void *chars;
    int kind;
    Py_ssize_t len;
    Py_ssize_t total = 0;
    int wide = 0;
    xp_gather seen;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        if (!concat_item_chars(tree, nodes->items[index], &chars, &kind, &len)) {
            return 1;
        }
        wide |= kind != PyUnicode_1BYTE_KIND && len > 0;
        /* members repeat at most the allowance, so the total stays within the text the tree holds */
        if (len > XP_GATHER_ALLOWANCE - total &&
            charge_repeated(&seen, ctx, nodes, NULL, index, len, total <= XP_GATHER_ALLOWANCE) < 0) {
            return -1;
        }
        total += len;
    }
    Py_UCS4 bits = 0;
    /* only spans are stored a byte a character, all in the tree's input */
    int scan = wide || !th_tree_input_ascii(tree, total);
    for (Py_ssize_t index = 0; scan && index < nodes->len && bits <= 0xFFFF; index++) {
        concat_item_chars(tree, nodes->items[index], &chars, &kind, &len);
        bits |= th_chars_width_bits(chars, kind, len);
    }
    Py_UCS4 maxchar = bits <= 0x7F ? 0x7F : bits <= 0xFF ? 0xFF : bits <= 0xFFFF ? 0xFFFF : 0x10FFFF;
    PyObject *object = PyUnicode_New(total, th_str_maxchar(maxchar));
    if (object == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    int object_kind = PyUnicode_KIND(object);
    char *destination = PyUnicode_DATA(object);
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        concat_item_chars(tree, nodes->items[index], &chars, &kind, &len);
        if (len > 0) {
            th_chars_copy(destination, object_kind, chars, kind, len);
            destination += len * object_kind;
        }
    }
    memset(out, 0, sizeof(*out));
    out->kind = XP_PYSTRING;
    out->object = object;
    return 0;
}

/* str:replace(string, search, replace): every non-overlapping literal occurrence of
   `search` in `string` swapped for `replace`. An empty `search` leaves the input
   untouched (no zero-width match loop). Out of line, so its match loop keeps its own registers: inlined, it shares
   them with all of eval_function and moves a pointer to the stack whenever another function there changes. */
static TH_NOINLINE int str_replace(xp_ctx *ctx, const xp_result *args, xp_result *out) {
    struct th_tree *tree = ctx->tree;
    Py_ssize_t src_len;
    Py_ssize_t search_len;
    Py_ssize_t repl_len;
    Py_UCS4 *src = to_string(tree, &args[0], &src_len);
    Py_UCS4 *search = to_string(tree, &args[1], &search_len);
    Py_UCS4 *repl = to_string(tree, &args[2], &repl_len);
    if (src == NULL || search == NULL || repl == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(src);                                 /* GCOVR_EXCL_LINE */
        PyMem_Free(search);                              /* GCOVR_EXCL_LINE */
        PyMem_Free(repl);                                /* GCOVR_EXCL_LINE */
        return -1;                                       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = 0;
    Py_ssize_t scan = 0;
    Py_ssize_t found;
    while (search_len > 0 && (found = ucs4_find(src + scan, src_len - scan, search, search_len)) >= 0) {
        count++;
        scan += found + search_len;
    }
    if (count == 0) {
        PyMem_Free(search);
        PyMem_Free(repl);
        result_string(out, src, src_len);
        return 0;
    }
    const size_t retained = (size_t)(src_len - count * search_len);
    const size_t limit = (size_t)PY_SSIZE_T_MAX / sizeof(Py_UCS4);
    /* GCOVR_EXCL_BR_START: allocation-size overflow */
    if (retained > limit || (size_t)repl_len > (limit - retained) / (size_t)count) {
        /* GCOVR_EXCL_BR_STOP */
        PyMem_Free(src);    /* GCOVR_EXCL_LINE: allocation size overflow */
        PyMem_Free(search); /* GCOVR_EXCL_LINE: allocation size overflow */
        PyMem_Free(repl);   /* GCOVR_EXCL_LINE: allocation size overflow */
        return -1;          /* GCOVR_EXCL_LINE: allocation size overflow */
    }
    const Py_ssize_t out_len = (Py_ssize_t)(retained + (size_t)count * (size_t)repl_len);
    if (out_len > src_len && charge_growth(ctx, out_len - src_len, 1) < 0) {
        PyMem_Free(src);
        PyMem_Free(search);
        PyMem_Free(repl);
        return -1;
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)(out_len > 0 ? out_len : 1) * sizeof(Py_UCS4));
    if (buf == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(src);    /* GCOVR_EXCL_LINE */
        PyMem_Free(search); /* GCOVR_EXCL_LINE */
        PyMem_Free(repl);   /* GCOVR_EXCL_LINE */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t read = 0;
    Py_ssize_t write = 0;
    while ((found = ucs4_find(src + read, src_len - read, search, search_len)) >= 0) {
        memcpy(buf + write, src + read, (size_t)found * sizeof(Py_UCS4));
        write += found;
        memcpy(buf + write, repl, (size_t)repl_len * sizeof(Py_UCS4));
        write += repl_len;
        read += found + search_len;
    }
    memcpy(buf + write, src + read, (size_t)(src_len - read) * sizeof(Py_UCS4));
    write += src_len - read;
    PyMem_Free(src);
    PyMem_Free(search);
    PyMem_Free(repl);
    result_string(out, buf, write);
    return 0;
}

/* str:padding(length, pattern?): a string of `length` characters built by cycling
   `pattern` (a single space by default). An empty pattern pads with spaces. */
static int str_padding(xp_ctx *ctx, const xp_result *args, int argc, xp_result *out) {
    struct th_tree *tree = ctx->tree;
    double requested = round(to_number(tree, &args[0]));
    /* source data can supply the length, so a tiny document could size a huge allocation; the ceiling is libexslt's
       (strings.c, df878571), raised as an error since truncating would contradict the EXSLT definition */
    const Py_ssize_t ceiling = 100000;
    if (requested > (double)ceiling) {
        char shown[320]; /* %.0f of the largest finite double is 309 digits */
        snprintf(shown, sizeof(shown), "%.0f", requested);
        PyErr_Format(PyExc_ValueError,
                     "xpath: str:padding length %s exceeds the maximum of %zd; pass a length of %zd or fewer", shown,
                     ceiling, ceiling);
        return -1;
    }
    Py_ssize_t target = requested >= 1 ? (Py_ssize_t)requested : 0;
    const Py_UCS4 space = ' ';
    const Py_UCS4 *pattern = &space;
    Py_ssize_t pattern_len = 1;
    Py_UCS4 *pattern_buf = NULL;
    if (argc >= 2) {
        pattern_buf = to_string(tree, &args[1], &pattern_len);
        if (pattern_buf == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;             /* GCOVR_EXCL_LINE */
        }
        pattern = pattern_buf;
    }
    if (target > pattern_len && charge_growth(ctx, target - pattern_len, 1) < 0) {
        PyMem_Free(pattern_buf);
        return -1;
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)target * sizeof(Py_UCS4));
    if (buf == NULL) {           /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(pattern_buf); /* GCOVR_EXCL_LINE */
        return -1;               /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < target; index++) {
        buf[index] = pattern_len > 0 ? pattern[index % pattern_len] : space;
    }
    PyMem_Free(pattern_buf);
    result_string(out, buf, target);
    return 0;
}

/* Whether a UCS-4 run equals an ASCII keyword. */
static int ucs4_eq_ascii(const Py_UCS4 *text, Py_ssize_t len, const char *kw) {
    Py_ssize_t index = 0;
    for (; kw[index] != '\0'; index++) {
        if (index >= len || text[index] != (Py_UCS4)(unsigned char)kw[index]) {
            return 0;
        }
    }
    return index == len;
}

/* str:align(string, width, alignment?): `string` placed within a field as wide as
   the `width` string and filled from it, aligned left (the default), right, or
   center. A string longer than the field is truncated to it. */
static int str_align(struct th_tree *tree, const xp_result *args, int argc, xp_result *out) {
    Py_ssize_t str_len;
    Py_ssize_t pad_len;
    Py_UCS4 *str_text = to_string(tree, &args[0], &str_len);
    Py_UCS4 *pad_text = to_string(tree, &args[1], &pad_len);
    Py_UCS4 *align_text = NULL;
    Py_ssize_t align_len = 0;
    if (argc >= 3) {
        align_text = to_string(tree, &args[2], &align_len);
    }
    if (str_text == NULL || pad_text == NULL || (argc >= 3 && align_text == NULL)) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(str_text);                                                        /* GCOVR_EXCL_LINE */
        PyMem_Free(pad_text);                                                        /* GCOVR_EXCL_LINE */
        PyMem_Free(align_text);                                                      /* GCOVR_EXCL_LINE */
        return -1;                                                                   /* GCOVR_EXCL_LINE */
    }
    int alignment = 0; /* 0 = left, 1 = right, 2 = center */
    if (ucs4_eq_ascii(align_text, align_len, "right")) {
        alignment = 1;
    } else if (ucs4_eq_ascii(align_text, align_len, "center")) {
        alignment = 2;
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)pad_len * sizeof(Py_UCS4));
    if (buf == NULL) {          /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(str_text);   /* GCOVR_EXCL_LINE */
        PyMem_Free(pad_text);   /* GCOVR_EXCL_LINE */
        PyMem_Free(align_text); /* GCOVR_EXCL_LINE */
        return -1;              /* GCOVR_EXCL_LINE */
    }
    if (str_len >= pad_len) {
        const Py_UCS4 *source = alignment == 1 ? str_text + (str_len - pad_len) : str_text;
        memcpy(buf, source, (size_t)pad_len * sizeof(Py_UCS4));
    } else {
        memcpy(buf, pad_text, (size_t)pad_len * sizeof(Py_UCS4));
        Py_ssize_t gap = pad_len - str_len;
        Py_ssize_t offset = alignment == 1 ? gap : alignment == 2 ? gap / 2 : 0;
        memcpy(buf + offset, str_text, (size_t)str_len * sizeof(Py_UCS4));
    }
    PyMem_Free(str_text);
    PyMem_Free(pad_text);
    PyMem_Free(align_text);
    result_string(out, buf, pad_len);
    return 0;
}

/* The EXSLT math functions (math:). */

/* math:min / math:max over a node-set's numeric string-values. An empty node-set or
   any non-numeric member yields NaN, matching EXSLT. */
static int math_extreme(struct th_tree *tree, const xp_result *arg, int want_max, xp_result *out) {
    const xp_nodeset *nodes = &arg->nodes;
    if (nodes->len == 0) {
        result_number(out, (double)NAN);
        return 0;
    }
    double extreme = want_max ? -INFINITY : INFINITY;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t length;
        Py_UCS4 *text = item_string(tree, nodes->items[index], &length);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            return -1;      /* GCOVR_EXCL_LINE */
        }
        double value = parse_number(text, length);
        PyMem_Free(text);
        if (isnan(value)) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
            extreme = (double)NAN;
            break;
        }
        if (want_max ? value > extreme : value < extreme) {
            extreme = value;
        }
    }
    result_number(out, extreme);
    return 0;
}

/* math:highest / math:lowest: the members whose numeric value is the maximum
   (`want_max`) or minimum. Any non-numeric member, or an empty node-set, yields an
   empty node-set. */
static int math_select(struct th_tree *tree, const xp_result *arg, int want_max, xp_result *out) {
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    const xp_nodeset *nodes = &arg->nodes;
    if (nodes->len == 0) {
        return 0;
    }
    double *values = PyMem_Malloc((size_t)nodes->len * sizeof(double));
    if (values == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    double extreme = want_max ? -INFINITY : INFINITY;
    int numeric = 1;
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        Py_ssize_t length;
        Py_UCS4 *text = item_string(tree, nodes->items[index], &length);
        if (text == NULL) {     /* GCOVR_EXCL_BR_LINE: alloc */
            PyMem_Free(values); /* GCOVR_EXCL_LINE */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        values[index] = parse_number(text, length);
        PyMem_Free(text);
        if (isnan(values[index])) { /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
            numeric = 0;
        } else if (want_max ? values[index] > extreme : values[index] < extreme) {
            extreme = values[index];
        }
    }
    int rc = 0;
    if (numeric) {
        for (Py_ssize_t index = 0; index < nodes->len; index++) {
            xp_item member = nodes->items[index];
            if (values[index] == extreme) {
                if (ns_push(&out->nodes, member.node, member.attr) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;                                              /* GCOVR_EXCL_LINE */
                } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            }
        }
    }
    PyMem_Free(values);
    if (rc < 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
        xp_nodeset_free(&out->nodes); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    return rc;
}

/* The EXSLT date functions (date:). */

/* Parse `count` ASCII digits into *value; 0 on a non-digit. */
static int ucs4_digits(const Py_UCS4 *text, Py_ssize_t count, int *value) {
    int accumulator = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        if (text[index] < '0' || text[index] > '9') {
            return 0;
        }
        accumulator = accumulator * 10 + (int)(text[index] - '0');
    }
    *value = accumulator;
    return 1;
}

/* Parse an ISO 8601 "YYYY-MM-DD" calendar date, ignoring any trailing time. 0 when
   the text does not begin with a well-formed, in-range date. */
static int parse_iso_date(const Py_UCS4 *text, Py_ssize_t len, int *year, int *month, int *day) {
    if (len < 10 || text[4] != '-' || text[7] != '-') {
        return 0;
    }
    if (!ucs4_digits(text, 4, year) || !ucs4_digits(text + 5, 2, month) || !ucs4_digits(text + 8, 2, day)) {
        return 0;
    }
    if (*month < 1 || *month > 12 || *day < 1 || *day > 31) {
        return 0;
    }
    return len == 10 || text[10] == 'T' || text[10] == ' ';
}

/* The day of the week for a Gregorian date, 0 = Sunday (Sakamoto's algorithm). */
static int day_of_week(int year, int month, int day) {
    static const int month_offsets[12] = {0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4};
    int adjusted_year = month < 3 ? year - 1 : year;
    return (adjusted_year + adjusted_year / 4 - adjusted_year / 100 + adjusted_year / 400 + month_offsets[month - 1] +
            day) %
           7;
}

/* date:year / date:month-in-year / date:day-in-month / date:day-in-week, selected by
   `field`. A string that is not a valid date yields NaN. */
static int date_number(struct th_tree *tree, const xp_result *arg, int field, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, arg, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    int year;
    int month;
    int day;
    int valid = parse_iso_date(text, len, &year, &month, &day);
    PyMem_Free(text);
    if (!valid) {
        result_number(out, (double)NAN);
        return 0;
    }
    double value = field == 0   ? year
                   : field == 1 ? month
                   : field == 2 ? day
                                : day_of_week(year, month, day) + 1; /* EXSLT counts Sunday as 1 */
    result_number(out, value);
    return 0;
}

/* date:leap-year: true when the date's year is a Gregorian leap year, false for a
   non-leap year or an unparsable date. */
static int date_leap_year(struct th_tree *tree, const xp_result *arg, xp_result *out) {
    Py_ssize_t len;
    Py_UCS4 *text = to_string(tree, arg, &len);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    int year;
    int month;
    int day;
    int valid = parse_iso_date(text, len, &year, &month, &day);
    PyMem_Free(text);
    int leap = valid && ((year % 4 == 0 && year % 100 != 0) || year % 400 == 0);
    result_bool(out, leap);
    return 0;
}

/* Every core and EXSLT function's fixed arity (XPath 1.0 §4): the least and most
   arguments it accepts, with max_args -1 for the one variadic function, concat. A call
   whose count falls outside this range is a static error rejected before the body runs,
   so no branch ever reads an argument the caller did not supply (issue #420). */
typedef struct {
    const char *name;
    int8_t min_args;
    int8_t max_args;
} xp_func_sig;

static const xp_func_sig FUNC_SIGS[] = {
    {"last", 0, 0},
    {"position", 0, 0},
    {"count", 1, 1},
    {"id", 1, 1},
    {"local-name", 0, 1},
    {"namespace-uri", 0, 1},
    {"name", 0, 1},
    {"string", 0, 1},
    {"concat", 2, -1},
    {"starts-with", 2, 2},
    {"contains", 2, 2},
    {"substring-before", 2, 2},
    {"substring-after", 2, 2},
    {"substring", 2, 3},
    {"string-length", 0, 1},
    {"normalize-space", 0, 1},
    {"translate", 3, 3},
    {"ends-with", 2, 2},
    {"string-join", 2, 2},
    {"lower-case", 1, 1},
    {"upper-case", 1, 1},
    {"matches", 2, 3},
    {"replace", 3, 4},
    {"boolean", 1, 1},
    {"not", 1, 1},
    {"true", 0, 0},
    {"false", 0, 0},
    {"lang", 1, 1},
    {"number", 0, 1},
    {"sum", 1, 1},
    {"floor", 1, 1},
    {"ceiling", 1, 1},
    {"round", 1, 1},
    {"re:test", 2, 3},
    {"re:replace", 4, 4},
    {"set:difference", 2, 2},
    {"set:intersection", 2, 2},
    {"set:has-same-node", 2, 2},
    {"set:leading", 2, 2},
    {"set:trailing", 2, 2},
    {"set:distinct", 1, 1},
    {"str:concat", 1, 1},
    {"str:replace", 3, 3},
    {"str:padding", 1, 2},
    {"str:align", 2, 3},
    {"math:min", 1, 1},
    {"math:max", 1, 1},
    {"math:highest", 1, 1},
    {"math:lowest", 1, 1},
    {"math:abs", 1, 1},
    {"math:power", 2, 2},
    {"date:year", 1, 1},
    {"date:month-in-year", 1, 1},
    {"date:day-in-month", 1, 1},
    {"date:day-in-week", 1, 1},
    {"date:leap-year", 1, 1},
};

static const xp_func_sig *func_signature(const xn *fn) {
    for (size_t index = 0; index < sizeof(FUNC_SIGS) / sizeof(FUNC_SIGS[0]); index++) {
        if (func_is(fn, FUNC_SIGS[index].name)) {
            return &FUNC_SIGS[index];
        }
    }
    return NULL;
}

/* Numbering uses a fixed source root. Builtins that read external state must remain uncached. */
int xp_pattern_is_static(const xp_program *prog) {
    for (int32_t index = 0; index < prog->count; index++) {
        const xn *node = &prog->nodes[index];
        if (node->kind == XN_VAR || (node->kind == XN_STEP && node->prefix_len != 0) ||
            (node->kind == XN_FUNC && func_signature(node) == NULL)) {
            return 0;
        }
    }
    return 1;
}

/* Whether the pattern reads a variable or calls a function outside the core set, so its matches can differ between
   two evaluations over one source. A prefixed name test cannot: its namespace is fixed where the pattern is written. */
int xp_pattern_reads_call(const xp_program *prog) {
    for (int32_t index = 0; index < prog->count; index++) {
        const xn *node = &prog->nodes[index];
        if (node->kind == XN_VAR || (node->kind == XN_FUNC && func_signature(node) == NULL)) {
            return 1;
        }
    }
    return 0;
}

/* A fresh Python str of the called function's name, for an error message. */
static PyObject *function_name(const xn *fn) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, fn->str, fn->str_len);
}

/* Report a call to a name that is neither a core function nor a registered extension.
   Returns -1 with a ValueError set, which xpath_raise_status then surfaces unchanged. */
static int raise_unknown_function(const xn *fn) {
    PyObject *name = function_name(fn);
    if (name != NULL) { /* GCOVR_EXCL_BR_LINE: the name allocation cannot be forced to fail */
        PyErr_Format(PyExc_ValueError, "xpath: unknown function '%U'", name);
        Py_DECREF(name);
    }
    return -1;
}

/* Report a call whose argument count is outside the function's fixed arity. */
static int raise_arity(const xn *fn, const xp_func_sig *sig, int argc) {
    PyObject *name = function_name(fn);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: the name allocation cannot be forced to fail */
        return -1;      /* GCOVR_EXCL_LINE */
    }
    if (sig->max_args < 0) {
        PyErr_Format(PyExc_ValueError, "xpath: %U() takes at least %d arguments, got %d", name, sig->min_args, argc);
    } else if (sig->min_args == sig->max_args) {
        PyErr_Format(PyExc_ValueError, "xpath: %U() takes %d argument%s, got %d", name, sig->min_args,
                     sig->min_args == 1 ? "" : "s", argc);
    } else {
        PyErr_Format(PyExc_ValueError, "xpath: %U() takes %d to %d arguments, got %d", name, sig->min_args,
                     sig->max_args, argc);
    }
    Py_DECREF(name);
    return -1;
}

int eval_function(const xp_program *prog, int32_t idx, xp_ctx *ctx, xp_result *out) {
    const xn *fn = &prog->nodes[idx];
    int argc = 0;
    for (int32_t arg_node = fn->first; arg_node >= 0; arg_node = prog->nodes[arg_node].next) {
        argc++;
    }
    const xp_func_sig *sig = func_signature(fn);
    if (sig != NULL && (argc < sig->min_args || (sig->max_args >= 0 && argc > sig->max_args))) {
        return raise_arity(fn, sig, argc);
    }
    if (func_is(fn, "count") && prog->nodes[fn->first].kind == XN_PATH) {
        Py_ssize_t selected;
        int walked = xp_count_walk(prog, fn->first, ctx, &selected);
        if (walked < 0) {
            return walked;
        }
        if (walked > 0) {
            result_number(out, (double)selected);
            return 0;
        }
    }
    xp_result small_args[3];
    xp_result *args = argc == 0 ? NULL : small_args;
    if (argc > 3) {
        args = PyMem_Calloc((size_t)argc, sizeof(xp_result));
        if (args == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
    } else if (argc > 0) {
        memset(small_args, 0, (size_t)argc * sizeof(xp_result));
    }
    xp_live_frame frame;
    if (ctx->live != NULL) {
        frame = (xp_live_frame){.results = args}; /* result_count follows the arguments as they are stored */
        xp_live_enter(ctx, &frame);
    }
    int filled = 0;
    int condition = func_is(fn, "not") || func_is(fn, "boolean"); /* the argument is only read by to_boolean() */
    for (int32_t arg_node = fn->first; arg_node >= 0; arg_node = prog->nodes[arg_node].next) {
        int arg_rc = condition ? eval_held(prog, arg_node, ctx, &args[filled], 1)
                               : eval_held(prog, arg_node, ctx, &args[filled], 0);
        if (arg_rc < 0) {
            xp_live_leave(ctx, &frame);
            for (int cleanup_index = 0; cleanup_index < filled; cleanup_index++) {
                xp_result_free(&args[cleanup_index]);
            }
            if (argc > 3) {
                PyMem_Free(args);
            }
            return arg_rc;
        }
        filled++;
        if (ctx->live != NULL) {
            frame.result_count = filled;
        }
    }
    int rc = 0;
    if (func_is(fn, "true") || func_is(fn, "false")) {
        result_bool(out, func_is(fn, "true"));
    } else if (func_is(fn, "position")) {
        result_number(out, (double)ctx->pos);
    } else if (func_is(fn, "last")) {
        result_number(out, (double)ctx->size);
    } else if (func_is(fn, "not")) {
        result_bool(out, !to_boolean(ctx->tree, &args[0]));
    } else if (func_is(fn, "boolean")) {
        result_bool(out, to_boolean(ctx->tree, &args[0]));
    } else if (func_is(fn, "number")) {
        result_number(out, argc >= 1 ? to_number(ctx->tree, &args[0]) : context_node_number(ctx));
    } else if (func_is(fn, "floor") || func_is(fn, "ceiling") || func_is(fn, "round")) {
        double value = to_number(ctx->tree, &args[0]);
        result_number(out, func_is(fn, "floor")     ? floor(value)
                           : func_is(fn, "ceiling") ? ceil(value)
                                                    : xp_round(value));
    } else if (func_is(fn, "count")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "count() of a non-node-set";
            rc = -4;
        } else {
            result_number(out, (double)args[0].nodes.len);
        }
    } else if (func_is(fn, "sum")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "sum() of a non-node-set";
            rc = -4;
        } else {
            double total = 0;
            for (Py_ssize_t index = 0; index < args[0].nodes.len; index++) {
                Py_ssize_t length;
                Py_UCS4 *text = item_string(ctx->tree, args[0].nodes.items[index], &length);
                if (text == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
                    rc = -1;        /* GCOVR_EXCL_LINE */
                    break;          /* GCOVR_EXCL_LINE */
                }
                total += parse_number(text, length);
                PyMem_Free(text);
            }
            if (rc == 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                result_number(out, total);
            }
        }
    } else if (func_is(fn, "string")) {
        Py_ssize_t length;
        Py_UCS4 *text = arg_or_context_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_string(out, text, length);
        }
    } else if (func_is(fn, "string-length")) {
        Py_ssize_t length;
        Py_UCS4 *text = arg_or_context_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_number(out, (double)length);
            PyMem_Free(text);
        }
    } else if (func_is(fn, "normalize-space")) {
        Py_ssize_t length;
        Py_UCS4 *text = arg_or_context_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : normalize_space(text, length, out); /* GCOVR_EXCL_BR_LINE: alloc */
        PyMem_Free(text);
    } else if (func_is(fn, "local-name") || func_is(fn, "name")) {
        Py_ssize_t length;
        Py_UCS4 *text = node_name_string(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_string(out, text, length);
        }
    } else if (func_is(fn, "namespace-uri")) {
        Py_ssize_t length;
        Py_UCS4 *text = node_namespace_uri(ctx, args, argc, &length);
        rc = text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {              /* GCOVR_EXCL_BR_LINE: alloc */
            result_string(out, text, length);
        }
    } else if (func_is(fn, "lang")) {
        int matched = node_lang(ctx, &args[0]);
        rc = matched < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
        if (rc == 0) {             /* GCOVR_EXCL_BR_LINE: alloc */
            result_bool(out, matched);
        }
    } else if (func_is(fn, "id")) {
        rc = eval_id(ctx, &args[0], out);
    } else if (func_is(fn, "re:test") || func_is(fn, "matches")) {
        rc = regex_test(ctx, args, argc, out);
    } else if (func_is(fn, "re:replace")) {
        rc = regex_replace(ctx, args, argc, 1, out);
    } else if (func_is(fn, "set:difference") || func_is(fn, "set:intersection") || func_is(fn, "set:has-same-node") ||
               func_is(fn, "set:leading") || func_is(fn, "set:trailing")) {
        if (args[0].kind != XP_NODESET || args[1].kind != XP_NODESET) {
            *ctx->feature = "a set: function on a non-node-set";
            rc = -4;
        } else if (func_is(fn, "set:difference")) {
            rc = set_filter(args, 0, out);
        } else if (func_is(fn, "set:intersection")) {
            rc = set_filter(args, 1, out);
        } else if (func_is(fn, "set:has-same-node")) {
            rc = set_has_same_node(args, out);
        } else {
            rc = set_split(args, func_is(fn, "set:leading"), out);
        }
    } else if (func_is(fn, "set:distinct")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "set:distinct on a non-node-set";
            rc = -4;
        } else if ((rc = xp_before_python(ctx)) == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = set_distinct(ctx, &args[0], out);
        }
    } else if (func_is(fn, "str:concat")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "str:concat on a non-node-set";
            rc = -4;
        } else {
            rc = ctx->root->want_object && idx == prog->root ? concat_object(ctx, &args[0].nodes, out) : 1;
            if (rc > 0) {
                rc = str_concat(ctx, &args[0], out);
            }
        }
    } else if (func_is(fn, "str:replace")) {
        rc = str_replace(ctx, args, out);
    } else if (func_is(fn, "str:padding")) {
        rc = str_padding(ctx, args, argc, out);
    } else if (func_is(fn, "str:align")) {
        rc = str_align(ctx->tree, args, argc, out);
    } else if (func_is(fn, "math:min") || func_is(fn, "math:max") || func_is(fn, "math:highest") ||
               func_is(fn, "math:lowest")) {
        if (args[0].kind != XP_NODESET) {
            *ctx->feature = "a math: function on a non-node-set";
            rc = -4;
        } else if (func_is(fn, "math:min") || func_is(fn, "math:max")) {
            rc = math_extreme(ctx->tree, &args[0], func_is(fn, "math:max"), out);
        } else {
            rc = math_select(ctx->tree, &args[0], func_is(fn, "math:highest"), out);
        }
    } else if (func_is(fn, "math:abs")) {
        result_number(out, fabs(to_number(ctx->tree, &args[0])));
    } else if (func_is(fn, "math:power")) {
        result_number(out, pow(to_number(ctx->tree, &args[0]), to_number(ctx->tree, &args[1])));
    } else if (func_is(fn, "date:year")) {
        rc = date_number(ctx->tree, &args[0], 0, out);
    } else if (func_is(fn, "date:month-in-year")) {
        rc = date_number(ctx->tree, &args[0], 1, out);
    } else if (func_is(fn, "date:day-in-month")) {
        rc = date_number(ctx->tree, &args[0], 2, out);
    } else if (func_is(fn, "date:day-in-week")) {
        rc = date_number(ctx->tree, &args[0], 3, out);
    } else if (func_is(fn, "date:leap-year")) {
        rc = date_leap_year(ctx->tree, &args[0], out);
    } else if (func_is(fn, "concat")) {
        rc = concat(ctx->tree, args, argc, out);
    } else if (func_is(fn, "starts-with") || func_is(fn, "contains")) {
        Py_ssize_t hl;
        Py_ssize_t nl;
        Py_UCS4 *hay = to_string(ctx->tree, &args[0], &hl);
        Py_UCS4 *needle = to_string(ctx->tree, &args[1], &nl);
        if (hay == NULL || needle == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                         /* GCOVR_EXCL_LINE */
        } else if (func_is(fn, "starts-with")) {
            result_bool(out, nl <= hl && memcmp(hay, needle, (size_t)nl * sizeof(Py_UCS4)) == 0);
        } else {
            result_bool(out, ucs4_find(hay, hl, needle, nl) >= 0);
        }
        PyMem_Free(hay);
        PyMem_Free(needle);
    } else if (func_is(fn, "substring-before") || func_is(fn, "substring-after")) {
        Py_ssize_t hl;
        Py_ssize_t nl;
        Py_UCS4 *hay = to_string(ctx->tree, &args[0], &hl);
        Py_UCS4 *needle = to_string(ctx->tree, &args[1], &nl);
        if (hay == NULL || needle == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                         /* GCOVR_EXCL_LINE */
        } else {                             /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            Py_ssize_t at = ucs4_find(hay, hl, needle, nl);
            const Py_UCS4 *start = at < 0 ? hay : func_is(fn, "substring-before") ? hay : hay + at + nl;
            Py_ssize_t rlen = at < 0 ? 0 : func_is(fn, "substring-before") ? at : hl - (at + nl);
            Py_UCS4 *result_text = ucs4_dup(start, rlen);
            rc = result_text == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: alloc */
            if (rc == 0) {                     /* GCOVR_EXCL_BR_LINE: alloc */
                result_string(out, result_text, rlen);
            }
        }
        PyMem_Free(hay);
        PyMem_Free(needle);
    } else if (func_is(fn, "substring")) {
        rc = substring(ctx->tree, args, argc, out);
    } else if (func_is(fn, "translate")) {
        Py_ssize_t sl;
        Py_ssize_t fl;
        Py_ssize_t tl;
        const Py_UCS4 *text;
        const Py_UCS4 *from;
        const Py_UCS4 *to;
        Py_UCS4 *text_owned;
        Py_UCS4 *from_owned;
        Py_UCS4 *to_owned;
        int text_rc = string_arg(ctx->tree, &args[0], &text, &sl, &text_owned);
        int from_rc = string_arg(ctx->tree, &args[1], &from, &fl, &from_owned);
        int to_rc = string_arg(ctx->tree, &args[2], &to, &tl, &to_owned);
        if (text_rc < 0 || from_rc < 0 || to_rc < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
            rc = -1;                                   /* GCOVR_EXCL_LINE */
        } else { /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            rc = translate(text, sl, from, fl, to, tl, out);
        }
        PyMem_Free(text_owned);
        PyMem_Free(from_owned);
        PyMem_Free(to_owned);
    } else if (func_is(fn, "ends-with")) {
        Py_ssize_t hl;
        Py_ssize_t nl;
        Py_UCS4 *hay = to_string(ctx->tree, &args[0], &hl);
        Py_UCS4 *needle = to_string(ctx->tree, &args[1], &nl);
        if (hay == NULL || needle == NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
            rc = -1;                         /* GCOVR_EXCL_LINE */
        } else {                             /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            result_bool(out, nl <= hl && memcmp(hay + hl - nl, needle, (size_t)nl * sizeof(Py_UCS4)) == 0);
        }
        PyMem_Free(hay);
        PyMem_Free(needle);
    } else if (func_is(fn, "string-join")) {
        rc = string_join(ctx, args, out);
    } else if (func_is(fn, "lower-case") || func_is(fn, "upper-case")) {
        if ((rc = xp_before_python(ctx)) == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = case_convert(ctx->tree, &args[0], func_is(fn, "upper-case"), out);
        }
    } else if (func_is(fn, "replace")) {
        rc = regex_replace(ctx, args, argc, 0, out);
    } else if (ctx->extension != NULL) {
        ctx->root->mixed = 1; /* the result can lie in any subtree, and Python can move this tree's own nodes */
        if ((rc = xp_before_python(ctx)) == 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rc = ctx->extension(ctx->extension_ctx, (xp_item){ctx->node, ctx->attr}, fn->str, fn->str_len, args, argc,
                                out);
        }
        if (rc == -2) {
            rc = raise_unknown_function(fn);
        }
    } else {
        rc = raise_unknown_function(fn);
    }
    xp_live_leave(ctx, &frame);
    for (int cleanup_index = 0; cleanup_index < argc; cleanup_index++) {
        xp_result_free(&args[cleanup_index]);
    }
    if (argc > 3) {
        PyMem_Free(args);
    }
    return rc;
}

PyObject *turbohtml_xpath_parse(PyObject *module, PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "xpath expression must be a str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    Py_UCS4 *src = PyUnicode_AsUCS4Copy(arg);
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    char err[128];
    module_state *state = PyModule_GetState(module);
    xp_program *prog = xp_compile(src, len, err, sizeof(err), state->xpath_depth_error);
    PyMem_Free(src);
    if (prog == NULL) {
        if (!PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: allocation error */
            PyErr_SetString(PyExc_ValueError, err);
        }
        return NULL;
    }
    Py_ssize_t dlen;
    Py_UCS4 *dump = xp_dump(prog, &dlen);
    xp_free(prog);
    if (dump == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, dump, dlen);
    PyMem_Free(dump);
    return result;
}
