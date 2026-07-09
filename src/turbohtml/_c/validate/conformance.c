/* HTML5 authoring-conformance checker with a severity model over a parsed tree.

   The engine behind turbohtml.conformance.check (issue #541). The WHATWG parser
   already recovers and reports tokenization/tree-construction ParseErrors; this is
   the distinct authoring surface the Nu Html Checker (validator.nu) occupies -- the
   document-conformance requirements a well-formed parse still violates: a missing
   img alt, an obsolete presentational element or attribute, a duplicate id, an
   invalid or redundant ARIA role, a heading with no text, a section or article with
   no heading, a document with no title or language. Each finding carries a stable
   message code and a severity (error / warning / info) the way validator.nu
   classifies by type/subType, and the document is valid exactly when no finding is
   an error. The WHATWG HTML standard's authoring requirements and WAI-ARIA 1.2 are
   the conformance authority.

   The walk is iterative (preorder over the borrowed subtree, with bounded inner
   scans for the heading and section checks) so no depth of tree can exhaust the C
   stack: unlike the schema validator this needs no recursion and no depth cap. Every
   name test runs against a static table by a loop rather than a chained comparison,
   so element and attribute spellings the atom table never interns are covered and
   the branch coverage stays stable across compilers. */

#include "core/common.h"

#include "core/ascii.h"
#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */

#include "dom/tree.h"

#include <stdarg.h>

static const char SEVERITY_ERROR[] = "error";
static const char SEVERITY_WARNING[] = "warning";
static const char SEVERITY_INFO[] = "info";

/* One accumulated finding is a (code, severity, message, line, column) tuple appended
   to a Python list. tree resolves a node's source position; failed latches an
   unforceable allocation failure so the walk stops reporting rather than crash. */
typedef struct {
    PyObject *findings;
    th_tree *tree;
    int failed;
} confctx;

static int ucs4_ci_equals_ascii(const Py_UCS4 *text, Py_ssize_t len, const char *ascii) {
    Py_ssize_t index = 0;
    for (; index < len; index++) {
        if (ascii[index] == '\0') {
            return 0;
        }
        if (lower_ascii((uint32_t)text[index]) != (uint32_t)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return ascii[len] == '\0';
}

static int bytes_ci_equals_ascii(const char *bytes, Py_ssize_t len, const char *ascii) {
    Py_ssize_t index = 0;
    for (; index < len; index++) {
        if (ascii[index] == '\0') {
            return 0;
        }
        if (lower_ascii((uint32_t)(unsigned char)bytes[index]) != (uint32_t)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return ascii[len] == '\0';
}

/* The next node of a preorder walk bounded to root's subtree, or NULL at the end. The
   checker never mutates the tree mid-walk, so every cursor stays a descendant of root
   and the climb always reaches it. Local to this unit so it depends only on dom/tree.h. */
static th_node *next_preorder(th_node *current, th_node *root) {
    if (current->first_child != NULL) {
        return current->first_child;
    }
    while (current != root) {
        if (current->next_sibling != NULL) {
            return current->next_sibling;
        }
        current = current->parent;
    }
    return NULL;
}

/* The attribute carrying name_atom on a node, or NULL when absent. Local to this unit
   so the checker depends only on dom/tree.h, not the wider nodes.h Python cluster. */
static const th_node_attr *attr_by_atom(const th_node *node, uint32_t atom) {
    Py_ssize_t index = 0;
    for (; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

/* Whether the node carries an attribute whose name matches ascii (case-insensitive),
   resolving each interned name back to its bytes. Covers presentational attributes the
   atom table never interns, so the obsolete-attribute and labeling tests read by name. */
static const th_node_attr *attr_by_name(th_tree *tree, const th_node *node, const char *ascii) {
    Py_ssize_t index = 0;
    for (; index < node->attr_count; index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        if (bytes_ci_equals_ascii(name, name_len, ascii)) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

/* The first whitespace-delimited token of a value, the effective role for a role="..."
   list. start/len bound it; a value that is all whitespace yields len 0. */
static void first_token(const Py_UCS4 *value, Py_ssize_t value_len, Py_ssize_t *start, Py_ssize_t *len) {
    Py_ssize_t cursor = 0;
    while (cursor < value_len && is_space((uint32_t)value[cursor])) {
        cursor++;
    }
    Py_ssize_t begin = cursor;
    while (cursor < value_len && !is_space((uint32_t)value[cursor])) {
        cursor++;
    }
    *start = begin;
    *len = cursor - begin;
}

/* Every non-abstract WAI-ARIA 1.2 role token that may appear in an author's role="".
   Abstract roles (command, composite, input, landmark, range, roletype, section,
   sectionhead, select, structure, widget, window) are deliberately absent, so using one
   is reported invalid the way validator.nu rejects it. */
static const char *const ARIA_ROLES[] = {
    "alert",
    "alertdialog",
    "application",
    "article",
    "banner",
    "blockquote",
    "button",
    "caption",
    "cell",
    "checkbox",
    "code",
    "columnheader",
    "combobox",
    "complementary",
    "contentinfo",
    "definition",
    "deletion",
    "dialog",
    "directory",
    "document",
    "emphasis",
    "feed",
    "figure",
    "form",
    "generic",
    "grid",
    "gridcell",
    "group",
    "heading",
    "img",
    "insertion",
    "link",
    "list",
    "listbox",
    "listitem",
    "log",
    "main",
    "marquee",
    "math",
    "menu",
    "menubar",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "meter",
    "navigation",
    "none",
    "note",
    "option",
    "paragraph",
    "presentation",
    "progressbar",
    "radio",
    "radiogroup",
    "region",
    "row",
    "rowgroup",
    "rowheader",
    "scrollbar",
    "search",
    "searchbox",
    "separator",
    "slider",
    "spinbutton",
    "status",
    "strong",
    "subscript",
    "superscript",
    "switch",
    "tab",
    "table",
    "tablist",
    "tabpanel",
    "term",
    "textbox",
    "time",
    "timer",
    "toolbar",
    "tooltip",
    "tree",
    "treegrid",
    "treeitem",
};
static const size_t ARIA_ROLE_COUNT = sizeof(ARIA_ROLES) / sizeof(ARIA_ROLES[0]);

static int is_known_role(const Py_UCS4 *token, Py_ssize_t len) {
    size_t index = 0;
    for (; index < ARIA_ROLE_COUNT; index++) {
        if (ucs4_ci_equals_ascii(token, len, ARIA_ROLES[index])) {
            return 1;
        }
    }
    return 0;
}

/* An element whose implicit ARIA role a matching role="" restates. Keyed by tag atom;
   an entry's role is the token that is redundant on that element. Only unambiguous,
   context-free implicit roles are listed, so the redundant-role warning never fires on
   a role that a browser would actually resolve differently by context. */
typedef struct {
    uint16_t tag_atom;
    const char *role;
} implicit_role;

static const implicit_role IMPLICIT_ROLES[] = {
    {TH_TAG_ARTICLE, "article"}, {TH_TAG_ASIDE, "complementary"}, {TH_TAG_BUTTON, "button"}, {TH_TAG_NAV, "navigation"},
    {TH_TAG_MAIN, "main"},       {TH_TAG_H1, "heading"},          {TH_TAG_H2, "heading"},    {TH_TAG_H3, "heading"},
    {TH_TAG_H4, "heading"},      {TH_TAG_H5, "heading"},          {TH_TAG_H6, "heading"},    {TH_TAG_UL, "list"},
    {TH_TAG_OL, "list"},         {TH_TAG_LI, "listitem"},         {TH_TAG_TABLE, "table"},   {TH_TAG_DIALOG, "dialog"},
    {TH_TAG_HR, "separator"},
};
static const size_t IMPLICIT_ROLE_COUNT = sizeof(IMPLICIT_ROLES) / sizeof(IMPLICIT_ROLES[0]);

static const char *implicit_role_for(uint16_t tag_atom) {
    size_t index = 0;
    for (; index < IMPLICIT_ROLE_COUNT; index++) {
        if (IMPLICIT_ROLES[index].tag_atom == tag_atom) {
            return IMPLICIT_ROLES[index].role;
        }
    }
    return NULL;
}

/* Obsolete (non-conforming) HTML elements from the WHATWG "Non-conforming features"
   table. Matched by tag spelling, not atom, so spellings the atom table never interns
   (acronym, blink, isindex, spacer) are still caught. */
static const char *const OBSOLETE_ELEMENTS[] = {
    "acronym",  "applet",    "basefont", "big",     "blink",   "center",  "dir",
    "font",     "frame",     "frameset", "isindex", "listing", "marquee", "nobr",
    "noframes", "plaintext", "spacer",   "strike",  "tt",      "xmp",     "bgsound",
};
static const size_t OBSOLETE_ELEMENT_COUNT = sizeof(OBSOLETE_ELEMENTS) / sizeof(OBSOLETE_ELEMENTS[0]);

/* Obsolete attributes. tag_atom == TH_TAG_UNKNOWN marks a presentational attribute that
   is non-conforming on any element; a specific atom restricts the finding to that
   element, the way the WHATWG obsolete-attributes table scopes link/vlink to body and
   frame/rules to table. */
typedef struct {
    const char *name;
    uint16_t tag_atom;
} obsolete_attr;

static const obsolete_attr OBSOLETE_ATTRS[] = {
    {"align", TH_TAG_UNKNOWN},
    {"bgcolor", TH_TAG_UNKNOWN},
    {"background", TH_TAG_UNKNOWN},
    {"bordercolor", TH_TAG_UNKNOWN},
    {"cellpadding", TH_TAG_UNKNOWN},
    {"cellspacing", TH_TAG_UNKNOWN},
    {"valign", TH_TAG_UNKNOWN},
    {"hspace", TH_TAG_UNKNOWN},
    {"vspace", TH_TAG_UNKNOWN},
    {"nowrap", TH_TAG_UNKNOWN},
    {"clear", TH_TAG_UNKNOWN},
    {"compact", TH_TAG_UNKNOWN},
    {"noshade", TH_TAG_UNKNOWN},
    {"link", TH_TAG_BODY},
    {"vlink", TH_TAG_BODY},
    {"alink", TH_TAG_BODY},
    {"text", TH_TAG_BODY},
    {"frame", TH_TAG_TABLE},
    {"rules", TH_TAG_TABLE},
    {"language", TH_TAG_SCRIPT},
    {"charset", TH_TAG_A},
    {"charset", TH_TAG_LINK},
    {"rev", TH_TAG_A},
    {"rev", TH_TAG_LINK},
    {"longdesc", TH_TAG_IMG},
};
static const size_t OBSOLETE_ATTR_COUNT = sizeof(OBSOLETE_ATTRS) / sizeof(OBSOLETE_ATTRS[0]);

/* Script types that a classic script may carry redundantly: omitting type has the same
   effect, so the WHATWG standard asks authors to leave it out. "module" and any data
   block (application/json, text/template) are meaningful and never flagged. */
static const char *const REDUNDANT_SCRIPT_TYPES[] = {
    "text/javascript",        "application/javascript", "text/ecmascript",
    "application/ecmascript", "text/javascript1.0",     "text/jscript",
};
static const size_t REDUNDANT_SCRIPT_TYPE_COUNT = sizeof(REDUNDANT_SCRIPT_TYPES) / sizeof(REDUNDANT_SCRIPT_TYPES[0]);

static void report(confctx *ctx, th_node *node, const char *code, const char *severity, const char *fmt, ...) {
    if (ctx->failed) { /* GCOVR_EXCL_BR_LINE: failed is set only by an unforceable allocation failure */
        return;        /* GCOVR_EXCL_LINE */
    }
    va_list args;
    va_start(args, fmt);
    PyObject *message = th_str_format_v(fmt, args);
    va_end(args);
    Py_ssize_t line = 0, col = 0;
    th_node_source_position(ctx->tree, node, &line, &col);
    PyObject *tuple = NULL;
    if (message != NULL) { /* GCOVR_EXCL_BR_LINE: str construction failure is unforceable */
        tuple = Py_BuildValue("(ssNnn)", code, severity, message, line, col);
    }
    int appended = tuple != NULL && PyList_Append(ctx->findings, tuple) == 0; /* GCOVR_EXCL_BR_LINE: append OOM */
    if (!appended) {     /* GCOVR_EXCL_BR_LINE: list append OOM is unforceable */
        ctx->failed = 1; /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: llvm attributes the unexecuted fall-through to this brace */
    Py_XDECREF(tuple);
}

/* Whether any text descendant of root holds a non-whitespace character. Bounded to the
   node's subtree so the heading-content scan never leaves it. th_node_text_is_blank
   realizes each text node's zero-copy source span on demand. */
static int subtree_has_text(th_tree *tree, th_node *root) {
    th_node *node = root->first_child;
    while (node != NULL) {
        if (node->type == TH_NODE_TEXT && !th_node_text_is_blank(tree, node)) {
            return 1;
        }
        node = next_preorder(node, root);
    }
    return 0;
}

/* Whether the subtree holds an HTML element with one of atoms (count of them), the
   heading set for the section check or the img set for the heading check. */
static int subtree_has_element(th_node *root, const uint16_t *atoms, size_t count) {
    th_node *node = root->first_child;
    while (node != NULL) {
        if (node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML) {
            size_t index = 0;
            for (; index < count; index++) {
                if (node->atom == atoms[index]) {
                    return 1;
                }
            }
        }
        node = next_preorder(node, root);
    }
    return 0;
}

static const uint16_t HEADING_ATOMS[] = {TH_TAG_H1, TH_TAG_H2, TH_TAG_H3, TH_TAG_H4, TH_TAG_H5, TH_TAG_H6};
static const size_t HEADING_ATOM_COUNT = sizeof(HEADING_ATOMS) / sizeof(HEADING_ATOMS[0]);
static const uint16_t IMG_ATOMS[] = {TH_TAG_IMG};
static const uint16_t SECTION_ATOMS[] = {TH_TAG_SECTION, TH_TAG_ARTICLE};
static const size_t SECTION_ATOM_COUNT = sizeof(SECTION_ATOMS) / sizeof(SECTION_ATOMS[0]);

static int atom_in(uint16_t atom, const uint16_t *atoms, size_t count) {
    size_t index = 0;
    for (; index < count; index++) {
        if (atoms[index] == atom) {
            return 1;
        }
    }
    return 0;
}

static void check_alt(confctx *ctx, th_node *node) {
    if (node->atom == TH_TAG_IMG) {
        if (attr_by_atom(node, TH_ATTR_ALT) == NULL) {
            report(ctx, node, "img-missing-alt", SEVERITY_ERROR, "img element has no alt attribute");
        }
        return;
    }
    if (node->atom == TH_TAG_AREA && attr_by_atom(node, TH_ATTR_HREF) != NULL) {
        if (attr_by_atom(node, TH_ATTR_ALT) == NULL) {
            report(ctx, node, "area-missing-alt", SEVERITY_ERROR, "area element with href has no alt attribute");
        }
        return;
    }
    if (node->atom == TH_TAG_INPUT) {
        const th_node_attr *type = attr_by_atom(node, TH_ATTR_TYPE);
        if (type != NULL && type->value != NULL && ucs4_ci_equals_ascii(type->value, type->value_len, "image") &&
            attr_by_atom(node, TH_ATTR_ALT) == NULL) {
            report(ctx, node, "input-image-missing-alt", SEVERITY_ERROR,
                   "input element of type image has no alt attribute");
        }
    }
}

static void check_obsolete_element(confctx *ctx, th_node *node) {
    size_t index = 0;
    for (; index < OBSOLETE_ELEMENT_COUNT; index++) {
        if (ucs4_ci_equals_ascii(node->text, node->text_len, OBSOLETE_ELEMENTS[index])) {
            report(ctx, node, "obsolete-element", SEVERITY_ERROR, "the %s element is obsolete",
                   OBSOLETE_ELEMENTS[index]);
            return;
        }
    }
}

static void check_obsolete_attributes(confctx *ctx, th_node *node) {
    Py_ssize_t attr_index = 0;
    for (; attr_index < node->attr_count; attr_index++) {
        Py_ssize_t name_len = 0;
        const char *name = th_attr_name(ctx->tree, node->attrs[attr_index].name_atom, &name_len);
        size_t rule = 0;
        for (; rule < OBSOLETE_ATTR_COUNT; rule++) {
            int scoped = OBSOLETE_ATTRS[rule].tag_atom == TH_TAG_UNKNOWN || OBSOLETE_ATTRS[rule].tag_atom == node->atom;
            if (scoped && bytes_ci_equals_ascii(name, name_len, OBSOLETE_ATTRS[rule].name)) {
                report(ctx, node, "obsolete-attribute", SEVERITY_ERROR, "the %s attribute is obsolete",
                       OBSOLETE_ATTRS[rule].name);
                break;
            }
        }
    }
}

static void check_role(confctx *ctx, th_node *node) {
    const th_node_attr *role = attr_by_atom(node, TH_ATTR_ROLE);
    if (role == NULL || role->value == NULL) {
        return;
    }
    Py_ssize_t start = 0, len = 0;
    first_token(role->value, role->value_len, &start, &len);
    if (len == 0) {
        return;
    }
    const Py_UCS4 *token = &role->value[start];
    if (!is_known_role(token, len)) {
        report(ctx, node, "aria-invalid-role", SEVERITY_ERROR, "role value is not a defined ARIA role");
        return;
    }
    const char *implicit = implicit_role_for(node->atom);
    if (node->atom == TH_TAG_A && attr_by_atom(node, TH_ATTR_HREF) != NULL) {
        implicit = "link";
    }
    if (implicit != NULL && ucs4_ci_equals_ascii(token, len, implicit)) {
        report(ctx, node, "aria-redundant-role", SEVERITY_WARNING, "role %s duplicates the element's implicit role",
               implicit);
    }
}

static void check_redundant_type(confctx *ctx, th_node *node) {
    const th_node_attr *type = attr_by_atom(node, TH_ATTR_TYPE);
    if (type == NULL || type->value == NULL) {
        return;
    }
    if (node->atom == TH_TAG_STYLE) {
        if (ucs4_ci_equals_ascii(type->value, type->value_len, "text/css")) {
            report(ctx, node, "redundant-type-attribute", SEVERITY_INFO,
                   "the type attribute is unnecessary for a CSS style element");
        }
        return;
    }
    if (node->atom == TH_TAG_SCRIPT) {
        size_t index = 0;
        for (; index < REDUNDANT_SCRIPT_TYPE_COUNT; index++) {
            if (ucs4_ci_equals_ascii(type->value, type->value_len, REDUNDANT_SCRIPT_TYPES[index])) {
                report(ctx, node, "redundant-type-attribute", SEVERITY_INFO,
                       "the type attribute is unnecessary for a JavaScript element");
                return;
            }
        }
    }
}

static void check_heading(confctx *ctx, th_node *node) {
    if (!atom_in(node->atom, HEADING_ATOMS, HEADING_ATOM_COUNT)) {
        return;
    }
    if (!subtree_has_text(ctx->tree, node) && !subtree_has_element(node, IMG_ATOMS, 1)) {
        report(ctx, node, "empty-heading", SEVERITY_WARNING, "heading element has no content");
    }
}

static void check_section(confctx *ctx, th_node *node) {
    if (!atom_in(node->atom, SECTION_ATOMS, SECTION_ATOM_COUNT)) {
        return;
    }
    if (subtree_has_element(node, HEADING_ATOMS, HEADING_ATOM_COUNT)) {
        return;
    }
    if (attr_by_name(ctx->tree, node, "aria-label") != NULL ||
        attr_by_name(ctx->tree, node, "aria-labelledby") != NULL || attr_by_name(ctx->tree, node, "title") != NULL) {
        return;
    }
    report(ctx, node, "section-no-heading", SEVERITY_WARNING, "sectioning element has no heading");
}

/* Record an id occurrence; report the second and later use of a value. ids maps each id
   string to Py_True, so a present key means the value was already seen. */
static void check_duplicate_id(confctx *ctx, th_node *node, PyObject *ids) {
    const th_node_attr *id = attr_by_atom(node, TH_ATTR_ID);
    if (id == NULL || id->value == NULL) { /* an empty id="" is stored valueless, so value NULL covers it */
        return;
    }
    PyObject *key = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, id->value, id->value_len);
    if (key == NULL) {   /* GCOVR_EXCL_BR_LINE: str construction failure is unforceable */
        ctx->failed = 1; /* GCOVR_EXCL_LINE */
        return;          /* GCOVR_EXCL_LINE */
    }
    int seen = PyDict_Contains(ids, key);
    if (seen < 0) {      /* GCOVR_EXCL_BR_LINE: a str key never fails to hash */
        ctx->failed = 1; /* GCOVR_EXCL_LINE */
        Py_DECREF(key);  /* GCOVR_EXCL_LINE */
        return;          /* GCOVR_EXCL_LINE */
    }
    if (seen) {
        report(ctx, node, "duplicate-id", SEVERITY_ERROR, "id %U is used more than once", key);
    } else if (PyDict_SetItem(ids, key, Py_True) < 0) { /* GCOVR_EXCL_BR_LINE: dict insert OOM is unforceable */
        ctx->failed = 1;                                /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: llvm attributes the unexecuted fall-through to this brace */
    Py_DECREF(key);
}

/* Walk the subtree once, gathering the per-element findings and the document-level
   signals (an html element, its lang attribute, a non-empty title). */
static void walk(confctx *ctx, th_node *root, th_node **html_out, int *has_lang, int *has_title) {
    PyObject *ids = PyDict_New();
    if (ids == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->failed = 1; /* GCOVR_EXCL_LINE */
        return;          /* GCOVR_EXCL_LINE */
    }
    th_node *node = root->type == TH_NODE_ELEMENT ? root : root->first_child;
    for (; node != NULL; node = next_preorder(node, root)) {
        if (ctx->failed) { /* GCOVR_EXCL_BR_LINE: failed is set only by an unforceable allocation failure */
            break;         /* GCOVR_EXCL_LINE */
        }
        if (node->type != TH_NODE_ELEMENT || node->ns != TH_NS_HTML) {
            continue;
        }
        if (node->atom == TH_TAG_HTML) {
            *html_out = node;
            *has_lang = attr_by_atom(node, TH_ATTR_LANG) != NULL || attr_by_name(ctx->tree, node, "xml:lang") != NULL;
        } else if (node->atom == TH_TAG_TITLE && subtree_has_text(ctx->tree, node)) {
            *has_title = 1;
        }
        check_alt(ctx, node);
        check_obsolete_element(ctx, node);
        check_obsolete_attributes(ctx, node);
        check_role(ctx, node);
        check_redundant_type(ctx, node);
        check_heading(ctx, node);
        check_section(ctx, node);
        check_duplicate_id(ctx, node, ids);
    }
    Py_DECREF(ids);
}

/* _conformance_check(node) -> [(code, severity, message, line, column), ...]. Runs the
   authoring-conformance checks over a parsed document or subtree; the thin Python shim
   shapes the findings into a ConformanceReport and derives the validity verdict. */
PyObject *turbohtml_conformance_check(PyObject *module, PyObject *arg) {
    th_tree *tree;
    th_node *node;
    if (turbohtml_node_borrow(module, arg, &tree, &node) < 0) {
        return NULL;
    }
    PyObject *findings = PyList_New(0);
    if (findings == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    confctx ctx = {findings, tree, 0};
    int is_document = node->type == TH_NODE_DOCUMENT;
    th_node *html = NULL;
    int has_lang = 0, has_title = 0;
    Py_BEGIN_CRITICAL_SECTION(turbohtml_node_handle(arg));
    walk(&ctx, node, &html, &has_lang, &has_title);
    if (is_document) { /* report() no-ops once ctx.failed, so no failed guard is needed here */
        if (html != NULL && !has_lang) {
            report(&ctx, html, "missing-lang", SEVERITY_WARNING, "html element has no lang attribute");
        }
        if (!has_title) {
            report(&ctx, html != NULL ? html : node, "missing-title", SEVERITY_ERROR,
                   "document has no non-empty title element");
        }
    }
    Py_END_CRITICAL_SECTION();
    if (ctx.failed) {        /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        Py_DECREF(findings); /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    return findings;
}
