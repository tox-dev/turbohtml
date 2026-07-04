/* turbohtml._html - module definition wiring the per-feature implementations.

   escape()/unescape() are stateless string functions (escape.c, unescape.c).
   The tokenizer adds the Token and Tokenizer types plus tokenize(); those heap
   types live in per-module state so the module supports sub-interpreters and
   the free-threaded build. These sources use only public, version-portable
   APIs, so they build on CPython 3.10 through 3.15. */

#include "core/common.h"

#include "tokenizer/binding.h"

PyDoc_STRVAR(escape_doc, "escape(s, quote=True)\n--\n\n"
                         "Replace special characters \"&\", \"<\" and \">\" with HTML-safe sequences.\n\n"
                         ":param s: text to escape.\n"
                         ":param quote: also translate the double (\") and single (') quotation marks,\n"
                         "    so the result is safe inside an attribute value.\n"
                         ":returns: the escaped text.\n"
                         ":raises TypeError: if s is not a str.");

PyDoc_STRVAR(unescape_doc, "unescape(s, /)\n--\n\n"
                           "Convert all named and numeric character references in s to the\n"
                           "corresponding Unicode characters, following the HTML5 rules.\n\n"
                           ":param s: text containing character references.\n"
                           ":returns: the text with every reference resolved.\n"
                           ":raises TypeError: if s is not a str.");

PyDoc_STRVAR(markup_escape_doc, "escape(s, /)\n--\n\n"
                                "Replace &, <, >, ', and \" with HTML-safe sequences and return a Markup.\n\n"
                                "An object with an __html__ method is trusted as already safe; any other\n"
                                "object is converted to a string first. Matches markupsafe.escape, including\n"
                                "the numeric &#34; and &#39; quote references.\n\n"
                                ":param s: value to escape; an __html__ method marks it already safe.\n"
                                ":returns: a Markup holding the escaped text.");

PyDoc_STRVAR(markup_escape_silent_doc, "escape_silent(s, /)\n--\n\n"
                                       "Like escape, but None becomes the empty Markup rather than 'None'.\n\n"
                                       ":param s: value to escape, or None for an empty Markup.\n"
                                       ":returns: a Markup holding the escaped text.");

PyDoc_STRVAR(markup_soft_str_doc, "soft_str(s, /)\n--\n\n"
                                  "Convert s to str only if it is not already one, preserving a Markup so\n"
                                  "already-safe text is not escaped a second time.\n\n"
                                  ":param s: value to coerce to text.\n"
                                  ":returns: s unchanged when it is already a str, otherwise str(s).");

PyDoc_STRVAR(tokenize_doc, "tokenize(s, /, *, resolve_references=True, capture_source=False)\n--\n\n"
                           "Tokenize a whole HTML string following the WHATWG tokenization algorithm.\n\n"
                           ":param s: the HTML to tokenize.\n"
                           ":param resolve_references: fold character references into the surrounding text\n"
                           "    run; when False each one becomes its own CHARACTER_REFERENCE token.\n"
                           ":param capture_source: record each markup token's verbatim source on\n"
                           "    Token.source.\n"
                           ":returns: an iterator of Token objects in document order.\n"
                           ":raises TypeError: if s is not a str.");

PyDoc_STRVAR(parse_doc, "parse(markup, *, encoding=None, strict=False, detect_encoding=False, positions=True)\n--\n\n"
                        "Parse a whole HTML document with the WHATWG tree-construction algorithm and\n"
                        "return a navigable Document.\n\n"
                        ":param markup: the document, as str, or bytes whose encoding is sniffed (the\n"
                        "    encoding argument, then a <meta> charset, then windows-1252).\n"
                        ":param encoding: the encoding to decode bytes with; a declared, <meta>, or BOM\n"
                        "    encoding still wins over it.\n"
                        ":param strict: raise the first recovered parse error as HTMLParseError instead\n"
                        "    of collecting it on Document.errors.\n"
                        ":param detect_encoding: add a content-based detection step for bytes input,\n"
                        "    used only when the spec's encoding steps yield nothing.\n"
                        ":param positions: record each element's source_line/source_col; pass False to\n"
                        "    skip it when memory or speed matters more than source locations.\n"
                        ":returns: the parsed Document.\n"
                        ":raises TypeError: if markup is neither a str nor a bytes-like object.\n"
                        ":raises LookupError: if encoding names a codec Python does not know.\n"
                        ":raises HTMLParseError: under strict=True, on the first recovered parse error;\n"
                        "    its error attribute carries the ParseError (code, line, col).");

PyDoc_STRVAR(parse_fragment_doc, "parse_fragment(html, context='div', *, positions=True)\n--\n\n"
                                 "Parse an HTML fragment as the innerHTML of a context element.\n\n"
                                 ":param html: the fragment markup.\n"
                                 ":param context: the context element's tag name, optionally namespaced\n"
                                 "    (e.g. 'td', 'svg path').\n"
                                 ":param positions: record each element's source_line/source_col; pass\n"
                                 "    False to skip it.\n"
                                 ":returns: the context Element with the parsed nodes as its children.\n"
                                 ":raises TypeError: if html or context is not a str.\n"
                                 ":raises ValueError: if context is not a known element tag.");

PyDoc_STRVAR(annotation_surface_doc, "annotation_surface(text, spans, /)\n--\n\n"
                                     "Group the annotated substrings by label, the inscriptis surface-form\n"
                                     "extractor.\n\n"
                                     ":param text: the rendered text from Node.to_annotated_text().\n"
                                     ":param spans: the (start, end, label) spans from the same call.\n"
                                     ":returns: a dict mapping each label to the list of text[start:end] slices\n"
                                     "    its spans cover, in document order.");

PyDoc_STRVAR(annotation_tags_doc, "annotation_tags(text, spans, /)\n--\n\n"
                                  "Weave the annotated spans back into the text as inline markup, the\n"
                                  "inscriptis inline-tagged (XML) exporter. The innermost span closes first,\n"
                                  "so properly nested spans stay well-formed.\n\n"
                                  ":param text: the rendered text from Node.to_annotated_text().\n"
                                  ":param spans: the (start, end, label) spans from the same call.\n"
                                  ":returns: the text with each span wrapped in <label>...</label> tags.");

static PyMethodDef html_methods[] = {
    {"escape", (PyCFunction)(void (*)(void))turbohtml_escape, METH_VARARGS | METH_KEYWORDS, escape_doc},
    {"unescape", turbohtml_unescape, METH_O, unescape_doc},
    {"_minify_css", turbohtml_minify_css, METH_VARARGS, NULL},
    {"_minify_css_inline", turbohtml_minify_css_inline, METH_VARARGS, NULL},
    {"_markup_escape", turbohtml_markup_escape, METH_O, markup_escape_doc},
    {"_markup_escape_silent", turbohtml_markup_escape_silent, METH_O, markup_escape_silent_doc},
    {"_markup_soft_str", turbohtml_markup_soft_str, METH_O, markup_soft_str_doc},
    {"_register_markup", turbohtml_register_markup, METH_O, NULL},
    {"_register_xpath_string", turbohtml_register_xpath_string, METH_O, NULL},
    {"_register_links", turbohtml_register_links, METH_O, NULL},
    {"_register_selector_error", turbohtml_register_selector_error, METH_O, NULL},
    {"_register_structured_data", turbohtml_register_structured_data, METH_VARARGS, NULL},
    {"_register_article", turbohtml_register_article, METH_O, NULL},
    {"_register_js_minify", turbohtml_register_js_minify, METH_O, NULL},
    {"_register_css_minify", turbohtml_register_css_minify, METH_O, NULL},
    {"_register_render_configs", turbohtml_register_render_configs, METH_VARARGS, NULL},
    {"tokenize", (PyCFunction)(void (*)(void))turbohtml_tokenize, METH_VARARGS | METH_KEYWORDS, tokenize_doc},
    {"parse", (PyCFunction)(void (*)(void))turbohtml_parse, METH_VARARGS | METH_KEYWORDS, parse_doc},
    {"parse_fragment", (PyCFunction)(void (*)(void))turbohtml_tree_parse_fragment, METH_VARARGS | METH_KEYWORDS,
     parse_fragment_doc},
    {"annotation_surface", turbohtml_annotation_surface, METH_VARARGS, annotation_surface_doc},
    {"annotation_tags", turbohtml_annotation_tags, METH_VARARGS, annotation_tags_doc},
    {"_reconstruct", turbohtml_reconstruct, METH_VARARGS, NULL},
    {"_build_document", (PyCFunction)(void (*)(void))turbohtml_build_document, METH_VARARGS | METH_KEYWORDS, NULL},
    {"_tokenize_states", turbohtml_tokenize_states, METH_VARARGS, NULL},
    {"_parse_tree", turbohtml_parse_tree, METH_O, NULL},
    {"_parse_fragment", turbohtml_parse_fragment, METH_VARARGS, NULL},
    {"_parse_only", turbohtml_parse_only, METH_O, NULL},
    {"_xpath_parse", turbohtml_xpath_parse, METH_O, NULL},
    {"_css_to_xpath", turbohtml_css_to_xpath, METH_VARARGS, NULL},
    {"_minify_js", turbohtml_minify_js, METH_VARARGS, NULL},
    {"_minify_js_tokens", turbohtml_minify_js_tokens, METH_O, NULL},
    {"_minify_js_parse", turbohtml_minify_js_parse, METH_O, NULL},
    {"_detect", turbohtml_detect_encoding, METH_O, NULL},
    {"_detect_language", turbohtml_detect_language, METH_VARARGS, NULL},
    {"_linkify_scan", turbohtml_linkify_scan, METH_VARARGS, NULL},
    {"_linkify_find", turbohtml_linkify_find, METH_VARARGS, NULL},
    {"_registrable_domain", turbohtml_registrable_domain, METH_O, NULL},
    {"_url_split", turbohtml_url_split, METH_O, NULL},
    {"_url_percent_encode", turbohtml_url_percent_encode, METH_VARARGS, NULL},
    {"_url_percent_decode", turbohtml_url_percent_decode, METH_O, NULL},
    {"_url_join", turbohtml_url_join, METH_VARARGS, NULL},
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
    Py_VISIT(state->node_type);           /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->element_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->text_type);           /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->comment_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->doctype_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->pi_type);             /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->cdata_type);          /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->document_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->parser_type);         /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->parse_error_type);    /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->parse_error_exc);     /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->handle_type);         /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->attrs_type);          /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->walker_type);         /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->string_walker_type);  /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->serialize_iter_type); /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->namespace_enum);      /* GCOVR_EXCL_BR_LINE: same */
    for (int index = 0; index < 3; index++) {
        Py_VISIT(state->namespaces[index]); /* GCOVR_EXCL_BR_LINE: same */
    }
    Py_VISIT(state->axis_enum);             /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->formatter_enum);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->minify_type);           /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->indent_type);           /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->pattern_type);          /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->re_compile);            /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->markup_type);           /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->xpath_string_type);     /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->xpath_type);            /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->selector_error);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->link_type);             /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->json_ld_parser);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->microdata_item_type);   /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->rdfa_item_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->structured_data_type);  /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->opengraph_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->article_type);          /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->js_minify_type);        /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->css_minify_type);       /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->markdown_config_type);  /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->plaintext_config_type); /* GCOVR_EXCL_BR_LINE: same */
    Py_VISIT(state->html_config_type);      /* GCOVR_EXCL_BR_LINE: same */
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
    th_node_freelist_clear(state); /* free pooled wrappers while their node types are still live */
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
    Py_CLEAR(state->serialize_iter_type);
    Py_CLEAR(state->namespace_enum);
    for (int index = 0; index < 3; index++) {
        Py_CLEAR(state->namespaces[index]);
    }
    Py_CLEAR(state->axis_enum);
    Py_CLEAR(state->formatter_enum);
    Py_CLEAR(state->minify_type);
    Py_CLEAR(state->indent_type);
    Py_CLEAR(state->pattern_type);
    Py_CLEAR(state->re_compile);
    Py_CLEAR(state->markup_type);
    Py_CLEAR(state->xpath_string_type);
    Py_CLEAR(state->xpath_type);
    Py_CLEAR(state->selector_error);
    Py_CLEAR(state->link_type);
    Py_CLEAR(state->json_ld_parser);
    Py_CLEAR(state->microdata_item_type);
    Py_CLEAR(state->rdfa_item_type);
    Py_CLEAR(state->structured_data_type);
    Py_CLEAR(state->opengraph_type);
    Py_CLEAR(state->article_type);
    Py_CLEAR(state->js_minify_type);
    Py_CLEAR(state->css_minify_type);
    Py_CLEAR(state->markdown_config_type);
    Py_CLEAR(state->plaintext_config_type);
    Py_CLEAR(state->html_config_type);
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
