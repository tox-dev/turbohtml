/* GitHub-Flavored-Markdown export, #included into treebuilder.c after
   treebuilder_serialize.h so it shares the sbuf buffer, need_text(), the tag
   atoms, and the attribute lookup. The walk is a recursive descent over the
   finished tree (the same shape collect_text() uses): block elements are laid
   out vertically with collapsed blank-line margins, inline elements wrap their
   content in markdown markers, and runs of whitespace collapse to one space the
   way the CSS normal flow does. Output is opinionated GFM with no options, so a
   `scrape -> markdown` pipeline needs no second dependency.

   The single non-obvious piece is whitespace. Text whitespace is never emitted
   eagerly: a run sets space_pending, and the deferred space is flushed (as one
   space) only just before the next real character, and only when the current
   line already holds content. That one rule collapses runs, drops the space at
   block and line starts, and — because a closing marker does not flush — moves a
   trailing inner space out of `**bold** ` instead of leaving `**bold **`, which
   is invalid markdown. */

/* ----------------------------------------------------------- block classes */

/* A block-level element opens its own line(s); everything else is inline and
   flows into the surrounding text. Unknown (custom) tags flow inline, matching
   how a browser lays out an undisplayed custom element's text. */
static int is_md_block(uint16_t atom) {
    switch (atom) {
    case TH_TAG_HTML:
    case TH_TAG_BODY:
    case TH_TAG_P:
    case TH_TAG_DIV:
    case TH_TAG_SECTION:
    case TH_TAG_ARTICLE:
    case TH_TAG_HEADER:
    case TH_TAG_FOOTER:
    case TH_TAG_NAV:
    case TH_TAG_ASIDE:
    case TH_TAG_MAIN:
    case TH_TAG_FIGURE:
    case TH_TAG_FIGCAPTION:
    case TH_TAG_ADDRESS:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_PRE:
    case TH_TAG_HR:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_UL:
    case TH_TAG_OL:
    case TH_TAG_LI:
    case TH_TAG_DL:
    case TH_TAG_DT:
    case TH_TAG_DD:
    case TH_TAG_MENU:
    case TH_TAG_DETAILS:
    case TH_TAG_SUMMARY:
    case TH_TAG_TABLE:
        return 1;
    default:
        return 0;
    }
}

/* Tags whose entire subtree contributes nothing to a text/markdown rendering:
   document metadata and scripts. */
static int is_md_skipped(uint16_t atom) {
    return atom == TH_TAG_HEAD || atom == TH_TAG_SCRIPT || atom == TH_TAG_STYLE;
}

/* ----------------------------------------------------------------- options */

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
    opt.quote_open = "\"";
    opt.quote_close = "\"";
    opt.escape_mode = TH_MD_ESCAPE_MINIMAL;
    opt.escape_asterisks = 1;
    opt.escape_underscores = 1;
    opt.line_break = TH_MD_BREAK_SPACES;
    opt.document_strip = TH_MD_DOC_STRIP;
    opt.sub = "";
    opt.sup = "";
    opt.google_doc = 0;
    opt.google_list_indent = 36;
    opt.hide_strikethrough = 0;
    return opt;
}

/* -------------------------------------------------------------- the cursor */

/* A reference-style link or image collected during the walk, flushed as a
   numbered "[n]: url" definition at the end. */
typedef struct {
    const Py_UCS4 *url;
    Py_ssize_t url_len;
    const Py_UCS4 *title;
    Py_ssize_t title_len;
} md_reference;

/* An emphasis/strikethrough marker whose opening run is deferred until the first
   visible character of its content, so a leading inner space lands outside the
   marker (`a<b> x</b>` -> `a **x**`, never the invalid `a** x**`). The frames
   chain through the C call stack, outermost reachable via prev. */
typedef struct md_pending {
    const char *marker;
    struct md_pending *prev;
    int emitted;
} md_pending;

typedef struct {
    sbuf out;
    th_tree *tree;
    const md_opts *opt;
    sbuf prefix;         /* what every continuation line starts with: list indent + "> " quotes */
    md_pending *pending; /* innermost inline marker whose open run is still deferred */
    md_reference *refs;  /* collected reference-link targets */
    Py_ssize_t ref_count;
    Py_ssize_t ref_cap;
    int started;          /* has any block content been emitted yet */
    int line_has_content; /* real content past the prefix/marker on the current line */
    int column;           /* visible width on the current line, for word wrapping */
    int space_pending;    /* a collapsed-away whitespace run is owed one space */
    int drop_space;       /* swallow the next pending space (block/inline start) without emitting */
    int pending_loose;    /* the previous block wants a blank line after it */
    int suppress_break;   /* the next block attaches to the current (list marker) line */
    int tight;            /* inside a list item: inline runs do not add blank lines */
    int list_depth;       /* nesting depth of the current list, for bullet cycling */
    int g_bold;           /* google_doc: a CSS font-weight bold is in force from an ancestor */
    int g_italic;         /* google_doc: a CSS font-style italic is in force from an ancestor */
    int failed;           /* a reference buffer allocation failed */
} md_ctx;

/* Emit a configured option string, which may hold non-ASCII (a typographic
   quote, a unicode bullet), decoding its UTF-8 to code points. */
static void md_puts8(sbuf *out, const char *s) {
    sbuf_put_utf8(out, s, (Py_ssize_t)strlen(s));
}

static int md_is_ws(Py_UCS4 c) {
    /* the tokenizer has already normalized CR to LF, so a tree text node never
       holds a carriage return; classifying it here would be a dead branch */
    return c == ' ' || c == '\t' || c == '\n' || c == '\f';
}

/* Append n spaces to the continuation prefix and return the start offset, so the
   caller can pop them back off after the nested block. */
static Py_ssize_t md_push_spaces(md_ctx *ctx, Py_ssize_t n) {
    Py_ssize_t base = ctx->prefix.len;
    for (Py_ssize_t i = 0; i < n; i++) {
        sbuf_putc(&ctx->prefix, ' ');
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

/* Emit the one space a collapsed whitespace run owes, unless it falls at a line
   start or has been marked for dropping (the start of a block). */
static void md_flush_space(md_ctx *ctx) {
    int drop = ctx->drop_space;
    ctx->drop_space = 0;
    if (!ctx->space_pending) {
        return;
    }
    ctx->space_pending = 0;
    /* every line start sets drop_space, so a line-start space is already covered
       by drop and no separate line_has_content test is needed here */
    if (drop) {
        return;
    }
    sbuf_putc(&ctx->out, ' ');
}

/* Emit the deferred opening markers from outermost to innermost (skipping any
   already written), so nested emphasis opens in source order. */
static void md_emit_pending(md_ctx *ctx, md_pending *frame) {
    if (frame == NULL || frame->emitted) {
        return;
    }
    md_emit_pending(ctx, frame->prev);
    md_puts8(&ctx->out, frame->marker);
    frame->emitted = 1;
    ctx->line_has_content = 1;
}

/* Called just before any visible character: settle the owed space first (so it
   stays outside an emphasis run), then open any markers that were waiting for
   real content. */
static void md_before_visible(md_ctx *ctx) {
    md_flush_space(ctx);
    md_emit_pending(ctx, ctx->pending);
}

/* ----------------------------------------------------------- inline output */

/* Characters that begin a markdown construct and so are backslash-escaped in
   running text. Leading line markers (#, >, -, +) are handled separately, only
   at the very start of a line. */
static int md_needs_escape(const md_opts *opt, Py_UCS4 c) {
    switch (c) {
    case '\\':
    case '`':
    case '[':
    case ']':
        return 1;
    case '*':
        return opt->escape_asterisks;
    case '_':
        return opt->escape_underscores;
    default:
        break;
    }
    /* the "all" mode escapes every other character a markdown reader could act on */
    return opt->escape_mode == TH_MD_ESCAPE_ALL && (c == '<' || c == '>' || c == '#' || c == '+' || c == '-' ||
                                                    c == '=' || c == '~' || c == '|' || c == '!' || c == '&');
}

static void md_put_char(md_ctx *ctx, Py_UCS4 c) {
    int line_start_marker = !ctx->line_has_content && (c == '#' || c == '>' || c == '-' || c == '+');
    if (md_needs_escape(ctx->opt, c) || line_start_marker) {
        sbuf_putc(&ctx->out, '\\');
    }
    sbuf_putc(&ctx->out, c);
    ctx->line_has_content = 1;
}

/* At a line start, "12. " or "3) " would be read as an ordered-list item, so a
   leading run of digits before a dot or paren is escaped. Returns how many code
   points were consumed (0 when the run is not list-like). */
static Py_ssize_t md_escape_line_number(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t i, Py_ssize_t len) {
    /* the caller only enters here on a digit, so the run is at least one long */
    Py_ssize_t j = i;
    while (j < len && text[j] >= '0' && text[j] <= '9') {
        j++;
    }
    if (j >= len || (text[j] != '.' && text[j] != ')')) {
        return 0;
    }
    sbuf_put_run(&ctx->out, &text[i], j - i);
    sbuf_putc(&ctx->out, '\\');
    sbuf_putc(&ctx->out, text[j]);
    ctx->line_has_content = 1;
    return j + 1 - i;
}

/* Emit inline text with normal-flow whitespace collapsing and markdown escaping.
   Prose is mostly plain runs, so after the first character of a word is placed
   (which resolves the deferred space, markers and escapes) the rest of the run —
   no whitespace, nothing to escape — is bulk-copied in one memcpy. */
static void md_emit_text(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t i = 0;
    while (i < len) {
        Py_UCS4 c = text[i];
        if (md_is_ws(c)) {
            ctx->space_pending = 1;
            i++;
            continue;
        }
        md_before_visible(ctx);
        if (!ctx->line_has_content && c >= '0' && c <= '9') {
            Py_ssize_t consumed = md_escape_line_number(ctx, text, i, len);
            if (consumed > 0) {
                i += consumed;
                continue;
            }
        }
        md_put_char(ctx, c);
        i++;
        Py_ssize_t start = i;
        while (i < len && !md_is_ws(text[i]) && !md_needs_escape(ctx->opt, text[i])) {
            i++;
        }
        if (i > start) {
            sbuf_put_run(&ctx->out, &text[start], i - start);
        }
    }
}

/* Write decimal digits of a non-negative number and return how many. */
static Py_ssize_t md_put_decimal(sbuf *out, Py_ssize_t n) {
    Py_UCS4 digits[20];
    Py_ssize_t count = 0;
    do {
        digits[count++] = (Py_UCS4)('0' + (int)(n % 10));
        n /= 10;
    } while (n > 0);
    for (Py_ssize_t i = count - 1; i >= 0; i--) {
        sbuf_putc(out, digits[i]);
    }
    return count;
}

/* The value of one attribute by interned name, or NULL with *len 0 when the
   attribute is absent or valueless. */
static const Py_UCS4 *md_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t *len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, (Py_ssize_t)strlen(name));
    if (index < 0 || node->attrs[index].value == NULL) {
        *len = 0;
        return NULL;
    }
    *len = node->attrs[index].value_len;
    return node->attrs[index].value;
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
        while (name_start < name_end && md_is_ws(style[name_start])) {
            name_start++;
        }
        while (name_end > name_start && md_is_ws(style[name_end - 1])) {
            name_end--;
        }
        while (value_start < value_end && md_is_ws(style[value_start])) {
            value_start++;
        }
        while (value_end > value_start && md_is_ws(style[value_end - 1])) {
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
        pixels = pixels * 10 + (int)(value[index] - '0');
    }
    return pixels;
}

static void md_render_inline(md_ctx *ctx, th_node *node);

static void md_inline_children(md_ctx *ctx, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        md_render_inline(ctx, child);
    }
}

/* Wrap an inline element's content in a marker (** , * , ~~). The open run is
   deferred (md_before_visible writes it at the first visible character) so a
   leading inner space moves outside; the close run is written only if the open
   one was, so an empty <b></b> leaves nothing behind. The owed space is left
   pending across the close, so a trailing inner space also lands outside. */
static void md_wrap(md_ctx *ctx, th_node *node, const char *marker) {
    md_pending frame = {marker, ctx->pending, 0};
    ctx->pending = &frame;
    md_inline_children(ctx, node);
    ctx->pending = frame.prev;
    if (frame.emitted) {
        md_puts8(&ctx->out, marker);
    }
}

/* The longest run of backticks anywhere in s, so an inline code span can fence
   with one more backtick than that and never be split by its own content. */
static Py_ssize_t md_max_backtick_run(const Py_UCS4 *s, Py_ssize_t len) {
    Py_ssize_t best = 0;
    Py_ssize_t run = 0;
    for (Py_ssize_t i = 0; i < len; i++) {
        if (s[i] == '`') {
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

static void md_emit_code_span(md_ctx *ctx, th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *content = th_node_text(ctx->tree, node, &len);
    if (content == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    md_before_visible(ctx);
    Py_ssize_t fence = md_max_backtick_run(content, len) + 1;
    int pad = len > 0 && (content[0] == '`' || content[len - 1] == '`');
    for (Py_ssize_t i = 0; i < fence; i++) {
        sbuf_putc(&ctx->out, '`');
    }
    if (pad) {
        sbuf_putc(&ctx->out, ' ');
    }
    sbuf_put_run(&ctx->out, content, len);
    if (pad) {
        sbuf_putc(&ctx->out, ' ');
    }
    for (Py_ssize_t i = 0; i < fence; i++) {
        sbuf_putc(&ctx->out, '`');
    }
    ctx->line_has_content = 1;
    PyMem_Free(content);
}

/* Write a URL/destination verbatim (no markdown escaping); a space inside it is
   wrapped in angle brackets so the destination stays a single token. */
static void md_emit_url(md_ctx *ctx, const Py_UCS4 *url, Py_ssize_t len) {
    int has_space = 0;
    for (Py_ssize_t i = 0; i < len; i++) {
        if (url[i] == ' ') {
            has_space = 1;
        }
    }
    if (has_space) {
        sbuf_putc(&ctx->out, '<');
        sbuf_put_run(&ctx->out, url, len);
        sbuf_putc(&ctx->out, '>');
    } else {
        sbuf_put_run(&ctx->out, url, len);
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
    for (Py_ssize_t i = 0; i < len; i++) {
        if (href[i] == ':') {
            return 1;
        }
        if (href[i] == '/' || href[i] == '#' || href[i] == '?') {
            return 0;
        }
    }
    return 0;
}

/* Record a reference-style target, returning its 1-based number. On allocation
   failure it sets ctx->failed and returns 0; the caller then renders inline. */
static Py_ssize_t md_add_reference(md_ctx *ctx, const Py_UCS4 *url, Py_ssize_t url_len, const Py_UCS4 *title,
                                   Py_ssize_t title_len) {
    if (ctx->ref_count == ctx->ref_cap) {
        Py_ssize_t cap = ctx->ref_cap ? ctx->ref_cap * 2 : 8;
        md_reference *grown = PyMem_Realloc(ctx->refs, (size_t)cap * sizeof(md_reference));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return 0;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ctx->refs = grown;
        ctx->ref_cap = cap;
    }
    ctx->refs[ctx->ref_count].url = url;
    ctx->refs[ctx->ref_count].url_len = url_len;
    ctx->refs[ctx->ref_count].title = title;
    ctx->refs[ctx->ref_count].title_len = title_len;
    return ++ctx->ref_count;
}

static void md_emit_link(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    Py_ssize_t href_len;
    const Py_UCS4 *href = md_attr(ctx->tree, node, "href", &href_len);
    if (href == NULL || opt->ignore_links || (opt->skip_internal_links && md_href_internal(href))) {
        md_inline_children(ctx, node);
        return;
    }
    Py_ssize_t title_len;
    const Py_UCS4 *title = md_attr(ctx->tree, node, "title", &title_len);
    int relative = *opt->base_url != '\0' && !md_href_absolute(href, href_len) && !md_href_internal(href);
    if (opt->autolink && title == NULL && !relative && md_href_absolute(href, href_len)) {
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
            return;
        }
    }
    md_before_visible(ctx);
    sbuf_putc(&ctx->out, '[');
    ctx->line_has_content = 1;
    ctx->drop_space = 1;
    md_inline_children(ctx, node);
    if (title == NULL && opt->link_title) {
        title = href;
        title_len = href_len;
    }
    if (opt->link_style == TH_MD_LINK_REFERENCE) {
        Py_ssize_t number = md_add_reference(ctx, href, href_len, title, title_len);
        if (ctx->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        sbuf_puts(&ctx->out, "][");
        md_put_decimal(&ctx->out, number);
        sbuf_putc(&ctx->out, ']');
        return;
    }
    sbuf_puts(&ctx->out, "](");
    if (relative) {
        md_puts8(&ctx->out, opt->base_url);
    }
    md_emit_url(ctx, href, href_len);
    if (title != NULL) {
        sbuf_puts(&ctx->out, " \"");
        sbuf_put_run(&ctx->out, title, title_len);
        sbuf_putc(&ctx->out, '"');
    }
    sbuf_putc(&ctx->out, ')');
}

/* Write an image's alt text, falling back to the configured default. */
static void md_emit_alt(md_ctx *ctx, const Py_UCS4 *alt, Py_ssize_t alt_len) {
    if (alt != NULL) {
        sbuf_put_run(&ctx->out, alt, alt_len);
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
    Py_ssize_t alt_len;
    const Py_UCS4 *alt = md_attr(ctx->tree, node, "alt", &alt_len);
    md_before_visible(ctx);
    if (opt->image_mode == TH_MD_IMAGE_ALT) {
        md_emit_alt(ctx, alt, alt_len);
        return;
    }
    Py_ssize_t src_len;
    const Py_UCS4 *src = md_attr(ctx->tree, node, "src", &src_len);
    sbuf_puts(&ctx->out, "![");
    md_emit_alt(ctx, alt, alt_len);
    sbuf_puts(&ctx->out, "](");
    if (src != NULL) {
        if (*opt->base_url != '\0' && !md_href_absolute(src, src_len) && !md_href_internal(src)) {
            md_puts8(&ctx->out, opt->base_url);
        }
        md_emit_url(ctx, src, src_len);
    }
    sbuf_putc(&ctx->out, ')');
    ctx->line_has_content = 1;
}

static void md_render_block(md_ctx *ctx, th_node *node);
static void md_block_children(md_ctx *ctx, th_node *node);

/* Render an element (or content node) by its tag, the common path shared by the
   plain walk and the google_doc CSS wrapper. Text is handled by the caller. */
static void md_render_inline_tag(md_ctx *ctx, th_node *node) {
    const md_opts *opt = ctx->opt;
    uint16_t atom = node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN;
    switch (atom) {
    case TH_TAG_STRONG:
    case TH_TAG_B:
        md_wrap(ctx, node, opt->keep_emphasis ? opt->strong : "");
        return;
    case TH_TAG_EM:
    case TH_TAG_I:
        md_wrap(ctx, node, opt->keep_emphasis ? opt->emphasis : "");
        return;
    case TH_TAG_DEL:
    case TH_TAG_S:
    case TH_TAG_STRIKE:
        if (!opt->keep_strikethrough) {
            return; /* hide struck-through content entirely */
        }
        md_wrap(ctx, node, opt->keep_emphasis ? opt->strikethrough : "");
        return;
    case TH_TAG_SUB:
        md_wrap(ctx, node, opt->sub);
        return;
    case TH_TAG_SUP:
        md_wrap(ctx, node, opt->sup);
        return;
    case TH_TAG_Q:
        md_before_visible(ctx);
        md_puts8(&ctx->out, opt->quote_open);
        md_inline_children(ctx, node);
        md_puts8(&ctx->out, opt->quote_close);
        return;
    case TH_TAG_CODE:
        md_emit_code_span(ctx, node);
        return;
    case TH_TAG_A:
        md_emit_link(ctx, node);
        return;
    case TH_TAG_IMG:
        md_emit_image(ctx, node);
        return;
    case TH_TAG_BR:
        sbuf_puts(&ctx->out, opt->line_break == TH_MD_BREAK_BACKSLASH ? "\\" : "  ");
        md_newline(ctx);
        return;
    case TH_TAG_WBR:
        return;
    default:
        break;
    }
    if (is_md_skipped(atom)) {
        return;
    }
    if (is_md_block(atom)) {
        md_render_block(ctx, node);
        return;
    }
    md_inline_children(ctx, node);
}

/* Render an element in google_doc mode: turn the inline-CSS styling a Google Docs
   export carries into Markdown. A font-weight/font-style/fixed-width font opens
   the matching marker (only on the transition the ancestor did not already set,
   so nested spans do not double the markup), and a line-through drops the text
   when hide_strikethrough is on. The markers are deferred like md_wrap so a
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
    md_pending italic_frame, bold_frame;
    if (italic && !outer_italic) {
        italic_frame = (md_pending){opt->emphasis, ctx->pending, 0};
        ctx->pending = &italic_frame;
    }
    if (bold && !outer_bold) {
        bold_frame = (md_pending){opt->strong, ctx->pending, 0};
        ctx->pending = &bold_frame;
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
    ctx->g_bold = outer_bold;
    ctx->g_italic = outer_italic;
    if (bold && !outer_bold) {
        ctx->pending = bold_frame.prev;
        if (bold_frame.emitted) {
            md_puts8(&ctx->out, opt->strong);
        }
    }
    if (italic && !outer_italic) {
        ctx->pending = italic_frame.prev;
        if (italic_frame.emitted) {
            md_puts8(&ctx->out, opt->emphasis);
        }
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
    if (ctx->opt->google_doc) {
        /* a content node carries no style, so it passes through unstyled */
        md_render_google(ctx, node);
        return;
    }
    md_render_inline_tag(ctx, node);
}

/* ------------------------------------------------------------ block output */

/* Whether the element's first meaningful child is inline content rather than a
   nested block, deciding if a list item's text rides on the bullet line. */
static int md_leads_with_inline(md_ctx *ctx, th_node *node) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            const Py_UCS4 *text = need_text(ctx->tree, child);
            for (Py_ssize_t i = 0; i < child->text_len; i++) {
                if (!md_is_ws(text[i])) {
                    return 1; /* leading visible text rides on the bullet line */
                }
            }
            continue; /* whitespace-only: keep looking past it */
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

/* Lay out the children of a block container: consecutive inline children form
   one paragraph-like run, and each block child recurses. */
static void md_block_children(md_ctx *ctx, th_node *node) {
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
            md_block_children(ctx, child);
            continue;
        } else if (child->type != TH_NODE_TEXT) {
            continue;
        }
        if (block) {
            in_run = 0;
            md_render_block(ctx, child);
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
            md_block_line(ctx, ctx->tight ? 0 : 1);
            in_run = 1;
        }
        md_render_inline(ctx, child);
    }
}

static void md_render_list(md_ctx *ctx, th_node *node, int ordered) {
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
    Py_ssize_t number = 1;
    if (ordered) {
        Py_ssize_t start_len;
        const Py_UCS4 *start = md_attr(ctx->tree, node, "start", &start_len);
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
    Py_ssize_t bullets_len = (Py_ssize_t)strlen(ctx->opt->bullets);
    char bullet = ctx->opt->bullets[ctx->list_depth % bullets_len];
    ctx->list_depth++;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT || child->ns != TH_NS_HTML || child->atom != TH_TAG_LI) {
            continue;
        }
        md_block_line(ctx, 0);
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
                for (int level = 0; level < nest; level++) {
                    sbuf_puts(&ctx->out, "  ");
                }
                lead = (Py_ssize_t)nest * 2;
            }
        }
        Py_ssize_t width;
        if (ordered) {
            width = lead + md_put_decimal(&ctx->out, number) + 2;
            sbuf_puts(&ctx->out, ". ");
            number++;
        } else {
            sbuf_putc(&ctx->out, (Py_UCS4)(unsigned char)bullet);
            sbuf_putc(&ctx->out, ' ');
            width = lead + 2;
        }
        ctx->line_has_content = 1;
        Py_ssize_t base = md_push_spaces(ctx, width);
        int saved_tight = ctx->tight;
        ctx->tight = 1;
        ctx->suppress_break = md_leads_with_inline(ctx, child);
        md_block_children(ctx, child);
        ctx->tight = saved_tight;
        ctx->prefix.len = base;
    }
    ctx->list_depth--;
}

/* Render a cell's inline content into dst, collapsing internal whitespace to
   single spaces and escaping pipes so the cell stays on one row. */
static void md_cell_text(md_ctx *ctx, th_node *node, sbuf *dst) {
    sbuf saved_out = ctx->out;
    sbuf saved_prefix = ctx->prefix;
    int saved_started = ctx->started;
    int saved_line = ctx->line_has_content;
    int saved_space = ctx->space_pending;
    int saved_drop = ctx->drop_space;
    md_pending *saved_pending = ctx->pending;
    ctx->out = (sbuf){NULL, 0, 0, 0};
    ctx->prefix = (sbuf){NULL, 0, 0, 0};
    ctx->pending = NULL;
    ctx->line_has_content = 1;
    ctx->space_pending = 0;
    ctx->drop_space = 1;
    md_inline_children(ctx, node);
    sbuf rendered = ctx->out;
    PyMem_Free(ctx->prefix.data);
    ctx->out = saved_out;
    ctx->prefix = saved_prefix;
    ctx->pending = saved_pending;
    ctx->started = saved_started;
    ctx->line_has_content = saved_line;
    ctx->space_pending = saved_space;
    ctx->drop_space = saved_drop;
    int space_run = 0;
    for (Py_ssize_t i = 0; i < rendered.len; i++) {
        Py_UCS4 c = rendered.data[i];
        if (c == '\n' || c == ' ') {
            space_run = 1;
            continue;
        }
        if (space_run) {
            sbuf_putc(dst, ' ');
            space_run = 0;
        }
        if (c == '|') {
            sbuf_puts(dst, "\\|");
        } else {
            sbuf_putc(dst, c);
        }
    }
    PyMem_Free(rendered.data);
}

/* A row's element children come only from HTML table parsing (foreign content is
   foster-parented out of the table), so the td/th atoms already imply the HTML
   namespace and no separate ns test is needed in these loops. */
static Py_ssize_t md_row_cells(th_node *row) {
    Py_ssize_t count = 0;
    for (th_node *cell = row->first_child; cell != NULL; cell = cell->next_sibling) {
        if (cell->type == TH_NODE_ELEMENT && (cell->atom == TH_TAG_TD || cell->atom == TH_TAG_TH)) {
            count++;
        }
    }
    return count;
}

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

static void md_emit_row(md_ctx *ctx, th_node *row, Py_ssize_t columns) {
    sbuf_puts(&ctx->out, "| ");
    Py_ssize_t emitted = 0;
    for (th_node *cell = row->first_child; cell != NULL; cell = cell->next_sibling) {
        if (cell->type != TH_NODE_ELEMENT || (cell->atom != TH_TAG_TD && cell->atom != TH_TAG_TH)) {
            continue;
        }
        md_cell_text(ctx, cell, &ctx->out);
        sbuf_puts(&ctx->out, " | ");
        emitted++;
    }
    for (; emitted < columns; emitted++) {
        sbuf_puts(&ctx->out, " | ");
    }
    ctx->line_has_content = 1;
}

/* Collect the table's rows in document order across an optional thead/tbody/tfoot
   wrapper, append each into rows, and return the widest row's column count. */
static Py_ssize_t md_collect_rows(th_node *node, th_node **rows, Py_ssize_t *count) {
    Py_ssize_t columns = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (child->atom == TH_TAG_TR) {
            /* cap counts every child and grandchild, so it bounds the rows by
               construction and indexing it needs no run-time guard */
            rows[*count] = child;
            (*count)++;
            Py_ssize_t cells = md_row_cells(child);
            if (cells > columns) {
                columns = cells;
            }
        } else if (child->atom == TH_TAG_THEAD || child->atom == TH_TAG_TBODY || child->atom == TH_TAG_TFOOT) {
            Py_ssize_t nested = md_collect_rows(child, rows, count);
            if (nested > columns) {
                columns = nested;
            }
        }
    }
    return columns;
}

/* Emit one padded grid row: each cell's text then spaces out to the column
   width, wrapped in pipes. A NULL grid emits an all-spaces (empty header) row. */
static void md_emit_padded_row(md_ctx *ctx, sbuf *grid, Py_ssize_t row, Py_ssize_t columns, const Py_ssize_t *widths) {
    sbuf_puts(&ctx->out, "| ");
    for (Py_ssize_t c = 0; c < columns; c++) {
        Py_ssize_t len = 0;
        if (grid != NULL) {
            sbuf *cell = &grid[row * columns + c];
            sbuf_put_run(&ctx->out, cell->data, cell->len);
            len = cell->len;
        }
        for (Py_ssize_t pad = len; pad < widths[c]; pad++) {
            sbuf_putc(&ctx->out, ' ');
        }
        sbuf_puts(&ctx->out, " | ");
    }
    ctx->line_has_content = 1;
}

/* Render the table as an aligned pipe grid: render every cell to a string, take
   each column's widest cell, then pad to that width (html2text's pad_tables). */
static void md_render_table_padded(md_ctx *ctx, th_node **rows, Py_ssize_t count, Py_ssize_t columns, int has_header) {
    sbuf *grid = PyMem_Calloc((size_t)(count * columns), sizeof(sbuf));
    Py_ssize_t *widths = PyMem_Calloc((size_t)columns, sizeof(Py_ssize_t));
    if (grid == NULL || widths == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(grid);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(widths);               /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->out.failed = 1;              /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t r = 0; r < count; r++) {
        Py_ssize_t col = 0;
        for (th_node *cell = rows[r]->first_child; cell != NULL; cell = cell->next_sibling) {
            if (cell->type != TH_NODE_ELEMENT || (cell->atom != TH_TAG_TD && cell->atom != TH_TAG_TH)) {
                continue;
            }
            /* columns is the widest row's cell count, so col never reaches it */
            md_cell_text(ctx, cell, &grid[r * columns + col]);
            if (grid[r * columns + col].len > widths[col]) {
                widths[col] = grid[r * columns + col].len;
            }
            col++;
        }
    }
    for (Py_ssize_t c = 0; c < columns; c++) {
        if (widths[c] < 3) {
            widths[c] = 3; /* the "---" separator needs at least three dashes */
        }
    }
    Py_ssize_t body_start = has_header ? 1 : 0;
    md_emit_padded_row(ctx, has_header ? grid : NULL, 0, columns, widths);
    md_newline(ctx);
    sbuf_puts(&ctx->out, "| ");
    for (Py_ssize_t c = 0; c < columns; c++) {
        for (Py_ssize_t d = 0; d < widths[c]; d++) {
            sbuf_putc(&ctx->out, '-');
        }
        sbuf_puts(&ctx->out, " | ");
    }
    ctx->line_has_content = 1;
    for (Py_ssize_t r = body_start; r < count; r++) {
        md_newline(ctx);
        md_emit_padded_row(ctx, grid, r, columns, widths);
    }
    for (Py_ssize_t i = 0; i < count * columns; i++) {
        PyMem_Free(grid[i].data);
    }
    PyMem_Free(grid);
    PyMem_Free(widths);
}

static void md_render_table(md_ctx *ctx, th_node *node) {
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
    Py_ssize_t count = 0;
    Py_ssize_t columns = md_collect_rows(node, rows, &count);
    if (count == 0 || columns == 0) {
        PyMem_Free(rows);
        return;
    }
    const md_opts *opt = ctx->opt;
    if (opt->table_mode == TH_MD_TABLE_STRIP) {
        /* drop the grid, keep each cell's text as a space-joined block per row */
        for (Py_ssize_t r = 0; r < count; r++) {
            md_block_line(ctx, 1);
            int first = 1;
            for (th_node *cell = rows[r]->first_child; cell != NULL; cell = cell->next_sibling) {
                if (cell->type != TH_NODE_ELEMENT || (cell->atom != TH_TAG_TD && cell->atom != TH_TAG_TH)) {
                    continue;
                }
                if (!first) {
                    sbuf_putc(&ctx->out, ' ');
                }
                first = 0;
                md_inline_children(ctx, cell);
            }
        }
        PyMem_Free(rows);
        return;
    }
    /* the header is the first row when it reads as one (or always, when inferred);
       header="none" keeps every row in the body under an empty header */
    int has_header = opt->table_header == TH_MD_HEADER_FIRST ||
                     (opt->table_header == TH_MD_HEADER_DETECT && md_row_is_header(rows[0]));
    md_block_line(ctx, 1);
    if (opt->pad_tables) {
        md_render_table_padded(ctx, rows, count, columns, has_header);
    } else {
        Py_ssize_t body_start = has_header ? 1 : 0;
        if (has_header) {
            md_emit_row(ctx, rows[0], columns);
        } else {
            sbuf_puts(&ctx->out, "| ");
            for (Py_ssize_t c = 0; c < columns; c++) {
                sbuf_puts(&ctx->out, " | ");
            }
            ctx->line_has_content = 1;
        }
        md_newline(ctx);
        sbuf_puts(&ctx->out, "| ");
        for (Py_ssize_t c = 0; c < columns; c++) {
            sbuf_puts(&ctx->out, "--- | ");
        }
        ctx->line_has_content = 1;
        for (Py_ssize_t r = body_start; r < count; r++) {
            md_newline(ctx);
            md_emit_row(ctx, rows[r], columns);
        }
    }
    PyMem_Free(rows);
}

/* Emit preformatted text verbatim, restarting the line prefix at each newline so
   a fence indent or blockquote marker carries down every line. */
static void md_emit_pre_text(md_ctx *ctx, const Py_UCS4 *text, Py_ssize_t end) {
    for (Py_ssize_t i = 0; i < end; i++) {
        if (text[i] == '\n') {
            md_newline(ctx);
        } else {
            sbuf_putc(&ctx->out, text[i]);
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
                for (Py_ssize_t i = 0; i < want_len; i++) {
                    if (cls[i] != (Py_UCS4)(unsigned char)want[i]) {
                        match = 0;
                        break;
                    }
                }
                if (match) {
                    lang = cls + want_len;
                    lang_len = cls_len - want_len;
                    for (Py_ssize_t i = 0; i < lang_len; i++) {
                        if (md_is_ws(lang[i])) {
                            lang_len = i;
                            break;
                        }
                    }
                }
            }
        }
    }
    Py_ssize_t text_len;
    Py_UCS4 *text = th_node_text(ctx->tree, content, &text_len);
    if (text == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->out.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;              /* GCOVR_EXCL_LINE: allocation-failure path */
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
        for (Py_ssize_t i = 0; i < fence; i++) {
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
        for (Py_ssize_t i = 0; i < fence; i++) {
            sbuf_putc(&ctx->out, '`');
        }
        ctx->line_has_content = 1;
    }
    PyMem_Free(text);
}

static void md_render_block(md_ctx *ctx, th_node *node) {
    /* only an HTML element is ever classified as a block, so the namespace check
       the callers already made guarantees node is HTML here */
    uint16_t atom = node->atom;
    switch (atom) {
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6: {
        md_block_line(ctx, 1);
        int level = atom - TH_TAG_H1 + 1;
        if (ctx->opt->heading_style == TH_MD_HEADING_SETEXT && level <= 2) {
            /* setext underlines the heading text, so render it then lay a run of
               '=' (h1) or '-' (h2) as wide as the text under it */
            Py_ssize_t mark = ctx->out.len;
            ctx->line_has_content = 1;
            ctx->drop_space = 1;
            md_inline_children(ctx, node);
            Py_ssize_t width = ctx->out.len - mark;
            md_newline(ctx);
            Py_UCS4 rule = level == 1 ? '=' : '-';
            for (Py_ssize_t i = 0; i < width; i++) {
                sbuf_putc(&ctx->out, rule);
            }
            ctx->line_has_content = 1;
            return;
        }
        for (int i = 0; i < level; i++) {
            sbuf_putc(&ctx->out, '#');
        }
        sbuf_putc(&ctx->out, ' ');
        ctx->line_has_content = 1;
        ctx->drop_space = 1;
        md_inline_children(ctx, node);
        if (ctx->opt->heading_style == TH_MD_HEADING_ATX_CLOSED) {
            sbuf_putc(&ctx->out, ' ');
            for (int i = 0; i < level; i++) {
                sbuf_putc(&ctx->out, '#');
            }
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
        md_render_list(ctx, node, 0);
        return;
    case TH_TAG_OL:
        md_render_list(ctx, node, 1);
        return;
    case TH_TAG_PRE:
        md_render_pre(ctx, node);
        return;
    case TH_TAG_TABLE:
        md_render_table(ctx, node);
        return;
    case TH_TAG_BLOCKQUOTE: {
        Py_ssize_t base = ctx->prefix.len;
        if (ctx->started) {
            /* close the previous block and open the separator with the OUTER
               prefix, so the blank line is not itself quoted, before adding the
               "> " that every line inside the quote carries */
            sbuf_putc(&ctx->out, '\n');
            if (!ctx->tight) {
                md_write_blank_prefix(ctx);
                sbuf_putc(&ctx->out, '\n');
            }
            sbuf_puts(&ctx->prefix, "> ");
            md_write_prefix(ctx);
            ctx->line_has_content = 0;
            ctx->space_pending = 0;
            ctx->drop_space = 1;
            ctx->pending_loose = 1;
            ctx->suppress_break = 1;
        } else {
            sbuf_puts(&ctx->prefix, "> ");
        }
        int saved_tight = ctx->tight;
        ctx->tight = 0;
        md_block_children(ctx, node);
        ctx->tight = saved_tight;
        ctx->prefix.len = base;
        return;
    }
    default:
        md_block_children(ctx, node);
        return;
    }
}

/* Trim whitespace off the finished buffer in place per the document_strip mode:
   both ends, the left only, the right only, or neither. */
static Py_ssize_t md_trim(sbuf *out, int mode) {
    Py_ssize_t start = 0;
    if (mode == TH_MD_DOC_STRIP || mode == TH_MD_DOC_LSTRIP) {
        /* strip leading blank lines only; block content never starts with a space
           except an indented code block, whose indent must survive */
        while (start < out->len && out->data[start] == '\n') {
            start++;
        }
    }
    Py_ssize_t end = out->len;
    if (mode == TH_MD_DOC_STRIP || mode == TH_MD_DOC_RSTRIP) {
        while (end > start && md_is_ws(out->data[end - 1])) {
            end--;
        }
    }
    if (start > 0) {
        memmove(out->data, out->data + start, (size_t)(end - start) * sizeof(Py_UCS4));
    }
    out->len = end - start;
    return start; /* how many leading code points were removed, to shift annotations */
}

/* Append the collected reference definitions ("[n]: url \"title\"") after the
   body, one per line, when link_style is reference. */
static void md_flush_references(md_ctx *ctx) {
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
        sbuf_puts(&ctx->out, "]: ");
        sbuf_put_run(&ctx->out, ctx->refs[i].url, ctx->refs[i].url_len);
        if (ctx->refs[i].title != NULL) {
            sbuf_puts(&ctx->out, " \"");
            sbuf_put_run(&ctx->out, ctx->refs[i].title, ctx->refs[i].title_len);
            sbuf_putc(&ctx->out, '"');
        }
    }
}

Py_UCS4 *th_node_markdown(th_tree *tree, th_node *node, const md_opts *opt, Py_ssize_t *out_len) {
    md_ctx ctx = {0};
    ctx.tree = tree;
    ctx.opt = opt;
    sbuf_presize_for_root(&ctx.out, tree, node);
    if (node->type == TH_NODE_TEXT) {
        ctx.started = 1;
        ctx.line_has_content = 1;
        md_emit_text(&ctx, need_text(tree, node), node->text_len);
    } else if (is_md_block(node->ns == TH_NS_HTML ? node->atom : TH_TAG_UNKNOWN)) {
        md_render_block(&ctx, node);
    } else {
        md_block_children(&ctx, node);
    }
    md_flush_references(&ctx);
    PyMem_Free(ctx.prefix.data);
    PyMem_Free(ctx.refs);
    md_trim(&ctx.out, opt->document_strip);
    return sbuf_finish(&ctx.out, out_len);
}
