/* The CSS Object Model cascade and getComputedStyle (issue #546). Three entry
   points share one parser: _css_parse_declarations turns a declaration block (an
   inline style="" or a rule body) into (property, value, important) triples;
   _css_parse_rules turns a whole stylesheet into (selectorText, declarations)
   rules for the CSSStyleSheet surface; and _css_computed_style resolves the CSS
   Cascade (https://www.w3.org/TR/css-cascade/) for one element -- collecting every
   <style> sheet plus the element chain's inline style, matching the reused native
   selector engine, ordering by importance, the style attribute, specificity, then
   source order, then folding in inheritance and each property's initial value.

   The returned value is the *computed* value in the cascade sense (the winning
   specified value after inherit/initial/unset are resolved). It is not the *used*
   value: turbohtml renders nothing, so no layout runs and lengths, percentages,
   relative units, and system colors are returned as written rather than resolved
   to pixels -- the same boundary jsdom and cssstyle draw. Every selector match and
   the cascade resolution run under the caller's per-tree critical section. */

#include "data/attr_atom.h"
#include "dom/nodes.h"
#include "css/select/selector.h"

#include <string.h>

/* One CSS longhand property the cascade resolves: its lowercase name, whether it
   inherits (CSS Cascade §7), and its initial value (each property's spec "Initial:"
   line). getComputedStyle returns a value for every entry, in this order. Only
   longhands appear here; shorthands are expanded into these before the cascade. */
typedef struct {
    const char *name;
    int inherited;
    const char *initial;
} css_prop_meta;

/* clang-format off */
enum css_prop_id {
    PROP_COLOR, PROP_FONT_SIZE, PROP_FONT_STYLE, PROP_FONT_WEIGHT, PROP_FONT_VARIANT,
    PROP_LINE_HEIGHT, PROP_TEXT_ALIGN, PROP_TEXT_INDENT, PROP_TEXT_TRANSFORM,
    PROP_LETTER_SPACING, PROP_WORD_SPACING, PROP_WHITE_SPACE, PROP_VISIBILITY,
    PROP_LIST_STYLE_TYPE, PROP_LIST_STYLE_POSITION, PROP_CURSOR, PROP_DIRECTION,
    PROP_CAPTION_SIDE,
    PROP_DISPLAY, PROP_POSITION, PROP_TOP, PROP_RIGHT, PROP_BOTTOM, PROP_LEFT,
    PROP_FLOAT, PROP_CLEAR, PROP_WIDTH, PROP_HEIGHT, PROP_MIN_WIDTH, PROP_MIN_HEIGHT,
    PROP_MAX_WIDTH, PROP_MAX_HEIGHT,
    PROP_MARGIN_TOP, PROP_MARGIN_RIGHT, PROP_MARGIN_BOTTOM, PROP_MARGIN_LEFT,
    PROP_PADDING_TOP, PROP_PADDING_RIGHT, PROP_PADDING_BOTTOM, PROP_PADDING_LEFT,
    PROP_BORDER_TOP_WIDTH, PROP_BORDER_RIGHT_WIDTH, PROP_BORDER_BOTTOM_WIDTH, PROP_BORDER_LEFT_WIDTH,
    PROP_BORDER_TOP_STYLE, PROP_BORDER_RIGHT_STYLE, PROP_BORDER_BOTTOM_STYLE, PROP_BORDER_LEFT_STYLE,
    PROP_BORDER_TOP_COLOR, PROP_BORDER_RIGHT_COLOR, PROP_BORDER_BOTTOM_COLOR, PROP_BORDER_LEFT_COLOR,
    PROP_BACKGROUND_COLOR, PROP_BACKGROUND_IMAGE, PROP_OPACITY, PROP_Z_INDEX,
    PROP_OVERFLOW_X, PROP_OVERFLOW_Y, PROP_VERTICAL_ALIGN, PROP_BOX_SIZING,
    PROP_OUTLINE_WIDTH, PROP_OUTLINE_STYLE, PROP_OUTLINE_COLOR,
    NUM_PROPS
};

static const css_prop_meta CSS_PROPS[NUM_PROPS] = {
    {"color", 1, "canvastext"},
    {"font-size", 1, "medium"},
    {"font-style", 1, "normal"},
    {"font-weight", 1, "normal"},
    {"font-variant", 1, "normal"},
    {"line-height", 1, "normal"},
    {"text-align", 1, "start"},
    {"text-indent", 1, "0"},
    {"text-transform", 1, "none"},
    {"letter-spacing", 1, "normal"},
    {"word-spacing", 1, "normal"},
    {"white-space", 1, "normal"},
    {"visibility", 1, "visible"},
    {"list-style-type", 1, "disc"},
    {"list-style-position", 1, "outside"},
    {"cursor", 1, "auto"},
    {"direction", 1, "ltr"},
    {"caption-side", 1, "top"},
    {"display", 0, "inline"},
    {"position", 0, "static"},
    {"top", 0, "auto"},
    {"right", 0, "auto"},
    {"bottom", 0, "auto"},
    {"left", 0, "auto"},
    {"float", 0, "none"},
    {"clear", 0, "none"},
    {"width", 0, "auto"},
    {"height", 0, "auto"},
    {"min-width", 0, "auto"},
    {"min-height", 0, "auto"},
    {"max-width", 0, "none"},
    {"max-height", 0, "none"},
    {"margin-top", 0, "0"},
    {"margin-right", 0, "0"},
    {"margin-bottom", 0, "0"},
    {"margin-left", 0, "0"},
    {"padding-top", 0, "0"},
    {"padding-right", 0, "0"},
    {"padding-bottom", 0, "0"},
    {"padding-left", 0, "0"},
    {"border-top-width", 0, "medium"},
    {"border-right-width", 0, "medium"},
    {"border-bottom-width", 0, "medium"},
    {"border-left-width", 0, "medium"},
    {"border-top-style", 0, "none"},
    {"border-right-style", 0, "none"},
    {"border-bottom-style", 0, "none"},
    {"border-left-style", 0, "none"},
    {"border-top-color", 0, "currentcolor"},
    {"border-right-color", 0, "currentcolor"},
    {"border-bottom-color", 0, "currentcolor"},
    {"border-left-color", 0, "currentcolor"},
    {"background-color", 0, "transparent"},
    {"background-image", 0, "none"},
    {"opacity", 0, "1"},
    {"z-index", 0, "auto"},
    {"overflow-x", 0, "visible"},
    {"overflow-y", 0, "visible"},
    {"vertical-align", 0, "baseline"},
    {"box-sizing", 0, "content-box"},
    {"outline-width", 0, "medium"},
    {"outline-style", 0, "none"},
    {"outline-color", 0, "currentcolor"},
};

/* CSS_PROPS ids ordered by their name, so css_prop_id can binary-search a declaration
   name instead of scanning all NUM_PROPS rows. Kept in sync with CSS_PROPS by hand: a
   new property must be inserted here at its lexicographic position. */
static const uint8_t CSS_PROPS_SORTED[NUM_PROPS] = {
    PROP_BACKGROUND_COLOR, PROP_BACKGROUND_IMAGE, PROP_BORDER_BOTTOM_COLOR, PROP_BORDER_BOTTOM_STYLE, PROP_BORDER_BOTTOM_WIDTH, PROP_BORDER_LEFT_COLOR,
    PROP_BORDER_LEFT_STYLE, PROP_BORDER_LEFT_WIDTH, PROP_BORDER_RIGHT_COLOR, PROP_BORDER_RIGHT_STYLE, PROP_BORDER_RIGHT_WIDTH, PROP_BORDER_TOP_COLOR,
    PROP_BORDER_TOP_STYLE, PROP_BORDER_TOP_WIDTH, PROP_BOTTOM, PROP_BOX_SIZING, PROP_CAPTION_SIDE, PROP_CLEAR,
    PROP_COLOR, PROP_CURSOR, PROP_DIRECTION, PROP_DISPLAY, PROP_FLOAT, PROP_FONT_SIZE,
    PROP_FONT_STYLE, PROP_FONT_VARIANT, PROP_FONT_WEIGHT, PROP_HEIGHT, PROP_LEFT, PROP_LETTER_SPACING,
    PROP_LINE_HEIGHT, PROP_LIST_STYLE_POSITION, PROP_LIST_STYLE_TYPE, PROP_MARGIN_BOTTOM, PROP_MARGIN_LEFT, PROP_MARGIN_RIGHT,
    PROP_MARGIN_TOP, PROP_MAX_HEIGHT, PROP_MAX_WIDTH, PROP_MIN_HEIGHT, PROP_MIN_WIDTH, PROP_OPACITY,
    PROP_OUTLINE_COLOR, PROP_OUTLINE_STYLE, PROP_OUTLINE_WIDTH, PROP_OVERFLOW_X, PROP_OVERFLOW_Y, PROP_PADDING_BOTTOM,
    PROP_PADDING_LEFT, PROP_PADDING_RIGHT, PROP_PADDING_TOP, PROP_POSITION, PROP_RIGHT, PROP_TEXT_ALIGN,
    PROP_TEXT_INDENT, PROP_TEXT_TRANSFORM, PROP_TOP, PROP_VERTICAL_ALIGN, PROP_VISIBILITY, PROP_WHITE_SPACE,
    PROP_WIDTH, PROP_WORD_SPACING, PROP_Z_INDEX,
};

/* A shorthand whose value distributes across four physical sides (margin, padding,
   the border-width/style/color families) using the 1-to-4 rule, or across two axes
   (overflow). sides holds the target longhand ids in top,right,bottom,left order
   (or x,y for a 2-axis shorthand); axes is 4 or 2. */
typedef struct {
    const char *name;
    int axes;
    int sides[4];
} css_shorthand;

static const css_shorthand CSS_SHORTHANDS[] = {
    {"margin", 4, {PROP_MARGIN_TOP, PROP_MARGIN_RIGHT, PROP_MARGIN_BOTTOM, PROP_MARGIN_LEFT}},
    {"padding", 4, {PROP_PADDING_TOP, PROP_PADDING_RIGHT, PROP_PADDING_BOTTOM, PROP_PADDING_LEFT}},
    {"border-width", 4, {PROP_BORDER_TOP_WIDTH, PROP_BORDER_RIGHT_WIDTH, PROP_BORDER_BOTTOM_WIDTH, PROP_BORDER_LEFT_WIDTH}},
    {"border-style", 4, {PROP_BORDER_TOP_STYLE, PROP_BORDER_RIGHT_STYLE, PROP_BORDER_BOTTOM_STYLE, PROP_BORDER_LEFT_STYLE}},
    {"border-color", 4, {PROP_BORDER_TOP_COLOR, PROP_BORDER_RIGHT_COLOR, PROP_BORDER_BOTTOM_COLOR, PROP_BORDER_LEFT_COLOR}},
    {"overflow", 2, {PROP_OVERFLOW_X, PROP_OVERFLOW_Y, 0, 0}},
};
/* clang-format on */

#define NUM_SHORTHANDS ((int)(sizeof(CSS_SHORTHANDS) / sizeof(CSS_SHORTHANDS[0])))

/* A <line-width> || <line-style> || <color> shorthand (CSS Backgrounds & Borders 3 §4
   for border and each border-<side>, CSS UI 4 §5 for outline). Its up-to-three
   components come in any order and any may be omitted, so each is classified by shape
   rather than position; an omitted component resets its longhand to that property's
   initial value. targets lists every longhand the shorthand writes, tagged with which
   component supplies it -- one width/style/color triple for a single side, four for the
   whole-box border. */
enum css_component_kind { COMPONENT_WIDTH, COMPONENT_STYLE, COMPONENT_COLOR };

typedef struct {
    int kind;
    int prop;
} css_box_target;

typedef struct {
    const char *name;
    int target_count;
    css_box_target targets[12];
} css_box_shorthand;

/* clang-format off */
static const css_box_shorthand CSS_BOX_SHORTHANDS[] = {
    {"border", 12, {
        {COMPONENT_WIDTH, PROP_BORDER_TOP_WIDTH},    {COMPONENT_STYLE, PROP_BORDER_TOP_STYLE},    {COMPONENT_COLOR, PROP_BORDER_TOP_COLOR},
        {COMPONENT_WIDTH, PROP_BORDER_RIGHT_WIDTH},  {COMPONENT_STYLE, PROP_BORDER_RIGHT_STYLE},  {COMPONENT_COLOR, PROP_BORDER_RIGHT_COLOR},
        {COMPONENT_WIDTH, PROP_BORDER_BOTTOM_WIDTH}, {COMPONENT_STYLE, PROP_BORDER_BOTTOM_STYLE}, {COMPONENT_COLOR, PROP_BORDER_BOTTOM_COLOR},
        {COMPONENT_WIDTH, PROP_BORDER_LEFT_WIDTH},   {COMPONENT_STYLE, PROP_BORDER_LEFT_STYLE},   {COMPONENT_COLOR, PROP_BORDER_LEFT_COLOR},
    }},
    {"border-top", 3, {
        {COMPONENT_WIDTH, PROP_BORDER_TOP_WIDTH}, {COMPONENT_STYLE, PROP_BORDER_TOP_STYLE}, {COMPONENT_COLOR, PROP_BORDER_TOP_COLOR},
    }},
    {"border-right", 3, {
        {COMPONENT_WIDTH, PROP_BORDER_RIGHT_WIDTH}, {COMPONENT_STYLE, PROP_BORDER_RIGHT_STYLE}, {COMPONENT_COLOR, PROP_BORDER_RIGHT_COLOR},
    }},
    {"border-bottom", 3, {
        {COMPONENT_WIDTH, PROP_BORDER_BOTTOM_WIDTH}, {COMPONENT_STYLE, PROP_BORDER_BOTTOM_STYLE}, {COMPONENT_COLOR, PROP_BORDER_BOTTOM_COLOR},
    }},
    {"border-left", 3, {
        {COMPONENT_WIDTH, PROP_BORDER_LEFT_WIDTH}, {COMPONENT_STYLE, PROP_BORDER_LEFT_STYLE}, {COMPONENT_COLOR, PROP_BORDER_LEFT_COLOR},
    }},
    {"outline", 3, {
        {COMPONENT_WIDTH, PROP_OUTLINE_WIDTH}, {COMPONENT_STYLE, PROP_OUTLINE_STYLE}, {COMPONENT_COLOR, PROP_OUTLINE_COLOR},
    }},
};
/* clang-format on */

#define NUM_BOX_SHORTHANDS ((int)(sizeof(CSS_BOX_SHORTHANDS) / sizeof(CSS_BOX_SHORTHANDS[0])))

static int css_is_ws(Py_UCS4 ch) {
    /* a loop over the literals, not a chained ||, so branch coverage does not hinge on
       seeing every one of the five whitespace code points (issue-era chained-|| gaps) */
    for (const char *space = " \t\n\r\f"; *space != '\0'; space++) {
        if (ch == (Py_UCS4)*space) {
            return 1;
        }
    }
    return 0;
}

static Py_UCS4 css_lower(Py_UCS4 ch) {
    return (ch >= 'A' && ch <= 'Z') ? ch + 32 : ch;
}

/* Case-insensitive (ASCII) compare of a code-point slice against a C string. */
static int css_slice_ci_eq(const Py_UCS4 *data, Py_ssize_t len, const char *word) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (word[index] == '\0' || css_lower(data[index]) != (Py_UCS4)word[index]) {
            return 0;
        }
    }
    return word[len] == '\0';
}

/* Three-way ASCII case-insensitive compare of a code-point slice against a lowercase C
   string, ordering the lowercased slice lexicographically: <0, 0, or >0. */
static int css_slice_ci_cmp(const Py_UCS4 *data, Py_ssize_t len, const char *word) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (word[index] == '\0') {
            return 1;
        }
        Py_UCS4 lowered = css_lower(data[index]);
        if (lowered != (Py_UCS4)word[index]) {
            return lowered < (Py_UCS4)word[index] ? -1 : 1;
        }
    }
    return word[len] == '\0' ? 0 : -1;
}

/* The longhand id for a property name slice, or -1 when it is not a property the
   cascade tracks. Binary search over CSS_PROPS_SORTED. */
static int css_prop_id(const Py_UCS4 *name, Py_ssize_t len) {
    int low = 0;
    int high = NUM_PROPS - 1;
    while (low <= high) {
        int mid = (low + high) / 2;
        int prop = CSS_PROPS_SORTED[mid];
        int order = css_slice_ci_cmp(name, len, CSS_PROPS[prop].name);
        if (order == 0) {
            return prop;
        }
        if (order < 0) {
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    return -1;
}

/* A code-point slice; the value a component of a grammar shorthand carries, or the
   fallback an omitted component resets its longhand to. */
typedef struct {
    const Py_UCS4 *value;
    Py_ssize_t len;
} css_span;

/* clang-format off */
static const Py_UCS4 CSS_INITIAL_WIDTH[] = {'m', 'e', 'd', 'i', 'u', 'm'};
static const Py_UCS4 CSS_INITIAL_STYLE[] = {'n', 'o', 'n', 'e'};
static const Py_UCS4 CSS_INITIAL_COLOR[] = {'c', 'u', 'r', 'r', 'e', 'n', 't', 'c', 'o', 'l', 'o', 'r'};
/* clang-format on */

/* The initial value an omitted component of a grammar shorthand resets its longhand to,
   indexed by css_component_kind; these mirror the border/outline longhands' initials. */
static const css_span CSS_COMPONENT_INITIAL[] = {
    {CSS_INITIAL_WIDTH, (Py_ssize_t)(sizeof(CSS_INITIAL_WIDTH) / sizeof(Py_UCS4))},
    {CSS_INITIAL_STYLE, (Py_ssize_t)(sizeof(CSS_INITIAL_STYLE) / sizeof(Py_UCS4))},
    {CSS_INITIAL_COLOR, (Py_ssize_t)(sizeof(CSS_INITIAL_COLOR) / sizeof(Py_UCS4))},
};

/* Classify one component of a <line-width> || <line-style> || <color> shorthand: a
   line-style keyword (plus outline's auto), a width keyword or a length (a numeric-led
   token), else a color -- the grammar's only remaining alternative. */
static int css_classify_component(const Py_UCS4 *data, Py_ssize_t len) {
    static const char *const styles[] = {"none",   "hidden", "dotted", "dashed", "solid", "auto",
                                         "double", "groove", "ridge",  "inset",  "outset"};
    for (size_t index = 0; index < sizeof(styles) / sizeof(styles[0]); index++) {
        if (css_slice_ci_eq(data, len, styles[index])) {
            return COMPONENT_STYLE;
        }
    }
    static const char *const widths[] = {"thin", "medium", "thick"};
    for (size_t index = 0; index < sizeof(widths) / sizeof(widths[0]); index++) {
        if (css_slice_ci_eq(data, len, widths[index])) {
            return COMPONENT_WIDTH;
        }
    }
    Py_UCS4 first = data[0];
    if (first >= '0' && first <= '9') {
        return COMPONENT_WIDTH;
    }
    /* a loop over the sign/dot leads, not a chained ||, so clang cannot fold the branch */
    for (const char *lead = ".+-"; *lead != '\0'; lead++) {
        if (first == (Py_UCS4)*lead) {
            return COMPONENT_WIDTH;
        }
    }
    return COMPONENT_COLOR;
}

/* Copy src stripping CSS block comments, honoring '' and "" strings (a comment
   marker inside a string is literal, and a backslash escapes the next character).
   Each comment becomes one space so it stays a token boundary. Returns an owned
   buffer of *out_len code points, or NULL on allocation failure. */
static Py_UCS4 *css_strip_comments(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_len) {
    Py_UCS4 *out = PyMem_Malloc((size_t)(len + 1) * sizeof(Py_UCS4));
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t write = 0;
    Py_ssize_t pos = 0;
    while (pos < len) {
        Py_UCS4 ch = src[pos];
        if (ch == '"' || ch == '\'') {
            Py_UCS4 quote = ch;
            out[write++] = ch;
            pos++;
            while (pos < len) {
                out[write++] = src[pos];
                if (src[pos] == '\\' && pos + 1 < len) {
                    out[write++] = src[pos + 1];
                    pos += 2;
                    continue;
                }
                if (src[pos] == quote) {
                    pos++;
                    break;
                }
                pos++;
            }
            continue;
        }
        if (ch == '/' && pos + 1 < len && src[pos + 1] == '*') {
            pos += 2;
            while (pos + 1 < len && !(src[pos] == '*' && src[pos + 1] == '/')) {
                pos++;
            }
            pos = pos + 1 < len ? pos + 2 : len;
            out[write++] = ' ';
            continue;
        }
        out[write++] = ch;
        pos++;
    }
    *out_len = write;
    return out;
}

/* One parsed declaration: slices into a cleaned buffer plus the !important flag. */
typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    const Py_UCS4 *value;
    Py_ssize_t value_len;
    int important;
} css_decl;

/* Trim leading and trailing whitespace from a slice in place. */
static void css_trim(const Py_UCS4 **data, Py_ssize_t *len) {
    Py_ssize_t start = 0;
    Py_ssize_t stop = *len;
    while (start < stop && css_is_ws((*data)[start])) {
        start++;
    }
    while (stop > start && css_is_ws((*data)[stop - 1])) {
        stop--;
    }
    *data += start;
    *len = stop - start;
}

/* Strip a trailing "! important" (case-insensitive, optional inner whitespace) from
   a value slice, returning 1 when it was present. */
static int css_take_important(const Py_UCS4 **value, Py_ssize_t *len) {
    Py_ssize_t stop = *len;
    Py_ssize_t cursor = stop;
    while (cursor > 0 && !css_is_ws((*value)[cursor - 1]) && (*value)[cursor - 1] != '!') {
        cursor--;
    }
    if (!css_slice_ci_eq(*value + cursor, stop - cursor, "important")) {
        return 0;
    }
    while (cursor > 0 && css_is_ws((*value)[cursor - 1])) {
        cursor--;
    }
    if (cursor == 0 || (*value)[cursor - 1] != '!') {
        return 0;
    }
    *len = cursor - 1;
    css_trim(value, len);
    return 1;
}

/* The offset of the top-level delimiter ch at or after pos in [pos, end), skipping
   strings and () / [] nesting, or end when none remains. */
static Py_ssize_t css_scan_to(const Py_UCS4 *data, Py_ssize_t pos, Py_ssize_t end, Py_UCS4 ch) {
    int depth = 0;
    while (pos < end) {
        Py_UCS4 cur = data[pos];
        if (cur == '"' || cur == '\'') {
            Py_UCS4 quote = cur;
            pos++;
            while (pos < end) {
                if (data[pos] == '\\' && pos + 1 < end) {
                    pos += 2;
                    continue;
                }
                if (data[pos] == quote) {
                    break;
                }
                pos++;
            }
        } else if (cur == '(' || cur == '[') {
            depth++;
        } else if (cur == ')' || cur == ']') {
            if (depth > 0) {
                depth--;
            }
        } else if (cur == ch && depth == 0) {
            return pos;
        }
        pos++;
    }
    return end;
}

/* The offset just past the '}' matching the '{' at pos, tracking nested braces (so
   an @media block's inner rule blocks are skipped whole) and skipping strings, or
   end when the block is unterminated. */
static Py_ssize_t css_match_brace(const Py_UCS4 *data, Py_ssize_t pos, Py_ssize_t end) {
    int depth = 0;
    while (pos < end) {
        Py_UCS4 cur = data[pos];
        if (cur == '"' || cur == '\'') {
            Py_UCS4 quote = cur;
            pos++;
            while (pos < end) {
                if (data[pos] == '\\' && pos + 1 < end) {
                    pos += 2;
                    continue;
                }
                if (data[pos] == quote) {
                    break;
                }
                pos++;
            }
        } else if (cur == '{') {
            depth++;
        } else if (cur == '}') {
            depth--;
            if (depth == 0) {
                return pos + 1;
            }
        }
        pos++;
    }
    return end;
}

/* Parse the declarations in the cleaned block [start, end) into decls, growing the
   array as needed. Returns the count, or -1 on allocation failure. */
static Py_ssize_t css_parse_block(const Py_UCS4 *data, Py_ssize_t start, Py_ssize_t end, css_decl **out,
                                  Py_ssize_t *capacity) {
    Py_ssize_t count = 0;
    Py_ssize_t pos = start;
    while (pos < end) {
        Py_ssize_t semi = css_scan_to(data, pos, end, ';');
        Py_ssize_t colon = css_scan_to(data, pos, semi, ':');
        if (colon < semi) {
            const Py_UCS4 *name = data + pos;
            Py_ssize_t name_len = colon - pos;
            css_trim(&name, &name_len);
            const Py_UCS4 *value = data + colon + 1;
            Py_ssize_t value_len = semi - colon - 1;
            css_trim(&value, &value_len);
            int important = css_take_important(&value, &value_len);
            if (name_len > 0 && value_len > 0) {
                if (count == *capacity) {
                    Py_ssize_t grown = *capacity == 0 ? 8 : *capacity * 2;
                    css_decl *bigger = PyMem_Realloc(*out, (size_t)grown * sizeof(css_decl));
                    if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    *out = bigger;
                    *capacity = grown;
                }
                (*out)[count++] = (css_decl){name, name_len, value, value_len, important};
            }
        }
        pos = semi + 1;
    }
    return count;
}

/* The end of the whitespace-delimited component starting at pos, treating a
   parenthesised group (rgb(...), calc(...)) as one component so its inner spaces do
   not split it. Used to break a shorthand value into its per-side parts. */
static Py_ssize_t css_component_end(const Py_UCS4 *data, Py_ssize_t pos, Py_ssize_t end) {
    int depth = 0;
    while (pos < end) {
        Py_UCS4 ch = data[pos];
        if (ch == '(') {
            depth++;
        } else if (ch == ')') {
            if (depth > 0) {
                depth--;
            }
        } else if (depth == 0 && css_is_ws(ch)) {
            break;
        }
        pos++;
    }
    return pos;
}

static PyObject *css_slice_str(const Py_UCS4 *data, Py_ssize_t len) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, len);
}

/* Build the ((name, value, important), ...) tuple for a parsed declaration list. */
static PyObject *css_decls_to_tuple(const css_decl *decls, Py_ssize_t count) {
    PyObject *result = PyTuple_New(count);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *name = css_slice_str(decls[index].name, decls[index].name_len);
        PyObject *value = css_slice_str(decls[index].value, decls[index].value_len);
        /* the whole triple must survive to be stored; any half-built member is freed here */
        if (name == NULL || value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            Py_XDECREF(name);                /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(value);               /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result);               /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *triple = PyTuple_Pack(3, name, value, decls[index].important ? Py_True : Py_False);
        Py_DECREF(name);
        Py_DECREF(value);
        if (triple == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyTuple_SET_ITEM(result, index, triple);
    }
    return result;
}

PyObject *turbohtml_css_parse_declarations(PyObject *module, PyObject *text) {
    (void)module;
    if (!PyUnicode_Check(text)) {
        PyErr_SetString(PyExc_TypeError, "declaration text must be a str");
        return NULL;
    }
    Py_UCS4 *source = PyUnicode_AsUCS4Copy(text);
    if (source == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t clean_len = 0;
    Py_UCS4 *clean = css_strip_comments(source, PyUnicode_GET_LENGTH(text), &clean_len);
    PyMem_Free(source);
    if (clean == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    css_decl *decls = NULL;
    Py_ssize_t capacity = 0;
    Py_ssize_t count = css_parse_block(clean, 0, clean_len, &decls, &capacity);
    PyObject *result;
    if (count < 0) {               /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        result = PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    } else {                       /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        result = css_decls_to_tuple(decls, count);
    }
    PyMem_Free(decls);
    PyMem_Free(clean);
    return result;
}

/* One style rule: the selector prelude slice and its declaration array. */
typedef struct {
    const Py_UCS4 *selector;
    Py_ssize_t selector_len;
    css_decl *decls;
    Py_ssize_t decl_count;
} css_rule;

static void css_free_rules(css_rule *rules, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        PyMem_Free(rules[index].decls);
    }
    PyMem_Free(rules);
}

/* Parse the cleaned stylesheet [0, len) into style rules. At-rules (a top-level
   token starting '@') are skipped whole, including any block. Returns the rules and
   sets *out_count, or NULL on allocation failure (with *out_count negative). */
static css_rule *css_parse_sheet(const Py_UCS4 *data, Py_ssize_t len, Py_ssize_t *out_count) {
    css_rule *rules = NULL;
    Py_ssize_t count = 0;
    Py_ssize_t capacity = 0;
    Py_ssize_t pos = 0;
    while (pos < len) {
        while (pos < len && css_is_ws(data[pos])) {
            pos++;
        }
        if (pos >= len) {
            break;
        }
        Py_ssize_t brace = css_scan_to(data, pos, len, '{');
        if (data[pos] == '@') {
            /* an at-rule: a statement one (@import, @charset) ends at ';', a block one
               (@media, @supports) at its balanced '}'; either is skipped whole */
            Py_ssize_t semi = css_scan_to(data, pos, len, ';');
            if (brace >= len || semi < brace) {
                pos = semi < len ? semi + 1 : len;
                continue;
            }
            pos = css_match_brace(data, brace, len);
            continue;
        }
        if (brace >= len) {
            break;
        }
        Py_ssize_t close = css_scan_to(data, brace + 1, len, '}');
        const Py_UCS4 *selector = data + pos;
        Py_ssize_t selector_len = brace - pos;
        css_trim(&selector, &selector_len);
        css_decl *decls = NULL;
        Py_ssize_t decl_cap = 0;
        Py_ssize_t decl_count = css_parse_block(data, brace + 1, close, &decls, &decl_cap);
        if (decl_count < 0) {             /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(decls);            /* GCOVR_EXCL_LINE: allocation-failure path */
            css_free_rules(rules, count); /* GCOVR_EXCL_LINE: allocation-failure path */
            *out_count = -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (selector_len > 0) {
            if (count == capacity) {
                Py_ssize_t grown = capacity == 0 ? 8 : capacity * 2;
                css_rule *bigger = PyMem_Realloc(rules, (size_t)grown * sizeof(css_rule));
                if (bigger == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    PyMem_Free(decls); /* GCOVR_EXCL_LINE: allocation-failure path */
                    css_free_rules(rules, count); /* GCOVR_EXCL_LINE: allocation-failure path */
                    *out_count = -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
                    return NULL;                  /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                rules = bigger;
                capacity = grown;
            }
            rules[count++] = (css_rule){selector, selector_len, decls, decl_count};
        } else {
            PyMem_Free(decls);
        }
        pos = close < len ? close + 1 : len;
    }
    *out_count = count;
    return rules;
}

PyObject *turbohtml_css_parse_rules(PyObject *module, PyObject *text) {
    (void)module;
    if (!PyUnicode_Check(text)) {
        PyErr_SetString(PyExc_TypeError, "stylesheet text must be a str");
        return NULL;
    }
    Py_UCS4 *source = PyUnicode_AsUCS4Copy(text);
    if (source == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t clean_len = 0;
    Py_UCS4 *clean = css_strip_comments(source, PyUnicode_GET_LENGTH(text), &clean_len);
    PyMem_Free(source);
    if (clean == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t rule_count = 0;
    css_rule *rules = css_parse_sheet(clean, clean_len, &rule_count);
    if (rule_count < 0) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(clean);       /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyTuple_New(rule_count);
    if (result == NULL) {                  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        css_free_rules(rules, rule_count); /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(clean);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < rule_count; index++) {
        PyObject *selector = css_slice_str(rules[index].selector, rules[index].selector_len);
        PyObject *decls = css_decls_to_tuple(rules[index].decls, rules[index].decl_count);
        /* both slices come from the same rule and fail only on allocation */
        PyObject *rule =
            selector != NULL && decls != NULL ? PyTuple_Pack(2, selector, decls) : NULL; /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(selector);
        Py_XDECREF(decls);
        if (rule == NULL) {                    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(result);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            css_free_rules(rules, rule_count); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(clean);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyTuple_SET_ITEM(result, index, rule);
    }
    css_free_rules(rules, rule_count);
    PyMem_Free(clean);
    return result;
}

/* The winner so far for one longhand in one element's cascade: its value slice and
   the ranking that beat every earlier candidate. */
typedef struct {
    const Py_UCS4 *value;
    Py_ssize_t value_len;
    int important;
    int inline_style;
    int spec_a;
    int spec_b;
    int spec_c;
    long order;
    int set;
} css_slot;

/* Whether a new declaration outranks the slot's current winner (CSS Cascade §6.4.4,
   author origin only): importance first, then the style attribute, then specificity,
   then source order. */
static int css_outranks(const css_slot *slot, int important, int inline_style, int spec_a, int spec_b, int spec_c,
                        long order) {
    if (!slot->set) {
        return 1;
    }
    if (important != slot->important) {
        return important > slot->important;
    }
    if (inline_style != slot->inline_style) {
        return inline_style > slot->inline_style;
    }
    if (spec_a != slot->spec_a) {
        return spec_a > slot->spec_a;
    }
    if (spec_b != slot->spec_b) {
        return spec_b > slot->spec_b;
    }
    if (spec_c != slot->spec_c) {
        return spec_c > slot->spec_c;
    }
    return order >= slot->order;
}

/* Store value as prop's cascade winner when it outranks the current one, carrying the
   ranking (importance, style attribute, specificity, source order) from rank. */
static void css_set_slot(css_slot slots[NUM_PROPS], int prop, const Py_UCS4 *value, Py_ssize_t len,
                         const css_slot *rank) {
    css_slot *slot = &slots[prop];
    if (css_outranks(slot, rank->important, rank->inline_style, rank->spec_a, rank->spec_b, rank->spec_c,
                     rank->order)) {
        *slot = (css_slot){
            value, len, rank->important, rank->inline_style, rank->spec_a, rank->spec_b, rank->spec_c, rank->order, 1};
    }
}

/* Expand a distributive shorthand (margin, padding, the border-width/style/color
   families, overflow) across its sides by the 1-to-4 rule. Returns 1 when decl names
   one -- even if invalid, which sets nothing -- and 0 when it names none. */
static int css_apply_distributive(css_slot slots[NUM_PROPS], const css_decl *decl, const css_slot *rank) {
    for (int index = 0; index < NUM_SHORTHANDS; index++) {
        const css_shorthand *shorthand = &CSS_SHORTHANDS[index];
        if (!css_slice_ci_eq(decl->name, decl->name_len, shorthand->name)) {
            continue;
        }
        const Py_UCS4 *parts[4];
        Py_ssize_t part_lens[4];
        int part_count = 0;
        /* the value is trimmed, so pos rests on a component's first (non-space) code
           point at the top of each pass, and the trailing skip lands on the next one
           or on the end */
        Py_ssize_t pos = 0;
        while (pos < decl->value_len && part_count < 4) {
            Py_ssize_t stop = css_component_end(decl->value, pos, decl->value_len);
            parts[part_count] = decl->value + pos;
            part_lens[part_count] = stop - pos;
            part_count++;
            pos = stop;
            while (pos < decl->value_len && css_is_ws(decl->value[pos])) {
                pos++;
            }
        }
        /* more components than sides (or than the 4 read) leaves a token unconsumed:
           the shorthand is invalid and sets nothing */
        if (part_count > shorthand->axes || pos < decl->value_len) {
            return 1;
        }
        for (int side = 0; side < shorthand->axes; side++) {
            /* the 1-to-4 rule: 1 value fills all, 2 splits opposite pairs, 3 sets the
               fourth from the second, 4 is verbatim; overflow's 2 axes mirror the same */
            int source = side;
            if (part_count == 1) {
                source = 0;
            } else if (part_count == 2) {
                source = side % 2;
            } else if (part_count == 3 && side == 3) {
                source = 1;
            }
            css_set_slot(slots, shorthand->sides[side], parts[source], part_lens[source], rank);
        }
        return 1;
    }
    return 0;
}

/* Expand a grammar shorthand (border, a border-<side>, outline) whose components come
   in any order and any may be omitted. Each component is classified by shape; a repeat
   of one kind makes the whole shorthand invalid (it sets nothing). Every longhand is
   written -- an omitted component resets its longhand to the property's initial value,
   so this shorthand overrides an earlier longhand it does not restate. Returns 1 when
   decl names such a shorthand, 0 when it names none. */
static int css_apply_box(css_slot slots[NUM_PROPS], const css_decl *decl, const css_slot *rank) {
    for (int index = 0; index < NUM_BOX_SHORTHANDS; index++) {
        const css_box_shorthand *shorthand = &CSS_BOX_SHORTHANDS[index];
        if (!css_slice_ci_eq(decl->name, decl->name_len, shorthand->name)) {
            continue;
        }
        css_span components[3] = {CSS_COMPONENT_INITIAL[0], CSS_COMPONENT_INITIAL[1], CSS_COMPONENT_INITIAL[2]};
        int seen[3] = {0, 0, 0};
        Py_ssize_t pos = 0;
        while (pos < decl->value_len) {
            Py_ssize_t stop = css_component_end(decl->value, pos, decl->value_len);
            int kind = css_classify_component(decl->value + pos, stop - pos);
            if (seen[kind]) {
                return 1;
            }
            seen[kind] = 1;
            components[kind] = (css_span){decl->value + pos, stop - pos};
            pos = stop;
            while (pos < decl->value_len && css_is_ws(decl->value[pos])) {
                pos++;
            }
        }
        for (int target = 0; target < shorthand->target_count; target++) {
            const css_box_target *item = &shorthand->targets[target];
            css_set_slot(slots, item->prop, components[item->kind].value, components[item->kind].len, rank);
        }
        return 1;
    }
    return 0;
}

/* Feed one declaration into the cascade slots, expanding a shorthand across its
   longhands. Every candidate for one declaration shares an order stamp. */
static void css_apply_decl(css_slot slots[NUM_PROPS], const css_decl *decl, int inline_style, int spec_a, int spec_b,
                           int spec_c, long order) {
    css_slot rank = {NULL, 0, decl->important, inline_style, spec_a, spec_b, spec_c, order, 1};
    int direct = css_prop_id(decl->name, decl->name_len);
    if (direct >= 0) {
        css_set_slot(slots, direct, decl->value, decl->value_len, &rank);
        return;
    }
    if (css_apply_distributive(slots, decl, &rank)) {
        return;
    }
    css_apply_box(slots, decl, &rank);
}

/* An element's resolved computed values: one owned code-point buffer per longhand. */
typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
} css_value;

static int css_value_set_slice(css_value *value, const Py_UCS4 *data, Py_ssize_t len) {
    Py_UCS4 *copy = PyMem_Malloc((size_t)(len + 1) * sizeof(Py_UCS4));
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(copy, data, (size_t)len * sizeof(Py_UCS4));
    PyMem_Free(value->data);
    value->data = copy;
    value->len = len;
    return 0;
}

static int css_value_set_ascii(css_value *value, const char *text) {
    Py_ssize_t len = (Py_ssize_t)strlen(text);
    Py_UCS4 buffer[32];
    for (Py_ssize_t index = 0; index < len; index++) {
        buffer[index] = (Py_UCS4)text[index];
    }
    return css_value_set_slice(value, buffer, len);
}

static void css_free_map(css_value *map) {
    for (int index = 0; index < NUM_PROPS; index++) {
        PyMem_Free(map[index].data);
    }
}

/* Resolve one longhand for an element: apply the cascade winner (or its absence)
   and the inherit/initial/unset/revert keywords against the parent's computed value.
   Returns -1 on allocation failure. */
static int css_resolve(css_value *out, const css_slot *slot, const css_value *parent, const css_prop_meta *meta) {
    const css_value *inherited = parent->data != NULL ? parent : NULL;
    if (slot->set) {
        const Py_UCS4 *value = slot->value;
        Py_ssize_t len = slot->value_len;
        if (css_slice_ci_eq(value, len, "inherit")) {
            return inherited != NULL ? css_value_set_slice(out, inherited->data, inherited->len)
                                     : css_value_set_ascii(out, meta->initial);
        }
        if (css_slice_ci_eq(value, len, "initial")) {
            return css_value_set_ascii(out, meta->initial);
        }
        if (css_slice_ci_eq(value, len, "unset") || css_slice_ci_eq(value, len, "revert")) {
            /* revert has no user/UA origin to fall back to here, so it collapses to unset */
            if (meta->inherited && inherited != NULL) {
                return css_value_set_slice(out, inherited->data, inherited->len);
            }
            return css_value_set_ascii(out, meta->initial);
        }
        return css_value_set_slice(out, value, len);
    }
    if (meta->inherited && inherited != NULL) {
        return css_value_set_slice(out, inherited->data, inherited->len);
    }
    return css_value_set_ascii(out, meta->initial);
}

/* A parsed stylesheet kept alive for the cascade: the cleaned buffer, its rules,
   and each rule's compiled selector (NULL when the selector did not compile). */
typedef struct {
    Py_UCS4 *clean;
    css_rule *rules;
    Py_ssize_t rule_count;
    sel_compiled **compiled;
} css_sheet;

static void css_free_sheets(css_sheet *sheets, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        for (Py_ssize_t rule = 0; rule < sheets[index].rule_count; rule++) {
            if (sheets[index].compiled[rule] != NULL) {
                selector_free(sheets[index].compiled[rule]);
            }
        }
        PyMem_Free(sheets[index].compiled);
        css_free_rules(sheets[index].rules, sheets[index].rule_count);
        PyMem_Free(sheets[index].clean);
    }
    PyMem_Free(sheets);
}

/* Collect every <style> element's text under root (document order), parsing each
   into a sheet whose selectors are compiled against tree. A selector that fails to
   compile leaves compiled[rule] NULL so the rule matches nothing. Returns the sheet
   array and sets *out_count, or NULL with *out_count negative on failure. */
static css_sheet *css_collect_sheets(module_state *state, th_tree *tree, th_node *root, Py_ssize_t *out_count) {
    css_sheet *sheets = NULL;
    Py_ssize_t count = 0;
    Py_ssize_t capacity = 0;
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_STYLE) {
            continue;
        }
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = th_node_text(tree, node, &text_len);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto fail;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t clean_len = 0;
        Py_UCS4 *clean = css_strip_comments(text, text_len, &clean_len);
        PyMem_Free(text);
        if (clean == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto fail;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t rule_count = 0;
        css_rule *rules = css_parse_sheet(clean, clean_len, &rule_count);
        if (rule_count < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(clean); /* GCOVR_EXCL_LINE: allocation-failure path */
            goto fail;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        sel_compiled **compiled = PyMem_Calloc((size_t)(rule_count > 0 ? rule_count : 1), sizeof(sel_compiled *));
        if (compiled == NULL) {                /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            css_free_rules(rules, rule_count); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(clean);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            goto fail;                         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t rule = 0; rule < rule_count; rule++) {
            PyObject *selector = css_slice_str(rules[rule].selector, rules[rule].selector_len);
            if (selector == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_ssize_t done = rule; /* GCOVR_EXCL_LINE: allocation-failure path */
                while (done-- > 0) {    /* GCOVR_EXCL_LINE: allocation-failure path */
                    selector_free(compiled[done]); /* GCOVR_EXCL_LINE: allocation-failure path */
                } /* GCOVR_EXCL_LINE: allocation-failure path */
                PyMem_Free(compiled);              /* GCOVR_EXCL_LINE: allocation-failure path */
                css_free_rules(rules, rule_count); /* GCOVR_EXCL_LINE: allocation-failure path */
                PyMem_Free(clean);                 /* GCOVR_EXCL_LINE: allocation-failure path */
                goto fail;                         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            compiled[rule] = selector_compile(state->selector_error, tree, selector);
            Py_DECREF(selector);
            if (compiled[rule] == NULL) {
                /* an unsupported or invalid selector list drops its rule (it matches nothing) */
                PyErr_Clear();
            }
        }
        if (count == capacity) {
            Py_ssize_t grown = capacity == 0 ? 4 : capacity * 2;
            css_sheet *bigger = PyMem_Realloc(sheets, (size_t)grown * sizeof(css_sheet));
            if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                for (Py_ssize_t rule = 0; rule < rule_count; rule++) { /* GCOVR_EXCL_LINE: alloc-failure path */
                    selector_free(compiled[rule]);                     /* GCOVR_EXCL_LINE: alloc-failure path */
                } /* GCOVR_EXCL_LINE: alloc-failure path */
                PyMem_Free(compiled);              /* GCOVR_EXCL_LINE: allocation-failure path */
                css_free_rules(rules, rule_count); /* GCOVR_EXCL_LINE: allocation-failure path */
                PyMem_Free(clean);                 /* GCOVR_EXCL_LINE: allocation-failure path */
                goto fail;                         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            sheets = bigger;
            capacity = grown;
        }
        sheets[count++] = (css_sheet){clean, rules, rule_count, compiled};
    }
    *out_count = count;
    return sheets;
fail:                               /* GCOVR_EXCL_LINE: allocation-failure path */
    css_free_sheets(sheets, count); /* GCOVR_EXCL_LINE: allocation-failure path */
    *out_count = -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
    return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
}

/* Run the whole cascade for element against the collected sheets plus its inline
   style, writing the resolved computed values into out given the parent's computed
   map. Returns -1 on allocation failure. */
static int css_cascade_element(th_node *element, const css_sheet *sheets, Py_ssize_t sheet_count, th_tree *tree,
                               int quirks, const css_value *parent, css_value *out) {
    css_slot slots[NUM_PROPS] = {0};
    sel_ctx ctx = {tree, element, quirks, NULL};
    long order = 0;
    for (Py_ssize_t sheet = 0; sheet < sheet_count; sheet++) {
        const css_sheet *current = &sheets[sheet];
        for (Py_ssize_t rule = 0; rule < current->rule_count; rule++) {
            sel_compiled *compiled = current->compiled[rule];
            if (compiled == NULL) {
                continue;
            }
            int best_a = -1;
            int best_b = 0;
            int best_c = 0;
            for (int alt = 0; alt < compiled->count; alt++) {
                if (!selector_matches_alt(element, &compiled->alts[alt], &ctx)) {
                    continue;
                }
                int spec_a = 0;
                int spec_b = 0;
                int spec_c = 0;
                sel_specificity(&compiled->alts[alt], &spec_a, &spec_b, &spec_c);
                if (spec_a > best_a ||
                    (spec_a == best_a && (spec_b > best_b || (spec_b == best_b && spec_c > best_c)))) {
                    best_a = spec_a;
                    best_b = spec_b;
                    best_c = spec_c;
                }
            }
            if (best_a < 0) {
                continue;
            }
            const css_rule *rule_data = &current->rules[rule];
            for (Py_ssize_t decl = 0; decl < rule_data->decl_count; decl++) {
                css_apply_decl(slots, &rule_data->decls[decl], 0, best_a, best_b, best_c, order++);
            }
        }
    }
    /* the inline style attribute's cleaned buffer must outlive the resolve below: the
       winning slots hold slices into it, not copies, so it is freed only at the end */
    Py_UCS4 *inline_clean = NULL;
    css_decl *inline_decls = NULL;
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        if (element->attrs[index].name_atom != TH_ATTR_STYLE || element->attrs[index].value == NULL) {
            continue;
        }
        Py_ssize_t clean_len = 0;
        inline_clean = css_strip_comments(element->attrs[index].value, element->attrs[index].value_len, &clean_len);
        if (inline_clean == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t capacity = 0;
        Py_ssize_t decl_count = css_parse_block(inline_clean, 0, clean_len, &inline_decls, &capacity);
        if (decl_count < 0) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(inline_decls); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(inline_clean); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t decl = 0; decl < decl_count; decl++) {
            css_apply_decl(slots, &inline_decls[decl], 1, 0, 0, 0, order++);
        }
        break;
    }
    for (int index = 0; index < NUM_PROPS; index++) {
        if (css_resolve(&out[index], &slots[index], &parent[index], &CSS_PROPS[index]) < 0) { /* GCOVR_EXCL_BR_LINE */
            PyMem_Free(inline_decls); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(inline_clean); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    PyMem_Free(inline_decls);
    PyMem_Free(inline_clean);
    return 0;
}

/* Build the element chain root-first (document order down to element), the order the
   cascade needs so a parent's computed values exist before its child inherits them.
   Returns the chain length, or -1 on allocation failure with *out_chain NULL. */
static Py_ssize_t css_ancestor_chain(th_node *element, th_node ***out_chain) {
    Py_ssize_t depth = 0;
    for (th_node *node = element; node != NULL && node->type == TH_NODE_ELEMENT; node = node->parent) {
        depth++;
    }
    th_node **chain = PyMem_Malloc((size_t)depth * sizeof(th_node *));
    if (chain == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        *out_chain = NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t index = depth;
    for (th_node *node = element; index > 0; node = node->parent) {
        chain[--index] = node;
    }
    *out_chain = chain;
    return depth;
}

PyObject *turbohtml_css_computed_style(PyObject *module, PyObject *arg) {
    module_state *state = PyModule_GetState(module);
    if (!is_node(arg, state) || ((NodeObject *)arg)->node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_TypeError, "computed style requires an Element");
        return NULL;
    }
    PyObject *handle = ((NodeObject *)arg)->handle;
    th_node *element = ((NodeObject *)arg)->node;
    th_tree *tree = ((HandleObject *)handle)->tree;
    css_value final_map[NUM_PROPS] = {0};
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: sheet collection and matching read the tree */
    th_node **chain = NULL;
    Py_ssize_t chain_len = css_ancestor_chain(element, &chain);
    if (chain_len < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    } else {             /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        Py_ssize_t sheet_count = 0;
        css_sheet *sheets = css_collect_sheets(state, tree, th_tree_document(tree), &sheet_count);
        if (sheet_count < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        } else {               /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            int quirks = th_tree_quirks(tree);
            css_value parent[NUM_PROPS] = {0};
            for (Py_ssize_t index = 0; index < chain_len; index++) {
                css_value current[NUM_PROPS] = {0};
                int rc = css_cascade_element(chain[index], sheets, sheet_count, tree, quirks, parent, current);
                css_free_map(parent);
                memcpy(parent, current, sizeof(parent));
                if (rc < 0) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;      /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
            memcpy(final_map, parent, sizeof(final_map)); /* the last element resolved is the target */
            css_free_sheets(sheets, sheet_count);
        }
    }
    PyMem_Free(chain);
    Py_END_CRITICAL_SECTION();
    if (failed) {                /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        css_free_map(final_map); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyTuple_New(NUM_PROPS);
    if (result == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        css_free_map(final_map); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < NUM_PROPS; index++) {
        PyObject *name = PyUnicode_FromString(CSS_PROPS[index].name);
        PyObject *value = css_slice_str(final_map[index].data, final_map[index].len);
        if (name == NULL || value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            Py_XDECREF(name);                /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(value);               /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result);               /* GCOVR_EXCL_LINE: allocation-failure path */
            css_free_map(final_map);         /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *pair = PyTuple_Pack(2, name, value);
        Py_DECREF(name);
        Py_DECREF(value);
        if (pair == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(result);       /* GCOVR_EXCL_LINE: allocation-failure path */
            css_free_map(final_map); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyTuple_SET_ITEM(result, index, pair);
    }
    css_free_map(final_map);
    return result;
}
