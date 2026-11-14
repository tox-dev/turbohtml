/* Loop-step counter for the deterministic complexity tests, compiled only by meson's -Dwork_steps=true (TH_WORK_STEPS).
   Outside that macro this header may only define macros, so a production build preprocesses to the same tokens. It
   needs no CPython, so the standalone harnesses can include it. */

#ifndef TURBOHTML_CORE_WORK_STEPS_H
#define TURBOHTML_CORE_WORK_STEPS_H

#ifdef TH_WORK_STEPS
#include <stdint.h>
#if defined(_MSC_VER)
extern __declspec(thread) uint64_t th_work_steps;
#else
extern _Thread_local uint64_t th_work_steps;
#endif
#define TH_WORK_STEPS_ADD(units) ((void)(th_work_steps += (uint64_t)(units)))
#else
#define TH_WORK_STEPS_ADD(units) ((void)0)
#endif

#define TH_WORK_STEP() TH_WORK_STEPS_ADD(1)

#endif /* TURBOHTML_CORE_WORK_STEPS_H */
