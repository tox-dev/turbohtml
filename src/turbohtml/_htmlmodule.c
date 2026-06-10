/* turbohtml._html - module definition wiring the per-feature implementations.

   escape()/unescape() are stateless string functions (escape.c, unescape.c).
   The tokenizer adds the Token and Tokenizer types plus tokenize(); those heap
   types live in per-module state so the module supports sub-interpreters and
   the free-threaded build. Only public, version-portable APIs are used, so the
   same sources build on CPython 3.10 through 3.15. */

#include "turbohtml.h"

#include "tokenizer_py.h"

PyDoc_STRVAR(escape_doc, "escape(s, quote=True)\n--\n\n"
                         "Replace special characters \"&\", \"<\" and \">\" with HTML-safe sequences.\n\n"
                         "If the optional flag quote is true (the default), the quotation mark\n"
                         "characters, both double quote (\") and single quote ('), are also translated.");

PyDoc_STRVAR(unescape_doc, "unescape(s, /)\n--\n\n"
                           "Convert all named and numeric character references in s to the\n"
                           "corresponding Unicode characters, following the HTML5 rules.");

PyDoc_STRVAR(tokenize_doc, "tokenize(s, /)\n--\n\n"
                           "Tokenize a whole HTML string, returning an iterator of Token objects\n"
                           "following the WHATWG tokenization algorithm.");

static PyMethodDef html_methods[] = {
    {"escape", (PyCFunction)(void (*)(void))turbohtml_escape, METH_VARARGS | METH_KEYWORDS, escape_doc},
    {"unescape", turbohtml_unescape, METH_O, unescape_doc},
    {"tokenize", turbohtml_tokenize, METH_O, tokenize_doc},
    {"_tokenize_states", turbohtml_tokenize_states, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL},
};

static int html_exec(PyObject *module) {
    module_state *state = PyModule_GetState(module);
    if (token_register(module, state) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                           /* GCOVR_EXCL_LINE */
    }
    return tokenizer_register(module, state);
}

static int html_traverse(PyObject *module, visitproc visit, void *arg) {
    module_state *state = PyModule_GetState(module);
    Py_VISIT(state->token_type); /* GCOVR_EXCL_BR_LINE: Py_VISIT's NULL arm is dead, module state is populated for the
                                    module's lifetime */
    Py_VISIT(state->tokenizer_type); /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->iter_type);      /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->kind_enum);      /* GCOVR_EXCL_BR_LINE: same */
    for (int i = 0; i < 5; i++) {
        Py_VISIT(state->kinds[i]); /* GCOVR_EXCL_BR_LINE: same */
    }
    return 0;
}

static int html_clear(PyObject *module) {
    module_state *state = PyModule_GetState(module);
    Py_CLEAR(state->token_type);
    Py_CLEAR(state->tokenizer_type);
    Py_CLEAR(state->iter_type);
    Py_CLEAR(state->kind_enum);
    for (int i = 0; i < 5; i++) {
        Py_CLEAR(state->kinds[i]);
    }
    return 0;
}

static void html_free(void *module) {
    (void)html_clear((PyObject *)module);
}

static PyModuleDef_Slot html_slots[] = {
    {Py_mod_exec, html_exec},
#if PY_VERSION_HEX >= 0x030C0000
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
#endif
#if PY_VERSION_HEX >= 0x030D0000
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, NULL},
};

static struct PyModuleDef htmlmodule = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_html",
    .m_doc = "C accelerator for turbohtml escaping, unescaping, and tokenizing.",
    .m_size = sizeof(module_state),
    .m_methods = html_methods,
    .m_slots = html_slots,
    .m_traverse = html_traverse,
    .m_clear = html_clear,
    .m_free = html_free,
};

PyMODINIT_FUNC PyInit__html(void) {
    return PyModuleDef_Init(&htmlmodule);
}
