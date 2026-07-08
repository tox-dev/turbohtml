/* The JavaScript-minifier engine depends on CPython only for two things: the
   Py_UCS4 / Py_ssize_t typedefs and the PyMem allocator. Routing both through this
   header lets the engine (lexer, ast, parser, printer, minify) compile two ways:
   inside the extension module against CPython, or - with JM_STANDALONE defined -
   as plain C against the system allocator, so a sanitizer harness can fuzz the whole
   corpus through it with AddressSanitizer / LeakSanitizer and no Python runtime. The
   Python binding (lexdump.c) always builds the CPython way. */

#ifndef TURBOHTML_SERIALIZE_JS_TYPES_H
#define TURBOHTML_SERIALIZE_JS_TYPES_H

#include "core/vec.h"

#ifdef JM_STANDALONE

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

typedef uint32_t Py_UCS4;
typedef ptrdiff_t Py_ssize_t;

#define jm_malloc(size) malloc(size)
#define jm_realloc(ptr, size) realloc((ptr), (size))
#define jm_free(ptr) free(ptr)
#define jm_calloc(count, size) calloc((count), (size))

#else

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>

#define jm_malloc(size) PyMem_Malloc(size)
#define jm_realloc(ptr, size) PyMem_Realloc((ptr), (size))
#define jm_free(ptr) PyMem_Free(ptr)
#define jm_calloc(count, size) PyMem_Calloc((count), (size))

#endif

#endif /* TURBOHTML_SERIALIZE_JS_TYPES_H */
