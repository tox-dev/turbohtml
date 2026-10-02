/* Renders a page as readable plain text the way a browser would lay it out --
   blocks split by blank lines, lists indented under their bullets, tables drawn
   as a column-aligned grid -- for search indexing, diffing, or a terminal view.

   It shares sbuf, need_text(), the tag atoms, the attribute lookup, and the
   is_space()/is_md_block()/is_md_skipped() predicates from serialize/internal.h.
   Unlike to_markdown it emits no markup: it keeps the visual structure of the
   page as plain text. */

#include "serialize/internal.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <string.h>

text_opts th_text_default_opts(void) {
    text_opts opt = {0};
    opt.width = 0;
    opt.links = TH_TEXT_LINKS_NONE;
    opt.images = 0;
    opt.extended = 1;
    opt.default_image_alt = "";
    opt.cell_separator = "  ";
    opt.bullet = "* ";
    return opt;
}

typedef struct {
    const Py_UCS4 *url;
    Py_ssize_t url_len;
} text_reference;

/* One open annotation: where the matching element's text began, and the rule
   whose labels close over it. */
typedef struct {
    Py_ssize_t start;
    const text_rule *rule;
} text_active;

/* How a frame walks its node's children. */
enum text_walk {
    TEXT_WALK_INLINE, /* each child renders in the inline flow */
    TEXT_WALK_BLOCK,  /* inline runs open lines, block children lay out as blocks */
    TEXT_WALK_LIST,   /* items take markers, a bare nested list indents under the last one */
    TEXT_WALK_TABLE,  /* each cell renders on its own for the grid */
};

/* What a frame writes or restores once its children are done. */
enum text_leave {
    TEXT_LEAVE_NONE,
    TEXT_LEAVE_LINK_INLINE,   /* the " (href)" after the link text */
    TEXT_LEAVE_LINK_FOOTNOTE, /* the "[n]" reference after the link text */
    TEXT_LEAVE_LIST,
    TEXT_LEAVE_CELL,  /* flatten the cell into its grid slot */
    TEXT_LEAVE_TABLE, /* lay out the finished grid */
};

/* A table being laid out: its rows, the rendered cell texts with their column widths,
   and the output state one cell's throwaway rendering set aside. */
typedef struct {
    th_node **rows;
    sbuf *grid;
    Py_ssize_t *widths;
    th_node **cells;
    th_node *cell; /* the cell rendered last, NULL before the first */
    Py_ssize_t count;
    Py_ssize_t columns;
    Py_ssize_t row;
    Py_ssize_t column;
    sbuf saved_out;
    sbuf saved_prefix;
    int saved_line;
    int saved_column;
    int saved_space;
    int saved_started;
} text_table;

/* One element whose children are being rendered: the child it reached and what it
   restores once they are done. Frames live on a heap stack, so nesting costs memory
   rather than C stack. */
typedef struct {
    th_node *node;
    th_node *child;         /* the child rendered last, NULL before the first */
    Py_ssize_t opened;      /* annotations the element opened */
    Py_ssize_t prefix_base; /* the prefix length (with saved_tight) to restore, or -1 */
    union {
        struct {
            Py_ssize_t number;     /* the next ordinal */
            Py_ssize_t sub_indent; /* how far a bare nested list indents */
            int ordered;
        } list;
        struct {
            const Py_UCS4 *href;
            Py_ssize_t href_len;
        } link;
        text_table *table;
    };
    int saved_tight;
    uint8_t walk;
    uint8_t leave;
    uint8_t in_run; /* block walk: inside a run of inline children */
} text_frame;

/* Frames start in storage on the C stack and move to the heap only past it. The Readability test pages and the
   CodSpeed corpora peak at 35 frames. */
#define TEXT_INLINE_FRAMES 64

typedef struct {
    sbuf out;
    th_tree *tree;
    const text_opts *opt;
    text_frame *frames;
    text_frame *inline_frames;
    Py_ssize_t frame_count;
    Py_ssize_t frame_cap;
    sbuf prefix;          /* spaces every continuation line starts with (list/quote indent) */
    text_reference *refs; /* footnote-link targets */
    Py_ssize_t ref_count;
    Py_ssize_t ref_cap;
    const text_rule *rules; /* annotation rules, or NULL when not annotating */
    Py_ssize_t n_rules;
    Py_ssize_t *rule_index;
    text_span *spans; /* recorded labeled spans */
    Py_ssize_t span_count;
    Py_ssize_t span_cap;
    text_active *active; /* annotations open at the current depth */
    Py_ssize_t active_count;
    Py_ssize_t active_cap;
    int annotate_off; /* suppress span recording (inside a table cell) */
    int started;
    int line_has_content;
    int column;        /* visible width on the current line, for word wrapping */
    int space_pending; /* a collapsed-away whitespace run is owed one space */
    int pending_loose; /* the previous block wants a blank line after it */
    int suppress_break;
    int tight;
    int list_depth;
    int failed;
} text_ctx;

static void text_write_prefix(text_ctx *ctx) {
    sbuf_put_run(&ctx->out, ctx->prefix.data, ctx->prefix.len);
    ctx->column = (int)ctx->prefix.len;
}

/* Start a fresh prefixed line for the next block, collapsing the margin with the
   previous block to a single blank line when either side is loose. */
static void text_block_line(text_ctx *ctx, int loose) {
    if (!ctx->started) {
        ctx->started = 1;
        text_write_prefix(ctx);
    } else if (ctx->suppress_break) {
        ctx->suppress_break = 0;
    } else {
        sbuf_putc(&ctx->out, '\n');
        if ((ctx->pending_loose || loose) && !ctx->tight) {
            sbuf_putc(&ctx->out, '\n');
        }
        text_write_prefix(ctx);
        ctx->line_has_content = 0;
    }
    ctx->pending_loose = loose;
    ctx->space_pending = 0;
}

/* A line break inside a block (a <br> or a wrapped line): newline plus prefix. */
static void text_newline(text_ctx *ctx) {
    sbuf_putc(&ctx->out, '\n');
    text_write_prefix(ctx);
    ctx->line_has_content = 0;
    ctx->space_pending = 0;
}

/* Place one word, wrapping to a fresh line first when it would overflow the
   configured width, otherwise settling the owed inter-word space. */
static void text_emit_word(text_ctx *ctx, const Py_UCS4 *word, Py_ssize_t len) {
    int width = ctx->opt->width;
    if (width > 0 && ctx->line_has_content && ctx->column + 1 + (int)len > width) {
        text_newline(ctx);
    }
    if (ctx->space_pending && ctx->line_has_content) {
        sbuf_putc(&ctx->out, ' ');
        ctx->column++;
    }
    ctx->space_pending = 0;
    sbuf_put_run(&ctx->out, word, len);
    ctx->column += (int)len;
    ctx->line_has_content = 1;
}

/* Emit inline text with normal-flow whitespace collapsing (no escaping). */
static void text_emit_text(text_ctx *ctx, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t pos = 0;
    while (pos < len) {
        if (is_space(text[pos])) {
            ctx->space_pending = 1;
            pos++;
            continue;
        }
        Py_ssize_t start = pos;
        while (pos < len && !is_space(text[pos])) {
            pos++;
        }
        text_emit_word(ctx, &text[start], pos - start);
    }
}

static const Py_UCS4 *text_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t *len) {
    return md_attr(tree, node, name, len);
}

static Py_ssize_t text_add_reference(text_ctx *ctx, const Py_UCS4 *url, Py_ssize_t url_len) {
    if (ctx->ref_count == ctx->ref_cap) {
        size_t cap;
        size_t bytes;
        int grew =
            th_grow_cap((size_t)(ctx->ref_count + 1), (size_t)ctx->ref_cap, 8, sizeof(text_reference), &cap, &bytes);
        if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            ctx->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return 0;        /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        text_reference *grown = PyMem_Realloc(ctx->refs, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return 0;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ctx->refs = grown;
        ctx->ref_cap = (Py_ssize_t)cap;
    }
    ctx->refs[ctx->ref_count].url = url;
    ctx->refs[ctx->ref_count].url_len = url_len;
    return ++ctx->ref_count;
}

/* Emit a run of ASCII characters (a bracket, a footnote number's punctuation). */
static void text_emit_ascii(text_ctx *ctx, const char *ascii) {
    for (const char *cursor = ascii; *cursor != '\0'; cursor++) {
        sbuf_putc(&ctx->out, (Py_UCS4)(unsigned char)*cursor);
        ctx->column++;
    }
    ctx->line_has_content = 1;
}

/* Emit an option string (a list bullet, possibly unicode) verbatim, so its own
   spacing survives the word machinery's collapsing. */
static void text_put_literal(text_ctx *ctx, const char *s) {
    Py_ssize_t before = ctx->out.len;
    sbuf_put_utf8(&ctx->out, s, (Py_ssize_t)strlen(s));
    ctx->column += (int)(ctx->out.len - before);
    ctx->line_has_content = 1;
}

/* Whether an element matches an annotation rule: the tag (or any tag) and, when
   the rule has an attribute condition, that the attribute is present and -- when
   a value is given -- carries it as a whitespace-separated token. */
static int text_rule_matches(text_ctx *ctx, th_node *node, const text_rule *rule) {
    if (!rule->any_tag && (node->ns != TH_NS_HTML || node->atom != rule->tag_atom)) {
        return 0;
    }
    if (rule->attr == NULL) {
        return 1;
    }
    Py_ssize_t idx = th_node_attr_find(ctx->tree, node, rule->attr, rule->attr_len);
    if (idx < 0 || node->attrs[idx].value == NULL) {
        return 0;
    }
    if (rule->value == NULL) {
        return 1;
    }
    const Py_UCS4 *attr_value = node->attrs[idx].value;
    Py_ssize_t attr_len = node->attrs[idx].value_len;
    Py_ssize_t pos = 0;
    while (pos < attr_len) {
        while (pos < attr_len && is_space(attr_value[pos])) {
            pos++;
        }
        Py_ssize_t start = pos;
        while (pos < attr_len && !is_space(attr_value[pos])) {
            pos++;
        }
        if (pos - start == rule->value_len &&
            memcmp(&attr_value[start], rule->value, (size_t)rule->value_len * sizeof(Py_UCS4)) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Record a labeled span over [start, end), trimming surrounding whitespace, one
   entry per label the rule carries. */
static void text_record_span(text_ctx *ctx, Py_ssize_t start, Py_ssize_t end, PyObject *labels) {
    while (start < end && is_space(ctx->out.data[start])) {
        start++;
    }
    while (end > start && is_space(ctx->out.data[end - 1])) {
        end--;
    }
    if (start >= end) {
        return; /* an element with no visible text (or only whitespace) gets no span */
    }
    Py_ssize_t label_count = PyTuple_GET_SIZE(labels);
    for (Py_ssize_t label_index = 0; label_index < label_count; label_index++) {
        if (ctx->span_count == ctx->span_cap) {
            size_t cap;
            size_t bytes;
            int grew =
                th_grow_cap((size_t)(ctx->span_count + 1), (size_t)ctx->span_cap, 8, sizeof(text_span), &cap, &bytes);
            if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
                ctx->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
                return;          /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            }
            text_span *grown = PyMem_Realloc(ctx->spans, bytes);
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                return;          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            ctx->spans = grown;
            ctx->span_cap = (Py_ssize_t)cap;
        }
        ctx->spans[ctx->span_count].start = start;
        ctx->spans[ctx->span_count].end = end;
        ctx->spans[ctx->span_count].label = PyTuple_GET_ITEM(labels, label_index);
        ctx->span_count++;
    }
}

/* Open a span for every rule this element matches, returning how many; the cursor
   offset now is each span's start. */
static Py_ssize_t text_open_annotations(text_ctx *ctx, th_node *node) {
    if (ctx->rules == NULL || ctx->annotate_off) {
        return 0;
    }
    Py_ssize_t opened = 0;
    Py_ssize_t tagged = ctx->n_rules;
    Py_ssize_t wildcard = ctx->n_rules;
    if (ctx->rule_index != NULL) {
        tagged = node->ns == TH_NS_HTML ? ctx->rule_index[node->atom] : ctx->n_rules;
        wildcard = ctx->rule_index[th_tag_count + 1];
    }
    for (Py_ssize_t rule_index = 0; rule_index < ctx->n_rules;) {
        if (ctx->rule_index != NULL) {
            rule_index = tagged < wildcard ? tagged : wildcard;
            if (rule_index == ctx->n_rules) {
                break;
            }
            if (rule_index == tagged) {
                tagged = ctx->rule_index[th_tag_count + 2 + rule_index];
            } else {
                wildcard = ctx->rule_index[th_tag_count + 2 + rule_index];
            }
        }
        const text_rule *rule = &ctx->rules[rule_index++];
        if (!text_rule_matches(ctx, node, rule)) {
            continue;
        }
        if (ctx->active_count == ctx->active_cap) {
            size_t cap;
            size_t bytes;
            int grew = th_grow_cap((size_t)(ctx->active_count + 1), (size_t)ctx->active_cap, 8, sizeof(text_active),
                                   &cap, &bytes);
            if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
                ctx->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
                return opened;   /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            }
            text_active *grown = PyMem_Realloc(ctx->active, bytes);
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                return opened;   /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            ctx->active = grown;
            ctx->active_cap = (Py_ssize_t)cap;
        }
        ctx->active[ctx->active_count].start = ctx->out.len;
        ctx->active[ctx->active_count].rule = rule;
        ctx->active_count++;
        opened++;
    }
    return opened;
}

/* Close the opened spans, recording each over the text emitted since it opened. */
static void text_close_annotations(text_ctx *ctx, Py_ssize_t opened) {
    for (Py_ssize_t index = 0; index < opened; index++) {
        ctx->active_count--;
        text_active *active = &ctx->active[ctx->active_count];
        text_record_span(ctx, active->start, ctx->out.len, active->rule->labels);
    }
}

/* Emit an ASCII option string (a default image alt) through the word machinery
   so it collapses and wraps like any other text. */
static void text_emit_text2(text_ctx *ctx, const char *ascii) {
    for (const char *cursor = ascii; *cursor != '\0'; cursor++) {
        Py_UCS4 ch = (Py_UCS4)(unsigned char)*cursor;
        if (is_space(ch)) {
            ctx->space_pending = 1;
        } else {
            text_emit_word(ctx, &ch, 1);
        }
    }
}

static void text_emit_image(text_ctx *ctx, th_node *node) {
    if (!ctx->opt->images) {
        return;
    }
    Py_ssize_t alt_len;
    const Py_UCS4 *alt = text_attr(ctx->tree, node, "alt", &alt_len);
    if (alt != NULL) {
        text_emit_text(ctx, alt, alt_len);
    } else {
        text_emit_text2(ctx, ctx->opt->default_image_alt);
    }
}

/* Double the full frame stack, moving it off the caller's inline frames on the first
   growth. Returns 0, or -1 after setting ctx->failed. */
static int text_grow_frames(text_ctx *ctx) {
    size_t cap;
    size_t bytes;
    int fits = th_grow_cap((size_t)ctx->frame_cap + 1, (size_t)ctx->frame_cap, 1, sizeof(text_frame), &cap, &bytes);
    if (!fits) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a depth no allocation could hold */
        ctx->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        return -1;       /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    text_frame *grown = ctx->frames == ctx->inline_frames ? PyMem_Malloc(bytes) : PyMem_Realloc(ctx->frames, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (ctx->frames == ctx->inline_frames) {
        memcpy(grown, ctx->frames, (size_t)ctx->frame_count * sizeof(text_frame));
    }
    ctx->frames = grown;
    ctx->frame_cap = (Py_ssize_t)cap;
    return 0;
}

/* Reserve the next frame, with no prefix to restore. NULL after setting ctx->failed
   when the stack cannot grow; the caller then skips the subtree. */
static inline text_frame *text_push(text_ctx *ctx, th_node *node, enum text_walk walk, enum text_leave leave,
                                    Py_ssize_t opened) {
    if (ctx->frame_count == ctx->frame_cap) {
        if (text_grow_frames(ctx) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    text_frame *frame = &ctx->frames[ctx->frame_count++];
    frame->node = node;
    frame->child = NULL;
    frame->opened = opened;
    frame->prefix_base = -1;
    frame->walk = (uint8_t)walk;
    frame->leave = (uint8_t)leave;
    frame->in_run = 0;
    return frame;
}

static void text_render_block(text_ctx *ctx, th_node *node);

static void text_enter_link(text_ctx *ctx, th_node *node, Py_ssize_t opened) {
    const text_opts *opt = ctx->opt;
    Py_ssize_t href_len;
    const Py_UCS4 *href = text_attr(ctx->tree, node, "href", &href_len);
    if (href == NULL || opt->links == TH_TEXT_LINKS_NONE) {
        text_push(ctx, node, TEXT_WALK_INLINE, TEXT_LEAVE_NONE, opened);
        return;
    }
    text_frame *frame =
        text_push(ctx, node, TEXT_WALK_INLINE,
                  opt->links == TH_TEXT_LINKS_INLINE ? TEXT_LEAVE_LINK_INLINE : TEXT_LEAVE_LINK_FOOTNOTE, opened);
    if (frame != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        frame->link.href = href;
        frame->link.href_len = href_len;
    }
}

static void text_render_inline(text_ctx *ctx, th_node *node) {
    if (node->type == TH_NODE_TEXT) {
        text_emit_text(ctx, need_text(ctx->tree, node), node->text_len);
        return;
    }
    if (node->type != TH_NODE_ELEMENT && node->type != TH_NODE_CONTENT) {
        return;
    }
    uint16_t atom = node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN;
    if (is_md_block(atom)) {
        text_render_block(ctx, node); /* text_render_block opens its own annotation */
        return;
    }
    Py_ssize_t opened = text_open_annotations(ctx, node);
    switch (atom) {
    case TH_TAG_A:
        text_enter_link(ctx, node, opened);
        return;
    case TH_TAG_IMG:
        text_emit_image(ctx, node);
        break;
    case TH_TAG_BR:
        text_newline(ctx);
        break;
    case TH_TAG_WBR:
        break;
    default:
        if (!is_md_skipped(node)) {
            text_push(ctx, node, TEXT_WALK_INLINE, TEXT_LEAVE_NONE, opened);
            return;
        }
    }
    text_close_annotations(ctx, opened);
}

/* A run of count spaces appended to the continuation prefix; returns the prior len. */
static Py_ssize_t text_push_indent(text_ctx *ctx, Py_ssize_t count) {
    Py_ssize_t base = ctx->prefix.len;
    for (Py_ssize_t index = 0; index < count; index++) {
        sbuf_putc(&ctx->prefix, ' ');
    }
    return base;
}

static int text_leads_with_inline(text_ctx *ctx, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            for (Py_ssize_t index = 0; index < child->text_len; index++) {
                if (!is_space(text[index])) {
                    return 1;
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
        return !is_md_block(atom);
    }
    return 0;
}

/* Lay out one child of a block container: a block child opens its own block, and an
   inline child joins the run frame->in_run tracks, opening it on a fresh line unless
   it is whitespace alone. */
static inline void text_block_child(text_ctx *ctx, text_frame *frame, th_node *child) {
    int block = 0;
    if (child->type == TH_NODE_ELEMENT) {
        uint16_t atom = child->ns == TH_NS_HTML ? child->atom : TH_TAG_UNKNOWN;
        if (is_md_skipped(child)) {
            return;
        }
        block = is_md_block(atom);
    } else if (child->type == TH_NODE_CONTENT) {
        text_push(ctx, child, TEXT_WALK_BLOCK, TEXT_LEAVE_NONE, 0);
        return;
    } else if (child->type != TH_NODE_TEXT) {
        return;
    }
    if (block) {
        frame->in_run = 0;
        text_render_block(ctx, child);
        return;
    }
    if (!frame->in_run) {
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
        text_block_line(ctx, ctx->tight ? 0 : 1);
        frame->in_run = 1;
    }
    text_render_inline(ctx, child);
}

/* Read a non-negative decimal ordinal from an attribute (an <ol start> or an
   <li value>), storing its leading-digit value in *out; returns 1 when the
   attribute is present and begins with a digit, 0 otherwise. */
static int text_ordinal_attr(text_ctx *ctx, th_node *node, const char *name, Py_ssize_t *out) {
    Py_ssize_t len;
    const Py_UCS4 *attr = text_attr(ctx->tree, node, name, &len);
    if (attr == NULL) {
        return 0;
    }
    Py_ssize_t value = 0;
    int seen = 0;
    for (Py_ssize_t index = 0; index < len && attr[index] >= '0' && attr[index] <= '9'; index++) {
        value = value * 10 + (attr[index] - '0');
        seen = 1;
    }
    if (seen) {
        *out = value;
    }
    return seen;
}

static text_frame *text_enter_list(text_ctx *ctx, th_node *node, Py_ssize_t opened) {
    text_frame *frame = text_push(ctx, node, TEXT_WALK_LIST, TEXT_LEAVE_LIST, opened);
    if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    frame->list.number = 1;
    frame->list.ordered = node->atom == TH_TAG_OL;
    if (frame->list.ordered) {
        text_ordinal_attr(ctx, node, "start", &frame->list.number);
    }
    frame->list.sub_indent = 2; /* how far a bare nested list indents: the last marker's width */
    ctx->list_depth++;
    return frame;
}

static void text_list_child(text_ctx *ctx, text_frame *frame, th_node *child) {
    if (child->type != TH_NODE_ELEMENT || child->ns != TH_NS_HTML) {
        return;
    }
    if (child->atom == TH_TAG_UL || child->atom == TH_TAG_OL || child->atom == TH_TAG_MENU) {
        /* a list nested directly in a list (a sibling of the <li>s) belongs to
           the preceding item; the parser makes this shape and skipping it would
           drop every nested item's text */
        Py_ssize_t base = text_push_indent(ctx, frame->list.sub_indent);
        int saved_tight = ctx->tight;
        ctx->tight = 1;
        text_frame *nested = text_enter_list(ctx, child, text_open_annotations(ctx, child));
        if (nested != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            nested->prefix_base = base;
            nested->saved_tight = saved_tight;
        }
        return;
    }
    if (child->atom != TH_TAG_LI) {
        return;
    }
    text_block_line(ctx, 0);
    Py_ssize_t width;
    if (frame->list.ordered) {
        /* WHATWG: <li value> sets this item's ordinal; the rest count up from it */
        text_ordinal_attr(ctx, child, "value", &frame->list.number);
        width = md_put_decimal(&ctx->out, frame->list.number) + 2;
        text_emit_ascii(ctx, ". ");
        frame->list.number++;
    } else {
        Py_ssize_t before = ctx->out.len;
        text_put_literal(ctx, ctx->opt->bullet);
        width = ctx->out.len - before;
    }
    ctx->column = (int)(ctx->prefix.len + width);
    ctx->line_has_content = 1;
    frame->list.sub_indent = width;
    Py_ssize_t base = text_push_indent(ctx, width);
    int saved_tight = ctx->tight;
    ctx->tight = 1;
    ctx->suppress_break = text_leads_with_inline(ctx, child);
    text_frame *item = text_push(ctx, child, TEXT_WALK_BLOCK, TEXT_LEAVE_NONE, 0);
    if (item != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        item->prefix_base = base;
        item->saved_tight = saved_tight;
    }
}

/* Collect the table's rows in document order across nested thead/tbody/tfoot wrappers
   and return the widest row's column count. With rows NULL it only counts, so the
   array is sized by the same walk that fills it. */
static Py_ssize_t text_collect_rows(th_node *table, th_node **rows, Py_ssize_t *count) {
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

static void text_enter_table(text_ctx *ctx, th_node *node, Py_ssize_t opened) {
    Py_ssize_t cap = 0;
    text_collect_rows(node, NULL, &cap);
    th_node **rows = PyMem_Malloc((size_t)(cap > 0 ? cap : 1) * sizeof(th_node *));
    if (rows == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    Py_ssize_t columns = text_collect_rows(node, rows, &count);
    if (count == 0 || columns == 0) {
        PyMem_Free(rows);
        text_close_annotations(ctx, opened);
        return;
    }
    text_table *table = PyMem_Calloc(1, sizeof(text_table));
    sbuf *grid = PyMem_Calloc((size_t)(count * columns), sizeof(sbuf));
    Py_ssize_t *widths = PyMem_Calloc((size_t)columns, sizeof(Py_ssize_t));
    th_node **cells = PyMem_Calloc((size_t)(count * columns), sizeof(th_node *));
    text_frame *frame = NULL;
    if (table != NULL && grid != NULL && widths != NULL && cells != NULL) { /* GCOVR_EXCL_BR_LINE: out of memory */
        frame = text_push(ctx, node, TEXT_WALK_TABLE, TEXT_LEAVE_TABLE, opened);
    }
    if (frame == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(table);   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(grid);    /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(widths);  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(cells);   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(rows);    /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *table =
        (text_table){.rows = rows, .grid = grid, .widths = widths, .cells = cells, .count = count, .columns = columns};
    frame->table = table;
}

/* Start rendering the table's next cell into a throwaway buffer, flattening its newlines
   to spaces once it is done, for measuring and padding into the grid. Returns 0 once every
   row is done. */
static int text_enter_cell(text_ctx *ctx, text_table *table) {
    th_node *cell = table->cell == NULL ? table->rows[0]->first_child : table->cell->next_sibling;
    for (;;) {
        while (cell != NULL &&
               (cell->type != TH_NODE_ELEMENT || (cell->atom != TH_TAG_TD && cell->atom != TH_TAG_TH))) {
            cell = cell->next_sibling;
        }
        if (cell != NULL) {
            break;
        }
        if (++table->row == table->count) {
            return 0;
        }
        table->column = 0;
        cell = table->rows[table->row]->first_child;
    }
    table->cell = cell;
    text_frame *frame = text_push(ctx, cell, TEXT_WALK_INLINE, TEXT_LEAVE_CELL, 0);
    if (frame == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    frame->table = table;
    table->saved_out = ctx->out;
    table->saved_prefix = ctx->prefix;
    table->saved_line = ctx->line_has_content;
    table->saved_column = ctx->column;
    table->saved_space = ctx->space_pending;
    table->saved_started = ctx->started;
    ctx->out = (sbuf){NULL, 0, 0, 0};
    ctx->prefix = (sbuf){NULL, 0, 0, 0};
    ctx->line_has_content = 1;
    ctx->column = 0;
    ctx->space_pending = 0;
    /* a cell renders into a throwaway buffer, so its offsets do not map to the
       grid; suppress span recording and let the placement loop annotate cells */
    ctx->annotate_off++;
    return 1;
}

static void text_leave_cell(text_ctx *ctx, text_table *table) {
    ctx->annotate_off--;
    sbuf rendered = ctx->out;
    PyMem_Free(ctx->prefix.data);
    ctx->out = table->saved_out;
    ctx->prefix = table->saved_prefix;
    ctx->line_has_content = table->saved_line;
    ctx->column = table->saved_column;
    ctx->space_pending = table->saved_space;
    ctx->started = table->saved_started;
    Py_ssize_t slot = table->row * table->columns + table->column;
    sbuf *dst = &table->grid[slot];
    for (Py_ssize_t index = 0; index < rendered.len; index++) {
        sbuf_putc(dst, rendered.data[index] == '\n' ? ' ' : rendered.data[index]);
    }
    PyMem_Free(rendered.data);
    table->cells[slot] = table->cell;
    if (dst->len > table->widths[table->column]) {
        table->widths[table->column] = dst->len;
    }
    table->column++;
}

static void text_leave_table(text_ctx *ctx, text_table *table) {
    Py_ssize_t count = table->count, columns = table->columns;
    text_block_line(ctx, 1);
    for (Py_ssize_t row_index = 0; row_index < count; row_index++) {
        if (row_index > 0) {
            text_newline(ctx);
        }
        for (Py_ssize_t col_index = 0; col_index < columns; col_index++) {
            if (col_index > 0) {
                text_emit_ascii(ctx, ctx->opt->cell_separator);
            }
            sbuf *cell = &table->grid[row_index * columns + col_index];
            th_node *cell_node = table->cells[row_index * columns + col_index];
            /* annotate the cell element over its placed (not throwaway) text */
            Py_ssize_t opened = cell_node != NULL ? text_open_annotations(ctx, cell_node) : 0;
            sbuf_put_run(&ctx->out, cell->data, cell->len);
            text_close_annotations(ctx, opened);
            /* the last column needs no padding: nothing follows it on the line */
            if (col_index + 1 < columns) {
                for (Py_ssize_t pad = cell->len; pad < table->widths[col_index]; pad++) {
                    sbuf_putc(&ctx->out, ' ');
                }
            }
        }
        ctx->line_has_content = 1;
    }
    for (Py_ssize_t cell_index = 0; cell_index < count * columns; cell_index++) {
        PyMem_Free(table->grid[cell_index].data);
    }
    PyMem_Free(table->grid);
    PyMem_Free(table->widths);
    PyMem_Free(table->cells);
    PyMem_Free(table->rows);
    PyMem_Free(table);
}

static void text_render_pre(text_ctx *ctx, th_node *node) {
    Py_ssize_t text_len;
    Py_UCS4 *text = th_node_text(ctx->tree, node, &text_len);
    if (text == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t end = text_len;
    if (end > 0 && text[end - 1] == '\n') {
        end--;
    }
    text_block_line(ctx, 1);
    for (Py_ssize_t index = 0; index < end; index++) {
        if (text[index] == '\n') {
            text_newline(ctx);
        } else {
            sbuf_putc(&ctx->out, text[index]);
            ctx->line_has_content = 1;
        }
    }
    PyMem_Free(text);
}

static void text_render_block(text_ctx *ctx, th_node *node) {
    Py_ssize_t opened = text_open_annotations(ctx, node);
    switch (node->atom) {
    case TH_TAG_UL:
    case TH_TAG_MENU:
    case TH_TAG_OL:
        text_enter_list(ctx, node, opened);
        return;
    case TH_TAG_PRE:
        text_render_pre(ctx, node);
        text_close_annotations(ctx, opened);
        return;
    case TH_TAG_TABLE:
        text_enter_table(ctx, node, opened);
        return;
    case TH_TAG_BLOCKQUOTE: {
        Py_ssize_t base = text_push_indent(ctx, 4);
        int saved_tight = ctx->tight;
        ctx->tight = 0;
        if (ctx->started) {
            sbuf_putc(&ctx->out, '\n');
            if (!saved_tight) {
                sbuf_putc(&ctx->out, '\n');
            }
            text_write_prefix(ctx);
            ctx->line_has_content = 0;
            ctx->pending_loose = 1;
            ctx->suppress_break = 1;
        }
        text_frame *frame = text_push(ctx, node, TEXT_WALK_BLOCK, TEXT_LEAVE_NONE, opened);
        if (frame != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            frame->prefix_base = base;
            frame->saved_tight = saved_tight;
        }
        return;
    }
    default:
        text_push(ctx, node, TEXT_WALK_BLOCK, TEXT_LEAVE_NONE, opened);
        return;
    }
}

static void text_leave(text_ctx *ctx) {
    text_frame *frame = &ctx->frames[--ctx->frame_count];
    if (frame->leave == TEXT_LEAVE_NONE && frame->prefix_base < 0 && frame->opened == 0) {
        return;
    }
    switch (frame->leave) {
    case TEXT_LEAVE_LINK_INLINE:
        text_emit_ascii(ctx, " (");
        sbuf_put_run(&ctx->out, frame->link.href, frame->link.href_len);
        ctx->column += (int)frame->link.href_len;
        text_emit_ascii(ctx, ")");
        break;
    case TEXT_LEAVE_LINK_FOOTNOTE: {
        Py_ssize_t number = text_add_reference(ctx, frame->link.href, frame->link.href_len);
        if (ctx->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            break;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        sbuf_putc(&ctx->out, '[');
        md_put_decimal(&ctx->out, number);
        sbuf_putc(&ctx->out, ']');
        ctx->line_has_content = 1;
        break;
    }
    case TEXT_LEAVE_LIST:
        ctx->list_depth--;
        break;
    case TEXT_LEAVE_CELL:
        text_leave_cell(ctx, frame->table);
        break;
    case TEXT_LEAVE_TABLE:
        text_leave_table(ctx, frame->table);
        break;
    default:
        break;
    }
    if (frame->prefix_base >= 0) {
        ctx->tight = frame->saved_tight;
        ctx->prefix.len = frame->prefix_base;
    }
    text_close_annotations(ctx, frame->opened);
}

/* Render the pushed frames to completion. Each step renders the next child of the
   innermost frame, which may push frames of its own, so the C stack stays flat at any
   nesting depth. The next child is read only once the previous one is done, as a
   recursive walk would. */
static void text_run(text_ctx *ctx) {
    while (ctx->frame_count > 0) {
        text_frame *frame = &ctx->frames[ctx->frame_count - 1];
        th_node *child = NULL;
        if (frame->walk == TEXT_WALK_TABLE) {
            if (text_enter_cell(ctx, frame->table)) {
                continue;
            }
        } else {
            child = frame->child == NULL ? frame->node->first_child : frame->child->next_sibling;
            /* text lays out without pushing a frame, so a run of it renders in place */
            if (frame->walk == TEXT_WALK_INLINE) {
                for (; child != NULL && child->type == TH_NODE_TEXT; child = child->next_sibling) {
                    text_emit_text(ctx, need_text(ctx->tree, child), child->text_len);
                }
            } else if (frame->walk == TEXT_WALK_BLOCK) {
                for (; child != NULL && child->type == TH_NODE_TEXT; child = child->next_sibling) {
                    text_block_child(ctx, frame, child);
                }
            }
        }
        if (child == NULL) {
            text_leave(ctx);
            continue;
        }
        frame->child = child;
        switch (frame->walk) {
        case TEXT_WALK_INLINE:
            text_render_inline(ctx, child);
            break;
        case TEXT_WALK_BLOCK:
            text_block_child(ctx, frame, child);
            break;
        default:
            text_list_child(ctx, frame, child);
            break;
        }
    }
}

static void text_flush_references(text_ctx *ctx) {
    if (ctx->ref_count == 0) {
        return;
    }
    sbuf_puts(&ctx->out, "\n\n");
    for (Py_ssize_t ref_index = 0; ref_index < ctx->ref_count; ref_index++) {
        if (ref_index > 0) {
            sbuf_putc(&ctx->out, '\n');
        }
        sbuf_putc(&ctx->out, '[');
        md_put_decimal(&ctx->out, ref_index + 1);
        sbuf_puts(&ctx->out, "] ");
        sbuf_put_run(&ctx->out, ctx->refs[ref_index].url, ctx->refs[ref_index].url_len);
    }
}

/* Walk node into ctx (the dispatch shared by the plain and annotated entries). */
static void text_render_root(text_ctx *ctx, th_node *node) {
    text_frame inline_frames[TEXT_INLINE_FRAMES];
    ctx->frames = ctx->inline_frames = inline_frames;
    ctx->frame_cap = TEXT_INLINE_FRAMES;
    sbuf_presize_for_root(&ctx->out, ctx->tree, node);
    if (node->type == TH_NODE_TEXT) {
        ctx->started = 1;
        ctx->line_has_content = 1;
        text_emit_text(ctx, need_text(ctx->tree, node), node->text_len);
    } else if (is_md_block(node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN)) {
        text_render_block(ctx, node);
    } else {
        text_push(ctx, node, TEXT_WALK_BLOCK, TEXT_LEAVE_NONE, 0);
    }
    text_run(ctx);
    if (ctx->frames != inline_frames) {
        PyMem_Free(ctx->frames);
    }
    text_flush_references(ctx);
    ctx->out.failed |= ctx->failed;
}

Py_UCS4 *th_node_layout_text(th_tree *tree, th_node *node, const text_opts *opt, Py_ssize_t *out_len) {
    text_ctx ctx = {0};
    ctx.tree = tree;
    ctx.opt = opt;
    text_render_root(&ctx, node);
    PyMem_Free(ctx.prefix.data);
    PyMem_Free(ctx.refs);
    md_trim(&ctx.out, TH_MD_DOC_STRIP);
    return sbuf_finish(&ctx.out, out_len);
}

Py_UCS4 *th_node_annotated_text(th_tree *tree, th_node *node, const text_opts *opt, const text_rule *rules,
                                Py_ssize_t n_rules, text_span **out_spans, Py_ssize_t *out_span_count,
                                Py_ssize_t *out_len) {
    text_ctx ctx = {0};
    ctx.tree = tree;
    ctx.opt = opt;
    ctx.rules = rules;
    ctx.n_rules = n_rules;
    if (n_rules >= 8) {
        size_t capacity;
        size_t bytes;
        /* GCOVR_EXCL_BR_START: allocation size overflow */
        if (!th_grow_cap((size_t)n_rules + (size_t)th_tag_count + 2, 0, 8, sizeof(Py_ssize_t), &capacity, &bytes)) {
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation size overflow */
            return NULL;      /* GCOVR_EXCL_LINE: allocation size overflow */
        }
        /* GCOVR_EXCL_BR_STOP */
        ctx.rule_index = PyMem_Malloc(bytes);
        if (ctx.rule_index == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            PyErr_NoMemory();         /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;              /* GCOVR_EXCL_LINE: allocation failure */
        }
        for (int atom = 0; atom < th_tag_count + 2; atom++) {
            ctx.rule_index[atom] = n_rules;
        }
        /* Keep span order stable across tag and wildcard rules. */
        for (Py_ssize_t index = n_rules; index-- > 0;) {
            int atom = rules[index].any_tag ? th_tag_count + 1 : rules[index].tag_atom;
            ctx.rule_index[th_tag_count + 2 + index] = ctx.rule_index[atom];
            ctx.rule_index[atom] = index;
        }
    }
    text_render_root(&ctx, node);
    PyMem_Free(ctx.rule_index);
    PyMem_Free(ctx.prefix.data);
    PyMem_Free(ctx.refs);
    PyMem_Free(ctx.active);
    /* the doc-strip removes leading blank lines, shifting every offset back by the
       same amount; record_span already trimmed each span to non-blank bounds, so
       the trailing strip never lands inside one and no clamping is needed */
    Py_ssize_t front = md_trim(&ctx.out, TH_MD_DOC_STRIP);
    for (Py_ssize_t span_index = 0; span_index < ctx.span_count; span_index++) {
        ctx.spans[span_index].start -= front;
        ctx.spans[span_index].end -= front;
    }
    *out_spans = ctx.spans;
    *out_span_count = ctx.span_count;
    return sbuf_finish(&ctx.out, out_len);
}
