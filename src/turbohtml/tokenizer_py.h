/* Internal glue between the state machine and the Python types.

   token_type.c defines the Token type and the TokenType enum; tokenizer_type.c
   defines the Tokenizer type, its token iterator, and the tokenize() helper;
   _htmlmodule.c creates the module and calls the register functions. All three
   share the per-module state declared here, which owns the heap types so the
   module stays compatible with sub-interpreters and the free-threaded build. */

#ifndef TURBOHTML_TOKENIZER_PY_H
#define TURBOHTML_TOKENIZER_PY_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "tokenizer_sm.h"

typedef struct {
    PyObject *token_type;     /* Token */
    PyObject *tokenizer_type; /* Tokenizer */
    PyObject *iter_type;      /* the iterator returned by feed()/close()/tokenize() */
    PyObject *kind_enum;      /* TokenType (enum.IntEnum) */
    PyObject *kinds[5];       /* cached TokenType members, indexed by enum th_kind */
} module_state;

/* Register the types and enum into module/state. Each returns 0 or -1. */
int token_register(PyObject *module, module_state *state);
int tokenizer_register(PyObject *module, module_state *state);

/* Build a Token from a freshly emitted record. Small records are copied and a
   large text run is moved out of the record (which then regrows). A slice
   record resolves lazily against source when the input is borrowed from it
   (the Token keeps source alive), and immediately against sm's own storage
   otherwise (a later feed may move it). */
PyObject *token_from_record(module_state *state, const th_tokenizer *sm, PyObject *source, th_token *record);

#endif /* TURBOHTML_TOKENIZER_PY_H */
