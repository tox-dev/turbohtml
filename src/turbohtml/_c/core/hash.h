/* Keyed hashing of names that arrive in untrusted input.

   The tokenizer's per-tag duplicate-attribute index (tokenizer/statemachine.c) and the tree's dynamic
   attribute-name table (dom/tree.c) are open-addressing tables with linear probing, keyed by names the
   document author picks. Linear probing stays O(1) only while home slots are spread over the table, so
   both hash through the one function here: a hash that can be evaluated offline lets a document choose
   names that land in one run of slots, and every further name then walks that run, the quadratic cost
   the tables exist to remove (CWE-407).

   The function is SipHash-1-3 (Aumasson and Bernstein), the keyed hash CPython itself uses for str and
   bytes (PEP 456). A salt in front of FNV-1a is not enough: two names that collide under one salt
   collide under every salt with the same low byte, so a set of colliding names computed offline still
   lands in one slot in one process out of 256.

   A header-only static inline, like core/vec.h, so each table inlines the rounds it runs. */

#ifndef TURBOHTML_CORE_HASH_H
#define TURBOHTML_CORE_HASH_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>
#include <string.h>

/* The 128-bit key, defined and set once per process in core/module.c. */
extern uint64_t th_name_hash_key[2];

static inline uint64_t th_sip_rotate(uint64_t word, int bits) {
    return (word << bits) | (word >> (64 - bits));
}

static inline void th_sip_round(uint64_t state[4]) {
    state[0] += state[1];
    state[1] = th_sip_rotate(state[1], 13) ^ state[0];
    state[0] = th_sip_rotate(state[0], 32);
    state[2] += state[3];
    state[3] = th_sip_rotate(state[3], 16) ^ state[2];
    state[0] += state[3];
    state[3] = th_sip_rotate(state[3], 21) ^ state[0];
    state[2] += state[1];
    state[1] = th_sip_rotate(state[1], 17) ^ state[2];
    state[2] = th_sip_rotate(state[2], 32);
}

/* SipHash-1-3 of `len` bytes under th_name_hash_key, truncated to the 32 bits the tables index by.
   Whole words are read in native byte order, since the value never leaves the process. Equal byte
   runs hash equal; the caller resolves a collision between distinct names by comparing them. */
static inline uint32_t th_name_hash(const void *data, Py_ssize_t len) {
    const unsigned char *bytes = data;
    uint64_t state[4] = {
        th_name_hash_key[0] ^ UINT64_C(0x736F6D6570736575),
        th_name_hash_key[1] ^ UINT64_C(0x646F72616E646F6D),
        th_name_hash_key[0] ^ UINT64_C(0x6C7967656E657261),
        th_name_hash_key[1] ^ UINT64_C(0x7465646279746573),
    };
    Py_ssize_t whole = len - len % 8;
    for (Py_ssize_t index = 0; index < whole; index += 8) {
        uint64_t word;
        memcpy(&word, bytes + index, sizeof(word));
        state[3] ^= word;
        th_sip_round(state);
        state[0] ^= word;
    }
    uint64_t last = (uint64_t)len << 56; /* the final word carries the length above the leftover bytes */
    for (Py_ssize_t index = whole; index < len; index++) {
        last |= (uint64_t)bytes[index] << (8 * (index - whole));
    }
    state[3] ^= last;
    th_sip_round(state);
    state[0] ^= last;
    state[2] ^= 0xFF;
    th_sip_round(state);
    th_sip_round(state);
    th_sip_round(state);
    return (uint32_t)(state[0] ^ state[1] ^ state[2] ^ state[3]);
}

/* The splitmix64 finalizer of a fixed-width integer key (a code point, a packed trigram) under the same key, for the
   tables probed once per input character, where the rounds of th_name_hash would show. The key hides which integers
   share a slot. */
static inline uint64_t th_key_mix(uint64_t key) {
    uint64_t hash = key ^ th_name_hash_key[0];
    hash = (hash ^ (hash >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
    hash = (hash ^ (hash >> 27)) * UINT64_C(0x94D049BB133111EB);
    return hash ^ (hash >> 31);
}

#endif /* TURBOHTML_CORE_HASH_H */
