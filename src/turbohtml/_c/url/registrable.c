/* Registrable-domain (eTLD+1) computation behind extract_links(external_only=True)'s site boundary, the C engine the
   thin _urls.py shim calls to decide whether two links share a site.

   Two hosts are the same site when they share a registrable domain: the public suffix plus the one label to its left.
   The public suffix is read right-to-left from two shipped tables -- the single-label IANA TLDs in tld_table.h (reused
   from linkify) and the multi-label Public Suffix List rules in psl_table.h -- following the PSL matching rules: an
   exception rule (!www.ck) narrows the wildcard (*.ck) it sits under, and the longest match wins. A host with no known
   suffix (an IP literal, a made-up TLD) is its own registrable domain, so two such hosts compare by their full host,
   the way courlan's tld-backed is_external falls back. */

#include "core/common.h"

#include "data/psl_table.h"
#include "data/tld_table.h"

#include <string.h>

enum { PSL_NORMAL = 0, PSL_WILDCARD = 1, PSL_EXCEPTION = 2 };

/* Is the single label [cand, cand+len) an IANA TLD? Bucketed on its first byte in tld_table.h, matched
   case-insensitively; a digit-led label (an IPv4 octet) buckets into an empty range and falls through to 0. */
static int is_iana_tld(const Py_UCS4 *cand, Py_ssize_t len) {
    Py_UCS4 first = cand[0];
    /* a non-ASCII first code point starts no IANA TLD (all ASCII / punycode) and would index past th_tld_first[257] */
    if (first > 255) {
        return 0;
    }
    for (int index = th_tld_first[first]; index < th_tld_first[first + 1]; index++) {
        if (th_tld_table[index].name_len != len) {
            continue;
        }
        int matched = 1;
        for (Py_ssize_t offset = 0; offset < len; offset++) {
            if ((Py_UCS4)(unsigned char)th_tld_table[index].name[offset] != cand[offset]) {
                matched = 0;
                break;
            }
        }
        if (matched) {
            return 1;
        }
    }
    return 0;
}

/* The kind of the PSL rule whose text equals [cand, cand+len) exactly (case-insensitive), or -1 for no match. A
   wildcard rule is stored with its "*." prefix, so a candidate carrying that prefix is how a wildcard is looked up. */
static int psl_kind(const Py_UCS4 *cand, Py_ssize_t len) {
    Py_UCS4 first = cand[0];
    /* a non-ASCII first code point matches no PSL rule (all ASCII / punycode) and would index past th_psl_first[257] */
    if (first > 255) {
        return -1;
    }
    for (int index = th_psl_first[first]; index < th_psl_first[first + 1]; index++) {
        if (th_psl_table[index].name_len != len) {
            continue;
        }
        int matched = 1;
        for (Py_ssize_t offset = 0; offset < len; offset++) {
            if ((Py_UCS4)(unsigned char)th_psl_table[index].name[offset] != cand[offset]) {
                matched = 0;
                break;
            }
        }
        if (matched) {
            return th_psl_table[index].kind;
        }
    }
    return -1;
}

/* Does a wildcard rule cover the k-label candidate? Its pattern is "*." followed by the candidate minus its leftmost
   label, i.e. the host from `tail`; build that string once and match it as a wildcard rule. */
static int psl_wildcard_covers(Py_UCS4 *scratch, const Py_UCS4 *tail, Py_ssize_t tail_len) {
    scratch[0] = '*';
    scratch[1] = '.';
    memcpy(scratch + 2, tail, (size_t)tail_len * sizeof(Py_UCS4));
    return psl_kind(scratch, tail_len + 2) == PSL_WILDCARD;
}

/* The number of trailing labels that form the public suffix of the host described by `label_start`/`count`, over the
   lowered code points in `host`. 0 means no known suffix (an IP or an unregistered TLD). */
static Py_ssize_t public_suffix_labels(const Py_UCS4 *host, Py_ssize_t host_len, const Py_ssize_t *label_start,
                                       Py_ssize_t count, Py_UCS4 *scratch) {
    /* take is the trailing-label count, walked shortest-first, so the last matching rule of each kind is the longest;
       a plain assignment is enough to keep the longest without a max() guard whose shorter arm is then unreachable. */
    Py_ssize_t best_normal = 0;
    Py_ssize_t best_exception = 0;
    for (Py_ssize_t take = 1; take <= count; take++) {
        Py_ssize_t start = label_start[count - take];
        if (take == 1) {
            if (is_iana_tld(host + start, host_len - start)) {
                best_normal = 1;
            }
            continue;
        }
        int kind = psl_kind(host + start, host_len - start);
        if (kind == PSL_EXCEPTION) {
            best_exception = take;
        } else if (kind == PSL_NORMAL) {
            best_normal = take;
        }
        Py_ssize_t tail = label_start[count - take + 1];
        if (psl_wildcard_covers(scratch, host + tail, host_len - tail)) {
            best_normal = take;
        }
    }
    if (best_exception > 0) {
        return best_exception - 1;
    }
    return best_normal;
}

/* _registrable_domain(host) -> str: the eTLD+1 of a lowercased ASCII host, or the whole host when it carries no known
   public suffix (an IP literal, an unregistered TLD) or is itself a public suffix. Empty in, empty out. */
PyObject *turbohtml_registrable_domain(PyObject *Py_UNUSED(module), PyObject *arg) {
    Py_ssize_t host_len = PyUnicode_GET_LENGTH(arg);
    if (host_len == 0) {
        return PyUnicode_FromString("");
    }
    Py_UCS4 *host = PyMem_Malloc((size_t)host_len * sizeof(Py_UCS4));
    Py_ssize_t *label_start = PyMem_Malloc((size_t)(host_len + 1) * sizeof(Py_ssize_t));
    Py_UCS4 *scratch = PyMem_Malloc((size_t)(host_len + 2) * sizeof(Py_UCS4));
    if (host == NULL || label_start == NULL || scratch == NULL) { /* GCOVR_EXCL_BR_LINE: alloc failure unforceable */
        PyMem_Free(host);                                         /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(label_start);                                  /* GCOVR_EXCL_LINE */
        PyMem_Free(scratch);                                      /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory();                                  /* GCOVR_EXCL_LINE */
    }
    /* the shim lowercases the host (urlsplit's hostname and _ascii_host both do), so the table's lowercase entries
       compare directly; the only per-code-point work here is finding the label boundaries */
    Py_ssize_t count = 1;
    label_start[0] = 0;
    for (Py_ssize_t index = 0; index < host_len; index++) {
        Py_UCS4 code = PyUnicode_READ_CHAR(arg, index);
        host[index] = code;
        if (code == '.') {
            label_start[count++] = index + 1;
        }
    }
    Py_ssize_t suffix = public_suffix_labels(host, host_len, label_start, count, scratch);
    Py_ssize_t keep = suffix > 0 && count > suffix ? suffix + 1 : count;
    Py_ssize_t start = label_start[count - keep];
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, host + start, host_len - start);
    PyMem_Free(host);
    PyMem_Free(label_start);
    PyMem_Free(scratch);
    return result;
}
