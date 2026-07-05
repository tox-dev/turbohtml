/* Foreign-content (SVG / MathML) handling: name/attribute case adjustments,
   integration-point and breakout checks, and the foreign insertion-mode step.
   #included at the top of dom/tree.c so the static helpers inline against the
   tree in the same translation unit. */

#ifndef TURBOHTML_DOM_FOREIGN_H
#define TURBOHTML_DOM_FOREIGN_H

/* Tree-builder helpers defined further down in dom/tree.c. foreign.h is included at
   the top of that file (issue #478 removed the mid-file include), so it forward-declares
   the static helpers it shares rather than relying on their definitions appearing first. */
static th_node *current_node(th_tree *tree);
static int stack_push(th_tree *tree, th_node *node);
static void stack_pop(th_tree *tree);
static th_node *insert_element(th_tree *tree, const th_token *token);
static void insert_comment(th_tree *tree, const th_token *token, th_node *parent);
static void insert_text(th_tree *tree, Py_UCS4 *text, Py_ssize_t len);
static Py_UCS4 *token_text(th_tree *tree, const th_token *token, Py_ssize_t *out_len);
static uint16_t tok_atom(const th_token *tok);
static int name_matches(const th_node *node, const th_token *token, int fold);
static int all_whitespace(const Py_UCS4 *text, Py_ssize_t len);

/* SVG element names that take a specific mixed case (the spec's "SVG element
   name adjustments"); everything else stays lowercase. */
static const struct {
    const char *lower;
    const char *mixed;
} SVG_NAMES[] = {
    {"altglyph", "altGlyph"},
    {"altglyphdef", "altGlyphDef"},
    {"altglyphitem", "altGlyphItem"},
    {"animatecolor", "animateColor"},
    {"animatemotion", "animateMotion"},
    {"animatetransform", "animateTransform"},
    {"clippath", "clipPath"},
    {"feblend", "feBlend"},
    {"fecolormatrix", "feColorMatrix"},
    {"fecomponenttransfer", "feComponentTransfer"},
    {"fecomposite", "feComposite"},
    {"feconvolvematrix", "feConvolveMatrix"},
    {"fediffuselighting", "feDiffuseLighting"},
    {"fedisplacementmap", "feDisplacementMap"},
    {"fedistantlight", "feDistantLight"},
    {"fedropshadow", "feDropShadow"},
    {"feflood", "feFlood"},
    {"fefunca", "feFuncA"},
    {"fefuncb", "feFuncB"},
    {"fefuncg", "feFuncG"},
    {"fefuncr", "feFuncR"},
    {"fegaussianblur", "feGaussianBlur"},
    {"feimage", "feImage"},
    {"femerge", "feMerge"},
    {"femergenode", "feMergeNode"},
    {"femorphology", "feMorphology"},
    {"feoffset", "feOffset"},
    {"fepointlight", "fePointLight"},
    {"fespecularlighting", "feSpecularLighting"},
    {"fespotlight", "feSpotLight"},
    {"fetile", "feTile"},
    {"feturbulence", "feTurbulence"},
    {"foreignobject", "foreignObject"},
    {"glyphref", "glyphRef"},
    {"lineargradient", "linearGradient"},
    {"radialgradient", "radialGradient"},
    {"textpath", "textPath"},
};

static const char *svg_adjust_name(const char *lower, Py_ssize_t len) {
    for (size_t index = 0; index < sizeof(SVG_NAMES) / sizeof(SVG_NAMES[0]); index++) {
        if ((Py_ssize_t)strlen(SVG_NAMES[index].lower) == len &&
            memcmp(SVG_NAMES[index].lower, lower, (size_t)len) == 0) {
            return SVG_NAMES[index].mixed;
        }
    }
    return NULL;
}

/* SVG attribute names that take mixed case (the spec's "SVG attribute name
   adjustments"); the serializer applies these on SVG elements. */
static const char *SVG_ATTRS[] = {
    "attributeName",
    "attributeType",
    "baseFrequency",
    "baseProfile",
    "calcMode",
    "clipPathUnits",
    "diffuseConstant",
    "edgeMode",
    "filterUnits",
    "glyphRef",
    "gradientTransform",
    "gradientUnits",
    "kernelMatrix",
    "kernelUnitLength",
    "keyPoints",
    "keySplines",
    "keyTimes",
    "lengthAdjust",
    "limitingConeAngle",
    "markerHeight",
    "markerUnits",
    "markerWidth",
    "maskContentUnits",
    "maskUnits",
    "numOctaves",
    "pathLength",
    "patternContentUnits",
    "patternTransform",
    "patternUnits",
    "pointsAtX",
    "pointsAtY",
    "pointsAtZ",
    "preserveAlpha",
    "preserveAspectRatio",
    "primitiveUnits",
    "refX",
    "refY",
    "repeatCount",
    "repeatDur",
    "requiredExtensions",
    "requiredFeatures",
    "specularConstant",
    "specularExponent",
    "spreadMethod",
    "startOffset",
    "stdDeviation",
    "stitchTiles",
    "surfaceScale",
    "systemLanguage",
    "tableValues",
    "targetX",
    "targetY",
    "textLength",
    "viewBox",
    "viewTarget",
    "xChannelSelector",
    "yChannelSelector",
    "zoomAndPan",
};

/* The mixed-case spelling of a foreign attribute name (lowercased input), or
   NULL to leave it as-is. */
static const char *foreign_adjust_attr(const char *lower, Py_ssize_t len, uint8_t ns) {
    if (ns == TH_NS_MATHML) {
        return (len == 13 && memcmp(lower, "definitionurl", 13) == 0) ? "definitionURL" : NULL;
    }
    if (ns == TH_NS_SVG) { /* GCOVR_EXCL_BR_LINE: only reached for SVG; MathML returns earlier */
        for (size_t index = 0; index < sizeof(SVG_ATTRS) / sizeof(SVG_ATTRS[0]); index++) {
            const char *mixed = SVG_ATTRS[index];
            if ((Py_ssize_t)strlen(mixed) == len) {
                int same = 1;
                for (Py_ssize_t char_index = 0; char_index < len && same; char_index++) {
                    char raw = mixed[char_index];
                    /* SVG attribute names are letters, so the lower bound always holds */
                    char lowered = raw >= 'A' /* GCOVR_EXCL_BR_LINE */ && raw <= 'Z' ? (char)(raw + 32) : raw;
                    if (lowered != lower[char_index]) {
                        same = 0;
                    }
                }
                if (same) {
                    return mixed;
                }
            }
        }
    }
    return NULL;
}

static int is_mathml_text_integration(const th_node *node) {
    return node->ns == TH_NS_MATHML && (node->atom == TH_TAG_MI || node->atom == TH_TAG_MO || node->atom == TH_TAG_MN ||
                                        node->atom == TH_TAG_MS || node->atom == TH_TAG_MTEXT);
}

/* ASCII case-insensitive match of a code-point run against a NUL-terminated ASCII string. */
static int value_ci_eq(const Py_UCS4 *value, Py_ssize_t value_len, const char *target) {
    if (value_len != (Py_ssize_t)strlen(target)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < value_len; index++) {
        Py_UCS4 ch = value[index] >= 'A' && value[index] <= 'Z' ? value[index] + 32 : value[index];
        if (ch != (Py_UCS4)(unsigned char)target[index]) {
            return 0;
        }
    }
    return 1;
}

static int is_html_integration(const th_node *node) {
    if (node->ns == TH_NS_SVG) {
        return node->atom == TH_TAG_FOREIGNOBJECT || node->atom == TH_TAG_DESC || node->atom == TH_TAG_TITLE;
    }
    /* annotation-xml is an HTML integration point only when its encoding attribute is an ASCII
       case-insensitive match for exactly text/html or application/xhtml+xml. Only foreign nodes
       reach here after the html case, so a non-svg node is always mathml. */
    if (node->ns == TH_NS_MATHML /* GCOVR_EXCL_BR_LINE */ && node->atom == TH_TAG_ANNOTATION_XML) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            const th_node_attr *attr = &node->attrs[index];
            if (attr->name_atom == TH_ATTR_ENCODING && attr->value != NULL &&
                (value_ci_eq(attr->value, attr->value_len, "text/html") ||
                 value_ci_eq(attr->value, attr->value_len, "application/xhtml+xml"))) {
                return 1;
            }
        }
    }
    return 0;
}

/* HTML start tags that break out of foreign content back into HTML parsing. */
static int is_foreign_breakout(uint16_t atom) {
    switch (atom) {
    case TH_TAG_B:
    case TH_TAG_BIG:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_BODY:
    case TH_TAG_BR:
    case TH_TAG_CENTER:
    case TH_TAG_CODE:
    case TH_TAG_DD:
    case TH_TAG_DIV:
    case TH_TAG_DL:
    case TH_TAG_DT:
    case TH_TAG_EM:
    case TH_TAG_EMBED:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_HEAD:
    case TH_TAG_HR:
    case TH_TAG_I:
    case TH_TAG_IMG:
    case TH_TAG_LI:
    case TH_TAG_LISTING:
    case TH_TAG_MENU:
    case TH_TAG_META:
    case TH_TAG_NOBR:
    case TH_TAG_OL:
    case TH_TAG_P:
    case TH_TAG_PRE:
    case TH_TAG_RUBY:
    case TH_TAG_S:
    case TH_TAG_SMALL:
    case TH_TAG_SPAN:
    case TH_TAG_STRONG:
    case TH_TAG_STRIKE:
    case TH_TAG_SUB:
    case TH_TAG_SUP:
    case TH_TAG_TABLE:
    case TH_TAG_TT:
    case TH_TAG_U:
    case TH_TAG_UL:
    case TH_TAG_VAR:
        return 1;
    default:
        return 0;
    }
}

/* The case-folding buffer for an SVG element name; names longer than this skip
   the mixed-case adjustment (every adjusted SVG name is well under the cap). */
#define MAX_SVG_NAME 64

/* Insert an element into a foreign (SVG/MathML) namespace, adjusting an SVG
   element's name to its mixed-case spelling. */
static th_node *insert_foreign(th_tree *tree, const th_token *token, uint8_t ns) {
    th_node *node = insert_element(tree, token);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    node->ns = ns;
    /* HTML category flags do not transfer (an svg <tr> is not special); only
       the integration-point elements are in the spec's special category */
    int special = (ns == TH_NS_MATHML &&
                   (node->atom == TH_TAG_MI || node->atom == TH_TAG_MO || node->atom == TH_TAG_MN ||
                    node->atom == TH_TAG_MS || node->atom == TH_TAG_MTEXT || node->atom == TH_TAG_ANNOTATION_XML)) ||
                  (ns == TH_NS_SVG &&
                   (node->atom == TH_TAG_FOREIGNOBJECT || node->atom == TH_TAG_DESC || node->atom == TH_TAG_TITLE));
    node->tag_flags = special ? TH_TAG_SPECIAL : 0;
    /* "adjust MathML/SVG attributes": map a lowercased foreign attribute name back to
       its mixed case (definitionURL, viewBox, attributeName, ...). foreign_adjust_attr
       dispatches on the namespace. Done here, at construction, so the cased name lands
       in the tree (node.attrs) and every serializer emits it, not only the #document
       debug format. */
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        const char *mixed = foreign_adjust_attr(name, name_len, ns);
        if (mixed != NULL) {
            node->attrs[index].name_atom = intern_attr_dynamic(tree, mixed, (Py_ssize_t)strlen(mixed));
        }
    }
    if (ns == TH_NS_SVG && node->text != NULL) { /* GCOVR_EXCL_BR_LINE: an SVG element always has a tag name */
        char lower[MAX_SVG_NAME];
        if (node->text_len < (Py_ssize_t)sizeof(lower)) { /* GCOVR_EXCL_BR_LINE: SVG names are under 64 chars */
            for (Py_ssize_t index = 0; index < node->text_len; index++) {
                lower[index] = (char)node->text[index];
            }
            const char *mixed = svg_adjust_name(lower, node->text_len);
            if (mixed != NULL) {
                Py_ssize_t mlen = (Py_ssize_t)strlen(mixed);
                Py_UCS4 *adjusted = arena_alloc(tree, mlen * (Py_ssize_t)sizeof(Py_UCS4));
                if (adjusted != NULL) { /* GCOVR_EXCL_BR_LINE: NULL only on alloc failure */
                    for (Py_ssize_t index = 0; index < mlen; index++) {
                        adjusted[index] = (Py_UCS4)mixed[index];
                    }
                    node->text = adjusted;
                    node->text_len = mlen;
                }
            }
        }
    }
    return node;
}

static int token_has_attr(const th_token *token, const char *name) {
    Py_ssize_t nlen = (Py_ssize_t)strlen(name);
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_buf *aname = &token->attrs[index].name;
        if (aname->len == nlen && aname->kind == PyUnicode_1BYTE_KIND && memcmp(aname->data, name, (size_t)nlen) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Whether the token is processed by foreign-content rules rather than the
   current HTML insertion mode (the tree-construction dispatcher). */
static int use_foreign_rules(th_tree *tree, const th_token *token) {
    if (tree->open_len == 0) {
        return 0;
    }
    th_node *cur = current_node(tree);
    if (cur->ns == TH_NS_HTML) {
        return 0;
    }
    if (is_mathml_text_integration(cur)) {
        if (token->kind == TH_TEXT) {
            return 0;
        }
        if (token->kind == TH_START_TAG) {
            uint16_t atom = tok_atom(token);
            if (atom != TH_TAG_MGLYPH && atom != TH_TAG_MALIGNMARK) {
                return 0; /* HTML rules, except mglyph/malignmark which stay foreign */
            }
        }
    }
    if (cur->ns == TH_NS_MATHML && cur->atom == TH_TAG_ANNOTATION_XML && token->kind == TH_START_TAG &&
        tok_atom(token) == TH_TAG_SVG) {
        return 0;
    }
    if (is_html_integration(cur) && (token->kind == TH_START_TAG || token->kind == TH_TEXT)) {
        return 0;
    }
    return 1;
}

/* Process a token under foreign-content rules. Returns 1 when handled, 0 when an
   HTML breakout/end-tag match means the caller should run the HTML rules. */
static int foreign_step(th_tree *tree, const th_token *token) {
    if (token->kind == TH_TEXT) {
        Py_ssize_t len;
        Py_UCS4 *text = token_text(tree, token, &len);
        /* a U+0000 in foreign content becomes U+FFFD instead of being dropped;
           only characters that were neither NUL nor whitespace pin the body
           against a later frameset */
        for (Py_ssize_t index = 0; index < len; index++) {
            Py_UCS4 character = text[index];
            if (character == 0) {
                text[index] = 0xFFFD;
            } else if (!is_space(character)) {
                tree->frameset_ok = 0;
            }
        }
        insert_text(tree, text, len);
        return 1;
    }
    if (token->kind == TH_COMMENT) {
        insert_comment(tree, token, NULL);
        return 1;
    }
    if (token->kind == TH_DOCTYPE) {
        return 1; /* ignored in foreign content */
    }
    if (token->kind == TH_START_TAG) {
        uint16_t atom = token->atom;
        int breakout = is_foreign_breakout(atom) ||
                       (atom == TH_TAG_FONT && (token_has_attr(token, "color") || token_has_attr(token, "face") ||
                                                token_has_attr(token, "size")));
        if (breakout) {
            /* html is never foreign, so the breakout loop never empties the stack */
            while (tree->open_len > 0) { /* GCOVR_EXCL_BR_LINE */
                th_node *cur = current_node(tree);
                if (cur->ns == TH_NS_HTML || is_mathml_text_integration(cur) || is_html_integration(cur) ||
                    cur == tree->fragment_root) {
                    break; /* never pop the fragment's context root */
                }
                stack_pop(tree);
            }
            return 0; /* reprocess under HTML rules */
        }
        uint8_t ns = current_node(tree)->ns;
        th_node *node = insert_foreign(tree, token, ns);
        /* the inserted foreign node is NULL only on allocation failure */
        if (node != NULL && !token->self_closing) { /* GCOVR_EXCL_BR_LINE */
            stack_push(tree, node);
        }
        return 1;
    }
    /* "any other end tag", which includes </br> and </p>: walk up the stack, stop at
       the first HTML element and run the HTML rules there (the token is reprocessed
       with the foreign element still current, so a synthesized <br>/<p> lands inside
       the foreign root rather than as a sibling), or match a foreign element by
       (case-insensitive) name and pop to it; never pop the fragment context root */
    for (Py_ssize_t index = tree->open_len - 1; index >= 0; index--) { /* GCOVR_EXCL_BR_LINE */
        th_node *node = tree->open[index];
        if (node->ns == TH_NS_HTML) {
            /* an HTML element, including an HTML-namespace fragment root, is where
               the walk hands the token back to the current insertion mode */
            return 0;
        }
        if (node == tree->fragment_root) {
            return 1; /* a foreign context root: its own end tag is ignored, never popped */
        }
        if (name_matches(node, token, 1)) {
            while (tree->open_len > index) {
                stack_pop(tree);
            }
            return 1;
        }
    } /* GCOVR_EXCL_BR_LINE: the walk always reaches the fragment root or an html element first */
    return 1; /* GCOVR_EXCL_LINE: same -- the foreign end-tag walk never falls through */
}

#endif /* TURBOHTML_DOM_FOREIGN_H */
