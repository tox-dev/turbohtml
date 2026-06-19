/* Layout-aware HTML-to-text export (the inscriptis role), #included into
   treebuilder.c after treebuilder_markdown.h so it shares sbuf, need_text(), the
   tag atoms, the attribute lookup, and md_is_ws()/is_md_block()/is_md_skipped().
   Unlike to_markdown it emits no markup: it keeps the visual structure of the
   page as plain text, with blocks separated by blank lines, lists indented under
   their bullets, and tables laid out as a column-aligned grid. */

/* ----------------------------------------------------------------- options */

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

/* -------------------------------------------------------------- the cursor */

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

typedef struct {
    sbuf out;
    th_tree *tree;
    const text_opts *opt;
    sbuf prefix;          /* spaces every continuation line starts with (list/quote indent) */
    text_reference *refs; /* footnote-link targets */
    Py_ssize_t ref_count;
    Py_ssize_t ref_cap;
    const text_rule *rules; /* annotation rules, or NULL when not annotating */
    Py_ssize_t n_rules;
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
    Py_ssize_t i = 0;
    while (i < len) {
        if (md_is_ws(text[i])) {
            ctx->space_pending = 1;
            i++;
            continue;
        }
        Py_ssize_t start = i;
        while (i < len && !md_is_ws(text[i])) {
            i++;
        }
        text_emit_word(ctx, &text[start], i - start);
    }
}

static const Py_UCS4 *text_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t *len) {
    return md_attr(tree, node, name, len);
}

static Py_ssize_t text_add_reference(text_ctx *ctx, const Py_UCS4 *url, Py_ssize_t url_len) {
    if (ctx->ref_count == ctx->ref_cap) {
        Py_ssize_t cap = ctx->ref_cap ? ctx->ref_cap * 2 : 8;
        text_reference *grown = PyMem_Realloc(ctx->refs, (size_t)cap * sizeof(text_reference));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return 0;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ctx->refs = grown;
        ctx->ref_cap = cap;
    }
    ctx->refs[ctx->ref_count].url = url;
    ctx->refs[ctx->ref_count].url_len = url_len;
    return ++ctx->ref_count;
}

/* Emit a run of ASCII characters (a bracket, a footnote number's punctuation). */
static void text_emit_ascii(text_ctx *ctx, const char *s) {
    for (const char *c = s; *c != '\0'; c++) {
        sbuf_putc(&ctx->out, (Py_UCS4)(unsigned char)*c);
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
    const Py_UCS4 *av = node->attrs[idx].value;
    Py_ssize_t avlen = node->attrs[idx].value_len;
    Py_ssize_t i = 0;
    while (i < avlen) {
        while (i < avlen && md_is_ws(av[i])) {
            i++;
        }
        Py_ssize_t start = i;
        while (i < avlen && !md_is_ws(av[i])) {
            i++;
        }
        if (i - start == rule->value_len &&
            memcmp(&av[start], rule->value, (size_t)rule->value_len * sizeof(Py_UCS4)) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Record a labeled span over [start, end), trimming surrounding whitespace, one
   entry per label the rule carries. */
static void text_record_span(text_ctx *ctx, Py_ssize_t start, Py_ssize_t end, PyObject *labels) {
    while (start < end && md_is_ws(ctx->out.data[start])) {
        start++;
    }
    while (end > start && md_is_ws(ctx->out.data[end - 1])) {
        end--;
    }
    if (start >= end) {
        return; /* an element with no visible text (or only whitespace) gets no span */
    }
    Py_ssize_t n = PyTuple_GET_SIZE(labels);
    for (Py_ssize_t i = 0; i < n; i++) {
        if (ctx->span_count == ctx->span_cap) {
            Py_ssize_t cap = ctx->span_cap ? ctx->span_cap * 2 : 8;
            text_span *grown = PyMem_Realloc(ctx->spans, (size_t)cap * sizeof(text_span));
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                return;          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            ctx->spans = grown;
            ctx->span_cap = cap;
        }
        ctx->spans[ctx->span_count].start = start;
        ctx->spans[ctx->span_count].end = end;
        ctx->spans[ctx->span_count].label = PyTuple_GET_ITEM(labels, i);
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
    for (Py_ssize_t r = 0; r < ctx->n_rules; r++) {
        if (!text_rule_matches(ctx, node, &ctx->rules[r])) {
            continue;
        }
        if (ctx->active_count == ctx->active_cap) {
            Py_ssize_t cap = ctx->active_cap ? ctx->active_cap * 2 : 8;
            text_active *grown = PyMem_Realloc(ctx->active, (size_t)cap * sizeof(text_active));
            if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                return opened;   /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            ctx->active = grown;
            ctx->active_cap = cap;
        }
        ctx->active[ctx->active_count].start = ctx->out.len;
        ctx->active[ctx->active_count].rule = &ctx->rules[r];
        ctx->active_count++;
        opened++;
    }
    return opened;
}

/* Close the opened spans, recording each over the text emitted since it opened. */
static void text_close_annotations(text_ctx *ctx, Py_ssize_t opened) {
    for (Py_ssize_t i = 0; i < opened; i++) {
        ctx->active_count--;
        text_active *a = &ctx->active[ctx->active_count];
        text_record_span(ctx, a->start, ctx->out.len, a->rule->labels);
    }
}

static void text_render_inline(text_ctx *ctx, th_node *node);
static void text_emit_text2(text_ctx *ctx, const char *s);

static void text_inline_children(text_ctx *ctx, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        text_render_inline(ctx, child);
    }
}

static void text_emit_link(text_ctx *ctx, th_node *node) {
    const text_opts *opt = ctx->opt;
    Py_ssize_t href_len;
    const Py_UCS4 *href = text_attr(ctx->tree, node, "href", &href_len);
    if (href == NULL || opt->links == TH_TEXT_LINKS_NONE) {
        text_inline_children(ctx, node);
        return;
    }
    if (opt->links == TH_TEXT_LINKS_INLINE) {
        text_inline_children(ctx, node);
        text_emit_ascii(ctx, " (");
        sbuf_put_run(&ctx->out, href, href_len);
        ctx->column += (int)href_len;
        text_emit_ascii(ctx, ")");
        return;
    }
    text_inline_children(ctx, node);
    Py_ssize_t number = text_add_reference(ctx, href, href_len);
    if (ctx->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    sbuf_putc(&ctx->out, '[');
    md_put_decimal(&ctx->out, number);
    sbuf_putc(&ctx->out, ']');
    ctx->line_has_content = 1;
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

static void text_render_block(text_ctx *ctx, th_node *node);
static void text_block_children(text_ctx *ctx, th_node *node);

static void text_render_inline_element(text_ctx *ctx, th_node *node, uint16_t atom) {
    if (atom == TH_TAG_A) {
        text_emit_link(ctx, node);
        return;
    }
    if (atom == TH_TAG_IMG) {
        text_emit_image(ctx, node);
        return;
    }
    if (atom == TH_TAG_BR) {
        text_newline(ctx);
        return;
    }
    if (atom == TH_TAG_WBR) {
        return;
    }
    if (is_md_skipped(atom)) {
        return;
    }
    text_inline_children(ctx, node);
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
    text_render_inline_element(ctx, node, atom);
    text_close_annotations(ctx, opened);
}

/* Emit an ASCII option string (a default image alt) through the word machinery
   so it collapses and wraps like any other text. */
static void text_emit_text2(text_ctx *ctx, const char *s) {
    for (const char *c = s; *c != '\0'; c++) {
        Py_UCS4 ch = (Py_UCS4)(unsigned char)*c;
        if (md_is_ws(ch)) {
            ctx->space_pending = 1;
        } else {
            text_emit_word(ctx, &ch, 1);
        }
    }
}

/* ------------------------------------------------------------ block output */

/* A run of n spaces appended to the continuation prefix; returns the prior len. */
static Py_ssize_t text_push_indent(text_ctx *ctx, Py_ssize_t n) {
    Py_ssize_t base = ctx->prefix.len;
    for (Py_ssize_t i = 0; i < n; i++) {
        sbuf_putc(&ctx->prefix, ' ');
    }
    return base;
}

static int text_leads_with_inline(text_ctx *ctx, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            for (Py_ssize_t i = 0; i < child->text_len; i++) {
                if (!md_is_ws(text[i])) {
                    return 1;
                }
            }
            continue;
        }
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        uint16_t atom = child->ns == TH_NS_HTML ? child->atom : TH_TAG_UNKNOWN;
        if (is_md_skipped(atom)) {
            continue;
        }
        return !is_md_block(atom);
    }
    return 0;
}

static void text_block_children(text_ctx *ctx, th_node *node) {
    int in_run = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        uint16_t atom = TH_TAG_UNKNOWN;
        int block = 0;
        if (child->type == TH_NODE_ELEMENT) {
            atom = child->ns == TH_NS_HTML ? child->atom : TH_TAG_UNKNOWN;
            if (is_md_skipped(atom)) {
                continue;
            }
            block = is_md_block(atom);
        } else if (child->type == TH_NODE_CONTENT) {
            text_block_children(ctx, child);
            continue;
        } else if (child->type != TH_NODE_TEXT) {
            continue;
        }
        if (block) {
            in_run = 0;
            text_render_block(ctx, child);
            continue;
        }
        if (!in_run) {
            int only_ws = child->type == TH_NODE_TEXT;
            if (only_ws) {
                const Py_UCS4 *text = need_text(ctx->tree, child);
                for (Py_ssize_t i = 0; i < child->text_len; i++) {
                    if (!md_is_ws(text[i])) {
                        only_ws = 0;
                        break;
                    }
                }
            }
            if (only_ws) {
                continue;
            }
            text_block_line(ctx, ctx->tight ? 0 : 1);
            in_run = 1;
        }
        text_render_inline(ctx, child);
    }
}

static void text_render_list(text_ctx *ctx, th_node *node, int ordered) {
    Py_ssize_t number = 1;
    if (ordered) {
        Py_ssize_t start_len;
        const Py_UCS4 *start = text_attr(ctx->tree, node, "start", &start_len);
        if (start != NULL) {
            Py_ssize_t value = 0;
            int seen = 0;
            for (Py_ssize_t i = 0; i < start_len; i++) {
                if (start[i] >= '0' && start[i] <= '9') {
                    value = value * 10 + (start[i] - '0');
                    seen = 1;
                } else {
                    break;
                }
            }
            if (seen) {
                number = value;
            }
        }
    }
    ctx->list_depth++;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT || child->ns != TH_NS_HTML || child->atom != TH_TAG_LI) {
            continue;
        }
        text_block_line(ctx, 0);
        Py_ssize_t width;
        if (ordered) {
            width = md_put_decimal(&ctx->out, number) + 2;
            text_emit_ascii(ctx, ". ");
            number++;
        } else {
            Py_ssize_t before = ctx->out.len;
            text_put_literal(ctx, ctx->opt->bullet);
            width = ctx->out.len - before;
        }
        ctx->column = (int)(ctx->prefix.len + width);
        ctx->line_has_content = 1;
        Py_ssize_t base = text_push_indent(ctx, width);
        int saved_tight = ctx->tight;
        ctx->tight = 1;
        ctx->suppress_break = text_leads_with_inline(ctx, child);
        text_block_children(ctx, child);
        ctx->tight = saved_tight;
        ctx->prefix.len = base;
    }
    ctx->list_depth--;
}

/* Render a table cell's inline content into dst with newlines flattened to
   spaces, for measuring and padding into the grid. */
static void text_cell_text(text_ctx *ctx, th_node *node, sbuf *dst) {
    sbuf saved_out = ctx->out;
    sbuf saved_prefix = ctx->prefix;
    int saved_line = ctx->line_has_content, saved_col = ctx->column, saved_space = ctx->space_pending;
    int saved_started = ctx->started;
    ctx->out = (sbuf){NULL, 0, 0, 0};
    ctx->prefix = (sbuf){NULL, 0, 0, 0};
    ctx->line_has_content = 1;
    ctx->column = 0;
    ctx->space_pending = 0;
    /* a cell renders into a throwaway buffer, so its offsets do not map to the
       grid; suppress span recording and let the placement loop annotate cells */
    ctx->annotate_off++;
    text_inline_children(ctx, node);
    ctx->annotate_off--;
    sbuf rendered = ctx->out;
    PyMem_Free(ctx->prefix.data);
    ctx->out = saved_out;
    ctx->prefix = saved_prefix;
    ctx->line_has_content = saved_line;
    ctx->column = saved_col;
    ctx->space_pending = saved_space;
    ctx->started = saved_started;
    for (Py_ssize_t i = 0; i < rendered.len; i++) {
        sbuf_putc(dst, rendered.data[i] == '\n' ? ' ' : rendered.data[i]);
    }
    PyMem_Free(rendered.data);
}

static Py_ssize_t text_collect_rows(th_node *node, th_node **rows, Py_ssize_t *count, Py_ssize_t *columns) {
    Py_ssize_t cols = *columns;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (child->atom == TH_TAG_TR) {
            rows[*count] = child;
            (*count)++;
            Py_ssize_t cells = md_row_cells(child);
            if (cells > cols) {
                cols = cells;
            }
        } else if (child->atom == TH_TAG_THEAD || child->atom == TH_TAG_TBODY || child->atom == TH_TAG_TFOOT) {
            text_collect_rows(child, rows, count, &cols);
        }
    }
    *columns = cols;
    return cols;
}

static void text_render_table(text_ctx *ctx, th_node *node) {
    Py_ssize_t cap = 0;
    for (th_node *body = node->first_child; body != NULL; body = body->next_sibling) {
        cap += 1;
        for (th_node *row = body->first_child; row != NULL; row = row->next_sibling) {
            cap += 1;
        }
    }
    th_node **rows = PyMem_Malloc((size_t)(cap > 0 ? cap : 1) * sizeof(th_node *));
    if (rows == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0, columns = 0;
    text_collect_rows(node, rows, &count, &columns);
    if (count == 0 || columns == 0) {
        PyMem_Free(rows);
        return;
    }
    sbuf *grid = PyMem_Calloc((size_t)(count * columns), sizeof(sbuf));
    Py_ssize_t *widths = PyMem_Calloc((size_t)columns, sizeof(Py_ssize_t));
    th_node **cells = PyMem_Calloc((size_t)(count * columns), sizeof(th_node *));
    if (grid == NULL || widths == NULL || cells == NULL) { /* GCOVR_EXCL_BR_LINE: cannot force an allocation failure */
        PyMem_Free(grid);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(widths);                                /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(cells);                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(rows);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->out.failed = 1;                               /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t r = 0; r < count; r++) {
        Py_ssize_t col = 0;
        for (th_node *cell = rows[r]->first_child; cell != NULL; cell = cell->next_sibling) {
            if (cell->type != TH_NODE_ELEMENT || (cell->atom != TH_TAG_TD && cell->atom != TH_TAG_TH)) {
                continue;
            }
            text_cell_text(ctx, cell, &grid[r * columns + col]);
            cells[r * columns + col] = cell;
            if (grid[r * columns + col].len > widths[col]) {
                widths[col] = grid[r * columns + col].len;
            }
            col++;
        }
    }
    text_block_line(ctx, 1);
    for (Py_ssize_t r = 0; r < count; r++) {
        if (r > 0) {
            text_newline(ctx);
        }
        for (Py_ssize_t c = 0; c < columns; c++) {
            if (c > 0) {
                text_emit_ascii(ctx, ctx->opt->cell_separator);
            }
            sbuf *cell = &grid[r * columns + c];
            th_node *cell_node = cells[r * columns + c];
            /* annotate the cell element over its placed (not throwaway) text */
            Py_ssize_t opened = cell_node != NULL ? text_open_annotations(ctx, cell_node) : 0;
            sbuf_put_run(&ctx->out, cell->data, cell->len);
            text_close_annotations(ctx, opened);
            /* the last column needs no padding: nothing follows it on the line */
            if (c + 1 < columns) {
                for (Py_ssize_t pad = cell->len; pad < widths[c]; pad++) {
                    sbuf_putc(&ctx->out, ' ');
                }
            }
        }
        ctx->line_has_content = 1;
    }
    for (Py_ssize_t i = 0; i < count * columns; i++) {
        PyMem_Free(grid[i].data);
    }
    PyMem_Free(grid);
    PyMem_Free(widths);
    PyMem_Free(cells);
    PyMem_Free(rows);
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
    for (Py_ssize_t i = 0; i < end; i++) {
        if (text[i] == '\n') {
            text_newline(ctx);
        } else {
            sbuf_putc(&ctx->out, text[i]);
            ctx->line_has_content = 1;
        }
    }
    PyMem_Free(text);
}

static void text_render_block_inner(text_ctx *ctx, th_node *node) {
    uint16_t atom = node->atom;
    switch (atom) {
    case TH_TAG_UL:
    case TH_TAG_MENU:
        text_render_list(ctx, node, 0);
        return;
    case TH_TAG_OL:
        text_render_list(ctx, node, 1);
        return;
    case TH_TAG_PRE:
        text_render_pre(ctx, node);
        return;
    case TH_TAG_TABLE:
        text_render_table(ctx, node);
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
        text_block_children(ctx, node);
        ctx->tight = saved_tight;
        ctx->prefix.len = base;
        return;
    }
    default:
        text_block_children(ctx, node);
        return;
    }
}

static void text_render_block(text_ctx *ctx, th_node *node) {
    Py_ssize_t opened = text_open_annotations(ctx, node);
    text_render_block_inner(ctx, node);
    text_close_annotations(ctx, opened);
}

static void text_flush_references(text_ctx *ctx) {
    if (ctx->ref_count == 0) {
        return;
    }
    sbuf_puts(&ctx->out, "\n\n");
    for (Py_ssize_t i = 0; i < ctx->ref_count; i++) {
        if (i > 0) {
            sbuf_putc(&ctx->out, '\n');
        }
        sbuf_putc(&ctx->out, '[');
        md_put_decimal(&ctx->out, i + 1);
        sbuf_puts(&ctx->out, "] ");
        sbuf_put_run(&ctx->out, ctx->refs[i].url, ctx->refs[i].url_len);
    }
}

/* Walk node into ctx (the dispatch shared by the plain and annotated entries). */
static void text_render_root(text_ctx *ctx, th_node *node) {
    sbuf_presize_for_root(&ctx->out, ctx->tree, node);
    if (node->type == TH_NODE_TEXT) {
        ctx->started = 1;
        ctx->line_has_content = 1;
        text_emit_text(ctx, need_text(ctx->tree, node), node->text_len);
    } else if (is_md_block(node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN)) {
        text_render_block(ctx, node);
    } else {
        text_block_children(ctx, node);
    }
    text_flush_references(ctx);
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
    text_render_root(&ctx, node);
    PyMem_Free(ctx.prefix.data);
    PyMem_Free(ctx.refs);
    PyMem_Free(ctx.active);
    /* the doc-strip removes leading blank lines, shifting every offset back by the
       same amount; record_span already trimmed each span to non-blank bounds, so
       the trailing strip never lands inside one and no clamping is needed */
    Py_ssize_t front = md_trim(&ctx.out, TH_MD_DOC_STRIP);
    for (Py_ssize_t i = 0; i < ctx.span_count; i++) {
        ctx.spans[i].start -= front;
        ctx.spans[i].end -= front;
    }
    *out_spans = ctx.spans;
    *out_span_count = ctx.span_count;
    return sbuf_finish(&ctx.out, out_len);
}
