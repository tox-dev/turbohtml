#ifndef TURBOHTML_CORE_HASH_LIMITS_H
#define TURBOHTML_CORE_HASH_LIMITS_H

/* The probe limits of the name tables in core/hash.h. They sit in a header with no dependencies because the
   standalone CSS minifier reads them, and that build has no interpreter headers for hash.h to include.

   A table kept at most half full uses TH_FAST_PROBE_LIMIT, and one filled to 3/4 uses TH_FAST_RUN_LIMIT,
   since runs of random names grow longer there. Both sit above the longest run random names reach in all
   but rare large tables, so ordinary documents stay on FNV-1a. */
#define TH_FAST_PROBE_LIMIT 64
#define TH_FAST_RUN_LIMIT (2 * TH_FAST_PROBE_LIMIT)

#endif
