/* Pulls the real article out of a cluttered page -- stripping nav, sidebars, and
   boilerplate -- so a reader view or a clean extract keeps only the main content.

   It scores the DOM by content density -- text length, comma count, tag weight
   and class/id weight, discounted by link density, the well-known readability
   heuristic -- and returns the single highest-scoring container element. It
   shares need_text(), the tag atoms, and the attribute lookup from
   serialize/internal.h. Everything here is pure C: the node bindings wrap the
   returned node (or render its text with th_node_layout_text) under the per-tree
   critical section, so no Python API is touched while the structure is walked. */

#include "serialize/internal.h"

#include "dom/tree.h"
#include "dom/tree_internal.h"

#include <stddef.h>
#include <string.h>

/* A paragraph shorter than this is treated as noise, not content. */
#define READ_MIN_PARAGRAPH_CHARS 25
/* The text-length bonus saturates here (each 100 chars adds a point, up to 3). */
#define READ_CHAR_SCORE_CAP 3
/* Candidate array growth: small initial block, doubled when full. */
#define READ_INITIAL_CANDIDATES 8
/* The visible-text floor for the semantic-container fallback: an <article>/<main>
   with at least this many code points reads as a real body, not a caption or lede.
   Well above a headline yet below any genuine article, so the fallback surfaces a
   div/list-structured body without resurrecting empty landmarks. */
#define READ_FALLBACK_MIN_CHARS 200

/* Boilerplate tags whose subtree never holds main content and whose text is excluded
   from the density counts too, in two groups. The hard group is markup that is never
   prose (scripts, form controls). The landmark group is page furniture stripped on
   the first pass but kept on the retry, since a landmark is sometimes the only place
   the body lives. <form> is deliberately in neither: it stays in the walk so its
   negative tag weight applies when it parents a paragraph. */
static const uint16_t read_hard_skip_tags[] = {
    TH_TAG_SCRIPT,   TH_TAG_STYLE, TH_TAG_NOSCRIPT, TH_TAG_BUTTON,   TH_TAG_SELECT,
    TH_TAG_TEXTAREA, TH_TAG_MENU,  TH_TAG_DIALOG,   TH_TAG_TEMPLATE,
};
static const uint16_t read_landmark_tags[] = {TH_TAG_NAV, TH_TAG_ASIDE, TH_TAG_FOOTER, TH_TAG_HEADER};

/* The text containers that earn a content score from their own text. */
static const uint16_t read_paragraph_tags[] = {TH_TAG_P, TH_TAG_TD, TH_TAG_PRE};

/* class/id substrings that mark an element as content (raise its weight). */
static const char *const read_positive_words[] = {
    "article", "body", "content", "entry", "hentry", "main", "page", "post", "text", "blog", "story",
};

/* class/id substrings that mark an element as boilerplate (lower its weight). */
static const char *const read_negative_words[] = {
    "hidden", "banner", "combx",   "comment", "contact", "foot",    "footer", "masthead", "media",
    "meta",   "promo",  "related", "scroll",  "sidebar", "sponsor", "tags",   "widget",
};

/* class/id substrings that disqualify a whole subtree before it is scored. */
static const char *const read_unlikely_words[] = {
    "banner",   "combx",   "comment", "community", "disqus",  "extra",  "foot",       "header",
    "menu",     "modal",   "nav",     "popup",     "related", "remark", "rss",        "share",
    "shoutbox", "sidebar", "sponsor", "ad-",       "agegate", "pager",  "pagination",
};

/* class/id substrings that rescue an otherwise-unlikely subtree (it is content
   after all). */
static const char *const read_maybe_words[] = {
    "and", "article", "body", "column", "content", "main", "shadow",
};

/* A growable map from a candidate element to its accumulated content score. The
   candidate set is the parents and grandparents of scored paragraphs, so it stays
   small; a linear find-or-insert is simpler to cover than a hash and fast enough
   for a one-shot extraction. */
typedef struct {
    th_node *node;
    double score;
} read_candidate;

typedef struct {
    th_tree *tree;
    read_candidate *candidates;
    Py_ssize_t count;
    Py_ssize_t cap;
    /* Whether the landmark tags are pruned this pass; cleared for the retry that
       rescues a body living inside a <footer>/<header>/<aside>/<nav>. */
    int strip_landmarks;
} read_scorer;

/* The text statistics gathered over a subtree: total code points, comma count
   (each comma marks a clause, a weak signal of prose), and the share that sits
   inside an <a>. */
typedef struct {
    Py_ssize_t chars;
    Py_ssize_t commas;
    Py_ssize_t link_chars;
} read_stats;

static int read_atom_in(uint16_t atom, const uint16_t *set, size_t count) {
    for (size_t index = 0; index < count; index++) {
        if (atom == set[index]) {
            return 1;
        }
    }
    return 0;
}

/* Case-insensitive (ASCII) substring test: is `needle` a substring of the
   `hay_len` code points at `hay`? Letters fold, every other byte compares
   literally, matching the readability class/id regexes. */
static int read_ci_contains(const Py_UCS4 *hay, Py_ssize_t hay_len, const char *needle) {
    Py_ssize_t needle_len = (Py_ssize_t)strlen(needle);
    if (needle_len > hay_len) {
        return 0;
    }
    for (Py_ssize_t start = 0; start + needle_len <= hay_len; start++) {
        Py_ssize_t index = 0;
        while (index < needle_len) {
            Py_UCS4 current = hay[start + index];
            Py_UCS4 folded = lower_ascii(current);
            if (folded != (Py_UCS4)(unsigned char)needle[index]) {
                break;
            }
            index++;
        }
        if (index == needle_len) {
            return 1;
        }
    }
    return 0;
}

/* Does the element's class or id attribute contain any of the keywords? */
static int read_match_keywords(th_tree *tree, th_node *node, const char *const *words, size_t count) {
    static const char *const attributes[] = {"class", "id"};
    static const Py_ssize_t attribute_lens[] = {5, 2};
    for (size_t attr = 0; attr < 2; attr++) {
        Py_ssize_t found = th_node_attr_find(tree, node, attributes[attr], attribute_lens[attr]);
        if (found < 0 || node->attrs[found].value == NULL) {
            continue;
        }
        const Py_UCS4 *value = node->attrs[found].value;
        Py_ssize_t value_len = node->attrs[found].value_len;
        for (size_t word = 0; word < count; word++) {
            if (read_ci_contains(value, value_len, words[word])) {
                return 1;
            }
        }
    }
    return 0;
}

/* The +25 / -25 class-weight readability gives an element for content/boilerplate
   class and id hints. */
static double read_class_weight(th_tree *tree, th_node *node) {
    double weight = 0;
    if (read_match_keywords(tree, node, read_negative_words, sizeof(read_negative_words) / sizeof(char *))) {
        weight -= 25;
    }
    if (read_match_keywords(tree, node, read_positive_words, sizeof(read_positive_words) / sizeof(char *))) {
        weight += 25;
    }
    return weight;
}

/* The structural weight readability seeds a candidate with from its tag. */
static double read_tag_base(uint16_t atom) {
    static const uint16_t plus_five[] = {TH_TAG_DIV};
    static const uint16_t plus_three[] = {TH_TAG_PRE, TH_TAG_TD, TH_TAG_BLOCKQUOTE};
    static const uint16_t minus_three[] = {TH_TAG_ADDRESS, TH_TAG_OL, TH_TAG_UL, TH_TAG_DL,
                                           TH_TAG_DD,      TH_TAG_DT, TH_TAG_LI, TH_TAG_FORM};
    static const uint16_t minus_five[] = {TH_TAG_H1, TH_TAG_H2, TH_TAG_H3, TH_TAG_H4, TH_TAG_H5, TH_TAG_H6, TH_TAG_TH};
    if (read_atom_in(atom, plus_five, sizeof(plus_five) / sizeof(uint16_t))) {
        return 5;
    }
    if (read_atom_in(atom, plus_three, sizeof(plus_three) / sizeof(uint16_t))) {
        return 3;
    }
    if (read_atom_in(atom, minus_three, sizeof(minus_three) / sizeof(uint16_t))) {
        return -3;
    }
    if (read_atom_in(atom, minus_five, sizeof(minus_five) / sizeof(uint16_t))) {
        return -5;
    }
    return 0;
}

/* Whether a tag names a boilerplate subtree to prune: always the hard-skip tags,
   and the landmark tags only while `strip_landmarks` is set. */
static int read_is_skip_tag(uint16_t atom, int strip_landmarks) {
    if (read_atom_in(atom, read_hard_skip_tags, sizeof(read_hard_skip_tags) / sizeof(uint16_t))) {
        return 1;
    }
    return strip_landmarks && read_atom_in(atom, read_landmark_tags, sizeof(read_landmark_tags) / sizeof(uint16_t));
}

/* Whether an element starts a boilerplate subtree the walk should not enter: a
   foreign-namespace root, a boilerplate tag, or an unlikely class/id not rescued
   by a "maybe" hint. The document roots <html>/<body> are never pruned: their
   class is a site-wide state flag (has-localnav, nav-open), not a content verdict,
   so an unlikely substring there must not zero the whole page. */
static int read_should_skip(th_tree *tree, th_node *node, int strip_landmarks) {
    if (node->ns != TH_NS_HTML) {
        return 1;
    }
    if (read_is_skip_tag(node->atom, strip_landmarks)) {
        return 1;
    }
    if (node->atom == TH_TAG_HTML || node->atom == TH_TAG_BODY) {
        return 0;
    }
    if (read_match_keywords(tree, node, read_unlikely_words, sizeof(read_unlikely_words) / sizeof(char *)) &&
        !read_match_keywords(tree, node, read_maybe_words, sizeof(read_maybe_words) / sizeof(char *))) {
        return 1;
    }
    return 0;
}

/* Accumulate the text statistics over node's subtree, excluding boilerplate
   subtrees; text inside an <a> also counts toward link_chars. `strip_landmarks`
   mirrors the walk so a retry that keeps landmarks also counts their text. */
static void read_text_stats(th_tree *tree, th_node *node, int in_anchor, int strip_landmarks, read_stats *stats) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            stats->chars += child->text_len;
            if (in_anchor) {
                stats->link_chars += child->text_len;
            }
            if (child->text_len > 0) {
                const Py_UCS4 *text = need_text(tree, child);
                for (Py_ssize_t index = 0; index < child->text_len; index++) {
                    if (text[index] == ',') {
                        stats->commas++;
                    }
                }
            }
        } else if (child->type == TH_NODE_ELEMENT) {
            if (child->ns != TH_NS_HTML || read_is_skip_tag(child->atom, strip_landmarks)) {
                continue;
            }
            read_text_stats(tree, child, in_anchor || child->atom == TH_TAG_A, strip_landmarks, stats);
        }
    }
}

/* The fraction of a candidate's text that lives inside links; prose scores high
   when little of it is link text. */
static double read_link_density(th_tree *tree, th_node *node, int strip_landmarks) {
    read_stats stats = {0, 0, 0};
    read_text_stats(tree, node, 0, strip_landmarks, &stats);
    if (stats.chars == 0) { /* GCOVR_EXCL_BR_LINE: a scored candidate always holds a >=25-char paragraph */
        return 0;           /* GCOVR_EXCL_LINE: the guard only prevents a division by zero that cannot occur */
    }
    return (double)stats.link_chars / (double)stats.chars;
}

/* Add `delta` to node's candidate score, seeding a fresh entry with its tag and
   class/id weight on first sight. */
static void read_add(read_scorer *scorer, th_node *node, double delta) {
    for (Py_ssize_t index = 0; index < scorer->count; index++) {
        if (scorer->candidates[index].node == node) {
            scorer->candidates[index].score += delta;
            return;
        }
    }
    if (scorer->count == scorer->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(scorer->count + 1), (size_t)scorer->cap, READ_INITIAL_CANDIDATES,
                               sizeof(read_candidate), &cap, &bytes);
        if (!grew) { /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return;  /* GCOVR_EXCL_LINE: size-overflow path drops the candidate */
        }
        read_candidate *resized = PyMem_Realloc(scorer->candidates, bytes);
        if (resized == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;            /* GCOVR_EXCL_LINE: allocation-failure path drops the candidate */
        }
        scorer->candidates = resized;
        scorer->cap = (Py_ssize_t)cap;
    }
    scorer->candidates[scorer->count].node = node;
    scorer->candidates[scorer->count].score = read_tag_base(node->atom) + read_class_weight(scorer->tree, node) + delta;
    scorer->count++;
}

/* Score one paragraph and propagate its contribution to the parent (full) and the
   grandparent (half), the candidates that compete to be the content root. */
static void read_score_paragraph(read_scorer *scorer, th_node *paragraph, th_node *parent, th_node *grandparent) {
    read_stats stats = {0, 0, 0};
    read_text_stats(scorer->tree, paragraph, 0, scorer->strip_landmarks, &stats);
    if (stats.chars < READ_MIN_PARAGRAPH_CHARS) {
        return;
    }
    Py_ssize_t length_bonus = stats.chars / 100;
    if (length_bonus > READ_CHAR_SCORE_CAP) {
        length_bonus = READ_CHAR_SCORE_CAP;
    }
    double contribution = 1.0 + (double)(stats.commas + 1) + (double)length_bonus;
    read_add(scorer, parent, contribution);
    if (grandparent != NULL) {
        read_add(scorer, grandparent, contribution / 2.0);
    }
}

/* Walk node's element children, scoring paragraphs against their parent (node, when
   it is an element) and grandparent (the nearest enclosing element above node). */
static void read_walk(read_scorer *scorer, th_node *node, th_node *grandparent) {
    int node_is_element = node->type == TH_NODE_ELEMENT;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT || read_should_skip(scorer->tree, child, scorer->strip_landmarks)) {
            continue;
        }
        if (node_is_element &&
            read_atom_in(child->atom, read_paragraph_tags, sizeof(read_paragraph_tags) / sizeof(uint16_t))) {
            read_score_paragraph(scorer, child, node, grandparent);
        }
        read_walk(scorer, child, node_is_element ? node : grandparent);
    }
}

/* The candidate with the highest score after each is discounted by its link
   density, or NULL when nothing scores positively. */
static th_node *read_best(read_scorer *scorer) {
    th_node *best = NULL;
    double best_score = 0;
    for (Py_ssize_t index = 0; index < scorer->count; index++) {
        double density = read_link_density(scorer->tree, scorer->candidates[index].node, scorer->strip_landmarks);
        double score = scorer->candidates[index].score * (1.0 - density);
        if (score > best_score) {
            best_score = score;
            best = scorer->candidates[index].node;
        }
    }
    return best;
}

/* The <article>/<main> element under root that holds the most visible text, tracked
   into `pick`. Reuses read_text_stats and discounts by link density so a link farm
   dressed as an <article> stays out. Landmark subtrees stay pruned even here, so the
   fallback never surfaces a <footer>/<nav> body. */
typedef struct {
    th_node *node;
    double effective;
} read_pick;

static void read_scan_containers(read_scorer *scorer, th_node *node, uint16_t wanted, read_pick *pick) {
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT || read_should_skip(scorer->tree, child, 1)) {
            continue;
        }
        if (child->atom == wanted) {
            read_stats stats = {0, 0, 0};
            read_text_stats(scorer->tree, child, 0, 1, &stats);
            if (stats.chars >= READ_FALLBACK_MIN_CHARS) {
                double effective = (double)stats.chars - (double)stats.link_chars;
                if (effective > pick->effective) {
                    pick->effective = effective;
                    pick->node = child;
                }
            }
        }
        read_scan_containers(scorer, child, wanted, pick);
    }
}

/* The explicit content landmark to fall back on when nothing scored: the richest
   <article>, else the richest <main>. HTML semantics make these the body by
   definition, so a div/list-structured article with no scoring <p> still resolves
   instead of extracting to empty. <article> wins ties with <main> as the more
   specific content unit. */
static th_node *read_semantic_fallback(read_scorer *scorer, th_node *root) {
    read_pick pick = {NULL, 0};
    read_scan_containers(scorer, root, TH_TAG_ARTICLE, &pick);
    if (pick.node != NULL) {
        return pick.node;
    }
    read_scan_containers(scorer, root, TH_TAG_MAIN, &pick);
    return pick.node;
}

/* The dominant content element under root by the readability content-density
   heuristic, or NULL when no element scores as content. Pure C; the caller holds
   the per-tree critical section and wraps (or renders) the result afterwards.
   Three passes, each a fallback for a total-loss case: score paragraphs with
   landmarks pruned; retry with landmarks kept when that found nothing; and finally
   surface an explicit <article>/<main> body that carries no scoring paragraph. */
th_node *th_node_main_content(th_tree *tree, th_node *root) {
    read_scorer scorer = {tree, NULL, 0, 0, 1};
    read_walk(&scorer, root, NULL);
    th_node *best = read_best(&scorer);
    if (best == NULL) {
        scorer.count = 0;
        scorer.strip_landmarks = 0;
        read_walk(&scorer, root, NULL);
        best = read_best(&scorer);
    }
    if (best == NULL) {
        best = read_semantic_fallback(&scorer, root);
    }
    PyMem_Free(scorer.candidates);
    return best;
}

/* Copy [text, text+len) into a fresh PyMem buffer with each run of HTML whitespace
   folded to a single space and the ends trimmed; *out_len receives the length.
   Returns NULL (with *out_len 0) when the trimmed value is empty, so a present but
   blank field reads the same as an absent one. */
static Py_UCS4 *read_normalize(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t *out_len) {
    *out_len = 0;
    Py_UCS4 *buffer = PyMem_Malloc((size_t)(len > 0 ? len : 1) * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t written = 0;
    int seen = 0;
    int pending_space = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (is_space(text[index])) {
            pending_space = seen;
            continue;
        }
        if (pending_space) {
            buffer[written++] = ' ';
            pending_space = 0;
        }
        buffer[written++] = text[index];
        seen = 1;
    }
    if (written == 0) {
        PyMem_Free(buffer);
        return NULL;
    }
    *out_len = written;
    return buffer;
}

/* The normalized concatenated text of an element's subtree, or NULL when empty. */
static Py_UCS4 *read_element_text(th_tree *tree, th_node *node, Py_ssize_t *out_len) {
    *out_len = 0;
    Py_ssize_t raw_len = 0;
    Py_UCS4 *raw = th_node_text(tree, node, &raw_len);
    if (raw == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS4 *normalized = read_normalize(raw, raw_len, out_len);
    PyMem_Free(raw);
    return normalized;
}

typedef int (*read_match_fn)(th_tree *tree, th_node *node, const void *ctx);

/* The first HTML element under root (document order, depth first) the predicate
   accepts, or NULL when none matches. */
static th_node *read_find(th_tree *tree, th_node *root, read_match_fn match, const void *ctx) {
    for (th_node *child = root->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (match(tree, child, ctx)) {
            return child;
        }
        th_node *found = read_find(tree, child, match, ctx);
        if (found != NULL) {
            return found;
        }
    }
    return NULL;
}

static int read_is_tag(th_tree *Py_UNUSED(tree), th_node *node, const void *ctx) {
    return node->ns == TH_NS_HTML && node->atom == *(const uint16_t *)ctx;
}

/* Whether node is a <meta> whose name or property attribute equals the key. */
static int read_is_meta(th_tree *tree, th_node *node, const void *ctx) {
    if (node->ns != TH_NS_HTML || node->atom != TH_TAG_META) {
        return 0;
    }
    const char *key = ctx;
    static const char *const names[] = {"property", "name"};
    static const Py_ssize_t name_lens[] = {8, 4};
    for (size_t attr = 0; attr < 2; attr++) {
        Py_ssize_t found = th_node_attr_find(tree, node, names[attr], name_lens[attr]);
        if (found >= 0 && node->attrs[found].value != NULL &&
            ser_value_iequals(node->attrs[found].value, node->attrs[found].value_len, key)) {
            return 1;
        }
    }
    return 0;
}

/* Whether a rel attribute run carries token as one of its whitespace-separated
   values, compared case-insensitively. A valueless rel carries value_len 0 (a NULL
   pointer if hand-built, an empty string if parsed), so the scan yields no tokens. */
static int read_rel_has_token(const Py_UCS4 *value, Py_ssize_t value_len, const char *token) {
    Py_ssize_t index = 0;
    while (index < value_len) {
        while (index < value_len && is_space(value[index])) {
            index++;
        }
        Py_ssize_t start = index;
        while (index < value_len && !is_space(value[index])) {
            index++;
        }
        if (index > start && ser_value_iequals(value + start, index - start, token)) {
            return 1;
        }
    }
    return 0;
}

/* Whether node is an <a> whose rel attribute carries the "author" token. */
static int read_is_author_link(th_tree *tree, th_node *node, const void *Py_UNUSED(ctx)) {
    if (node->ns != TH_NS_HTML || node->atom != TH_TAG_A) {
        return 0;
    }
    Py_ssize_t found = th_node_attr_find(tree, node, "rel", 3);
    if (found < 0) {
        return 0;
    }
    return read_rel_has_token(node->attrs[found].value, node->attrs[found].value_len, "author");
}

/* The normalized content of the first <meta> matching key, or NULL when absent. */
static Py_UCS4 *read_meta_content(th_tree *tree, th_node *root, const char *key, Py_ssize_t *out_len) {
    *out_len = 0;
    th_node *meta = read_find(tree, root, read_is_meta, key);
    if (meta == NULL) {
        return NULL;
    }
    Py_ssize_t content = th_node_attr_find(tree, meta, "content", 7);
    if (content < 0) {
        return NULL;
    }
    return read_normalize(meta->attrs[content].value, meta->attrs[content].value_len, out_len);
}

/* The normalized value of node's named attribute, or NULL when absent or empty. A
   valueless attribute carries value_len 0 (a NULL pointer for a hand-built one, an
   empty string when parsed), both of which read_normalize folds to NULL. */
static Py_UCS4 *read_attr_value(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len,
                                Py_ssize_t *out_len) {
    *out_len = 0;
    Py_ssize_t found = th_node_attr_find(tree, node, name, name_len);
    if (found < 0) {
        return NULL;
    }
    return read_normalize(node->attrs[found].value, node->attrs[found].value_len, out_len);
}

/* Title: the first <h1>'s text, else the og:title meta, else the <title>'s text. */
static Py_UCS4 *read_harvest_title(th_tree *tree, th_node *root, Py_ssize_t *out_len) {
    uint16_t h1_atom = TH_TAG_H1;
    th_node *heading = read_find(tree, root, read_is_tag, &h1_atom);
    if (heading != NULL) {
        Py_UCS4 *text = read_element_text(tree, heading, out_len);
        if (text != NULL) {
            return text;
        }
    }
    Py_UCS4 *og = read_meta_content(tree, root, "og:title", out_len);
    if (og != NULL) {
        return og;
    }
    uint16_t title_atom = TH_TAG_TITLE;
    th_node *title = read_find(tree, root, read_is_tag, &title_atom);
    if (title != NULL) {
        return read_element_text(tree, title, out_len);
    }
    return NULL;
}

/* Byline: a rel=author link's text, else the author meta, else article:author. */
static Py_UCS4 *read_harvest_byline(th_tree *tree, th_node *root, Py_ssize_t *out_len) {
    th_node *author = read_find(tree, root, read_is_author_link, NULL);
    if (author != NULL) {
        Py_UCS4 *text = read_element_text(tree, author, out_len);
        if (text != NULL) {
            return text;
        }
    }
    Py_UCS4 *meta = read_meta_content(tree, root, "author", out_len);
    if (meta != NULL) {
        return meta;
    }
    return read_meta_content(tree, root, "article:author", out_len);
}

/* Date: the first <time>'s datetime (or its text), else article:published_time,
   else a common date meta. */
static Py_UCS4 *read_harvest_date(th_tree *tree, th_node *root, Py_ssize_t *out_len) {
    uint16_t time_atom = TH_TAG_TIME;
    th_node *time_element = read_find(tree, root, read_is_tag, &time_atom);
    if (time_element != NULL) {
        Py_UCS4 *datetime = read_attr_value(tree, time_element, "datetime", 8, out_len);
        if (datetime != NULL) {
            return datetime;
        }
        Py_UCS4 *text = read_element_text(tree, time_element, out_len);
        if (text != NULL) {
            return text;
        }
    }
    Py_UCS4 *published = read_meta_content(tree, root, "article:published_time", out_len);
    if (published != NULL) {
        return published;
    }
    static const char *const date_keys[] = {"date", "pubdate", "dc.date"};
    for (size_t index = 0; index < sizeof(date_keys) / sizeof(char *); index++) {
        Py_UCS4 *meta = read_meta_content(tree, root, date_keys[index], out_len);
        if (meta != NULL) {
            return meta;
        }
    }
    return NULL;
}

/* Description: the og:description meta, else the description meta. */
static Py_UCS4 *read_harvest_description(th_tree *tree, th_node *root, Py_ssize_t *out_len) {
    Py_UCS4 *og = read_meta_content(tree, root, "og:description", out_len);
    if (og != NULL) {
        return og;
    }
    return read_meta_content(tree, root, "description", out_len);
}

/* Lang: the <html> element's lang attribute. */
static Py_UCS4 *read_harvest_lang(th_tree *tree, th_node *root, Py_ssize_t *out_len) {
    uint16_t html_atom = TH_TAG_HTML;
    th_node *html = read_find(tree, root, read_is_tag, &html_atom);
    if (html == NULL) {
        return NULL;
    }
    return read_attr_value(tree, html, "lang", 4, out_len);
}

/* Append the normalized [text, text+len) run to the tags array, growing it as needed.
   A run that normalizes to empty is skipped so a blank keyword reads as no tag. Returns
   -1 only on the excluded allocation-failure path. */
static int read_tags_append(th_article_tag **items, Py_ssize_t *count, Py_ssize_t *cap, const Py_UCS4 *text,
                            Py_ssize_t len) {
    Py_ssize_t norm_len = 0;
    Py_UCS4 *norm = read_normalize(text, len, &norm_len);
    if (norm == NULL) {
        return 0;
    }
    if (*count == *cap) {
        size_t new_cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(*count + 1), (size_t)*cap, 4, sizeof(th_article_tag), &new_cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            PyMem_Free(norm); /* GCOVR_EXCL_LINE: size-overflow path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        th_article_tag *grown = PyMem_Realloc(*items, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(norm); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        *items = grown;
        *cap = (Py_ssize_t)new_cap;
    }
    (*items)[*count].data = norm;
    (*items)[*count].len = norm_len;
    (*count)++;
    return 0;
}

/* Append each comma-separated segment of a keywords content run as its own tag.
   Returns -1 only on the excluded allocation-failure path. */
static int read_tags_split(th_article_tag **items, Py_ssize_t *count, Py_ssize_t *cap, const Py_UCS4 *value,
                           Py_ssize_t value_len) {
    Py_ssize_t start = 0;
    for (Py_ssize_t index = 0; index <= value_len; index++) {
        if (index == value_len || value[index] == (Py_UCS4)',') {
            if (read_tags_append(items, count, cap, value + start, index - start) < 0) { /* GCOVR_EXCL_BR_LINE:
                                                                                        allocation-failure path */
                return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            start = index + 1;
        }
    }
    return 0;
}

/* The first-seen source for each single-valued head field, gathered in one <head> walk
   so the tree is crawled once rather than once per field. Each Py_UCS4 slot owns a
   normalized buffer, NULL until a non-blank source fills it; tags grows as keyword and
   article:tag values arrive. */
typedef struct {
    Py_UCS4 *canonical;
    Py_ssize_t canonical_len;
    Py_UCS4 *og_url;
    Py_ssize_t og_url_len;
    Py_UCS4 *og_site_name;
    Py_ssize_t og_site_name_len;
    Py_UCS4 *app_name;
    Py_ssize_t app_name_len;
    Py_UCS4 *og_image;
    Py_ssize_t og_image_len;
    Py_UCS4 *twitter_image;
    Py_ssize_t twitter_image_len;
    th_article_tag *tags;
    Py_ssize_t tags_count;
    Py_ssize_t tags_cap;
} read_social;

/* The single-valued <meta> keys and where their first non-blank content lands in a
   read_social, addressed by offset so one loop covers them all. */
typedef struct {
    const char *key;
    size_t field_offset;
    size_t len_offset;
} read_social_key;

static const read_social_key read_social_keys[] = {
    {"og:url", offsetof(read_social, og_url), offsetof(read_social, og_url_len)},
    {"og:site_name", offsetof(read_social, og_site_name), offsetof(read_social, og_site_name_len)},
    {"application-name", offsetof(read_social, app_name), offsetof(read_social, app_name_len)},
    {"og:image", offsetof(read_social, og_image), offsetof(read_social, og_image_len)},
    {"twitter:image", offsetof(read_social, twitter_image), offsetof(read_social, twitter_image_len)},
};

/* Fold one <meta>'s content into out: comma-split keywords and each article:tag into
   tags, else the first non-blank content of a single-valued social key into its slot.
   Returns -1 only on the excluded allocation-failure path. */
static int read_social_visit_meta(th_tree *tree, th_node *meta, read_social *out) {
    Py_ssize_t content = th_node_attr_find(tree, meta, "content", 7);
    if (content < 0 || meta->attrs[content].value == NULL) {
        return 0;
    }
    const Py_UCS4 *value = meta->attrs[content].value;
    Py_ssize_t value_len = meta->attrs[content].value_len;
    if (read_is_meta(tree, meta, "keywords")) {
        return read_tags_split(&out->tags, &out->tags_count, &out->tags_cap, value, value_len);
    }
    if (read_is_meta(tree, meta, "article:tag")) {
        return read_tags_append(&out->tags, &out->tags_count, &out->tags_cap, value, value_len);
    }
    for (size_t index = 0; index < sizeof(read_social_keys) / sizeof(read_social_keys[0]); index++) {
        const read_social_key *entry = &read_social_keys[index];
        if (!read_is_meta(tree, meta, entry->key)) {
            continue;
        }
        Py_UCS4 **field = (Py_UCS4 **)((char *)out + entry->field_offset);
        if (*field == NULL) {
            *field = read_normalize(value, value_len, (Py_ssize_t *)((char *)out + entry->len_offset));
        }
        break;
    }
    return 0;
}

/* Walk the <head> subtree, folding every <meta> into out and taking the first
   <link rel=canonical> href. The head is where this metadata lives, so scoping the crawl
   here keeps it independent of body size. Returns -1 only on the excluded
   allocation-failure path. */
static int read_walk_social(th_tree *tree, th_node *root, read_social *out) {
    for (th_node *child = root->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (child->atom == TH_TAG_META) {
            if (read_social_visit_meta(tree, child, out) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
                return -1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        } else if (child->atom == TH_TAG_LINK && out->canonical == NULL) {
            Py_ssize_t rel = th_node_attr_find(tree, child, "rel", 3);
            if (rel >= 0 && read_rel_has_token(child->attrs[rel].value, child->attrs[rel].value_len, "canonical")) {
                out->canonical = read_attr_value(tree, child, "href", 4, &out->canonical_len);
            }
        }
        if (read_walk_social(tree, child, out) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return -1;                                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* Resolve the gathered sources into meta, each field preferring its primary key and
   falling back to the secondary, then hand tags over. Buffers the fallback drops are
   freed. */
static void read_apply_social(read_social *social, th_article_meta *meta) {
    if (social->canonical != NULL) {
        meta->canonical = social->canonical;
        meta->canonical_len = social->canonical_len;
        PyMem_Free(social->og_url);
    } else {
        meta->canonical = social->og_url;
        meta->canonical_len = social->og_url_len;
    }
    if (social->og_site_name != NULL) {
        meta->site_name = social->og_site_name;
        meta->site_name_len = social->og_site_name_len;
        PyMem_Free(social->app_name);
    } else {
        meta->site_name = social->app_name;
        meta->site_name_len = social->app_name_len;
    }
    if (social->og_image != NULL) {
        meta->image = social->og_image;
        meta->image_len = social->og_image_len;
        PyMem_Free(social->twitter_image);
    } else {
        meta->image = social->twitter_image;
        meta->image_len = social->twitter_image_len;
    }
    meta->tags = social->tags;
    meta->tags_count = social->tags_count;
}

/* Harvest canonical, site_name, tags, and image in one crawl of the <head> subtree. A
   page with no head (a fragment or hand-built element) has nothing to harvest. On the
   excluded allocation-failure path every gathered buffer is freed and the fields stay
   absent, the way an empty head reads. */
static void read_harvest_social(th_tree *tree, th_node *root, th_article_meta *meta) {
    uint16_t head_atom = TH_TAG_HEAD;
    th_node *head = read_find(tree, root, read_is_tag, &head_atom);
    if (head == NULL) {
        return;
    }
    read_social social = {0};
    if (read_walk_social(tree, head, &social) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        PyMem_Free(social.canonical);                /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(social.og_url);                   /* GCOVR_EXCL_LINE */
        PyMem_Free(social.og_site_name);             /* GCOVR_EXCL_LINE */
        PyMem_Free(social.app_name);                 /* GCOVR_EXCL_LINE */
        PyMem_Free(social.og_image);                 /* GCOVR_EXCL_LINE */
        PyMem_Free(social.twitter_image);            /* GCOVR_EXCL_LINE */
        for (Py_ssize_t index = 0; index < social.tags_count; index++) { /* GCOVR_EXCL_LINE */
            PyMem_Free(social.tags[index].data);                         /* GCOVR_EXCL_LINE */
        } /* GCOVR_EXCL_LINE */
        PyMem_Free(social.tags); /* GCOVR_EXCL_LINE */
        return;                  /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: llvm-cov flags this fall-through brace of the allocation-failure block */
    read_apply_social(&social, meta);
}

void th_article_metadata(th_tree *tree, th_node *root, th_article_meta *meta) {
    if (root == NULL) {
        /* a node built by hand (Element(...)) owns a tree with no document root, so
           there is no page to harvest; every field stays absent */
        return;
    }
    meta->title = read_harvest_title(tree, root, &meta->title_len);
    meta->byline = read_harvest_byline(tree, root, &meta->byline_len);
    meta->date = read_harvest_date(tree, root, &meta->date_len);
    meta->description = read_harvest_description(tree, root, &meta->description_len);
    meta->lang = read_harvest_lang(tree, root, &meta->lang_len);
    read_harvest_social(tree, root, meta);
}

void th_article_meta_clear(th_article_meta *meta) {
    PyMem_Free(meta->title);
    PyMem_Free(meta->byline);
    PyMem_Free(meta->date);
    PyMem_Free(meta->description);
    PyMem_Free(meta->lang);
    PyMem_Free(meta->canonical);
    PyMem_Free(meta->site_name);
    PyMem_Free(meta->image);
    for (Py_ssize_t index = 0; index < meta->tags_count; index++) {
        PyMem_Free(meta->tags[index].data);
    }
    PyMem_Free(meta->tags);
}
