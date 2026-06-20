/* turbohtml._html - module definition wiring the per-feature implementations.

   escape()/unescape() are stateless string functions (escape.c, unescape.c).
   The tokenizer adds the Token and Tokenizer types plus tokenize(); those heap
   types live in per-module state so the module supports sub-interpreters and
   the free-threaded build. These sources use only public, version-portable
   APIs, so they build on CPython 3.10 through 3.15. */

#include "turbohtml.h"

#include "tokenizer_py.h"

PyDoc_STRVAR(escape_doc, "escape(s, quote=True)\n--\n\n"
                         "Replace special characters \"&\", \"<\" and \">\" with HTML-safe sequences.\n\n"
                         "If the optional flag quote is true (the default), the quotation mark\n"
                         "characters, both double quote (\") and single quote ('), are also translated.");

PyDoc_STRVAR(unescape_doc, "unescape(s, /)\n--\n\n"
                           "Convert all named and numeric character references in s to the\n"
                           "corresponding Unicode characters, following the HTML5 rules.");

PyDoc_STRVAR(markup_escape_doc, "escape(s, /)\n--\n\n"
                                "Replace &, <, >, ', and \" with HTML-safe sequences and return a Markup.\n\n"
                                "An object with an __html__ method is trusted as already safe; any other\n"
                                "object is converted to a string first. Matches markupsafe.escape, including\n"
                                "the numeric &#34; and &#39; quote references.");

PyDoc_STRVAR(markup_escape_silent_doc, "escape_silent(s, /)\n--\n\n"
                                       "Like escape, but None becomes the empty Markup rather than 'None'.");

PyDoc_STRVAR(markup_soft_str_doc, "soft_str(s, /)\n--\n\n"
                                  "Convert s to str only if it is not already one, preserving a Markup so\n"
                                  "already-safe text is not escaped a second time.");

PyDoc_STRVAR(tokenize_doc, "tokenize(s, /, *, resolve_references=True, capture_source=False)\n--\n\n"
                           "Tokenize a whole HTML string, returning an iterator of Token objects\n"
                           "following the WHATWG tokenization algorithm. With resolve_references\n"
                           "false each character reference in text becomes its own\n"
                           "CHARACTER_REFERENCE token instead of folding into the run; with\n"
                           "capture_source true every markup token records its verbatim source\n"
                           "(Token.source).");

PyDoc_STRVAR(parse_doc, "parse(markup, *, encoding=None, strict=False, positions=True)\n--\n\n"
                        "Parse a whole HTML document with the WHATWG tree-construction algorithm\n"
                        "and return a navigable Document. markup is a str, or bytes whose encoding\n"
                        "is sniffed (the encoding argument, a <meta> charset, then windows-1252).\n\n"
                        "The recovered parse errors are on Document.errors; with strict=True the\n"
                        "first one is raised as HTMLParseError instead. positions records each\n"
                        "element's source_line/source_col; pass False to skip it when memory or\n"
                        "speed matters more than source locations.");

PyDoc_STRVAR(parse_fragment_doc, "parse_fragment(html, context='div', *, positions=True)\n--\n\n"
                                 "Parse an HTML fragment as the innerHTML of a context element and return\n"
                                 "that context Element with the parsed nodes as its children. context is a\n"
                                 "tag name, optionally namespaced (e.g. 'td', 'svg path'). positions records\n"
                                 "element source_line/source_col; pass False to skip it.");

static PyMethodDef html_methods[] = {
    {"escape", (PyCFunction)(void (*)(void))turbohtml_escape, METH_VARARGS | METH_KEYWORDS, escape_doc},
    {"unescape", turbohtml_unescape, METH_O, unescape_doc},
    {"_markup_escape", turbohtml_markup_escape, METH_O, markup_escape_doc},
    {"_markup_escape_silent", turbohtml_markup_escape_silent, METH_O, markup_escape_silent_doc},
    {"_markup_soft_str", turbohtml_markup_soft_str, METH_O, markup_soft_str_doc},
    {"_register_markup", turbohtml_register_markup, METH_O, NULL},
    {"_register_xpath_string", turbohtml_register_xpath_string, METH_O, NULL},
    {"_register_links", turbohtml_register_links, METH_O, NULL},
    {"tokenize", (PyCFunction)(void (*)(void))turbohtml_tokenize, METH_VARARGS | METH_KEYWORDS, tokenize_doc},
    {"parse", (PyCFunction)(void (*)(void))turbohtml_parse, METH_VARARGS | METH_KEYWORDS, parse_doc},
    {"parse_fragment", (PyCFunction)(void (*)(void))turbohtml_tree_parse_fragment, METH_VARARGS | METH_KEYWORDS,
     parse_fragment_doc},
    {"_reconstruct", turbohtml_reconstruct, METH_VARARGS, NULL},
    {"_tokenize_states", turbohtml_tokenize_states, METH_VARARGS, NULL},
    {"_parse_tree", turbohtml_parse_tree, METH_O, NULL},
    {"_parse_fragment", turbohtml_parse_fragment, METH_VARARGS, NULL},
    {"_parse_only", turbohtml_parse_only, METH_O, NULL},
    {"_xpath_parse", turbohtml_xpath_parse, METH_O, NULL},
    {"_linkify_scan", turbohtml_linkify_scan, METH_VARARGS, NULL},
    {"_linkify_find", turbohtml_linkify_find, METH_VARARGS, NULL},
    {"_sanitize", turbohtml_sanitize, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL},
};

static int html_exec(PyObject *module) {
    module_state *state = PyModule_GetState(module);
    if (token_register(module, state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* allocation failure cannot be forced from a test */
    if (tokenizer_register(module, state) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                               /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree_register(module, state);
}

static int html_traverse(PyObject *module, visitproc visit, void *arg) {
    module_state *state = PyModule_GetState(module);
    Py_VISIT(state->token_type); /* GCOVR_EXCL_BR_LINE: Py_VISIT's NULL arm is dead, module state is populated for the
                                    module's lifetime */
    Py_VISIT(state->tokenizer_type); /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->iter_type);      /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->kind_enum);      /* GCOVR_EXCL_BR_LINE: same */
    for (int index = 0; index < 5; index++) {
        Py_VISIT(state->kinds[index]); /* GCOVR_EXCL_BR_LINE: same */
    }
    Py_VISIT(state->node_type);          /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->element_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->text_type);          /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->comment_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->doctype_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->pi_type);            /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->cdata_type);         /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->document_type);      /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->parser_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->parse_error_type);   /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->parse_error_exc);    /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->handle_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->attrs_type);         /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->walker_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->string_walker_type); /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->namespace_enum);     /* GCOVR_EXCL_BR_LINE: same */
    for (int index = 0; index < 3; index++) {
        Py_VISIT(state->namespaces[index]); /* GCOVR_EXCL_BR_LINE: same */
    }
    Py_VISIT(state->axis_enum);         /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->formatter_enum);    /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->minify_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->indent_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->pattern_type);      /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->markup_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->xpath_string_type); /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->link_type);         /* GCOVR_EXCL_BR_LINE: same */
    for (int index = 0; index < 7; index++) {
        Py_VISIT(state->axes[index]); /* GCOVR_EXCL_BR_LINE: same */
    }
    for (int index = 0; index < 3; index++) {
        Py_VISIT(state->formatters[index]); /* GCOVR_EXCL_BR_LINE: same */
    }
    return 0;
}

static int html_clear(PyObject *module) {
    module_state *state = PyModule_GetState(module);
    Py_CLEAR(state->token_type);
    Py_CLEAR(state->tokenizer_type);
    Py_CLEAR(state->iter_type);
    Py_CLEAR(state->kind_enum);
    for (int index = 0; index < 5; index++) {
        Py_CLEAR(state->kinds[index]);
    }
    Py_CLEAR(state->node_type);
    Py_CLEAR(state->element_type);
    Py_CLEAR(state->text_type);
    Py_CLEAR(state->comment_type);
    Py_CLEAR(state->doctype_type);
    Py_CLEAR(state->pi_type);
    Py_CLEAR(state->cdata_type);
    Py_CLEAR(state->document_type);
    Py_CLEAR(state->parser_type);
    Py_CLEAR(state->parse_error_type);
    Py_CLEAR(state->parse_error_exc);
    Py_CLEAR(state->handle_type);
    Py_CLEAR(state->attrs_type);
    Py_CLEAR(state->walker_type);
    Py_CLEAR(state->string_walker_type);
    Py_CLEAR(state->namespace_enum);
    for (int index = 0; index < 3; index++) {
        Py_CLEAR(state->namespaces[index]);
    }
    Py_CLEAR(state->axis_enum);
    Py_CLEAR(state->formatter_enum);
    Py_CLEAR(state->minify_type);
    Py_CLEAR(state->indent_type);
    Py_CLEAR(state->pattern_type);
    Py_CLEAR(state->markup_type);
    Py_CLEAR(state->xpath_string_type);
    Py_CLEAR(state->link_type);
    for (int index = 0; index < 7; index++) {
        Py_CLEAR(state->axes[index]);
    }
    for (int index = 0; index < 3; index++) {
        Py_CLEAR(state->formatters[index]);
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

// NOLINTNEXTLINE(misc-use-internal-linkage): the module init must be exported under this exact name
PyMODINIT_FUNC PyInit__html(void) {
    return PyModuleDef_Init(&htmlmodule);
}
