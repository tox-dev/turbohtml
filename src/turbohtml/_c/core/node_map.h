#ifndef TURBOHTML_CORE_NODE_MAP_H
#define TURBOHTML_CORE_NODE_MAP_H

#include <Python.h>
#include <stdint.h>

#include "core/hash.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "dom/tree.h"

typedef struct {
    const struct th_node *node;
    Py_ssize_t value;
} th_node_map_entry;

typedef struct {
    th_node_map_entry *entries;
    size_t capacity;
    size_t count;
} th_node_map;

/* Fibonacci hashing with Linux's GOLDEN_RATIO_32 (2^32 / phi^2): the top bits of the
   product spread any evenly spaced run of keys, such as a document-order subset of node
   sequences, across the table. */
static inline uint32_t th_fibonacci_hash(uint32_t key) {
    return key * 0x61C88647U;
}

/* Reduce a 32-bit hash to a slot of a power-of-two table by keeping its top bits. */
static inline size_t th_hash_slot(uint32_t hash, size_t capacity) {
    return (size_t)(((uint64_t)hash * capacity) >> 32);
}

/* The stride a probe takes once it has walked TH_FAST_PROBE_LIMIT slots one by one. Sequences come from
   the input, so a document can number the nodes it keys into one run of home slots; the keyed mix hides
   where each one strides to from there, and an odd stride reaches every slot of a power-of-two table. */
static inline size_t th_node_probe_step(uint64_t key) {
    return (size_t)th_key_mix(key) | 1;
}

/* The rest of a probe whose home slot and the slot after it are both taken. Out of line and
   holding the walk limit, so the inlined lookup keeps the cost of a plain linear probe. */
static TH_NOINLINE size_t th_node_map_walk(const th_node_map *map, const struct th_node *node, size_t slot) {
    size_t step = 1;
    for (size_t walked = 2; map->entries[slot].node != NULL && map->entries[slot].node != node; walked++) {
        TH_WORK_STEP();
        if (walked == TH_FAST_PROBE_LIMIT) {
            step = th_node_probe_step(node->seq);
        }
        slot = (slot + step) & (map->capacity - 1);
    }
    return slot;
}

/* Hash the node's creation sequence, not its address, so probe counts depend on the
   input document and not on where the allocator placed the tree. */
static inline size_t th_node_map_slot(const th_node_map *map, const struct th_node *node) {
    size_t slot = th_hash_slot(th_fibonacci_hash(node->seq), map->capacity);
    if (map->entries[slot].node != NULL && map->entries[slot].node != node) {
        TH_WORK_STEP();
        slot = (slot + 1) & (map->capacity - 1);
        if (map->entries[slot].node != NULL && map->entries[slot].node != node) {
            slot = th_node_map_walk(map, node, slot);
        }
    }
    return slot;
}

static inline Py_ssize_t th_node_map_find(const th_node_map *map, const struct th_node *node) {
    return map->capacity == 0 ? 0 : map->entries[th_node_map_slot(map, node)].value;
}

static inline int th_node_map_reserve(th_node_map *map, size_t count) {
    if (count > map->capacity / 2) {
        if (count > SIZE_MAX / 2) { /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return -1;              /* GCOVR_EXCL_LINE */
        }
        size_t capacity;
        size_t bytes;
        const int grew = th_grow_cap(count * 2, map->capacity, 16, sizeof(th_node_map_entry), &capacity, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: allocation size overflow */
            return -1; /* GCOVR_EXCL_LINE: allocation size overflow */
        }
        th_node_map grown = {PyMem_Calloc(1, bytes), capacity, map->count};
        if (grown.entries == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;               /* GCOVR_EXCL_LINE: allocation failure */
        }
        for (size_t index = 0; index < map->capacity; index++) {
            if (map->entries[index].node != NULL) {
                grown.entries[th_node_map_slot(&grown, map->entries[index].node)] = map->entries[index];
            }
        }
        PyMem_Free(map->entries);
        *map = grown;
    }
    return 0;
}

/* Values are positive indices; zero marks an absent node. Callers insert each node once. */
static inline int th_node_map_insert(th_node_map *map, const struct th_node *node, Py_ssize_t value) {
    if (th_node_map_reserve(map, map->count + 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                      /* GCOVR_EXCL_LINE */
    }
    map->entries[th_node_map_slot(map, node)] = (th_node_map_entry){node, value};
    map->count++;
    return 0;
}

#endif
