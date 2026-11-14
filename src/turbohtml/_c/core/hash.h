/* Hashing of names that arrive in untrusted input.

   The tokenizer's per-tag duplicate-attribute index (tokenizer/statemachine.c), the tree's dynamic
   attribute-name table (dom/tree.c), and the string tables of XPath id() and translate(), xsl:key and
   minify_js are open-addressing tables with linear probing, keyed by text the document author picks.
   Linear probing stays O(1) only while home slots are spread over the table: a hash that can be
   evaluated offline lets a document choose names that land in one run of slots, and every further
   name then walks that run, the quadratic cost the tables exist to remove (CWE-407).

   Each of those tables starts on its own unkeyed hash, which is cheap, and watches the runs it builds.
   Once a run passes TH_FAST_PROBE_LIMIT the table rehashes its entries under th_name_hash (or
   th_key_mix for code points) and stays there. th_name_hash is SipHash-1-3 (Aumasson and Bernstein),
   the keyed hash CPython uses for str and bytes (PEP 456), under a 128-bit key the document cannot
   see, so the slots cannot be predicted from the input.

   A header-only static inline, like core/vec.h, so each table inlines the rounds it runs. */

#ifndef TURBOHTML_CORE_HASH_H
#define TURBOHTML_CORE_HASH_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>
#include <string.h>

#include "core/work_steps.h"

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

/* FNV-1a over the bytes: the hash the name tables start on, since it costs a multiply per byte where
   th_name_hash costs its rounds. FNV-1a is fixed, so a document can still pick names that share one run of
   slots. Each table therefore watches its runs, and once one passes the limit the table rehashes its
   entries under th_name_hash for the rest of its life. Up to that point a probe walks at most the limit,
   and past it the key decides the slots, so a document can buy one bounded rehash and nothing quadratic.

   A table kept at most half full uses TH_FAST_PROBE_LIMIT, and one filled to 3/4 uses TH_FAST_RUN_LIMIT,
   since runs of random names grow longer there. Both sit above the longest run random names reach in all
   but rare large tables, so ordinary documents stay on FNV-1a. */
#define TH_FAST_PROBE_LIMIT 64
#define TH_FAST_RUN_LIMIT (2 * TH_FAST_PROBE_LIMIT)

#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

static inline uint32_t th_fnv1a_bytes(const void *data, Py_ssize_t len) {
    const unsigned char *bytes = data;
    uint32_t hash = 2166136261u;
    for (Py_ssize_t index = 0; index < len; index++) {
        hash = (hash ^ bytes[index]) * 16777619u;
    }
    return hash;
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
