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
    th_node *node;   /* the wrapped element when it has an HTML fallback, else NULL */
    uint8_t html;    /* enum md_html */
    uint8_t flags;   /* MD_MARK_* */
    uint8_t emitted; /* the open run was written, so the close run must be too */
} md_marker;

/* The layout state one list threads through its items, including the items it
   finds inside a wrapper element it looks through. */
typedef struct {
    Py_ssize_t number;     /* the next ordered item's number */
    Py_ssize_t sub_indent; /* how far content attached to the last item indents: its marker's width */
    char bullet;
    char delimiter; /* what follows an ordered item's number */
    int ordered;
    int loose;
    int item_seen; /* an item has opened, so content between items continues it */
    int in_run;    /* content between items is in the middle of an inline run */
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
    MD_LEAVE_NO_WRAP,
    MD_LEAVE_GOOGLE, /* close the CSS-derived markers and restore the inherited style */
    MD_LEAVE_SETEXT,
    MD_LEAVE_ATX,
    MD_LEAVE_ITEM,
    MD_LEAVE_LIST,
    MD_LEAVE_CONVERT, /* hand the rendered children to the converter */
    MD_LEAVE_CELL,    /* collapse the cell onto its row */
    MD_LEAVE_TABLE,
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
    int saved_space;
    int saved_drop;
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
    int space_pending;
    int drop_space;
    int pending_loose;
    int suppress_break;
    int tight;
    int list_depth;
    int g_bold;
    int g_italic;
    int inline_only;
    char list_end_marker;
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
    sbuf prefix;      /* what every continuation line starts with: list indent + "> " quotes */
    md_frame *frames; /* the elements being rendered, outermost first */
    md_frame *inline_frames;
    Py_ssize_t frame_count;
    Py_ssize_t frame_cap;
    md_marker *markers; /* the inline markers still open, outermost first */
    md_marker *inline_markers;
    Py_ssize_t marker_count;
    Py_ssize_t marker_cap;
    Py_ssize_t marker_base; /* markers below it belong to the output a cell or converter set aside */
    md_marker *pending;     /* the innermost marker above marker_base, NULL when there is none */
    md_reference *refs;     /* collected reference-link targets */
    Py_ssize_t ref_count;
    Py_ssize_t ref_cap;
    int started;          /* has any block content been emitted yet */
    int line_has_content; /* real content past the prefix/marker on the current line */
    Py_ssize_t line_start;
    Py_ssize_t line_checked;
    int space_pending;          /* a collapsed-away whitespace run is owed one space */
    int pending_word;           /* code points in the word the owed space precedes, for greedy wrapping */
    int no_wrap;                /* >0 inside verbatim/grid/unbreakable content: never insert a wrap break */
    int inline_only;            /* >0 inside link text: a block flattens to inline, never opens a line */
    int in_cell;                /* inside a table cell: a pipe is escaped as it is written, a block turns into HTML */
    int drop_space;             /* swallow the next pending space (block/inline start) without emitting */
    int pending_loose;          /* the previous block wants a blank line after it */
    int suppress_break;         /* the next block attaches to the current (list marker) line */
    int tight;                  /* inside a list item: inline runs do not add blank lines */
    int list_depth;             /* nesting depth of the current list, for bullet cycling */
    int indent_levels;          /* list and quote nesting that indents the prefix, capped by TH_MAX_INDENT_LEVELS */
    Py_ssize_t list_end;        /* output length when the last list closed, to spot a list right after it */
    Py_ssize_t list_end_prefix; /* the prefix length that list was laid out under */
    char list_end_marker;       /* its bullet or ordered delimiter; 0 until a list closes */
    int g_bold;                 /* google_doc: a CSS font-weight bold is in force from an ancestor */
    int g_italic;               /* google_doc: a CSS font-style italic is in force from an ancestor */
    int failed;                 /* a reference buffer allocation failed */
    int escape_prose;           /* escape what a reader would parse in prose; off under Escaping(mode="none") */
    uint8_t escape_mask;        /* the MD_ASCII classes the options escape */
    uint8_t run_stop;           /* the MD_ASCII classes that end a bulk-copied run */
} md_ctx;

/* Emit a configured option string, which may hold non-ASCII (a typographic
   quote, a unicode bullet), decoding its UTF-8 to code points. */
static void md_puts8(sbuf *out, const char *text) {
    sbuf_put_utf8(out, text, (Py_ssize_t)strlen(text));
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

/* Position the cursor at the start of a fresh, prefixed line for the next block,
   collapsing the margin with the previous block (a blank line when either side
   is loose). loose marks whether this block wants blank lines around it. */
static void md_block_line(md_ctx *ctx, int loose) {
    if (!ctx->started) {
        ctx->started = 1;
        md_write_prefix(ctx);
    } else if (ctx->suppress_break) {
        ctx->suppress_break = 0;
    } else {
        sbuf_putc(&ctx->out, '\n');
        if ((ctx->pending_loose || loose) && !ctx->tight && !ctx->opt->block_spacing_single) {
            md_write_blank_prefix(ctx);
            sbuf_putc(&ctx->out, '\n');
        }
        md_write_prefix(ctx);
        ctx->line_has_content = 0;
    }
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
    if (ctx->opt->wrap_width > 0 && ctx->no_wrap == 0 && md_line_column(ctx) + 1 + word > ctx->opt->wrap_width) {
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
static const uint8_t MD_EDGE[128] = {
    ['\t'] = MD_EDGE_SPACE, ['\n'] = MD_EDGE_SPACE, ['\f'] = MD_EDGE_SPACE, ['\r'] = MD_EDGE_SPACE,
    [' '] = MD_EDGE_SPACE,  ['!'] = MD_EDGE_PUNCT,  ['"'] = MD_EDGE_PUNCT,  ['#'] = MD_EDGE_PUNCT,
    ['$'] = MD_EDGE_PUNCT,  ['%'] = MD_EDGE_PUNCT,  ['&'] = MD_EDGE_PUNCT,  ['\''] = MD_EDGE_PUNCT,
    ['('] = MD_EDGE_PUNCT,  [')'] = MD_EDGE_PUNCT,  ['*'] = MD_EDGE_PUNCT,  ['+'] = MD_EDGE_PUNCT,
    [','] = MD_EDGE_PUNCT,  ['-'] = MD_EDGE_PUNCT,  ['.'] = MD_EDGE_PUNCT,  ['/'] = MD_EDGE_PUNCT,
    [':'] = MD_EDGE_PUNCT,  [';'] = MD_EDGE_PUNCT,  ['<'] = MD_EDGE_PUNCT,  ['='] = MD_EDGE_PUNCT,
    ['>'] = MD_EDGE_PUNCT,  ['?'] = MD_EDGE_PUNCT,  ['@'] = MD_EDGE_PUNCT,  ['['] = MD_EDGE_PUNCT,
    ['\\'] = MD_EDGE_PUNCT, [']'] = MD_EDGE_PUNCT,  ['^'] = MD_EDGE_PUNCT,  ['_'] = MD_EDGE_PUNCT,
    ['`'] = MD_EDGE_PUNCT,  ['{'] = MD_EDGE_PUNCT,  ['|'] = MD_EDGE_PUNCT,  ['}'] = MD_EDGE_PUNCT,
    ['~'] = MD_EDGE_PUNCT,
};

static inline uint8_t md_edge(Py_UCS4 ch) {
    return ch < 128 ? MD_EDGE[ch] : 0;
}

static int md_is_ascii_punct(Py_UCS4 ch) {
    return md_edge(ch) == MD_EDGE_PUNCT;
}

static const char *md_alternate_run(md_ctx *ctx, const md_marker *marker);

/* Whether marker[index] must fall back to raw inline HTML because its Markdown
   delimiter would not survive a round-trip. Three ways it fails: it touches an
   identical delimiter, so the two runs merge into one, unless the other emphasis
   character can take its place; a ~~ run nests directly in another, which opens a code
   fence (and commonmark.js has no strikethrough); or its flanking context (CommonMark
   6.2) leaves neither side able to pair. The flanking test runs only for a
   single-text-node element, where the surrounding characters are known without a
   subtree scan, so the walk stays linear. */
static int md_marker_html(md_ctx *ctx, Py_ssize_t index, Py_ssize_t first) {
    md_marker *marker = &ctx->markers[index];
    if (marker->node == NULL) {
        return 0;
    }
    Py_ssize_t len = ctx->out.len;
    if (index == first && len > 0 && ctx->out.data[len - 1] == (unsigned char)marker->open[0] &&
        !(len >= 2 && ctx->out.data[len - 2] == '\\')) {
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

/* Emit the deferred opening markers from outermost to innermost (skipping any
   already written), so nested emphasis opens in source order. */
static TH_NOINLINE void md_emit_pending(md_ctx *ctx) {
    Py_ssize_t first = ctx->marker_count;
    while (first > ctx->marker_base && !ctx->markers[first - 1].emitted) {
        first--;
    }
    for (Py_ssize_t index = first; index < ctx->marker_count; index++) {
        md_marker *marker = &ctx->markers[index];
        if (md_marker_html(ctx, index, first)) {
            marker->open = MD_HTML_TAGS[marker->html - 1][0];
            marker->close = MD_HTML_TAGS[marker->html - 1][1];
        }
        md_puts8(&ctx->out, marker->open);
        marker->emitted = 1;
        ctx->line_has_content = 1;
    }
}

/* Called just before any visible character: settle the owed space first (so it
   stays outside an emphasis run), then open any markers that were waiting for
   real content. */
static void md_before_visible(md_ctx *ctx) {
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

/* Emit inline text with normal-flow whitespace collapsing and markdown escaping.
   Prose is mostly plain runs, so after the first character of a word is placed
   (which resolves the deferred space, markers and escapes) the rest of the run --
   no whitespace, nothing to escape -- is bulk-copied in one memcpy. */
static void md_emit_text(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t len) {
    int translit = ctx->opt->transliterate;
    Py_ssize_t index = 0;
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
        }
        int by_context = ((kind & MD_CH_CONTEXT) || (translit && ch == 0x2190)) && ctx->escape_prose &&
                         md_escape_by_context(text, index, len);
        md_put_char(ctx, ch, (kind & ctx->escape_mask) || by_context);
        index++;
        /* past the word's first character nothing is at a line start, so an escaped
           character only needs its backslash and the plain runs between them copy whole */
        while (1) {
            Py_ssize_t start = index;
            while (index < len && (text[index] < 0x80 ? !(MD_ASCII[text[index]] & ctx->run_stop) : !translit)) {
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

/* Close the marker a frame opened, returning its closing run when the open run was
   written (so the close run must be too), NULL otherwise. */
static const char *md_pop_marker(md_ctx *ctx, Py_ssize_t marker) {
    if (marker < 0) {
        return NULL;
    }
    ctx->marker_count = marker;
    md_settle_pending(ctx);
    return ctx->markers[marker].emitted ? ctx->markers[marker].close : NULL;
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
   table cell and a converter can each put text right after a block, so there it is not. */
static int md_followed_by_break(md_ctx *ctx, th_node *node) {
    th_node *sibling = node->next_sibling;
    if (sibling == NULL) {
        /* a node that is not an element carries TH_TAG_UNKNOWN, never a block atom */
        th_node *parent = node->parent;
        return parent->ns == TH_NS_HTML && is_md_block(parent->atom) && ctx->inline_only == 0 && !ctx->in_cell &&
               ctx->opt->converters == NULL;
    }
    if (sibling->type != TH_NODE_TEXT || sibling->text_len == 0) {
        return 0;
    }
    return md_edge(need_text(ctx->tree, sibling)[0]) != 0;
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
        Py_UCS4 next = sibling != NULL && sibling->type == TH_NODE_TEXT && sibling->text_len > 0
                           ? need_text(ctx->tree, sibling)[0]
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
    marker->flags = html >= MD_HTML_DEL ? MD_MARK_STRIKE : 0;
    md_wrap_flank(ctx, node, marker);
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

/* Block descendants need a separator when flattened into code text, but inline
   descendants and explicit whitespace must keep their original adjacency. */
static void md_collect_code_text(th_tree *tree, th_node *root, sbuf *out, Py_UCS4 separator) {
    int boundary = 0;
    th_node *parent = root;
    th_node *child = root->first_child;
    for (;;) {
        while (child == NULL) {
            if (parent == root) {
                return;
            }
            if (parent->type == TH_NODE_ELEMENT && parent->ns == TH_NS_HTML && is_md_block(parent->atom)) {
                boundary = 1;
            }
            child = parent->next_sibling;
            parent = parent->parent;
        }
        if (child->type == TH_NODE_TEXT && child->text_len > 0) {
            const Py_UCS4 *text = need_text(tree, child);
            if (boundary && out->len > 0 && !is_space(out->data[out->len - 1]) && !is_space(text[0])) {
                sbuf_putc(out, separator);
            }
            sbuf_put_run(out, text, child->text_len);
            boundary = 0;
        } else if (child->type == TH_NODE_ELEMENT || child->type == TH_NODE_CONTENT) {
            if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML && child->atom == TH_TAG_BR) {
                if (separator == ' ') {
                    sbuf_putc(out, ' ');
                    boundary = 0;
                }
            } else {
                if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML && is_md_block(child->atom)) {
                    boundary = 1;
                }
                parent = child;
                child = child->first_child;
                continue;
            }
        }
        child = child->next_sibling;
    }
}

static void md_emit_code_span(md_ctx *ctx, th_node *node) {
    sbuf content = {0};
    md_collect_code_text(ctx->tree, node, &content, ' ');
    if (content.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(content.data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t len = content.len;
    if (len == 0) {
        PyMem_Free(content.data);
        return;
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
    int pad = content.data[0] == '`' || content.data[len - 1] == '`';
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

/* Write a link destination (CommonMark 6.3) after an optional base prefix. A bare
   destination cannot hold a space or start with `<`, and takes parentheses only
   in balanced pairs; anything else goes in the `<...>` form, which takes any
   parenthesis but no unescaped angle bracket. Parentheses stay bare while they
   balance, so a `wiki/Foo_(bar)` URL reads as written. A backslash, and a `&` that
   would decode as a character reference, are escaped in either form. */
static void md_emit_url(md_ctx *ctx, const char *base, const Py_UCS4 *url, Py_ssize_t len) {
    int angle = 0;
    int depth = 0;
    int unbalanced = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (url[index] == ' ' || (index == 0 && *base == '\0' && url[index] == '<')) {
            angle = 1;
        } else if (url[index] == '(') {
            depth++;
        } else if (url[index] == ')') {
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
        Py_UCS4 ch = url[index];
        int bracket = angle ? ch == '<' || ch == '>' : unbalanced && (ch == '(' || ch == ')');
        if (bracket || ch == '\\' || (ch == '&' && md_starts_reference(url, index, len))) {
            sbuf_putc(&ctx->out, '\\');
        }
        md_put_literal(ctx, ch);
    }
    if (angle) {
        sbuf_putc(&ctx->out, '>');
    }
}

/* Write a link/image title inside its `"..."` delimiters: a `"` would close the
   title early and a `\` would escape the next character, so both are backslashed. */
static void md_emit_title(md_ctx *ctx, const Py_UCS4 *title, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (title[index] == '"' || title[index] == '\\') {
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

static void md_enter_link(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    /* wrap_links off keeps the whole [text](url) construct on one line */
    if (!opt->wrap_links) {
        ctx->no_wrap++;
    }
    Py_ssize_t href_len;
    const Py_UCS4 *href = md_attr(ctx->tree, node, "href", &href_len);
    if (href == NULL || opt->ignore_links || (opt->skip_internal_links && md_href_internal(href))) {
        md_push(ctx, node, MD_WALK_INLINE, opt->wrap_links ? MD_LEAVE_NONE : MD_LEAVE_NO_WRAP);
        return;
    }
    Py_ssize_t title_len;
    const Py_UCS4 *title = md_attr(ctx->tree, node, "title", &title_len);
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
}

static void md_leave_link(md_ctx *ctx, md_frame *frame) {
    const md_opts *opt = ctx->opt;
    ctx->inline_only--;
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

static void md_emit_pre_text(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t end);

/* Pass a node's outer HTML through verbatim (the image/table escape hatch),
   restarting the continuation prefix at each newline so a list or blockquote
   marker carries down every line of the embedded markup. */
static void md_emit_raw_html(md_ctx *ctx, th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *html = th_node_html(ctx->tree, node, &len);
    if (html == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    md_emit_pre_text(ctx, html, len);
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

/* Write a block a pipe-table cell cannot hold -- a nested table or list -- as its
   source HTML, which is inline content and so legal in a cell (and renders as the
   real thing wherever embedded HTML is allowed). The `<tbody>` the parser inserts
   around every row group is dropped on the way out: it reparses back in and only
   widens the column. The caller's whitespace collapse puts the rest on one line. */
static void md_emit_cell_html(md_ctx *ctx, th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *html = th_node_html(ctx->tree, node, &len);
    if (html == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    md_before_visible(ctx);
    Py_ssize_t start = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_ssize_t tag = md_tbody_tag(&html[index], len - index);
        if (tag > 0) {
            md_put_run(ctx, &html[start], index - start);
            index += tag - 1;
            start = index + 1;
        }
    }
    md_put_run(ctx, &html[start], len - start);
    ctx->line_has_content = 1;
    PyMem_Free(html);
}

/* GFM pipe cells cannot hold nested block markup. */
static int md_is_cell_block(uint16_t atom) {
    return atom == TH_TAG_TABLE || atom == TH_TAG_UL || atom == TH_TAG_OL || atom == TH_TAG_MENU;
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
   `![...]` description a bracket or backslash is escaped the same way link text
   escapes them (an unescaped `]` would close the description early); the plain
   alt-only image mode passes escape=0 since it emits no brackets to protect. */
static void md_emit_alt(md_ctx *ctx, const Py_UCS4 *alt, Py_ssize_t alt_len, int escape) {
    if (alt != NULL) {
        if (escape) {
            for (Py_ssize_t index = 0; index < alt_len; index++) {
                if (alt[index] == '[' || alt[index] == ']' || alt[index] == '\\') {
                    sbuf_putc(&ctx->out, '\\');
                }
                md_put_literal(ctx, alt[index]);
            }
        } else {
            md_put_run(ctx, alt, alt_len);
        }
    } else {
        md_puts8(&ctx->out, ctx->opt->default_image_alt);
    }
    ctx->line_has_content = 1;
}

static void md_emit_image(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    if (opt->image_mode == TH_MD_IMAGE_IGNORE) {
        return;
    }
    md_before_visible(ctx);
    if (opt->image_mode == TH_MD_IMAGE_HTML) {
        md_emit_raw_html(ctx, node);
        return;
    }
    Py_ssize_t alt_len;
    const Py_UCS4 *alt = md_attr(ctx->tree, node, "alt", &alt_len);
    if (opt->image_mode == TH_MD_IMAGE_ALT) {
        md_emit_alt(ctx, alt, alt_len, 0);
        return;
    }
    Py_ssize_t src_len;
    const Py_UCS4 *src = md_attr(ctx->tree, node, "src", &src_len);
    sbuf_puts(&ctx->out, "![");
    md_emit_alt(ctx, alt, alt_len, 1);
    sbuf_puts(&ctx->out, "](");
    if (src != NULL) {
        int relative = *opt->base_url != '\0' && !md_href_absolute(src, src_len) && !md_href_internal(src);
        md_emit_url(ctx, relative ? opt->base_url : "", src, src_len);
    }
    Py_ssize_t title_len;
    const Py_UCS4 *title = md_attr(ctx->tree, node, "title", &title_len);
    if (title != NULL) {
        sbuf_puts(&ctx->out, " \"");
        md_emit_title(ctx, title, title_len);
        sbuf_putc(&ctx->out, '"');
    }
    sbuf_putc(&ctx->out, ')');
    ctx->line_has_content = 1;
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
        md_emit_code_span(ctx, node);
        return;
    case TH_TAG_A:
        md_enter_link(ctx, node);
        return;
    case TH_TAG_IMG:
        md_emit_image(ctx, node);
        return;
    case TH_TAG_BR:
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
        sbuf_puts(&ctx->out, opt->line_break == TH_MD_BREAK_BACKSLASH ? "\\" : "  ");
        md_newline(ctx);
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
        if (!ctx->inline_only) {
            md_render_block(ctx, node);
            return;
        }
        if (ctx->in_cell && opt->cell_blocks == TH_MD_CELL_TEXT && md_is_cell_block(atom)) {
            ctx->space_pending = 1;
            md_enter_cell_flat(ctx, node, -1);
            return;
        }
        /* inside link text a block cannot open its own line (a blank line would
           split the CommonMark link), so it flattens to inline; its boundary still
           reads as a space so adjacent words never fuse */
        ctx->space_pending = 1;
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
        md_emit_code_span(ctx, node);
    } else {
        md_render_inline_tag(ctx, node);
    }
}

static void md_leave_google(md_ctx *ctx, md_frame *frame) {
    ctx->g_bold = frame->google.outer_bold;
    ctx->g_italic = frame->google.outer_italic;
    const char *close = md_pop_marker(ctx, frame->marker);
    if (close != NULL) {
        md_puts8(&ctx->out, close);
    }
    close = md_pop_marker(ctx, frame->google.italic_marker);
    if (close != NULL) {
        md_puts8(&ctx->out, close);
    }
}

/* Render one node encountered in an inline run. A block element nested in inline
   flow (rare, e.g. a <div> inside a <span>) is laid out as its own block. */
static void md_render_inline(md_ctx *ctx, th_node *node) {
    if (node->type == TH_NODE_TEXT) {
        md_emit_text(ctx, need_text(ctx->tree, node), node->text_len);
        return;
    }
    if (node->type != TH_NODE_ELEMENT && node->type != TH_NODE_CONTENT) {
        return;
    }
    if (md_apply_converter(ctx, node)) {
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

/* A loose item needs its first block on the marker line even when a transparent
   container wraps it; otherwise CommonMark reads that block as code. The scan
   descends into such containers and climbs back out of one that leads with
   nothing, so 1 means inline, 0 a self-framing block, -1 nothing at all. */
static int md_leads_with_inline(md_ctx *ctx, th_node *root) {
    th_node *parent = root;
    th_node *child = root->first_child;
    for (;;) {
        while (child == NULL) {
            if (parent == root) {
                return -1;
            }
            child = parent->next_sibling;
            parent = parent->parent;
        }
        if (child->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            for (Py_ssize_t index = 0; index < child->text_len; index++) {
                if (!is_space(text[index])) {
                    return 1; /* leading visible text rides on the bullet line */
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
        if (!is_md_block(atom) || md_is_paragraph_block(atom)) {
            return 1;
        }
        if (atom >= TH_TAG_H1 && atom <= TH_TAG_H6) {
            return ctx->opt->heading_style != TH_MD_HEADING_SETEXT || atom > TH_TAG_H2;
        }
        switch (atom) {
        case TH_TAG_BLOCKQUOTE:
        case TH_TAG_PRE:
        case TH_TAG_HR:
        case TH_TAG_UL:
        case TH_TAG_OL:
        case TH_TAG_MENU:
        case TH_TAG_TABLE:
            return 0;
        default:
            parent = child;
            child = child->first_child;
        }
    }
}

/* Whether a list item lays out as more than one paragraph, so it renders as a
   CommonMark loose item (a blank line between its blocks): a leading text/inline
   run plus a paragraph block, or two paragraph blocks. A nested list, blockquote
   or other self-framing block is not a paragraph and does not force looseness (a
   tight item can still carry a sublist). */
static int md_item_is_loose(md_ctx *ctx, th_node *node) {
    int units = 0;
    int in_run = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            for (Py_ssize_t index = 0; index < child->text_len; index++) {
                if (!is_space(text[index])) {
                    if (!in_run) {
                        units++;
                        in_run = 1;
                    }
                    break;
                }
            }
            continue;
        }
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        uint16_t atom = child->ns == TH_NS_HTML ? child->atom : TH_TAG_UNKNOWN;
        if (is_md_skipped(child)) {
            continue;
        }
        if (is_md_block(atom)) {
            in_run = 0;
            if (md_is_paragraph_block(atom)) {
                units++;
            }
        } else if (!in_run) {
            units++;
            in_run = 1;
        }
        if (units > 1) {
            return 1;
        }
    }
    return 0;
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
        int only_ws = child->type == TH_NODE_TEXT;
        if (only_ws) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
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
        .space_pending = ctx->space_pending,
        .drop_space = ctx->drop_space,
        .pending_loose = ctx->pending_loose,
        .suppress_break = ctx->suppress_break,
        .tight = ctx->tight,
        .list_depth = ctx->list_depth,
        .g_bold = ctx->g_bold,
        .g_italic = ctx->g_italic,
        .inline_only = ctx->inline_only,
        .list_end_marker = ctx->list_end_marker,
    };
    ctx->out = (sbuf){0};
    ctx->prefix = (sbuf){0};
    ctx->marker_base = ctx->marker_count;
    ctx->pending = NULL;
    ctx->started = 0;
    ctx->line_has_content = 0;
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
    ctx->list_end_marker = 0;
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
   walk never begins a block with whitespace, so only a trailing line break is
   trimmed off the children's Markdown. */
static void md_leave_converter(md_ctx *ctx, md_frame *frame) {
    md_converting *saved = frame->convert;
    Py_UCS4 *data = ctx->out.data;
    Py_ssize_t end = ctx->out.len;
    while (end > 0 && is_space(data[end - 1])) {
        end--;
    }
    PyObject *content = NULL;
    if (!ctx->out.failed) { /* GCOVR_EXCL_BR_LINE: the sub-buffer fails only on an unforceable allocation */
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
    ctx->list_end_marker = saved->list_end_marker;
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
    Py_DECREF(result);
}

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

/* Whether a list child is a wrapper around list items (`<ul><div><li>`), which the
   list looks through so its items keep their markers and numbering. */
static int md_is_item_wrapper(th_node *node) {
    if (node->type != TH_NODE_ELEMENT || node->ns != TH_NS_HTML) {
        return 0;
    }
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML && child->atom == TH_TAG_LI) {
            return 1;
        }
    }
    return 0;
}

/* CommonMark: a list is loose (blank lines around every item and between an
   item's blocks) when any item holds more than one paragraph. The scan looks
   through item wrappers the way the layout does. */
static int md_list_is_loose(md_ctx *ctx, th_node *list) {
    th_node *parent = list;
    th_node *scan = list->first_child;
    for (;;) {
        while (scan == NULL) {
            if (parent == list) {
                return 0;
            }
            scan = parent->next_sibling;
            parent = parent->parent;
        }
        if (scan->type == TH_NODE_ELEMENT && scan->ns == TH_NS_HTML && scan->atom == TH_TAG_LI) {
            if (md_item_is_loose(ctx, scan)) {
                return 1;
            }
        } else if (md_is_item_wrapper(scan)) {
            parent = scan;
            scan = scan->first_child;
            continue;
        }
        scan = scan->next_sibling;
    }
}

/* Open one list item: its marker on a fresh line, then its children laid out as
   blocks indented under the marker. */
static void md_render_item(md_ctx *ctx, th_node *child, md_list_state *state) {
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
        width = lead + md_put_decimal(&ctx->out, state->number) + 2;
        sbuf_putc(&ctx->out, (Py_UCS4)(unsigned char)state->delimiter);
        sbuf_putc(&ctx->out, ' ');
        if (state->number < PY_SSIZE_T_MAX) {
            state->number++;
        }
    } else {
        sbuf_putc(&ctx->out, (Py_UCS4)(unsigned char)state->bullet);
        sbuf_putc(&ctx->out, ' ');
        width = lead + 2;
    }
    /* the item's content starts its own line: `- 1. x` would nest an ordered list */
    ctx->line_has_content = 0;
    state->sub_indent = width;
    state->item_seen = 1;
    state->in_run = 0;
    int saved_levels = ctx->indent_levels;
    Py_ssize_t base = md_indent(ctx, width);
    int saved_tight = ctx->tight;
    ctx->tight = !state->loose;
    ctx->suppress_break = md_leads_with_inline(ctx, child) > 0;
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
               list re-applies the wrap guard per item, so none is needed here. */
            state->in_run = 0;
            md_push_indent(ctx, state->sub_indent, 1);
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

static void md_enter_list(md_ctx *ctx, th_node *node) {
    int ordered = md_list_ordered(ctx, node);
    Py_ssize_t number = ordered ? md_list_number_attr(ctx, node, "start", 1) : 1;
    Py_ssize_t bullets_len = (Py_ssize_t)strlen(ctx->opt->bullets);
    md_list_state state = {
        .number = number,
        .sub_indent = 2, /* how far a bare nested list indents: the last marker's width */
        .bullet = ctx->opt->bullets[ctx->list_depth % bullets_len],
        .delimiter = '.',
        .ordered = ordered,
        .loose = md_list_is_loose(ctx, node),
    };
    /* CommonMark keeps items with the same bullet or ordered delimiter in one list,
       blank line or not, so a list that follows one of its own kind with nothing in
       between switches its marker to stay a separate list */
    if (ctx->list_end_marker != 0 && ctx->list_end == ctx->out.len && ctx->list_end_prefix == ctx->prefix.len) {
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
        ctx->list_end_marker = frame->list.state.ordered ? frame->list.state.delimiter : frame->list.state.bullet;
    }
}

/* Count the ordinals of the items before target, looking through item wrappers. The
   caller found container by climbing from target through those wrappers, so the scan
   reaches target before it runs out of container. */
static void md_list_number_before(md_ctx *ctx, th_node *container, th_node *target, Py_ssize_t *number) {
    th_node *parent = container;
    th_node *child = container->first_child;
    for (;;) {
        while (child == NULL) {
            child = parent->next_sibling;
            parent = parent->parent;
        }
        if (child == target) {
            return;
        }
        if (child->type == TH_NODE_ELEMENT && child->ns == TH_NS_HTML && child->atom == TH_TAG_LI) {
            *number = md_list_number_attr(ctx, child, "value", *number);
            if (*number < PY_SSIZE_T_MAX) {
                (*number)++;
            }
        } else if (md_is_item_wrapper(child)) {
            parent = child;
            child = child->first_child;
            continue;
        }
        child = child->next_sibling;
    }
}

static void md_render_root_item(md_ctx *ctx, th_node *node) {
    th_node *list = node->parent;
    while (list != NULL && list->atom != TH_TAG_UL && list->atom != TH_TAG_OL && list->atom != TH_TAG_MENU &&
           md_is_item_wrapper(list)) {
        list = list->parent;
    }
    int in_list = list != NULL && list->ns == TH_NS_HTML &&
                  (list->atom == TH_TAG_UL || list->atom == TH_TAG_OL || list->atom == TH_TAG_MENU);
    int ordered = in_list && md_list_ordered(ctx, list);
    Py_ssize_t number = ordered ? md_list_number_attr(ctx, list, "start", 1) : 1;
    if (ordered) {
        md_list_number_before(ctx, list, node, &number);
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
        .bullet = ctx->opt->bullets[(depth > 0 ? depth - 1 : 0) % (Py_ssize_t)strlen(ctx->opt->bullets)],
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
    table->saved_space = ctx->space_pending;
    table->saved_drop = ctx->drop_space;
    ctx->out = (sbuf){NULL, 0, 0, 0};
    ctx->prefix = (sbuf){NULL, 0, 0, 0};
    ctx->marker_base = ctx->marker_count;
    ctx->pending = NULL;
    ctx->line_has_content = 1;
    ctx->space_pending = 0;
    ctx->drop_space = 1;
    ctx->in_cell = 1;
}

static void md_leave_cell(md_ctx *ctx, md_table *table) {
    sbuf rendered = ctx->out;
    PyMem_Free(ctx->prefix.data);
    ctx->out = table->saved_out;
    ctx->prefix = table->saved_prefix;
    ctx->marker_base = table->saved_marker_base;
    md_settle_pending(ctx);
    ctx->started = table->saved_started;
    ctx->line_has_content = table->saved_line;
    ctx->space_pending = table->saved_space;
    ctx->drop_space = table->saved_drop;
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

/* Collect the table's rows in document order across nested thead/tbody/tfoot wrappers
   and return the widest row's column count. With rows NULL it only counts, so the
   array is sized by the same walk that fills it. */
static Py_ssize_t md_collect_rows(th_node *table, th_node **rows, Py_ssize_t *count) {
    Py_ssize_t columns = 0;
    th_node *node = table->first_child;
    while (node != NULL) {
        if (node->type == TH_NODE_ELEMENT) {
            if (node->atom == TH_TAG_TR) {
                if (rows != NULL) {
                    rows[*count] = node;
                    Py_ssize_t cells = md_row_cells(node);
                    if (cells > columns) {
                        columns = cells;
                    }
                }
                (*count)++;
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
    return columns;
}

/* Emit one padded grid row: each cell's text then spaces out to the column
   width, wrapped in pipes. A NULL grid emits an all-spaces (empty header) row. */
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
        for (Py_ssize_t pad = len; pad < widths[column]; pad++) {
            sbuf_putc(&ctx->out, ' ');
        }
        sbuf_puts(&ctx->out, " |");
    }
    ctx->line_has_content = 1;
}

/* Lay out the rendered cells as an aligned pipe grid: each column as wide as its
   widest cell (html2text's pad_tables). */
static void md_emit_padded(md_ctx *ctx, md_table *table) {
    Py_ssize_t columns = table->columns;
    for (Py_ssize_t column = 0; column < columns; column++) {
        if (table->widths[column] < 3) {
            table->widths[column] = 3; /* the "---" separator needs at least three dashes */
        }
    }
    md_emit_padded_row(ctx, table->has_header ? table->grid : NULL, 0, columns, table->widths);
    md_newline(ctx);
    sbuf_putc(&ctx->out, '|');
    for (Py_ssize_t column = 0; column < columns; column++) {
        sbuf_putc(&ctx->out, ' ');
        for (Py_ssize_t pad = 0; pad < table->widths[column]; pad++) {
            sbuf_putc(&ctx->out, '-');
        }
        sbuf_puts(&ctx->out, " |");
    }
    ctx->line_has_content = 1;
    for (Py_ssize_t row = table->has_header ? 1 : 0; row < table->count; row++) {
        md_newline(ctx);
        md_emit_padded_row(ctx, table->grid, row, columns, table->widths);
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

/* Start a table: an HTML-mode table is written whole; any other walks its captions
   and then its cells through md_table_step. */
static void md_enter_table(md_ctx *ctx, th_node *node) {
    Py_ssize_t cap = 0;
    md_collect_rows(node, NULL, &cap);
    th_node **rows = PyMem_Malloc((size_t)(cap > 0 ? cap : 1) * sizeof(th_node *));
    if (rows == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    Py_ssize_t columns = md_collect_rows(node, rows, &count);
    if (ctx->opt->table_mode == TH_MD_TABLE_HTML) {
        if (count > 0 && columns > 0) {
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
    if (table->count == 0 || table->columns == 0) {
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
                for (; table->column < table->columns; table->column++) {
                    sbuf_puts(&ctx->out, "  |");
                }
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
            md_emit_text(ctx, need_text(ctx->tree, child), child->text_len);
        }
        if (table->phase != MD_TABLE_STRIP) {
            md_leave_cell(ctx, table);
        }
    }
}

static void md_leave_table(md_ctx *ctx, md_table *table) {
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

static void md_render_pre(md_ctx *ctx, th_node *node) {
    th_node *code = node->first_child;
    th_node *content = node;
    Py_ssize_t lang_len = 0;
    const Py_UCS4 *lang = NULL;
    if (code != NULL && code->type == TH_NODE_ELEMENT && code->ns == TH_NS_HTML && code->atom == TH_TAG_CODE &&
        code->next_sibling == NULL) {
        content = code;
        Py_ssize_t cls_len;
        const Py_UCS4 *cls = md_attr(ctx->tree, code, "class", &cls_len);
        if (cls != NULL) {
            const char *want = "language-";
            Py_ssize_t want_len = 9;
            if (cls_len > want_len) {
                int match = 1;
                for (Py_ssize_t index = 0; index < want_len; index++) {
                    if (cls[index] != (Py_UCS4)(unsigned char)want[index]) {
                        match = 0;
                        break;
                    }
                }
                if (match) {
                    lang = cls + want_len;
                    lang_len = cls_len - want_len;
                    for (Py_ssize_t index = 0; index < lang_len; index++) {
                        if (is_space(lang[index])) {
                            lang_len = index;
                            break;
                        }
                    }
                }
            }
        }
    }
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
    const md_opts *opt = ctx->opt;
    /* drop one trailing newline so the close is not preceded by a blank line */
    Py_ssize_t end = text_len;
    if (end > 0 && text[end - 1] == '\n') {
        end--;
    }
    md_block_line(ctx, 1);
    if (opt->code_mark_open != NULL) {
        /* html2text's [code]...[/code] markers in place of a fence */
        md_puts8(&ctx->out, opt->code_mark_open);
        ctx->line_has_content = 1;
        md_newline(ctx);
        md_emit_pre_text(ctx, text, end);
        md_newline(ctx);
        md_puts8(&ctx->out, opt->code_mark_close);
        ctx->line_has_content = 1;
    } else if (opt->code_block_style == TH_MD_CODE_INDENTED) {
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
            sbuf_put_run(&ctx->out, lang, lang_len);
        } else {
            md_puts8(&ctx->out, opt->code_language);
        }
        ctx->line_has_content = 1;
        md_newline(ctx);
        md_emit_pre_text(ctx, text, end);
        md_newline(ctx);
        for (Py_ssize_t index = 0; index < fence; index++) {
            sbuf_putc(&ctx->out, '`');
        }
        ctx->line_has_content = 1;
    }
    PyMem_Free(text);
}

static void md_render_block(md_ctx *ctx, th_node *node) {
    if (md_apply_converter(ctx, node)) {
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
        }
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
        Py_ssize_t base = ctx->prefix.len;
        int saved_levels = ctx->indent_levels;
        const char *marker = ++ctx->indent_levels <= TH_MAX_INDENT_LEVELS ? "> " : "";
        if (ctx->started) {
            /* close the previous block and open the separator with the OUTER
               prefix, so the blank line is not itself quoted, before adding the
               "> " that every line inside the quote carries */
            sbuf_putc(&ctx->out, '\n');
            if (!ctx->tight) {
                md_write_blank_prefix(ctx);
                sbuf_putc(&ctx->out, '\n');
            }
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
        md_frame *frame = md_push(ctx, node, MD_WALK_BLOCK, MD_LEAVE_NONE);
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
    Py_ssize_t mark = frame->heading.mark;
    int level = frame->heading.level;
    if (frame->leave == MD_LEAVE_SETEXT) {
        Py_ssize_t width = ctx->out.len - mark;
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
        const char *close = md_pop_marker(ctx, frame->marker);
        if (close != NULL) {
            md_puts8(&ctx->out, close);
        }
        break;
    }
    case MD_LEAVE_QUOTE:
        md_puts8(&ctx->out, ctx->opt->quote_close);
        break;
    case MD_LEAVE_LINK:
        md_leave_link(ctx, frame);
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
    default:
        break;
    }
    if (frame->prefix_base >= 0) {
        ctx->tight = frame->saved_tight;
        ctx->indent_levels = frame->saved_levels;
        ctx->prefix.len = frame->prefix_base;
    }
}

/* Render the pushed frames to completion. Each step renders the next child of the
   innermost frame, which may push frames of its own, so the C stack stays flat at any
   nesting depth. The next child is read only once the previous one is done, as a
   recursive walk would, so a converter that edits the tree sees the same walk. */
static void md_run(md_ctx *ctx) {
    while (ctx->frame_count > 0) {
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
                for (; child != NULL && child->type == TH_NODE_TEXT; child = child->next_sibling) {
                    md_emit_text(ctx, need_text(ctx->tree, child), child->text_len);
                }
            } else if (frame->walk == MD_WALK_BLOCK) {
                for (; child != NULL && child->type == TH_NODE_TEXT; child = child->next_sibling) {
                    md_block_child(ctx, child, &frame->in_run);
                }
            }
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

Py_UCS4 *th_node_markdown(th_tree *tree, th_node *node, const md_opts *opt, Py_ssize_t *out_len) {
    md_frame inline_frames[MD_INLINE_FRAMES];
    md_marker inline_markers[MD_INLINE_MARKERS];
    md_ctx ctx = {0};
    ctx.tree = tree;
    ctx.opt = opt;
    ctx.frames = ctx.inline_frames = inline_frames;
    ctx.frame_cap = MD_INLINE_FRAMES;
    ctx.markers = ctx.inline_markers = inline_markers;
    ctx.marker_cap = MD_INLINE_MARKERS;
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
        md_emit_text(&ctx, need_text(tree, node), node->text_len);
    } else if (md_apply_converter(&ctx, node)) {
        /* a converter registered for the root element renders it whole */
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
    PyMem_Free(ctx.prefix.data);
    PyMem_Free(ctx.refs);
    if (ctx.failed) {
        PyMem_Free(ctx.out.data);
        return NULL;
    }
    md_trim(&ctx.out, opt->document_strip);
    return sbuf_finish(&ctx.out, out_len);
}
