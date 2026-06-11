/* Kind-stamped tokenizer core. This file is #included three times by
   tokenizer_sm.c — once per PyUnicode storage width — with TH_NAME, TH_CHAR
   and TH_KIND defined, the same way CPython's stringlib specializes per kind.
   Every input read compiles to direct indexing, so the per-character paths
   and the run scanners carry no width dispatch. gcov attributes all three
   instantiations to these lines, so coverage aggregates across kinds. */

#define TH_READ(i) ((Py_UCS4)((const TH_CHAR *)self->input.data)[(i)])

/* Resolve a character reference starting at the '&' under self->pos, appending
   the decoded code points to dest. in_attr selects the legacy attribute rule
   (a named reference without a trailing ';' is left literal when followed by
   '=' or an ASCII alphanumeric). Returns the number of input code points to
   consume (always >= 1) or -1 to suspend for more input; allocation failures
   land on the sticky oom flag. The numeric rules follow the tokenizer spec, which emits control and
   noncharacter code points rather than dropping them the way unescape() does. */
static Py_ssize_t TH_NAME(consume_charref)(th_tokenizer *self, th_buf *dest, int in_attr) {
    Py_ssize_t len = self->input.len;
    Py_ssize_t amp = self->pos;
    Py_ssize_t p = amp + 1;

    if (p >= len) {
        if (!self->eof) {
            return -1;
        }
        push(self, dest, '&');
        return 1;
    }

    Py_UCS4 first = TH_READ(p);
    if (first == '#') {
        Py_ssize_t cursor = p + 1;
        int hex = 0;
        if (cursor >= len && !self->eof) {
            return -1;
        }
        if (cursor < len && (TH_READ(cursor) == 'x' || TH_READ(cursor) == 'X')) {
            hex = 1;
            cursor++;
        }
        Py_UCS4 num = 0;
        int overflow = 0;
        Py_ssize_t first_digit = cursor;
        while (cursor < len) {
            Py_UCS4 digit = TH_READ(cursor);
            if (hex) {
                int value = charref_hex_value(digit);
                if (value < 0) {
                    break;
                }
                num = num * 16 + (Py_UCS4)value;
            } else {
                if (digit < '0' || digit > '9') {
                    break;
                }
                num = num * 10 + (digit - '0');
            }
            if (num > 0x110000) {
                num = 0x110000;
                overflow = 1;
            }
            cursor++;
        }
        if (cursor >= len && !self->eof) {
            return -1; /* the run of digits might continue */
        }
        if (cursor == first_digit) {
            /* "&#" or "&#x" with no digits is not a reference: emit it literally */
            push(self, dest, '&');
            push(self, dest, '#');
            if (hex) {
                push(self, dest, TH_READ(p + 1));
            }
            return hex ? 3 : 2;
        }
        /* cursor < len or eof here: the digits-at-buffer-end case already suspended */
        Py_ssize_t end = cursor;
        if (cursor < len && TH_READ(cursor) == ';') {
            end = cursor + 1;
        }
        Py_UCS4 replacement;
        if (overflow || (num >= 0xD800 && num <= 0xDFFF) || num > 0x10FFFF) {
            push(self, dest, REPLACEMENT);
        } else if (charref_find_invalid(num, &replacement)) {
            push(self, dest, replacement);
        } else {
            push(self, dest, num);
        }
        return end - amp;
    }

    if (!is_ascii_alpha(first) && !(first >= '0' && first <= '9')) {
        /* "&" not followed by '#' or a name start is a literal ampersand */
        push(self, dest, '&');
        return 1;
    }

    /* Named reference: collect ASCII alphanumerics (the table's name alphabet)
       plus an optional ';', then take the longest table match. */
    Py_UCS4 chars[HTML5_MAX_NAME_LEN];
    char ascii[HTML5_MAX_NAME_LEN + 1];
    int name_len = 0;
    Py_ssize_t cursor = p;
    while (cursor < len && name_len < HTML5_MAX_NAME_LEN) {
        Py_UCS4 candidate = TH_READ(cursor);
        if (!is_ascii_alpha(candidate) && !(candidate >= '0' && candidate <= '9')) {
            break;
        }
        chars[name_len] = candidate;
        ascii[name_len] = (char)candidate;
        name_len++;
        cursor++;
    }
    if (cursor >= len && name_len == HTML5_MAX_NAME_LEN) {
        /* full buffer, fine to stop */
    } else if (cursor >= len && !self->eof) {
        return -1; /* more name characters might follow */
    }
    int semicolon = 0;
    if (cursor < len && TH_READ(cursor) == ';') {
        ascii[name_len] = ';';
        semicolon = 1;
    } else if (cursor >= len && !self->eof) {
        return -1; /* a ';' might still follow */
    }
    int token_len = name_len + semicolon;

    const html5_entity *entity = charref_find_entity(ascii, token_len);
    int match_len = token_len;
    int match_semicolon = semicolon;
    if (entity == NULL) {
        for (int prefix = name_len - 1; prefix >= 2; prefix--) {
            entity = charref_find_entity(ascii, prefix);
            if (entity != NULL) {
                match_len = prefix;
                match_semicolon = 0;
                break;
            }
        }
    }

    if (entity == NULL) {
        /* no match: emit '&' and the consumed name characters literally */
        push(self, dest, '&');
        for (int i = 0; i < name_len; i++) {
            push(self, dest, chars[i]);
        }
        return 1 + name_len;
    }

    if (in_attr && !match_semicolon) {
        Py_UCS4 after = (match_len < name_len) ? chars[match_len] : (cursor < len ? TH_READ(cursor) : 0);
        if (after == '=' || is_ascii_alpha(after) || (after >= '0' && after <= '9')) {
            /* legacy rule: leave the reference literal inside an attribute */
            push(self, dest, '&');
            for (int i = 0; i < name_len; i++) {
                push(self, dest, chars[i]);
            }
            return 1 + name_len;
        }
    }

    push(self, dest, entity->cp0);
    if (entity->cp1) {
        push(self, dest, entity->cp1);
    }
    /* characters consumed past the matched name are emitted literally */
    for (int i = match_len; i < name_len; i++) {
        push(self, dest, chars[i]);
    }
    return 1 + name_len + match_semicolon;
}

/* Compare the avail available code points against keyword: 2 = full match,
   1 = a proper prefix (more input could complete it), 0 = mismatch. */
static int TH_NAME(match_kw)(const th_tokenizer *self, const char *keyword, int klen, int fold) {
    Py_ssize_t avail = self->input.len - self->pos;
    int n = avail < klen ? (int)avail : klen;
    for (int i = 0; i < n; i++) {
        Py_UCS4 c = TH_READ(self->pos + i);
        if (fold) {
            c = lower_ascii(c);
        }
        if ((Py_UCS4)(unsigned char)keyword[i] != c) {
            return 0;
        }
    }
    return avail >= klen ? 2 : 1;
}

static enum run_result TH_NAME(run)(th_tokenizer *self) {
    for (;;) {
        int at_eof;
        Py_UCS4 ch;
        if (self->pos < self->input.len) {
            ch = TH_READ(self->pos);
            at_eof = 0;
        } else if (self->eof) {
            ch = 0;
            at_eof = 1;
        } else {
            return RUN_NEED_MORE;
        }

        switch (self->state) { /* GCOVR_EXCL_BR_LINE: enum-complete switch; the out-of-range edge is unreachable */
        case ST_DATA:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '&' && ch != '<' && ch != '\n') {
#if TH_UCS1
                Py_ssize_t stop = scan_stops_ucs1(self, self->pos + 1, '&', '<', '\n', '\n');
#else
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = TH_READ(stop);
                    if (c == '&' || c == '<' || c == '\n') {
                        break;
                    }
                    stop++;
                }
#endif
                text_append_run(self, stop);
                continue;
            }
            if (ch == '&') {
                text_begin(self);
                text_materialize(self);
                Py_ssize_t n = TH_NAME(consume_charref)(self, &self->text, 0);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_TAG_OPEN;
                continue;
            }
            text_push(self, ch);
            CONSUME();
            continue;

        case ST_RCDATA:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '&' && ch != '<' && ch != '\n' && ch != 0) {
#if TH_UCS1
                Py_ssize_t stop = scan_stops_ucs1(self, self->pos + 1, '&', '<', '\n', 0);
#else
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = TH_READ(stop);
                    if (c == '&' || c == '<' || c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
#endif
                text_append_run(self, stop);
                continue;
            }
            if (ch == '&') {
                text_begin(self);
                text_materialize(self);
                Py_ssize_t n = TH_NAME(consume_charref)(self, &self->text, 0);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_RCDATA_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_RAWTEXT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '<' && ch != '\n' && ch != 0) {
#if TH_UCS1
                Py_ssize_t stop = scan_stops_ucs1(self, self->pos + 1, '<', '\n', 0, 0);
#else
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = TH_READ(stop);
                    if (c == '<' || c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
#endif
                text_append_run(self, stop);
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_RAWTEXT_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '<' && ch != '\n' && ch != 0) {
#if TH_UCS1
                Py_ssize_t stop = scan_stops_ucs1(self, self->pos + 1, '<', '\n', 0, 0);
#else
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = TH_READ(stop);
                    if (c == '<' || c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
#endif
                text_append_run(self, stop);
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_PLAINTEXT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '\n' && ch != 0) {
#if TH_UCS1
                Py_ssize_t stop = scan_stops_ucs1(self, self->pos + 1, '\n', 0, 0, 0);
#else
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = TH_READ(stop);
                    if (c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
#endif
                text_append_run(self, stop);
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_TAG_OPEN:
            if (at_eof) {
                text_begin_mark(self);
                text_push(self, '<');
                EOF_FLUSH();
            }
            if (ch == '!') {
                CONSUME();
                self->state = ST_MARKUP_DECL_OPEN;
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_END_TAG_OPEN;
                continue;
            }
            if (is_ascii_alpha(ch)) {
                start_tag(self, 0, lower_ascii(ch));
                CONSUME();
                self->state = ST_TAG_NAME;
                continue;
            }
            if (ch == '?') {
                init_markup(self, TH_COMMENT);
                self->state = ST_BOGUS_COMMENT;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_DATA;
            continue;

        case ST_END_TAG_OPEN:
            if (at_eof) {
                text_begin_mark(self);
                text_push(self, '<');
                text_push(self, '/');
                EOF_FLUSH();
            }
            if (is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                CONSUME();
                self->state = ST_TAG_NAME;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                self->state = ST_DATA;
                continue;
            }
            init_markup(self, TH_COMMENT);
            self->state = ST_BOGUS_COMMENT;
            continue;

        case ST_TAG_NAME:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            push(self, &self->tok.name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            continue;

        case ST_RCDATA_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_RCDATA_END_OPEN;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_RCDATA;
            continue;

        case ST_RCDATA_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_RCDATA_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_RCDATA;
            continue;

        case ST_RCDATA_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_RCDATA);
            continue;

        case ST_RAWTEXT_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_RAWTEXT_END_OPEN;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_RAWTEXT;
            continue;

        case ST_RAWTEXT_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_RAWTEXT_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_RAWTEXT;
            continue;

        case ST_RAWTEXT_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_RAWTEXT);
            continue;

        case ST_SCRIPT_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_SCRIPT_END_OPEN;
                continue;
            }
            if (!at_eof && ch == '!') {
                CONSUME();
                text_begin_mark(self);
                text_push(self, '<');
                text_push(self, '!');
                self->state = ST_SCRIPT_ESC_START;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_SCRIPT_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_SCRIPT);
            continue;

        case ST_SCRIPT_ESC_START:
            if (!at_eof && ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESC_START_DASH;
                continue;
            }
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_ESC_START_DASH:
            if (!at_eof && ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESCAPED_DASH_DASH;
                continue;
            }
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_ESCAPED:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESCAPED_DASH;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT_ESCAPED_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESCAPED_DASH_DASH;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_DASH_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_LT;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                text_push(self, '>');
                self->state = ST_SCRIPT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_SCRIPT_ESCAPED_END_OPEN;
                continue;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                buf_reset(&self->temp);
                text_begin_mark(self);
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESC_START;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_SCRIPT_ESCAPED);
            continue;

        case ST_SCRIPT_DOUBLE_ESC_START:
            if (!at_eof && (is_space(ch) || ch == '/' || ch == '>')) {
                CONSUME();
                text_push(self, ch);
                self->state = (self->temp.len == 6 && memcmp(self->temp.data, "script", 6) == 0)
                                  ? ST_SCRIPT_DOUBLE_ESCAPED
                                  : ST_SCRIPT_ESCAPED;
                continue;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->temp, lower_ascii(ch));
                text_push(self, ch);
                CONSUME();
                continue;
            }
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_DASH;
                continue;
            }
            if (ch == '<') {
                CONSUME();
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_DASH_DASH;
                continue;
            }
            if (ch == '<') {
                CONSUME();
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_DASH_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                continue;
            }
            if (ch == '<') {
                CONSUME();
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_LT;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                text_push(self, '>');
                self->state = ST_SCRIPT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                text_push(self, '/');
                self->state = ST_SCRIPT_DOUBLE_ESC_END;
                continue;
            }
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESC_END:
            if (!at_eof && (is_space(ch) || ch == '/' || ch == '>')) {
                CONSUME();
                text_push(self, ch);
                self->state = (self->temp.len == 6 && memcmp(self->temp.data, "script", 6) == 0)
                                  ? ST_SCRIPT_ESCAPED
                                  : ST_SCRIPT_DOUBLE_ESCAPED;
                continue;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->temp, lower_ascii(ch));
                text_push(self, ch);
                CONSUME();
                continue;
            }
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_BEFORE_ATTR_NAME:
            if (at_eof || ch == '/' || ch == '>') {
                self->state = ST_AFTER_ATTR_NAME;
                continue;
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            new_attr(self);
            if (ch == '=') {
                push(self, &self->attr->name, ch);
                CONSUME();
            }
            self->state = ST_ATTR_NAME;
            continue;

        case ST_ATTR_NAME:
            if (at_eof || is_space(ch) || ch == '/' || ch == '>') {
                self->state = ST_AFTER_ATTR_NAME;
                continue;
            }
            if (ch == '=') {
                CONSUME();
                self->state = ST_BEFORE_ATTR_VALUE;
                continue;
            }
            push(self, &self->attr->name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            continue;

        case ST_AFTER_ATTR_NAME:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (ch == '=') {
                CONSUME();
                self->state = ST_BEFORE_ATTR_VALUE;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            new_attr(self);
            self->state = ST_ATTR_NAME;
            continue;

        case ST_BEFORE_ATTR_VALUE:
            if (!at_eof && is_space(ch)) {
                CONSUME();
                continue;
            }
            self->attr->has_value = 1;
            if (!at_eof && ch == '"') {
                CONSUME();
                self->state = ST_ATTR_VALUE_DQ;
                continue;
            }
            if (!at_eof && ch == '\'') {
                CONSUME();
                self->state = ST_ATTR_VALUE_SQ;
                continue;
            }
            if (!at_eof && ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            self->state = ST_ATTR_VALUE_UNQ;
            continue;

        case ST_ATTR_VALUE_DQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '"') {
                CONSUME();
                self->state = ST_AFTER_ATTR_VALUE_QUOTED;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t n = TH_NAME(consume_charref)(self, &self->attr->value, 1);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_ATTR_VALUE_SQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '\'') {
                CONSUME();
                self->state = ST_AFTER_ATTR_VALUE_QUOTED;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t n = TH_NAME(consume_charref)(self, &self->attr->value, 1);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_ATTR_VALUE_UNQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t n = TH_NAME(consume_charref)(self, &self->attr->value, 1);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_AFTER_ATTR_VALUE_QUOTED:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            self->state = ST_BEFORE_ATTR_NAME;
            continue;

        case ST_SELF_CLOSING_START_TAG:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '>') {
                CONSUME();
                self->tok.self_closing = 1;
                finish_tag(self);
                return RUN_EMITTED;
            }
            self->state = ST_BEFORE_ATTR_NAME;
            continue;

        case ST_BOGUS_COMMENT:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.text, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_MARKUP_DECL_OPEN: {
            int m = TH_NAME(match_kw)(self, "--", 2, 0);
            if (m == 2) {
                self->pos += 2;
                self->col += 2;
                init_markup(self, TH_COMMENT);
                self->state = ST_COMMENT_START;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            m = TH_NAME(match_kw)(self, "doctype", 7, 1);
            if (m == 2) {
                self->pos += 7;
                self->col += 7;
                self->state = ST_DOCTYPE;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            m = TH_NAME(match_kw)(self, "[CDATA[", 7, 0);
            if (m == 2) {
                self->pos += 7;
                self->col += 7;
                init_markup(self, TH_COMMENT);
                for (const char *k = "[CDATA["; *k; k++) {
                    push(self, &self->tok.text, (Py_UCS4)(unsigned char)*k);
                }
                self->state = ST_BOGUS_COMMENT;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            init_markup(self, TH_COMMENT);
            self->state = ST_BOGUS_COMMENT;
            continue;
        }

        case ST_COMMENT_START:
            if (!at_eof && ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_START_DASH;
                continue;
            }
            if (!at_eof && ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_START_DASH:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_END;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.text, '-');
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '<') {
                push(self, &self->tok.text, '<');
                CONSUME();
                self->state = ST_COMMENT_LT;
                continue;
            }
            if (ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_END_DASH;
                continue;
            }
            push(self, &self->tok.text, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_COMMENT_LT:
            if (!at_eof && ch == '!') {
                push(self, &self->tok.text, '!');
                CONSUME();
                self->state = ST_COMMENT_LT_BANG;
                continue;
            }
            if (!at_eof && ch == '<') {
                push(self, &self->tok.text, '<');
                CONSUME();
                continue;
            }
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_LT_BANG:
            if (!at_eof && ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_LT_BANG_DASH;
                continue;
            }
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_LT_BANG_DASH:
            if (!at_eof && ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_LT_BANG_DASH_DASH;
                continue;
            }
            self->state = ST_COMMENT_END_DASH;
            continue;

        case ST_COMMENT_LT_BANG_DASH_DASH:
            self->state = ST_COMMENT_END;
            continue;

        case ST_COMMENT_END_DASH:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_END;
                continue;
            }
            push(self, &self->tok.text, '-');
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_END:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == '!') {
                CONSUME();
                self->state = ST_COMMENT_END_BANG;
                continue;
            }
            if (ch == '-') {
                push(self, &self->tok.text, '-');
                CONSUME();
                continue;
            }
            push(self, &self->tok.text, '-');
            push(self, &self->tok.text, '-');
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_END_BANG:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '-') {
                push(self, &self->tok.text, '-');
                push(self, &self->tok.text, '-');
                push(self, &self->tok.text, '!');
                CONSUME();
                self->state = ST_COMMENT_END_DASH;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.text, '-');
            push(self, &self->tok.text, '-');
            push(self, &self->tok.text, '!');
            self->state = ST_COMMENT;
            continue;

        case ST_DOCTYPE:
            if (at_eof) {
                init_markup(self, TH_DOCTYPE);
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_DOCTYPE_NAME;
                continue;
            }
            self->state = ST_BEFORE_DOCTYPE_NAME;
            continue;

        case ST_BEFORE_DOCTYPE_NAME:
            if (at_eof) {
                init_markup(self, TH_DOCTYPE);
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            init_markup(self, TH_DOCTYPE);
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            self->state = ST_DOCTYPE_NAME;
            continue;

        case ST_DOCTYPE_NAME:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_AFTER_DOCTYPE_NAME;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            continue;

        case ST_AFTER_DOCTYPE_NAME: {
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            int m = TH_NAME(match_kw)(self, "public", 6, 1);
            if (m == 2) {
                self->pos += 6;
                self->col += 6;
                self->state = ST_AFTER_DOCTYPE_PUBLIC_KW;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            m = TH_NAME(match_kw)(self, "system", 6, 1);
            if (m == 2) {
                self->pos += 6;
                self->col += 6;
                self->state = ST_AFTER_DOCTYPE_SYSTEM_KW;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;
        }

        case ST_AFTER_DOCTYPE_PUBLIC_KW:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_DOCTYPE_PUBLIC_ID;
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_public_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_PUBLIC_ID_DQ : ST_DOCTYPE_PUBLIC_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BEFORE_DOCTYPE_PUBLIC_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_public_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_PUBLIC_ID_DQ : ST_DOCTYPE_PUBLIC_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_DOCTYPE_PUBLIC_ID_DQ:
        case ST_DOCTYPE_PUBLIC_ID_SQ: {
            Py_UCS4 quote = (self->state == ST_DOCTYPE_PUBLIC_ID_DQ) ? '"' : '\'';
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (ch == quote) {
                CONSUME();
                self->state = ST_AFTER_DOCTYPE_PUBLIC_ID;
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.public_id, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;
        }

        case ST_AFTER_DOCTYPE_PUBLIC_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BETWEEN_DOCTYPE_PUB_SYS;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BETWEEN_DOCTYPE_PUB_SYS:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_AFTER_DOCTYPE_SYSTEM_KW:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_DOCTYPE_SYSTEM_ID;
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BEFORE_DOCTYPE_SYSTEM_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_DOCTYPE_SYSTEM_ID_DQ:
        case ST_DOCTYPE_SYSTEM_ID_SQ: {
            Py_UCS4 quote = (self->state == ST_DOCTYPE_SYSTEM_ID_DQ) ? '"' : '\'';
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (ch == quote) {
                CONSUME();
                self->state = ST_AFTER_DOCTYPE_SYSTEM_ID;
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.system_id, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;
        }

        case ST_AFTER_DOCTYPE_SYSTEM_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BOGUS_DOCTYPE:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            CONSUME();
            continue;

        case ST_CDATA:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != ']' && ch != '\n') {
#if TH_UCS1
                Py_ssize_t stop = scan_stops_ucs1(self, self->pos + 1, ']', '\n', '\n', '\n');
#else
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = TH_READ(stop);
                    if (c == ']' || c == '\n') {
                        break;
                    }
                    stop++;
                }
#endif
                text_append_run(self, stop);
                continue;
            }
            if (ch == ']') {
                CONSUME();
                self->state = ST_CDATA_BRACKET;
                continue;
            }
            text_push(self, ch);
            CONSUME();
            continue;

        case ST_CDATA_BRACKET:
            if (!at_eof && ch == ']') {
                CONSUME();
                self->state = ST_CDATA_END;
                continue;
            }
            text_push(self, ']');
            self->state = ST_CDATA;
            continue;

        case ST_CDATA_END:
            if (!at_eof && ch == '>') {
                CONSUME();
                self->state = ST_DATA;
                continue;
            }
            if (!at_eof && ch == ']') {
                text_push(self, ']');
                CONSUME();
                continue;
            }
            text_push(self, ']');
            text_push(self, ']');
            self->state = ST_CDATA;
            continue;
        }
    }
}

#undef TH_READ
