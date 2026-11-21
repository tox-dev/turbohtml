/* Turns a scraped page into clean GitHub-Flavored Markdown -- headings, lists,
   tables, links and emphasis -- so a `scrape -> markdown` pipeline needs no
   second dependency.

   It shares the sbuf buffer, need_text(), the tag atoms, the attribute lookup,
   and the block/whitespace predicates from serialize/internal.h. The walk is a
   depth-first descent over the finished tree on a heap stack of frames, so no
   nesting depth exhausts the C stack: block elements are laid out vertically
   with collapsed blank-line margins, inline elements wrap their content in
   markdown markers, and runs of whitespace collapse to one space the way the CSS
   normal flow does. Output is opinionated GFM with no options.

   The single non-obvious piece is whitespace. Text whitespace is never emitted
   eagerly: a run sets space_pending, and the deferred space is flushed (as one
   space) only just before the next real character, and only when the current
   line already holds content. That one rule collapses runs, drops the space at
   block and line starts, and -- because a closing marker does not flush -- moves a
   trailing inner space out of `**bold** ` instead of leaving `**bold **`, which
   is invalid markdown. */

#include "serialize/internal.h"
#include "core/work_steps.h"
#include "css/cssom/cssom.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <limits.h>
#include <string.h>

/* Whether an element's own Markdown markup is dropped under the strip/convert
   filter, leaving its children to render transparently in the surrounding flow.
   A strip set names the tags to drop; a convert set names the only tags to keep,
   so every tag outside it is dropped. */
static int md_tag_filtered(const md_opts *opt, uint16_t atom) {
    if (opt->tag_filter == TH_MD_FILTER_NONE) {
        return 0;
    }
    int present = (opt->filter_tags[atom >> 6] >> (atom & 63)) & 1;
    return opt->tag_filter == TH_MD_FILTER_STRIP ? present : !present;
}

md_opts th_markdown_default_opts(void) {
    md_opts opt = {0};
    opt.heading_style = TH_MD_HEADING_ATX;
    opt.bullets = "-";
    opt.strong = "**";
    opt.emphasis = "*";
    opt.strikethrough = "~~";
    opt.keep_emphasis = 1;
    opt.keep_strikethrough = 1;
    opt.code_block_style = TH_MD_CODE_FENCED;
    opt.code_language = "";
    opt.link_style = TH_MD_LINK_INLINE;
    opt.autolink = 1;
    opt.skip_internal_links = 0;
    opt.image_mode = TH_MD_IMAGE_MARKDOWN;
    opt.default_image_alt = "";
    opt.base_url = "";
    opt.table_mode = TH_MD_TABLE_MARKDOWN;
    opt.table_header = TH_MD_HEADER_FIRST;
    opt.cell_blocks = TH_MD_CELL_HTML;
    opt.quote_open = "\"";
    opt.quote_close = "\"";
    opt.escape_mode = TH_MD_ESCAPE_MINIMAL;
    opt.escape_asterisks = 1;
    opt.escape_underscores = 1;
    opt.line_break = TH_MD_BREAK_SPACES;
    opt.wrap_width = 0;
    opt.wrap_list_items = 0;
    opt.wrap_links = 1;
    opt.transliterate = 0;
    opt.document_strip = TH_MD_DOC_STRIP;
    opt.sub = "";
    opt.sup = "";
    opt.google_doc = 0;
    opt.google_list_indent = 36;
    opt.hide_strikethrough = 0;
    opt.raw_list_html = 1;
    opt.tag_filter = TH_MD_FILTER_NONE;
    opt.converters = NULL;
    opt.wrap_node = NULL;
    opt.wrap_node_ctx = NULL;
    return opt;
}

/* A reference-style link or image collected during the walk, flushed as a
   numbered "[n]: url" definition at the end. */
typedef struct {
    const Py_UCS4 *url;
    Py_ssize_t url_len;
    const Py_UCS4 *title;
    Py_ssize_t title_len;
} md_reference;

/* An emphasis/strikethrough marker or a link's opening bracket whose run is deferred
   until the first visible character of its content, so a leading inner space lands
   outside the marker (`a<b> x</b>` -> `a **x**`, never the invalid `a** x**`). The
   open markers stack outermost first.

   open/close hold what is written for the run; they start as the Markdown delimiter
   and are swapped at emit time when the delimiter would not round-trip: for the other
   emphasis character where that one can pair, else for the element's HTML tag (CommonMark
   6 allows raw inline HTML). The flanking context of a single-text-node element is kept
   as two bits, filled in O(1) so the decision needs no subtree scan. */
enum md_html { MD_HTML_NONE, MD_HTML_EM, MD_HTML_STRONG, MD_HTML_DEL, MD_HTML_S, MD_HTML_STRIKE };

/* The raw-HTML fallback tags, indexed by enum md_html minus one. */
static const char *const MD_HTML_TAGS[][2] = {
    {"<em>", "</em>"}, {"<strong>", "</strong>"}, {"<del>", "</del>"}, {"<s>", "</s>"}, {"<strike>", "</strike>"},
};

enum {
    MD_MARK_STRIKE = 1,      /* a ~~ run: it must not touch another ~~ run */
    MD_MARK_OPEN_PUNCT = 2,  /* the content starts with punctuation */
    MD_MARK_CLOSE_FAILS = 4, /* the content ends with punctuation and an ordinary character follows */
};

typedef struct {
    const char *open;
    const char *close;
    th_node *node;       /* the wrapped element when it has an HTML fallback, else NULL */
    Py_ssize_t open_at;  /* where the open run was written in out */
    Py_ssize_t open_len; /* how many code points it takes there: open is UTF-8, so not its strlen */
    uint8_t html;        /* enum md_html */
    uint8_t flags;       /* MD_MARK_* */
    uint8_t emitted;     /* the open run was written, so the close run must be too */
    uint8_t spanning;    /* HTML marker for deep nesting */
} md_marker;

/* The layout state one list threads through its items, including the items it
   finds inside a wrapper element it looks through. */
typedef struct {
    Py_ssize_t number;     /* the next ordered item's number */
    Py_ssize_t sub_indent; /* how far content attached to the last item indents: its marker's width */
    Py_UCS4 bullet;        /* a code point, since a configured bullet may be non-ASCII */
    char delimiter;        /* what follows an ordered item's number */
    int ordered;
    int loose;
    int item_seen;          /* an item has opened, so content between items continues it */
    int in_run;             /* content between items is in the middle of an inline run */
    th_node *checked_item;  /* the first item md_enter_list decided against the text before the list */
    Py_ssize_t checked_len; /* the output length at that decision */
} md_list_state;

/* How a frame walks its node's children. */
enum md_walk {
    MD_WALK_NONE,      /* no children: the frame only restores state once the frames above it finish */
    MD_WALK_INLINE,    /* each child renders in the inline flow */
    MD_WALK_BLOCK,     /* inline runs open lines, block children lay out as blocks */
    MD_WALK_LIST,      /* items take markers, other content continues the last item */
    MD_WALK_WRAPPER,   /* an element wrapping list items, laid out as part of the owner list */
    MD_WALK_CELL_FLAT, /* a block inside a pipe cell, flattened to inline text */
    MD_WALK_TABLE,     /* captions, then cells row by row */
};

/* What a frame writes or restores once its children are done. */
enum md_leave {
    MD_LEAVE_NONE,
    MD_LEAVE_WRAP,  /* close the emphasis marker, if it opened */
    MD_LEAVE_QUOTE, /* the closing <q> wrapper */
    MD_LEAVE_LINK,  /* the "](url)" or "][n]" after the link text */
    MD_LEAVE_RAW_LINK,
    MD_LEAVE_NO_WRAP,
    MD_LEAVE_GOOGLE, /* close the CSS-derived markers and restore the inherited style */
    MD_LEAVE_SETEXT,
    MD_LEAVE_ATX,
    MD_LEAVE_ITEM,
    MD_LEAVE_LIST,
    MD_LEAVE_CONVERT, /* hand the rendered children to the converter */
    MD_LEAVE_CELL,    /* collapse the cell onto its row */
    MD_LEAVE_TABLE,
    MD_LEAVE_BLOCKQUOTE, /* mark a quote that opened no block of its own */
    MD_LEAVE_BLOCK_END,  /* the block inside an inline element is done */
    MD_LEAVE_SPACE,      /* a flattened block's end reads as a space */
};

enum md_table_phase {
    MD_TABLE_CAPTIONS,
    MD_TABLE_STRIP,  /* each row a block of space-joined cell text */
    MD_TABLE_PADDED, /* cells rendered into a grid, laid out once every width is known */
    MD_TABLE_ROWS,   /* cells written straight into pipe rows */
};

/* A table being laid out: its rows, where the walk is, and the output state one
   cell's own rendering set aside. */
typedef struct {
    th_node **rows;
    sbuf *grid;         /* padded: each cell's collapsed text */
    Py_ssize_t *widths; /* padded: each column's widest cell */
    th_node *cell;      /* the cell rendered last in the current row, NULL before the first */
    Py_ssize_t count;
    Py_ssize_t columns;
    Py_ssize_t row;
    Py_ssize_t column;
    int phase; /* enum md_table_phase */
    int has_header;
    int in_row;
    sbuf saved_out;
    sbuf saved_prefix;
    Py_ssize_t saved_marker_base;
    int saved_started;
    int saved_line;
    Py_ssize_t saved_line_number_start;
    Py_ssize_t saved_line_number_checked;
    Py_ssize_t saved_break_start;
    Py_ssize_t saved_break_end;
    int saved_space;
    int saved_drop;
    Py_ssize_t saved_table_end;
} md_table;

/* The output state a converted element's children render without, restored before
   the converter runs. */
typedef struct {
    PyObject *tag;
    PyObject *converter; /* borrowed from the options' converter dict */
    sbuf out;
    sbuf prefix;
    Py_ssize_t marker_base;
    Py_ssize_t line_start;
    Py_ssize_t line_checked;
    int started;
    int line_has_content;
    Py_ssize_t line_number_start;
    Py_ssize_t line_number_checked;
    Py_ssize_t break_start;
    Py_ssize_t break_end;
    int space_pending;
    int drop_space;
    int pending_loose;
    int suppress_break;
    int tight;
    int list_depth;
    int g_bold;
    int g_italic;
    int inline_only;
    int deep_block_span;
    Py_UCS4 list_end_marker;
    Py_ssize_t table_end;
    int converting;
} md_converting;

/* One element whose children are being rendered: the child it reached and what it
   writes or restores once they are done. Frames live on a heap stack, so nesting
   costs memory rather than C stack. */
typedef struct {
    th_node *node;
    th_node *child;         /* the child rendered last, NULL before the first */
    Py_ssize_t prefix_base; /* the prefix length (with saved_tight) to restore, or -1 */
    Py_ssize_t marker;      /* the deferred marker this frame opened, or -1 */
    union {
        struct {
            md_list_state state;
            Py_ssize_t start; /* the output length when the list opened */
        } list;
        Py_ssize_t owner; /* wrapper: the list frame whose state it continues */
        struct {
            Py_ssize_t owner; /* the frame numbering the items, or -1 */
            Py_ssize_t number;
            int ordered;
        } flat;
        struct {
            Py_ssize_t mark; /* where the heading text starts */
            int level;
            int outer_in_heading;
        } heading;
        struct {
            const Py_UCS4 *href;
            const Py_UCS4 *title;
            Py_ssize_t href_len;
            Py_ssize_t title_len;
            int relative;
        } link;
        struct {
            Py_ssize_t italic_marker; /* bold uses marker */
            int outer_bold;
            int outer_italic;
        } google;
        md_converting *convert;
        md_table *table;
    };
    int saved_tight;
    int saved_levels; /* the indent_levels to restore with the prefix */
    int in_run;       /* block walk: inside a run of inline children */
    uint8_t walk;
    uint8_t leave;
} md_frame;

/* Frames and markers start in storage on the C stack and move to the heap only past it. The Readability test pages
   and the CodSpeed corpora peak at 39 frames (one page at 67) and 4 markers. */
#define MD_INLINE_FRAMES 64
#define MD_INLINE_MARKERS 16

typedef struct {
    sbuf out;
    th_tree *tree;
    const md_opts *opt;
    void **ordinals;  /* the handle's md_ordinal_cache slot, for a root that is an ordered-list item */
    sbuf prefix;      /* what every continuation line starts with: list indent + "> " quotes */
    md_frame *frames; /* the elements being rendered, outermost first */
    md_frame *inline_frames;
    Py_ssize_t frame_count;
    Py_ssize_t frame_cap;
    md_marker *markers; /* the inline markers still open, outermost first */
    md_marker *inline_markers;
    Py_ssize_t marker_count;
    Py_ssize_t marker_cap;
    Py_ssize_t marker_base;  /* markers below it belong to the output a cell or converter set aside */
    Py_ssize_t open_markers; /* emitted markers that still need closing at a block */
    md_marker *pending;      /* the innermost marker above marker_base, NULL when there is none */
    md_reference *refs;      /* collected reference-link targets */
    Py_ssize_t ref_count;
    Py_ssize_t ref_cap;
    int started;          /* has any block content been emitted yet */
    int line_has_content; /* real content past the prefix/marker on the current line */
    Py_ssize_t line_number_start;
    Py_ssize_t line_number_checked; /* digits up to here are verified as the run line_number_start opens */
    Py_ssize_t line_start;
    Py_ssize_t line_checked;
    int space_pending;          /* a collapsed-away whitespace run is owed one space */
    int pending_word;           /* code points in the word the owed space precedes, for greedy wrapping */
    int no_wrap;                /* >0 inside verbatim/grid/unbreakable content: never insert a wrap break */
    int inline_only;            /* >0 inside link text: a block flattens to inline, never opens a line */
    th_node *link;              /* the link whose text is being written, kept for a converter's content too */
    int in_cell;                /* inside a table cell: a pipe is escaped as it is written, a block turns into HTML */
    int in_heading;             /* inside a heading: a <br> becomes a space, since a heading is one line of text */
    int drop_space;             /* swallow the next pending space (block/inline start) without emitting */
    int pending_loose;          /* the previous block wants a blank line after it */
    int suppress_break;         /* the next block attaches to the current (list marker) line */
    int block_ended;            /* a block in an inline element closed: next content opens a block */
    int interrupt_blank;        /* the next block cannot interrupt the paragraph before it, so a blank line ends it */
    int item_marker;            /* the line holds a list marker alone, so the item's first block takes no blank line */
    int tight;                  /* inside a list item: inline runs do not add blank lines */
    int list_depth;             /* nesting depth of the current list, for bullet cycling */
    int indent_levels;          /* list and quote nesting that indents the prefix, capped by TH_MAX_INDENT_LEVELS */
    Py_ssize_t clone_budget;    /* the attribute characters the parser's clones of an element may still write */
    Py_ssize_t padding_left;    /* the spaces padded tables may still add */
    Py_ssize_t tags_left;       /* the tag characters runs reopened as raw HTML may still add */
    Py_ssize_t list_end;        /* output length when the last list closed, to spot a list right after it */
    Py_ssize_t list_end_prefix; /* the prefix length that list was laid out under */
    Py_UCS4 list_end_marker;    /* its bullet or ordered delimiter; 0 until a list closes */
    Py_ssize_t table_end;       /* output length when the last pipe table closed, -1 when none ends the output */
    Py_ssize_t table_prefix;    /* the prefix length that table was laid out under */
    Py_ssize_t list_over;       /* output length where a reader is out of that list; behind list_end until then */
    int g_bold;                 /* google_doc: a CSS font-weight bold is in force from an ancestor */
    int g_italic;               /* google_doc: a CSS font-style italic is in force from an ancestor */
    int converting;             /* rendering a converted element's children, which its converter extends */
    int failed;                 /* a reference buffer allocation failed */
    int escape_prose;           /* escape what a reader would parse in prose; off under Escaping(mode="none") */
    int amp_open;               /* the last text node ended in an `&` and name, which the next can finish */
    int deep_block_span;        /* nested inline tags crossed a block boundary */
    int raw_emphasis;           /* HTML tags keep nested delimiters and breaks from changing on reparse */
    th_node *br_from;           /* the node md_br_trailing last answered for... */
    th_node *br_link;           /* ...the link it was in... */
    int br_trailing;            /* ...and the answer, which a scan from there has for every node it steps over */
    uint8_t escape_mask;        /* the MD_ASCII classes the options escape */
    uint8_t run_stop;           /* the MD_ASCII classes that end a bulk-copied run */
    Py_ssize_t break_start;     /* where the last hard break was written in out... */
    Py_ssize_t break_end;       /* ...and where it ended, -1 when there is none */
} md_ctx;

static int md_is_emphasis_node(const th_node *node) {
    if (node == NULL || node->type != TH_NODE_ELEMENT || node->ns != TH_NS_HTML) {
        return 0;
    }
    switch (node->atom) {
    case TH_TAG_STRONG:
    case TH_TAG_B:
    case TH_TAG_EM:
    case TH_TAG_I:
    case TH_TAG_DEL:
    case TH_TAG_S:
    case TH_TAG_STRIKE:
        return 1;
    default:
        return 0;
    }
}

static int md_needs_raw_emphasis(th_tree *tree, th_node *root) {
    th_node *previous = NULL;
    Py_ssize_t open = 0;
    for (th_node *current = root; current != NULL; current = preorder_next(current, root)) {
        if (previous != NULL) {
            for (th_node *ancestor = previous; ancestor != current->parent; ancestor = ancestor->parent) {
                open -= md_is_emphasis_node(ancestor);
            }
        }
        if (md_is_emphasis_node(current)) {
            open++;
            if (current->last_child != NULL && current->last_child->atom == TH_TAG_BR &&
                ((current->first_child != current->last_child && current->first_child->atom == TH_TAG_BR) ||
                 (current->prev_sibling != NULL && current->prev_sibling->atom == TH_TAG_BR))) {
                return 1;
            }
        } else if (current->type == TH_NODE_TEXT && (open > 1 || md_is_emphasis_node(current->parent))) {
            int source_span = text_is_span(current);
            Py_ssize_t offset = source_span ? text_span_offset(current) : 0;
            for (Py_ssize_t index = 0; index < current->text_len; index++) {
                Py_UCS4 ch =
                    source_span ? PyUnicode_READ(tree->kind, tree->data, offset + index) : current->text[index];
                if (open > 1 && (ch >= 0x80 || ch == '&' || ch == '\\' || ch == '|')) {
                    return 1;
                }
                if (ch >= 0x80 &&
                    ((current->parent->prev_sibling != NULL && current->parent->prev_sibling->type == TH_NODE_TEXT &&
                      current->parent->prev_sibling->text_len > 0) ||
                     (current->parent->next_sibling != NULL && current->parent->next_sibling->type == TH_NODE_TEXT &&
                      current->parent->next_sibling->text_len > 0))) {
                    return 1;
                }
                if (ch == '\\') {
                    if (md_is_emphasis_node(current->parent->prev_sibling) ||
                        md_is_emphasis_node(current->parent->next_sibling)) {
                        return 1;
                    }
                }
            }
        }
        previous = current;
    }
    return 0;
}

static int md_text_visible(th_tree *tree, th_node *node) {
    const Py_UCS4 *text = need_text(tree, node);
    if (text == NULL && node->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        return 0;                             /* GCOVR_EXCL_LINE: the walk reports the failure */
    }
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        if (!Py_UNICODE_ISSPACE(text[index])) {
            return 1;
        }
    }
    return 0;
}

static int md_subtree_visible(th_tree *tree, th_node *root) {
    th_node *current = root;
    while (current != NULL) {
        TH_WORK_STEP();
        if (current->type == TH_NODE_TEXT && md_text_visible(tree, current)) {
            return 1;
        }
        if (current->type == TH_NODE_ELEMENT && is_md_skipped(current)) {
            while (current != root && current->next_sibling == NULL) {
                current = current->parent;
            }
            current = current == root ? NULL : current->next_sibling;
            continue;
        }
        current = preorder_next(current, root);
    }
    return 0;
}

static int md_is_html_br(const th_node *node) {
    return node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && node->atom == TH_TAG_BR;
}

/* Whether the siblings strictly between from and to write a visible character, checking their own text before any
   subtree. A converter can render any element as nothing, so with converters only text counts. */
static int md_gap_visible(md_ctx *ctx, th_node *from, th_node *to) {
    for (th_node *node = from->next_sibling; node != to; node = node->next_sibling) {
        if (node->type == TH_NODE_TEXT && md_text_visible(ctx->tree, node)) {
            return 1;
        }
    }
    if (ctx->opt->converters != NULL) {
        return 0;
    }
    for (th_node *node = from->next_sibling; node != to; node = node->next_sibling) {
        if (node->type == TH_NODE_ELEMENT && md_subtree_visible(ctx->tree, node)) {
            return 1;
        }
    }
    return 0;
}

/* Two hard breaks with nothing visible between them would end the paragraph inside the emphasis, so a break next to
   such a gap is written as a tag. */
static int md_repeated_br_in_emphasis(md_ctx *ctx, th_node *node) {
    if (!md_is_emphasis_node(node->parent)) {
        return 0;
    }
    th_node *before = node->prev_sibling;
    while (before != NULL && !md_is_html_br(before)) {
        before = before->prev_sibling;
    }
    if (before != NULL && !md_gap_visible(ctx, before, node)) {
        return 1;
    }
    th_node *after = node->next_sibling;
    while (after != NULL && !md_is_html_br(after)) {
        after = after->next_sibling;
    }
    return after != NULL && !md_gap_visible(ctx, node, after);
}

/* Emit a configured option string, which may hold non-ASCII (a typographic
   quote, a unicode bullet), decoding its UTF-8 to code points. */
static void md_puts8(sbuf *out, const char *text) {
    sbuf_put_utf8(out, text, (Py_ssize_t)strlen(text));
}

/* The first code point of a configured option string, which reads as 0 when it is empty. */
static Py_UCS4 md_first_cp(const char *text) {
    Py_ssize_t index = 0;
    return utf8_next(text, (Py_ssize_t)strlen(text), &index);
}

/* How many code points a configured option string holds: the bytes that do not continue a sequence. */
static Py_ssize_t md_cp_len(const char *text) {
    Py_ssize_t count = 0;
    for (const char *cursor = text; *cursor != '\0'; cursor++) {
        count += (*cursor & 0xC0) != 0x80;
    }
    return count;
}

/* The unordered-list marker for a list depth levels deep: the bullets' code points, cycled. */
static Py_UCS4 md_bullet_at(const char *bullets, Py_ssize_t depth) {
    if (bullets[1] == '\0') {
        return (unsigned char)bullets[0];
    }
    Py_ssize_t len = (Py_ssize_t)strlen(bullets);
    Py_ssize_t index = 0;
    for (Py_ssize_t skip = depth % md_cp_len(bullets); skip > 0; skip--) {
        utf8_next(bullets, len, &index);
    }
    return utf8_next(bullets, len, &index);
}

/* Indent what follows by count more columns, one list level deeper, and return the
   prefix length to pop back to after the nested block. Past TH_MAX_INDENT_LEVELS the
   prefix stops growing and deeper content lays out at that depth. */
static Py_ssize_t md_indent(md_ctx *ctx, Py_ssize_t count) {
    Py_ssize_t base = ctx->prefix.len;
    ctx->indent_levels += 2;
    if (ctx->indent_levels <= TH_MAX_INDENT_LEVELS) {
        for (Py_ssize_t index = 0; index < count; index++) {
            sbuf_putc(&ctx->prefix, ' ');
        }
    }
    return base;
}

/* Write the continuation prefix at the head of a fresh line. */
static void md_write_prefix(md_ctx *ctx) {
    sbuf_put_run(&ctx->out, ctx->prefix.data, ctx->prefix.len);
}

/* Write the prefix for a blank separator line with its trailing spaces trimmed,
   so a plain blank line stays empty and a blockquote shows ">" not "> ". */
static void md_write_blank_prefix(md_ctx *ctx) {
    Py_ssize_t len = ctx->prefix.len;
    while (len > 0 && ctx->prefix.data[len - 1] == ' ') {
        len--;
    }
    sbuf_put_run(&ctx->out, ctx->prefix.data, len);
}

/* What a line opens for a reader: nothing, the HTML block of a tag alone on its line, or
   that of a table or list passed through raw. */
enum { MD_HTML_LINE_NONE, MD_HTML_LINE_TAG, MD_HTML_LINE_BLOCK };

/* Whether the line written so far opens an HTML block that only a blank line ends
   (CommonMark 4.6): the one-line HTML of a table or list passed through raw (start
   condition 6), or a single tag with nothing else on its line, as a `<br>` or a raw
   `<img>` leaves (condition 7). A reader copies the lines after it out verbatim, their
   backslash escapes and code fences dead, so the caller keeps what it writes next off
   them. The line is read from the output rather than from the prefix, past whatever a
   container leads a line with (indentation, quote markers, a list item's marker), since
   the prefix may have grown or shrunk since the line was written and a reader need not
   agree that its container closed. Escaped prose never puts a bare `<` there before a
   letter; an autolink does, and its scheme or address runs on where a tag name ends at
   a space or `>`. A tag's attribute values are double-quoted and may hold a `>`. */
static int md_line_opens_html(md_ctx *ctx) {
    static const char lead[] = " >-*+.)0123456789";
    const Py_UCS4 *data = ctx->out.data;
    Py_ssize_t end = ctx->out.len;
    if (end == 0 || data[end - 1] != '>') {
        return MD_HTML_LINE_NONE; /* a raw block and a lone tag both end on their `>`, so the scan back is skipped */
    }
    Py_ssize_t start = end;
    while (start > 0 && data[start - 1] != '\n') {
        start--;
    }
    while (start < end && data[start] < 0x80 && memchr(lead, (int)data[start], sizeof(lead) - 1) != NULL) {
        start++;
    }
    if (start + 1 >= end || data[start] != '<') {
        return MD_HTML_LINE_NONE;
    }
    Py_ssize_t name = start + 1 + (data[start + 1] == '/');
    Py_ssize_t scan = name;
    while (is_ascii_alpha(data[scan])) { /* the line ends on a `>`, which is no letter, so the name stops inside it */
        scan++;
    }
    if (scan == name || (data[scan] != ' ' && data[scan] != '>')) {
        return MD_HTML_LINE_NONE;
    }
    if (ser_value_iequals(&data[name], scan - name, "table") || ser_value_iequals(&data[name], scan - name, "ol")) {
        return MD_HTML_LINE_BLOCK;
    }
    int quoted = 0;
    for (; scan < end - 1; scan++) {
        if (data[scan] == '"') {
            quoted = !quoted;
        } else if (data[scan] == '>' && !quoted) {
            return MD_HTML_LINE_NONE; /* the tag ends before the line does, so it is not alone on it */
        }
    }
    return MD_HTML_LINE_TAG;
}

/* Whether the output ends with a pipe table that the next line, written at the table's depth or deeper, would
   join: a GFM reader takes the line under a table as one more row (GFM 4.10) and cuts it at every pipe before it
   reads inline spans, so a code span holding one is split and its text read as markup. A line of a container the
   table sat in is not a row, so an item marker after the table's own item stays tight. */
static int md_after_table(md_ctx *ctx) {
    return ctx->out.len == ctx->table_end && ctx->prefix.len >= ctx->table_prefix;
}

/* Position the cursor at the start of a fresh, prefixed line for the next block,
   collapsing the margin with the previous block (a blank line when either side
   is loose). loose marks whether this block wants blank lines around it. */
static void md_suspend_markers(md_ctx *ctx);

static void md_block_line(md_ctx *ctx, int loose) {
    md_suspend_markers(ctx);
    ctx->block_ended = 0;
    if (!ctx->started) {
        ctx->started = 1;
        md_write_prefix(ctx);
    } else if (ctx->suppress_break) {
        ctx->suppress_break = 0;
    } else {
        /* an empty block quote left its "> " prefix on the line with nothing after
           it; trim the trailing spaces so the quoted blank line reads as ">" */
        if (!ctx->line_has_content) {
            Py_ssize_t end = ctx->out.len;
            while (end > 0 && ctx->out.data[end - 1] == ' ') {
                end--;
            }
            if (end > 0 && ctx->out.data[end - 1] == '>') {
                ctx->out.len = end;
                ctx->break_end = -1; /* a hard break that ended on the trimmed line no longer ends the output */
            }
        }
        /* a list item starts with at most one blank line (CommonMark 5.2), and a marker
           line with nothing after it already is one; an HTML block ends at a blank line
           alone (4.6), so one follows it in a tight item and under single spacing too */
        if (((ctx->pending_loose || loose) && !ctx->tight && !ctx->opt->block_spacing_single && !ctx->item_marker) ||
            ctx->interrupt_blank || md_after_table(ctx) || md_line_opens_html(ctx)) {
            sbuf_putc(&ctx->out, '\n');
            md_write_blank_prefix(ctx);
        }
        sbuf_putc(&ctx->out, '\n');
        md_write_prefix(ctx);
        ctx->line_has_content = 0;
    }
    ctx->interrupt_blank = 0;
    ctx->item_marker = 0;
    ctx->pending_loose = loose;
    ctx->space_pending = 0;
    ctx->drop_space = 1;
}

/* Start a new continuation line inside the current block (a <br> or a code-block
   line break): one newline plus the prefix, content reset but the block open. */
static void md_newline(md_ctx *ctx) {
    sbuf_putc(&ctx->out, '\n');
    md_write_prefix(ctx);
    ctx->line_has_content = 0;
    ctx->space_pending = 0;
    ctx->drop_space = 1;
}

/* Write a hard break, remembering where it sits so an emphasis run that closes right
   after it can put its closing delimiter in front of it (md_put_close). A line of only
   spaces is blank (CommonMark 4.9) and ends the paragraph, so a break with nothing
   before it on its line takes the backslash spelling whatever the style. So does a
   break after a tag alone on its line: with only spaces behind it the tag opens an HTML
   block (4.6) that takes the rest of the paragraph as raw HTML. */
static int md_opener_ends_output(md_ctx *ctx) {
    Py_ssize_t index = ctx->marker_count;
    while (index > ctx->marker_base && !ctx->markers[index - 1].emitted) {
        index--;
    }
    if (index == ctx->marker_base) {
        return 0;
    }
    const md_marker *marker = &ctx->markers[index - 1];
    return marker->open_at + marker->open_len == ctx->out.len;
}

static void md_write_break(md_ctx *ctx) {
    if (ctx->block_ended) {
        md_block_line(ctx, 1); /* the break opens the block after an inner one, so it leads that block's line */
    }
    ctx->break_start = ctx->out.len;
    /* a run opened with nothing after it would take the spaces as the space after its delimiter, which settles it
       as a raw tag, and the break would leave that tag alone on its line */
    int backslash = ctx->opt->line_break == TH_MD_BREAK_BACKSLASH || !ctx->line_has_content ||
                    md_line_opens_html(ctx) || md_opener_ends_output(ctx);
    sbuf_puts(&ctx->out, backslash ? "\\" : "  ");
    md_newline(ctx);
    ctx->break_end = ctx->out.len;
}

/* Write an emphasis run's closing delimiter. When the run's content ended with a hard
   break that nothing has followed yet, the delimiter would open the next line, where it
   is not right-flanking (CommonMark 6.2) and cannot close, so it goes in front of the
   break and the break is written again after it. The record of the break belongs to the
   output as it stands: a converter or table cell sets it aside with the output and starts
   with none, and whatever cuts the output back (here, or md_block_line trimming the line
   the break left) drops it, so only a break that still ends this output matches. */
static void md_put_close(md_ctx *ctx, const char *close) {
    if (ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return;            /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (ctx->out.len == ctx->break_end) {
        ctx->out.len = ctx->break_start;
        ctx->break_end = -1; /* md_write_break can close enclosing runs before it records the break again */
        md_puts8(&ctx->out, close);
        ctx->line_has_content = 1;
        md_write_break(ctx);
        return;
    }
    md_puts8(&ctx->out, close);
}

static Py_ssize_t md_line_column(md_ctx *ctx) {
    for (Py_ssize_t cursor = ctx->out.len; cursor > ctx->line_checked; cursor--) {
        if (ctx->out.data[cursor - 1] == '\n') {
            ctx->line_start = cursor;
            break;
        }
    }
    ctx->line_checked = ctx->out.len;
    return ctx->out.len - ctx->line_start;
}

/* Emit the one space a collapsed whitespace run owes, unless it falls at a line
   start or has been marked for dropping (the start of a block). When word
   wrapping is on, a break replaces the space once the following word would carry
   the line past wrap_width (greedy: never split a word, honor the prefix). */
static void md_flush_space(md_ctx *ctx) {
    int drop = ctx->drop_space;
    ctx->drop_space = 0;
    int word = ctx->pending_word;
    ctx->pending_word = 0;
    if (!ctx->space_pending) {
        return;
    }
    ctx->space_pending = 0;
    /* every line start sets drop_space, so a line-start space is already covered
       by drop and the wrap check below only ever sees a mid-line space */
    if (drop) {
        return;
    }
    /* a tag left alone on its line by a wrap would open an HTML block there, so its line runs on */
    if (ctx->opt->wrap_width > 0 && ctx->no_wrap == 0 && md_line_column(ctx) + 1 + word > ctx->opt->wrap_width &&
        md_line_opens_html(ctx) != MD_HTML_LINE_TAG) {
        md_newline(ctx);
        /* the break consumed the owed space and the wrapped word follows it with
           no leading space, so clear the drop md_newline raised for a real <br> */
        ctx->drop_space = 0;
        return;
    }
    sbuf_putc(&ctx->out, ' ');
}

static void md_settle_pending(md_ctx *ctx) {
    ctx->pending = ctx->marker_count > ctx->marker_base ? &ctx->markers[ctx->marker_count - 1] : NULL;
}

/* What a character is to the CommonMark flanking rules: whitespace, ASCII punctuation,
   or (0) anything else. */
enum { MD_EDGE_SPACE = 1, MD_EDGE_PUNCT = 2 };
/* U+3000 is the highest Zs space; direct indexing keeps the ASCII lookup unchanged. */
static const uint8_t MD_EDGE[0x3001] = {
    ['\t'] = MD_EDGE_SPACE,   ['\n'] = MD_EDGE_SPACE,   ['\f'] = MD_EDGE_SPACE,   ['\r'] = MD_EDGE_SPACE,
    [' '] = MD_EDGE_SPACE,    ['!'] = MD_EDGE_PUNCT,    ['"'] = MD_EDGE_PUNCT,    ['#'] = MD_EDGE_PUNCT,
    ['$'] = MD_EDGE_PUNCT,    ['%'] = MD_EDGE_PUNCT,    ['&'] = MD_EDGE_PUNCT,    ['\''] = MD_EDGE_PUNCT,
    ['('] = MD_EDGE_PUNCT,    [')'] = MD_EDGE_PUNCT,    ['*'] = MD_EDGE_PUNCT,    ['+'] = MD_EDGE_PUNCT,
    [','] = MD_EDGE_PUNCT,    ['-'] = MD_EDGE_PUNCT,    ['.'] = MD_EDGE_PUNCT,    ['/'] = MD_EDGE_PUNCT,
    [':'] = MD_EDGE_PUNCT,    [';'] = MD_EDGE_PUNCT,    ['<'] = MD_EDGE_PUNCT,    ['='] = MD_EDGE_PUNCT,
    ['>'] = MD_EDGE_PUNCT,    ['?'] = MD_EDGE_PUNCT,    ['@'] = MD_EDGE_PUNCT,    ['['] = MD_EDGE_PUNCT,
    ['\\'] = MD_EDGE_PUNCT,   [']'] = MD_EDGE_PUNCT,    ['^'] = MD_EDGE_PUNCT,    ['_'] = MD_EDGE_PUNCT,
    ['`'] = MD_EDGE_PUNCT,    ['{'] = MD_EDGE_PUNCT,    ['|'] = MD_EDGE_PUNCT,    ['}'] = MD_EDGE_PUNCT,
    ['~'] = MD_EDGE_PUNCT,    [0xA0] = MD_EDGE_SPACE,   [0x1680] = MD_EDGE_SPACE, [0x2000] = MD_EDGE_SPACE,
    [0x2001] = MD_EDGE_SPACE, [0x2002] = MD_EDGE_SPACE, [0x2003] = MD_EDGE_SPACE, [0x2004] = MD_EDGE_SPACE,
    [0x2005] = MD_EDGE_SPACE, [0x2006] = MD_EDGE_SPACE, [0x2007] = MD_EDGE_SPACE, [0x2008] = MD_EDGE_SPACE,
    [0x2009] = MD_EDGE_SPACE, [0x200A] = MD_EDGE_SPACE, [0x202F] = MD_EDGE_SPACE, [0x205F] = MD_EDGE_SPACE,
    [0x3000] = MD_EDGE_SPACE,
};

static inline uint8_t md_edge(Py_UCS4 ch) {
    return ch < sizeof(MD_EDGE) ? MD_EDGE[ch] : 0;
}

static int md_is_ascii_punct(Py_UCS4 ch) {
    return md_edge(ch) == MD_EDGE_PUNCT;
}

static const char *md_alternate_run(md_ctx *ctx, const md_marker *marker);

/* Whether marker[index] must fall back to raw inline HTML because its Markdown
   delimiter would not survive a round-trip. Three ways it fails: it touches an
   identical delimiter, the run of an enclosing marker opened in the same batch included,
   so the two runs merge into one, unless the other emphasis character can take its
   place; a ~~ run nests directly in another, which opens a code fence (and commonmark.js
   has no strikethrough); or its flanking context (CommonMark 6.2) leaves neither side
   able to pair. The flanking test runs only for a single-text-node element, where the
   surrounding characters are known without a subtree scan, so the walk stays linear;
   md_settle_run checks the rest once the content is written. */
static int md_marker_html(md_ctx *ctx, Py_ssize_t index, Py_ssize_t first) {
    md_marker *marker = &ctx->markers[index];
    if (marker->node == NULL) {
        return 0;
    }
    Py_ssize_t len = ctx->out.len;
    /* an enclosing `*` opened together with this `**` reads as `***`, which nests the
       same way (CommonMark 6.2 rule 14), so only that pairing keeps its delimiters */
    if (len > 0 && ctx->out.data[len - 1] == md_first_cp(marker->open) &&
        !(len >= 2 && ctx->out.data[len - 2] == '\\') &&
        !(index > first && md_cp_len(ctx->markers[index - 1].open) < md_cp_len(marker->open))) {
        const char *alternate = md_alternate_run(ctx, marker);
        if (alternate == NULL) {
            return 1;
        }
        marker->open = alternate;
        marker->close = alternate;
        return 0;
    }
    if ((marker->flags & MD_MARK_STRIKE) &&
        ((index > first && (ctx->markers[index - 1].flags & MD_MARK_STRIKE)) ||
         (index + 1 < ctx->marker_count && (ctx->markers[index + 1].flags & MD_MARK_STRIKE)))) {
        return 1; /* a ~~ run directly inside another: the pair would read as `~~~~` */
    }
    return (marker->flags & MD_MARK_CLOSE_FAILS) ||
           ((marker->flags & MD_MARK_OPEN_PUNCT) && md_edge(len > 0 ? ctx->out.data[len - 1] : ' ') == 0);
}

/* The characters a run's raw-HTML open and close tags take together. */
static Py_ssize_t md_tags_len(uint8_t html) {
    return (Py_ssize_t)(strlen(MD_HTML_TAGS[html - 1][0]) + strlen(MD_HTML_TAGS[html - 1][1]));
}

/* Spend md_ctx.tags_left on one more pair of a run's raw-HTML tags; 0 when it does not cover them. */
static int md_fund_tags(md_ctx *ctx, uint8_t html) {
    Py_ssize_t cost = md_tags_len(html);
    if (cost > ctx->tags_left) {
        return 0;
    }
    ctx->tags_left -= cost;
    return 1;
}

/* Whether md_emit_pending left an emphasis run closed for the block: it wrote nothing, so it owes no close either. */
static inline int md_folded(const md_marker *marker) {
    return marker->html != MD_HTML_NONE && marker->open[0] == '\0';
}

/* Emit the deferred opening markers from outermost to innermost (skipping any
   already written), so nested emphasis opens in source order. */
static TH_NOINLINE void md_emit_pending(md_ctx *ctx) {
    Py_ssize_t first = ctx->marker_count;
    while (first > ctx->marker_base && !ctx->markers[first - 1].emitted) {
        first--;
    }
    unsigned opened = 0; /* the kinds of run, as bits of enum md_html, opened here around the next one */
    for (Py_ssize_t index = first; index < ctx->marker_count; index++) {
        md_marker *marker = &ctx->markers[index];
        /* An inner close next to its parent's star run otherwise merges into one run. */
        if (marker->node != NULL && strcmp(marker->open, "*") == 0 && marker->node->next_sibling == NULL &&
            marker->node->parent->ns == TH_NS_HTML &&
            (marker->node->parent->atom == TH_TAG_EM || marker->node->parent->atom == TH_TAG_I) && ctx->out.len > 0 &&
            md_edge(ctx->out.data[ctx->out.len - 1]) == MD_EDGE_PUNCT) {
            marker->open = "_";
            marker->close = "_";
        }
        if ((ctx->deep_block_span || marker->spanning) && marker->html != 0) {
            marker->open = MD_HTML_TAGS[marker->html - 1][0];
            marker->close = MD_HTML_TAGS[marker->html - 1][1];
            marker->spanning = 1;
            /* Every block and table cell reopens all the runs around it, which multiplies the output by the nesting
               depth unless the tags are paid for. The outermost run of each kind always reopens, so the block keeps
               its emphasis; a run inside one of its own kind changes nothing a reader sees, so it needs the budget
               to cover its tags and stays closed for this block when it does not. */
            if (ctx->deep_block_span && (opened & (1u << marker->html)) && !md_fund_tags(ctx, marker->html)) {
                marker->open = "";
                marker->close = "";
            }
            opened |= 1u << marker->html;
        } else if (md_marker_html(ctx, index, first)) {
            marker->open = MD_HTML_TAGS[marker->html - 1][0];
            marker->close = MD_HTML_TAGS[marker->html - 1][1];
        }
        if (marker->open[0] == '[' && marker->open[1] == '\0' && ctx->out.len > 0 &&
            ctx->out.data[ctx->out.len - 1] == '!') {
            Py_ssize_t slash = ctx->out.len - 1;
            while (slash > 0 && ctx->out.data[slash - 1] == '\\') {
                slash--;
            }
            if (((ctx->out.len - 1 - slash) & 1) == 0) {
                /* CommonMark reads an unescaped ! before a link as an image. */
                ctx->out.data[ctx->out.len - 1] = '\\';
                sbuf_putc(&ctx->out, '!');
            }
        }
        marker->open_at = ctx->out.len;
        if (marker->open[0] != '\0' && marker->open[1] == '\0') {
            sbuf_putc(&ctx->out, (unsigned char)marker->open[0]);
        } else {
            md_puts8(&ctx->out, marker->open);
        }
        marker->open_len = ctx->out.len - marker->open_at;
        marker->emitted = 1;
        ctx->open_markers++;
        /* an empty delimiter writes nothing, so the next character still starts the line */
        if (marker->open_len > 0) {
            ctx->line_has_content = 1;
        }
    }
}

/* Called just before any visible character: settle the owed space first (so it
   stays outside an emphasis run), then open any markers that were waiting for
   real content. */
static void md_before_visible(md_ctx *ctx) {
    if (ctx->block_ended) {
        md_block_line(ctx, 1);
    }
    md_flush_space(ctx);
    if (ctx->pending != NULL && !ctx->pending->emitted) {
        md_emit_pending(ctx);
    }
}

/* What each ASCII character means to running text, as bits, so one lookup answers
   both whether it escapes under the options (md_ctx.escape_mask) and whether it ends
   a bulk-copied run (md_ctx.run_stop). Leading line markers (#, >, -, +, =) are
   handled separately, only at the very start of a line. */
enum {
    MD_CH_ESCAPE = 1,     /* always escaped; GFM strikes through text between tildes */
    MD_CH_ASTERISK = 2,   /* escaped unless Escaping(asterisks=False) */
    MD_CH_UNDERSCORE = 4, /* escaped unless Escaping(underscores=False) */
    MD_CH_ALL = 8,        /* escaped only by the "all" mode */
    MD_CH_CONTEXT = 16,   /* escaped by what follows it (md_escape_by_context) */
    MD_CH_SPACE = 32,     /* part of a whitespace run */
};

static const uint8_t MD_ASCII[128] = {
    ['\t'] = MD_CH_SPACE,
    ['\n'] = MD_CH_SPACE,
    ['\f'] = MD_CH_SPACE,
    ['\r'] = MD_CH_SPACE,
    [' '] = MD_CH_SPACE,
    ['\\'] = MD_CH_ESCAPE,
    ['`'] = MD_CH_ESCAPE,
    ['['] = MD_CH_ESCAPE,
    [']'] = MD_CH_ESCAPE,
    ['~'] = MD_CH_ESCAPE,
    ['*'] = MD_CH_ASTERISK,
    ['_'] = MD_CH_UNDERSCORE,
    ['<'] = MD_CH_ALL | MD_CH_CONTEXT,
    ['&'] = MD_CH_ALL | MD_CH_CONTEXT,
    ['>'] = MD_CH_ALL,
    ['#'] = MD_CH_ALL,
    ['+'] = MD_CH_ALL,
    ['-'] = MD_CH_ALL,
    ['='] = MD_CH_ALL,
    ['|'] = MD_CH_ALL,
    ['!'] = MD_CH_ALL,
};

/* Whether the `&` at text[index] opens something CommonMark decodes as a character
   reference: `&name;`, `&#digits;` or `&#xhex;`. The name is not looked up, so a
   shape that names no entity is escaped too, which renders the same. */
static int md_starts_reference(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    Py_ssize_t scan = index + 1;
    if (scan < len && text[scan] == '#') {
        scan++;
        if (scan < len && (text[scan] == 'x' || text[scan] == 'X')) {
            scan++;
        }
    }
    Py_ssize_t start = scan;
    while (scan < len && (is_ascii_alpha(text[scan]) || is_ascii_digit(text[scan]))) {
        scan++;
    }
    return scan > start && scan < len && text[scan] == ';';
}

/* Whether a `<` or `&` in running text needs a backslash, which depends on what
   follows it: a `&` can open a character reference, and a `<` before anything but
   whitespace can open raw HTML, a comment or an autolink. The run's end counts as
   a possible tag start, since the next node's text continues the line. A U+2190
   arrow transliterates to "<-", and what follows it decides that `<` the same way,
   since `-` can start an email autolink's local part. */
static int md_escape_by_context(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    if (text[index] == '&') {
        return md_starts_reference(text, index, len);
    }
    return index + 1 == len || !is_space(text[index + 1]);
}

/* The transliterate map: common non-ASCII typography folded to an ASCII spelling.
   Punctuation/symbols plus the Latin-1 and a few Latin-Extended-A accented
   letters, kept as data so the scan is one branch and the ASCII path is free. */
typedef struct {
    Py_UCS4 cp;
    const char *ascii;
} md_translit_entry;

static const md_translit_entry MD_TRANSLIT[] = {
    {0x00A0, " "},  {0x00A9, "(C)"}, {0x00AB, "\""}, {0x00AE, "(R)"}, {0x00B7, "*"},  {0x00BB, "\""}, {0x00C0, "A"},
    {0x00C1, "A"},  {0x00C2, "A"},   {0x00C3, "A"},  {0x00C4, "A"},   {0x00C5, "A"},  {0x00C6, "AE"}, {0x00C7, "C"},
    {0x00C8, "E"},  {0x00C9, "E"},   {0x00CA, "E"},  {0x00CB, "E"},   {0x00CC, "I"},  {0x00CD, "I"},  {0x00CE, "I"},
    {0x00CF, "I"},  {0x00D0, "D"},   {0x00D1, "N"},  {0x00D2, "O"},   {0x00D3, "O"},  {0x00D4, "O"},  {0x00D5, "O"},
    {0x00D6, "O"},  {0x00D7, "x"},   {0x00D8, "O"},  {0x00D9, "U"},   {0x00DA, "U"},  {0x00DB, "U"},  {0x00DC, "U"},
    {0x00DD, "Y"},  {0x00DE, "Th"},  {0x00DF, "ss"}, {0x00E0, "a"},   {0x00E1, "a"},  {0x00E2, "a"},  {0x00E3, "a"},
    {0x00E4, "a"},  {0x00E5, "a"},   {0x00E6, "ae"}, {0x00E7, "c"},   {0x00E8, "e"},  {0x00E9, "e"},  {0x00EA, "e"},
    {0x00EB, "e"},  {0x00EC, "i"},   {0x00ED, "i"},  {0x00EE, "i"},   {0x00EF, "i"},  {0x00F0, "d"},  {0x00F1, "n"},
    {0x00F2, "o"},  {0x00F3, "o"},   {0x00F4, "o"},  {0x00F5, "o"},   {0x00F6, "o"},  {0x00F8, "o"},  {0x00F9, "u"},
    {0x00FA, "u"},  {0x00FB, "u"},   {0x00FC, "u"},  {0x00FD, "y"},   {0x00FE, "th"}, {0x00FF, "y"},  {0x0152, "OE"},
    {0x0153, "oe"}, {0x0160, "S"},   {0x0161, "s"},  {0x0178, "Y"},   {0x017D, "Z"},  {0x017E, "z"},  {0x2010, "-"},
    {0x2011, "-"},  {0x2013, "-"},   {0x2014, "--"}, {0x2018, "'"},   {0x2019, "'"},  {0x201A, "'"},  {0x201C, "\""},
    {0x201D, "\""}, {0x201E, "\""},  {0x2022, "*"},  {0x2026, "..."}, {0x2190, "<-"}, {0x2192, "->"}, {0x2122, "(TM)"},
};

/* The ASCII spelling for a code point, or NULL to emit it unchanged. ASCII never
   maps, so the common path costs one comparison. */
static const char *md_translit(Py_UCS4 ch) {
    if (ch < 0x80) {
        return NULL;
    }
    for (size_t index = 0; index < sizeof(MD_TRANSLIT) / sizeof(MD_TRANSLIT[0]); index++) {
        if (MD_TRANSLIT[index].cp == ch) {
            return MD_TRANSLIT[index].ascii;
        }
    }
    return NULL;
}

/* Write one code point of running text. escape says the caller found it escapes
   wherever it sits: an escaped character under the options, or a `<` or `&` that
   what follows turns into markup. A transliterated "<-" passes that on to its `<`.
   At a line start `=` and `-` also escape: after a paragraph line they would
   underline it into a setext heading. */
static void md_put_char(md_ctx *ctx, Py_UCS4 ch, int escape) {
    if (ctx->opt->transliterate) {
        const char *ascii = md_translit(ch);
        if (ascii != NULL) {
            for (const char *cursor = ascii; *cursor != '\0'; cursor++) {
                unsigned char folded = (unsigned char)*cursor;
                md_put_char(ctx, folded, (MD_ASCII[folded] & ctx->escape_mask) || (folded == '<' && escape));
            }
            return;
        }
    }
    int line_start_marker =
        !ctx->line_has_content && (ch == '#' || ch == '>' || ch == '-' || ch == '+' || ch == '=') && ctx->escape_prose;
    if (escape || line_start_marker || (ctx->in_cell && ch == '|')) {
        sbuf_putc(&ctx->out, '\\');
    }
    sbuf_putc(&ctx->out, ch);
    ctx->line_has_content = 1;
}

/* Write one content code point, escaping a pipe that would otherwise end the table
   cell it sits in. GFM puts that escape on the cell's *content* ("include a pipe in a
   cell's content by escaping it, including inside other inline spans"), so every writer
   of content goes through here or md_put_run and escapes it exactly once, where it is
   written. Escaping the rendered cell instead cannot tell a literal pipe from one this
   writer already escaped: `\|` becomes `\\|`, a backslash followed by a live break. */
static void md_put_literal(md_ctx *ctx, Py_UCS4 ch) {
    if (ctx->in_cell && ch == '|') {
        sbuf_putc(&ctx->out, '\\');
    }
    sbuf_putc(&ctx->out, ch);
}

/* The same rule over a run, which is how prose and embedded markup are written. */
static void md_put_run(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t len) {
    if (!ctx->in_cell) {
        sbuf_put_run(&ctx->out, text, len);
        return;
    }
    Py_ssize_t start = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (text[index] == '|') {
            sbuf_put_run(&ctx->out, &text[start], index - start);
            sbuf_puts(&ctx->out, "\\|");
            start = index + 1;
        }
    }
    sbuf_put_run(&ctx->out, &text[start], len - start);
}

/* At a line start, "12. " or "3) " would be read as an ordered-list item, so a
   leading run of digits before a dot or paren is escaped. Returns how many code
   points were consumed (0 when the run is not list-like). */
static Py_ssize_t md_escape_line_number(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    /* the caller only enters here on a digit, so the run is at least one long */
    Py_ssize_t scan = index;
    while (scan < len && text[scan] >= '0' && text[scan] <= '9') {
        scan++;
    }
    if (scan >= len || (text[scan] != '.' && text[scan] != ')')) {
        return 0;
    }
    sbuf_put_run(&ctx->out, &text[index], scan - index);
    sbuf_putc(&ctx->out, '\\');
    sbuf_putc(&ctx->out, text[scan]);
    ctx->line_has_content = 1;
    return scan + 1 - index;
}

/* md_escape_by_context for running text, which also notes an `&` whose name runs to
   the node's end, so the next node can tell whether it finishes a reference. */
static int md_escape_in_text(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    if (md_escape_by_context(text, index, len)) {
        return 1;
    }
    if (text[index] == '&') {
        Py_ssize_t scan = index + 1;
        while (scan < len && (is_ascii_alpha(text[scan]) || is_ascii_digit(text[scan]) || text[scan] == '#')) {
            scan++;
        }
        ctx->amp_open = scan == len;
    }
    return 0;
}

/* md_starts_reference sees one text node at a time, so a node that continues the
   output right after an unescaped `&` and the start of a name or number (`&` then
   `amp;`) can finish a reference across the two. The shape is checked over both,
   capped where no reference CommonMark decodes reaches (2.5: a WHATWG entity name is
   at most 31 letters, a number at most 7 digits), and the `&` escaped in place. The
   node's first character follows with no space, so no wrap check has measured the line
   past the `&`. */
static void md_escape_split_reference(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t len) {
    const Py_UCS4 *data = ctx->out.data;
    Py_ssize_t amp = ctx->out.len;
    while (amp > 0 && ctx->out.len - amp < 34 &&
           (is_ascii_alpha(data[amp - 1]) || is_ascii_digit(data[amp - 1]) || data[amp - 1] == '#')) {
        amp--;
    }
    Py_ssize_t slashes = 0;
    while (amp - 1 - slashes > 0 && data[amp - 2 - slashes] == '\\') {
        slashes++;
    }
    if (amp == 0 || data[amp - 1] != '&' || slashes % 2 == 1) {
        return;
    }
    Py_UCS4 shape[40];
    Py_ssize_t tail = ctx->out.len - amp + 1; /* the `&` and the name or number after it */
    memcpy(shape, &data[amp - 1], (size_t)tail * sizeof(Py_UCS4));
    Py_ssize_t count = tail;
    for (Py_ssize_t index = 0; index < len && count < 40; index++) {
        shape[count++] = text[index];
        if (text[index] == ';') {
            break;
        }
    }
    if (!md_starts_reference(shape, 0, count)) {
        return;
    }
    sbuf_reserve(&ctx->out, 1);
    if (ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memmove(&ctx->out.data[amp], &ctx->out.data[amp - 1], (size_t)tail * sizeof(Py_UCS4));
    ctx->out.data[amp - 1] = '\\';
    ctx->out.len++;
}

/* Emit inline text with normal-flow whitespace collapsing and markdown escaping.
   Prose is mostly plain runs, so after the first character of a word is placed
   (which resolves the deferred space, markers and escapes) the rest of the run --
   no whitespace, nothing to escape -- is bulk-copied in one memcpy. */
static int md_br_trailing(md_ctx *ctx, th_node *node);

static inline int md_non_ascii_space(Py_UCS4 ch) {
    return Py_UNICODE_ISSPACE(ch); /* GCOVR_EXCL_BR_LINE: ch >= 128 makes the macro's ASCII arm unreachable */
}

/* Whether every character written since the line's first digit is a digit, the run a line-numbered marker starts in.
   The scan resumes where the last one stopped, so a run of digits costs its length once and not once per text node
   written after it. */
static int md_line_digits_only(md_ctx *ctx) {
    Py_ssize_t index = ctx->line_number_checked;
    while (index < ctx->out.len && is_ascii_digit(ctx->out.data[index])) {
        TH_WORK_STEP();
        index++;
    }
    ctx->line_number_checked = index;
    return index == ctx->out.len;
}

static void md_emit_text(md_ctx *ctx, th_node *node, const Py_UCS4 *text, Py_ssize_t len) {
    if (text == NULL && len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        ctx->out.failed = 1;       /* GCOVR_EXCL_LINE: allocation failure */
        return;                    /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (ctx->line_number_start > 0 && !ctx->space_pending && ctx->pending == NULL) {
        if (md_line_digits_only(ctx)) {
            Py_ssize_t scan = 0;
            while (scan < len && text[scan] >= '0' && text[scan] <= '9') {
                scan++;
            }
            if (scan < len && (text[scan] == '.' || text[scan] == ')')) {
                md_emit_text(ctx, node, text, scan);
                sbuf_putc(&ctx->out, '\\');
                md_emit_text(ctx, node, &text[scan], len - scan);
                return;
            }
        } else {
            ctx->line_number_start = 0;
        }
    }
    /* Source text funds at most the same number of attribute characters repeated by clones. */
    ctx->clone_budget += len;
    /* Source text funds at most the same number of reopened tag characters. */
    ctx->tags_left += len;
    /* a reference continues only when nothing comes between the last node's `&` and name
       and this node's first character; what is already written md_escape_split_reference
       reads back, and an opening run still pending would come between */
    if (ctx->amp_open) {
        ctx->amp_open = 0;
        if (ctx->pending == NULL || ctx->pending->emitted) {
            md_escape_split_reference(ctx, text, len);
        }
    }
    int translit = ctx->opt->transliterate;
    Py_ssize_t index = 0;
    Py_ssize_t trailing_space_start = -1;
    int at_line_end = -1;
    while (index < len) {
        Py_UCS4 ch = text[index];
        uint8_t kind = ch < 0x80 ? MD_ASCII[ch] : 0;
        if (kind & MD_CH_SPACE) {
            ctx->space_pending = 1;
            index++;
            continue;
        }
        if (ctx->space_pending && !ctx->drop_space && ctx->opt->wrap_width > 0 && ctx->no_wrap == 0) {
            Py_ssize_t word_end = index;
            while (word_end < len && !is_space(text[word_end])) {
                word_end++;
            }
            ctx->pending_word = (int)(word_end - index);
        }
        md_before_visible(ctx);
        if (!ctx->line_has_content && ch >= '0' && ch <= '9' && ctx->escape_prose) {
            Py_ssize_t consumed = md_escape_line_number(ctx, text, index, len);
            if (consumed > 0) {
                index += consumed;
                continue;
            }
            ctx->line_number_start = ctx->out.len + 1;
            ctx->line_number_checked = ctx->out.len;
        }
        int by_context = ((kind & MD_CH_CONTEXT) || (translit && ch == 0x2190)) && ctx->escape_prose &&
                         md_escape_in_text(ctx, text, index, len);
        /* markdown-it decodes &#133; as U+FFFD. */
        if (ch >= 0x80 && ch != 0x85 && !translit && ctx->escape_prose && ctx->marker_count == 0 &&
            md_non_ascii_space(ch)) {
            if (trailing_space_start < 0) {
                trailing_space_start = len;
                while (trailing_space_start > index && Py_UNICODE_ISSPACE(text[trailing_space_start - 1])) {
                    trailing_space_start--;
                }
            }
            if (index >= trailing_space_start && at_line_end < 0) {
                at_line_end = node == NULL || md_br_trailing(ctx, node);
            }
            if (ctx->in_heading || !ctx->line_has_content || (index >= trailing_space_start && at_line_end)) {
                sbuf_puts(&ctx->out, "&#");
                md_put_decimal(&ctx->out, (Py_ssize_t)ch);
                sbuf_putc(&ctx->out, ';');
                ctx->line_has_content = 1;
            } else {
                md_put_char(ctx, ch, 0);
            }
        } else {
            md_put_char(ctx, ch, (kind & ctx->escape_mask) || by_context);
        }
        index++;
        /* past the word's first character nothing is at a line start, so an escaped
           character only needs its backslash and the plain runs between them copy whole */
        while (1) {
            Py_ssize_t start = index;
            while (index < len && (text[index] < 0x80 ? !(MD_ASCII[text[index]] & ctx->run_stop)
                                                      : !translit && (!ctx->escape_prose || ctx->marker_count > 0 ||
                                                                      !md_non_ascii_space(text[index])))) {
                index++;
            }
            if (index > start) {
                md_put_run(ctx, &text[start], index - start);
            }
            if (index == len || text[index] >= 0x80 || !(MD_ASCII[text[index]] & ctx->escape_mask)) {
                break;
            }
            sbuf_putc(&ctx->out, '\\');
            sbuf_putc(&ctx->out, text[index]);
            index++;
        }
    }
}

/* Case-insensitive ASCII compare of a code-point slice to a lowercase C key. */
static int md_ucs4_ieq(const Py_UCS4 *text, Py_ssize_t len, const char *key) {
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (character >= 'A' && character <= 'Z') {
            character += 'a' - 'A';
        }
        if (key[index] == '\0' || character != (Py_UCS4)(unsigned char)key[index]) {
            return 0;
        }
    }
    return key[len] == '\0';
}

/* Find the declaration `prop: value` in an inline style string (a `style`
   attribute value), matching the property case-insensitively and trimming the
   value. Returns 1 with the value slice set when present. */
static int md_css_prop(const Py_UCS4 *style, Py_ssize_t style_len, const char *prop, const Py_UCS4 **out_value,
                       Py_ssize_t *out_len) {
    Py_ssize_t prop_len = (Py_ssize_t)strlen(prop);
    Py_ssize_t cursor = 0;
    while (cursor < style_len) {
        Py_ssize_t name_start = cursor;
        while (cursor < style_len && style[cursor] != ':' && style[cursor] != ';') {
            cursor++;
        }
        Py_ssize_t name_end = cursor;
        if (cursor >= style_len || style[cursor] == ';') {
            cursor++; /* a declaration without a colon: skip past its terminator */
            continue;
        }
        cursor++; /* past ':' */
        Py_ssize_t value_start = cursor;
        while (cursor < style_len && style[cursor] != ';') {
            cursor++;
        }
        Py_ssize_t value_end = cursor;
        cursor++; /* past ';' */
        while (name_start < name_end && is_space(style[name_start])) {
            name_start++;
        }
        while (name_end > name_start && is_space(style[name_end - 1])) {
            name_end--;
        }
        while (value_start < value_end && is_space(style[value_start])) {
            value_start++;
        }
        while (value_end > value_start && is_space(style[value_end - 1])) {
            value_end--;
        }
        if (name_end - name_start == prop_len && md_ucs4_ieq(&style[name_start], prop_len, prop)) {
            *out_value = &style[value_start];
            *out_len = value_end - value_start;
            return 1;
        }
    }
    return 0;
}

static int md_css_whitespace(md_ctx *ctx, th_node *node, int inherited) {
    Py_ssize_t length;
    const Py_UCS4 *style = md_attr(ctx->tree, node, "style", &length);
    if (length == 0) {
        return inherited;
    }
    int mode = th_css_inline_whitespace(style, length, inherited);
    if (mode == -2) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return inherited;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return mode;
}

/* Whether the value is one of the four bold weights a Google Docs export emits. */
static int md_css_bold(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const weights[] = {"bold", "700", "800", "900"};
    for (size_t index = 0; index < sizeof(weights) / sizeof(weights[0]); index++) {
        if (md_ucs4_ieq(value, len, weights[index])) {
            return 1;
        }
    }
    return 0;
}

/* Whether the value names one of the fixed-width fonts Google Docs uses for code. */
static int md_css_fixed(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const fonts[] = {"courier new", "consolas"};
    for (size_t index = 0; index < sizeof(fonts) / sizeof(fonts[0]); index++) {
        if (md_ucs4_ieq(value, len, fonts[index])) {
            return 1;
        }
    }
    return 0;
}

/* Whether a list-style-type value renders as an unordered bullet rather than a
   number, mirroring inscriptis/html2text's set of bullet keywords. */
static int md_css_unordered(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const bullets[] = {"disc", "circle", "square", "none"};
    for (size_t index = 0; index < sizeof(bullets) / sizeof(bullets[0]); index++) {
        if (md_ucs4_ieq(value, len, bullets[index])) {
            return 1;
        }
    }
    return 0;
}

/* The leading integer pixel count of a length value ("72px" -> 72). */
static int md_css_px(const Py_UCS4 *value, Py_ssize_t len) {
    int pixels = 0;
    for (Py_ssize_t index = 0; index < len && value[index] >= '0' && value[index] <= '9'; index++) {
        int digit = (int)(value[index] - '0');
        if (pixels > (INT_MAX - digit) / 10) {
            return INT_MAX;
        }
        pixels = pixels * 10 + digit;
    }
    return pixels;
}

/* Double a full render stack, moving it off the caller's inline storage on the first
   growth. Returns 0, or -1 after setting ctx->failed. */
static int md_grow_stack(md_ctx *ctx, void **items, Py_ssize_t *cap, const void *inline_items, size_t item_size) {
    size_t grown_cap;
    size_t bytes;
    int fits = th_grow_cap((size_t)*cap + 1, (size_t)*cap, 1, item_size, &grown_cap, &bytes);
    if (!fits) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a depth no allocation could hold */
        ctx->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        return -1;       /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    void *grown = *items == inline_items ? PyMem_Malloc(bytes) : PyMem_Realloc(*items, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (*items == inline_items) {
        memcpy(grown, inline_items, (size_t)*cap * item_size);
    }
    *items = grown;
    *cap = (Py_ssize_t)grown_cap;
    return 0;
}

/* Reserve the next frame, with no prefix to restore and no marker. NULL after setting
   ctx->failed when the stack cannot grow; the caller then skips the subtree. */
static inline md_frame *md_push(md_ctx *ctx, th_node *node, enum md_walk walk, enum md_leave leave) {
    if (ctx->frame_count == ctx->frame_cap) {
        int grown = md_grow_stack(ctx, (void **)&ctx->frames, &ctx->frame_cap, ctx->inline_frames, sizeof(md_frame));
        if (grown < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    md_frame *frame = &ctx->frames[ctx->frame_count++];
    frame->node = node;
    frame->child = NULL;
    frame->prefix_base = -1;
    frame->marker = -1;
    frame->walk = (uint8_t)walk;
    frame->leave = (uint8_t)leave;
    frame->in_run = 0;
    return frame;
}

/* Open a deferred marker, returning its index, or -1 after setting ctx->failed. */
static Py_ssize_t md_push_marker(md_ctx *ctx, const char *text) {
    if (ctx->marker_count == ctx->marker_cap) {
        int grown =
            md_grow_stack(ctx, (void **)&ctx->markers, &ctx->marker_cap, ctx->inline_markers, sizeof(md_marker));
        if (grown < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    ctx->markers[ctx->marker_count] = (md_marker){.open = text, .close = text};
    ctx->pending = &ctx->markers[ctx->marker_count];
    return ctx->marker_count++;
}

static int md_is_emphasis(uint16_t atom) {
    switch (atom) {
    case TH_TAG_STRONG:
    case TH_TAG_B:
    case TH_TAG_EM:
    case TH_TAG_I:
    case TH_TAG_DEL:
    case TH_TAG_S:
    case TH_TAG_STRIKE:
        return 1;
    default:
        return 0;
    }
}

/* Whether a delimiter run between before and after is left- or right-flanking
   (CommonMark 6.2), classifying whitespace and punctuation as md_edge does. */
static int md_left_flanking(Py_UCS4 before, Py_UCS4 after) {
    uint8_t next = md_edge(after);
    return next != MD_EDGE_SPACE && (next != MD_EDGE_PUNCT || md_edge(before) != 0);
}

static int md_right_flanking(Py_UCS4 before, Py_UCS4 after) {
    uint8_t prev = md_edge(before);
    return prev != MD_EDGE_SPACE && (prev != MD_EDGE_PUNCT || md_edge(after) != 0);
}

/* Whether a run of `*` or `~` (star) or of `_` can open, or close, emphasis
   (CommonMark 6.2 rules 1-8; GFM strikethrough flanks as `*` does). */
static int md_run_opens(Py_UCS4 before, Py_UCS4 after, int star) {
    return md_left_flanking(before, after) &&
           (star || !md_right_flanking(before, after) || md_edge(before) == MD_EDGE_PUNCT);
}

static int md_run_closes(Py_UCS4 before, Py_UCS4 after, int star) {
    return md_right_flanking(before, after) &&
           (star || !md_left_flanking(before, after) || md_edge(after) == MD_EDGE_PUNCT);
}

/* The first character of a non-empty text node, or fallback when realizing its span
   fails; md_run then stops on the tree's failure and the render raises MemoryError. */
static Py_UCS4 md_text_first(md_ctx *ctx, th_node *node, Py_UCS4 fallback) {
    const Py_UCS4 *text = need_text(ctx->tree, node);
    return text == NULL ? fallback : text[0]; /* GCOVR_EXCL_BR_LINE: lazy text OOM */
}

static int md_is_link(md_ctx *ctx, th_node *node);

/* How far md_char_after reads past a closing run, in nodes. The character that follows is
   nearly always in the next node or two: only elements that hold no text stretch the walk,
   and an inline SVG icon is the long case a real page has. */
#define MD_AFTER_REACH 256

/* An empty opening mark or a non-ASCII mark reads as ordinary: the content or the closing mark
   comes first, and an ordinary character is the safe answer when unsure (md_char_after). */
static Py_UCS4 md_quote_edge(const char *mark) {
    unsigned char first = (unsigned char)mark[0];
    return first != '\0' && first < 0x80 ? first : 'a';
}

/* Whether a converter is registered for the element's tag; an allocation failure answers yes, the uncertain side. */
static int md_has_converter(md_ctx *ctx, th_node *node) {
    PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->text, node->text_len);
    if (tag == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int found = PyDict_Contains(ctx->opt->converters, tag);
    Py_DECREF(tag);
    if (found < 0) {     /* GCOVR_EXCL_BR_LINE: a str key never raises on lookup */
        ctx->failed = 1; /* GCOVR_EXCL_LINE: unreachable hash-error path */
        return 1;        /* GCOVR_EXCL_LINE: unreachable hash-error path */
    }
    return found;
}

/* The character the output holds right after an inline element, read off the tree for
   the flanking test of its closing run: the next text's first character, a line end at
   a block or cell boundary or a break, the punctuation an enclosing run, link or quote
   closes with, or the punctuation that a following link, quote, run or code span opens
   with. An image, preserved white space, or anything a converter writes, reads as
   ordinary: an ordinary character is where a run after punctuation cannot close, so an
   uncertain answer only ever trades a delimiter for the raw-HTML tag. A converter writes
   only for the elements registered with it, so the scan gives up where it meets one: an
   element it would step into, or an enclosing one whose converter adds to what its
   children wrote. Inside a converted element a block's line can end the converted text,
   which the converter then extends, so there a block boundary reads as ordinary too.
   Every element that writes without holding a text node has to end the scan, or a line
   of them is scanned to its end from the run around each; for one this function does not
   know, the scan gives the uncertain answer past MD_AFTER_REACH nodes. The climb ends at the block around the
   element, or past the top of a tree that has none above it (a fragment, a shadow root, a
   detached inline element, an XML document, whose elements carry no HTML atom), where the
   output ends like a line. */
static Py_UCS4 md_char_after(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    int converters = opt->converters != NULL;
    th_node *cursor = node;
    th_node *next = node->next_sibling;
    int depth = 0;       /* how far the scan has descended into the elements after node */
    int punct_depth = 0; /* the outermost entered element that writes punctuation before its text, 0 for none */
    int code_depth = 0;  /* the outermost entered code span, which writes only the text it holds, 0 for none */
    for (int reach = 0; reach < MD_AFTER_REACH; reach++) {
        TH_WORK_STEP();
        while (next == NULL) {
            cursor = cursor->parent;
            if (cursor == NULL) {
                return '\n'; /* past a parentless root nothing follows */
            }
            if (depth > 0) {
                /* an entered element ran out without text, so it wrote nothing */
                punct_depth = punct_depth == depth ? 0 : punct_depth;
                code_depth = code_depth == depth ? 0 : code_depth;
                depth--;
                next = cursor->next_sibling;
                continue;
            }
            if (converters && md_has_converter(ctx, cursor)) {
                return 'a';
            }
            uint16_t atom = cursor->ns == TH_NS_HTML ? cursor->atom : TH_TAG_UNKNOWN;
            if (is_md_block(atom) || atom == TH_TAG_TD || atom == TH_TAG_TH) {
                return ctx->converting ? 'a' : '\n';
            }
            if (md_is_emphasis(atom)) {
                return '*';
            }
            /* inside link text only that link closes with a bracket: an `a` in it is a placeholder */
            if (ctx->link != NULL ? cursor == ctx->link : md_is_link(ctx, cursor)) {
                return ']';
            }
            if (atom == TH_TAG_Q && opt->quote_close[0] != '\0' && !md_tag_filtered(opt, atom)) {
                return md_quote_edge(opt->quote_close);
            }
            next = cursor->next_sibling;
        }
        cursor = next;
        next = cursor->next_sibling;
        if (cursor->type == TH_NODE_TEXT) {
            if (cursor->text_len > 0) {
                return punct_depth > 0 ? '*' : md_text_first(ctx, cursor, '*');
            }
            continue;
        }
        if (cursor->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (converters && md_has_converter(ctx, cursor)) {
            return punct_depth > 0 ? '*' : 'a'; /* a converter runs before an element is skipped */
        }
        if (is_md_skipped(cursor)) {
            continue;
        }
        uint16_t atom = cursor->ns == TH_NS_HTML ? cursor->atom : TH_TAG_UNKNOWN;
        if (is_md_block(atom) || atom == TH_TAG_BR) {
            return '\n';
        }
        if (code_depth == 0 && cursor->attr_count > 0 && md_css_whitespace(ctx, cursor, TH_WS_NORMAL) != TH_WS_NORMAL) {
            /* preserved white space is written whole, empty or not (md_render_whitespace) */
            return punct_depth > 0 ? '*' : 'a';
        }
        if (atom == TH_TAG_IMG) {
            return punct_depth > 0 ? '*' : 'a';
        }
        if (atom == TH_TAG_Q && code_depth == 0 && !md_tag_filtered(opt, atom)) {
            /* a quote opens the runs waiting around it, then writes its marks, empty or not */
            if (punct_depth > 0) {
                return '*';
            }
            if (opt->quote_open[0] != '\0' || opt->quote_close[0] != '\0') {
                return md_quote_edge(opt->quote_open);
            }
        }
        if (ctx->link == NULL && md_is_link(ctx, cursor)) {
            return '['; /* link text opens with its bracket, empty or not; inside link text an `a` is a placeholder */
        }
        if ((atom == TH_TAG_DEL || atom == TH_TAG_S || atom == TH_TAG_STRIKE) && !opt->keep_strikethrough) {
            continue; /* struck-through content is not written */
        }
        if (cursor->first_child != NULL) {
            depth++;
            int code = atom == TH_TAG_CODE || atom == TH_TAG_KBD || atom == TH_TAG_SAMP;
            if (punct_depth == 0 && (md_is_emphasis(atom) || code)) {
                punct_depth = depth;
            }
            if (code_depth == 0 && code) {
                code_depth = depth;
            }
            next = cursor->first_child;
        }
    }
    return 'a'; /* out of reach */
}

/* Rewrite a written open run as the element's raw-HTML tag, moving the content after
   it and every recorded output offset past it along. */
static void md_rewrite_open(md_ctx *ctx, md_marker *marker) {
    const char *open = MD_HTML_TAGS[marker->html - 1][0];
    Py_ssize_t open_len = marker->open_len;
    Py_ssize_t shift = (Py_ssize_t)strlen(open) - open_len;
    sbuf_reserve(&ctx->out, shift);
    if (ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS4 *at = &ctx->out.data[marker->open_at];
    memmove(at + open_len + shift, at + open_len,
            (size_t)(ctx->out.len - marker->open_at - open_len) * sizeof(Py_UCS4));
    for (Py_ssize_t index = 0; open[index] != '\0'; index++) {
        at[index] = (Py_UCS4)(unsigned char)open[index];
    }
    ctx->out.len += shift;
    /* a break recorded past the run moves with it; one before it no longer ends the output */
    if (ctx->break_end >= 0 && ctx->break_start > marker->open_at) {
        ctx->break_start += shift;
        ctx->break_end += shift;
    }
    /* the wrap column rescans the line from the last newline */
    ctx->line_start = 0;
    ctx->line_checked = 0;
    /* the shift can put markup inside the digit run, so the run is checked again from its start */
    ctx->line_number_checked = ctx->line_number_start - 1;
    marker->open = open;
    marker->open_len = open_len + shift;
    marker->close = MD_HTML_TAGS[marker->html - 1][1];
}

/* Whether data[at] is the delimiter character with no backslash escaping it. */
static int md_live_delimiter(const Py_UCS4 *data, Py_ssize_t at, Py_UCS4 delimiter) {
    return data[at] == delimiter && !(at > 0 && data[at - 1] == '\\');
}

/* Once a run's content is written the characters around both of its delimiters are
   known, so a run that cannot open or close where it sits (CommonMark 6.2), or whose
   opener could close an enclosing open run of the same character instead, falls back to
   its raw-HTML tag. Flanking is judged for the whole delimiter run, which takes in the
   same character written right next to this one by a neighboring element. md_marker_html
   decided before any content existed and sees only an element holding one text node, so
   this settles the rest. node is the element, whose closing run md_char_after looks
   past, or NULL when a line end follows the close. */
static void md_settle_run(md_ctx *ctx, Py_ssize_t index, th_node *node) {
    md_marker *marker = &ctx->markers[index];
    if (!marker->emitted || marker->node == NULL || marker->open[0] == '<' || md_folded(marker)) {
        return;
    }
    const Py_UCS4 *data = ctx->out.data;
    /* the run is read back as written: a configured wrapper may be non-ASCII, and out
       holds it decoded, one element per code point */
    Py_UCS4 delimiter = data[marker->open_at];
    Py_ssize_t open_len = marker->open_len;
    /* md_put_close moves a close that would follow a hard break in front of it */
    int moved = ctx->out.len == ctx->break_end;
    Py_ssize_t close_at = moved ? ctx->break_start : ctx->out.len;
    /* the usual shape settles at a glance: a star run after a space or a line start,
       around content that starts and ends with an ordinary character, opens (it cannot
       close) and its close closes, and with equal runs on both sides the rule of 3 never
       applies; a run that nothing was written after has no first character to read */
    if (delimiter == '*' && marker->open_at + open_len < close_at && md_edge(data[marker->open_at + open_len]) == 0 &&
        md_edge(data[close_at - 1]) == 0 &&
        (marker->open_at == 0 || md_edge(data[marker->open_at - 1]) == MD_EDGE_SPACE)) {
        return;
    }
    Py_ssize_t run_start = marker->open_at;
    while (run_start > 0 && md_live_delimiter(data, run_start - 1, delimiter)) {
        run_start--;
    }
    Py_ssize_t run_end = marker->open_at + open_len;
    while (run_end < close_at && data[run_end] == delimiter) {
        run_end++;
    }
    Py_UCS4 before = run_start > 0 ? data[run_start - 1] : '\n';
    Py_UCS4 after = run_end < close_at ? data[run_end] : '\n';
    int star = delimiter != '_';
    int settled = md_run_opens(before, after, star);
    for (Py_ssize_t outer = ctx->marker_base; settled && outer < index; outer++) {
        /* an enclosing run written into this same delimiter run opens with it instead */
        settled = !(md_first_cp(ctx->markers[outer].open) == md_first_cp(marker->open) &&
                    ctx->markers[outer].open_at < run_start && md_run_closes(before, after, star));
    }
    if (settled) {
        Py_ssize_t last = close_at - 1;
        while (last > run_end && md_live_delimiter(data, last, delimiter)) {
            last--;
        }
        Py_UCS4 follow = '\n';
        if (moved) {
            follow = ctx->opt->line_break == TH_MD_BREAK_BACKSLASH ? '\\' : ' ';
        } else if (ctx->space_pending) {
            follow = ' ';
        } else if (node != NULL && (!star || md_edge(data[last]) != 0)) {
            /* after an ordinary character a star run closes whatever follows */
            follow = md_char_after(ctx, node);
        }
        settled = md_run_closes(data[last], follow, star);
        /* when either run can both open and close, the two pair only if their lengths do
           not sum to a multiple of 3, unless both lengths are (CommonMark 6.2 rule 9); with
           the sum a multiple of 3, the opener's length decides both */
        Py_ssize_t open_run = run_end - run_start;
        Py_ssize_t close_run = close_at - 1 - last + open_len; /* a delimiter closes with the run it opens with */
        if (settled && (md_run_closes(before, after, star) || md_run_opens(data[last], follow, star)) &&
            (open_run + close_run) % 3 == 0 && open_run % 3 != 0) {
            settled = 0;
        }
    }
    Py_ssize_t suffix_start = close_at;
    while (suffix_start > marker->open_at + open_len && md_live_delimiter(data, suffix_start - 1, delimiter)) {
        suffix_start--;
    }
    /* A nested close of the same width would merge with this close into one run. */
    if (close_at - suffix_start == open_len) {
        settled = 0;
    }
    /* GFM can bind adjacent strike and emphasis runs across a punctuation edge. */
    if (settled && delimiter == '~' && marker->open_at > 0) {
        Py_UCS4 previous = data[marker->open_at - 1];
        if (previous == '*' || previous == '_') {
            Py_ssize_t boundary = marker->open_at;
            while (boundary > 0 && md_live_delimiter(data, boundary - 1, previous)) {
                boundary--;
            }
            if (boundary > 0 && md_edge(data[boundary - 1]) == MD_EDGE_PUNCT) {
                settled = 0;
            }
        }
    }
    if (settled && delimiter != '~' && marker->open_at >= 2 && data[marker->open_at - 1] == '~' &&
        data[marker->open_at - 2] == '~' && md_edge(data[marker->open_at + open_len]) == MD_EDGE_PUNCT) {
        settled = 0;
    }
    if (!settled) {
        md_rewrite_open(ctx, marker);
    }
}

/* Close the marker a frame opened, returning its closing run when the open run was
   written (so the close run must be too), NULL otherwise. */
/* A delimiter run pairs only within one block's inline content (CommonMark 6.2).
   Deep raw HTML markers also need closing here, or the HTML reader may drop
   formatting when it reconstructs later blocks. */
static void md_suspend_markers(md_ctx *ctx) {
    if (ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return;            /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (ctx->marker_count - ctx->marker_base > 8) {
        ctx->deep_block_span = 1;
    }
    if (ctx->open_markers == 0) {
        return;
    }
    for (Py_ssize_t index = ctx->marker_count; index > ctx->marker_base; index--) {
        md_marker *marker = &ctx->markers[index - 1];
        if (!marker->emitted) {
            continue;
        }
        if (!marker->spanning) {
            md_settle_run(ctx, index - 1, NULL);
            if (ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return;            /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        marker->emitted = 0;
        ctx->open_markers--;
        if (md_folded(marker)) {
            continue;
        }
        md_put_close(ctx, marker->close);
        if (ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return;            /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
}

static const char *md_pop_marker(md_ctx *ctx, Py_ssize_t marker) {
    if (marker < 0) {
        return NULL;
    }
    for (Py_ssize_t index = marker; index < ctx->marker_count; index++) {
        if (ctx->markers[index].emitted) {
            ctx->open_markers--;
        }
    }
    ctx->marker_count = marker;
    if (ctx->marker_count - ctx->marker_base <= 8) {
        ctx->deep_block_span = 0;
    }
    md_settle_pending(ctx);
    const md_marker *popped = &ctx->markers[marker];
    return popped->emitted && !md_folded(popped) ? popped->close : NULL;
}

static int md_enter_converter(md_ctx *ctx, th_node *node);

static inline int md_apply_converter(md_ctx *ctx, th_node *node) {
    return ctx->opt->converters != NULL && node->type == TH_NODE_ELEMENT && md_enter_converter(ctx, node);
}
static void md_render_block(md_ctx *ctx, th_node *node);

/* Whether whitespace or punctuation follows the element in the output, which a closing
   `_` run needs to pair (commonmark.js 0.31.2 inlines.js L295-L297) and is enough for a
   `*` run too. It is known in O(1) when a text sibling follows, or when the element ends
   a block laid out in the normal flow, whose line then ends. Link text, a text-mode
   table cell and the converter of an enclosing element can each put text right after a
   block, so there it is not. */
static int md_followed_by_break(md_ctx *ctx, th_node *node) {
    th_node *sibling = node->next_sibling;
    if (sibling == NULL) {
        /* a node that is not an element carries TH_TAG_UNKNOWN, never a block atom */
        th_node *parent = node->parent;
        if (parent->ns == TH_NS_HTML && md_is_emphasis(parent->atom)) {
            /* the enclosing run's closing delimiter or tag is punctuation, unless a converter writes it instead */
            return ctx->opt->converters == NULL || !md_has_converter(ctx, parent);
        }
        return parent->ns == TH_NS_HTML && is_md_block(parent->atom) && ctx->inline_only == 0 && !ctx->in_cell &&
               !ctx->converting;
    }
    if (sibling->type != TH_NODE_TEXT || sibling->text_len == 0) {
        return 0;
    }
    return md_edge(md_text_first(ctx, sibling, ' ')) != 0;
}

static const char *const MD_ALTERNATES[][2] = {{"*", "_"}, {"_", "*"}, {"**", "__"}, {"__", "**"}};

/* The other emphasis character for a run that would merge with the identical run the
   element before it left (`*a*_b_`), or NULL when that cannot round-trip either. Right
   after the earlier run's punctuation it always opens; it closes where whitespace or
   punctuation follows, and the prose must escape it so no literal one joins the run. */
static const char *md_alternate_run(md_ctx *ctx, const md_marker *marker) {
    for (size_t index = 0; index < sizeof(MD_ALTERNATES) / sizeof(MD_ALTERNATES[0]); index++) {
        if (strcmp(marker->open, MD_ALTERNATES[index][0]) == 0) {
            const char *alternate = MD_ALTERNATES[index][1];
            return MD_ASCII[(unsigned char)alternate[0]] & ctx->escape_mask && md_followed_by_break(ctx, marker->node)
                       ? alternate
                       : NULL;
        }
    }
    return NULL;
}

/* Record, as MD_MARK_OPEN_PUNCT and MD_MARK_CLOSE_FAILS, the flanking context an
   emphasis/strikethrough element needs to choose between its delimiter and the raw-HTML
   fallback: whether the first visible content character is punctuation, and whether the
   last one is punctuation with an ordinary character after the element. Only an element
   whose content is one text node is handled, so the lookup is O(1) and never descends
   the tree; any richer content keeps the delimiter. A run around ordinary text always
   flanks, so it returns after one look at each end. An element at the end of its
   parent, or before a non-text sibling, is treated as followed by whitespace, which is
   what the end of a line is to the flanking rules. */
static void md_wrap_flank(md_ctx *ctx, th_node *node, md_marker *marker) {
    th_node *only = node->first_child;
    if (only == NULL || only->next_sibling != NULL || only->type != TH_NODE_TEXT || only->text_len == 0) {
        return;
    }
    const Py_UCS4 *text = need_text(ctx->tree, only);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        return;         /* GCOVR_EXCL_LINE: the walk reports the allocation failure */
    }
    Py_ssize_t start = 0;
    Py_ssize_t end = only->text_len;
    Py_UCS4 first = text[start];
    Py_UCS4 last = text[end - 1];
    if ((first | last) < 128 && (MD_EDGE[first] | MD_EDGE[last]) == 0) {
        return; /* ordinary characters at both ends: the run flanks wherever it sits */
    }
    while (start < end && is_space(text[start])) {
        start++;
    }
    while (end > start && is_space(text[end - 1])) {
        end--;
    }
    if (start == end) {
        return;
    }
    if (md_is_ascii_punct(text[start])) {
        marker->flags |= MD_MARK_OPEN_PUNCT;
    }
    if (md_is_ascii_punct(text[end - 1])) {
        th_node *sibling = node->next_sibling;
        Py_UCS4 next = end < only->text_len ? ' '
                       : sibling != NULL && sibling->type == TH_NODE_TEXT && sibling->text_len > 0
                           ? md_text_first(ctx, sibling, ' ')
                           : ' ';
        if (md_edge(next) == 0) {
            marker->flags |= MD_MARK_CLOSE_FAILS;
        }
    }
}

/* Wrap an inline element's content in a marker (** , * , ~~). The open run is
   deferred (md_before_visible writes it at the first visible character) so a
   leading inner space moves outside; the close run is written only if the open
   one was, so an empty <b></b> leaves nothing behind. The owed space is left
   pending across the close, so a trailing inner space also lands outside. Returns the
   marker's index, or -1 after an allocation failure. */
static Py_ssize_t md_enter_wrap(md_ctx *ctx, th_node *node, const char *delim) {
    md_frame *frame = md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_WRAP);
    if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return frame->marker = md_push_marker(ctx, delim);
}

/* Wrap an emphasis or strikethrough element, giving its marker the raw-HTML fallback
   (html) used when the delimiter would not round-trip, and its flanking context. */
static void md_enter_emphasis(md_ctx *ctx, th_node *node, const char *delim, uint8_t html) {
    Py_ssize_t index = md_enter_wrap(ctx, node, delim);
    if (index < 0) { /* GCOVR_EXCL_BR_LINE: -1 only on an allocation failure */
        return;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (delim[0] == '\0') {
        return; /* the options drop the markup, so there is nothing to fall back from */
    }
    md_marker *marker = &ctx->markers[index];
    marker->node = node;
    marker->html = html;
    ctx->tags_left += md_tags_len(html); /* each element pays for one pair of its own tags */
    if (ctx->raw_emphasis) {
        marker->open = MD_HTML_TAGS[html - 1][0];
        marker->close = MD_HTML_TAGS[html - 1][1];
    }
    marker->flags = html >= MD_HTML_DEL ? MD_MARK_STRIKE : 0;
    md_wrap_flank(ctx, node, marker);
}

static int md_all_spaces(const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (text[index] != ' ') {
            return 0;
        }
    }
    return 1;
}

/* The longest run of backticks anywhere in s, so an inline code span can fence
   with one more backtick than that and never be split by its own content. */
static Py_ssize_t md_max_backtick_run(const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t best = 0;
    Py_ssize_t run = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (text[index] == '`') {
            run++;
            if (run > best) {
                best = run;
            }
        } else {
            run = 0;
        }
    }
    return best;
}

enum { MD_CODE_INLINE, MD_CODE_LINE, MD_CODE_BREAK, MD_CODE_SKIPPED };

/* How code text treats an element: a block, or a table row or cell that the table
   model sets apart from its neighbors, lays out on lines of its own; hidden subtrees
   contribute no text, and anything else runs inline. */
static int md_code_kind(const th_node *node) {
    if (is_md_skipped(node)) {
        return MD_CODE_SKIPPED;
    }
    if (node->ns != TH_NS_HTML) {
        return MD_CODE_INLINE;
    }
    switch (node->atom) {
    case TH_TAG_BR:
        return MD_CODE_BREAK;
    case TH_TAG_TR:
    case TH_TAG_TD:
    case TH_TAG_TH:
        return MD_CODE_LINE;
    default:
        return is_md_block(node->atom) ? MD_CODE_LINE : MD_CODE_INLINE;
    }
}

/* Write the separator a block edge owes before a run that starts with first. */
static inline void md_code_boundary(sbuf *out, Py_UCS4 separator, Py_UCS4 first) {
    if (out->len > 0 && (separator == '\n' ? out->data[out->len - 1] != '\n'
                                           : !is_space(out->data[out->len - 1]) && !is_space(first))) {
        sbuf_putc(out, separator);
    }
}

/* Block descendants need a separator when flattened into code text, but inline
   descendants and explicit whitespace must keep their original adjacency. In a code
   block (separator '\n') a block edge and a <br> each end the line, as CSS lays them
   out, so a block's edge adds a newline unless the line already ended; in a span
   (separator ' ') both read as one space. */
static void md_collect_code_text(th_tree *tree, th_node *root, sbuf *out, Py_UCS4 separator) {
    int boundary = 0;
    th_node *parent = root;
    th_node *child = root->first_child;
    for (;;) {
        while (child == NULL) {
            if (parent == root) {
                return;
            }
            if (md_code_kind(parent) == MD_CODE_LINE) {
                boundary = 1;
            }
            child = parent->next_sibling;
            parent = parent->parent;
        }
        if (child->type == TH_NODE_TEXT && child->text_len > 0) {
            const Py_UCS4 *text = need_text(tree, child);
            if (text == NULL) {  /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                out->failed = 1; /* GCOVR_EXCL_LINE: allocation failure */
                return;          /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (boundary) {
                md_code_boundary(out, separator, text[0]);
            }
            sbuf_put_run(out, text, child->text_len);
            boundary = 0;
        } else if (child->type == TH_NODE_ELEMENT) {
            int kind = md_code_kind(child);
            if (kind == MD_CODE_BREAK) {
                /* a break ends the line as a newline does */
                if (boundary) {
                    md_code_boundary(out, separator, separator);
                }
                sbuf_putc(out, separator);
                boundary = 0;
            } else if (kind != MD_CODE_SKIPPED) {
                boundary |= kind == MD_CODE_LINE;
                parent = child;
                child = child->first_child;
                continue;
            }
        }
        child = child->next_sibling;
    }
}

/* Write an element's text as a code span. block marks a code block written inline,
   whose final newline ends its last line and draws nothing, as md_render_pre drops it. */
static void md_emit_code_span(md_ctx *ctx, th_node *node, int block) {
    sbuf content = {0};
    md_collect_code_text(ctx->tree, node, &content, ' ');
    if (content.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(content.data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t len = content.len;
    if (block && len > 0 && content.data[len - 1] == '\n') {
        len--;
    }
    if (len == 0) {
        PyMem_Free(content.data);
        return;
    }
    /* a reader turns a line ending in a code span into a space (CommonMark 6.1), and
       written as a newline it could open a block on the next line, so write the space */
    for (Py_ssize_t index = 0; index < len; index++) {
        if (content.data[index] == '\n') {
            content.data[index] = ' ';
        }
    }
    md_before_visible(ctx);
    if (ctx->out.len > 0 && ctx->out.data[ctx->out.len - 1] == '`') {
        /* a backtick right before this span would merge the two code runs into one
           (CommonMark 6.1); raw inline HTML keeps them apart, its content escaped so a
           reader takes it literally */
        sbuf_puts(&ctx->out, "<code>");
        for (Py_ssize_t index = 0; index < len; index++) {
            if (md_is_ascii_punct(content.data[index])) {
                sbuf_putc(&ctx->out, '\\');
            }
            sbuf_putc(&ctx->out, content.data[index]);
        }
        sbuf_puts(&ctx->out, "</code>");
        ctx->line_has_content = 1;
        PyMem_Free(content.data);
        return;
    }
    Py_ssize_t fence = md_max_backtick_run(content.data, len) + 1;
    /* a reader strips one space from each end of content that both starts and ends with
       one (CommonMark 6.1), so such content, like content edged with a backtick, gets
       one more on each side */
    int pad = content.data[0] == '`' || content.data[len - 1] == '`' ||
              (content.data[0] == ' ' && content.data[len - 1] == ' ' && !md_all_spaces(content.data, len));
    for (Py_ssize_t index = 0; index < fence; index++) {
        sbuf_putc(&ctx->out, '`');
    }
    if (pad) {
        sbuf_putc(&ctx->out, ' ');
    }
    md_put_run(ctx, content.data, len);
    if (pad) {
        sbuf_putc(&ctx->out, ' ');
    }
    for (Py_ssize_t index = 0; index < fence; index++) {
        sbuf_putc(&ctx->out, '`');
    }
    ctx->line_has_content = 1;
    PyMem_Free(content.data);
}

static void md_emit_url_without_breaks(md_ctx *ctx, const char *base, const Py_UCS4 *url, Py_ssize_t len);
static int md_attr_has_space_run(const Py_UCS4 *value, Py_ssize_t len);
static void md_emit_raw_html(md_ctx *ctx, th_node *node);
static void md_put_html_run(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t len, int encode_lf);

/* What a destination character means to md_emit_url, so one table load per character
   answers every test the layout pass makes. */
enum { MD_URL_ANGLE = 1, MD_URL_LT = 2, MD_URL_OPEN = 4, MD_URL_CLOSE = 8, MD_URL_BREAK = 16 };
static const uint8_t MD_URL[128] = {
    ['\t'] = MD_URL_BREAK, ['\n'] = MD_URL_BREAK, ['\r'] = MD_URL_BREAK, [' '] = MD_URL_ANGLE,  ['<'] = MD_URL_LT,
    ['('] = MD_URL_OPEN,   [')'] = MD_URL_CLOSE,  [0x00] = MD_URL_ANGLE, [0x01] = MD_URL_ANGLE, [0x02] = MD_URL_ANGLE,
    [0x03] = MD_URL_ANGLE, [0x04] = MD_URL_ANGLE, [0x05] = MD_URL_ANGLE, [0x06] = MD_URL_ANGLE, [0x07] = MD_URL_ANGLE,
    [0x08] = MD_URL_ANGLE, [0x0b] = MD_URL_ANGLE, [0x0c] = MD_URL_ANGLE, [0x0e] = MD_URL_ANGLE, [0x0f] = MD_URL_ANGLE,
    [0x10] = MD_URL_ANGLE, [0x11] = MD_URL_ANGLE, [0x12] = MD_URL_ANGLE, [0x13] = MD_URL_ANGLE, [0x14] = MD_URL_ANGLE,
    [0x15] = MD_URL_ANGLE, [0x16] = MD_URL_ANGLE, [0x17] = MD_URL_ANGLE, [0x18] = MD_URL_ANGLE, [0x19] = MD_URL_ANGLE,
    [0x1a] = MD_URL_ANGLE, [0x1b] = MD_URL_ANGLE, [0x1c] = MD_URL_ANGLE, [0x1d] = MD_URL_ANGLE, [0x1e] = MD_URL_ANGLE,
    [0x1f] = MD_URL_ANGLE, [0x7f] = MD_URL_ANGLE,
};

static int md_space_in_run(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t index) {
    return (index > 0 && text[index - 1] == ' ') || (index + 1 < len && text[index + 1] == ' ');
}

/* Write a link destination (CommonMark 6.3) after an optional base prefix. A bare
   destination cannot hold a space or an ASCII control character or start with `<`,
   and takes parentheses only in balanced pairs; anything else goes in the `<...>`
   form, which takes any parenthesis but no unescaped angle bracket. Parentheses stay
   bare while they balance, so a `wiki/Foo_(bar)` URL reads as written. A backslash,
   and a `&` that would decode as a character reference, are escaped in either form.
   So is a `<`, which the bare form would take as written: a reader that does not take
   the link (a destination nested past its parenthesis limit, a link inside the link
   text) reads the destination as running text, where that `<` would open raw HTML. A
   tab or line break is left out: no destination form holds a line ending (CommonMark
   6.3), and the URL parser removes every tab and newline anyway (URL Standard 4.4). */
static void md_emit_url(md_ctx *ctx, const char *base, const Py_UCS4 *url, Py_ssize_t len) {
    /* an empty destination is spelled "<>": bare, a following title or a reference
       definition's line end would be read in its place (CommonMark 4.7, 6.3) */
    int angle = len == 0 && *base == '\0';
    int depth = 0;
    int unbalanced = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        uint8_t kind = url[index] < 128 ? MD_URL[url[index]] : 0;
        if (kind == 0) {
            continue;
        }
        if (kind & MD_URL_BREAK) {
            md_emit_url_without_breaks(ctx, base, url, len);
            return;
        }
        if ((kind & MD_URL_ANGLE) || ((kind & MD_URL_LT) && index == 0 && *base == '\0')) {
            angle = 1;
        } else if (kind & MD_URL_OPEN) {
            depth++;
        } else if (kind & MD_URL_CLOSE) {
            unbalanced |= depth == 0;
            depth -= depth > 0;
        }
    }
    unbalanced |= depth > 0;
    if (angle) {
        sbuf_putc(&ctx->out, '<');
    }
    md_puts8(&ctx->out, base);
    for (Py_ssize_t index = 0; index < len; index++) {
        TH_WORK_STEP();
        Py_UCS4 ch = url[index];
        if (ctx->in_cell && ch == ' ' && md_space_in_run(url, len, index)) {
            sbuf_puts(&ctx->out, "&#32;");
            continue;
        }
        int bracket = ch == '<' || (angle ? ch == '>' : unbalanced && (ch == '(' || ch == ')'));
        if (bracket || ch == '\\' || (ch == '&' && md_starts_reference(url, index, len))) {
            sbuf_putc(&ctx->out, '\\');
        }
        md_put_literal(ctx, ch);
    }
    if (angle) {
        sbuf_putc(&ctx->out, '>');
    }
}

/* Write a destination that holds a tab or line break without them. Only such a rare
   destination pays for the copy, made so that a reference split by a break is seen. */
static void md_emit_url_without_breaks(md_ctx *ctx, const char *base, const Py_UCS4 *url, Py_ssize_t len) {
    Py_UCS4 *kept = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (kept == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (url[index] >= 128 || !(MD_URL[url[index]] & MD_URL_BREAK)) {
            kept[count++] = url[index];
        }
    }
    md_emit_url(ctx, base, kept, count);
    PyMem_Free(kept);
}

/* Write a link/image title inside its `"..."` delimiters: a `"` would close the
   title early and a `\` would escape the next character, so both are backslashed, as
   is a `&` that would decode as a character reference. A `<` is escaped as prose
   escapes it: a reader that does not take the link reads the title as running text,
   where that `<` would open raw HTML. A line break is written as its numeric
   reference, which a title decodes (CommonMark 6.3): written raw, the next line could
   open a block (a `>` quote, a `#` heading) and end the paragraph mid-link. A cell's
   copy-out collapses a space run, so a space in one is a reference too (CommonMark 2.5). */
static void md_emit_title(md_ctx *ctx, const Py_UCS4 *title, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        TH_WORK_STEP();
        if (title[index] == '\n' || title[index] == '\r') {
            sbuf_puts(&ctx->out, title[index] == '\n' ? "&#10;" : "&#13;");
            continue;
        }
        if (ctx->in_cell && title[index] == ' ' && md_space_in_run(title, len, index)) {
            sbuf_puts(&ctx->out, "&#32;");
            continue;
        }
        if (title[index] == '"' || title[index] == '\\' ||
            ((title[index] == '<' || title[index] == '&') && md_escape_by_context(title, index, len))) {
            sbuf_putc(&ctx->out, '\\');
        }
        md_put_literal(ctx, title[index]);
    }
}

/* A "#fragment" href targets the same document. The caller only reaches here with
   a present, non-empty href (an empty attribute resolves to no href at all). */
static int md_href_internal(const Py_UCS4 *href) {
    return href[0] == '#';
}

/* An href that already carries a scheme ("https://", "mailto:") is absolute and
   takes no base-url prefix; the autolink shortcut also needs an absolute target. */
static int md_href_absolute(const Py_UCS4 *href, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (href[index] == ':') {
            return 1;
        }
        if (href[index] == '/' || href[index] == '#' || href[index] == '?') {
            return 0;
        }
    }
    return 0;
}

/* An autolink `<url>` takes its content literally and ends at the first `>`, so it
   holds only a URL free of spaces, angle brackets and control characters. */
static int md_href_autolinkable(const Py_UCS4 *href, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (href[index] <= ' ' || href[index] == '<' || href[index] == '>' || href[index] == 0x7F) {
            return 0;
        }
    }
    return 1;
}

/* Record a reference-style target, returning its 1-based number. On allocation
   failure it sets ctx->failed and returns 0; the caller then renders inline. */
static Py_ssize_t md_add_reference(md_ctx *ctx, const Py_UCS4 *url, Py_ssize_t url_len, const Py_UCS4 *title,
                                   Py_ssize_t title_len) {
    if (ctx->ref_count == ctx->ref_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)(ctx->ref_count + 1), (size_t)ctx->ref_cap, 8, sizeof(md_reference), &cap, &bytes);
        if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            ctx->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return 0;        /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        md_reference *grown = PyMem_Realloc(ctx->refs, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return 0;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ctx->refs = grown;
        ctx->ref_cap = (Py_ssize_t)cap;
    }
    ctx->refs[ctx->ref_count].url = url;
    ctx->refs[ctx->ref_count].url_len = url_len;
    ctx->refs[ctx->ref_count].title = title;
    ctx->refs[ctx->ref_count].title_len = title_len;
    return ++ctx->ref_count;
}

/* The tree builder reopens an unclosed formatting element in every block after it, and each copy repeats the attributes
   the source spelled once. Rendered text funds the budget a copy's attributes spend, so the output cannot grow with the
   block count; a copy the budget does not cover keeps its text and drops the link or language. */
static int md_clone_pays(md_ctx *ctx, const th_node *node, Py_ssize_t len) {
    if (!(node->tag_flags & TH_ELEM_IMPLIED)) {
        ctx->clone_budget += len;
        return 1;
    }
    if (len > ctx->clone_budget) {
        return 0;
    }
    ctx->clone_budget -= len;
    return 1;
}

static void md_enter_link(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    Py_ssize_t href_len;
    const Py_UCS4 *href = md_attr(ctx->tree, node, "href", &href_len);
    if (href == NULL && th_node_attr_find(ctx->tree, node, "href", 4) >= 0) {
        /* an empty href links to the document itself (WHATWG 4.6.2); only a missing one
           leaves a placeholder */
        static const Py_UCS4 empty_href[1] = {0};
        href = empty_href;
    }
    int ignored = href == NULL || opt->ignore_links || (opt->skip_internal_links && md_href_internal(href));
    if (ctx->in_cell && !ignored && md_attr_has_space_run(href, href_len) && opt->link_style != TH_MD_LINK_REFERENCE &&
        !opt->link_title && (*opt->base_url == '\0' || md_href_absolute(href, href_len) || md_href_internal(href))) {
        Py_ssize_t len;
        Py_UCS4 *opening = th_node_markdown_open_tag(ctx->tree, node, TH_MARKDOWN_HTML_CELL, &ctx->clone_budget, &len);
        if (opening == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        md_before_visible(ctx);
        md_put_html_run(ctx, opening, len, 1);
        PyMem_Free(opening);
        ctx->line_has_content = 1;
        if (md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_RAW_LINK) == NULL) { /* GCOVR_EXCL_BR_LINE */
            return; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ctx->inline_only++;
        return;
    }
    /* wrap_links off keeps the whole [text](url) construct on one line */
    if (!opt->wrap_links) {
        ctx->no_wrap++;
    }
    Py_ssize_t title_len;
    const Py_UCS4 *title = md_attr(ctx->tree, node, "title", &title_len);
    /* a link cannot hold a link (CommonMark 6.3): a reader takes the inner one alone and reads the outer
       destination and title as text, where a `<` opens raw HTML, so inside link text an `a` is a placeholder too */
    if (ignored || ctx->link != NULL || !md_clone_pays(ctx, node, href_len + title_len)) {
        md_push(ctx, node, MD_WALK_INLINE, opt->wrap_links ? MD_LEAVE_NONE : MD_LEAVE_NO_WRAP);
        return;
    }
    int relative = *opt->base_url != '\0' && !md_href_absolute(href, href_len) && !md_href_internal(href);
    if (opt->autolink && title == NULL && !relative && md_href_absolute(href, href_len) &&
        md_href_autolinkable(href, href_len)) {
        /* the bare-URL shortcut <url> applies when the visible text is the href */
        Py_ssize_t text_len;
        Py_UCS4 *text = th_node_text(ctx->tree, node, &text_len);
        int matches = 0;
        if (text == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        } else if (text_len == href_len && memcmp(text, href, (size_t)href_len * sizeof(Py_UCS4)) == 0) {
            matches = 1;
        }
        PyMem_Free(text);
        if (matches) {
            md_before_visible(ctx);
            sbuf_putc(&ctx->out, '<');
            sbuf_put_run(&ctx->out, href, href_len);
            sbuf_putc(&ctx->out, '>');
            ctx->line_has_content = 1;
            if (!opt->wrap_links) {
                ctx->no_wrap--;
            }
            return;
        }
    }
    md_frame *frame = md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_LINK);
    if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    frame->link.href = href;
    frame->link.href_len = href_len;
    frame->link.title = title;
    frame->link.title_len = title_len;
    frame->link.relative = relative;
    /* the opening bracket waits for the first visible character, as an emphasis
       marker does, so a leading inner space lands before it: `x<a> t</a>` gives
       `x [t](...)` */
    frame->marker = md_push_marker(ctx, "[");
    ctx->inline_only++;
    ctx->link = node;
}

static void md_leave_link(md_ctx *ctx, md_frame *frame) {
    const md_opts *opt = ctx->opt;
    ctx->inline_only--;
    ctx->link = NULL;
    if (frame->marker >= 0 && !ctx->markers[frame->marker].emitted) { /* GCOVR_EXCL_BR_LINE: -1 only on failure */
        md_before_visible(ctx); /* link text with nothing visible still gets its brackets */
    }
    md_pop_marker(ctx, frame->marker);
    const Py_UCS4 *href = frame->link.href;
    Py_ssize_t href_len = frame->link.href_len;
    const Py_UCS4 *title = frame->link.title;
    Py_ssize_t title_len = frame->link.title_len;
    if (title == NULL && opt->link_title) {
        title = href;
        title_len = href_len;
    }
    if (opt->link_style == TH_MD_LINK_REFERENCE) {
        Py_ssize_t number = md_add_reference(ctx, href, href_len, title, title_len);
        if (!ctx->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            sbuf_puts(&ctx->out, "][");
            md_put_decimal(&ctx->out, number);
            sbuf_putc(&ctx->out, ']');
        }
    } else {
        sbuf_puts(&ctx->out, "](");
        md_emit_url(ctx, frame->link.relative ? opt->base_url : "", href, href_len);
        if (title != NULL) {
            sbuf_puts(&ctx->out, " \"");
            md_emit_title(ctx, title, title_len);
            sbuf_putc(&ctx->out, '"');
        }
        sbuf_putc(&ctx->out, ')');
    }
    if (!opt->wrap_links) {
        ctx->no_wrap--;
    }
}

/* Text and attribute CRs are already escaped as &#13;, so a raw HTML run only has line feeds to encode. */
static void md_put_html_run(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t len, int encode_lf) {
    Py_ssize_t run = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (encode_lf && text[index] == '\n') {
            md_put_run(ctx, text + run, index - run);
            sbuf_puts(&ctx->out, "&#10;");
            run = index + 1;
        }
    }
    md_put_run(ctx, text + run, len - run);
}

/* Pass a node's outer HTML through on one line (the image/table/list escape hatch). */
static void md_emit_raw_html(md_ctx *ctx, th_node *node) {
    sbuf cell_open = {0};
    sbuf cell_close = {0};
    if (node->atom == TH_TAG_TABLE) {
        /* every cell takes the whole wrap, so past the depth where runs span blocks as raw HTML it holds only the
           outermost run of each kind, or the output grows with the nesting depth times the cell count */
        Py_ssize_t nested[MD_HTML_STRIKE + 1] = {0};
        for (Py_ssize_t index = ctx->marker_base; index < ctx->marker_count; index++) {
            uint8_t marker_html = ctx->markers[index].html;
            if (marker_html != MD_HTML_NONE && (nested[marker_html]++ == 0 || !ctx->deep_block_span)) {
                sbuf_puts(&cell_open, MD_HTML_TAGS[marker_html - 1][0]);
            }
        }
        for (Py_ssize_t index = ctx->marker_count; index > ctx->marker_base; index--) {
            uint8_t marker_html = ctx->markers[index - 1].html;
            if (marker_html != MD_HTML_NONE && (--nested[marker_html] == 0 || !ctx->deep_block_span)) {
                sbuf_puts(&cell_close, MD_HTML_TAGS[marker_html - 1][1]);
            }
        }
    }
    if (cell_open.failed || cell_close.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        ctx->out.failed = 1;                     /* GCOVR_EXCL_LINE: allocation failure */
        PyMem_Free(cell_open.data);              /* GCOVR_EXCL_LINE: allocation failure */
        PyMem_Free(cell_close.data);             /* GCOVR_EXCL_LINE: allocation failure */
        return;                                  /* GCOVR_EXCL_LINE: allocation failure */
    }
    const th_cell_wrap wrap = {cell_open.data, cell_open.len, cell_close.data, cell_close.len};
    Py_ssize_t len;
    Py_UCS4 *html =
        cell_open.len > 0
            ? th_node_markdown_table_html(ctx->tree, node, &wrap, &ctx->clone_budget, &len)
            : th_node_markdown_html(ctx->tree, node, ctx->in_cell ? TH_MARKDOWN_HTML_CELL : TH_MARKDOWN_HTML_BLOCK,
                                    &ctx->clone_budget, &len);
    PyMem_Free(cell_open.data);
    PyMem_Free(cell_close.data);
    if (html == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    md_put_html_run(ctx, html, len, 1);
    ctx->line_has_content = 1;
    PyMem_Free(html);
}

/* The length of a bare `<tbody>`/`</tbody>` tag at the head of text, or 0 for
   anything else. One with attributes is not bare and keeps its markup. */
static Py_ssize_t md_tbody_tag(const Py_UCS4 *text, Py_ssize_t len) {
    static const char *const tags[] = {"<tbody>", "</tbody>"};
    for (size_t which = 0; which < sizeof(tags) / sizeof(tags[0]); which++) {
        Py_ssize_t tag_len = (Py_ssize_t)strlen(tags[which]);
        if (len < tag_len) {
            continue;
        }
        Py_ssize_t index = 0;
        while (index < tag_len && text[index] == (Py_UCS4)(unsigned char)tags[which][index]) {
            index++;
        }
        if (index == tag_len) {
            return tag_len;
        }
    }
    return 0;
}

/* The length of the tag at the head of text, which starts at its `<`. Text never holds a bare `<` and an attribute
   value never holds a `>`, since the serializer writes them as `&lt;` and `&gt;`; inline HTML has no comment, raw text
   or processing instruction, which would. The one `<!` left is a CDATA section, and it holds no `>`: one that does is
   written as escaped text. */
static Py_ssize_t md_markup_length(const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 1; index < len; index++) { /* GCOVR_EXCL_BR_LINE */
        if (text[index] == '>') {
            return index + 1;
        }
    }
    return len; /* GCOVR_EXCL_LINE */
}

/* Write a block a pipe-table cell cannot hold -- a nested table or list -- as its
   source HTML, which is inline content and so legal in a cell (and renders as the
   real thing wherever embedded HTML is allowed). The `<tbody>` the parser inserts
   around every row group is dropped on the way out: it reparses back in and only
   widens the column. The text between the tags is still read as inline Markdown,
   unlike inside a raw HTML block, so it is escaped as running text is. The caller's
   whitespace collapse puts the rest on one line. */
static void md_emit_cell_html(md_ctx *ctx, th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *html = th_node_markdown_html(ctx->tree, node, TH_MARKDOWN_HTML_INLINE, &ctx->clone_budget, &len);
    if (html == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    md_before_visible(ctx);
    Py_ssize_t start = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = html[index];
        if (ch == '<') {
            Py_ssize_t tag = md_tbody_tag(&html[index], len - index);
            if (tag > 0) {
                md_put_html_run(ctx, &html[start], index - start, 0);
                index += tag - 1;
                start = index + 1;
            } else {
                Py_ssize_t markup = md_markup_length(&html[index], len - index);
                md_put_html_run(ctx, &html[start], index - start, 0);
                /* Cell whitespace collapse must not change an attribute value. */
                md_put_html_run(ctx, &html[index], markup, html[index + 1] != '!');
                index += markup - 1;
                start = index + 1;
            }
        } else if (ch < 0x80 && (MD_ASCII[ch] & ctx->escape_mask)) {
            md_put_html_run(ctx, &html[start], index - start, 0);
            sbuf_putc(&ctx->out, '\\');
            start = index;
        }
    }
    md_put_html_run(ctx, &html[start], len - start, 0);
    ctx->line_has_content = 1;
    PyMem_Free(html);
}

/* GFM pipe cells cannot hold nested block markup. */
static int md_is_cell_block(uint16_t atom) {
    return atom == TH_TAG_TABLE || atom == TH_TAG_UL || atom == TH_TAG_OL || atom == TH_TAG_MENU;
}

static int md_in_cell_quote(const th_node *node) {
    /* Called only while rendering a td/th descendant. */
    for (const th_node *ancestor = node; ancestor->atom != TH_TAG_TD && ancestor->atom != TH_TAG_TH;
         ancestor = ancestor->parent) {
        if (ancestor->atom == TH_TAG_BLOCKQUOTE) {
            return 1;
        }
    }
    return 0;
}

/* Collapsed block boundaries need spaces to keep adjacent words apart. */
static int md_is_cell_scaffold(uint16_t atom) {
    switch (atom) {
    case TH_TAG_TABLE:
    case TH_TAG_THEAD:
    case TH_TAG_TBODY:
    case TH_TAG_TFOOT:
    case TH_TAG_TR:
    case TH_TAG_TD:
    case TH_TAG_TH:
    case TH_TAG_UL:
    case TH_TAG_OL:
    case TH_TAG_MENU:
    case TH_TAG_LI:
        return 1;
    default:
        return 0;
    }
}

static Py_ssize_t md_list_number_attr(md_ctx *ctx, th_node *node, const char *name, Py_ssize_t fallback);

/* Flatten a block a pipe cell cannot hold into the cell's inline flow. A list numbers
   its own items; a table starts over unnumbered; any other scaffold keeps numbering
   for the list it sits in, which owner names (-1 for none). */
static void md_enter_cell_flat(md_ctx *ctx, th_node *node, Py_ssize_t owner) {
    md_frame *frame = md_push(ctx, node, MD_WALK_CELL_FLAT, MD_LEAVE_NONE);
    if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    frame->flat.owner = owner;
    if (node->atom == TH_TAG_TABLE) {
        frame->flat.owner = -1;
    } else if (node->atom == TH_TAG_UL || node->atom == TH_TAG_OL || node->atom == TH_TAG_MENU) {
        frame->flat.owner = ctx->frame_count - 1;
        frame->flat.ordered = node->atom == TH_TAG_OL;
        frame->flat.number = frame->flat.ordered ? md_list_number_attr(ctx, node, "start", 1) : 1;
    }
}

static void md_render_inline(md_ctx *ctx, th_node *node);
static int md_render_whitespace(md_ctx *ctx, th_node *node, int inherited);

static void md_cell_flat_child(md_ctx *ctx, Py_ssize_t owner, th_node *child) {
    if (child->type != TH_NODE_ELEMENT || child->ns != TH_NS_HTML || !md_is_cell_scaffold(child->atom)) {
        md_render_inline(ctx, child);
        return;
    }
    ctx->space_pending = 1;
    if (child->atom != TH_TAG_LI) {
        md_enter_cell_flat(ctx, child, owner);
        return;
    }
    /* Pipe rows cannot wrap, and every item has just opened a separator. */
    if (!ctx->drop_space) {
        sbuf_putc(&ctx->out, ' ');
    }
    ctx->space_pending = 0;
    ctx->drop_space = 0;
    ctx->pending_word = 0;
    md_emit_pending(ctx);
    if (owner >= 0 && ctx->frames[owner].flat.ordered) {
        md_frame *list = &ctx->frames[owner];
        list->flat.number = md_list_number_attr(ctx, child, "value", list->flat.number);
        md_put_decimal(&ctx->out, list->flat.number);
        sbuf_puts(&ctx->out, ". ");
        if (list->flat.number < PY_SSIZE_T_MAX) {
            list->flat.number++;
        }
    } else {
        if (ctx->pending != NULL) {
            sbuf_putc(&ctx->out, '\\');
        }
        sbuf_puts(&ctx->out, "* ");
    }
    ctx->line_has_content = 1;
    md_enter_cell_flat(ctx, child, -1);
}

/* Write an image's alt text, falling back to the configured default. Inside the
   `![...]` description a bracket, backslash or backtick is escaped the same way
   link text escapes them: an unescaped `]` would close the description early, and
   a backtick pairs with a later one into a code span, which CommonMark 6.3 reads
   before the image brackets. The description is inline content whose plain text
   becomes the alt (CommonMark 6.4), so an emphasis delimiter, or a `<` or `&` that
   would open raw HTML or a character reference, is escaped as prose escapes it, and
   a line break, which could open a block on the next line, is written as its numeric
   reference (6.5). The alt-only image mode writes no description: there the alt
   text is prose (md_emit_image). */
static void md_emit_alt(md_ctx *ctx, const Py_UCS4 *alt, Py_ssize_t alt_len) {
    if (alt != NULL) {
        for (Py_ssize_t index = 0; index < alt_len; index++) {
            Py_UCS4 ch = alt[index];
            uint8_t kind = ch < 0x80 ? MD_ASCII[ch] : 0;
            if (kind != 0) { /* a character in no class needs no escape */
                if (ch == '\n' || ch == '\r') {
                    sbuf_puts(&ctx->out, ch == '\n' ? "&#10;" : "&#13;");
                    continue;
                }
                if (ch == '[' || ch == ']' || ch == '\\' || ch == '`' || (kind & ctx->escape_mask) ||
                    ((kind & MD_CH_CONTEXT) && ctx->escape_prose && md_escape_by_context(alt, index, alt_len))) {
                    sbuf_putc(&ctx->out, '\\');
                }
            }
            md_put_literal(ctx, ch);
        }
    } else {
        md_puts8(&ctx->out, ctx->opt->default_image_alt);
    }
    ctx->line_has_content = 1;
}

static void md_emit_html_attribute(sbuf *out, const Py_UCS4 *value, Py_ssize_t len, int in_cell) {
    for (Py_ssize_t index = 0; index < len; index++) {
        switch (value[index]) {
        case '&':
            sbuf_puts(out, "&amp;");
            break;
        case '<':
            sbuf_puts(out, "&lt;");
            break;
        case '"':
            sbuf_puts(out, "&quot;");
            break;
        case '\n':
            sbuf_puts(out, "&#10;");
            break;
        case '\r':
            sbuf_puts(out, "&#13;");
            break;
        case '\t':
            sbuf_puts(out, "&#9;");
            break;
        case '\f':
            sbuf_puts(out, "&#12;");
            break;
        case ' ':
            if (in_cell) {
                sbuf_puts(out, "&#32;");
            } else {
                sbuf_putc(out, ' ');
            }
            break;
        default:
            sbuf_putc(out, value[index]);
            break;
        }
    }
}

static int md_attr_has_space_run(const Py_UCS4 *value, Py_ssize_t len) {
    for (Py_ssize_t index = 1; index < len; index++) {
        if (value[index - 1] == ' ' && value[index] == ' ') {
            return 1;
        }
    }
    return 0;
}

/* Alt text the prose writer would copy unchanged is written as one run. A digit only
   matters as the first character, where it may start a line-numbered list item. */
static int md_alt_is_plain(const md_ctx *ctx, const Py_UCS4 *alt, Py_ssize_t alt_len) {
    if (is_ascii_digit(alt[0]) || ctx->opt->transliterate || ctx->opt->wrap_width > 0 || ctx->amp_open ||
        ctx->line_number_start > 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < alt_len; index++) {
        if (alt[index] >= 0x80 || MD_ASCII[alt[index]] != 0) {
            return 0;
        }
    }
    return 1;
}

static void md_emit_image(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    if (opt->image_mode == TH_MD_IMAGE_IGNORE) {
        return;
    }
    Py_ssize_t alt_len;
    const Py_UCS4 *alt = md_attr(ctx->tree, node, "alt", &alt_len);
    if (opt->image_mode == TH_MD_IMAGE_ALT && (alt != NULL ? alt_len == 0 : *opt->default_image_alt == '\0')) {
        return; /* no alt text to write, so nothing visible follows: a pending run must not open for it */
    }
    if (opt->image_mode == TH_MD_IMAGE_ALT && alt != NULL) {
        /* with no brackets around it the alt text joins the prose, and a reader parses it as it does any text */
        if (md_alt_is_plain(ctx, alt, alt_len)) {
            md_before_visible(ctx);
            md_put_run(ctx, alt, alt_len);
            ctx->line_has_content = 1;
            ctx->clone_budget += alt_len;
            ctx->tags_left += alt_len;
        } else {
            md_emit_text(ctx, node, alt, alt_len);
        }
        return;
    }
    md_before_visible(ctx);
    if (opt->image_mode == TH_MD_IMAGE_HTML) {
        md_emit_raw_html(ctx, node);
        return;
    }
    if (opt->image_mode == TH_MD_IMAGE_ALT) {
        md_emit_alt(ctx, NULL, 0); /* no alt text: the configured default */
        return;
    }
    Py_ssize_t src_len;
    const Py_UCS4 *src = md_attr(ctx->tree, node, "src", &src_len);
    Py_ssize_t title_len;
    const Py_UCS4 *title = md_attr(ctx->tree, node, "title", &title_len);
    int use_html = ctx->in_cell && (md_attr_has_space_run(alt, alt_len) || md_attr_has_space_run(src, src_len) ||
                                    md_attr_has_space_run(title, title_len));
    if (src != NULL) {
        for (Py_ssize_t index = 0; index < src_len; index++) {
            if (src[index] == '\n' || src[index] == '\r' || src[index] == '\t' || src[index] == '\f') {
                use_html = 1;
                break;
            }
        }
    }
    if (use_html) {
        sbuf_puts(&ctx->out, "<img src=\"");
        if (src != NULL) {
            md_emit_html_attribute(&ctx->out, src, src_len, ctx->in_cell);
        }
        sbuf_puts(&ctx->out, "\" alt=\"");
        if (alt != NULL) {
            md_emit_html_attribute(&ctx->out, alt, alt_len, ctx->in_cell);
        }
        if (title != NULL) {
            sbuf_puts(&ctx->out, "\" title=\"");
            md_emit_html_attribute(&ctx->out, title, title_len, ctx->in_cell);
        }
        sbuf_puts(&ctx->out, "\">");
        ctx->line_has_content = 1;
        return;
    }
    sbuf_puts(&ctx->out, "![");
    md_emit_alt(ctx, alt, alt_len);
    sbuf_puts(&ctx->out, "](");
    if (src != NULL) {
        int relative = *opt->base_url != '\0' && !md_href_absolute(src, src_len) && !md_href_internal(src);
        md_emit_url(ctx, relative ? opt->base_url : "", src, src_len);
    } else {
        sbuf_puts(&ctx->out, "<>"); /* a missing src is an empty destination */
    }
    if (title != NULL) {
        sbuf_puts(&ctx->out, " \"");
        md_emit_title(ctx, title, title_len);
        sbuf_putc(&ctx->out, '"');
    }
    sbuf_putc(&ctx->out, ')');
    ctx->line_has_content = 1;
}

static int md_is_code_span(uint16_t atom) {
    return atom == TH_TAG_CODE || atom == TH_TAG_KBD || atom == TH_TAG_SAMP;
}

/* Whether a code span writes anything for an element: the text md_emit_code_span would
   collect, even spaces alone. */
static int md_code_writes_text(md_ctx *ctx, th_node *node) {
    sbuf content = {0};
    md_collect_code_text(ctx->tree, node, &content, ' ');
    int writes = content.len > 0;
    ctx->out.failed |= content.failed; /* a failed probe reads as empty, which would pick the wrong lead */
    PyMem_Free(content.data);
    return writes;
}

/* Whether an element renders as a Markdown link, whose text cannot hold a block: the
   cases md_enter_link leaves as a placeholder are not. Inside link text every other
   link is a placeholder too, which the callers decide from md_ctx.link. */
static int md_is_link(md_ctx *ctx, th_node *node) {
    if (node->ns != TH_NS_HTML || node->atom != TH_TAG_A || ctx->opt->ignore_links ||
        md_tag_filtered(ctx->opt, TH_TAG_A) || th_node_attr_find(ctx->tree, node, "href", 4) < 0) {
        return 0;
    }
    Py_ssize_t href_len;
    const Py_UCS4 *href = md_attr(ctx->tree, node, "href", &href_len);
    return !(ctx->opt->skip_internal_links && href != NULL && md_href_internal(href));
}

/* Whether a <br> sits at the end of its block, where a hard break does nothing
   (CommonMark 6.7): no visible content follows it before a block boundary. The scan
   walks forward in document order: non-space text, an image, a quote or a code span
   with text is visible content, and a block ends the run; another break, or an inline
   element holding none of these (an empty link draws nothing either), is looked
   through, as is nothing when a converter could write anything. Climbing out of an
   inline wrapper reaches the block that encloses the break. A tree need not have one:
   a fragment, a shadow root and a detached inline element have no block above their
   content, and an XML document has none at all, its elements carrying no HTML atom.
   There the climb runs past the parentless root, where the output ends and the run
   with it.

   The scan starts after node, in link (NULL outside link text). Given a target, it also
   stops, with -1, when it starts at target or steps over it in target_link: from there
   on it is the scan that starts after target, so the two end alike. */
static inline int md_br_scan(md_ctx *ctx, th_node *node, th_node *link, th_node *target, th_node *target_link,
                             Py_ssize_t *steps) {
    th_node *cursor = node;
    th_node *next = node->next_sibling;
    for (;;) {
        TH_WORK_STEP();
        (*steps)++;
        if (cursor == target && link == target_link && next == cursor->next_sibling) {
            return -1; /* at target or stepped over it, not into it */
        }
        while (next == NULL) {
            cursor = cursor->parent;
            if (cursor == NULL ||
                (link == NULL && is_md_block(cursor->ns == TH_NS_HTML ? cursor->atom : TH_TAG_UNKNOWN))) {
                return 1;
            }
            if (cursor == link) {
                link = NULL;
            }
            next = cursor->next_sibling;
        }
        cursor = next;
        next = cursor->next_sibling;
        if (cursor->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, cursor);
            if (text == NULL && cursor->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                return 0;                               /* GCOVR_EXCL_LINE: the walk reports the failure */
            }
            for (Py_ssize_t index = 0; index < cursor->text_len; index++) {
                if (!is_space(text[index])) {
                    return 0;
                }
            }
        } else if (cursor->type == TH_NODE_ELEMENT && !is_md_skipped(cursor)) {
            uint16_t atom = cursor->ns == TH_NS_HTML ? cursor->atom : TH_TAG_UNKNOWN;
            if (link == NULL && is_md_block(atom)) {
                return 1;
            }
            if (ctx->opt->converters != NULL || atom == TH_TAG_IMG || atom == TH_TAG_Q) {
                return 0;
            }
            if (md_is_code_span(atom)) {
                if (md_code_writes_text(ctx, cursor)) {
                    return 0; /* a code span writes its text, blocks and all */
                }
            } else if (cursor->first_child != NULL) {
                link = link == NULL && md_is_link(ctx, cursor) ? cursor : link;
                next = cursor->first_child;
            }
        }
    }
}

/* md_br_scan with a memo, so that a run of breaks costs its length and not the square of
   it. The scan for one break steps over the breaks after it on the way to whatever
   decides them all, so going over the last scan again from where it started meets a
   later break of the run at once and hands it the same answer, however the run is laid
   out and whichever of its breaks are asked about. Only a node the last scan never
   stepped over is scanned for afresh. A scan that stays short is cheaper than its
   replay, so the memo is kept only after a scan walks past MD_BR_MEMO_STEPS nodes. */
#define MD_BR_MEMO_STEPS 8

static int md_br_trailing(md_ctx *ctx, th_node *node) {
    /* link text flattens its blocks onto its line (md_render_inline_tag), so inside a
       link a block ends nothing; link holds the link the scan is in */
    th_node *link = ctx->inline_only > 0 ? ctx->link : NULL;
    Py_ssize_t steps = 0;
    if (ctx->opt->converters != NULL) {
        /* a converter can edit the tree, so no node is kept across one; with converters
           a scan ends at the next element anyway */
        return md_br_scan(ctx, node, link, NULL, NULL, &steps);
    }
    if (ctx->br_from != NULL && md_br_scan(ctx, ctx->br_from, ctx->br_link, node, link, &steps) < 0) {
        ctx->br_from = node;
        ctx->br_link = link;
        return ctx->br_trailing;
    }
    steps = 0;
    ctx->br_trailing = md_br_scan(ctx, node, link, NULL, NULL, &steps);
    ctx->br_from = steps > MD_BR_MEMO_STEPS ? node : NULL;
    ctx->br_link = link;
    return ctx->br_trailing;
}

/* Whether the nearest block around a node is a heading; a tree with no block above the
   node (see md_br_scan) has none. */
static int md_in_heading_element(th_node *node) {
    th_node *block = node->parent;
    while (block != NULL && !is_md_block(block->ns == TH_NS_HTML ? block->atom : TH_TAG_UNKNOWN)) {
        block = block->parent;
    }
    return block != NULL && block->atom >= TH_TAG_H1 && block->atom <= TH_TAG_H6;
}

/* Render an element (or content node) by its tag, the common path shared by the
   plain walk and the google_doc CSS wrapper. Text is handled by the caller. */
static void md_render_inline_tag(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    uint16_t atom = node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN;
    switch (atom) {
    case TH_TAG_STRONG:
    case TH_TAG_B:
        md_enter_emphasis(ctx, node, opt->keep_emphasis ? opt->strong : "", MD_HTML_STRONG);
        return;
    case TH_TAG_EM:
    case TH_TAG_I:
        md_enter_emphasis(ctx, node, opt->keep_emphasis ? opt->emphasis : "", MD_HTML_EM);
        return;
    case TH_TAG_DEL:
    case TH_TAG_S:
    case TH_TAG_STRIKE:
        if (!opt->keep_strikethrough) {
            return; /* hide struck-through content entirely */
        }
        /* the fallback keeps the element's own tag so the round-trip reaches the same element */
        md_enter_emphasis(ctx, node, opt->keep_emphasis ? opt->strikethrough : "",
                          atom == TH_TAG_DEL ? MD_HTML_DEL : (atom == TH_TAG_S ? MD_HTML_S : MD_HTML_STRIKE));
        return;
    case TH_TAG_SUB:
        md_enter_wrap(ctx, node, opt->sub);
        return;
    case TH_TAG_SUP:
        md_enter_wrap(ctx, node, opt->sup);
        return;
    case TH_TAG_Q:
        md_before_visible(ctx);
        md_puts8(&ctx->out, opt->quote_open);
        md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_QUOTE);
        return;
    case TH_TAG_CODE:
    case TH_TAG_KBD:
    case TH_TAG_SAMP:
        md_emit_code_span(ctx, node, 0);
        return;
    case TH_TAG_A:
        md_enter_link(ctx, node);
        return;
    case TH_TAG_IMG:
        md_emit_image(ctx, node);
        return;
    case TH_TAG_BR:
        if (ctx->in_heading || ((ctx->inline_only || ctx->in_cell) && md_in_heading_element(node))) {
            /* a hard break ends the line, but an ATX heading is one line and a
               setext heading joins its lines, so the break reads as a space; a heading
               flattened into link text or a cell keeps that reading */
            ctx->space_pending = 1;
            return;
        }
        if (ctx->in_cell) {
            /* a row is one line, so the markdown spellings of a break cannot be used
               here: their marker would survive the collapse to a row and the break
               itself would not. HTML's own break does work inside a cell. */
            if (opt->cell_blocks == TH_MD_CELL_HTML) {
                sbuf_puts(&ctx->out, "<br>");
                ctx->line_has_content = 1;
            } else {
                ctx->space_pending = 1;
            }
            return;
        }
        if (md_br_trailing(ctx, node)) {
            /* a hard break at the end of a block does nothing (CommonMark 6.7); emitting
               it would leave a line of spaces that reads as a blank line */
            return;
        }
        if (ctx->block_ended) {
            /* the break opens the block after an inner one: a tag written here would sit on that block's last
               line, and behind a closing code fence it keeps the fence from closing */
            md_write_break(ctx);
            return;
        }
        if (ctx->raw_emphasis && md_is_emphasis_node(node->parent)) {
            sbuf_puts(&ctx->out, "<br>");
            ctx->line_has_content = 1;
            return;
        }
        if (md_repeated_br_in_emphasis(ctx, node)) {
            sbuf_puts(&ctx->out, "<br>");
            ctx->line_has_content = 1;
            return;
        }
        for (Py_ssize_t index = ctx->marker_base; index < ctx->marker_count; index++) {
            md_marker *marker = &ctx->markers[index];
            if (!marker->emitted || marker->node == NULL) {
                continue;
            }
            /* a <q> with an empty quote_open opens the run and writes nothing, so the run may still end the output */
            if (marker->open_at + marker->open_len < ctx->out.len &&
                md_edge(ctx->out.data[marker->open_at + marker->open_len]) == MD_EDGE_SPACE) {
                sbuf_puts(&ctx->out, "<br>");
                ctx->line_has_content = 1;
                return;
            }
        }
        md_write_break(ctx);
        return;
    case TH_TAG_WBR:
        return;
    default:
        break;
    }
    if (is_md_skipped(node)) {
        return;
    }
    if (is_md_block(atom)) {
        if (!ctx->inline_only && !ctx->in_heading && !ctx->in_cell) {
            /* the content after the block belongs to a new block, so note where it ends */
            md_push(ctx, node, MD_WALK_NONE, MD_LEAVE_BLOCK_END);
            md_render_block(ctx, node);
            return;
        }
        if (ctx->in_cell && !ctx->inline_only && md_is_cell_block(atom)) {
            md_render_block(ctx, node); /* a nested table or list keeps its HTML or flattens item by item */
            return;
        }
        if (ctx->in_cell && opt->cell_blocks == TH_MD_CELL_TEXT && md_is_cell_block(atom)) {
            ctx->space_pending = 1;
            md_enter_cell_flat(ctx, node, -1);
            return;
        }
        /* inside link text, a heading or a table cell a block cannot open its own line (a
           blank line would split the CommonMark link, a heading is one line, and a GFM
           cell holds inline content, 4.10), so it flattens to inline; its boundary still
           reads as a space so adjacent words never fuse */
        if (ctx->in_cell && !ctx->inline_only && md_in_cell_quote(node)) {
            /* A quote has its own flow of inline runs inside a flattened cell. */
            md_suspend_markers(ctx);
        }
        ctx->space_pending = 1;
        if (atom == TH_TAG_PRE) {
            md_emit_code_span(ctx, node, 1); /* a code span is the inline form of a code block (CommonMark 6.1) */
            ctx->space_pending = 1;
            return;
        }
        md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_SPACE);
        return;
    }
    if (atom == TH_TAG_TR || atom == TH_TAG_TD || atom == TH_TAG_TH) {
        /* only a table flattened into a line reaches its rows here, and its cells must not fuse */
        ctx->space_pending = 1;
        md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_SPACE);
        return;
    }
    md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_NONE);
}

/* Render an element in google_doc mode: turn the inline-CSS styling a Google Docs
   export carries into Markdown. A font-weight/font-style/fixed-width font opens
   the matching marker (only on the transition the ancestor did not already set,
   so nested spans do not double the markup), and a line-through drops the text
   when hide_strikethrough is on. The markers are deferred like md_enter_wrap so a
   leading inner space lands outside and an empty span leaves nothing behind. */
static void md_render_google(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    Py_ssize_t style_len;
    const Py_UCS4 *style = md_attr(ctx->tree, node, "style", &style_len);
    const Py_UCS4 *value;
    Py_ssize_t value_len;
    if (opt->hide_strikethrough && style != NULL &&
        md_css_prop(style, style_len, "text-decoration", &value, &value_len) &&
        md_ucs4_ieq(value, value_len, "line-through")) {
        return; /* the struck-through subtree is hidden entirely */
    }
    int outer_bold = ctx->g_bold, outer_italic = ctx->g_italic;
    int bold = outer_bold, italic = outer_italic, fixed = 0;
    if (style != NULL) {
        if (md_css_prop(style, style_len, "font-weight", &value, &value_len)) {
            bold = md_css_bold(value, value_len);
        }
        if (md_css_prop(style, style_len, "font-style", &value, &value_len)) {
            italic = md_ucs4_ieq(value, value_len, "italic");
        }
        if (md_css_prop(style, style_len, "font-family", &value, &value_len)) {
            fixed = md_css_fixed(value, value_len);
        }
    }
    md_frame *frame = md_push(ctx, NULL, MD_WALK_NONE, MD_LEAVE_GOOGLE);
    if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    frame->google.outer_bold = outer_bold;
    frame->google.outer_italic = outer_italic;
    frame->google.italic_marker = -1;
    if (italic && !outer_italic) {
        frame->google.italic_marker = md_push_marker(ctx, opt->emphasis);
    }
    if (bold && !outer_bold) {
        frame->marker = md_push_marker(ctx, opt->strong);
    }
    ctx->g_bold = bold;
    ctx->g_italic = italic;
    if (fixed) {
        /* a fixed-width run renders as an inline code span; its subtree is consumed
           as text, so a nested fixed span is never reached and needs no dedup */
        md_emit_code_span(ctx, node, 0);
    } else {
        md_render_inline_tag(ctx, node);
    }
}

static void md_leave_google(md_ctx *ctx, md_frame *frame) {
    ctx->g_bold = frame->google.outer_bold;
    ctx->g_italic = frame->google.outer_italic;
    const char *close = md_pop_marker(ctx, frame->marker);
    if (close != NULL) {
        md_put_close(ctx, close);
    }
    close = md_pop_marker(ctx, frame->google.italic_marker);
    if (close != NULL) {
        md_put_close(ctx, close);
    }
}

/* Render one node encountered in an inline run. A block element nested in inline
   flow (rare, e.g. a <div> inside a <span>) is laid out as its own block. */
static void md_render_inline(md_ctx *ctx, th_node *node) {
    if (node->type == TH_NODE_TEXT) {
        md_emit_text(ctx, node, need_text(ctx->tree, node), node->text_len);
        return;
    }
    if (node->type != TH_NODE_ELEMENT) {
        return;
    }
    if (md_apply_converter(ctx, node)) {
        return;
    }
    if (node->attr_count > 0 && md_render_whitespace(ctx, node, TH_WS_NORMAL)) {
        return;
    }
    uint16_t atom = node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN;
    if (md_tag_filtered(ctx->opt, atom) && !is_md_skipped(node)) {
        /* drop this tag's markup but keep its inline content (a skipped tag, e.g.
           <script>, still vanishes whole, so it falls through to the no-op below) */
        md_push(ctx, node, MD_WALK_INLINE, MD_LEAVE_NONE);
        return;
    }
    if (ctx->opt->google_doc) {
        /* a content node carries no style, so it passes through unstyled */
        md_render_google(ctx, node);
        return;
    }
    md_render_inline_tag(ctx, node);
}

/* A block that lays out as a plain paragraph run, as opposed to a list, blockquote,
   table, heading, rule or pre that frames its own lines. Its first paragraph rides
   the list marker, and a second such block makes the item loose. <p> is the explicit
   paragraph; <div> is the generic flow container browsers render the same way. */
static int md_is_paragraph_block(uint16_t atom) {
    return atom == TH_TAG_P || atom == TH_TAG_DIV;
}

/* What an element's content writes first, which decides whether it can ride a list
   marker's line, whether a list it starts can interrupt a paragraph, and whether an
   inline wrapper needs a run line of its own. */
enum md_lead {
    MD_LEAD_NOTHING = -1,
    MD_LEAD_BLOCK = 0,    /* a self-framing block */
    MD_LEAD_INLINE = 1,   /* inline content */
    MD_LEAD_IN_BLOCK = 2, /* a paragraph's content or an ATX heading, which opens its own line */
};

static TH_NOINLINE int md_task_checkbox(md_ctx *ctx, th_node *node) {
    if (ctx->opt->converters != NULL || ctx->opt->tag_filter != TH_MD_FILTER_NONE) {
        return 0;
    }
    Py_ssize_t length;
    const Py_UCS4 *type = md_attr(ctx->tree, node, "type", &length);
    if (type == NULL || !md_ucs4_ieq(type, length, "checkbox")) {
        return 0;
    }
    return th_node_attr_find(ctx->tree, node, "checked", 7) >= 0 ? 2 : 1;
}

/* A loose item needs its first block on the marker line even when a transparent
   container wraps it; otherwise CommonMark reads that block as code. The scan descends
   into such containers, into inline elements and into paragraphs, and climbs back out of
   one that leads with nothing: an inline element that writes nothing (an empty <em>, a
   link placeholder) does not count as content. A code span writes all its text, spaces
   and blocks included, on one line, so it leads inline once it holds any. */
static int md_leads_with_inline(md_ctx *ctx, th_node *root, int *task, int for_list_interrupt) {
    th_node *parent = root;
    th_node *child = root->first_child;
    th_node *paragraph = NULL; /* the outermost paragraph block the scan is inside */
    for (;;) {
        while (child == NULL) {
            if (parent == root) {
                return MD_LEAD_NOTHING;
            }
            paragraph = parent == paragraph ? NULL : paragraph;
            child = parent->next_sibling;
            parent = parent->parent;
        }
        if (child->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            if (text == NULL && child->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                return MD_LEAD_NOTHING;                /* GCOVR_EXCL_LINE: the walk reports the failure */
            }
            for (Py_ssize_t index = 0; index < child->text_len; index++) {
                if (!is_space(text[index])) {
                    /* leading visible text rides on the bullet line */
                    return paragraph != NULL ? MD_LEAD_IN_BLOCK : MD_LEAD_INLINE;
                }
            }
            child = child->next_sibling; /* whitespace-only: keep looking past it */
            continue;
        }
        if (child->type != TH_NODE_ELEMENT || is_md_skipped(child)) {
            child = child->next_sibling;
            continue;
        }
        uint16_t atom = child->ns == TH_NS_HTML ? child->atom : TH_TAG_UNKNOWN;
        if (atom == TH_TAG_INPUT && task != NULL && *task == 0 && (parent == root || parent == paragraph)) {
            int state = md_task_checkbox(ctx, child);
            *task = state == 0 ? -1 : state;
            child = child->next_sibling;
            continue;
        }
        if (md_is_code_span(atom)) {
            if (md_code_writes_text(ctx, child)) {
                return paragraph != NULL ? MD_LEAD_IN_BLOCK : MD_LEAD_INLINE;
            }
            child = child->next_sibling;
            continue;
        }
        /* A trailing break writes nothing, so its empty item cannot interrupt a paragraph. */
        if (atom == TH_TAG_IMG || (atom == TH_TAG_BR && (!for_list_interrupt || !md_br_trailing(ctx, child))) ||
            atom == TH_TAG_Q || (atom == TH_TAG_A && th_node_attr_find(ctx->tree, child, "href", 4) >= 0)) {
            return paragraph != NULL ? MD_LEAD_IN_BLOCK : MD_LEAD_INLINE; /* it writes markup even with no text */
        }
        if (atom >= TH_TAG_H1 && atom <= TH_TAG_H6) {
            return ctx->opt->heading_style != TH_MD_HEADING_SETEXT || atom > TH_TAG_H2 ? MD_LEAD_IN_BLOCK
                                                                                       : MD_LEAD_BLOCK;
        }
        switch (atom) {
        case TH_TAG_BLOCKQUOTE:
        case TH_TAG_PRE:
        case TH_TAG_HR:
        case TH_TAG_UL:
        case TH_TAG_OL:
        case TH_TAG_MENU:
        case TH_TAG_TABLE:
            return MD_LEAD_BLOCK;
        default:
            if (paragraph == NULL && md_is_paragraph_block(atom)) {
                paragraph = child;
            }
            parent = child;
            child = child->first_child;
        }
    }
}

/* What an inline element writes first: an empty one writes something only when it is a
   break, an image, a quote or a link, and a code span leads inline once it holds text. */
static int md_element_lead(md_ctx *ctx, th_node *node, uint16_t atom) {
    if (atom == TH_TAG_A && th_node_attr_find(ctx->tree, node, "href", 4) >= 0) {
        return MD_LEAD_INLINE; /* the brackets are written even around nothing */
    }
    if (atom == TH_TAG_Q && ctx->opt->quote_open[0] != '\0' && !md_tag_filtered(ctx->opt, atom)) {
        return MD_LEAD_INLINE; /* the opening mark is written before whatever the quote holds, a block included */
    }
    if (md_is_code_span(atom)) {
        return md_code_writes_text(ctx, node) ? MD_LEAD_INLINE : MD_LEAD_NOTHING;
    }
    if (node->first_child == NULL) {
        return atom == TH_TAG_BR || atom == TH_TAG_IMG || atom == TH_TAG_Q ? MD_LEAD_INLINE : MD_LEAD_NOTHING;
    }
    return md_leads_with_inline(ctx, node, NULL, 0);
}

/* What an inline child at a run start leads with, to decide whether it opens a run line:
   an inline element that writes nothing (an empty emphasis, a link placeholder) opens no
   run, and an inline wrapper whose first content sits in a block (a paragraph, a nested
   list, quote or table) has no inline run of its own; opening one would leave an empty
   line that reads as a blank line and splits the surrounding item, so the inner block
   opens its own line instead. Kept out of line so md_block_child keeps its shape on the
   common path. */
static TH_NOINLINE int md_run_lead(md_ctx *ctx, th_node *child, uint16_t atom) {
    if (ctx->opt->converters != NULL) {
        return MD_LEAD_INLINE;
    }
    return md_element_lead(ctx, child, atom);
}

/* Whether a list item lays out as more than one paragraph, so it renders as a
   CommonMark loose item (a blank line between its blocks): two runs of visible inline
   content split by a paragraph block, also when a div, a link or an emphasis wraps
   them, since a tight item writes its paragraphs on consecutive lines that read as one.
   Content after a nested list, quote or table needs the blank line too, or it
   continues that block lazily (CommonMark 5.1, 5.2) or as a row (GFM 4.10). A
   heading, rule or code block ends on its own line and does not force looseness, so a
   tight item can still carry one, and the scan never enters a self-framing block. */
static int md_item_is_loose(md_ctx *ctx, th_node *node) {
    if (node->first_child == NULL ||
        (node->first_child == node->last_child && node->first_child->type == TH_NODE_TEXT)) {
        return 0;
    }
    int units = 0;
    int in_run = 0;
    int absorbs = 0; /* the last block reads a following line as its own */
    th_node *parent = node;
    th_node *child = node->first_child;
    for (;;) {
        while (child == NULL) {
            if (parent == node) {
                return 0;
            }
            if (is_md_block(parent->ns == TH_NS_HTML ? parent->atom : TH_TAG_UNKNOWN)) {
                in_run = 0;
            }
            child = parent->next_sibling;
            parent = parent->parent;
        }
        int visible = 0;
        if (child->type == TH_NODE_TEXT) {
            /* inside a run more text only extends it, so only a run's first text is read */
            if (!in_run) {
                const Py_UCS4 *text = need_text(ctx->tree, child);
                if (text == NULL && child->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                    return 0;                              /* GCOVR_EXCL_LINE: the walk reports the failure */
                }
                for (Py_ssize_t index = 0; index < child->text_len && !visible; index++) {
                    visible = !is_space(text[index]);
                }
            }
        } else if (child->type == TH_NODE_ELEMENT && !is_md_skipped(child)) {
            uint16_t atom = child->ns == TH_NS_HTML ? child->atom : TH_TAG_UNKNOWN;
            if (is_md_block(atom)) {
                in_run = 0;
                switch (atom) {
                case TH_TAG_UL:
                case TH_TAG_OL:
                case TH_TAG_MENU:
                case TH_TAG_BLOCKQUOTE:
                case TH_TAG_TABLE:
                    absorbs = 1;
                    child = child->next_sibling;
                    continue;
                case TH_TAG_PRE:
                case TH_TAG_HR:
                case TH_TAG_H1:
                case TH_TAG_H2:
                case TH_TAG_H3:
                case TH_TAG_H4:
                case TH_TAG_H5:
                case TH_TAG_H6:
                    absorbs = 0;
                    child = child->next_sibling;
                    continue;
                default:
                    break;
                }
            }
            /* an image, and a link even with no text, write markup of their own */
            visible = atom == TH_TAG_IMG || (atom == TH_TAG_A && th_node_attr_find(ctx->tree, child, "href", 4) >= 0);
            if (!visible && child->first_child != NULL) {
                parent = child;
                child = child->first_child;
                continue;
            }
        }
        if (visible && !in_run) {
            if (++units > 1 || absorbs) {
                return 1;
            }
            in_run = 1;
        }
        child = child->next_sibling;
    }
}

/* Lay out one child of a block container: a block child opens its own block, and an
   inline child joins the paragraph-like run *in_run tracks, opening it on a fresh line
   unless it is whitespace alone. in_run may sit in the frame stack, so it is settled
   before anything is pushed. */
static inline void md_block_child(md_ctx *ctx, th_node *child, int *in_run) {
    uint16_t atom = TH_TAG_UNKNOWN;
    int block = 0;
    if (child->type == TH_NODE_ELEMENT) {
        atom = child->ns == TH_NS_HTML ? child->atom : TH_TAG_UNKNOWN;
        if (is_md_skipped(child)) {
            return;
        }
        block = is_md_block(atom);
    } else if (child->type == TH_NODE_CONTENT) {
        md_push(ctx, child, MD_WALK_BLOCK, MD_LEAVE_NONE);
        return;
    } else if (child->type != TH_NODE_TEXT) {
        return;
    }
    if (block) {
        *in_run = 0;
        md_render_block(ctx, child);
        return;
    }
    if (!*in_run) {
        if (atom == TH_TAG_BR && md_br_trailing(ctx, child)) {
            return; /* nothing visible follows, so the break does nothing (CommonMark 6.7) */
        }
        int only_ws = child->type == TH_NODE_TEXT;
        if (only_ws) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            if (text == NULL && child->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                ctx->out.failed = 1;                   /* GCOVR_EXCL_LINE: allocation failure */
                return;                                /* GCOVR_EXCL_LINE: allocation failure */
            }
            for (Py_ssize_t index = 0; index < child->text_len; index++) {
                if (!is_space(text[index])) {
                    only_ws = 0;
                    break;
                }
            }
        }
        if (only_ws) {
            return;
        }
        /* the element test keeps the scan off the common run that starts with text */
        if (child->type == TH_NODE_ELEMENT &&
            (child->first_child == NULL || child->first_child->type == TH_NODE_ELEMENT)) {
            int lead = md_run_lead(ctx, child, atom);
            if (lead != MD_LEAD_INLINE) {
                /* inline content after a wrapper that wrote anything continues the line it
                   ended on, or the block a closing inner block leaves owed */
                *in_run = lead != MD_LEAD_NOTHING;
                md_render_inline(ctx, child);
                return;
            }
        }
        md_block_line(ctx, ctx->tight ? 0 : 1);
        *in_run = 1;
    }
    md_render_inline(ctx, child);
}

/* Run the per-tag converter hook for an element: its children render into a
   standalone Markdown string for the converter to wrap. They render on the live ctx
   with a swapped-in buffer and a reset layout state, so the inner content carries
   no outer prefix, but the reference-link accumulator stays shared so a link inside
   the subtree still registers globally. Returns 1 when the element is handled (its
   built-in rendering replaced, or the walk aborted by an error that leaves
   ctx->failed and a Python exception set), 0 when no converter applies and the
   caller should render the element normally. */
static int md_enter_converter(md_ctx *ctx, th_node *node) {
    PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->text, node->text_len);
    if (tag == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *converter = PyDict_GetItemWithError(ctx->opt->converters, tag); /* borrowed */
    if (converter == NULL) {
        Py_DECREF(tag);
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a str key never raises on lookup */
            ctx->failed = 1;    /* GCOVR_EXCL_LINE: unreachable hash-error path */
            return 1;           /* GCOVR_EXCL_LINE: unreachable hash-error path */
        }
        return 0;
    }
    md_converting *saved = PyMem_Malloc(sizeof(md_converting));
    md_frame *frame = NULL;
    if (saved != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        frame = md_push(ctx, node, MD_WALK_BLOCK, MD_LEAVE_CONVERT);
    }
    if (frame == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(saved); /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(tag);    /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->failed = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        return 1;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    frame->convert = saved;
    *saved = (md_converting){
        .tag = tag,
        .converter = converter,
        .out = ctx->out,
        .prefix = ctx->prefix,
        .marker_base = ctx->marker_base,
        .line_start = ctx->line_start,
        .line_checked = ctx->line_checked,
        .started = ctx->started,
        .line_has_content = ctx->line_has_content,
        .line_number_start = ctx->line_number_start,
        .line_number_checked = ctx->line_number_checked,
        .break_start = ctx->break_start,
        .break_end = ctx->break_end,
        .space_pending = ctx->space_pending,
        .drop_space = ctx->drop_space,
        .pending_loose = ctx->pending_loose,
        .suppress_break = ctx->suppress_break,
        .tight = ctx->tight,
        .list_depth = ctx->list_depth,
        .g_bold = ctx->g_bold,
        .g_italic = ctx->g_italic,
        .inline_only = ctx->inline_only,
        .deep_block_span = ctx->deep_block_span,
        .list_end_marker = ctx->list_end_marker,
        .table_end = ctx->table_end,
        .converting = ctx->converting,
    };
    ctx->out = (sbuf){0};
    ctx->prefix = (sbuf){0};
    ctx->marker_base = ctx->marker_count;
    ctx->pending = NULL;
    ctx->started = 0;
    ctx->line_has_content = 0;
    ctx->line_number_start = 0;
    ctx->break_end = -1;
    ctx->line_start = 0;
    ctx->line_checked = 0;
    ctx->space_pending = 0;
    ctx->drop_space = 1;
    ctx->pending_loose = 0;
    ctx->suppress_break = 0;
    ctx->tight = 0;
    ctx->list_depth = 0;
    ctx->g_bold = 0;
    ctx->g_italic = 0;
    ctx->inline_only = 0;
    ctx->deep_block_span = 0; /* it counts the markers above marker_base, and a run closing in here must not clear it */
    ctx->list_end_marker = 0;
    ctx->table_end = -1;
    ctx->converting = 1;
    return 1;
}

/* Whether the content an element renders starts (from_start) or ends with
   whitespace, judged by what comes first or last in document order: a text node's
   edge character, a line break (which reads as whitespace), or an image or form
   control (visible, so no). Any other empty element renders nothing and is
   looked past. */
static int md_edge_space(md_ctx *ctx, th_node *root, int from_start) {
    th_node *node = from_start ? root->first_child : root->last_child;
    while (node != NULL) {
        if (node->type == TH_NODE_TEXT && node->text_len > 0) {
            const Py_UCS4 *text = need_text(ctx->tree, node);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                return 0;       /* GCOVR_EXCL_LINE: the walk reports the allocation failure */
            }
            return is_space(text[from_start ? 0 : node->text_len - 1]);
        }
        if (node->type == TH_NODE_ELEMENT && !is_md_skipped(node)) {
            uint16_t atom = node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN;
            if (atom == TH_TAG_BR) {
                return 1;
            }
            if (atom == TH_TAG_IMG || atom == TH_TAG_INPUT) {
                return 0;
            }
            th_node *inner = from_start ? node->first_child : node->last_child;
            if (inner != NULL) {
                node = inner;
                continue;
            }
        }
        while (node != root && (from_start ? node->next_sibling : node->prev_sibling) == NULL) {
            node = node->parent;
        }
        if (node == root) {
            return 0;
        }
        node = from_start ? node->next_sibling : node->prev_sibling;
    }
    return 0;
}

/* Splice a converter's returned Markdown into the output at the element's position:
   a registered block tag opens its own block line, anything else flows inline. A
   newline inside the string starts a fresh continuation line so an outer list or
   blockquote prefix keeps applying; every other code point is copied verbatim,
   since the converter already produced final Markdown. An empty result emits
   nothing, leaving no stray blank line behind. */
static void md_emit_converted(md_ctx *ctx, th_node *node, PyObject *text, int blank) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    if (len == 0) {
        return;
    }
    int inline_edges = !(node->ns == TH_NS_HTML && is_md_block(node->atom));
    if (!inline_edges) {
        md_block_line(ctx, 1);
    } else {
        /* the content reached the converter trimmed, so whitespace at its edges is
           owed around the converted text instead, as it lands outside a marker */
        if (ctx->line_has_content && md_edge_space(ctx, node, 1)) {
            ctx->space_pending = 1;
        }
        md_before_visible(ctx);
    }
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        if (character == '\n') {
            md_newline(ctx);
        } else {
            md_put_literal(ctx, character);
            ctx->line_has_content = 1;
        }
    }
    /* content that is whitespace alone owes one space, already placed before it */
    if (inline_edges && !blank && md_edge_space(ctx, node, 0)) {
        ctx->space_pending = 1;
    }
}

/* Hand the rendered children to the converter and splice in what it returns. The
   walk never begins a block with whitespace, so only a trailing hard break and
   whitespace are trimmed off the children's Markdown. */
static void md_leave_converter(md_ctx *ctx, md_frame *frame) {
    md_converting *saved = frame->convert;
    Py_UCS4 *data = ctx->out.data;
    Py_ssize_t end = ctx->out.len == ctx->break_end ? ctx->break_start : ctx->out.len;
    while (end > 0 && is_space(data[end - 1])) {
        end--;
    }
    int table_ended = end == ctx->table_end; /* the converted children end with a pipe table */
    PyObject *content = NULL;
    if (!(ctx->out.failed | ctx->prefix.failed)) { /* GCOVR_EXCL_BR_LINE: only an unforceable allocation fails */
        content = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, end);
    }
    PyMem_Free(data);
    PyMem_Free(ctx->prefix.data);
    ctx->out = saved->out;
    ctx->prefix = saved->prefix;
    ctx->marker_base = saved->marker_base;
    md_settle_pending(ctx);
    ctx->started = saved->started;
    ctx->line_has_content = saved->line_has_content;
    ctx->line_number_start = saved->line_number_start;
    ctx->line_number_checked = saved->line_number_checked;
    ctx->break_start = saved->break_start;
    ctx->break_end = saved->break_end;
    ctx->line_start = saved->line_start;
    ctx->line_checked = saved->line_checked;
    ctx->space_pending = saved->space_pending;
    ctx->drop_space = saved->drop_space;
    ctx->pending_loose = saved->pending_loose;
    ctx->suppress_break = saved->suppress_break;
    ctx->tight = saved->tight;
    ctx->list_depth = saved->list_depth;
    ctx->g_bold = saved->g_bold;
    ctx->g_italic = saved->g_italic;
    ctx->inline_only = saved->inline_only;
    ctx->deep_block_span = saved->deep_block_span;
    ctx->list_end_marker = saved->list_end_marker;
    ctx->table_end = saved->table_end;
    ctx->converting = saved->converting;
    PyObject *tag = saved->tag;
    PyObject *converter = saved->converter;
    PyMem_Free(saved);
    th_node *node = frame->node;
    if (content == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(tag);    /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->failed = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        return;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *element = ctx->opt->wrap_node(ctx->opt->wrap_node_ctx, node);
    if (element == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(tag);     /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(content); /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->failed = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
        return;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int blank = PyUnicode_GET_LENGTH(content) == 0;
    PyObject *result = PyObject_CallFunctionObjArgs(converter, element, content, NULL);
    Py_DECREF(element);
    Py_DECREF(content);
    if (result == NULL) {
        Py_DECREF(tag);
        ctx->failed = 1;
        return;
    }
    if (!PyUnicode_Check(result)) {
        PyErr_Format(PyExc_TypeError, "to_markdown converter for <%U> must return a str, not %.200s", tag,
                     Py_TYPE(result)->tp_name);
        Py_DECREF(tag);
        Py_DECREF(result);
        ctx->failed = 1;
        return;
    }
    Py_DECREF(tag);
    md_emit_converted(ctx, node, result, blank);
    if (PyUnicode_GET_LENGTH(result) > 0) {
        /* a converter that hands its content back leaves a table that ended it at the end of the output; text
           after it would run on along the table's last line, as one more row of a pipe table or inside the HTML
           block of a raw one, so it opens a block of its own */
        if (table_ended) {
            ctx->table_end = ctx->out.len;
            ctx->table_prefix = ctx->prefix.len;
        }
        if (table_ended || md_line_opens_html(ctx) == MD_HTML_LINE_BLOCK) {
            ctx->block_ended = 1;
        }
    }
    Py_DECREF(result);
}

/* The largest number an ordered list marker holds: CommonMark 5.2 reads one to nine digits as a marker, and a longer
   number as paragraph text. */
#define MD_LIST_NUMBER_MAX 999999999

static Py_ssize_t md_list_number_attr(md_ctx *ctx, th_node *node, const char *name, Py_ssize_t fallback) {
    Py_ssize_t length;
    const Py_UCS4 *value = md_attr(ctx->tree, node, name, &length);
    if (value == NULL) {
        return fallback;
    }
    Py_ssize_t index = 0;
    while (index < length && is_space(value[index])) {
        index++;
    }
    if (index < length && value[index] == '+') {
        index++;
    }
    if (index == length || value[index] < '0' || value[index] > '9') {
        return fallback;
    }
    Py_ssize_t number = 0;
    for (; index < length && value[index] >= '0' && value[index] <= '9'; index++) {
        Py_ssize_t digit = value[index] - '0';
        if (number > (PY_SSIZE_T_MAX - digit) / 10) {
            return fallback;
        }
        number = number * 10 + digit;
    }
    return number;
}

static int md_list_number_needs_html(md_ctx *ctx, th_node *node, const char *name) {
    Py_ssize_t length;
    const Py_UCS4 *value = md_attr(ctx->tree, node, name, &length);
    if (value == NULL) {
        return 0;
    }
    Py_ssize_t index = 0;
    while (index < length && is_space(value[index])) {
        index++;
    }
    if (index < length && value[index] == '-') {
        return index + 1 < length && value[index + 1] >= '0' && value[index + 1] <= '9';
    }
    return md_list_number_attr(ctx, node, name, 1) > MD_LIST_NUMBER_MAX;
}

/* Whether a list child is a wrapper around list items (`<ul><div><li>`), which the
   list looks through so its items keep their markers and numbering. */
static int md_is_item_wrapper(th_node *node) {
    if (node->type != TH_NODE_ELEMENT || node->ns != TH_NS_HTML) {
        return 0;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        TH_WORK_STEP();
        if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML && child->atom == TH_TAG_LI) {
            return 1;
        }
    }
    return 0;
}

/* Scan through item wrappers once for both loose layout and ordinals that GFM
   cannot carry. Subsequent list-marker numbers are ignored by GFM readers. */
static int md_list_layout(md_ctx *ctx, th_node *list, int ordered, int *raw_html) {
    th_node *parent = list;
    th_node *scan = list->first_child;
    int loose = 0;
    int first = 1;
    for (;;) {
        while (scan == NULL) {
            if (parent == list) {
                return loose;
            }
            scan = parent->next_sibling;
            parent = parent->parent;
        }
        if (scan->type == TH_NODE_ELEMENT && scan->ns == TH_NS_HTML && scan->atom == TH_TAG_LI) {
            if (!loose && md_item_is_loose(ctx, scan)) {
                loose = 1;
            }
            if (ordered && scan->attr_count > 0) {
                Py_ssize_t length;
                if (md_attr(ctx->tree, scan, "value", &length) != NULL) {
                    *raw_html |= !first || md_list_number_needs_html(ctx, scan, "value");
                }
            }
            if (ordered) {
                first = 0;
            }
        } else if (md_is_item_wrapper(scan)) {
            parent = scan;
            scan = scan->first_child;
            continue;
        }
        if (loose && (!ordered || *raw_html)) {
            return loose;
        }
        if (loose && parent == list) {
            scan = scan->next_sibling;
            while (scan != NULL && scan->atom == TH_TAG_LI && scan->attr_count == 0) {
                scan = scan->next_sibling;
            }
            continue;
        }
        scan = scan->next_sibling;
    }
}

static int md_list_raw_safe(th_tree *tree, th_node *list) {
    th_node *scan = list;
    for (;;) {
        if (scan->type == TH_NODE_ELEMENT && scan->ns == TH_NS_HTML) {
            switch (scan->atom) {
            case TH_TAG_OL:
                if (scan->attr_count > 0 &&
                    scan->attr_count > (th_node_attr_find(tree, scan, "start", 5) >= 0) +
                                           (th_node_attr_find(tree, scan, "reversed", 8) >= 0)) {
                    return 0;
                }
                break;
            case TH_TAG_LI:
                if (scan->attr_count > 0 && (scan->attr_count > 1 || th_node_attr_find(tree, scan, "value", 5) < 0)) {
                    return 0;
                }
                break;
            case TH_TAG_UL:
            case TH_TAG_MENU:
            case TH_TAG_P:
            case TH_TAG_DIV:
            case TH_TAG_SPAN:
            case TH_TAG_B:
            case TH_TAG_STRONG:
            case TH_TAG_I:
            case TH_TAG_EM:
            case TH_TAG_S:
            case TH_TAG_DEL:
            case TH_TAG_STRIKE:
            case TH_TAG_CODE:
            case TH_TAG_PRE:
            case TH_TAG_BR:
            case TH_TAG_BLOCKQUOTE:
                if (scan->attr_count > 0) {
                    return 0;
                }
                break;
            default:
                return 0;
            }
        } else if (scan->type != TH_NODE_TEXT) {
            return 0;
        }
        if (scan->first_child != NULL) {
            scan = scan->first_child;
            continue;
        }
        while (scan != list && scan->next_sibling == NULL) {
            scan = scan->parent;
        }
        if (scan == list) {
            return 1;
        }
        scan = scan->next_sibling;
    }
}

/* Whether a list item cannot interrupt the paragraph on the line before it: it starts with a blank marker line, or it
   is numbered other than 1 (CommonMark 5.2). */
static TH_NOINLINE int md_item_cannot_interrupt(md_ctx *ctx, th_node *item, int ordered, Py_ssize_t number) {
    return md_leads_with_inline(ctx, item, NULL, 1) <= 0 ||
           (ordered && md_list_number_attr(ctx, item, "value", number) != 1);
}

/* Open one list item: its marker on a fresh line, then its children laid out as
   blocks indented under the marker. */
static void md_render_item(md_ctx *ctx, th_node *child, md_list_state *state) {
    /* what a list holds before its first item is laid out as a block of its own, which that item then follows the way
       the list follows a paragraph */
    /* md_enter_list already decided this item, and nothing has been written since */
    if (!state->item_seen && ctx->line_has_content &&
        !(child == state->checked_item && ctx->out.len == state->checked_len) &&
        md_item_cannot_interrupt(ctx, child, state->ordered, state->number)) {
        ctx->interrupt_blank = 1;
    }
    md_block_line(ctx, state->loose);
    Py_ssize_t lead = 0;
    if (ctx->opt->google_doc) {
        /* Google Docs flattens nested lists, signaling depth with margin-left
           instead, so each google_list_indent pixels add one indent level */
        Py_ssize_t style_len;
        const Py_UCS4 *style = md_attr(ctx->tree, child, "style", &style_len);
        const Py_UCS4 *value;
        Py_ssize_t value_len;
        if (style != NULL && md_css_prop(style, style_len, "margin-left", &value, &value_len)) {
            int nest = md_css_px(value, value_len) / ctx->opt->google_list_indent;
            if (nest > TH_MAX_INDENT_LEVELS / 2) {
                nest = TH_MAX_INDENT_LEVELS / 2; /* each margin step is a list level */
            }
            for (int level = 0; level < nest; level++) {
                sbuf_puts(&ctx->out, "  ");
            }
            lead = (Py_ssize_t)nest * 2;
        }
    }
    Py_ssize_t width;
    if (state->ordered) {
        state->number = md_list_number_attr(ctx, child, "value", state->number);
        /* a marker over nine digits reads as paragraph text, and the item's indented blocks would continue it */
        if (state->number > MD_LIST_NUMBER_MAX) {
            state->number = MD_LIST_NUMBER_MAX;
        }
        width = lead + md_put_decimal(&ctx->out, state->number) + 2;
        sbuf_putc(&ctx->out, (Py_UCS4)(unsigned char)state->delimiter);
        sbuf_putc(&ctx->out, ' ');
        state->number++;
    } else {
        sbuf_putc(&ctx->out, state->bullet);
        sbuf_putc(&ctx->out, ' ');
        width = lead + 2;
    }
    /* the item's content starts its own line: `- 1. x` would nest an ordered list */
    ctx->line_has_content = 0;
    ctx->item_marker = 1;
    ctx->list_over = ctx->out.len; /* a marker ends the list that closed before it */
    state->sub_indent = width;
    state->item_seen = 1;
    state->in_run = 0;
    int saved_levels = ctx->indent_levels;
    Py_ssize_t base = md_indent(ctx, width);
    int saved_tight = ctx->tight;
    ctx->tight = !state->loose;
    th_node *only = child->first_child;
    int task = 0;
    ctx->suppress_break = (only != NULL && only == child->last_child && only->type == TH_NODE_TEXT &&
                           only->text_len > 0 && !is_space(md_text_first(ctx, only, ' '))) ||
                          md_leads_with_inline(ctx, child, &task, 0) > 0;
    if (task > 0) {
        sbuf_puts(&ctx->out, task == 2 ? "[x]" : "[ ]");
        if (ctx->suppress_break) {
            sbuf_putc(&ctx->out, ' ');
        }
        ctx->line_has_content = 1;
    }
    if (!ctx->opt->wrap_list_items) {
        ctx->no_wrap++;
    }
    md_frame *frame = md_push(ctx, child, MD_WALK_BLOCK, MD_LEAVE_ITEM);
    if (frame != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        frame->prefix_base = base;
        frame->saved_tight = saved_tight;
        frame->saved_levels = saved_levels;
    }
}

/* Indent what follows under the last item, restoring the prefix and tightness once
   the frames pushed on top of this one are done. */
static void md_push_indent(md_ctx *ctx, Py_ssize_t width, int tight) {
    int saved_levels = ctx->indent_levels;
    Py_ssize_t base = md_indent(ctx, width);
    int saved_tight = ctx->tight;
    ctx->tight = tight;
    md_frame *frame = md_push(ctx, NULL, MD_WALK_NONE, MD_LEAVE_NONE);
    if (frame != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        frame->prefix_base = base;
        frame->saved_tight = saved_tight;
        frame->saved_levels = saved_levels;
    }
}

/* Lay out one child of a list, whose state the frame at owner holds. Content that is
   neither an item nor a nested list is still rendered, the way a browser shows it:
   before the first item it is a block of its own, and after an item it continues
   that item, indented under its marker so the list stays one list. */
static void md_list_child(md_ctx *ctx, Py_ssize_t owner, th_node *child) {
    md_list_state *state = &ctx->frames[owner].list.state;
    if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML) {
        if (child->atom == TH_TAG_LI) {
            md_render_item(ctx, child, state);
            return;
        }
        if (child->atom == TH_TAG_UL || child->atom == TH_TAG_OL || child->atom == TH_TAG_MENU) {
            /* a list nested directly in a list (a sibling of the <li>s, not wrapped
               in one) belongs to the preceding item as a sublist; the parser makes
               this shape, and dropping it would lose every nested item. The nested
               list re-applies the wrap guard per item, so none is needed here.
               Before the first item no marker exists to indent under. */
            state->in_run = 0;
            if (state->item_seen) {
                md_push_indent(ctx, state->sub_indent, 1);
            }
            md_render_block(ctx, child);
            return;
        }
        if (md_is_item_wrapper(child)) {
            md_frame *frame = md_push(ctx, child, MD_WALK_WRAPPER, MD_LEAVE_NONE);
            if (frame != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                frame->owner = owner;
            }
            return;
        }
    }
    if (!state->item_seen) {
        md_block_child(ctx, child, &state->in_run);
    } else if (child->type == TH_NODE_TEXT) {
        /* text pushes no frame, so its indent is undone right here */
        int saved_levels = ctx->indent_levels;
        Py_ssize_t base = md_indent(ctx, state->sub_indent);
        int saved_tight = ctx->tight;
        ctx->tight = !state->loose;
        md_block_child(ctx, child, &state->in_run);
        ctx->tight = saved_tight;
        ctx->prefix.len = base;
        ctx->indent_levels = saved_levels;
    } else {
        md_push_indent(ctx, state->sub_indent, !state->loose);
        md_block_child(ctx, child, &ctx->frames[owner].list.state.in_run);
    }
}

/* Whether only line breaks and line prefixes were written since offset, as an empty
   block leaves them, so nothing a reader sees separates what came before from what
   follows. offset sits mid-line, right after the last character written there. */
static int md_blank_since(md_ctx *ctx, Py_ssize_t offset) {
    /* text last on the output answers at once: a line prefix holds only spaces and `>` */
    if (ctx->out.len > offset) {
        Py_UCS4 last = ctx->out.data[ctx->out.len - 1];
        if (last != '\n' && last != ' ' && last != '>') {
            return 0;
        }
    }
    Py_ssize_t column = ctx->prefix.len;
    for (Py_ssize_t index = offset; index < ctx->out.len; index++) {
        if (ctx->out.data[index] == '\n') {
            column = 0;
        } else if (column < ctx->prefix.len) {
            column++; /* every line opens with the prefix, so the first prefix.len characters are it */
        } else {
            return 0;
        }
    }
    return 1;
}

static int md_list_ordered(md_ctx *ctx, th_node *node) {
    int ordered = node->atom == TH_TAG_OL;
    if (ctx->opt->google_doc) {
        /* a Google Docs export keeps the ol/ul element but states the real marker
           kind in list-style-type, so honor it when present */
        Py_ssize_t style_len;
        const Py_UCS4 *style = md_attr(ctx->tree, node, "style", &style_len);
        const Py_UCS4 *value;
        Py_ssize_t value_len;
        if (style != NULL && md_css_prop(style, style_len, "list-style-type", &value, &value_len)) {
            ordered = !md_css_unordered(value, value_len);
        }
    }
    return ordered;
}

/* The item that decides whether a list can interrupt the paragraph on the line before it: only
   the list's first element child, and only when it is an item. Anything before it renders as a
   block of its own. */
static TH_NOINLINE th_node *md_list_first_item(th_node *list) {
    th_node *item = list->first_child;
    while (item != NULL && item->type != TH_NODE_ELEMENT) {
        item = item->next_sibling;
    }
    return item != NULL && item->ns == TH_NS_HTML && item->atom == TH_TAG_LI ? item : NULL;
}

/* Whether a container under parent needs more indent levels than the prefix can hold, so a block there would sit
   outside its own column. An item adds two levels and a block quote one; a child of a list that is not an item is
   indented by two more (md_list_child). Past TH_MAX_INDENT_LEVELS the prefix stops growing, so the outermost
   container that does not fit is written as one raw HTML block instead. */
static int md_nesting_exceeds(th_node *parent, int in_list, int room) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        TH_WORK_STEP();
        int step = in_list ? 2 : 0;
        int child_in_list = 0;
        if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML) {
            if (child->atom == TH_TAG_LI) {
                step = 2;
            } else if (child->atom == TH_TAG_BLOCKQUOTE) {
                step += 1;
            } else if (child->atom == TH_TAG_UL || child->atom == TH_TAG_OL || child->atom == TH_TAG_MENU) {
                child_in_list = 1;
            } else if (in_list && md_is_item_wrapper(child)) {
                step = 0;
                child_in_list = in_list;
            }
        }
        if (step > room || (child->first_child != NULL && md_nesting_exceeds(child, child_in_list, room - step))) {
            return 1;
        }
    }
    return 0;
}

static void md_enter_list(md_ctx *ctx, th_node *node) {
    /* a top container that fits holds every container under it within the cap, so only the outermost one walks */
    if (ctx->indent_levels == 0 && md_nesting_exceeds(node, 1, TH_MAX_INDENT_LEVELS)) {
        md_block_line(ctx, 0);
        md_emit_raw_html(ctx, node);
        return;
    }
    int ordered = md_list_ordered(ctx, node);
    Py_ssize_t number = ordered ? md_list_number_attr(ctx, node, "start", 1) : 1;
    int preserve_ordinals = node->atom == TH_TAG_OL && ctx->opt->raw_list_html;
    int raw_html = preserve_ordinals && (th_node_attr_find(ctx->tree, node, "reversed", 8) >= 0 ||
                                         md_list_number_needs_html(ctx, node, "start"));
    int loose = raw_html ? 0 : md_list_layout(ctx, node, preserve_ordinals, &raw_html);
    if (raw_html && !md_list_raw_safe(ctx->tree, node)) {
        raw_html = 0;
        loose = md_list_layout(ctx, node, 0, &raw_html);
    }
    if (raw_html) {
        md_block_line(ctx, 1);
        md_emit_raw_html(ctx, node);
        return;
    }
    th_node *checked_item = ctx->line_has_content ? md_list_first_item(node) : NULL;
    if (checked_item != NULL && md_item_cannot_interrupt(ctx, checked_item, ordered, number)) {
        ctx->interrupt_blank = 1;
    }
    md_list_state state = {
        .number = number,
        .bullet = md_bullet_at(ctx->opt->bullets, ctx->list_depth),
        .delimiter = '.',
        .ordered = ordered,
        .loose = loose,
        .checked_item = checked_item,
        .checked_len = ctx->out.len,
    };
    /* CommonMark keeps items with the same bullet or ordered delimiter in one list,
       blank line or not, so a list that follows one of its own kind with nothing in
       between switches its marker to stay a separate list */
    if (ctx->list_end_marker != 0 && ctx->list_end_prefix == ctx->prefix.len && md_blank_since(ctx, ctx->list_end)) {
        if (ordered && ctx->list_end_marker == '.') {
            state.delimiter = ')';
        } else if (!ordered && ctx->list_end_marker == state.bullet) {
            state.bullet = state.bullet == '-' ? '*' : '-';
        }
    }
    md_frame *frame = md_push(ctx, node, MD_WALK_LIST, MD_LEAVE_LIST);
    if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    frame->list.state = state;
    frame->list.start = ctx->out.len;
    ctx->list_depth++;
}

static void md_leave_list(md_ctx *ctx, md_frame *frame) {
    ctx->list_depth--;
    if (ctx->out.len > frame->list.start) {
        /* a list with no items writes nothing, so the one before it stays the neighbor */
        ctx->list_end = ctx->out.len;
        ctx->list_end_prefix = ctx->prefix.len;
        ctx->list_end_marker =
            frame->list.state.ordered ? (Py_UCS4)frame->list.state.delimiter : frame->list.state.bullet;
    }
}

/* Kept on the handle between to_markdown() calls, so converting a list item by item does not recount the list for each
   item. An item maps to the complement of its number, which is never zero and so marks an absent node. The numbers read
   the start and value attributes, so an attribute write drops the cache; a structural change drops it through
   handle_drop_index. */
typedef struct {
    th_node_map map;
    th_node_map wrappers; /* 1 where md_is_item_wrapper holds, 2 where it does not */
    uint64_t attr_version;
} md_ordinal_cache;

void th_markdown_ordinals_free(void *ordinals) {
    md_ordinal_cache *const cache = ordinals;
    if (cache != NULL) {
        PyMem_Free(cache->map.entries);
        PyMem_Free(cache->wrappers.entries);
        PyMem_Free(cache);
    }
}

static md_ordinal_cache *md_ordinal_cache_get(md_ctx *ctx) {
    md_ordinal_cache *cache = *ctx->ordinals;
    if (cache != NULL && cache->attr_version != ctx->tree->attr_version) {
        th_markdown_ordinals_free(cache);
        *ctx->ordinals = cache = NULL;
    }
    if (cache == NULL) {
        *ctx->ordinals = cache = PyMem_Calloc(1, sizeof(*cache));
        if (cache == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        cache->attr_version = ctx->tree->attr_version;
    }
    return cache;
}

static int md_is_item(const th_node *node) {
    return node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && node->atom == TH_TAG_LI;
}

static int md_is_list_atom(uint16_t atom) {
    return atom == TH_TAG_UL || atom == TH_TAG_OL || atom == TH_TAG_MENU;
}

/* Walk back from target over the items md_list_number_before counts for container, in the reverse of its order, to
   the nearest one whose number map holds; NULL when no item before target has one. An item inside a list that scan
   enters as a wrapper does not qualify: the scan counts it, but what map holds for it is its number in that list. */
static th_node *md_numbered_item_before(const th_node_map *map, const th_node *container, th_node *target) {
    th_node *node = target;
    Py_ssize_t nested = 0; /* the lists among the wrappers the walk is inside */
    for (;;) {
        while (node->prev_sibling == NULL) {
            node = node->parent;
            if (node == container) {
                return NULL;
            }
            nested -= md_is_list_atom(node->atom);
        }
        node = node->prev_sibling;
        TH_WORK_STEP();
        while (!md_is_item(node) && md_is_item_wrapper(node)) {
            nested += md_is_list_atom(node->atom);
            node = node->last_child;
        }
        if (nested == 0 && md_is_item(node) && th_node_map_find(map, node) != 0) {
            return node;
        }
    }
}

/* Count the ordinals of the items before target, looking through item wrappers. The
   scan starts at from, an item before target whose number *number holds, or at the top
   of container when from is NULL. The caller found container by climbing from target
   through those wrappers, so the scan reaches target before it runs out of container.
   With a map, each item passed after from enters it with the number before that item,
   unless the scan is inside a list it entered as a wrapper: it counts those items, but
   they number from their own list. Returns 0, or -1 when map cannot grow. */
static int md_list_number_before(md_ctx *ctx, th_node *container, th_node *from, th_node *target, Py_ssize_t *number,
                                 th_node_map *map) {
    th_node *parent = from != NULL ? from->parent : container;
    th_node *child = from != NULL ? from : container->first_child;
    Py_ssize_t nested = 0; /* the lists among the wrappers the scan is inside */
    for (;;) {
        while (child == NULL) {
            nested -= md_is_list_atom(parent->atom);
            child = parent->next_sibling;
            parent = parent->parent;
        }
        if (child == target) {
            return 0;
        }
        TH_WORK_STEP();
        if (md_is_item(child)) {
            if (map != NULL && nested == 0 && child != from) {
                if (th_node_map_insert(map, child, ~*number) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                                      /* GCOVR_EXCL_LINE: allocation failure */
                }
            }
            *number = md_list_number_attr(ctx, child, "value", *number);
            if (*number < PY_SSIZE_T_MAX) {
                (*number)++;
            }
        } else if (md_is_item_wrapper(child)) {
            nested += md_is_list_atom(child->atom);
            parent = child;
            child = child->first_child;
            continue;
        }
        child = child->next_sibling;
    }
}

/* A number not yet known counts on from the nearest earlier item that has one, and the items passed on the way keep
   theirs. The first item counted after a change keeps only its own, so one conversion does not pay for numbers nobody
   asks for. */
static Py_ssize_t md_root_item_number(md_ctx *ctx, md_ordinal_cache *cache, th_node *list, th_node *node) {
    Py_ssize_t known = th_node_map_find(&cache->map, node);
    if (known != 0) {
        return ~known;
    }
    th_node_map *passed = cache->map.count > 0 ? &cache->map : NULL; /* where the items passed go, if anywhere */
    th_node *from = passed != NULL ? md_numbered_item_before(passed, list, node) : NULL;
    Py_ssize_t number = from != NULL ? ~th_node_map_find(passed, from) : md_list_number_attr(ctx, list, "start", 1);
    int failed = md_list_number_before(ctx, list, from, node, &number, passed) < 0;
    failed |= th_node_map_insert(&cache->map, node, ~number) < 0;
    if (failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        /* GCOVR_EXCL_START: allocation-failure path */
        th_markdown_ordinals_free(cache); /* GCOVR_EXCL_LINE: allocation-failure path */
        *ctx->ordinals = NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->out.failed = 1;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_STOP */
    return number;
}

/* md_is_item_wrapper, remembered on the handle: the climb from an item asks each wrapper it passes, and a wrapper with
   many children that are not items would otherwise be scanned once for every item behind it. */
static int md_is_item_wrapper_cached(md_ctx *ctx, th_node *node) {
    md_ordinal_cache *cache = md_ordinal_cache_get(ctx);
    if (cache == NULL) {                 /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return md_is_item_wrapper(node); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t known = th_node_map_find(&cache->wrappers, node);
    if (known != 0) {
        return known == 1;
    }
    int wrapper = md_is_item_wrapper(node);
    if (th_node_map_insert(&cache->wrappers, node, wrapper ? 1 : 2) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        /* GCOVR_EXCL_START: allocation-failure path */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_STOP */
    return wrapper;
}

static void md_render_root_item(md_ctx *ctx, th_node *node) {
    th_node *list = node->parent;
    /* an item is never a wrapper, even around an item the parser foster-parented into it: the list renders and counts
       that item and looks for none inside, so climbing through it would name a list whose scan cannot reach node.
       The parent holds node, so it is a wrapper without a scan. */
    while (list != NULL && list->atom != TH_TAG_UL && list->atom != TH_TAG_OL && list->atom != TH_TAG_MENU &&
           list->atom != TH_TAG_LI &&
           (list == node->parent ? list->ns == TH_NS_HTML : md_is_item_wrapper_cached(ctx, list))) {
        list = list->parent;
    }
    int in_list = list != NULL && list->ns == TH_NS_HTML &&
                  (list->atom == TH_TAG_UL || list->atom == TH_TAG_OL || list->atom == TH_TAG_MENU);
    int ordered = in_list && md_list_ordered(ctx, list);
    md_ordinal_cache *cache = ordered ? md_ordinal_cache_get(ctx) : NULL;
    Py_ssize_t number = 1;
    if (cache != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        number = md_root_item_number(ctx, cache, list, node);
    }
    Py_ssize_t depth = 0;
    for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->ns == TH_NS_HTML &&
            (ancestor->atom == TH_TAG_UL || ancestor->atom == TH_TAG_OL || ancestor->atom == TH_TAG_MENU)) {
            depth++;
        }
    }
    md_list_state state = {
        .number = number,
        .bullet = md_bullet_at(ctx->opt->bullets, depth > 0 ? depth - 1 : 0),
        .delimiter = '.',
        .ordered = ordered,
        .loose = md_item_is_loose(ctx, node),
    };
    ctx->list_depth = (int)depth;
    md_render_item(ctx, node, &state);
}

/* Start rendering a cell's content on its own, collapsing internal whitespace to single
   spaces so the cell stays on one row once md_leave_cell copies it out. in_cell steers
   the writers themselves: a pipe is escaped where it is written and a block that a cell
   cannot hold becomes HTML, so nothing here has to reinterpret finished markdown. */
static void md_enter_cell(md_ctx *ctx, md_table *table) {
    table->saved_out = ctx->out;
    table->saved_prefix = ctx->prefix;
    table->saved_marker_base = ctx->marker_base;
    table->saved_started = ctx->started;
    table->saved_line = ctx->line_has_content;
    table->saved_line_number_start = ctx->line_number_start;
    table->saved_line_number_checked = ctx->line_number_checked;
    table->saved_break_start = ctx->break_start;
    table->saved_break_end = ctx->break_end;
    table->saved_space = ctx->space_pending;
    table->saved_drop = ctx->drop_space;
    table->saved_table_end = ctx->table_end;
    ctx->table_end = -1;
    ctx->out = (sbuf){NULL, 0, 0, 0, 0, NULL};
    ctx->prefix = (sbuf){NULL, 0, 0, 0, 0, NULL};
    Py_ssize_t outer = ctx->marker_count;
    ctx->marker_base = outer;
    ctx->pending = NULL;
    /* a cell's text renders on its own, but emphasis around the table styles it too, and
       a pipe cell holds inline content (GFM 4.10), so every enclosing run reopens in it */
    for (Py_ssize_t index = table->saved_marker_base; index < outer; index++) {
        md_marker copy = ctx->markers[index];
        Py_ssize_t pushed = md_push_marker(ctx, copy.open);
        if (pushed >= 0) { /* GCOVR_EXCL_BR_LINE: -1 only on an allocation failure */
            copy.emitted = 0;
            copy.flags &= MD_MARK_STRIKE; /* the flanking the element saw outside does not hold in the cell */
            ctx->markers[pushed] = copy;
        }
    }
    ctx->line_has_content = 1;
    ctx->line_number_start = 0;
    ctx->break_end = -1;
    ctx->space_pending = 0;
    ctx->drop_space = 1;
    ctx->in_cell = 1;
}

static void md_leave_cell(md_ctx *ctx, md_table *table) {
    if (ctx->marker_count > ctx->marker_base) {
        md_suspend_markers(ctx); /* the runs reopened in the cell close inside it */
        ctx->marker_count = ctx->marker_base;
    }
    sbuf rendered = ctx->out;
    /* the cell's buffers and its padded grid slot fail apart from the output, so their flags join it here */
    int failed = rendered.failed | ctx->prefix.failed;
    PyMem_Free(ctx->prefix.data);
    ctx->out = table->saved_out;
    ctx->prefix = table->saved_prefix;
    ctx->marker_base = table->saved_marker_base;
    md_settle_pending(ctx);
    ctx->started = table->saved_started;
    ctx->line_has_content = table->saved_line;
    ctx->line_number_start = table->saved_line_number_start;
    ctx->line_number_checked = table->saved_line_number_checked;
    ctx->break_start = table->saved_break_start;
    ctx->break_end = table->saved_break_end;
    ctx->space_pending = table->saved_space;
    ctx->drop_space = table->saved_drop;
    ctx->table_end = table->saved_table_end;
    ctx->in_cell = 0;
    sbuf *dst = table->grid != NULL ? &table->grid[table->row * table->columns + table->column] : &ctx->out;
    /* a block in the cell opens on a fresh line, so the rendering can start with a
       whitespace run; only a run between two characters becomes a space */
    int space_run = 0;
    int wrote = 0;
    for (Py_ssize_t index = 0; index < rendered.len; index++) {
        Py_UCS4 ch = rendered.data[index];
        if (ch == '\n' || ch == ' ') {
            space_run = wrote;
            continue;
        }
        if (space_run) {
            sbuf_putc(dst, ' ');
            space_run = 0;
        }
        sbuf_putc(dst, ch);
        wrote = 1;
    }
    PyMem_Free(rendered.data);
    ctx->out.failed |= failed | dst->failed;
    if (table->grid != NULL) {
        if (dst->len > table->widths[table->column]) {
            table->widths[table->column] = dst->len;
        }
    } else {
        sbuf_puts(&ctx->out, " |");
    }
    table->column++;
}

/* A row's element children come only from HTML table parsing (foreign content is
   foster-parented out of the table), so the td/th atoms already imply the HTML
   namespace and no separate ns test is needed in these loops. */
/* A row reads as a header when it sits in a <thead> or holds a <th> cell. A
   collected row always has an element parent (the table or a section), so its
   namespace and type need no guard. */
static int md_row_is_header(th_node *row) {
    if (row->parent->atom == TH_TAG_THEAD) {
        return 1;
    }
    for (th_node *cell = row->first_child; cell != NULL; cell = cell->next_sibling) {
        if (cell->type == TH_NODE_ELEMENT && cell->atom == TH_TAG_TH) {
            return 1;
        }
    }
    return 0;
}

static TH_NOINLINE int md_cell_has_span(th_node *cell) {
    for (Py_ssize_t index = 0; index < cell->attr_count; index++) {
        uint32_t atom = cell->attrs[index].name_atom;
        if (atom == TH_ATTR_COLSPAN || atom == TH_ATTR_ROWSPAN) {
            return 1;
        }
    }
    return 0;
}

/* Collect the table's rows in document order across nested thead/tbody/tfoot wrappers
   and return the widest row's column count. With rows NULL it only counts, so the
   array is sized by the same walk that fills it. A row with no cell draws nothing,
   while every pipe row holds at least one cell (GFM 4.10), so it is left out. */
static Py_ssize_t md_collect_rows(th_node *table, th_node **rows, Py_ssize_t *count, int *has_spans) {
    Py_ssize_t columns = 0;
    Py_ssize_t fewest = PY_SSIZE_T_MAX;
    th_node *node = table->first_child;
    while (node != NULL) {
        if (node->type == TH_NODE_ELEMENT) {
            if (node->atom == TH_TAG_TR) {
                if (rows == NULL) {
                    (*count)++;
                } else {
                    Py_ssize_t cells = 0;
                    for (th_node *cell = node->first_child; cell != NULL; cell = cell->next_sibling) {
                        if (cell->type == TH_NODE_ELEMENT && (cell->atom == TH_TAG_TD || cell->atom == TH_TAG_TH)) {
                            cells++;
                            if (cell->attr_count != 0 && md_cell_has_span(cell)) {
                                *has_spans = 1;
                            }
                        }
                    }
                    if (cells > 0) {
                        rows[(*count)++] = node;
                        if (cells < fewest) {
                            fewest = cells;
                        }
                    }
                    if (cells > columns) {
                        columns = cells;
                    }
                }
            } else if ((node->atom == TH_TAG_THEAD || node->atom == TH_TAG_TBODY || node->atom == TH_TAG_TFOOT) &&
                       node->first_child != NULL) {
                node = node->first_child;
                continue;
            }
        }
        while (node->next_sibling == NULL && node->parent != table) {
            node = node->parent;
        }
        node = node->next_sibling;
    }
    if (rows != NULL && fewest < columns) {
        *has_spans = 1;
    }
    return columns;
}

/* Emit one padded grid row: each cell's text then spaces out to the column
   width, wrapped in pipes. A NULL grid emits an all-spaces (empty header) row, and
   NULL widths a row without padding. */
static void md_emit_padded_row(md_ctx *ctx, sbuf *grid, Py_ssize_t row, Py_ssize_t columns, const Py_ssize_t *widths) {
    sbuf_putc(&ctx->out, '|');
    for (Py_ssize_t column = 0; column < columns; column++) {
        sbuf_putc(&ctx->out, ' ');
        Py_ssize_t len = 0;
        if (grid != NULL) {
            sbuf *cell = &grid[row * columns + column];
            sbuf_put_run(&ctx->out, cell->data, cell->len);
            len = cell->len;
        }
        for (Py_ssize_t pad = len; widths != NULL && pad < widths[column]; pad++) {
            TH_WORK_STEP();
            sbuf_putc(&ctx->out, ' ');
        }
        sbuf_puts(&ctx->out, " |");
    }
    ctx->line_has_content = 1;
}

/* Whether the cells fund aligning the table, spending the budget when they do. Every row is padded out to its
   column's widest cell, so one wide cell over many short rows would multiply the output by the row count. Each
   cell funds three spaces per character it holds and twelve for itself; the count stops once it passes the budget. */
static int md_padding_funded(md_ctx *ctx, const md_table *table) {
    Py_ssize_t columns = table->columns;
    for (Py_ssize_t index = 0; index < table->count * columns; index++) {
        TH_WORK_STEP();
        ctx->padding_left += 3 * table->grid[index].len + 12;
    }
    Py_ssize_t padding = 0;
    for (Py_ssize_t column = 0; !table->has_header && column < columns; column++) {
        padding += table->widths[column]; /* the empty header row is all padding */
    }
    for (Py_ssize_t index = 0; index < table->count * columns && padding <= ctx->padding_left; index++) {
        padding += table->widths[index % columns] - table->grid[index].len;
    }
    if (padding > ctx->padding_left) {
        return 0;
    }
    ctx->padding_left -= padding;
    return 1;
}

/* Lay out the rendered cells as an aligned pipe grid: each column as wide as its
   widest cell (html2text's pad_tables). A table the padding budget does not cover is
   written whole as plain pipe rows, which GFM reads the same, so no table comes out
   aligned in part. */
static void md_emit_padded(md_ctx *ctx, md_table *table) {
    Py_ssize_t columns = table->columns;
    for (Py_ssize_t column = 0; column < columns; column++) {
        if (table->widths[column] < 3) {
            table->widths[column] = 3; /* the "---" separator needs at least three dashes */
        }
    }
    const Py_ssize_t *widths = md_padding_funded(ctx, table) ? table->widths : NULL;
    md_emit_padded_row(ctx, table->has_header ? table->grid : NULL, 0, columns, widths);
    md_newline(ctx);
    sbuf_putc(&ctx->out, '|');
    for (Py_ssize_t column = 0; column < columns; column++) {
        sbuf_putc(&ctx->out, ' ');
        for (Py_ssize_t pad = 0; pad < (widths != NULL ? widths[column] : 3); pad++) {
            sbuf_putc(&ctx->out, '-');
        }
        sbuf_puts(&ctx->out, " |");
    }
    ctx->line_has_content = 1;
    for (Py_ssize_t row = table->has_header ? 1 : 0; row < table->count; row++) {
        md_newline(ctx);
        md_emit_padded_row(ctx, table->grid, row, columns, widths);
    }
}

static void md_emit_separator_row(md_ctx *ctx, Py_ssize_t columns) {
    md_newline(ctx);
    sbuf_putc(&ctx->out, '|');
    for (Py_ssize_t column = 0; column < columns; column++) {
        sbuf_puts(&ctx->out, " --- |");
    }
    ctx->line_has_content = 1;
}

/* GFM pipe tables cannot encode merged cells, so spans require raw HTML. */
static void md_enter_table(md_ctx *ctx, th_node *node) {
    Py_ssize_t cap = 0;
    md_collect_rows(node, NULL, &cap, NULL);
    th_node **rows = PyMem_Malloc((size_t)(cap > 0 ? cap : 1) * sizeof(th_node *));
    if (rows == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    int has_spans = 0;
    Py_ssize_t columns = md_collect_rows(node, rows, &count, &has_spans);
    if (ctx->opt->table_mode == TH_MD_TABLE_HTML || (ctx->opt->table_mode == TH_MD_TABLE_MARKDOWN && has_spans)) {
        if (count > 0) { /* a collected row holds a cell, so there are columns too */
            md_block_line(ctx, 1);
            md_emit_raw_html(ctx, node);
        }
        PyMem_Free(rows);
        return;
    }
    md_table *table = PyMem_Calloc(1, sizeof(md_table));
    md_frame *frame = NULL;
    if (table != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        frame = md_push(ctx, node, MD_WALK_TABLE, MD_LEAVE_TABLE);
    }
    if (frame == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(table);   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(rows);    /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    table->rows = rows;
    table->count = count;
    table->columns = columns;
    frame->table = table;
    /* a wrap break inside a cell would corrupt the pipe grid, so the whole
       table renders unbreakable regardless of wrap_width */
    ctx->no_wrap++;
}

/* Once the captions are out, pick how the rows render. Returns 0 when the table has
   no grid to draw. */
static int md_table_layout(md_ctx *ctx, md_table *table) {
    if (table->count == 0) {
        return 0;
    }
    const md_opts *opt = ctx->opt;
    if (opt->table_mode == TH_MD_TABLE_STRIP) {
        /* drop the grid, keep each cell's text as a space-joined block per row */
        table->phase = MD_TABLE_STRIP;
        return 1;
    }
    /* the header is the first row when it reads as one (or always, when inferred);
       header="none" keeps every row in the body under an empty header */
    table->has_header = opt->table_header == TH_MD_HEADER_FIRST ||
                        (opt->table_header == TH_MD_HEADER_DETECT && md_row_is_header(table->rows[0]));
    md_block_line(ctx, 1);
    if (opt->pad_tables) {
        table->phase = MD_TABLE_PADDED;
        table->grid = PyMem_Calloc((size_t)(table->count * table->columns), sizeof(sbuf));
        table->widths = PyMem_Calloc((size_t)table->columns, sizeof(Py_ssize_t));
        if (table->grid == NULL || table->widths == NULL) { /* GCOVR_EXCL_BR_LINE: cannot force an allocation failure */
            ctx->out.failed = 1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
            return 0;                                       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return 1;
    }
    table->phase = MD_TABLE_ROWS;
    if (!table->has_header) {
        sbuf_putc(&ctx->out, '|');
        for (Py_ssize_t column = 0; column < table->columns; column++) {
            sbuf_puts(&ctx->out, "  |");
        }
        ctx->line_has_content = 1;
        md_emit_separator_row(ctx, table->columns);
    }
    return 1;
}

static int md_text_only(const th_node *node) {
    for (const th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_TEXT) {
            return 0;
        }
    }
    return 1;
}

static th_node *md_table_next_cell(md_table *table) {
    th_node *cell = table->cell == NULL ? table->rows[table->row]->first_child : table->cell->next_sibling;
    while (cell != NULL && (cell->type != TH_NODE_ELEMENT || (cell->atom != TH_TAG_TD && cell->atom != TH_TAG_TH))) {
        cell = cell->next_sibling;
    }
    table->cell = cell;
    return cell;
}

/* Advance a table to its next caption or cell, pushing the frame that renders it.
   Returns 0 once the table is complete. */
static int md_table_step(md_ctx *ctx, md_frame *frame) {
    md_table *table = frame->table;
    if (table->phase == MD_TABLE_CAPTIONS) {
        /* a pipe table has no caption row, so each <caption> renders as a paragraph
           above the grid, where CSS draws it (caption-side: top) wherever the source
           put it */
        th_node *child = frame->child == NULL ? frame->node->first_child : frame->child->next_sibling;
        while (child != NULL && (child->type != TH_NODE_ELEMENT || child->atom != TH_TAG_CAPTION)) {
            child = child->next_sibling;
        }
        if (child != NULL) {
            frame->child = child;
            md_push(ctx, child, MD_WALK_BLOCK, MD_LEAVE_NONE);
            return 1;
        }
        if (!md_table_layout(ctx, table)) {
            return 0;
        }
    }
    for (;;) {
        if (!table->in_row) {
            if (table->row == table->count) {
                if (table->phase == MD_TABLE_PADDED) {
                    md_emit_padded(ctx, table);
                }
                return 0;
            }
            if (table->phase == MD_TABLE_STRIP) {
                md_block_line(ctx, 1);
            } else if (table->phase == MD_TABLE_ROWS) {
                if (!table->has_header || table->row > 0) {
                    md_newline(ctx);
                }
                sbuf_putc(&ctx->out, '|');
            }
            table->in_row = 1;
            table->column = 0;
            table->cell = NULL;
        }
        th_node *cell = md_table_next_cell(table);
        if (cell == NULL) {
            if (table->phase == MD_TABLE_ROWS) {
                ctx->line_has_content = 1;
                if (table->has_header && table->row == 0) {
                    md_emit_separator_row(ctx, table->columns);
                }
            }
            table->in_row = 0;
            table->row++;
            continue;
        }
        if (table->phase == MD_TABLE_STRIP) {
            if (table->column++ > 0) {
                sbuf_putc(&ctx->out, ' ');
            }
        } else {
            if (table->phase == MD_TABLE_ROWS) {
                sbuf_putc(&ctx->out, ' ');
            }
            md_enter_cell(ctx, table);
        }
        if (cell->attr_count > 0 && md_render_whitespace(ctx, cell, TH_WS_NORMAL)) {
            if (table->phase != MD_TABLE_STRIP) {
                md_leave_cell(ctx, table);
            }
            continue;
        }
        if (!md_text_only(cell)) {
            md_frame *walk =
                md_push(ctx, cell, MD_WALK_INLINE, table->phase == MD_TABLE_STRIP ? MD_LEAVE_NONE : MD_LEAVE_CELL);
            if (walk != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                walk->table = table;
            }
            return 1;
        }
        /* a cell of text alone renders in place, with no frame of its own */
        for (th_node *child = cell->first_child; child != NULL; child = child->next_sibling) {
            md_emit_text(ctx, child, need_text(ctx->tree, child), child->text_len);
        }
        if (table->phase != MD_TABLE_STRIP) {
            md_leave_cell(ctx, table);
        }
    }
}

static void md_leave_table(md_ctx *ctx, md_table *table) {
    if (table->phase == MD_TABLE_ROWS || table->phase == MD_TABLE_PADDED) {
        ctx->table_end = ctx->out.len;
        ctx->table_prefix = ctx->prefix.len;
    }
    if (table->grid != NULL) {
        for (Py_ssize_t index = 0; index < table->count * table->columns; index++) {
            PyMem_Free(table->grid[index].data);
        }
    }
    PyMem_Free(table->grid);
    PyMem_Free(table->widths);
    PyMem_Free(table->rows);
    PyMem_Free(table);
    ctx->no_wrap--;
}

/* Emit preformatted text verbatim, restarting the line prefix at each newline so
   a fence indent or blockquote marker carries down every line. */
static void md_emit_pre_text(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t end) {
    for (Py_ssize_t index = 0; index < end; index++) {
        if (text[index] == '\n') {
            md_newline(ctx);
        } else {
            md_put_literal(ctx, text[index]);
            ctx->line_has_content = 1;
        }
    }
}

static const Py_UCS4 *md_code_language(md_ctx *ctx, th_node *node, Py_ssize_t *length) {
    Py_ssize_t class_len;
    const Py_UCS4 *classes = md_attr(ctx->tree, node, "class", &class_len);
    for (Py_ssize_t start = 0; start < class_len;) {
        if (is_space(classes[start])) {
            start++;
            continue;
        }
        Py_ssize_t end = start;
        while (end < class_len && !is_space(classes[end])) {
            end++;
        }
        static const Py_UCS4 prefix[] = {'l', 'a', 'n', 'g', 'u', 'a', 'g', 'e', '-'};
        /* a backtick fence's info string cannot hold a backtick (CommonMark 4.5): the line would open no code block
           and the verbatim code text would read as Markdown, so such a token names no language */
        if (end - start > 9 && memcmp(classes + start, prefix, sizeof(prefix)) == 0 &&
            md_max_backtick_run(classes + start + 9, end - start - 9) == 0) {
            *length = end - start - 9;
            return classes + start + 9;
        }
        start = end;
    }
    return NULL;
}

static th_node *md_pre_code(md_ctx *ctx, th_node *node) {
    th_node *code = NULL;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            if (text == NULL && child->text_len > 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                return NULL;                           /* GCOVR_EXCL_LINE: the walk reports the failure */
            }
            for (Py_ssize_t index = 0; index < child->text_len; index++) {
                if (!is_space(text[index])) {
                    return NULL;
                }
            }
        } else if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML && child->atom == TH_TAG_CODE &&
                   code == NULL) {
            code = child;
        } else {
            return NULL;
        }
    }
    return code;
}

static void md_render_pre_content(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t text_len, const Py_UCS4 *lang,
                                  Py_ssize_t lang_len);

static const Py_UCS4 *md_pre_language(md_ctx *ctx, th_node *node, th_node *code, Py_ssize_t *length) {
    const Py_UCS4 *language = code == NULL ? NULL : md_code_language(ctx, code, length);
    if (language != NULL && !md_clone_pays(ctx, code, *length)) {
        language = NULL; /* a cloned <code> repeats its class in every <pre> it is reopened in */
    }
    return language == NULL ? md_code_language(ctx, node, length) : language;
}

static void md_render_pre(md_ctx *ctx, th_node *node) {
    th_node *code = md_pre_code(ctx, node);
    th_node *content = code != NULL && code == node->first_child && code->next_sibling == NULL ? code : node;
    Py_ssize_t lang_len = 0;
    const Py_UCS4 *lang = md_pre_language(ctx, node, code, &lang_len);
    Py_ssize_t text_len;
    Py_UCS4 *text;
    th_node *first = content->first_child;
    if (first == NULL || (first->type == TH_NODE_TEXT && first->next_sibling == NULL)) {
        text = th_node_text(ctx->tree, content, &text_len);
        if (text == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    } else {
        sbuf code_text = {0};
        md_collect_code_text(ctx->tree, content, &code_text, '\n');
        if (code_text.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->out.failed = 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(code_text.data); /* GCOVR_EXCL_LINE: allocation-failure path */
            return;                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        text_len = code_text.len;
        text = code_text.data;
    }
    md_render_pre_content(ctx, text, text_len, lang, lang_len);
    PyMem_Free(text);
}

/* Whether a reader can still be inside the list that closed last, whatever was laid out since. An item marker written
   after it ends it, and so do a blank line and then a line of something else: with nothing after the blank line the
   list is still open, and without the blank line that line can read as the last item's lazy continuation
   (CommonMark 5.1). Where the list ends is kept, so the blocks after it do not each scan up to there. */
static int md_list_lingers(md_ctx *ctx) {
    if (ctx->list_end_marker == 0 || ctx->list_over > ctx->list_end) {
        return 0;
    }
    Py_ssize_t index = ctx->list_end;
    /* an emphasis run the list sat in closes on the last item's own line */
    while (index < ctx->out.len && ctx->out.data[index] != '\n') {
        index++;
    }
    /* the line under it is blank when it holds a trimmed line prefix alone */
    for (index++; index < ctx->out.len && ctx->out.data[index] != '\n'; index++) {
        if (ctx->out.data[index] != ' ' && ctx->out.data[index] != '>') {
            return 1;
        }
    }
    for (; index < ctx->out.len; index++) {
        Py_UCS4 ch = ctx->out.data[index];
        if (ch != '\n' && ch != ' ' && ch != '>') {
            ctx->list_over = index + 1;
            return 0;
        }
    }
    return 1;
}

/* Whether a reader takes four-space lines as code where this block stands; a fence is read as code anywhere. Under a
   list a reader still holds open, the indent continues the last item as a paragraph (CommonMark 5.2), as it does under
   an item nested too deep for the prefix to indent. An indented code block cannot start with a blank line either (4.4),
   and behind a list marker that line leaves the item empty, which cannot interrupt a paragraph (5.2). */
static int md_code_can_indent(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t end) {
    if (ctx->indent_levels > TH_MAX_INDENT_LEVELS || md_list_lingers(ctx)) {
        return 0;
    }
    Py_ssize_t index = 0;
    while (index < end && text[index] != '\n' && is_space(text[index])) {
        index++;
    }
    return index < end && text[index] != '\n';
}

/* Whether the line just opened holds its prefix alone under a blank line or at the head of the output, going by the
   text written rather than the layout state: a quote that follows another in a tight item opens its own line, yet
   continues the quote on the line above it. */
static int md_under_blank_line(md_ctx *ctx) {
    int lines = 0;
    for (Py_ssize_t index = ctx->out.len; index > 0 && lines < 2; index--) {
        Py_UCS4 last = ctx->out.data[index - 1];
        if (last == '\n') {
            lines++;
        } else if (last != ' ' && last != '>') {
            return 0;
        }
    }
    return 1;
}

static void md_render_pre_content(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t text_len, const Py_UCS4 *lang,
                                  Py_ssize_t lang_len) {
    const md_opts *opt = ctx->opt;
    /* drop one trailing newline so the close is not preceded by a blank line */
    Py_ssize_t end = text_len;
    if (end > 0 && text[end - 1] == '\n') {
        end--;
    }
    /* the text is written unescaped, so it takes the four-space style only where a reader takes that as code */
    int indented = opt->code_mark_open == NULL && opt->code_block_style == TH_MD_CODE_INDENTED &&
                   md_code_can_indent(ctx, text, end);
    if (indented && ctx->line_has_content) {
        /* an indented code block cannot interrupt a paragraph (CommonMark 4.4): it would be its continuation text */
        ctx->interrupt_blank = 1;
    }
    md_block_line(ctx, 1);
    /* a block that opens a list item is fenced too: its marker line may itself continue a paragraph above it */
    if (indented && !md_under_blank_line(ctx)) {
        indented = 0;
    }
    if (opt->code_mark_open != NULL) {
        /* html2text's [code]...[/code] markers in place of a fence */
        md_puts8(&ctx->out, opt->code_mark_open);
        ctx->line_has_content = 1;
        md_newline(ctx);
        md_emit_pre_text(ctx, text, end);
        md_newline(ctx);
        md_puts8(&ctx->out, opt->code_mark_close);
        ctx->line_has_content = 1;
    } else if (indented) {
        /* indent every line by four spaces instead of fencing it */
        Py_ssize_t base = ctx->prefix.len;
        sbuf_puts(&ctx->prefix, "    ");
        sbuf_puts(&ctx->out, "    ");
        ctx->line_has_content = 1;
        md_emit_pre_text(ctx, text, end);
        ctx->prefix.len = base;
    } else {
        Py_ssize_t fence = md_max_backtick_run(text, text_len);
        fence = fence + 1 > 3 ? fence + 1 : 3;
        for (Py_ssize_t index = 0; index < fence; index++) {
            sbuf_putc(&ctx->out, '`');
        }
        if (lang != NULL) {
            TH_WORK_STEPS_ADD(lang_len);
            sbuf_put_run(&ctx->out, lang, lang_len);
        } else if (strchr(opt->code_language, '`') == NULL) {
            md_puts8(&ctx->out, opt->code_language);
        }
        ctx->line_has_content = 1;
        md_newline(ctx);
        /* an empty body takes no content line, since a blank one reads as a newline of code */
        if (text_len > 0) {
            md_emit_pre_text(ctx, text, end);
            md_newline(ctx);
        }
        for (Py_ssize_t index = 0; index < fence; index++) {
            sbuf_putc(&ctx->out, '`');
        }
        ctx->line_has_content = 1;
    }
}

/* The setext heading that has written none of its text yet, or NULL. The innermost heading is the one being written,
   unless a converter inside it renders into a buffer of its own. */
static md_frame *md_unstarted_setext(md_ctx *ctx) {
    for (Py_ssize_t index = ctx->frame_count; index-- > 0;) { /* GCOVR_EXCL_BR_LINE: in_heading has a frame */
        md_frame *frame = &ctx->frames[index];
        if (frame->leave == MD_LEAVE_SETEXT && frame->heading.mark == ctx->out.len) {
            return frame;
        }
        if (frame->leave == MD_LEAVE_SETEXT || frame->leave == MD_LEAVE_ATX || frame->leave == MD_LEAVE_CONVERT) {
            break;
        }
    }
    return NULL;
}

/* Get ready to write the inline fragment of md_render_whitespace. A line that starts with <pre is an HTML block
   (CommonMark 4.6, start condition 1): it interrupts a paragraph and takes its whole line as raw HTML, the escaped
   text after </pre> included. A setext heading that has written nothing is written as an ATX one, as an empty one is,
   and a hard break just written is spelled <br> and the line goes on, as in a cell. A heading holds no hard break, and
   a cell or converter starts with no record, so a break written to another output never matches. Converter output
   that ends in a line feed leaves the fragment at a line start, so it is returned as a span: a span is not an HTML
   block start. */
static int md_before_inline_pre(md_ctx *ctx) {
    ctx->no_wrap++;
    md_before_visible(ctx);
    ctx->no_wrap--;
    if (ctx->in_heading) {
        md_frame *heading = md_unstarted_setext(ctx);
        if (heading != NULL) {
            for (int index = 0; index < heading->heading.level; index++) {
                sbuf_putc(&ctx->out, '#');
            }
            sbuf_putc(&ctx->out, ' ');
            heading->heading.mark = ctx->out.len;
            heading->leave = MD_LEAVE_ATX;
        }
    } else if (ctx->out.len == ctx->break_end) {
        ctx->out.len = ctx->break_start;
        ctx->break_end = -1; /* the record is spent: what is written next may reach its old end again */
        sbuf_puts(&ctx->out, "<br>");
    }
    return ctx->out.len > 0 && ctx->out.data[ctx->out.len - 1] == '\n';
}

static int md_render_whitespace(md_ctx *ctx, th_node *node, int inherited) {
    int mode = md_css_whitespace(ctx, node, inherited);
    if (mode == TH_WS_NORMAL || is_md_skipped(node)) {
        return 0;
    }
    sbuf text = {0};
    int inline_modes[MD_INLINE_FRAMES];
    int *modes = inline_modes;
    Py_ssize_t capacity = MD_INLINE_FRAMES;
    Py_ssize_t depth = 0;
    int pending = 0;
    int boundary = 0;
    th_node *parent = node;
    th_node *child = node->first_child;
    for (;;) {
        while (child == NULL) {
            if (parent == node) {
                goto done;
            }
            boundary |= md_code_kind(parent) == MD_CODE_LINE;
            child = parent->next_sibling;
            parent = parent->parent;
            mode = modes[--depth];
        }
        if (child->type == TH_NODE_TEXT && child->text_len > 0) {
            const Py_UCS4 *data = need_text(ctx->tree, child);
            if (data == NULL) {  /* GCOVR_EXCL_BR_LINE: lazy text OOM */
                text.failed = 1; /* GCOVR_EXCL_LINE: allocation failure */
                goto done;       /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (boundary) {
                md_code_boundary(&text, '\n', data[0]);
                boundary = 0;
                pending = 0;
            }
            for (Py_ssize_t index = 0; index < child->text_len; index++) {
                Py_UCS4 character = data[index];
                if (mode != TH_WS_PRESERVE && is_space(character)) {
                    if (mode == TH_WS_LINES && character == '\n') {
                        sbuf_putc(&text, '\n');
                        pending = 0;
                    } else {
                        pending = 1;
                    }
                    continue;
                }
                if (pending && text.len > 0 && text.data[text.len - 1] != '\n') {
                    sbuf_putc(&text, ' ');
                }
                pending = 0;
                sbuf_putc(&text, character);
            }
        } else if (child->type == TH_NODE_ELEMENT) {
            int kind = md_code_kind(child);
            if (kind == MD_CODE_BREAK) {
                sbuf_putc(&text, '\n');
                pending = 0;
                boundary = 0;
            } else if (kind != MD_CODE_SKIPPED) {
                if (depth == capacity) {
                    int grown = md_grow_stack(ctx, (void **)&modes, &capacity, inline_modes, sizeof(int));
                    if (grown < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                        goto done;   /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                }
                modes[depth++] = mode;
                mode = md_css_whitespace(ctx, child, child->atom == TH_TAG_PRE ? TH_WS_PRESERVE : mode);
                boundary |= kind == MD_CODE_LINE;
                parent = child;
                child = child->first_child;
                continue;
            }
        }
        child = child->next_sibling;
    }
done:
    if (modes != inline_modes) {
        PyMem_Free(modes);
    }
    ctx->out.failed |= text.failed;
    if (ctx->in_cell || ctx->inline_only || ctx->in_heading) {
        int at_line_start = md_before_inline_pre(ctx);
        sbuf_puts(&ctx->out, at_line_start ? "<span style=\"white-space:pre\">" : "<pre>");
        for (Py_ssize_t index = 0; index < text.len; index++) {
            Py_UCS4 character = text.data[index];
            if (is_space(character) || md_is_ascii_punct(character)) {
                sbuf_puts(&ctx->out, "&#");
                md_put_decimal(&ctx->out, character);
                sbuf_putc(&ctx->out, ';');
            } else {
                sbuf_putc(&ctx->out, character);
            }
        }
        sbuf_puts(&ctx->out, at_line_start ? "</span>" : "</pre>");
        ctx->line_has_content = 1;
    } else {
        static const Py_UCS4 empty_language[] = {0};
        Py_ssize_t language_len = 0;
        const Py_UCS4 *language = node->atom == TH_TAG_PRE
                                      ? md_pre_language(ctx, node, md_pre_code(ctx, node), &language_len)
                                      : empty_language;
        md_render_pre_content(ctx, text.data, text.len, language, language_len);
        ctx->block_ended = 1;
    }
    PyMem_Free(text.data);
    return 1;
}

static void md_render_block(md_ctx *ctx, th_node *node) {
    if (md_apply_converter(ctx, node)) {
        return;
    }
    if (node->attr_count > 0 && md_render_whitespace(ctx, node, TH_WS_NORMAL)) {
        return;
    }
    /* only an HTML element is ever classified as a block, so the namespace check
       the callers already made guarantees node is HTML here */
    uint16_t atom = node->atom;
    if (md_tag_filtered(ctx->opt, atom)) {
        /* drop the block's own markup but keep laying its children out as blocks */
        md_push(ctx, node, MD_WALK_BLOCK, MD_LEAVE_NONE);
        return;
    }
    if (ctx->in_cell && md_is_cell_block(atom)) {
        /* GFM 4.10 excludes nested blocks from pipe cells; retain HTML or collapse
           their layout while keeping list-item boundaries visible. */
        if (ctx->opt->cell_blocks == TH_MD_CELL_HTML) {
            md_emit_cell_html(ctx, node);
        } else {
            md_enter_cell_flat(ctx, node, -1);
        }
        return;
    }
    switch (atom) {
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6: {
        md_block_line(ctx, 1);
        int level = atom - TH_TAG_H1 + 1;
        int setext = ctx->opt->heading_style == TH_MD_HEADING_SETEXT && level <= 2;
        if (!setext) {
            for (int index = 0; index < level; index++) {
                sbuf_putc(&ctx->out, '#');
            }
            sbuf_putc(&ctx->out, ' ');
            ctx->line_has_content = 1;
        }
        ctx->drop_space = 1;
        md_frame *frame = md_push(ctx, node, MD_WALK_INLINE, setext ? MD_LEAVE_SETEXT : MD_LEAVE_ATX);
        if (frame != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            frame->heading.mark = ctx->out.len;
            frame->heading.level = level;
            frame->heading.outer_in_heading = ctx->in_heading;
        }
        ctx->in_heading = 1;
        return;
    }
    case TH_TAG_HR:
        md_block_line(ctx, 1);
        sbuf_puts(&ctx->out, "---");
        ctx->line_has_content = 1;
        return;
    case TH_TAG_UL:
    case TH_TAG_MENU:
    case TH_TAG_OL:
        md_enter_list(ctx, node);
        return;
    case TH_TAG_PRE:
        md_render_pre(ctx, node);
        return;
    case TH_TAG_TABLE:
        md_enter_table(ctx, node);
        return;
    case TH_TAG_BLOCKQUOTE: {
        if (ctx->indent_levels == 0 && md_nesting_exceeds(node, 0, TH_MAX_INDENT_LEVELS - 1)) {
            md_block_line(ctx, 0);
            md_emit_raw_html(ctx, node);
            return;
        }
        /* the quote opens its lines here rather than through md_block_line, so it closes
           the runs before it (CommonMark 6.2 pairs them within one block) and takes the
           block start an inner block left pending */
        md_suspend_markers(ctx);
        ctx->block_ended = 0;
        Py_ssize_t base = ctx->prefix.len;
        int saved_levels = ctx->indent_levels;
        const char *marker = ++ctx->indent_levels <= TH_MAX_INDENT_LEVELS ? "> " : "";
        if (ctx->started) {
            /* close the previous block and open the separator with the OUTER
               prefix, so the blank line is not itself quoted, before adding the
               "> " that every line inside the quote carries; an HTML block before
               the quote ends at that blank line alone, so a tight item writes it too */
            if ((!ctx->tight && !ctx->item_marker) || md_after_table(ctx) || md_line_opens_html(ctx)) {
                sbuf_putc(&ctx->out, '\n');
                md_write_blank_prefix(ctx);
            }
            sbuf_putc(&ctx->out, '\n');
            ctx->item_marker = 0;
            sbuf_puts(&ctx->prefix, marker);
            md_write_prefix(ctx);
            ctx->line_has_content = 0;
            ctx->space_pending = 0;
            ctx->drop_space = 1;
            ctx->pending_loose = 1;
            ctx->suppress_break = 1;
        } else {
            sbuf_puts(&ctx->prefix, marker);
        }
        int saved_tight = ctx->tight;
        ctx->tight = 0;
        md_frame *frame = md_push(ctx, node, MD_WALK_BLOCK, MD_LEAVE_BLOCKQUOTE);
        if (frame != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            frame->prefix_base = base;
            frame->saved_tight = saved_tight;
            frame->saved_levels = saved_levels;
        }
        return;
    }
    default:
        md_push(ctx, node, MD_WALK_BLOCK, MD_LEAVE_NONE);
        return;
    }
}

/* Close a heading: a setext one underlines its text with a run of '=' (h1) or '-'
   (h2) as wide as the text; an ATX one closes with its '#' run or escapes a trailing
   one that would read as a closing sequence. */
static void md_leave_heading(md_ctx *ctx, md_frame *frame) {
    ctx->in_heading = frame->heading.outer_in_heading;
    Py_ssize_t mark = frame->heading.mark;
    int level = frame->heading.level;
    if (frame->leave == MD_LEAVE_SETEXT) {
        Py_ssize_t width = ctx->out.len - mark;
        if (width == 0) {
            sbuf_puts(&ctx->out, level == 1 ? "#" : "##");
            ctx->line_has_content = 1;
            return;
        }
        if (md_line_opens_html(ctx)) {
            return; /* the underline would only be one more line of the HTML block the text opens */
        }
        md_newline(ctx);
        Py_UCS4 rule = level == 1 ? '=' : '-';
        for (Py_ssize_t index = 0; index < width; index++) {
            sbuf_putc(&ctx->out, rule);
        }
        ctx->line_has_content = 1;
        return;
    }
    if (ctx->opt->heading_style == TH_MD_HEADING_ATX_CLOSED) {
        sbuf_putc(&ctx->out, ' ');
        for (int index = 0; index < level; index++) {
            sbuf_putc(&ctx->out, '#');
        }
        return;
    }
    /* a trailing `#` run after a space reads as the optional closing sequence and
       vanishes; escaping its first `#` keeps it text. The run is all `#`, so the
       backslash overwrites its head and one more `#` restores its length. */
    Py_ssize_t run = ctx->out.len;
    while (run > mark && ctx->out.data[run - 1] == '#') {
        run--;
    }
    if (ctx->escape_prose && run < ctx->out.len && (run == mark || ctx->out.data[run - 1] == ' ')) {
        ctx->out.data[run] = '\\';
        sbuf_putc(&ctx->out, '#');
    }
}

static void md_leave(md_ctx *ctx) {
    md_frame *frame = &ctx->frames[--ctx->frame_count];
    if (frame->leave == MD_LEAVE_NONE && frame->prefix_base < 0) {
        return;
    }
    switch (frame->leave) {
    case MD_LEAVE_WRAP: {
        if (frame->marker >= 0) { /* GCOVR_EXCL_BR_LINE: -1 only on an allocation failure */
            md_settle_run(ctx, frame->marker, frame->node);
        }
        const char *close = md_pop_marker(ctx, frame->marker);
        if (close != NULL) {
            md_put_close(ctx, close);
        }
        break;
    }
    case MD_LEAVE_QUOTE:
        if (ctx->block_ended && ctx->opt->quote_close[0] != '\0') {
            /* after an inner block the mark opens the next one, since on that block's last line it would spoil
               a closing fence; only then, because a space owed after inline content belongs outside the mark */
            md_before_visible(ctx);
        }
        md_puts8(&ctx->out, ctx->opt->quote_close);
        break;
    case MD_LEAVE_LINK:
        md_leave_link(ctx, frame);
        break;
    case MD_LEAVE_RAW_LINK:
        sbuf_puts(&ctx->out, "</a>");
        ctx->line_has_content = 1;
        ctx->inline_only--;
        break;
    case MD_LEAVE_NO_WRAP:
        ctx->no_wrap--;
        break;
    case MD_LEAVE_GOOGLE:
        md_leave_google(ctx, frame);
        break;
    case MD_LEAVE_SETEXT:
    case MD_LEAVE_ATX:
        md_leave_heading(ctx, frame);
        break;
    case MD_LEAVE_ITEM:
        if (!ctx->opt->wrap_list_items) {
            ctx->no_wrap--;
        }
        break;
    case MD_LEAVE_LIST:
        md_leave_list(ctx, frame);
        break;
    case MD_LEAVE_CONVERT:
        md_leave_converter(ctx, frame);
        break;
    case MD_LEAVE_CELL:
        md_leave_cell(ctx, frame->table);
        break;
    case MD_LEAVE_TABLE:
        md_leave_table(ctx, frame->table);
        break;
    case MD_LEAVE_BLOCK_END:
        ctx->block_ended = 1;
        break;
    case MD_LEAVE_SPACE:
        ctx->space_pending = 1;
        break;
    case MD_LEAVE_BLOCKQUOTE:
        /* a leading quote writes its marker only with its first block; CommonMark
           reads a lone ">" as an empty quote, so keep one when no block came */
        if (!ctx->started) {
            ctx->started = 1;
            md_write_blank_prefix(ctx);
            ctx->pending_loose = 1;
        }
        break;
    default:
        break;
    }
    if (frame->prefix_base >= 0) {
        ctx->tight = frame->saved_tight;
        ctx->indent_levels = frame->saved_levels;
        ctx->prefix.len = frame->prefix_base;
        /* a list item or block quote defers its marker line's break for the first
           block to ride; an empty one emits no such block, so the pending break has
           to be released here or the next block attaches to the marker */
        ctx->suppress_break = 0;
        ctx->item_marker = 0;
    }
}

/* Render the pushed frames to completion. Each step renders the next child of the
   innermost frame, which may push frames of its own, so the C stack stays flat at any
   nesting depth. The next child is read only once the previous one is done, as a
   recursive walk would, so a converter that edits the tree sees the same walk. */
static void md_run(md_ctx *ctx) {
    while (ctx->frame_count > 0 && !ctx->tree->failed && !ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: OOM */
        md_frame *frame = &ctx->frames[ctx->frame_count - 1];
        th_node *child = NULL;
        if (frame->walk == MD_WALK_TABLE) {
            if (md_table_step(ctx, frame)) {
                continue;
            }
        } else if (frame->walk != MD_WALK_NONE) {
            child = frame->child == NULL ? frame->node->first_child : frame->child->next_sibling;
            /* text lays out without running Python or pushing a frame, so a run of it renders in place */
            if (frame->walk == MD_WALK_INLINE) {
                for (; child != NULL && child->type == TH_NODE_TEXT && !ctx->tree->failed; /* GCOVR_EXCL_BR_LINE: OOM */
                     child = child->next_sibling) {
                    md_emit_text(ctx, child, need_text(ctx->tree, child), child->text_len);
                }
            } else if (frame->walk == MD_WALK_BLOCK) {
                for (; child != NULL && child->type == TH_NODE_TEXT && !ctx->tree->failed; /* GCOVR_EXCL_BR_LINE: OOM */
                     child = child->next_sibling) {
                    md_block_child(ctx, child, &frame->in_run);
                }
            }
        }
        if (ctx->tree->failed) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
            break;               /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (child == NULL) {
            md_leave(ctx);
            continue;
        }
        frame->child = child;
        switch (frame->walk) {
        case MD_WALK_INLINE:
            md_render_inline(ctx, child);
            break;
        case MD_WALK_BLOCK:
            md_block_child(ctx, child, &frame->in_run);
            break;
        case MD_WALK_LIST:
            md_list_child(ctx, ctx->frame_count - 1, child);
            break;
        case MD_WALK_WRAPPER:
            md_list_child(ctx, frame->owner, child);
            break;
        default:
            md_cell_flat_child(ctx, frame->flat.owner, child);
            break;
        }
    }
}

/* Append the collected reference definitions ("[n]: url \"title\"") after the
   body, one per line, when link_style is reference. */
static void md_flush_references(md_ctx *ctx) {
    if (ctx->ref_count == 0) {
        return;
    }
    sbuf_puts(&ctx->out, "\n\n");
    for (Py_ssize_t index = 0; index < ctx->ref_count; index++) {
        if (index > 0) {
            sbuf_putc(&ctx->out, '\n');
        }
        sbuf_putc(&ctx->out, '[');
        md_put_decimal(&ctx->out, index + 1);
        sbuf_puts(&ctx->out, "]: ");
        md_emit_url(ctx, "", ctx->refs[index].url, ctx->refs[index].url_len);
        if (ctx->refs[index].title != NULL) {
            sbuf_puts(&ctx->out, " \"");
            md_emit_title(ctx, ctx->refs[index].title, ctx->refs[index].title_len);
            sbuf_putc(&ctx->out, '"');
        }
    }
}

static int md_inherited_whitespace(md_ctx *ctx, th_node *node) {
    for (th_node *parent = node->parent; parent != NULL; parent = parent->parent) {
        if (parent->type == TH_NODE_ELEMENT) {
            int mode = md_css_whitespace(ctx, parent, -1);
            if (mode >= 0) {
                return mode;
            }
            if (parent->atom == TH_TAG_PRE) {
                return TH_WS_PRESERVE;
            }
        }
    }
    return TH_WS_NORMAL;
}

Py_UCS4 *th_node_markdown(th_tree *tree, th_node *node, const md_opts *opt, void **ordinals, Py_ssize_t *out_len) {
    md_frame inline_frames[MD_INLINE_FRAMES];
    md_marker inline_markers[MD_INLINE_MARKERS];
    md_ctx ctx = {0};
    ctx.tree = tree;
    ctx.opt = opt;
    ctx.ordinals = ordinals;
    ctx.frames = ctx.inline_frames = inline_frames;
    ctx.frame_cap = MD_INLINE_FRAMES;
    ctx.markers = ctx.inline_markers = inline_markers;
    ctx.marker_cap = MD_INLINE_MARKERS;
    ctx.break_end = -1;
    ctx.table_end = -1;
    /* a few short targets repeat in clones before any rendered text funds the budget */
    ctx.clone_budget = TH_CLONE_ALLOWANCE;
    /* Let a few deep runs reopen in full before source text has to fund them. */
    ctx.tags_left = 256;
    /* The first tables may align on this allowance before any cells have funded padding. */
    ctx.padding_left = 256;
    ctx.raw_emphasis = opt->keep_emphasis && md_needs_raw_emphasis(tree, node);
    /* mode="none" leaves prose as written apart from the asterisk and underscore
       choices, keeping only the cell pipe escape a pipe table cannot do without */
    ctx.escape_prose = opt->escape_mode != TH_MD_ESCAPE_NONE;
    ctx.escape_mask = (ctx.escape_prose ? MD_CH_ESCAPE : 0) | (opt->escape_asterisks ? MD_CH_ASTERISK : 0) |
                      (opt->escape_underscores ? MD_CH_UNDERSCORE : 0) |
                      (opt->escape_mode == TH_MD_ESCAPE_ALL ? MD_CH_ALL : 0);
    ctx.run_stop = ctx.escape_mask | (ctx.escape_prose ? MD_CH_CONTEXT : 0) | MD_CH_SPACE;
    sbuf_presize_for_root(&ctx.out, tree, node);
    if (node->type == TH_NODE_TEXT) {
        ctx.started = 1;
        ctx.line_has_content = 1;
        md_emit_text(&ctx, NULL, need_text(tree, node), node->text_len);
    } else if (md_apply_converter(&ctx, node) ||
               (node->type == TH_NODE_ELEMENT &&
                md_render_whitespace(&ctx, node, md_inherited_whitespace(&ctx, node)))) {
    } else if (node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && node->atom == TH_TAG_LI) {
        md_render_root_item(&ctx, node);
    } else if (is_md_block(node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN)) {
        md_render_block(&ctx, node);
    } else {
        md_push(&ctx, node, MD_WALK_BLOCK, MD_LEAVE_NONE);
    }
    md_run(&ctx);
    md_flush_references(&ctx);
    if (ctx.frames != inline_frames) {
        PyMem_Free(ctx.frames);
    }
    if (ctx.markers != inline_markers) {
        PyMem_Free(ctx.markers);
    }
    /* the line prefix fails apart from the output buffer */
    ctx.out.failed |= ctx.prefix.failed | tree->failed;
    PyMem_Free(ctx.prefix.data);
    PyMem_Free(ctx.refs);
    if (ctx.failed) {
        PyMem_Free(ctx.out.data);
        return NULL;
    }
    md_trim(&ctx.out, opt->document_strip);
    if (ctx.out.len > 0 && ctx.out.data[0] == 0xFEFF) {
        /* Some Markdown readers consume a leading BOM as an input signature. */
        sbuf_reserve(&ctx.out, 7);
        if (!ctx.out.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            const Py_UCS4 reference[] = {'&', '#', '6', '5', '2', '7', '9', ';'};
            memmove(&ctx.out.data[8], &ctx.out.data[1], (size_t)(ctx.out.len - 1) * sizeof(Py_UCS4));
            memcpy(ctx.out.data, reference, sizeof(reference));
            ctx.out.len += 7;
        }
    }
    return sbuf_finish(&ctx.out, out_len);
}
