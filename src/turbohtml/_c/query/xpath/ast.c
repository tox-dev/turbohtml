/* Builds the flat arena AST nodes and renders a compiled program back as the canonical
   S-expression the parser conformance tests diff against. */

#include "core/common.h"
#include "core/vec.h"
#include "query/xpath/internal.h"
#include "query/xpath/xpath.h"

#include <math.h>
#include <string.h>

/* Append a blank node, returning its index or -1 on OOM. */
int32_t xn_new(xp_program *prog, enum xn_kind kind) {
    if (prog->count == prog->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)prog->cap + 1, (size_t)prog->cap, 16, sizeof(xn), &cap, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        xn *grown = PyMem_Realloc(prog->nodes, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        prog->nodes = grown;
        prog->cap = (int32_t)cap;
    }
    int32_t idx = prog->count++;
    xn *node = &prog->nodes[idx];
    memset(node, 0, sizeof(*node));
    node->kind = (uint8_t)kind;
    node->first = node->second = node->next = -1;
    return idx;
}

typedef struct {
    Py_UCS4 *buf;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} dumper;

static void dput(dumper *out, Py_UCS4 ch) {
    if (out->len == out->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)out->cap + 1, (size_t)out->cap, 64, sizeof(Py_UCS4), &cap, &bytes);
        if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            out->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;          /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        Py_UCS4 *grown = PyMem_Realloc(out->buf, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            out->failed = 1; /* GCOVR_EXCL_LINE */
            return;          /* GCOVR_EXCL_LINE */
        }
        out->buf = grown;
        out->cap = (Py_ssize_t)cap;
    }
    out->buf[out->len++] = ch;
}

static void dputs(dumper *out, const char *text) {
    for (; *text; text++) {
        dput(out, (Py_UCS4)(unsigned char)*text);
    }
}

static void dput_run(dumper *out, const Py_UCS4 *text, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        dput(out, text[index]);
    }
}

static void dput_num(dumper *out, double value) {
    char tmp[64];
    if (value == (double)(long)value && fabs(value) < 1e15) {
        snprintf(tmp, sizeof(tmp), "%ld", (long)value);
    } else {
        snprintf(tmp, sizeof(tmp), "%g", value);
    }
    dputs(out, tmp);
}

static const char *axis_name(uint8_t axis) {
    switch (axis) {
    case AX_CHILD:
        return "child";
    case AX_DESCENDANT:
        return "descendant";
    case AX_DESCENDANT_OR_SELF:
        return "descendant-or-self";
    case AX_ATTRIBUTE:
        return "attribute";
    case AX_SELF:
        return "self";
    case AX_PARENT:
        return "parent";
    case AX_ANCESTOR:
        return "ancestor";
    case AX_ANCESTOR_OR_SELF:
        return "ancestor-or-self";
    case AX_FOLLOWING_SIBLING:
        return "following-sibling";
    case AX_PRECEDING_SIBLING:
        return "preceding-sibling";
    case AX_FOLLOWING:
        return "following";
    case AX_PRECEDING:
        return "preceding";
    default: /* AX_NAMESPACE */
        return "namespace";
    }
}

static void dump_node(dumper *d, const xp_program *prog, int32_t idx);

static void dump_step(dumper *out, const xp_program *prog, int32_t idx) {
    const xn *expr = &prog->nodes[idx];
    dputs(out, "(step ");
    dputs(out, axis_name(expr->axis));
    dput(out, ' ');
    switch (expr->test) {
    case NT_NAME:
        dputs(out, "name '");
        dput_run(out, expr->str, expr->str_len);
        dput(out, '\'');
        break;
    case NT_STAR:
        dputs(out, "*");
        break;
    case NT_NODE:
        dputs(out, "node()");
        break;
    case NT_TEXT:
        dputs(out, "text()");
        break;
    case NT_COMMENT:
        dputs(out, "comment()");
        break;
    default: /* NT_PI */
        dputs(out, "pi(");
        if (expr->str != NULL) {
            dput(out, '\'');
            dput_run(out, expr->str, expr->str_len);
            dput(out, '\'');
        }
        dput(out, ')');
        break;
    }
    for (int32_t pr = expr->first; pr >= 0; pr = prog->nodes[pr].next) {
        dputs(out, " (pred ");
        dump_node(out, prog, prog->nodes[pr].first);
        dput(out, ')');
    }
    dput(out, ')');
}

static void dump_node(dumper *out, const xp_program *prog, int32_t idx) {
    const xn *expr = &prog->nodes[idx];
    static const char *binop[] = {"or", "and", "=", "!=", "<", "<=", ">", ">=", "+", "-", "*", "div", "mod"};
    switch (expr->kind) {
    case XN_OR:
    case XN_AND:
    case XN_EQ:
    case XN_NE:
    case XN_LT:
    case XN_LE:
    case XN_GT:
    case XN_GE:
    case XN_ADD:
    case XN_SUB:
    case XN_MUL:
    case XN_DIV:
    case XN_MOD:
        dput(out, '(');
        dputs(out, binop[expr->kind - XN_OR]);
        dput(out, ' ');
        dump_node(out, prog, expr->first);
        dput(out, ' ');
        dump_node(out, prog, expr->second);
        dput(out, ')');
        break;
    case XN_NEG:
        dputs(out, "(neg ");
        dump_node(out, prog, expr->first);
        dput(out, ')');
        break;
    case XN_UNION:
        dputs(out, "(union ");
        dump_node(out, prog, expr->first);
        dput(out, ' ');
        dump_node(out, prog, expr->second);
        dput(out, ')');
        break;
    case XN_NUM:
        dputs(out, "(num ");
        dput_num(out, expr->num);
        dput(out, ')');
        break;
    case XN_LIT:
        dputs(out, "(lit '");
        dput_run(out, expr->str, expr->str_len);
        dputs(out, "')");
        break;
    case XN_VAR:
        dputs(out, "(var '");
        dput_run(out, expr->str, expr->str_len);
        dputs(out, "')");
        break;
    case XN_FUNC:
        dputs(out, "(call '");
        dput_run(out, expr->str, expr->str_len);
        dput(out, '\'');
        for (int32_t arg = expr->first; arg >= 0; arg = prog->nodes[arg].next) {
            dput(out, ' ');
            dump_node(out, prog, arg);
        }
        dput(out, ')');
        break;
    case XN_FILTER:
        dputs(out, "(filter ");
        dump_node(out, prog, expr->first);
        for (int32_t pr = expr->second; pr >= 0; pr = prog->nodes[pr].next) {
            dputs(out, " (pred ");
            dump_node(out, prog, prog->nodes[pr].first);
            dput(out, ')');
        }
        dput(out, ')');
        break;
    default: /* XN_PATH */
        dputs(out, "(path ");
        dputs(out, expr->absolute ? "abs" : "rel");
        if (expr->second >= 0) {
            dputs(out, " (from ");
            dump_node(out, prog, expr->second);
            dput(out, ')');
        }
        for (int32_t st = expr->first; st >= 0; st = prog->nodes[st].next) {
            dput(out, ' ');
            dump_step(out, prog, st);
        }
        dput(out, ')');
        break;
    }
}

Py_UCS4 *xp_dump(const xp_program *prog, Py_ssize_t *out_len) {
    dumper state = {0};
    dump_node(&state, prog, prog->root);
    if (state.failed) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        PyMem_Free(state.buf); /* GCOVR_EXCL_LINE */
        return NULL;           /* GCOVR_EXCL_LINE */
    }
    if (state.buf == NULL) {                       /* GCOVR_EXCL_BR_LINE: the root always emits at least "()" */
        state.buf = PyMem_Malloc(sizeof(Py_UCS4)); /* GCOVR_EXCL_LINE */
    } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    *out_len = state.len;
    return state.buf;
}
