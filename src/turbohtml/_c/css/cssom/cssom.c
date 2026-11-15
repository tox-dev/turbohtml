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
#include "core/ascii.h"
#include "core/node_map.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "css/cssom/cssom.h"
#include "dom/nodes.h"
#include "css/select/selector.h"

#include <stdio.h>
#include <string.h>

/* One CSS longhand property the cascade resolves: its lowercase name, whether it
   inherits (CSS Cascade §7), and its initial value (each property's spec "Initial:"
   line). getComputedStyle returns a value for every entry, in this order. Only
   longhands appear here; shorthands are expanded into these before the cascade. */
typedef struct {
    const char *name;
    const char *initial;
    uint8_t name_len;
    uint8_t initial_len;
    uint8_t inherited;
} css_prop_meta;

/* The lengths come from the literals at compile time, so building a computed style runs no strlen. Byte-wide fields
   after the pointers keep each row the size it had before the lengths. */
#define CSS_PROP(name, inherited, initial) {name, initial, sizeof(name) - 1, sizeof(initial) - 1, inherited}

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
    CSS_PROP("color", 1, "canvastext"),
    CSS_PROP("font-size", 1, "medium"),
    CSS_PROP("font-style", 1, "normal"),
    CSS_PROP("font-weight", 1, "normal"),
    CSS_PROP("font-variant", 1, "normal"),
    CSS_PROP("line-height", 1, "normal"),
    CSS_PROP("text-align", 1, "start"),
    CSS_PROP("text-indent", 1, "0"),
    CSS_PROP("text-transform", 1, "none"),
    CSS_PROP("letter-spacing", 1, "normal"),
    CSS_PROP("word-spacing", 1, "normal"),
    CSS_PROP("white-space", 1, "normal"),
    CSS_PROP("visibility", 1, "visible"),
    CSS_PROP("list-style-type", 1, "disc"),
    CSS_PROP("list-style-position", 1, "outside"),
    CSS_PROP("cursor", 1, "auto"),
    CSS_PROP("direction", 1, "ltr"),
    CSS_PROP("caption-side", 1, "top"),
    CSS_PROP("display", 0, "inline"),
    CSS_PROP("position", 0, "static"),
    CSS_PROP("top", 0, "auto"),
    CSS_PROP("right", 0, "auto"),
    CSS_PROP("bottom", 0, "auto"),
    CSS_PROP("left", 0, "auto"),
    CSS_PROP("float", 0, "none"),
    CSS_PROP("clear", 0, "none"),
    CSS_PROP("width", 0, "auto"),
    CSS_PROP("height", 0, "auto"),
    CSS_PROP("min-width", 0, "auto"),
    CSS_PROP("min-height", 0, "auto"),
    CSS_PROP("max-width", 0, "none"),
    CSS_PROP("max-height", 0, "none"),
    CSS_PROP("margin-top", 0, "0"),
    CSS_PROP("margin-right", 0, "0"),
    CSS_PROP("margin-bottom", 0, "0"),
    CSS_PROP("margin-left", 0, "0"),
    CSS_PROP("padding-top", 0, "0"),
    CSS_PROP("padding-right", 0, "0"),
    CSS_PROP("padding-bottom", 0, "0"),
    CSS_PROP("padding-left", 0, "0"),
    CSS_PROP("border-top-width", 0, "medium"),
    CSS_PROP("border-right-width", 0, "medium"),
    CSS_PROP("border-bottom-width", 0, "medium"),
    CSS_PROP("border-left-width", 0, "medium"),
    CSS_PROP("border-top-style", 0, "none"),
    CSS_PROP("border-right-style", 0, "none"),
    CSS_PROP("border-bottom-style", 0, "none"),
    CSS_PROP("border-left-style", 0, "none"),
    CSS_PROP("border-top-color", 0, "currentcolor"),
    CSS_PROP("border-right-color", 0, "currentcolor"),
    CSS_PROP("border-bottom-color", 0, "currentcolor"),
    CSS_PROP("border-left-color", 0, "currentcolor"),
    CSS_PROP("background-color", 0, "transparent"),
    CSS_PROP("background-image", 0, "none"),
    CSS_PROP("opacity", 0, "1"),
    CSS_PROP("z-index", 0, "auto"),
    CSS_PROP("overflow-x", 0, "visible"),
    CSS_PROP("overflow-y", 0, "visible"),
    CSS_PROP("vertical-align", 0, "baseline"),
    CSS_PROP("box-sizing", 0, "content-box"),
    CSS_PROP("outline-width", 0, "medium"),
    CSS_PROP("outline-style", 0, "none"),
    CSS_PROP("outline-color", 0, "currentcolor"),
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

static void css_unescape_name(Py_UCS4 *name, Py_ssize_t *length);

typedef enum {
    CSS_WIDE_NONE,
    CSS_WIDE_INHERIT,
    CSS_WIDE_INITIAL,
    CSS_WIDE_UNSET,
    CSS_WIDE_REVERT,
    CSS_WIDE_REVERT_LAYER,
} css_wide_value;

static css_wide_value css_wide_keyword(const Py_UCS4 *value, Py_ssize_t len) {
    if (len < 5) {
        return CSS_WIDE_NONE;
    }
    Py_UCS4 first = css_lower(value[0]);
    if (first != '\\' && first != 'i' && first != 'r' && first != 'u') {
        return CSS_WIDE_NONE;
    }
    Py_UCS4 decoded[97];
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        if (value[pos] != '\\') {
            continue;
        }
        if (len > 96) {
            return CSS_WIDE_NONE;
        }
        memcpy(decoded, value, (size_t)len * sizeof(Py_UCS4));
        decoded[len] = 0;
        css_unescape_name(decoded, &len);
        value = decoded;
        break;
    }
    switch (css_lower(value[0])) {
    case 'i':
        if (css_slice_ci_eq(value, len, "inherit")) {
            return CSS_WIDE_INHERIT;
        }
        return css_slice_ci_eq(value, len, "initial") ? CSS_WIDE_INITIAL : CSS_WIDE_NONE;
    case 'u':
        return css_slice_ci_eq(value, len, "unset") ? CSS_WIDE_UNSET : CSS_WIDE_NONE;
    case 'r':
        if (css_slice_ci_eq(value, len, "revert")) {
            return CSS_WIDE_REVERT;
        }
        return css_slice_ci_eq(value, len, "revert-layer") ? CSS_WIDE_REVERT_LAYER : CSS_WIDE_NONE;
    default:
        return CSS_WIDE_NONE;
    }
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

/* The code points css_strip_comments acts on, as bits offset from the double quote, the lowest of them. */
#define CSS_STRIP_STOPS                                                                                                \
    ((UINT64_C(1) << ('"' - '"')) | (UINT64_C(1) << ('\'' - '"')) | (UINT64_C(1) << ('/' - '"')) |                     \
     (UINT64_C(1) << ('\\' - '"')))

/* Copy src stripping CSS block comments. A comment marker inside a '' or "" string or right after a backslash is
   literal. Each comment becomes one space so it stays a token boundary. Returns an owned buffer of *out_len code
   points, or NULL on allocation failure. */
static Py_UCS4 *css_strip_comments(const Py_UCS4 *src, Py_ssize_t len, Py_ssize_t *out_len) {
    Py_UCS4 *out = PyMem_Malloc((size_t)(len + 1) * sizeof(Py_UCS4));
    if (out == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t write = 0;
    Py_ssize_t pos = 0;
    while (pos < len) {
        Py_UCS4 ch = src[pos];
        /* one bit test passes every code point but a quote, the backslash and the slash, which keeps the per-character
           cost of the escape check off names, numbers and punctuation */
        Py_UCS4 offset = ch - '"';
        if (offset > '\\' - '"' || !((CSS_STRIP_STOPS >> offset) & 1)) {
            out[write++] = ch;
            pos++;
            continue;
        }
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
        if (ch == '\\' && pos + 1 < len) {
            /* the escaped code point joins a name (CSS Syntax 3 §4.3.7), so an escaped slash opens no comment */
            out[write++] = ch;
            out[write++] = src[pos + 1];
            pos += 2;
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
    unsigned char important;
    unsigned char escaped_name;
    unsigned char mixed_wide;
} css_decl;

static int css_mixed_wide_keyword(const Py_UCS4 *value, Py_ssize_t len);

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

/* Trim like css_trim, but keep a trailing whitespace a `\` escapes: it ends the name or selector it belongs to (CSS
   Syntax 3 §4.3.7). */
static void css_trim_escaped(const Py_UCS4 **data, Py_ssize_t *len) {
    Py_ssize_t start = 0;
    Py_ssize_t stop = *len;
    while (start < stop && css_is_ws((*data)[start])) {
        start++;
    }
    while (stop > start && css_is_ws((*data)[stop - 1])) {
        Py_ssize_t backslashes = 0;
        while (stop - backslashes > start + 1 && (*data)[stop - backslashes - 2] == '\\') {
            backslashes++;
        }
        if (backslashes & 1) {
            break;
        }
        stop--;
    }
    *data += start;
    *len = stop - start;
}

static void css_unescape_name(Py_UCS4 *name, Py_ssize_t *length) {
    Py_ssize_t write = 0;
    for (Py_ssize_t read = 0; read < *length; read++) {
        if (name[read] != '\\') {
            name[write++] = name[read];
            continue;
        }
        read++;
        if (!is_ascii_hexdigit(name[read])) {
            name[write++] = name[read] == 0 ? 0xFFFD : name[read];
            continue;
        }
        uint32_t code = 0;
        int digits = 0;
        do {
            Py_UCS4 hex = name[read++];
            code = code * 16 + (hex <= '9' ? (uint32_t)(hex - '0') : (uint32_t)((hex | 32) - 'a' + 10));
            digits++;
        } while (digits < 6 && read < *length && is_ascii_hexdigit(name[read]));
        if (read < *length && css_is_ws(name[read])) {
            if (name[read] == '\r' && name[read + 1] == '\n') {
                read++;
            }
            read++;
        }
        name[write++] = code == 0 || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF) ? 0xFFFD : code;
        read--;
    }
    *length = write;
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

/* What css_scan_to does at each ASCII code point: 1, 2 and 3 open a (), [] or {} block and name its kind, 4 marks a
   closer, a quote, a backslash, `:` or `;`, and 0 lets the run of names and numbers between them skip the
   comparisons. */
static const unsigned char CSS_SCAN_CLASS[128] = {
    ['('] = 1, ['['] = 2,  ['{'] = 3, [')'] = 4, [']'] = 4,  ['}'] = 4,
    ['"'] = 4, ['\''] = 4, [':'] = 4, [';'] = 4, ['\\'] = 4,
};

/* The closer of each block kind; kind 0 stands for no open block and matches nothing. */
static const Py_UCS4 CSS_BLOCK_CLOSERS[4] = {0, ')', ']', '}'};

/* The offset of the first top-level stop or also_stop in [pos, end), skipping strings and (), [] and {} blocks,
   or end when none remains, or -1 on allocation failure. A custom property's value holds {} blocks (CSS Variables 1
   §2), so a `;` or `}` inside one is block content. A block ends only at the closer of its own opener; any other
   closer is a token inside it (CSS Syntax 3 §5.5.8), so `{a)}` stays one block and the open kinds form a stack. The
   innermost 32 kinds sit two bits each in a register, which keeps a stack array and its per-call setup out of the
   common shallow scan; deeper kinds spill to the heap. */
static Py_ssize_t css_scan_to(const Py_UCS4 *data, Py_ssize_t pos, Py_ssize_t end, Py_UCS4 stop, Py_UCS4 also_stop,
                              int *escaped) {
    uint64_t open_kinds = 0;
    unsigned char *spilled = NULL;
    Py_ssize_t spilled_capacity = 0;
    Py_ssize_t depth = 0;
    Py_ssize_t found = end;
    while (pos < end) {
        TH_WORK_STEP();
        Py_UCS4 cur = data[pos];
        unsigned char class_of = cur < 128 ? CSS_SCAN_CLASS[cur] : 0;
        if (class_of == 0) {
            pos++;
            continue;
        }
        if (depth == 0 && (cur == stop || cur == also_stop)) {
            found = pos;
            break;
        }
        if (class_of < 4) {
            if (depth >= 32) {
                if (depth - 32 == spilled_capacity) {
                    spilled_capacity = spilled_capacity == 0 ? TH_INITIAL_CAPACITY(32) : spilled_capacity * 2;
                    unsigned char *grown = PyMem_Realloc(spilled, (size_t)spilled_capacity);
                    if (grown == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        PyMem_Free(spilled); /* GCOVR_EXCL_LINE: allocation-failure path */
                        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    spilled = grown;
                }
                spilled[depth - 32] = (unsigned char)(open_kinds >> 62);
            }
            open_kinds = open_kinds << 2 | class_of;
            depth++;
        } else if (cur == CSS_BLOCK_CLOSERS[open_kinds & 3]) {
            open_kinds >>= 2;
            depth--;
            if (depth >= 32) {
                open_kinds |= (uint64_t)spilled[depth - 32] << 62;
            }
        } else if (cur == '"' || cur == '\'') {
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
        } else if (cur == '\\') {
            /* an escaped code point is part of a name (CSS Syntax 3 §4.3.7), so `\}` stays in the name */
            if (escaped != NULL && *escaped >= 0) {
                *escaped =
                    pos + 1 < end && (data[pos + 1] == '\n' || data[pos + 1] == '\r' || data[pos + 1] == '\f') ? -1 : 1;
            }
            pos++;
        }
        pos++;
    }
    if (spilled != NULL) {
        PyMem_Free(spilled);
    }
    return found;
}

/* Parse the declarations in the cleaned block [start, end) into decls, growing the
   array as needed. Returns the count, or -1 on allocation failure. */
static Py_ssize_t css_parse_block(Py_UCS4 *data, Py_ssize_t start, Py_ssize_t end, css_decl **out, Py_ssize_t *capacity,
                                  int decode_names) {
    Py_ssize_t count = 0;
    Py_ssize_t pos = start;
    while (pos < end) {
        /* the value scan starts past the colon, so the name is read once */
        int escaped = 0;
        Py_ssize_t colon = css_scan_to(data, pos, end, ':', ';', &escaped);
        if (colon < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t semi = colon < end && data[colon] == ':' ? css_scan_to(data, colon + 1, end, ';', ';', NULL) : colon;
        if (semi < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (colon < semi) {
            const Py_UCS4 *name = data + pos;
            Py_ssize_t name_len = colon - pos;
            if (escaped) {
                css_trim_escaped(&name, &name_len);
            } else {
                css_trim(&name, &name_len);
            }
            const Py_UCS4 *value = data + colon + 1;
            Py_ssize_t value_len = semi - colon - 1;
            css_trim(&value, &value_len);
            int important = css_take_important(&value, &value_len);
            if (name_len > 0 && value_len > 0 && escaped >= 0) {
                if (decode_names && escaped) {
                    css_unescape_name((Py_UCS4 *)name, &name_len);
                }
                if (count == *capacity) {
                    Py_ssize_t grown = *capacity == 0 ? TH_INITIAL_CAPACITY(8) : *capacity * 2;
                    css_decl *bigger = PyMem_Realloc(*out, (size_t)grown * sizeof(css_decl));
                    if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    *out = bigger;
                    *capacity = grown;
                }
                (*out)[count++] = (css_decl){name,
                                             name_len,
                                             value,
                                             value_len,
                                             important,
                                             !decode_names && escaped,
                                             decode_names && css_mixed_wide_keyword(value, value_len)};
            }
        }
        pos = semi + 1;
    }
    return count;
}

int th_css_inline_whitespace(const Py_UCS4 *source, Py_ssize_t length, int inherited) {
    Py_ssize_t clean_len;
    Py_UCS4 *clean = css_strip_comments(source, length, &clean_len);
    if (clean == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -2;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    css_decl *declarations = NULL;
    Py_ssize_t capacity = 0;
    Py_ssize_t count = css_parse_block(clean, 0, clean_len, &declarations, &capacity, 1);
    int mode = inherited;
    int important = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        css_decl *declaration = &declarations[index];
        if (!css_slice_ci_eq(declaration->name, declaration->name_len, "white-space")) {
            continue;
        }
        const Py_UCS4 *value = declaration->value;
        Py_ssize_t value_len = declaration->value_len;
        int next = -2;
        if (css_slice_ci_eq(value, value_len, "pre") || css_slice_ci_eq(value, value_len, "pre-wrap") ||
            css_slice_ci_eq(value, value_len, "break-spaces")) {
            next = TH_WS_PRESERVE;
        } else if (css_slice_ci_eq(value, value_len, "pre-line")) {
            next = TH_WS_LINES;
        } else if (css_slice_ci_eq(value, value_len, "normal") || css_slice_ci_eq(value, value_len, "nowrap") ||
                   css_slice_ci_eq(value, value_len, "initial")) {
            next = TH_WS_NORMAL;
        } else if (css_slice_ci_eq(value, value_len, "inherit") || css_slice_ci_eq(value, value_len, "unset") ||
                   css_slice_ci_eq(value, value_len, "revert") || css_slice_ci_eq(value, value_len, "revert-layer")) {
            next = inherited;
        }
        if (next >= -1 && declaration->important >= important) {
            mode = next;
            important = declaration->important;
        }
    }
    PyMem_Free(declarations);
    PyMem_Free(clean);
    return count < 0 ? -2 : mode; /* GCOVR_EXCL_BR_LINE: a negative count requires allocation failure */
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

static PyObject *css_decoded_name(PyObject *name);
static PyObject *css_property_name(const Py_UCS4 *data, Py_ssize_t len) {
    if (len > 1 && data[0] == '-' && data[1] == '-') {
        return css_slice_str(data, len);
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (data[index] >= 'A' && data[index] <= 'Z') {
            goto lowercase;
        }
    }
    return css_slice_str(data, len);

lowercase:
    {
        Py_UCS4 maximum = 0;
        for (Py_ssize_t index = 0; index < len; index++) {
            if (data[index] > maximum) {
                maximum = data[index];
            }
        }
        PyObject *name = PyUnicode_New(len, maximum);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;    /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < len; index++) {
            Py_UCS4 point = data[index];
            PyUnicode_WRITE(PyUnicode_KIND(name), PyUnicode_DATA(name), index,
                            point >= 'A' && point <= 'Z' ? point + ('a' - 'A') : point);
        }
        return name;
    }
}

/* Build the ((name, value, important), ...) tuple for a parsed declaration list. */
static PyObject *css_decls_to_tuple(const css_decl *decls, Py_ssize_t count) {
    PyObject *result = PyTuple_New(count);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *raw_name = decls[index].escaped_name ? css_slice_str(decls[index].name, decls[index].name_len) : NULL;
        if (decls[index].escaped_name && raw_name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(result);                               /* GCOVR_EXCL_LINE */
            return NULL;                                     /* GCOVR_EXCL_LINE */
        }
        PyObject *name;
        if (decls[index].escaped_name) {
            name = css_decoded_name(raw_name);
        } else {
            name = css_property_name(decls[index].name, decls[index].name_len);
        }
        PyObject *value = css_slice_str(decls[index].value, decls[index].value_len);
        if (name == NULL || value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            Py_XDECREF(raw_name);            /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(name);                /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(value);               /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result);               /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *triple = raw_name == NULL
                               ? PyTuple_Pack(3, name, value, decls[index].important ? Py_True : Py_False)
                               : PyTuple_Pack(4, name, value, decls[index].important ? Py_True : Py_False, raw_name);
        Py_XDECREF(raw_name);
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

/* Borrow the ((name, value, important), ...) tuple a StyleDeclaration wraps, rejecting anything else. */
static int css_declaration_items(PyObject *items) {
    if (!PyTuple_Check(items)) {
        PyErr_SetString(PyExc_TypeError, "declaration items must be a tuple of (name, value, important) triples");
        return -1;
    }
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(items); index++) {
        PyObject *triple = PyTuple_GET_ITEM(items, index);
        if (!PyTuple_Check(triple) ||
            (PyTuple_GET_SIZE(triple) != 3 &&
             (PyTuple_GET_SIZE(triple) != 4 || !PyUnicode_Check(PyTuple_GET_ITEM(triple, 3)))) ||
            !PyUnicode_Check(PyTuple_GET_ITEM(triple, 0)) || !PyUnicode_Check(PyTuple_GET_ITEM(triple, 1))) {
            PyErr_SetString(PyExc_TypeError, "declaration items must be a tuple of (name, value, important) triples");
            return -1;
        }
    }
    return 0;
}

static PyObject *css_decoded_name(PyObject *name) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(name);
    Py_UCS4 *decoded = PyUnicode_AsUCS4Copy(name);
    if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    css_unescape_name(decoded, &length);
    PyObject *result = css_property_name(decoded, length);
    PyMem_Free(decoded);
    return result;
}

/* _css_declaration_index(items) -> {name: index}: important declarations win over normal duplicates. */
PyObject *turbohtml_css_declaration_index(PyObject *Py_UNUSED(module), PyObject *items) {
    if (css_declaration_items(items) < 0) {
        return NULL;
    }
    PyObject *index_of = PyDict_New();
    if (index_of == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(items); index++) {
        PyObject *triple = PyTuple_GET_ITEM(items, index);
        PyObject *name = PyTuple_GET_ITEM(triple, 0);
        PyObject *position = PyLong_FromSsize_t(index);
        if (position == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(index_of); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
        int stored = PyDict_SetItem(index_of, name, position);
        Py_DECREF(position);
        if (stored < 0) {        /* GCOVR_EXCL_BR_LINE: a str-keyed insert only fails on allocation failure */
            Py_DECREF(index_of); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE */
        }
    }
    if (PyDict_Size(index_of) < PyTuple_GET_SIZE(items)) {
        for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(items); index++) {
            PyObject *triple = PyTuple_GET_ITEM(items, index);
            if (PyTuple_GET_ITEM(triple, 2) != Py_True) {
                continue;
            }
            PyObject *name = PyTuple_GET_ITEM(triple, 0);
            if (PyLong_AsSsize_t(PyDict_GetItem(index_of, name)) == index) {
                continue;
            }
            PyObject *position = PyLong_FromSsize_t(index);
            if (position == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_DECREF(index_of); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int stored = PyDict_SetItem(index_of, name, position);
            Py_DECREF(position);
            if (stored < 0) {        /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_DECREF(index_of); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    return index_of;
}

/* _css_declaration_text(items) -> str: the declarations serialized as "name: value" pairs, an important one
   carrying its " !important" flag, joined by "; " in source order. */
PyObject *turbohtml_css_declaration_text(PyObject *Py_UNUSED(module), PyObject *items) {
    if (css_declaration_items(items) < 0) {
        return NULL;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(items);
    PyObject *parts = PyList_New(count);
    if (parts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *triple = PyTuple_GET_ITEM(items, index);
        int important = PyObject_IsTrue(PyTuple_GET_ITEM(triple, 2));
        if (important < 0) { /* the flag's truth test raised */
            Py_DECREF(parts);
            return NULL;
        }
        PyObject *value = PyTuple_GET_ITEM(triple, 1);
        Py_ssize_t value_len = PyUnicode_GET_LENGTH(value);
        Py_ssize_t slash_start = value_len;
        while (slash_start > 0 && PyUnicode_READ_CHAR(value, slash_start - 1) == '\\') {
            slash_start--;
        }
        /* A trailing escape would consume the semicolon between declarations. */
        const char *line_break = index + 1 < count && !important && (value_len - slash_start) % 2 ? "\n" : "";
        PyObject *part = th_str_format("%U: %U%s%s", PyTuple_GET_ITEM(triple, PyTuple_GET_SIZE(triple) == 4 ? 3 : 0),
                                       value, line_break, important ? " !important" : "");
        if (part == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(parts); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        PyList_SET_ITEM(parts, index, part);
    }
    PyObject *separator = PyUnicode_FromString("; ");
    if (separator == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(parts);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    PyObject *text = PyUnicode_Join(separator, parts);
    Py_DECREF(separator);
    Py_DECREF(parts);
    return text;
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
    Py_ssize_t count = css_parse_block(clean, 0, clean_len, &decls, &capacity, 0);
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
static css_rule *css_parse_sheet(Py_UCS4 *data, Py_ssize_t len, Py_ssize_t *out_count, int decode_names) {
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
        if (data[pos] == '@') {
            /* an at-rule: a statement one (@import, @charset) ends at ';', a block one
               (@media, @supports) at its balanced '}'; either is skipped whole. The scan stops at the first of ';' and
               '{', so a statement never scans on to a '{' that belongs to a later rule. */
            Py_ssize_t stop = css_scan_to(data, pos, len, ';', '{', NULL);
            if (stop < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                goto fail;  /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            if (stop >= len || data[stop] == ';') {
                pos = stop < len ? stop + 1 : len;
                continue;
            }
            Py_ssize_t block_end = css_scan_to(data, stop + 1, len, '}', '}', NULL);
            if (block_end < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                goto fail;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            pos = block_end < len ? block_end + 1 : len;
            continue;
        }
        Py_ssize_t brace = css_scan_to(data, pos, len, '{', '{', NULL);
        if (brace < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto fail;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (brace >= len) {
            break;
        }
        Py_ssize_t close = css_scan_to(data, brace + 1, len, '}', '}', NULL);
        if (close < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto fail;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        const Py_UCS4 *selector = data + pos;
        Py_ssize_t selector_len = brace - pos;
        css_trim_escaped(&selector, &selector_len);
        css_decl *decls = NULL;
        Py_ssize_t decl_cap = 0;
        Py_ssize_t decl_count = css_parse_block(data, brace + 1, close, &decls, &decl_cap, decode_names);
        if (decl_count < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(decls); /* GCOVR_EXCL_LINE: allocation-failure path */
            goto fail;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (selector_len > 0) {
            if (count == capacity) {
                Py_ssize_t grown = capacity == 0 ? TH_INITIAL_CAPACITY(8) : capacity * 2;
                css_rule *bigger = PyMem_Realloc(rules, (size_t)grown * sizeof(css_rule));
                if (bigger == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    PyMem_Free(decls); /* GCOVR_EXCL_LINE: allocation-failure path */
                    goto fail;         /* GCOVR_EXCL_LINE: allocation-failure path */
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
fail:                             /* GCOVR_EXCL_LINE: allocation-failure path */
    css_free_rules(rules, count); /* GCOVR_EXCL_LINE: allocation-failure path */
    *out_count = -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
    return NULL;                  /* GCOVR_EXCL_LINE: allocation-failure path */
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
    css_rule *rules = css_parse_sheet(clean, clean_len, &rule_count, 0);
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
        if (selector == NULL) {                /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(result);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            css_free_rules(rules, rule_count); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(clean);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *decls = css_decls_to_tuple(rules[index].decls, rules[index].decl_count);
        PyObject *rule = decls == NULL ? NULL : PyTuple_Pack(2, selector, decls); /* GCOVR_EXCL_BR_LINE: allocation */
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
        if (part_count > 1) {
            for (int part = 0; part < part_count; part++) {
                if (css_wide_keyword(parts[part], part_lens[part])) {
                    return 1;
                }
            }
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
        if (css_wide_keyword(decl->value, decl->value_len)) {
            for (int target = 0; target < shorthand->target_count; target++) {
                css_set_slot(slots, shorthand->targets[target].prop, decl->value, decl->value_len, rank);
            }
            return 1;
        }
        css_span components[3] = {CSS_COMPONENT_INITIAL[0], CSS_COMPONENT_INITIAL[1], CSS_COMPONENT_INITIAL[2]};
        int seen[3] = {0, 0, 0};
        Py_ssize_t pos = 0;
        while (pos < decl->value_len) {
            Py_ssize_t stop = css_component_end(decl->value, pos, decl->value_len);
            if (css_wide_keyword(decl->value + pos, stop - pos)) {
                return 1;
            }
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

static Py_ssize_t css_escape_end(const Py_UCS4 *value, Py_ssize_t pos, Py_ssize_t len) {
    pos++;
    if (!is_ascii_hexdigit(value[pos])) {
        return pos + 1;
    }
    int digits = 0;
    do {
        pos++;
        digits++;
    } while (digits < 6 && pos < len && is_ascii_hexdigit(value[pos]));
    return pos < len && css_is_ws(value[pos]) ? pos + 1 : pos;
}

static int css_mixed_wide_keyword(const Py_UCS4 *value, Py_ssize_t len) {
    if (len < 5) {
        return 0;
    }
    int candidate = 0;
    for (Py_ssize_t pos = 0; pos + 2 < len; pos++) {
        if (value[pos] == '\\') {
            candidate = 1;
            break;
        }
        Py_UCS4 first = value[pos] | 32;
        if (first != 'i' && first != 'r' && first != 'u') {
            continue;
        }
        Py_UCS4 second = value[pos + 1] | 32;
        Py_UCS4 third = value[pos + 2] | 32;
        if ((first == 'i' && second == 'n' && (third == 'h' || third == 'i')) ||
            (first == 'r' && second == 'e' && third == 'v') || (first == 'u' && second == 'n' && third == 's')) {
            candidate = 1;
            break;
        }
    }
    if (!candidate) {
        return 0;
    }
    Py_UCS4 quote = 0;
    int depth = 0;
    for (Py_ssize_t pos = 0; pos < len;) {
        Py_UCS4 ch = value[pos];
        if (quote != 0) {
            if (ch == '\\' && pos + 1 < len) { /* GCOVR_EXCL_BR_LINE: a terminal escape cannot precede a keyword */
                pos = css_escape_end(value, pos, len);
                continue;
            }
            if (ch == quote) {
                quote = 0;
            }
            pos++;
            continue;
        }
        if (ch == '"' || ch == '\'') {
            quote = ch;
            pos++;
            continue;
        }
        if (ch == '\\' && depth > 0 && pos + 1 < len) { /* GCOVR_EXCL_BR_LINE: terminal escape */
            pos = css_escape_end(value, pos, len);
            continue;
        }
        if (ch == '(') {
            depth++;
            pos++;
            continue;
        }
        if (ch == ')' && depth > 0) {
            depth--;
            pos++;
            continue;
        }
        if (depth > 0) {
            pos++;
            continue;
        }
        if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '-' ||
              ch == '_' || ch >= 128 || ch == '\\')) {
            pos++;
            continue;
        }
        Py_ssize_t stop = pos;
        while (stop < len) {
            Py_UCS4 part = value[stop];
            if (part == '\\') {
                stop = stop + 1 < len ? css_escape_end(value, stop, len) : stop + 1;
            } else if ((part >= 'a' && part <= 'z') || (part >= 'A' && part <= 'Z') || (part >= '0' && part <= '9') ||
                       part == '-' || part == '_' || part >= 128) {
                stop++;
            } else {
                break;
            }
        }
        if (css_wide_keyword(value + pos, stop - pos)) {
            return pos != 0 || stop != len;
        }
        pos = stop;
    }
    return 0;
}

/* Feed one declaration into the cascade slots, expanding a shorthand across its
   longhands. Every candidate for one declaration shares an order stamp. */
static void css_apply_decl(css_slot slots[NUM_PROPS], const css_decl *decl, int inline_style, int spec_a, int spec_b,
                           int spec_c, long order) {
    css_slot rank = {NULL, 0, decl->important, inline_style, spec_a, spec_b, spec_c, order, 1};
    if (decl->name_len == 3 && css_slice_ci_eq(decl->name, decl->name_len, "all")) {
        if (css_wide_keyword(decl->value, decl->value_len)) {
            for (int prop = 0; prop < NUM_PROPS; prop++) {
                if (prop != PROP_DIRECTION) {
                    css_set_slot(slots, prop, decl->value, decl->value_len, &rank);
                }
            }
        }
        return;
    }
    int direct = css_prop_id(decl->name, decl->name_len);
    if (direct >= 0) {
        if (decl->mixed_wide) {
            return;
        }
        css_set_slot(slots, direct, decl->value, decl->value_len, &rank);
        return;
    }
    if (css_apply_distributive(slots, decl, &rank)) {
        return;
    }
    css_apply_box(slots, decl, &rank);
}

/* One longhand's computed value. In the cache it borrows text that outlives the cache: a stylesheet's buffer, a
   parsed style attribute or the initial values, all of which the cache holds. A map handed to the caller owns copies,
   since the caller reads them after it lets go of the tree. */
typedef struct {
    const Py_UCS4 *data;
    Py_ssize_t len;
} css_value;

static void css_free_map(css_value *map) {
    for (int index = 0; index < NUM_PROPS; index++) {
        PyMem_Free((Py_UCS4 *)map[index].data);
    }
}

/* Resolve one longhand for an element: apply the cascade winner (or its absence)
   and the inherit/initial/unset/revert keywords against the parent's computed value. */
static void css_resolve(css_value *out, const css_slot *slot, const css_value *parent, const css_value *initial,
                        int inherited) {
    const int has_parent = parent->data != NULL;
    if (slot->set) {
        css_wide_value wide = css_wide_keyword(slot->value, slot->value_len);
        if (wide == CSS_WIDE_NONE) {
            *out = (css_value){slot->value, slot->value_len};
            return;
        }
        if (wide == CSS_WIDE_INHERIT) {
            *out = has_parent ? *parent : *initial;
            return;
        }
        if (wide == CSS_WIDE_INITIAL) {
            *out = *initial;
            return;
        }
        /* no user/UA origin or layers exist here, so both rollback keywords act as unset */
    }
    *out = inherited && has_parent ? *parent : *initial;
}

typedef struct {
    int ids;
    int classes;
    int types;
} css_specificity;

/* A parsed stylesheet kept alive for the cascade: the cleaned buffer, its rules,
   and each rule's compiled selector (NULL when the selector did not compile). A <style>
   whose type is not CSS keeps an empty sheet, so a later write to its type is seen. */
typedef struct {
    Py_UCS4 *clean;
    css_rule *rules;
    Py_ssize_t rule_count;
    sel_compiled **compiled;
    css_specificity *specificities;
    const th_node *style;
    int supported;
    int nests_climbs; /* a rule's selector climbs from its climbs (sel_compiled.climbs 2) */
    Py_ssize_t chain; /* the longest chain of a rule's selector (sel_compiled.chain) */
} css_sheet;

typedef struct {
    th_node *node;
    Py_ssize_t depth; /* how many element ancestors node has */
    Py_ssize_t slot;  /* where entries holds it */
    int deep;         /* the path once reached CSS_CHECKPOINT_STRIDE levels below it */
    int on_path;
    css_value values[NUM_PROPS];
} css_computed_entry;

/* A style attribute parsed once per cache. Computed values borrow slices of clean, and parsing it again would decode
   the escaped names that the first parse decoded in place a second time. */
typedef struct {
    Py_UCS4 *clean;
    css_decl *decls;
    Py_ssize_t count;
} css_inline_style;

/* The most answers of the cascade's deep :has() memo, in bytes: 2 MiB of two-bit answers, 8M of them, more than the
   1.5M the cascade's old hash table held. A page's cascade keeps one memo for every rule and element. */
#define CSS_DEEP_MEMO_LIMIT ((size_t)1 << 21)

/* The largest :has() table the cascade keeps: 2^21 slots of 24 bytes, 48 MB. Each :has() argument keys its own answers,
   one per element, so without a cap the table grows with arguments times elements. */
#define CSS_HAS_MEMO_LIMIT ((size_t)1 << 21)

/* The deep memo's store keeps one 512-byte page per 2048 tree nodes for each key a walk uses, and a rule uses at most
   four keys, so this many bytes hold every rule's answers over a tree of nodes nodes. */
#define CSS_DEEP_MEMO_KEYS_PER_RULE 4
#define CSS_DEEP_MEMO_PAGE_NODES ((size_t)1 << 11)
#define CSS_DEEP_MEMO_PAGE_BYTES ((size_t)1 << 9)

/* The cap of the cascade's deep memo for a stylesheet of rules rules over a tree of nodes nodes, at least
   CSS_DEEP_MEMO_LIMIT. A product past SIZE_MAX keeps the fixed cap, so the store fails and the walks take the direct
   path. */
size_t th_css_deep_memo_limit(size_t rules, size_t nodes) {
    const size_t pages = nodes / CSS_DEEP_MEMO_PAGE_NODES + 1;
    if (rules > SIZE_MAX / CSS_DEEP_MEMO_KEYS_PER_RULE / pages / CSS_DEEP_MEMO_PAGE_BYTES) {
        return CSS_DEEP_MEMO_LIMIT;
    }
    const size_t wanted = rules * CSS_DEEP_MEMO_KEYS_PER_RULE * pages * CSS_DEEP_MEMO_PAGE_BYTES;
    return wanted > CSS_DEEP_MEMO_LIMIT ? wanted : CSS_DEEP_MEMO_LIMIT;
}

/* How many consecutive levels of an ancestor chain path holds. The HTML tree builder nests no deeper than
   TH_MAX_TREE_DEPTH, so the chain of a parsed page fits whole; an XML or a built tree can be deeper. */
#define CSS_PATH_DENSE ((Py_ssize_t)2 * TH_MAX_TREE_DEPTH)

/* The spacing of the checkpoints: a level whose depth is a multiple of it, and that the path once reached this many
   levels below, keeps its values after the path leaves it. */
#define CSS_CHECKPOINT_STRIDE 16

/* path holds the computed values of the last element asked about and of its ancestors, outermost first, up to
   CSS_PATH_DENSE levels. A walk over a tree keeps coming back to an ancestor for its next child, and the ancestor is
   still here, so the walk cascades each element once. A level the path leaves is freed unless it is a checkpoint, which
   entries keeps until the tree changes. Any element is then fewer than 2 * CSS_CHECKPOINT_STRIDE cascades below a kept
   ancestor or below the elements no call has reached yet, whatever order the calls come in. A checkpoint has its own
   CSS_CHECKPOINT_STRIDE descendants that no other checkpoint counts, so checkpoints number at most the elements divided
   by the stride. */
typedef struct {
    uint64_t attr_version;
    sel_memo has_memo;
    sel_nth_memo nth_memo;
    sel_position_memo position_memo;
    css_computed_entry **path;
    Py_ssize_t path_len;
    Py_ssize_t path_capacity;
    /* every kept level, path and checkpoints; entry_index maps a node to its slot plus one, and a freed level leaves
       its slot NULL until the slots are compacted */
    css_computed_entry **entries;
    Py_ssize_t entry_count;
    Py_ssize_t entry_capacity;
    Py_ssize_t live;
    th_node_map entry_index;
    th_node **pending; /* the levels a call cascades, innermost first */
    Py_ssize_t pending_capacity;
    css_inline_style *inline_styles;
    Py_ssize_t inline_count;
    Py_ssize_t inline_capacity;
    th_node_map inline_index;
    Py_UCS4 *initial_text;
    css_value initial[NUM_PROPS];
    sel_memo legend_memo;
    /* one answer per rule and long attribute value, a product the page sets both factors of, so capped as has_memo */
    sel_memo value_memo;
    /* the answers of the ancestor walks on a tree that may nest deep (sel_deep_query), capped at CSS_DEEP_MEMO_LIMIT */
    sel_deep_memo deep_memo;
} css_computed_cache;

static css_computed_cache *css_computed_new(uint64_t version) {
    css_computed_cache *cache = PyMem_Calloc(1, sizeof(*cache));
    if (cache == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_ssize_t total = 0;
    for (int prop = 0; prop < NUM_PROPS; prop++) {
        total += CSS_PROPS[prop].initial_len;
    }
    cache->initial_text = PyMem_Malloc((size_t)total * sizeof(Py_UCS4));
    if (cache->initial_text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(cache);             /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;                   /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_UCS4 *text = cache->initial_text;
    for (int prop = 0; prop < NUM_PROPS; prop++) {
        cache->initial[prop] = (css_value){text, CSS_PROPS[prop].initial_len};
        for (Py_ssize_t index = 0; index < CSS_PROPS[prop].initial_len; index++) {
            *text++ = (Py_UCS4)CSS_PROPS[prop].initial[index];
        }
    }
    cache->attr_version = version;
    cache->has_memo.limit = CSS_HAS_MEMO_LIMIT;
    cache->value_memo.limit = CSS_HAS_MEMO_LIMIT;
    cache->deep_memo.limit = CSS_DEEP_MEMO_LIMIT;
    cache->deep_memo.subject_scope = 1; /* the cascade binds :scope to the element it styles */
    return cache;
}

/* Take path levels [from, to) off the path, freeing each but the checkpoints. */
static void css_path_drop(css_computed_cache *cache, Py_ssize_t from, Py_ssize_t to) {
    if (from == to) {
        return; /* also the first call, whose path is still NULL */
    }
    for (Py_ssize_t index = from; index < to; index++) {
        css_computed_entry *const entry = cache->path[index];
        if (entry->deep && entry->depth % CSS_CHECKPOINT_STRIDE == 0) {
            entry->on_path = 0;
            continue;
        }
        cache->entries[entry->slot] = NULL;
        cache->live--;
        PyMem_Free(entry);
    }
    memmove(cache->path + from, cache->path + to, (size_t)(cache->path_len - to) * sizeof(css_computed_entry *));
    cache->path_len -= to - from;
}

/* Make room on the path for one more level. Returns -1 on allocation failure. */
static int css_path_reserve(css_computed_cache *cache) {
    if (cache->path_len < cache->path_capacity || cache->path_len == CSS_PATH_DENSE) {
        return 0;
    }
    size_t capacity;
    size_t bytes;
    const int grew = th_grow_cap((size_t)cache->path_len + 1, (size_t)cache->path_capacity, 8,
                                 sizeof(css_computed_entry *), &capacity, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: the path stops growing at CSS_PATH_DENSE levels */
        return -1; /* GCOVR_EXCL_LINE: the path stops growing at CSS_PATH_DENSE levels */
    }
    css_computed_entry **bigger = PyMem_Realloc(cache->path, bytes);
    if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    cache->path = bigger;
    cache->path_capacity = (Py_ssize_t)capacity;
    return 0;
}

/* Put entry, a child of the innermost level, on the path; css_path_reserve made the room. A full path first lets go of
   its outer half, whose levels are all far enough above the new one to stay as checkpoints when their depth fits. */
static void css_path_append(css_computed_cache *cache, css_computed_entry *entry) {
    if (cache->path_len == CSS_PATH_DENSE) {
        css_path_drop(cache, 0, CSS_PATH_DENSE / 2);
    }
    entry->on_path = 1;
    cache->path[cache->path_len++] = entry;
    if (cache->path_len > CSS_CHECKPOINT_STRIDE) {
        cache->path[cache->path_len - 1 - CSS_CHECKPOINT_STRIDE]->deep = 1;
    }
}

static css_computed_entry *css_entry_find(const css_computed_cache *cache, const th_node *node) {
    const Py_ssize_t slot = th_node_map_find(&cache->entry_index, node);
    return slot > 0 ? cache->entries[slot - 1] : NULL;
}

/* Make room for one more kept level. Once half the slots are freed, compacting them and rebuilding entry_index takes
   the place of growing, so both stay within twice the kept levels. Returns -1 on allocation failure. */
static int css_entry_reserve(css_computed_cache *cache) {
    if (cache->entry_count == cache->entry_capacity && cache->live <= cache->entry_count / 2) {
        Py_ssize_t kept = 0;
        for (Py_ssize_t slot = 0; slot < cache->entry_count; slot++) {
            if (cache->entries[slot] != NULL) {
                cache->entries[slot]->slot = kept;
                cache->entries[kept++] = cache->entries[slot];
            }
        }
        cache->entry_count = kept;
        PyMem_Free(cache->entry_index.entries);
        cache->entry_index = (th_node_map){0};
        for (Py_ssize_t slot = 0; slot < kept; slot++) {
            const int indexed = th_node_map_insert(&cache->entry_index, cache->entries[slot]->node, slot + 1);
            if (indexed < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;     /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
    }
    if (cache->entry_count == cache->entry_capacity) {
        size_t capacity;
        size_t bytes;
        const int grew = th_grow_cap((size_t)cache->entry_count + 1, (size_t)cache->entry_capacity, 16,
                                     sizeof(css_computed_entry *), &capacity, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        css_computed_entry **bigger = PyMem_Realloc(cache->entries, bytes);
        if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;        /* GCOVR_EXCL_LINE: allocation failure */
        }
        cache->entries = bigger;
        cache->entry_capacity = (Py_ssize_t)capacity;
    }
    /* at most a quarter full: most lookups are misses, which walk a whole run of slots */
    return th_node_map_reserve(&cache->entry_index, 2 * (cache->entry_index.count + 1));
}

/* Keep entry; css_entry_reserve made the room. A node whose earlier level was freed takes over its stale index slot. */
static void css_entry_add(css_computed_cache *cache, css_computed_entry *entry) {
    entry->slot = cache->entry_count;
    cache->entries[cache->entry_count++] = entry;
    cache->live++;
    const size_t index = th_node_map_slot(&cache->entry_index, entry->node);
    cache->entry_index.count += cache->entry_index.entries[index].node == NULL;
    cache->entry_index.entries[index] = (th_node_map_entry){entry->node, entry->slot + 1};
}

/* The parsed style attribute attr of element, parsed on its first cascade under this cache. Returns NULL on
   allocation failure. */
static const css_inline_style *css_inline_style_of(css_computed_cache *cache, const th_node *element,
                                                   const th_node_attr *attr) {
    const Py_ssize_t known = th_node_map_find(&cache->inline_index, element);
    if (known > 0) {
        return &cache->inline_styles[known - 1];
    }
    if (cache->inline_count == cache->inline_capacity) {
        size_t capacity;
        size_t bytes;
        const int grew = th_grow_cap((size_t)cache->inline_count + 1, (size_t)cache->inline_capacity, 8,
                                     sizeof(css_inline_style), &capacity, &bytes);
        if (!grew) {     /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return NULL; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        css_inline_style *bigger = PyMem_Realloc(cache->inline_styles, bytes);
        if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
        }
        cache->inline_styles = bigger;
        cache->inline_capacity = (Py_ssize_t)capacity;
    }
    css_inline_style style = {0};
    Py_ssize_t clean_len = 0;
    style.clean = css_strip_comments(attr->value, attr->value_len, &clean_len);
    if (style.clean == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;           /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_ssize_t capacity = 0;
    style.count = css_parse_block(style.clean, 0, clean_len, &style.decls, &capacity, 1);
    const Py_ssize_t index = cache->inline_count + 1;
    if (style.count < 0 || th_node_map_insert(&cache->inline_index, element, index) < 0) { /* GCOVR_EXCL_BR_LINE */
        PyMem_Free(style.decls); /* GCOVR_EXCL_LINE: allocation failure */
        PyMem_Free(style.clean); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;             /* GCOVR_EXCL_LINE: allocation failure */
    }
    cache->inline_styles[cache->inline_count] = style;
    return &cache->inline_styles[cache->inline_count++];
}

static void css_free_computed(HandleObject *handle) {
    css_computed_cache *cache = handle->css_computed;
    if (cache != NULL) {
        /* Memo keys borrow stylesheet selectors, so clear them before rebuilding sheets. */
        sel_memo_free(&cache->has_memo);
        sel_memo_free(&cache->legend_memo);
        sel_memo_free(&cache->value_memo);
        sel_deep_memo_free(&cache->deep_memo);
        sel_position_memo_free(&cache->position_memo);
        for (Py_ssize_t slot = 0; slot < cache->entry_count; slot++) {
            PyMem_Free(cache->entries[slot]);
        }
        PyMem_Free(cache->entries);
        PyMem_Free(cache->entry_index.entries);
        PyMem_Free(cache->path);
        PyMem_Free(cache->pending);
        for (Py_ssize_t index = 0; index < cache->inline_count; index++) {
            PyMem_Free(cache->inline_styles[index].decls);
            PyMem_Free(cache->inline_styles[index].clean);
        }
        PyMem_Free(cache->inline_styles);
        PyMem_Free(cache->inline_index.entries);
        PyMem_Free(cache->initial_text);
        PyMem_Free(cache);
        handle->css_computed = NULL;
    }
}

/* Copy source into dest as buffers dest owns. */
static int css_copy_map(css_value *dest, const css_value *source) {
    for (int index = 0; index < NUM_PROPS; index++) {
        Py_UCS4 *copy = PyMem_Malloc((size_t)(source[index].len + 1) * sizeof(Py_UCS4));
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;      /* GCOVR_EXCL_LINE: allocation failure */
        }
        memcpy(copy, source[index].data, (size_t)source[index].len * sizeof(Py_UCS4));
        dest[index] = (css_value){copy, source[index].len};
    }
    return 0;
}

static void css_free_sheets(css_sheet *sheets, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        for (Py_ssize_t rule = 0; rule < sheets[index].rule_count; rule++) {
            if (sheets[index].compiled[rule] != NULL) {
                selector_free(sheets[index].compiled[rule]);
            }
        }
        PyMem_Free(sheets[index].specificities);
        PyMem_Free(sheets[index].compiled);
        css_free_rules(sheets[index].rules, sheets[index].rule_count);
        PyMem_Free(sheets[index].clean);
    }
    PyMem_Free(sheets);
}

void handle_clear_css_cache(HandleObject *handle) {
    css_free_computed(handle);
    css_free_sheets(handle->css_sheets, handle->css_sheet_count);
    handle->css_sheets = NULL;
    handle->css_sheet_count = 0;
    handle->css_sheets_ready = 0;
}

static int css_inside_style(const th_node *node) {
    for (; node != NULL; node = node->parent) {
        if (node->type == TH_NODE_ELEMENT && node->atom == TH_TAG_STYLE) {
            return 1;
        }
    }
    return 0;
}

/* Called while each node is still where the edit finds it. Any edit can change a match, so the computed values always
   go; the compiled sheets depend only on the <style> elements and their text, so they go only when one is involved. */
void handle_clear_css_edit(HandleObject *handle, const th_node *parent, th_node *const *nodes, Py_ssize_t count) {
    css_free_computed(handle);
    if (!handle->css_sheets_ready) {
        return;
    }
    int styled = css_inside_style(parent);
    for (Py_ssize_t index = 0; !styled && index < count; index++) {
        styled = css_inside_style(nodes[index]->parent);
        for (th_node *node = nodes[index]; !styled && node != NULL; node = preorder_next(node, nodes[index])) {
            styled = node->type == TH_NODE_ELEMENT && node->atom == TH_TAG_STYLE;
        }
    }
    if (styled) {
        handle_clear_css_cache(handle);
    }
}

static void css_prioritize_subject(sel_compiled *compiled) {
    for (int alt = 0; alt < compiled->count; alt++) {
        sel_complex *complex = &compiled->alts[alt];
        sel_compound *subject = &complex->compounds[complex->count - 1];
        if (subject->simples[0].kind != ':' && subject->simples[0].kind != '*') {
            continue;
        }
        for (int index = 1; index < subject->count; index++) {
            if (subject->simples[index].kind == '#' || subject->simples[index].kind == '.') {
                /* A compound is a conjunction; its specificity does not depend on predicate order. */
                sel_simple first = subject->simples[0];
                subject->simples[0] = subject->simples[index];
                subject->simples[index] = first;
                break;
            }
        }
    }
}

static int css_style_type_supported(const th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        if (attr->name_atom == TH_ATTR_TYPE) {
            if (attr->value_len == 0) {
                return 1;
            }
            if (attr->value_len != 8) {
                return 0;
            }
            for (Py_ssize_t offset = 0; offset < 8; offset++) {
                Py_UCS4 codepoint = attr->value[offset];
                if ((node->ns == TH_NS_HTML ? lower_ascii(codepoint) : codepoint) != (Py_UCS4) "text/css"[offset]) {
                    return 0;
                }
            }
            return 1;
        }
    }
    return 1;
}

/* Collect every <style> element's text under root (document order), parsing each
   into a sheet whose selectors are compiled against tree. A sheet is the element's
   child text content, not its descendant text: an SVG or MathML <style> can hold
   further <style> elements, and descendant text would compile the innermost one once
   per enclosing element. A selector that fails to compile leaves compiled[rule] NULL
   so the rule matches nothing. Returns the sheet array and sets *out_count, or NULL
   with *out_count negative on failure. */
static css_sheet *css_collect_sheets(module_state *state, th_tree *tree, th_node *root, Py_ssize_t *out_count) {
    css_sheet *sheets = NULL;
    Py_ssize_t count = 0;
    Py_ssize_t capacity = 0;
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_STYLE) {
            continue;
        }
        if (count == capacity) {
            Py_ssize_t grown = capacity == 0 ? TH_INITIAL_CAPACITY(4) : capacity * 2;
            css_sheet *bigger = PyMem_Realloc(sheets, (size_t)grown * sizeof(css_sheet));
            if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                goto fail;        /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            sheets = bigger;
            capacity = grown;
        }
        const int supported = css_style_type_supported(node);
        sheets[count++] = (css_sheet){.style = node, .supported = supported};
        if (!supported) {
            continue;
        }
        Py_ssize_t text_len = 0;
        Py_UCS4 *text = th_node_child_text(tree, node, &text_len);
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
        css_rule *rules = css_parse_sheet(clean, clean_len, &rule_count, 1);
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
        Py_ssize_t alternative_count = 0;
        int nests_climbs = 0;
        Py_ssize_t chain = 0;
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
                if (!PyErr_ExceptionMatches(state->selector_error)) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    for (Py_ssize_t done = 0; done < rule; done++) {  /* GCOVR_EXCL_LINE: allocation failure */
                        selector_free(compiled[done]);                /* GCOVR_EXCL_LINE: allocation failure */
                    } /* GCOVR_EXCL_LINE: allocation failure */
                    PyMem_Free(compiled);              /* GCOVR_EXCL_LINE: allocation failure */
                    css_free_rules(rules, rule_count); /* GCOVR_EXCL_LINE: allocation failure */
                    PyMem_Free(clean);                 /* GCOVR_EXCL_LINE: allocation failure */
                    goto fail;                         /* GCOVR_EXCL_LINE: allocation failure */
                }
                /* an unsupported or invalid selector list drops its rule (it matches nothing) */
                PyErr_Clear();
            } else {
                css_prioritize_subject(compiled[rule]);
                alternative_count += compiled[rule]->count;
                nests_climbs |= compiled[rule]->climbs > 1;
                chain = compiled[rule]->chain > chain ? compiled[rule]->chain : chain;
            }
        }
        sheets[count - 1].clean = clean;
        sheets[count - 1].rules = rules;
        sheets[count - 1].rule_count = rule_count;
        sheets[count - 1].compiled = compiled;
        sheets[count - 1].nests_climbs = nests_climbs;
        sheets[count - 1].chain = chain;
        if (alternative_count == 0) {
            continue;
        }
        css_specificity *specificities = PyMem_Calloc((size_t)alternative_count, sizeof(css_specificity));
        if (specificities == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto fail;               /* GCOVR_EXCL_LINE: allocation failure */
        }
        sheets[count - 1].specificities = specificities;
        Py_ssize_t offset = 0;
        for (Py_ssize_t rule = 0; rule < rule_count; rule++) {
            if (compiled[rule] == NULL) {
                continue;
            }
            for (int alt = 0; alt < compiled[rule]->count; alt++) {
                sel_specificity(&compiled[rule]->alts[alt], &specificities[offset].ids, &specificities[offset].classes,
                                &specificities[offset].types);
                offset++;
            }
        }
    }
    *out_count = count;
    return sheets;
fail:                               /* GCOVR_EXCL_LINE: allocation-failure path */
    css_free_sheets(sheets, count); /* GCOVR_EXCL_LINE: allocation-failure path */
    *out_count = -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
    return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
}

static int css_style_types_moved(const css_sheet *sheets, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        if (css_style_type_supported(sheets[index].style) != sheets[index].supported) {
            return 1;
        }
    }
    return 0;
}

/* An edit that links or unlinks a <style>, or a node inside one, or writes the text of one, clears the cache itself
   (handle_clear_css_edit). An attribute write relinks nothing and clears nothing, so once the tree's attribute version
   moves, the cache checks whether a write changed which <style> elements count as CSS. */
static css_sheet *css_cached_sheets(module_state *state, HandleObject *handle, Py_ssize_t *out_count) {
    uint32_t attr_gen = th_tree_attr_generation(handle->tree);
    const uint64_t attr_version = th_tree_attr_version(handle->tree);
    if (!handle->css_sheets_ready || handle->css_sheet_attr_gen != attr_gen ||
        (handle->css_sheet_attr_version != attr_version &&
         css_style_types_moved(handle->css_sheets, handle->css_sheet_count))) {
        handle_clear_css_cache(handle);
        Py_ssize_t count = 0;
        css_sheet *sheets = css_collect_sheets(state, handle->tree, th_tree_document(handle->tree), &count);
        if (count < 0) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            *out_count = -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        handle->css_sheets = sheets;
        handle->css_sheet_count = count;
        Py_ssize_t chain = 0;
        for (Py_ssize_t sheet = 0; sheet < count; sheet++) {
            chain = sheets[sheet].chain > chain ? sheets[sheet].chain : chain;
        }
        handle->css_sheet_chain = chain;
        handle->css_sheet_attr_gen = attr_gen;
        handle->css_sheets_ready = 1;
    }
    handle->css_sheet_attr_version = attr_version;
    *out_count = handle->css_sheet_count;
    return handle->css_sheets;
}

#if defined(__GNUC__)
#define CSS_CASCADE_INLINE inline __attribute__((always_inline))
#else
#define CSS_CASCADE_INLINE inline
#endif

/* Run the whole cascade for element against the collected sheets plus its inline
   style, writing the resolved computed values into out given the parent's computed
   map. On a tree that may nest deep, or under a rule that climbs from its climbs, deep matches the rules that climb as
   a query's stand-in does (sel_deep_query).
   Returns -1 on allocation failure. */
static CSS_CASCADE_INLINE int css_cascade_with(th_node *element, const css_sheet *sheets, Py_ssize_t sheet_count,
                                               th_tree *tree, int quirks, const css_value *parent, css_value *out,
                                               css_computed_cache *cache, const int deep) {
    TH_WORK_STEP();
    css_slot slots[NUM_PROPS] = {0};
    /* the legend memo keeps its own table: it holds one answer per legend, so it needs no cap and must outlive a
       :has() table dropped at CSS_HAS_MEMO_LIMIT; one dir=auto answer per element shares it for the same reason */
    sel_ctx ctx = {tree,
                   element,
                   quirks,
                   NULL,
                   &cache->nth_memo,
                   NULL,
                   NULL,
                   &cache->legend_memo,
                   &cache->position_memo,
                   &cache->value_memo};
    cache->deep_memo.failed |= th_tree_seq_wrapped(tree);
    long order = 0;
    for (Py_ssize_t sheet = 0; sheet < sheet_count; sheet++) {
        const css_sheet *current = &sheets[sheet];
        Py_ssize_t offset = 0;
        for (Py_ssize_t rule = 0; rule < current->rule_count; rule++) {
            sel_compiled *compiled = current->compiled[rule];
            if (compiled == NULL) {
                continue;
            }
            const css_specificity *specificities = current->specificities + offset;
            offset += compiled->count;
            ctx.has_memo = selector_uses_has_memo(compiled) ? &cache->has_memo : NULL;
            int best_a = -1;
            int best_b = 0;
            int best_c = 0;
            for (int alt = 0; alt < compiled->count; alt++) {
                if (!(deep && compiled->climbs
                          ? sel_deep_matches_alt(element, &compiled->alts[alt], &ctx, &cache->deep_memo)
                          : selector_matches_alt(element, &compiled->alts[alt], &ctx))) {
                    continue;
                }
                int spec_a = specificities[alt].ids;
                int spec_b = specificities[alt].classes;
                int spec_c = specificities[alt].types;
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
    for (Py_ssize_t index = 0; index < element->attr_count; index++) {
        if (element->attrs[index].name_atom != TH_ATTR_STYLE || element->attrs[index].value == NULL) {
            continue;
        }
        const css_inline_style *style = css_inline_style_of(cache, element, &element->attrs[index]);
        if (style == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t decl = 0; decl < style->count; decl++) {
            css_apply_decl(slots, &style->decls[decl], 1, 0, 0, 0, order++);
        }
        break;
    }
    for (int index = 0; index < NUM_PROPS; index++) {
        css_resolve(&out[index], &slots[index], &parent[index], &cache->initial[index], CSS_PROPS[index].inherited);
    }
    return 0;
}

static int css_cascade_element(th_node *element, const css_sheet *sheets, Py_ssize_t sheet_count, th_tree *tree,
                               int quirks, const css_value *parent, css_value *out, css_computed_cache *cache) {
    int deep = th_tree_nests_deep(tree);
    for (Py_ssize_t sheet = 0; !deep && sheet < sheet_count; sheet++) {
        deep = sheets[sheet].nests_climbs;
    }
    if (deep) {
        return css_cascade_with(element, sheets, sheet_count, tree, quirks, parent, out, cache, 1);
    }
    return css_cascade_with(element, sheets, sheet_count, tree, quirks, parent, out, cache, 0);
}

/* Cascade element, a child of the innermost path level (a root when the path is empty), and keep it as the new
   innermost level. Returns -1 on allocation failure. */
static int css_path_push(css_computed_cache *cache, th_node *element, const css_sheet *sheets, Py_ssize_t sheet_count,
                         th_tree *tree) {
    static const css_value no_parent[NUM_PROPS] = {0};
    if (css_path_reserve(cache) < 0 || css_entry_reserve(cache) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                     /* GCOVR_EXCL_LINE: allocation failure */
    }
    css_computed_entry *const entry = PyMem_Malloc(sizeof(*entry));
    if (entry == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    const css_computed_entry *const parent = cache->path_len > 0 ? cache->path[cache->path_len - 1] : NULL;
    *entry = (css_computed_entry){.node = element, .depth = parent != NULL ? parent->depth + 1 : 0};
    const int resolved = css_cascade_element(element, sheets, sheet_count, tree, th_tree_quirks(tree),
                                             parent != NULL ? parent->values : no_parent, entry->values, cache);
    if (resolved < 0) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(entry); /* GCOVR_EXCL_LINE: allocation failure */
        return -1;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    css_entry_add(cache, entry);
    css_path_append(cache, entry);
    return 0;
}

/* Returns 0, -1 on allocation failure, or -2 with RecursionError set. */
static int css_compute_map(module_state *state, HandleObject *handle, th_node *element, css_value *out) {
    Py_ssize_t sheet_count = 0;
    const css_sheet *const sheets = css_cached_sheets(state, handle, &sheet_count);
    if (sheet_count < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (sel_stack_check(handle->css_sheet_chain) < 0) {
        return -2;
    }
    const uint64_t version = th_tree_attr_version(handle->tree);
    css_computed_cache *cache = handle->css_computed;
    if (cache != NULL && cache->attr_version != version) {
        css_free_computed(handle);
        cache = NULL;
    }
    if (cache == NULL) {
        cache = css_computed_new(version);
        if (cache == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        handle->css_computed = cache;
        size_t rules = 0;
        for (Py_ssize_t sheet = 0; sheet < sheet_count; sheet++) {
            rules += (size_t)sheets[sheet].rule_count;
        }
        cache->deep_memo.limit = th_css_deep_memo_limit(rules, th_tree_node_count(handle->tree));
    }
    if (cache->path_len > 0 && cache->path[cache->path_len - 1]->node == element) {
        return css_copy_map(out, cache->path[cache->path_len - 1]->values);
    }
    /* Climb to the nearest kept ancestor-or-self: a walk in document order finds the parent on the path, and any other
       order is fewer than 2 * CSS_CHECKPOINT_STRIDE levels from a checkpoint or from levels never cascaded before. */
    css_computed_entry *kept = NULL;
    Py_ssize_t pending = 0;
    for (th_node *node = element; node != NULL && node->type == TH_NODE_ELEMENT; node = node->parent) {
        TH_WORK_STEP();
        kept = css_entry_find(cache, node);
        if (kept != NULL) {
            break;
        }
        if (pending == cache->pending_capacity) {
            size_t capacity;
            size_t bytes;
            const int grew = th_grow_cap((size_t)pending + 1, (size_t)cache->pending_capacity, 16, sizeof(th_node *),
                                         &capacity, &bytes);
            if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
                return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            }
            th_node **bigger = PyMem_Realloc(cache->pending, bytes);
            if (bigger == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;        /* GCOVR_EXCL_LINE: allocation failure */
            }
            cache->pending = bigger;
            cache->pending_capacity = (Py_ssize_t)capacity;
        }
        cache->pending[pending++] = node;
    }
    if (pending == 0) {
        return css_copy_map(out, kept->values);
    }
    /* The path is one chain, so a kept ancestor on it sits at the index its depth gives; one off it is a checkpoint
       the path restarts from. */
    const Py_ssize_t keep = kept != NULL && kept->on_path ? kept->depth - cache->path[0]->depth + 1 : 0;
    css_path_drop(cache, keep, cache->path_len);
    if (kept != NULL && keep == 0) {
        if (css_path_reserve(cache) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                     /* GCOVR_EXCL_LINE: allocation failure */
        }
        css_path_append(cache, kept);
    }
    while (pending > 0) {
        th_node *const next = cache->pending[--pending];
        if (css_path_push(cache, next, sheets, sheet_count, handle->tree) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1; /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    return css_copy_map(out, cache->path[cache->path_len - 1]->values);
}

PyObject *turbohtml_css_computed_style(PyObject *module, PyObject *arg) {
    module_state *state = PyModule_GetState(module);
    if (!is_node(arg, state) || ((NodeObject *)arg)->node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_TypeError, "computed style requires an Element");
        return NULL;
    }
    PyObject *handle_obj = ((NodeObject *)arg)->handle;
    HandleObject *handle = (HandleObject *)handle_obj;
    th_node *element = ((NodeObject *)arg)->node;
    css_value final_map[NUM_PROPS] = {0};
    int status = 0;
    Py_BEGIN_CRITICAL_SECTION(handle_obj); /* per-tree lock: sheet collection and matching read the tree */
    status = css_compute_map(state, handle, element, final_map);
    Py_END_CRITICAL_SECTION();
    if (status < 0) {
        css_free_map(final_map);
        return status == -1 ? PyErr_NoMemory() : NULL; /* GCOVR_EXCL_BR_LINE: only an allocation failure gives -1 */
    }
    PyObject *result = PyTuple_New(NUM_PROPS);
    if (result == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        css_free_map(final_map); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < NUM_PROPS; index++) {
        PyObject *name = PyUnicode_FromStringAndSize(CSS_PROPS[index].name, CSS_PROPS[index].name_len);
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

/* _css_escape_identifier(text): CSS.escape per the CSSOM serialize-an-identifier rules, so a raw class or id can be
   dropped into a selector. Worst case every code point becomes "\XXXXXX " (8 UCS4), which bounds the buffer. */
PyObject *turbohtml_css_escape_identifier(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "escape_identifier() argument must be str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    Py_UCS4 *out = PyMem_Malloc((size_t)(len > 0 ? len : 1) * 8 * sizeof(Py_UCS4));
    if (out == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t at = 0;
    for (Py_ssize_t position = 0; position < len; position++) {
        Py_UCS4 code = PyUnicode_READ(kind, data, position);
        int is_digit = code >= 0x30 && code <= 0x39;
        /* a digit leading the identifier (or following an initial dash) cannot start a CSS name */
        int leading_digit = is_digit && (position == 0 || (position == 1 && PyUnicode_READ(kind, data, 0) == '-'));
        int is_name_char = code >= 0x80 || is_digit || code == '-' || code == '_' || (code >= 0x41 && code <= 0x5A) ||
                           (code >= 0x61 && code <= 0x7A);
        /* a lone "-" cannot stand as an identifier, so it is backslashed exactly like a non-name character */
        int lone_dash = position == 0 && code == '-' && len == 1;
        if (code == 0) {
            out[at++] = 0xFFFD;
        } else if (code <= 0x1F || code == 0x7F || leading_digit) {
            char hex[16];
            int wrote = snprintf(hex, sizeof(hex), "\\%x ", (unsigned int)code);
            for (int index = 0; index < wrote; index++) {
                out[at++] = (Py_UCS4)(unsigned char)hex[index];
            }
        } else if (is_name_char && !lone_dash) {
            out[at++] = code;
        } else {
            out[at++] = '\\';
            out[at++] = code;
        }
    }
    PyObject *result = th_str_from_kind(PyUnicode_4BYTE_KIND, out, at);
    PyMem_Free(out);
    return result;
}
