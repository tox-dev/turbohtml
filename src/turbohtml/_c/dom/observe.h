/* Synchronous mutation observation (issue #554). The DOM MutationObserver batches
   records and delivers them on a microtask; turbohtml has no event loop, so the
   record queue is drained synchronously through take_records()/deliver().

   These entry points back dom/observe.c. The mutation primitives (dom/mutate.c) and
   the binding layer (dom/element.c) call them to record a change; each is a fast
   no-op when the tree carries no live observer, so the mutation hot path pays a
   single predictable branch. The MutationObserver Python type and the record drain
   live in dom/observe.c behind observe_register (declared in tokenizer/binding.h). */

#ifndef TURBOHTML_DOM_OBSERVE_H
#define TURBOHTML_DOM_OBSERVE_H

#include "dom/tree.h"

/* One tree's observer registry, allocated lazily on the first observe(). Opaque here;
   the layout and the queue-a-mutation-record algorithm stay in dom/observe.c. */
typedef struct th_observer th_observer;

/* Mutation-record kinds, matching the order MutationRecord.type reports. */
enum th_mo_kind { TH_MO_CHILD_LIST = 0, TH_MO_ATTRIBUTES = 1, TH_MO_CHARACTER_DATA = 2 };

/* Record a child linked under parent (call after the link, so previous/next sibling
   read straight off child). A no-op when the tree has no observers. */
void th_mo_child_inserted(th_tree *tree, th_node *parent, th_node *child);

/* Record a child about to be unlinked from parent (call before the unlink, passing
   the siblings captured while child is still linked). */
void th_mo_child_removed(th_tree *tree, th_node *parent, th_node *child, th_node *prev, th_node *next);

/* Record an attribute change on target. old_value is the value the attribute held
   before the change; had_value is 0 when the attribute did not exist (a fresh add),
   so the record reports oldValue None. */
void th_mo_attr_changed(th_tree *tree, th_node *target, uint32_t name_atom, const Py_UCS4 *old_value,
                        Py_ssize_t old_len, int had_value);

/* Record a character-data change on target, old_value being its text before the edit. */
void th_mo_char_data_changed(th_tree *tree, th_node *target, const Py_UCS4 *old_value, Py_ssize_t old_len);

#endif /* TURBOHTML_DOM_OBSERVE_H */
