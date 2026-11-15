/* Python's re dialect on a Pike VM, so an untrusted pattern matches in time linear in the input; back-references run on
   a backtracking matcher. Both matchers' steps draw on a budget shared by the whole XPath evaluation. */

#ifndef TURBOHTML_XPATH_REGEX_H
#define TURBOHTML_XPATH_REGEX_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

enum {
    XR_IGNORECASE = 1,
    XR_MULTILINE = 2,
    XR_DOTALL = 4,
    XR_VERBOSE = 8,
};

/* pattern_error 1 reports a malformed pattern or replacement in message, which the caller raises as re.error so
   existing handlers keep catching it; 0 leaves a Python exception set. */
typedef struct {
    int pattern_error;
    char message[160];
} xr_error;

#define XR_CACHE_SLOTS 64

typedef struct xr_program xr_program;

/* Compiled patterns, the step budget and the string growth of one XPath evaluation, zeroed before its first use. */
typedef struct xr_cache {
    xr_program *programs[XR_CACHE_SLOTS];
    xr_program *recent;
    size_t rest;          /* bytes the cached programs other than recent hold */
    Py_ssize_t allowance; /* matching steps granted so far in the evaluation */
    Py_ssize_t budget;    /* the part of the allowance still unspent */
    Py_ssize_t grown;     /* characters replacing, joining and padding have added to their inputs */
} xr_cache;

/* Free the compiled patterns; the cache's own storage belongs to the caller. */
void xr_cache_clear(xr_cache *cache);

/* Charge `copies` strings of `length` characters that a replace, join or padding adds beyond its inputs against the
   evaluation's allowance. 0 when they fit, -1 with ValueError set when they do not. */
int xr_charge_growth(xr_cache *cache, Py_ssize_t length, Py_ssize_t copies);

/* 1 when pattern matches somewhere in text, 0 when not, -1 on error. */
int xr_test(xr_cache *cache, const Py_UCS4 *pattern, Py_ssize_t pattern_len, int flags, const Py_UCS4 *text,
            Py_ssize_t text_len, xr_error *error);

/* Substitute the matches of pattern (the first only when first_only) into a PyMem buffer in *out, reading re.sub
   template syntax, or fn:replace $N references when xpath_syntax is set. 0 on success, -1 on error. */
int xr_replace(xr_cache *cache, const Py_UCS4 *pattern, Py_ssize_t pattern_len, int flags, const Py_UCS4 *text,
               Py_ssize_t text_len, const Py_UCS4 *replacement, Py_ssize_t replacement_len, int xpath_syntax,
               int first_only, Py_UCS4 **out, Py_ssize_t *out_len, xr_error *error);

#endif /* TURBOHTML_XPATH_REGEX_H */
