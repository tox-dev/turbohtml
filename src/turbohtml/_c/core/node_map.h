#ifndef TURBOHTML_CORE_NODE_MAP_H
#define TURBOHTML_CORE_NODE_MAP_H

#include <Python.h>
#include <stdint.h>

#include "core/vec.h"

struct th_node;

typedef struct {
    const struct th_node *node;
    Py_ssize_t value;
} th_node_map_entry;

typedef struct {
    th_node_map_entry *entries;
    size_t capacity;
    size_t count;
} th_node_map;

static inline size_t th_node_map_slot(const th_node_map *map, const struct th_node *node) {
    const uintptr_t address = (uintptr_t)node;
    size_t slot = ((address >> 4) ^ (address >> 13)) & (map->capacity - 1);
    while (map->entries[slot].node != NULL && map->entries[slot].node != node) {
        slot = (slot + 1) & (map->capacity - 1);
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
