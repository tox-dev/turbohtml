/* The Tokenizer type, its token iterator, and the tokenize() helper.

   Tokenizer owns a state machine. feed() appends input and returns an iterator
   that yields the tokens completed so far; close() signals end-of-input and
   returns an iterator over whatever remains. tokenize() is the one-shot form:
   feed the whole string, close, iterate.

   The iterator is where the content model is applied: after a start tag for a
   raw-text element (script, style, title, ...) it tells the machine to switch,
   which the spec assigns to tree construction. Only an element the HTML rules
   insert switches, so the Tokenizer follows which open elements are SVG or
   MathML (tk_follow). The conformance hook _tokenize_states drives the machine
   directly without this switch, so the state machine stays a faithful,
   separately testable implementation. */

#include "tokenizer/binding.h"

#include "core/ascii.h"
#include "dom/tree_internal.h"

#include <string.h>

/* One open element on the spine tk_follow keeps. */
typedef struct {
    uint16_t atom;      /* TH_TAG_UNKNOWN for a name outside the tag table, which names then holds */
    uint8_t ns;         /* the namespace a browser gives the element */
    uint8_t html_point; /* an SVG/MathML integration point: the HTML rules process the start tags inside it */
    uint8_t category;   /* TH_TAG_SPECIAL and TH_TAG_SCOPING, which bound the HTML end tags inside it */
    uint8_t in_table;   /* an HTML table or template is open here or below, so a table part inside is honored */
    Py_ssize_t name;    /* where the element's run of names begins */
} tk_open;

typedef struct {
    PyObject_HEAD th_tokenizer *sm;
    PyObject *source; /* borrowed-input owner for one-shot tokenize(), else NULL */
    int closed;       /* set by close()/__exit__; feed() after it is an error until reset() */
    /* Where the token dispatch() last handed over began, so a handler asking for its position reads a field
       instead of the loop writing two Python attributes per token. */
    Py_ssize_t line;
    Py_ssize_t col;
    /* The open elements, innermost last, and back to back the names of those outside the tag table. */
    tk_open *open;
    Py_ssize_t depth;
    Py_ssize_t open_cap;
    Py_UCS4 *names;
    Py_ssize_t names_len;
    Py_ssize_t names_cap;
    Py_ssize_t foreign;     /* how many of the open elements are SVG/MathML */
    int capture_attributes; /* the caller's option; the machine also captures where foreign rules read them */
} TokenizerObject;

typedef struct {
    PyObject_HEAD PyObject *owner; /* the Tokenizer whose machine we read */
} IterObject;

static module_state *state_of(PyObject *self) {
    return PyType_GetModuleState(Py_TYPE(self));
}

static int name_eq(const th_buf *name, const char *literal, size_t len) {
    if ((size_t)name->len != len || name->kind != PyUnicode_1BYTE_KIND) {
        return 0;
    }
    return memcmp(name->data, literal, len) == 0;
}

/* Compare against a string literal without a runtime strlen. */
#define name_is(name, literal) name_eq((name), (literal), sizeof(literal) - 1)

/* A browser switches the tokenizer's content model only for a start tag its HTML rules process. Inside <svg> or <math>
   the foreign-content rules insert <title>, <style>, <xmp>, <script>, ... as ordinary elements and the tokenizer stays
   in the data state, so their content is markup; there, and only there, <![CDATA[ opens a section of text instead of a
   bogus comment. The Tokenizer therefore keeps the spine of open elements with the namespace a browser gives each one
   and follows the tree-construction dispatcher (kept in sync with dom/foreign.h): which start tags the HTML rules
   take, which ones break out of foreign content, and which end tags close a foreign element. It pairs the tags as
   written; the implied tags, the insertion modes and the list of active formatting elements stay with the tree
   builder.

   TK_MAX_DEPTH is the deepest spine kept, the nesting at which the tree builder stops opening elements. An HTML
   element whose end tag is omitted (<p>, <li>, <td>, ...) stays on the spine until an enclosing end tag arrives, so a
   long flat document reaches it too; past it the outer half is forgotten, because the innermost elements are the ones
   that decide the namespace. An end tag then walks at most this many entries.

   While every open element is HTML, which is all of ordinary HTML, the spine is followed on a fast path (tk_follow):
   one table read decides what a start tag does, and an end tag that closes the innermost element compares its name
   with that element's without interning it. */
#define TK_MAX_DEPTH TH_MAX_TREE_DEPTH

/* What a start tag the HTML rules process does, read off its atom: the content model it switches the machine into
   (TK_MODEL, a TH_INIT_* value, 0 for none), whether it begins foreign content, whether a browser leaves no element
   open for it (a void element; <html> and <body> only add attributes to the elements a browser already has, a <head>,
   <frame> or <frameset> it honors never holds an element followed here, and <image> is read as <img>), and whether it
   is a table part, which a browser ignores unless a table or template (TK_TABLE) is open. */
enum {
    TK_MODEL = 7,
    TK_FOREIGN = 8,
    TK_CHILDLESS = 16,
    TK_TABLE_PART = 32,
    TK_TABLE = 64,
};

static const uint8_t TK_START[TH_TAG_XMP + 1] = {
    [TH_TAG_TITLE] = TH_INIT_RCDATA,       [TH_TAG_TEXTAREA] = TH_INIT_RCDATA,   [TH_TAG_STYLE] = TH_INIT_RAWTEXT,
    [TH_TAG_XMP] = TH_INIT_RAWTEXT,        [TH_TAG_IFRAME] = TH_INIT_RAWTEXT,    [TH_TAG_NOEMBED] = TH_INIT_RAWTEXT,
    [TH_TAG_NOFRAMES] = TH_INIT_RAWTEXT,   [TH_TAG_NOSCRIPT] = TH_INIT_RAWTEXT,  [TH_TAG_SCRIPT] = TH_INIT_SCRIPT_DATA,
    [TH_TAG_PLAINTEXT] = TH_INIT_PLAINTEXT, [TH_TAG_SVG] = TK_FOREIGN,           [TH_TAG_MATH] = TK_FOREIGN,
    [TH_TAG_AREA] = TK_CHILDLESS,          [TH_TAG_BASE] = TK_CHILDLESS,         [TH_TAG_BASEFONT] = TK_CHILDLESS,
    [TH_TAG_BGSOUND] = TK_CHILDLESS,       [TH_TAG_BR] = TK_CHILDLESS,           [TH_TAG_COL] = TK_CHILDLESS,
    [TH_TAG_EMBED] = TK_CHILDLESS,         [TH_TAG_HR] = TK_CHILDLESS,           [TH_TAG_IMG] = TK_CHILDLESS,
    [TH_TAG_INPUT] = TK_CHILDLESS,         [TH_TAG_KEYGEN] = TK_CHILDLESS,       [TH_TAG_LINK] = TK_CHILDLESS,
    [TH_TAG_META] = TK_CHILDLESS,          [TH_TAG_PARAM] = TK_CHILDLESS,        [TH_TAG_SOURCE] = TK_CHILDLESS,
    [TH_TAG_TRACK] = TK_CHILDLESS,         [TH_TAG_WBR] = TK_CHILDLESS,          [TH_TAG_HTML] = TK_CHILDLESS,
    [TH_TAG_BODY] = TK_CHILDLESS,          [TH_TAG_HEAD] = TK_CHILDLESS,         [TH_TAG_FRAME] = TK_CHILDLESS,
    [TH_TAG_FRAMESET] = TK_CHILDLESS,      [TH_TAG_IMAGE] = TK_CHILDLESS,        [TH_TAG_CAPTION] = TK_TABLE_PART,
    [TH_TAG_COLGROUP] = TK_TABLE_PART,     [TH_TAG_TBODY] = TK_TABLE_PART,       [TH_TAG_THEAD] = TK_TABLE_PART,
    [TH_TAG_TFOOT] = TK_TABLE_PART,        [TH_TAG_TR] = TK_TABLE_PART,          [TH_TAG_TD] = TK_TABLE_PART,
    [TH_TAG_TH] = TK_TABLE_PART,           [TH_TAG_TABLE] = TK_TABLE,            [TH_TAG_TEMPLATE] = TK_TABLE,
};

static void tk_switch_content(th_tokenizer *sm, uint16_t atom) {
    int model = TK_START[atom] & TK_MODEL;
    if (model != 0) {
        th_tok_switch(sm, (enum th_initial_state)model);
    }
}

/* The tag's atom and its category flags. Every tag the Tokenizer hands over pays this, so a name of at most 8 bytes,
   nearly every tag, is loaded as one word and found through the table's packed-key index; a longer one goes through
   the perfect hash the tree builder interns with. */
static uint16_t tk_atom(const th_buf *name, uint8_t *flags) {
    *flags = 0;
    if (name->kind != PyUnicode_1BYTE_KIND) {
        return TH_TAG_UNKNOWN;
    }
    Py_ssize_t len = name->len; /* a tag name has at least one character */
    /* the word read stays inside the name's allocation, and the bytes past the name are masked off */
    if (len <= 8 && name->cap >= 8) { /* GCOVR_EXCL_BR_LINE: only the fuzz build allocates a name below a word */
        uint64_t key;
        memcpy(&key, name->data, sizeof(key));
#if !PY_LITTLE_ENDIAN
        key = __builtin_bswap64(key);
#endif
        key &= ~(uint64_t)0 >> (64 - 8 * len);
        uint8_t atom = th_tag_key_hash[(key * TH_TAG_KEY_MULTIPLIER) >> TH_TAG_KEY_SHIFT];
        if (th_tag_keys[atom] != key) { /* a slot no name fills holds atom 0, whose key 0 no name packs to */
            return TH_TAG_UNKNOWN;
        }
        *flags = th_tag_table[atom - 1].flags;
        return atom;
    }
    const unsigned char *str = (const unsigned char *)name->data;
    size_t slot = ((size_t)len * TH_TAG_HASH_LENGTH + (size_t)str[0] * TH_TAG_HASH_FIRST +
                   (size_t)str[len - 1] * TH_TAG_HASH_LAST + (size_t)str[len > 1] * TH_TAG_HASH_SECOND) &
                  TH_TAG_HASH_MASK;
    uint8_t index = th_tag_hash[slot];
    if (index == 0) {
        return TH_TAG_UNKNOWN;
    }
    const th_tag_entry *entry = &th_tag_table[index - 1];
    if (entry->name_len != len) {
        return TH_TAG_UNKNOWN;
    }
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        if (str[pos] != (unsigned char)entry->name[pos]) {
            return TH_TAG_UNKNOWN;
        }
    }
    *flags = entry->flags;
    return entry->atom;
}

static int tk_atom_in(uint16_t atom, const uint16_t *atoms, size_t count) {
    for (size_t index = 0; index < count; index++) {
        if (atoms[index] == atom) {
            return 1;
        }
    }
    return 0;
}

#define TK_ATOM_IN(atom, atoms) tk_atom_in(atom, atoms, sizeof(atoms) / sizeof((atoms)[0]))

static const uint16_t TK_SVG_HTML_POINTS[] = {TH_TAG_FOREIGNOBJECT, TH_TAG_DESC, TH_TAG_TITLE};
static const uint16_t TK_MATHML_TEXT_POINTS[] = {TH_TAG_MI, TH_TAG_MO, TH_TAG_MN, TH_TAG_MS, TH_TAG_MTEXT};

/* A MathML annotation-xml is an HTML integration point when its encoding is text/html or application/xhtml+xml in any
   ASCII case. The machine already dropped the duplicates of an attribute name, keeping the first as a browser does. */
static int tk_annotation_xml_is_html(const th_token *token) {
    static const char *const encodings[] = {"text/html", "application/xhtml+xml"};
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_attr *attr = &token->attrs[index];
        if (!name_is(&attr->name, "encoding")) {
            continue;
        }
        for (size_t which = 0; which < sizeof(encodings) / sizeof(encodings[0]); which++) {
            Py_ssize_t len = (Py_ssize_t)strlen(encodings[which]);
            int same = attr->value.len == len; /* a bare attribute has length 0, so its NULL value is never read */
            for (Py_ssize_t pos = 0; same && pos < len; pos++) {
                same = lower_ascii(buf_read(&attr->value, pos)) == (Py_UCS4)(unsigned char)encodings[which][pos];
            }
            if (same) {
                return 1;
            }
        }
        return 0;
    }
    return 0;
}

/* Whether an SVG/MathML element is an integration point: the HTML rules process the start tags inside it, and a tag
   that breaks out of foreign content closes the open elements only down to it. */
static int tk_integration_point(const th_token *token, uint16_t atom, uint8_t ns) {
    if (ns == TH_NS_SVG) {
        return TK_ATOM_IN(atom, TK_SVG_HTML_POINTS);
    }
    return TK_ATOM_IN(atom, TK_MATHML_TEXT_POINTS) ||
           (atom == TH_TAG_ANNOTATION_XML && tk_annotation_xml_is_html(token));
}

/* The HTML start tags that break out of foreign content; <font> does too when it carries color, face or size. */
static int tk_foreign_breakout(const th_token *token, uint16_t atom) {
    static const uint16_t breakout[] = {
        TH_TAG_B,      TH_TAG_BIG,    TH_TAG_BLOCKQUOTE, TH_TAG_BODY,    TH_TAG_BR,    TH_TAG_CENTER, TH_TAG_CODE,
        TH_TAG_DD,     TH_TAG_DIV,    TH_TAG_DL,         TH_TAG_DT,      TH_TAG_EM,    TH_TAG_EMBED,  TH_TAG_H1,
        TH_TAG_H2,     TH_TAG_H3,     TH_TAG_H4,         TH_TAG_H5,      TH_TAG_H6,    TH_TAG_HEAD,   TH_TAG_HR,
        TH_TAG_I,      TH_TAG_IMG,    TH_TAG_LI,         TH_TAG_LISTING, TH_TAG_MENU,  TH_TAG_META,   TH_TAG_NOBR,
        TH_TAG_OL,     TH_TAG_P,      TH_TAG_PRE,        TH_TAG_RUBY,    TH_TAG_S,     TH_TAG_SMALL,  TH_TAG_SPAN,
        TH_TAG_STRONG, TH_TAG_STRIKE, TH_TAG_SUB,        TH_TAG_SUP,     TH_TAG_TABLE, TH_TAG_TT,     TH_TAG_U,
        TH_TAG_UL,     TH_TAG_VAR,
    };
    if (atom != TH_TAG_FONT) {
        return TK_ATOM_IN(atom, breakout);
    }
    for (Py_ssize_t index = 0; index < token->attr_count; index++) {
        const th_buf *name = &token->attrs[index].name;
        if (name_is(name, "color") || name_is(name, "face") || name_is(name, "size")) {
            return 1;
        }
    }
    return 0;
}

/* Close the open elements from `depth` up; their names end where the outermost of them began. */
static void tk_close_from(TokenizerObject *self, Py_ssize_t depth) {
    for (Py_ssize_t index = depth; self->foreign > 0 && index < self->depth; index++) {
        self->foreign -= self->open[index].ns != TH_NS_HTML;
    }
    self->names_len = self->open[depth].name;
    self->depth = depth;
}

/* Close the open SVG/MathML elements down to the nearest integration point or HTML element, as a breakout start tag
   and </p> or </br> do. */
static void tk_pop_foreign(TokenizerObject *self) {
    while (self->depth > 0) {
        const tk_open *top = &self->open[self->depth - 1];
        if (top->ns == TH_NS_HTML || top->html_point) {
            return;
        }
        tk_close_from(self, self->depth - 1);
    }
}

/* The namespace a browser inserts a start tag's element in. The HTML rules take it when the innermost open element is
   an HTML element or an integration point for it; otherwise the foreign-content rules do, which either keep the
   element in that namespace or, for a breakout tag, close the foreign elements and hand it to the HTML rules. */
static uint8_t tk_start_namespace(TokenizerObject *self, const th_token *token, uint16_t atom) {
    if (self->depth > 0 && self->open[self->depth - 1].ns != TH_NS_HTML) {
        const tk_open *top = &self->open[self->depth - 1];
        int html_rules;
        if (top->ns == TH_NS_MATHML && TK_ATOM_IN(top->atom, TK_MATHML_TEXT_POINTS)) {
            html_rules = atom != TH_TAG_MGLYPH && atom != TH_TAG_MALIGNMARK;
        } else if (top->ns == TH_NS_MATHML && top->atom == TH_TAG_ANNOTATION_XML && atom == TH_TAG_SVG) {
            html_rules = 1;
        } else {
            html_rules = top->html_point;
        }
        if (!html_rules) {
            if (!tk_foreign_breakout(token, atom)) {
                return top->ns;
            }
            tk_pop_foreign(self);
        }
    }
    if (atom == TH_TAG_SVG) {
        return TH_NS_SVG;
    }
    return atom == TH_TAG_MATH ? TH_NS_MATHML : TH_NS_HTML;
}

static int tk_in_table(const TokenizerObject *self) {
    return self->depth > 0 && self->open[self->depth - 1].in_table;
}

static int tk_table_part(uint16_t atom) {
    return (TK_START[atom] & TK_TABLE_PART) != 0;
}

/* Whether a start tag the HTML rules process leaves no element open (TK_CHILDLESS, or a table part outside a table). */
static int tk_html_childless(const TokenizerObject *self, uint16_t atom) {
    return (TK_START[atom] & TK_CHILDLESS) != 0 || (tk_table_part(atom) && !tk_in_table(self));
}

/* Open the start tag's element. Returns -1 when the spine cannot grow. */
static int tk_push(TokenizerObject *self, const th_token *token, uint16_t atom, uint8_t flags, uint8_t ns) {
    if (self->depth == TK_MAX_DEPTH) {
        Py_ssize_t dropped = TK_MAX_DEPTH / 2;
        for (Py_ssize_t index = 0; self->foreign > 0 && index < dropped; index++) {
            self->foreign -= self->open[index].ns != TH_NS_HTML;
        }
        Py_ssize_t shift = self->open[dropped].name;
        self->depth -= dropped;
        self->names_len -= shift;
        memmove(self->open, self->open + dropped, (size_t)self->depth * sizeof(tk_open));
        if (shift > 0) {
            memmove(self->names, self->names + shift, (size_t)self->names_len * sizeof(Py_UCS4));
        }
        for (Py_ssize_t index = 0; index < self->depth; index++) {
            self->open[index].name -= shift;
        }
    }
    /* a known tag is told apart by its atom; any other by its name, already lowercased by the machine */
    Py_ssize_t name_len = atom == TH_TAG_UNKNOWN ? token->name.len : 0;
    size_t cap;
    size_t bytes;
    if (self->depth == self->open_cap) {
        /* GCOVR_EXCL_BR_START: the spine is capped far below a size that overflows, and allocation cannot fail in a
           test */
        tk_open *grown = th_grow_cap((size_t)self->depth + 1, (size_t)self->open_cap, 32, sizeof(tk_open), &cap, &bytes)
                             ? PyMem_Realloc(self->open, bytes)
                             : NULL;
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* GCOVR_EXCL_BR_STOP */
        self->open = grown;
        self->open_cap = (Py_ssize_t)cap;
    }
    if (self->names_len + name_len > self->names_cap) {
        /* GCOVR_EXCL_BR_START: a name is no longer than the input, and allocation cannot fail in a test */
        Py_UCS4 *grown = th_grow_cap((size_t)(self->names_len + name_len), (size_t)self->names_cap, 64, sizeof(Py_UCS4),
                                     &cap, &bytes)
                             ? PyMem_Realloc(self->names, bytes)
                             : NULL;
        if (grown == NULL) {
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* GCOVR_EXCL_BR_STOP */
        self->names = grown;
        self->names_cap = (Py_ssize_t)cap;
    }
    for (Py_ssize_t pos = 0; pos < name_len; pos++) {
        self->names[self->names_len + pos] = buf_read(&token->name, pos);
    }
    int in_table = tk_in_table(self);
    tk_open *open = &self->open[self->depth++];
    open->atom = atom;
    open->ns = ns;
    open->name = self->names_len;
    self->names_len += name_len;
    if (ns == TH_NS_HTML) {
        open->html_point = 0;
        open->category = flags & (TH_TAG_SPECIAL | TH_TAG_SCOPING);
        open->in_table = in_table || atom == TH_TAG_TABLE || atom == TH_TAG_TEMPLATE;
    } else {
        /* of the SVG/MathML elements the integration points and every MathML annotation-xml are special, and they
           bound a scope, so an end tag the HTML rules process never closes one or anything below it */
        open->html_point = tk_integration_point(token, atom, ns);
        int scoping = open->html_point || (ns == TH_NS_MATHML && atom == TH_TAG_ANNOTATION_XML);
        open->category = scoping ? TH_TAG_SPECIAL | TH_TAG_SCOPING : 0;
        open->in_table = in_table;
        self->foreign++;
    }
    return 0;
}

/* Whether an end tag names the open element at `index`: a known tag matches by atom, any other by its name. */
static int tk_end_tag_names(const TokenizerObject *self, Py_ssize_t index, const th_token *token, uint16_t atom) {
    const tk_open *open = &self->open[index];
    if (open->atom != atom) {
        return 0;
    }
    if (atom != TH_TAG_UNKNOWN) {
        return 1;
    }
    Py_ssize_t end = index + 1 < self->depth ? self->open[index + 1].name : self->names_len;
    int same = end - open->name == token->name.len;
    for (Py_ssize_t pos = 0; same && pos < token->name.len; pos++) {
        same = self->names[open->name + pos] == buf_read(&token->name, pos);
    }
    return same;
}

static int tk_heading(uint16_t atom) {
    return atom >= TH_TAG_H1 && atom <= TH_TAG_H6;
}

static void tk_end_tag(TokenizerObject *self, const th_token *token, uint16_t atom, uint8_t flags) {
    if (self->depth > 0 && self->open[self->depth - 1].ns != TH_NS_HTML && (atom == TH_TAG_P || atom == TH_TAG_BR)) {
        tk_pop_foreign(self); /* </p> and </br> break out of foreign content like their start tags */
    }
    /* while the innermost open elements are SVG/MathML the end tag closes the nearest of them with its name */
    for (Py_ssize_t index = self->depth - 1; index >= 0 && self->open[index].ns != TH_NS_HTML; index--) {
        if (tk_end_tag_names(self, index, token, atom)) {
            tk_close_from(self, index); /* with the unclosed elements inside it */
            return;
        }
    }
    /* Past the first HTML element the HTML rules take the end tag, and those close only an HTML element, as far down
       as they look for it. A table part is sought in table scope, which only a table or a template bounds, and a
       template anywhere. The end tag of a special or formatting element (</div>, </li>, </b>, ...) and </dialog> are
       sought in scope, up to the first scoping element; any other stops at the first special element. An end tag that
       matches nothing is a stray. */
    int table_scoped = atom == TH_TAG_TABLE || tk_table_part(atom);
    uint8_t bound = TH_TAG_SPECIAL;
    if (table_scoped || atom == TH_TAG_TEMPLATE) {
        bound = 0;
    } else if ((flags & (TH_TAG_SPECIAL | TH_TAG_FORMATTING)) != 0 || atom == TH_TAG_DIALOG) {
        bound = TH_TAG_SCOPING;
    }
    int heading = tk_heading(atom);
    for (Py_ssize_t index = self->depth - 1; index >= 0; index--) {
        const tk_open *open = &self->open[index];
        int html = open->ns == TH_NS_HTML;
        /* the end tag of one heading closes any other */
        if (html && (tk_end_tag_names(self, index, token, atom) || (heading && tk_heading(open->atom)))) {
            if (atom == TH_TAG_FORM) {
                /* </form> takes the form alone off the stack; it is a known tag, so no name has to move */
                self->depth--;
                memmove(self->open + index, self->open + index + 1, (size_t)(self->depth - index) * sizeof(tk_open));
            } else {
                tk_close_from(self, index);
            }
            return;
        }
        if ((open->category & bound) != 0 ||
            (table_scoped && html && (open->atom == TH_TAG_TABLE || open->atom == TH_TAG_TEMPLATE))) {
            return;
        }
    }
}

/* Put the machine in the state the innermost open element calls for once a tag in foreign content is followed. */
static void tk_sync(const TokenizerObject *self, th_tokenizer *sm) {
    int foreign = self->depth > 0 && self->open[self->depth - 1].ns != TH_NS_HTML;
    th_tok_set_cdata(sm, foreign);
    if (!self->capture_attributes) {
        /* the foreign-content rules read a start tag's attributes (tk_foreign_breakout, tk_annotation_xml_is_html),
           so the machine keeps them there; tk_hide_attrs keeps them out of what the caller sees */
        th_tok_capture_attributes(sm, foreign);
    }
}

/* Follow a start tag on the spine with the full rules and put `sm` in the state tree construction would before it reads
   on. Returns -1 when the spine cannot grow. */
static int tk_track_start(TokenizerObject *self, th_tokenizer *sm, const th_token *record, uint16_t atom, uint8_t flags) {
    uint8_t ns = tk_start_namespace(self, record, atom); /* first: a breakout tag closes open foreign elements */
    /* the trailing slash ends only an SVG/MathML element: on an HTML start tag a browser ignores it, so <title/>
       and <script/> still open their element and switch */
    int childless = ns == TH_NS_HTML ? tk_html_childless(self, atom) : record->self_closing;
    if (!childless && tk_push(self, record, atom, flags, ns) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (ns == TH_NS_HTML) {
        tk_switch_content(sm, atom);
    }
    tk_sync(self, sm);
    return 0;
}

/* Follow a start or end tag on the spine. While every open element is HTML, a start tag that opens a known element
   with room on the spine is pushed in place and the end tag of the innermost known element pops it; the rest take the
   full rules. Inlined at both call sites: it runs for every tag the Tokenizer hands over. Returns -1 when the spine
   cannot grow. */
static inline int tk_follow(TokenizerObject *self, th_tokenizer *sm, const th_token *record) {
    uint8_t flags;
    uint16_t atom = tk_atom(&record->name, &flags);
    if (self->foreign == 0) {
        if (record->kind == TH_END_TAG) {
            if (self->depth > 0 && atom != TH_TAG_UNKNOWN && self->open[self->depth - 1].atom == atom) {
                self->depth--;
            } else {
                tk_end_tag(self, record, atom, flags);
            }
            return 0;
        }
        uint8_t start = TK_START[atom];
        /* the capacity doubles from a power of two up to TK_MAX_DEPTH, so room on it is room under the cap */
        if ((start & (TK_FOREIGN | TK_CHILDLESS | TK_TABLE_PART)) == 0 && atom != TH_TAG_UNKNOWN &&
            self->depth < self->open_cap) {
            int in_table = tk_in_table(self) || (start & TK_TABLE) != 0;
            tk_open *open = &self->open[self->depth++];
            *open = (tk_open){atom, TH_NS_HTML, 0, flags & (TH_TAG_SPECIAL | TH_TAG_SCOPING), (uint8_t)in_table,
                              self->names_len};
            if ((start & TK_MODEL) != 0) {
                th_tok_switch(sm, (enum th_initial_state)(start & TK_MODEL));
            }
            return 0;
        }
        if ((start & TK_CHILDLESS) != 0) {
            return 0;
        }
        return tk_track_start(self, sm, record, atom, flags);
    }
    if (record->kind == TH_START_TAG) {
        return tk_track_start(self, sm, record, atom, flags);
    }
    tk_end_tag(self, record, atom, flags);
    tk_sync(self, sm);
    return 0;
}

/* Attributes the machine captured for tk_track_start alone are not part of the token the caller asked for. Returns
   how many it hid; the caller adds them back once the token is handed over, so the machine still resets every slot it
   filled. */
static Py_ssize_t tk_hide_attrs(const TokenizerObject *self, th_token *record) {
    if (self->capture_attributes) {
        return 0;
    }
    Py_ssize_t hidden = record->attr_count;
    record->attr_count = 0;
    return hidden;
}

static PyObject *iter_new(module_state *state, PyObject *owner) {
    IterObject *self = PyObject_GC_New(IterObject, (PyTypeObject *)state->iter_type);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->owner = Py_NewRef(owner);
    PyObject_GC_Track(self);
    return (PyObject *)self;
}

static int iter_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));               /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(((IterObject *)self)->owner); /* GCOVR_EXCL_BR_LINE: set at creation, dropped only in dealloc */
    return 0;
}

static int iter_clear(PyObject *self) {
    Py_CLEAR(((IterObject *)self)->owner); /* GCOVR_EXCL_BR_LINE: the iterator never sits in a reference cycle, so clear
                                              runs once with a live owner */
    return 0;
}

static void iter_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    iter_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *iter_next(PyObject *self) {
    module_state *state = state_of(self);
    TokenizerObject *owner = (TokenizerObject *)((IterObject *)self)->owner;
    th_tokenizer *sm = owner->sm;
    PyObject *result = NULL;
    /* the shared state machine, its record buffers, and its free list are mutated here;
       lock the owning tokenizer so concurrent feed()/iteration on the free-threaded
       interpreter cannot corrupt them */
    Py_BEGIN_CRITICAL_SECTION(owner);
    th_token *record;
    switch (th_tok_next(sm, &record)) { /* GCOVR_EXCL_BR_LINE: the TH_STEP_ERROR edge needs an allocation failure, which
                                           cannot be forced from a test */
    case TH_STEP_TOKEN: {
        Py_ssize_t hidden = 0;
        if (record->kind == TH_START_TAG || record->kind == TH_END_TAG) {
            if (tk_follow(owner, sm, record) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                result = PyErr_NoMemory();          /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                              /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            hidden = tk_hide_attrs(owner, record);
        }
        /* one call site, so the compiler keeps the token builder inlined in this loop */
        result = token_from_record(state, sm, owner->source, record);
        record->attr_count += hidden;
        break;
    }
    case TH_STEP_ERROR:            /* GCOVR_EXCL_LINE: the only step error is an out-of-memory condition */
        result = PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        break;                     /* GCOVR_EXCL_LINE */
    default:                       /* NEED_MORE or DONE: nothing more from this iterator */
        break;
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

PyDoc_STRVAR(iter_doc, "Iterator over the tokens a Tokenizer has buffered. Yields Token objects.");

static PyType_Slot iter_slots[] = {
    {Py_tp_doc, (void *)iter_doc},
    {Py_tp_dealloc, iter_dealloc},
    {Py_tp_traverse, iter_traverse},
    {Py_tp_clear, iter_clear},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, iter_next},
    TH_SEALED_END,
};

static PyType_Spec iter_spec = {
    .name = "turbohtml._html._TokenIterator",
    .basicsize = sizeof(IterObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | TH_SEALED,
    .slots = iter_slots,
};

static PyObject *tokenizer_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"resolve_references", "capture_source", "capture_attributes", NULL};
    int resolve_references = 1;
    int capture_source = 0;
    int capture_attributes = 1;
    /* a streaming tokenizer starts empty; its options are keyword-only and
       reject any other positional or keyword argument */
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$ppp:Tokenizer", keywords, &resolve_references, &capture_source,
                                     &capture_attributes)) {
        return NULL;
    }
    /* the state machine comes first: tokenizer_dealloc frees it, so the object never exists without one */
    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    TokenizerObject *self = (TokenizerObject *)type->tp_alloc(type, 0);
    if (self == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tok_free(sm); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->source = NULL;
    self->closed = 0;
    self->line = 1;
    self->col = 0;
    self->sm = sm;
    self->capture_attributes = capture_attributes;
    th_tok_set_options(sm, resolve_references, capture_source, capture_attributes);
    return (PyObject *)self;
}

static int tokenizer_traverse(PyObject *self, visitproc visit, void *arg) {
    TokenizerObject *tokenizer = (TokenizerObject *)self;
    Py_VISIT(Py_TYPE(self));     /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(tokenizer->source); /* GCOVR_EXCL_BR_LINE: the failing-visit arm needs a gc callback that errors */
    return 0;
}

static void tokenizer_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    th_tok_free(((TokenizerObject *)self)->sm);
    PyMem_Free(((TokenizerObject *)self)->open);
    PyMem_Free(((TokenizerObject *)self)->names);
    Py_XDECREF(((TokenizerObject *)self)->source);
    type->tp_free(self);
    Py_DECREF(type);
}

static int feed_string(TokenizerObject *tokenizer, PyObject *data) {
    if (!PyUnicode_Check(data)) {
        PyErr_SetString(PyExc_TypeError, "feed() argument must be str");
        return -1;
    }
    th_tok_feed(tokenizer->sm, PyUnicode_KIND(data), PyUnicode_DATA(data), PyUnicode_GET_LENGTH(data));
    return 0;
}

PyDoc_STRVAR(tokenizer_feed_doc, "feed(data)\n--\n\n"
                                 "Append a chunk of markup. Text before an unfinished tag stays buffered until\n"
                                 "more is fed or close() is called.\n\n"
                                 ":param data: the next chunk of markup.\n"
                                 ":returns: an iterator over the tokens that are now complete.\n"
                                 ":raises TypeError: if data is not a str.\n"
                                 ":raises ValueError: if the tokenizer is closed; call reset() to reuse it.");

static PyObject *tokenizer_feed(PyObject *self, PyObject *data) {
    if (((TokenizerObject *)self)->closed) {
        PyErr_SetString(PyExc_ValueError, "cannot feed a closed Tokenizer; call reset() to reuse it");
        return NULL;
    }
    int rc;
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_feed mutates the shared state machine */
    rc = feed_string((TokenizerObject *)self, data);
    Py_END_CRITICAL_SECTION();
    if (rc < 0) {
        return NULL;
    }
    return iter_new(state_of(self), self);
}

PyDoc_STRVAR(tokenizer_close_doc, "close()\n--\n\n"
                                  "Signal end of input, flushing any buffered text and the token in progress.\n\n"
                                  ":returns: an iterator over the final tokens.");

static PyObject *tokenizer_close(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_tokenizer *sm = ((TokenizerObject *)self)->sm;
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_close mutates the shared state machine */
    th_tok_close(sm);
    ((TokenizerObject *)self)->closed = 1;
    Py_END_CRITICAL_SECTION();
    return iter_new(state_of(self), self);
}

PyDoc_STRVAR(tokenizer_reset_doc, "reset()\n--\n\n"
                                  "Discard all input and return to the initial Data state.");

static PyObject *tokenizer_reset(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_reset mutates the shared state machine */
    TokenizerObject *tokenizer = (TokenizerObject *)self;
    th_tok_reset(tokenizer->sm);
    tokenizer->closed = 0;
    tokenizer->line = 1;
    tokenizer->col = 0;
    /* no element is open any more, so the machine is back in HTML content with the caller's options */
    tokenizer->depth = 0;
    tokenizer->names_len = 0;
    tokenizer->foreign = 0;
    th_tok_set_cdata(tokenizer->sm, 0);
    th_tok_capture_attributes(tokenizer->sm, tokenizer->capture_attributes);
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

PyDoc_STRVAR(tokenizer_enter_doc, "__enter__()\n--\n\nEnter a with block; returns the tokenizer itself.");

static PyObject *tokenizer_enter(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return Py_NewRef(self);
}

PyDoc_STRVAR(tokenizer_exit_doc, "__exit__(*exc)\n--\n\n"
                                 "Leave a with block, signaling end of input as close() does. Buffered\n"
                                 "tokens stay available: iterate the tokenizer to drain them.");

static PyObject *tokenizer_exit(PyObject *self, PyObject *Py_UNUSED(args)) {
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_close mutates the shared state machine */
    th_tok_close(((TokenizerObject *)self)->sm);
    ((TokenizerObject *)self)->closed = 1;
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

/* iter(tokenizer) yields the tokens completed so far, like the feed() iterators. */
static PyObject *tokenizer_iter(PyObject *self) {
    return iter_new(state_of(self), self);
}

/* The handlers dispatch() drives, bound once per call: a token then costs one call instead of an attribute lookup
   and a Python-level walk over the token's fields. Held as an array indexed by the enum so binding walks the name
   table rather than testing seven results in one condition, which no single input can exercise every arm of. */
enum { SAX_STARTTAG, SAX_STARTENDTAG, SAX_ENDTAG, SAX_DATA, SAX_COMMENT, SAX_DECL, SAX_PI, SAX_COUNT };

static const char *const HANDLER_NAMES[SAX_COUNT] = {
    "handle_starttag", "handle_startendtag", "handle_endtag", "handle_data",
    "handle_comment",  "handle_decl",        "handle_pi",
};

typedef struct {
    PyObject *fn[SAX_COUNT];
} sax_handlers;

static void handlers_release(sax_handlers *bound) {
    for (int index = 0; index < SAX_COUNT; index++) {
        Py_XDECREF(bound->fn[index]);
    }
}

static int handlers_bind(sax_handlers *bound, PyObject *handler) {
    for (int index = 0; index < SAX_COUNT; index++) {
        bound->fn[index] = NULL;
    }
    for (int index = 0; index < SAX_COUNT; index++) {
        bound->fn[index] = PyObject_GetAttrString(handler, HANDLER_NAMES[index]);
        if (bound->fn[index] == NULL) {
            handlers_release(bound);
            return -1;
        }
    }
    return 0;
}

static PyObject *attrs_of(const th_token *record) {
    PyObject *list = PyList_New(record->attr_count);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < record->attr_count; index++) {
        const th_attr *attr = &record->attrs[index];
        PyObject *name = th_buf_to_str(&attr->name);
        /* a valueless attribute reports "", the empty string the WHATWG tokenizer assigns it */
        PyObject *value = th_buf_to_str(&attr->value);
        /* GCOVR_EXCL_BR_START: a failed name or value is an allocation failure, which no test can force */
        PyObject *pair = (name != NULL && value != NULL) ? PyTuple_Pack(2, name, value) : NULL;
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(name);
        Py_XDECREF(value);
        if (pair == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyList_SET_ITEM(list, index, pair);
    }
    return list;
}

/* A TEXT run is either an untouched span of the machine's input or its own buffer; the span is resolved here rather
   than held, because the next step may move the storage under it. */
static PyObject *text_of(const th_tokenizer *sm, const th_token *record) {
    if (!record->is_slice) {
        return th_buf_to_str(&record->text);
    }
    int kind;
    const char *data = th_tok_input_data(sm, &kind);
    return th_str_from_kind(kind, data + record->src_start * kind, record->src_len);
}

/* The DOCTYPE spelled as html.parser hands it to handle_decl: the leading "<!" and trailing ">" removed. */
static PyObject *doctype_of(const th_token *record) {
    PyObject *name = th_buf_to_str(&record->name);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *decl = NULL;
    if (record->has_public_id) {
        PyObject *public_id = th_buf_to_str(&record->public_id);
        PyObject *system_id = record->has_system_id ? th_buf_to_str(&record->system_id) : NULL;
        /* a NULL identifier is an allocation failure; whether a system id follows the public one is real */
        if (public_id != NULL && (system_id != NULL || !record->has_system_id)) { /* GCOVR_EXCL_BR_LINE */
            decl = system_id != NULL ? th_str_format("DOCTYPE %U PUBLIC \"%U\" \"%U\"", name, public_id, system_id)
                                     : th_str_format("DOCTYPE %U PUBLIC \"%U\"", name, public_id);
        }
        Py_XDECREF(public_id);
        Py_XDECREF(system_id);
    } else if (record->has_system_id) {
        PyObject *system_id = th_buf_to_str(&record->system_id);
        if (system_id != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            decl = th_str_format("DOCTYPE %U SYSTEM \"%U\"", name, system_id);
        }
        Py_XDECREF(system_id);
    } else {
        decl = record->name.len != 0 ? th_str_format("DOCTYPE %U", name) : PyUnicode_FromString("DOCTYPE");
    }
    Py_DECREF(name);
    return decl;
}

static PyObject *pi_of(const th_token *record) {
    PyObject *target = th_buf_to_str(&record->name);
    if (target == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (record->text.len == 0) {
        return target;
    }
    PyObject *data = th_buf_to_str(&record->text);
    if (data == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(target); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = th_str_format("%U %U", target, data);
    Py_DECREF(target);
    Py_DECREF(data);
    return result;
}

PyDoc_STRVAR(tokenizer_dispatch_doc, "dispatch(handler)\n--\n\n"
                                     "Drive handler's handle_* methods over the tokens completed so far.\n\n"
                                     "The same tokens iterating the tokenizer would yield, delivered without\n"
                                     "building a Token for each one.\n\n"
                                     ":param handler: an object with the html.parser handle_* methods.\n"
                                     ":raises AttributeError: if handler is missing one of them.");

static PyObject *tokenizer_dispatch(PyObject *self, PyObject *handler) {
    sax_handlers bound;
    if (handlers_bind(&bound, handler) < 0) {
        return NULL;
    }
    TokenizerObject *tokenizer = (TokenizerObject *)self;
    th_tokenizer *sm = tokenizer->sm;
    for (;;) {
        PyObject *first = NULL;
        PyObject *second = NULL;
        PyObject *target = NULL;
        int drained = 0;
        /* the state machine, its record buffers and its free list are mutated here, so the owning tokenizer is
           locked while a record is read and copied out; the handler runs outside the lock */
        Py_BEGIN_CRITICAL_SECTION(self);
        th_token *record;
        switch (th_tok_next(sm, &record)) { /* GCOVR_EXCL_BR_LINE: the TH_STEP_ERROR edge needs an allocation
                                               failure, which cannot be forced from a test */
        case TH_STEP_TOKEN:
            tokenizer->line = record->line;
            tokenizer->col = record->col;
            if (record->kind == TH_START_TAG) {
                if (tk_follow(tokenizer, sm, record) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    PyErr_NoMemory();                       /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                Py_ssize_t hidden = tk_hide_attrs(tokenizer, record);
                first = th_buf_to_str(&record->name);
                second = attrs_of(record);
                record->attr_count += hidden;
                target = record->self_closing ? bound.fn[SAX_STARTENDTAG] : bound.fn[SAX_STARTTAG];
            } else if (record->kind == TH_END_TAG) {
                tk_follow(tokenizer, sm, record); /* an end tag only shrinks the spine, so it cannot fail */
                first = th_buf_to_str(&record->name);
                target = bound.fn[SAX_ENDTAG];
            } else if (record->kind == TH_TEXT) {
                first = text_of(sm, record);
                target = bound.fn[SAX_DATA];
            } else if (record->kind == TH_COMMENT) {
                first = th_buf_to_str(&record->text);
                target = bound.fn[SAX_COMMENT];
            } else if (record->kind == TH_PI) {
                first = pi_of(record);
                target = bound.fn[SAX_PI];
            } else {
                first = doctype_of(record);
                target = bound.fn[SAX_DECL];
            }
            break;
        case TH_STEP_ERROR:   /* GCOVR_EXCL_LINE: the only step error is an out-of-memory condition */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            break;            /* GCOVR_EXCL_LINE */
        default:              /* NEED_MORE or DONE: every buffered token has been handed over */
            drained = 1;
            break;
        }
        Py_END_CRITICAL_SECTION();
        if (drained) {
            break;
        }
        /* a failed conversion, or the step error above, leaves the exception set */
        /* GCOVR_EXCL_BR_START: every arm here is an allocation failure, which no test can force */
        if (first == NULL || (target == bound.fn[SAX_STARTTAG] && second == NULL) ||
            (target == bound.fn[SAX_STARTENDTAG] && second == NULL)) {
            /* GCOVR_EXCL_BR_STOP */
            Py_XDECREF(first);        /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(second);       /* GCOVR_EXCL_LINE: allocation-failure path */
            handlers_release(&bound); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *call = second != NULL ? PyObject_CallFunctionObjArgs(target, first, second, NULL)
                                        : PyObject_CallFunctionObjArgs(target, first, NULL);
        Py_DECREF(first);
        Py_XDECREF(second);
        if (call == NULL) {
            handlers_release(&bound);
            return NULL;
        }
        Py_DECREF(call);
    }
    handlers_release(&bound);
    Py_RETURN_NONE;
}

PyDoc_STRVAR(tokenizer_position_doc, "position()\n--\n\n"
                                     "The 1-based line and 0-based column where the last dispatched token began.");

static PyObject *tokenizer_position(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    TokenizerObject *tokenizer = (TokenizerObject *)self;
    return Py_BuildValue("(nn)", tokenizer->line, tokenizer->col);
}

static PyMethodDef tokenizer_methods[] = {
    {"dispatch", tokenizer_dispatch, METH_O, tokenizer_dispatch_doc},
    {"position", tokenizer_position, METH_NOARGS, tokenizer_position_doc},
    {"feed", tokenizer_feed, METH_O, tokenizer_feed_doc},
    {"close", tokenizer_close, METH_NOARGS, tokenizer_close_doc},
    {"reset", tokenizer_reset, METH_NOARGS, tokenizer_reset_doc},
    {"__enter__", tokenizer_enter, METH_NOARGS, tokenizer_enter_doc},
    {"__exit__", tokenizer_exit, METH_VARARGS, tokenizer_exit_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(tokenizer_doc,
             "Tokenizer(*, resolve_references=True, capture_source=False, capture_attributes=True)\n--\n\n"
             "Streaming HTML tokenizer. Feed markup with feed() and iterate the\n"
             "returned iterators; call close() at the end, or use the tokenizer as a\n"
             "context manager so leaving the with block signals end of input, then\n"
             "iterate the tokenizer itself for the remaining tokens. For a whole\n"
             "string at once use tokenize().\n\n"
             ":param resolve_references: fold character references into the surrounding\n"
             "    text run; when False each one is emitted as its own CHARACTER_REFERENCE\n"
             "    token (its data the resolved value, its source the verbatim reference).\n"
             "    Attribute-value references are always resolved.\n"
             ":param capture_source: record each markup token's verbatim source slice,\n"
             "    available as Token.source.\n"
             ":param capture_attributes: retain tag attributes; disable this when only tag names and\n"
             "    text are needed to skip attribute storage and copying.");

static PyType_Slot tokenizer_slots[] = {
    {Py_tp_doc, (void *)tokenizer_doc},
    {Py_tp_new, tokenizer_new},
    {Py_tp_dealloc, tokenizer_dealloc},
    {Py_tp_traverse, tokenizer_traverse},
    {Py_tp_iter, tokenizer_iter},
    {Py_tp_methods, tokenizer_methods},
    {0, NULL},
};

static PyType_Spec tokenizer_spec = {
    .name = "turbohtml._html.Tokenizer",
    .basicsize = sizeof(TokenizerObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .slots = tokenizer_slots,
};

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in core/common.h and called from core/module.c
PyObject *turbohtml_tokenize(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"", "resolve_references", "capture_source", "capture_attributes", NULL};
    PyObject *arg;
    int resolve_references = 1;
    int capture_source = 0;
    int capture_attributes = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|$ppp:tokenize", keywords, &arg, &resolve_references,
                                     &capture_source, &capture_attributes)) {
        return NULL;
    }
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "tokenize() argument must be str");
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *tokenizer = PyObject_CallNoArgs(state->tokenizer_type);
    if (tokenizer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tokenizer *sm = ((TokenizerObject *)tokenizer)->sm;
    ((TokenizerObject *)tokenizer)->capture_attributes = capture_attributes;
    th_tok_set_options(sm, resolve_references, capture_source, capture_attributes);
    Py_ssize_t length = PyUnicode_GET_LENGTH(arg);
    if (PyUnicode_FindChar(arg, '\r', 0, length, 1) == -1) {
        /* nothing to normalize: borrow the string's storage instead of
           copying the whole document; the tokenizer keeps the string alive */
        th_tok_borrow_input(sm, PyUnicode_KIND(arg), PyUnicode_DATA(arg), length);
        ((TokenizerObject *)tokenizer)->source = Py_NewRef(arg);
    } else {
        th_tok_feed(sm, PyUnicode_KIND(arg), PyUnicode_DATA(arg), length);
    }
    th_tok_close(sm);
    PyObject *iterator = iter_new(state, tokenizer);
    Py_DECREF(tokenizer);
    return iterator;
}

/* Format one record the way the html5lib tokenizer tests express tokens, so the
   test harness can compare directly. Used only by _tokenize_states. */
static PyObject *record_as_test_tuple(const th_tokenizer *sm, const th_token *record) {
    if (record->is_slice) {
        int kind;
        const char *data = th_tok_input_data(sm, &kind);
        PyObject *text = th_str_from_kind(kind, data + record->src_start * kind, record->src_len);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(sN)", "Character", text);
    }
    if (record->kind == TH_TEXT || record->kind == TH_COMMENT) {
        PyObject *data = th_str_from_kind(record->text.kind, record->text.data, record->text.len);
        if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(sN)", record->kind == TH_TEXT ? "Character" : "Comment", data);
    }
    if (record->kind == TH_PI) {
        PyObject *target = th_str_from_kind(record->name.kind, record->name.data, record->name.len);
        PyObject *data = th_str_from_kind(record->text.kind, record->text.data, record->text.len);
        if (target == NULL || data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_XDECREF(target);               /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(data);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(sNN)", "ProcessingInstruction", target, data);
    }
    if (record->kind == TH_END_TAG) {
        PyObject *name = th_str_from_kind(record->name.kind, record->name.data, record->name.len);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(sN)", "EndTag", name);
    }
    if (record->kind == TH_START_TAG) {
        PyObject *name = th_str_from_kind(record->name.kind, record->name.data, record->name.len);
        PyObject *attrs = PyDict_New();
        if (name == NULL || attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_XDECREF(name);                /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(attrs);               /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < record->attr_count; index++) {
            const th_attr *attr = &record->attrs[index];
            PyObject *key = th_str_from_kind(attr->name.kind, attr->name.data, attr->name.len);
            if (key == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(name);  /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(attrs); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            /* the tokenizer already dropped duplicate attribute names, so each key is unique */
            PyObject *value = th_str_from_kind(attr->value.kind, attr->value.data, attr->value.len);
            /* allocation failure cannot be forced from a test */
            if (value == NULL || PyDict_SetItem(attrs, key, value) < 0) { /* GCOVR_EXCL_BR_LINE */
                Py_XDECREF(value);                                        /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(key);                                           /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(name);                                          /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(attrs);                                         /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            Py_DECREF(key);
            Py_DECREF(value);
        }
        if (record->self_closing) {
            return Py_BuildValue("(sNNO)", "StartTag", name, attrs, Py_True);
        }
        return Py_BuildValue("(sNN)", "StartTag", name, attrs);
    }
    /* DOCTYPE */
    PyObject *name = record->name.len ? th_str_from_kind(record->name.kind, record->name.data, record->name.len)
                                      : Py_NewRef(Py_None);
    PyObject *public_id = record->has_public_id
                              ? th_str_from_kind(record->public_id.kind, record->public_id.data, record->public_id.len)
                              : Py_NewRef(Py_None);
    PyObject *system_id = record->has_system_id
                              ? th_str_from_kind(record->system_id.kind, record->system_id.data, record->system_id.len)
                              : Py_NewRef(Py_None);
    /* allocation failure cannot be forced from a test */
    if (name == NULL || public_id == NULL || system_id == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(name);                                         /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(public_id);                                    /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(system_id);                                    /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return Py_BuildValue("(sNNNO)", "DOCTYPE", name, public_id, system_id, record->force_quirks ? Py_False : Py_True);
}

static int initial_state_from_name(const char *name, enum th_initial_state *out) {
    if (strcmp(name, "Data state") == 0) {
        *out = TH_INIT_DATA;
    } else if (strcmp(name, "RCDATA state") == 0) {
        *out = TH_INIT_RCDATA;
    } else if (strcmp(name, "RAWTEXT state") == 0) {
        *out = TH_INIT_RAWTEXT;
    } else if (strcmp(name, "Script data state") == 0) {
        *out = TH_INIT_SCRIPT_DATA;
    } else if (strcmp(name, "PLAINTEXT state") == 0) {
        *out = TH_INIT_PLAINTEXT;
    } else if (strcmp(name, "CDATA section state") == 0) {
        *out = TH_INIT_CDATA;
    } else {
        PyErr_Format(PyExc_ValueError, "unknown initial state '%s'", name);
        return -1;
    }
    return 0;
}

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in core/common.h and called from core/module.c
PyObject *turbohtml_tokenize_states(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    const char *state_name;
    PyObject *last_tag = Py_None;
    int storage_kind = 1;
    if (!PyArg_ParseTuple(args, "Us|Oi:_tokenize_states", &text, &state_name, &last_tag, &storage_kind)) {
        return NULL;
    }
    enum th_initial_state initial;
    if (initial_state_from_name(state_name, &initial) < 0) {
        return NULL;
    }
    if (storage_kind != 1 && storage_kind != 2 && storage_kind != 4) {
        PyErr_SetString(PyExc_ValueError, "storage_kind must be 1, 2 or 4");
        return NULL;
    }
    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (storage_kind > (int)PyUnicode_KIND(text)) {
        th_tok_widen_input(sm, storage_kind);
    }
    if (last_tag != Py_None) {
        if (!PyUnicode_Check(last_tag)) {
            PyErr_SetString(PyExc_TypeError, "last_start_tag must be str or None");
            th_tok_free(sm);
            return NULL;
        }
        Py_ssize_t len = PyUnicode_GET_LENGTH(last_tag);
        int kind = PyUnicode_KIND(last_tag);
        const void *data = PyUnicode_DATA(last_tag);
        Py_UCS4 *buffer = PyMem_New(Py_UCS4, len ? len : 1); /* GCOVR_EXCL_BR_LINE: size-overflow guard */
        if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            th_tok_free(sm);         /* GCOVR_EXCL_LINE: allocation-failure path */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < len; index++) {
            buffer[index] = PyUnicode_READ(kind, data, index);
        }
        th_tok_set_initial(sm, initial, buffer, len);
        PyMem_Free(buffer);
    } else {
        th_tok_set_initial(sm, initial, NULL, 0);
    }
    th_error_sink errors = {0};
    th_tok_set_error_sink(sm, &errors);
    th_tok_feed(sm, PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text));
    th_tok_close(sm);

    PyObject *out = PyList_New(0);
    if (out == NULL) {               /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tok_free(sm);             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_token *record;
    enum th_step step;
    while ((step = th_tok_next(sm, &record)) == TH_STEP_TOKEN) {
        PyObject *tuple = record_as_test_tuple(sm, record);
        /* allocation failure cannot be forced from a test */
        if (tuple == NULL || PyList_Append(out, tuple) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(tuple);                                /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(out);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
            th_error_sink_free(&errors);                      /* GCOVR_EXCL_LINE: allocation-failure path */
            th_tok_free(sm);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(tuple);
    }
    th_tok_free(sm);
    if (step == TH_STEP_ERROR || th_error_sink_failed(&errors)) { /* GCOVR_EXCL_BR_LINE: an allocation failed */
        Py_DECREF(out);                                           /* GCOVR_EXCL_LINE: allocation-failure path */
        th_error_sink_free(&errors);                              /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_error_sink preprocessing = {0};
    th_input_stream_errors(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text), &preprocessing);
    int merge_failed = th_error_sink_merge(&errors, &preprocessing) < 0;
    th_error_sink_free(&preprocessing);
    if (merge_failed) {              /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
        th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the conformance hook also reports the WHATWG parse errors, as (code, line, col)
       triples, so the suite's expected `errors` arrays gate them */
    PyObject *reported = PyList_New(errors.len);
    if (reported == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
        th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < errors.len; index++) {
        PyObject *entry =
            Py_BuildValue("(snn)", errors.items[index].code, errors.items[index].line, errors.items[index].col);
        if (entry == NULL) {             /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(reported);         /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
            th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyList_SET_ITEM(reported, index, entry);
    }
    th_error_sink_free(&errors);
    return Py_BuildValue("(NN)", out, reported);
}

int tokenizer_register(PyObject *module, module_state *state) {
    state->iter_type = PyType_FromModuleAndSpec(module, &iter_spec, NULL);
    if (state->iter_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->tokenizer_type = PyType_FromModuleAndSpec(module, &tokenizer_spec, NULL);
    if (state->tokenizer_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return PyModule_AddObjectRef(module, "Tokenizer", state->tokenizer_type);
}
