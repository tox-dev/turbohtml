/* Kind-stamped tokenizer core. tokenizer/statemachine.c #includes this file three times,
   once per PyUnicode storage width, with TH_NAME, TH_CHAR and TH_KIND defined,
   the way CPython's stringlib specializes per kind. Every input read compiles
   to direct indexing, so the per-character paths and run scanners carry no
   width dispatch. gcov attributes all three instantiations to these lines, so
   coverage aggregates across kinds. */

#define TH_READ(i) ((Py_UCS4)((const TH_CHAR *)self->input.data)[(i)])

static inline void TH_NAME(charref_push)(th_tokenizer *self, th_buf *dest, Py_UCS4 ch) {
    if (dest != NULL) {
        push(self, dest, ch);
    }
}

/* Resolve a character reference starting at the '&' under self->pos, appending
   the decoded code points to dest. in_attr selects the legacy attribute rule
   (a named reference without a trailing ';' is left literal when followed by
   '=' or an ASCII alphanumeric). Returns the number of input code points to
   consume (always >= 1) or -1 to suspend for more input; allocation failures
   land on the sticky oom flag. The numeric rules follow the tokenizer spec,
   which emits control and noncharacter code points rather than dropping them
   the way unescape() does. */
/* is_reference (when non-NULL) reports whether the run decoded an actual
   reference (1) rather than leaving a literal ampersand sequence (0), so the
   text states can split a real reference into its own token. */
static Py_ssize_t TH_NAME(consume_charref)(th_tokenizer *self, th_buf *dest, int in_attr, int *is_reference) {
    Py_ssize_t len = self->input.len;
    Py_ssize_t amp = self->pos;
    Py_ssize_t after_amp = amp + 1;
    if (is_reference != NULL) {
        *is_reference = 0;
    }

    if (after_amp >= len) {
        if (!self->eof) {
            return -1;
        }
        TH_NAME(charref_push)(self, dest, '&');
        return 1;
    }

    Py_UCS4 first = TH_READ(after_amp);
    if (first == '#') {
        Py_ssize_t cursor = after_amp + 1;
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
            tok_error_at(self, "absence-of-digits-in-numeric-character-reference", self->col + (hex ? 3 : 2));
            TH_NAME(charref_push)(self, dest, '&');
            TH_NAME(charref_push)(self, dest, '#');
            if (hex) {
                TH_NAME(charref_push)(self, dest, TH_READ(after_amp + 1));
            }
            return hex ? 3 : 2;
        }
        /* cursor < len or eof here: the digits-at-buffer-end case already suspended */
        Py_ssize_t end = cursor;
        if (cursor < len && TH_READ(cursor) == ';') {
            end = cursor + 1;
        }
        /* the spec places each error below at the reference's end rather than at its '&' */
        Py_ssize_t end_col = self->col + (end - amp);
        if (end == cursor) {
            tok_error_at(self, "missing-semicolon-after-character-reference", end_col);
        }
        Py_UCS4 replacement;
        if (num == 0) {
            tok_error_at(self, "null-character-reference", end_col);
            TH_NAME(charref_push)(self, dest, REPLACEMENT);
        } else if (overflow || num > 0x10FFFF) {
            tok_error_at(self, "character-reference-outside-unicode-range", end_col);
            TH_NAME(charref_push)(self, dest, REPLACEMENT);
        } else if (num >= 0xD800 && num <= 0xDFFF) {
            tok_error_at(self, "surrogate-character-reference", end_col);
            TH_NAME(charref_push)(self, dest, REPLACEMENT);
        } else {
            if ((num >= 0xFDD0 && num <= 0xFDEF) || (num & 0xFFFE) == 0xFFFE) {
                tok_error_at(self, "noncharacter-character-reference", end_col);
            } else if (num == 0x0D || (num >= 0x7F && num <= 0x9F) ||
                       (num < 0x20 && num != 0x09 && num != 0x0A && num != 0x0C)) {
                /* 0x0D is ASCII whitespace, and the spec still names it here */
                tok_error_at(self, "control-character-reference", end_col);
            }
            if (charref_find_invalid(num, &replacement)) {
                TH_NAME(charref_push)(self, dest, replacement);
            } else {
                TH_NAME(charref_push)(self, dest, num);
            }
        }
        if (is_reference != NULL) {
            *is_reference = 1;
        }
        return end - amp;
    }

    if (!is_ascii_alpha(first) && !(first >= '0' && first <= '9')) {
        /* "&" not followed by '#' or a name start is a literal ampersand */
        TH_NAME(charref_push)(self, dest, '&');
        return 1;
    }

    /* Named reference: collect ASCII alphanumerics (the table's name alphabet)
       plus an optional ';', then take the longest table match. */
    Py_UCS4 chars[HTML5_MAX_NAME_LEN];
    char ascii[HTML5_MAX_NAME_LEN + 1];
    int name_len = 0;
    Py_ssize_t cursor = after_amp;
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
    /* A name longer than the table's longest cannot match, but the spec keeps consuming
       alphanumerics, so a ';' beyond the cap still says the author meant a reference. */
    int named_semicolon = semicolon;
    Py_ssize_t ambiguous_end = cursor;
    if (name_len == HTML5_MAX_NAME_LEN) {
        while (ambiguous_end < len && (is_ascii_alpha(TH_READ(ambiguous_end)) ||
                                       (TH_READ(ambiguous_end) >= '0' && TH_READ(ambiguous_end) <= '9'))) {
            ambiguous_end++;
        }
        if (ambiguous_end >= len && !self->eof) {
            return -1; /* the run of name characters might continue */
        }
        named_semicolon = ambiguous_end < len && TH_READ(ambiguous_end) == ';';
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
        /* no match: emit '&' and the consumed name characters literally. A trailing ';'
           means the author meant a reference the table does not have. */
        if (named_semicolon) {
            tok_error_at(self, "unknown-named-character-reference", self->col + (ambiguous_end - amp));
        }
        TH_NAME(charref_push)(self, dest, '&');
        for (int index = 0; index < name_len; index++) {
            TH_NAME(charref_push)(self, dest, chars[index]);
        }
        return 1 + name_len;
    }

    if (in_attr && !match_semicolon) {
        Py_UCS4 after = (match_len < name_len) ? chars[match_len] : (cursor < len ? TH_READ(cursor) : 0);
        if (after == '=' || is_ascii_alpha(after) || (after >= '0' && after <= '9')) {
            /* legacy rule: leave the reference literal inside an attribute */
            TH_NAME(charref_push)(self, dest, '&');
            for (int index = 0; index < name_len; index++) {
                TH_NAME(charref_push)(self, dest, chars[index]);
            }
            return 1 + name_len;
        }
    }

    if (!match_semicolon) {
        tok_error_at(self, "missing-semicolon-after-character-reference", self->col + 1 + match_len);
    }
    TH_NAME(charref_push)(self, dest, entity->cp0);
    if (entity->cp1) {
        TH_NAME(charref_push)(self, dest, entity->cp1);
    }
    /* characters consumed past the matched name are emitted literally */
    for (int index = match_len; index < name_len; index++) {
        TH_NAME(charref_push)(self, dest, chars[index]);
    }
    if (is_reference != NULL) {
        *is_reference = 1;
    }
    return 1 + name_len + match_semicolon;
}

/* Handle a '&' in text content when references are split out (resolve_references
   off). Decode it into the scratch buffer: a real reference becomes a queued
   TH_CHARREF token (RUN_EMITTED), a literal ampersand sequence joins the
   surrounding text run (RUN_DONE), and an incomplete reference suspends
   (RUN_NEED_MORE). */
static enum run_result TH_NAME(text_charref)(th_tokenizer *self) {
    Py_ssize_t amp = self->pos;
    Py_ssize_t ref_line = self->line;
    Py_ssize_t ref_col = self->col;
    int is_ref = 0;
    buf_reset(&self->ref);
    Py_ssize_t consumed = TH_NAME(consume_charref)(self, &self->ref, 0, &is_ref);
    if (consumed == -1) {
        return RUN_NEED_MORE;
    }
    if (is_ref) {
        self->ref_line = ref_line;
        self->ref_col = ref_col;
        self->pos += consumed;
        self->col += consumed;
        emit_charref(self, amp, consumed);
        return RUN_EMITTED;
    }
    text_begin(self);
    text_materialize(self);
    for (Py_ssize_t index = 0; index < self->ref.len; index++) {
        push(self, &self->text, buf_read(&self->ref, index));
    }
    self->pos += consumed;
    self->col += consumed;
    return RUN_DONE;
}

/* Compare the available code points against keyword: 2 = full match,
   1 = a proper prefix (more input could complete it), 0 = mismatch. */
static int TH_NAME(match_kw)(const th_tokenizer *self, const char *keyword, int klen, int fold) {
    Py_ssize_t avail = self->input.len - self->pos;
    int count = avail < klen ? (int)avail : klen;
    for (int index = 0; index < count; index++) {
        Py_UCS4 character = TH_READ(self->pos + index);
        if (fold) {
            character = lower_ascii(character);
        }
        if ((Py_UCS4)(unsigned char)keyword[index] != character) {
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

        /* The first time EOF is reached with a tag/comment/doctype construct
           still open, report it once; eof_code names the construct (cleared when
           it is emitted), so a clean DATA-state EOF leaves it NULL. */
        if (at_eof && !self->eof_reported && self->eof_code != NULL) {
            tok_error(self, self->eof_code);
            self->eof_reported = 1;
        }

        switch (self->state) { /* GCOVR_EXCL_BR_LINE: enum-complete switch; the out-of-range edge is unreachable */
        case ST_DATA:
            if (at_eof) {
                EOF_FLUSH();
            }
#if TH_UCS1
            /* '\n' is ordinary text in DATA -- the only per-newline work is
               line/column tracking, so a single SIMD pass runs straight through
               newlines to the next markup (a paragraph between tags becomes one
               sweep, not one per wrapped line) and folds the newline count and
               last-newline column in along the way. The wider widths keep the
               generic newline-stopping scan: they are rarer and the dedicated
               1-byte pass is where real documents live. */
            if (ch != '&' && ch != '<' && ch != 0) {
                Py_ssize_t newlines;
                Py_ssize_t last_nl;
                Py_ssize_t stop = scan_data_ucs1(self, self->pos, &newlines, &last_nl);
                text_append_run_lc(self, stop, newlines, last_nl < 0 ? 0 : stop - last_nl - 1);
                continue;
            }
#else
            if (ch != '&' && ch != '<' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = TH_SCAN(self, self->pos + 1, '&', '<', '\n', 0);
                text_append_run(self, stop);
                continue;
            }
#endif
            if (ch == 0) {
                /* the data state keeps the NUL, where every other text state replaces it */
                tok_error(self, "unexpected-null-character");
                text_begin(self);
                text_push(self, 0);
                CONSUME();
                continue;
            }
            if (ch == '&') {
                if (self->resolve_references) {
                    text_begin(self);
                    text_materialize(self);
                    Py_ssize_t consumed = TH_NAME(consume_charref)(self, &self->text, 0, NULL);
                    if (consumed == -1) {
                        return RUN_NEED_MORE;
                    }
                    self->pos += consumed;
                    self->col += consumed;
                    continue;
                }
                switch (TH_NAME(text_charref)(self)) {
                case RUN_NEED_MORE:
                    return RUN_NEED_MORE;
                case RUN_EMITTED:
                    return RUN_EMITTED;
                default: /* a literal ampersand sequence joined the text run */
                    continue;
                }
            }
#if TH_UCS1
            /* the run branch consumed every character but '&' and '<', and '&' is
               handled above, so ch is '<' here: open a tag with no further test
               (the wider widths still fall through a bare-newline text_push) */
            MARK();
            CONSUME();
            self->state = ST_TAG_OPEN;
            continue;
#else
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_TAG_OPEN;
                continue;
            }
            text_push(self, ch);
            CONSUME();
            continue;
#endif

        case ST_RCDATA:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '&' && ch != '<' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = TH_SCAN(self, self->pos + 1, '&', '<', '\n', 0);
                text_append_run(self, stop);
                continue;
            }
            if (ch == '&') {
                if (self->resolve_references) {
                    text_begin(self);
                    text_materialize(self);
                    Py_ssize_t consumed = TH_NAME(consume_charref)(self, &self->text, 0, NULL);
                    if (consumed == -1) {
                        return RUN_NEED_MORE;
                    }
                    self->pos += consumed;
                    self->col += consumed;
                    continue;
                }
                switch (TH_NAME(text_charref)(self)) {
                case RUN_NEED_MORE:
                    return RUN_NEED_MORE;
                case RUN_EMITTED:
                    return RUN_EMITTED;
                default: /* a literal ampersand sequence joined the text run */
                    continue;
                }
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_RCDATA_LT;
                continue;
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_RAWTEXT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '<' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = TH_SCAN(self, self->pos + 1, '<', '\n', 0, 0);
                text_append_run(self, stop);
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_RAWTEXT_LT;
                continue;
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '<' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = TH_SCAN(self, self->pos + 1, '<', '\n', 0, 0);
                text_append_run(self, stop);
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_LT;
                continue;
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_PLAINTEXT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '\n' && ch != 0) {
                Py_ssize_t stop = TH_SCAN(self, self->pos + 1, '\n', 0, 0, 0);
                text_append_run(self, stop);
                continue;
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_TAG_OPEN:
            if (at_eof) {
                tok_error(self, "eof-before-tag-name");
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
                tok_error(self, "unexpected-question-mark-instead-of-tag-name");
                init_markup(self, TH_COMMENT);
                self->tok.is_pi = 1; /* a `<?` bogus comment the SAX walk reports as a processing instruction */
                self->state = ST_BOGUS_COMMENT;
                continue;
            }
            tok_error(self, "invalid-first-character-of-tag-name");
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_DATA;
            continue;

        case ST_END_TAG_OPEN:
            if (at_eof) {
                tok_error(self, "eof-before-tag-name");
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
                tok_error(self, "missing-end-tag-name");
                CONSUME();
                self->state = ST_DATA;
                continue;
            }
            tok_error(self, "invalid-first-character-of-tag-name");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
                self->eof_code = NULL; /* speculative until it proves to be the appropriate end tag */
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
                self->eof_code = "eof-in-tag"; /* the end tag is the appropriate one, so it is now open */
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->eof_code = "eof-in-tag";
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
                self->eof_code = NULL; /* speculative until it proves to be the appropriate end tag */
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
                self->eof_code = "eof-in-tag"; /* the end tag is the appropriate one, so it is now open */
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->eof_code = "eof-in-tag";
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
                self->eof_code = NULL; /* speculative until it proves to be the appropriate end tag */
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
                self->eof_code = "eof-in-tag"; /* the end tag is the appropriate one, so it is now open */
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->eof_code = "eof-in-tag";
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
                tok_error(self, "eof-in-script-html-comment-like-text");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT_ESCAPED_DASH:
            if (at_eof) {
                tok_error(self, "eof-in-script-html-comment-like-text");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_DASH_DASH:
            if (at_eof) {
                tok_error(self, "eof-in-script-html-comment-like-text");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
                self->eof_code = NULL; /* speculative until it proves to be the appropriate end tag */
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
                self->eof_code = "eof-in-tag"; /* the end tag is the appropriate one, so it is now open */
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->eof_code = "eof-in-tag";
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
                tok_error(self, "eof-in-script-html-comment-like-text");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_DASH:
            if (at_eof) {
                tok_error(self, "eof-in-script-html-comment-like-text");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_DASH_DASH:
            if (at_eof) {
                tok_error(self, "eof-in-script-html-comment-like-text");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
                tok_error(self, "unexpected-equals-sign-before-attribute-name");
                if (self->capture_attributes) {
                    push(self, &self->attr->name, ch);
                }
                CONSUME();
            }
            self->state = ST_ATTR_NAME;
            continue;

        case ST_ATTR_NAME:
            if (at_eof || is_space(ch) || ch == '/' || ch == '>') {
                finish_attr_name(self);
                self->state = ST_AFTER_ATTR_NAME;
                continue;
            }
            if (ch == '=') {
                finish_attr_name(self);
                CONSUME();
                self->state = ST_BEFORE_ATTR_VALUE;
                continue;
            }
            if (ch == '"' || ch == '\'' || ch == '<') {
                tok_error(self, "unexpected-character-in-attribute-name");
            } else if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            if (self->capture_attributes) {
                push(self, &self->attr->name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            }
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
            if (self->capture_attributes) {
                self->attr->has_value = 1;
            }
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
                tok_error(self, "missing-attribute-value");
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
            if (ch != '"' && ch != '&' && ch != '\n' && ch != 0) {
                /* bulk-copy the ordinary value run; '\n' is a stop so line/col
                   stay accurate, and &/" /NUL keep their dedicated handling */
                Py_ssize_t stop = TH_SCAN(self, self->pos, '"', '&', '\n', 0);
                if (self->capture_attributes) {
                    buf_append_input(self, &self->attr->value, self->pos, stop - self->pos);
                }
                self->col += stop - self->pos;
                self->pos = stop;
                continue;
            }
            if (ch == '"') {
                CONSUME();
                attr_end_here(self); /* the closing quote is part of the attribute span */
                self->state = ST_AFTER_ATTR_VALUE_QUOTED;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t consumed =
                    TH_NAME(consume_charref)(self, self->capture_attributes ? &self->attr->value : NULL, 1, NULL);
                if (consumed == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += consumed;
                self->col += consumed;
                continue;
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            if (self->capture_attributes) {
                push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            }
            CONSUME();
            continue;

        case ST_ATTR_VALUE_SQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '\'' && ch != '&' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = TH_SCAN(self, self->pos, '\'', '&', '\n', 0);
                if (self->capture_attributes) {
                    buf_append_input(self, &self->attr->value, self->pos, stop - self->pos);
                }
                self->col += stop - self->pos;
                self->pos = stop;
                continue;
            }
            if (ch == '\'') {
                CONSUME();
                attr_end_here(self); /* the closing quote is part of the attribute span */
                self->state = ST_AFTER_ATTR_VALUE_QUOTED;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t consumed =
                    TH_NAME(consume_charref)(self, self->capture_attributes ? &self->attr->value : NULL, 1, NULL);
                if (consumed == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += consumed;
                self->col += consumed;
                continue;
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            if (self->capture_attributes) {
                push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            }
            CONSUME();
            continue;

        case ST_ATTR_VALUE_UNQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                attr_end_here(self); /* the value ends at the delimiting whitespace */
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t consumed =
                    TH_NAME(consume_charref)(self, self->capture_attributes ? &self->attr->value : NULL, 1, NULL);
                if (consumed == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += consumed;
                self->col += consumed;
                continue;
            }
            if (ch == '>') {
                attr_end_here(self); /* the value ends at the closing '>' */
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (ch == '"' || ch == '\'' || ch == '<' || ch == '=' || ch == '`') {
                tok_error(self, "unexpected-character-in-unquoted-attribute-value");
            } else if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            if (self->capture_attributes) {
                push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            }
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
            tok_error(self, "missing-whitespace-between-attributes");
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
            tok_error(self, "unexpected-solidus-in-tag");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            push(self, &self->tok.text, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_MARKUP_DECL_OPEN: {
            int match = TH_NAME(match_kw)(self, "--", 2, 0);
            if (match == 2) {
                self->pos += 2;
                self->col += 2;
                init_markup(self, TH_COMMENT);
                self->eof_code = "eof-in-comment"; /* the comment is now open */
                self->state = ST_COMMENT_START;
                continue;
            }
            if (match == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            match = TH_NAME(match_kw)(self, "doctype", 7, 1);
            if (match == 2) {
                self->pos += 7;
                self->col += 7;
                self->eof_code = "eof-in-doctype"; /* the doctype is now open */
                self->state = ST_DOCTYPE;
                continue;
            }
            if (match == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            match = TH_NAME(match_kw)(self, "[CDATA[", 7, 0);
            if (match == 2) {
                self->pos += 7;
                self->col += 7;
                if (self->cdata_ok) {
                    self->state = ST_CDATA; /* CDATA content is a plain text run */
                    continue;
                }
                tok_error_at(self, "cdata-in-html-content", self->col - 1);
                init_markup(self, TH_COMMENT);
                for (const char *cdata_char = "[CDATA["; *cdata_char; cdata_char++) {
                    push(self, &self->tok.text, (Py_UCS4)(unsigned char)*cdata_char);
                }
                self->state = ST_BOGUS_COMMENT;
                continue;
            }
            if (match == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            tok_error(self, "incorrectly-opened-comment");
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
                tok_error(self, "abrupt-closing-of-empty-comment");
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
                tok_error(self, "abrupt-closing-of-empty-comment");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
            if (!at_eof && ch != '>') {
                tok_error(self, "nested-comment");
            }
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
                tok_error(self, "incorrectly-closed-comment");
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
            if (ch != '>') {
                tok_error(self, "missing-whitespace-before-doctype-name");
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
                tok_error(self, "missing-doctype-name");
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
            int match = TH_NAME(match_kw)(self, "public", 6, 1);
            if (match == 2) {
                self->pos += 6;
                self->col += 6;
                self->state = ST_AFTER_DOCTYPE_PUBLIC_KW;
                continue;
            }
            if (match == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            match = TH_NAME(match_kw)(self, "system", 6, 1);
            if (match == 2) {
                self->pos += 6;
                self->col += 6;
                self->state = ST_AFTER_DOCTYPE_SYSTEM_KW;
                continue;
            }
            if (match == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            tok_error(self, "invalid-character-sequence-after-doctype-name");
            self->tok.force_quirks = 1;
            BOGUS_DOCTYPE();
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
                tok_error(self, "missing-whitespace-after-doctype-public-keyword");
                self->tok.has_public_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_PUBLIC_ID_DQ : ST_DOCTYPE_PUBLIC_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                tok_error(self, "missing-doctype-public-identifier");
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            tok_error(self, "missing-quote-before-doctype-public-identifier");
            self->tok.force_quirks = 1;
            BOGUS_DOCTYPE();

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
                tok_error(self, "missing-doctype-public-identifier");
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            tok_error(self, "missing-quote-before-doctype-public-identifier");
            self->tok.force_quirks = 1;
            BOGUS_DOCTYPE();

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
                tok_error(self, "abrupt-doctype-public-identifier");
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
                tok_error(self, "missing-whitespace-between-doctype-public-and-system-identifiers");
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            tok_error(self, "missing-quote-before-doctype-system-identifier");
            self->tok.force_quirks = 1;
            BOGUS_DOCTYPE();

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
            tok_error(self, "missing-quote-before-doctype-system-identifier");
            self->tok.force_quirks = 1;
            BOGUS_DOCTYPE();

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
                tok_error(self, "missing-whitespace-after-doctype-system-keyword");
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                tok_error(self, "missing-doctype-system-identifier");
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            tok_error(self, "missing-quote-before-doctype-system-identifier");
            self->tok.force_quirks = 1;
            BOGUS_DOCTYPE();

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
                tok_error(self, "missing-doctype-system-identifier");
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            tok_error(self, "missing-quote-before-doctype-system-identifier");
            self->tok.force_quirks = 1;
            BOGUS_DOCTYPE();

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
                tok_error(self, "abrupt-doctype-system-identifier");
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
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
            tok_error(self, "unexpected-character-after-doctype-system-identifier");
            BOGUS_DOCTYPE();

        case ST_BOGUS_DOCTYPE:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == 0) {
                tok_error(self, "unexpected-null-character");
            }
            CONSUME();
            continue;

        case ST_CDATA:
            if (at_eof) {
                tok_error(self, "eof-in-cdata");
                EOF_FLUSH();
            }
            if (ch != ']' && ch != '\n') {
                Py_ssize_t stop = TH_SCAN(self, self->pos + 1, ']', '\n', '\n', '\n');
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
