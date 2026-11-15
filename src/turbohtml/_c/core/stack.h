/* The C stack bound of the calling thread, for recursive walks that must stop before they overflow it.

   A level count caps a recursive walk, but the stack a level takes depends on the compiler, and the stack a thread has
   depends on who started it: macOS gives a secondary thread 512 KiB, Windows 1 MiB, musl 128 KiB, and glibc 8 MiB. A
   walk that compares its frame address with th_stack_floor() stops where the stack ends on the thread it runs on.
   Header-only, so the JM_STANDALONE fuzz harnesses, which link no core object, get the same bound. */

#ifndef TURBOHTML_CORE_STACK_H
#define TURBOHTML_CORE_STACK_H

#include <stddef.h>
#include <stdint.h>

#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

#if defined(_MSC_VER)
#include <intrin.h>
#define TH_FRAME_ADDRESS() ((uintptr_t)_AddressOfReturnAddress())
#else
/* the frame address, not a local's: under ASan a local can live on a heap-allocated fake stack */
#define TH_FRAME_ADDRESS() ((uintptr_t)__builtin_frame_address(0))
#endif

#if defined(__linux__) || defined(__APPLE__) || defined(_WIN32)
/* The platforms whose threads report their stack bounds; elsewhere callers fall back to a level count. */
#define TH_STACK_FLOOR 1

#if defined(_WIN32)
/* The kernel32 declaration, spelled out: <windows.h> defines `near` and `far` away, which code that includes this
   header uses as names. */
#if defined(_WIN64)
typedef unsigned __int64 th_ulong_ptr;
#else
typedef unsigned long th_ulong_ptr;
#endif
__declspec(dllimport) void __stdcall GetCurrentThreadStackLimits(th_ulong_ptr *low_limit, th_ulong_ptr *high_limit);
#define TH_STACK_THREAD_LOCAL __declspec(thread)
#else
#include <pthread.h>
#define TH_STACK_THREAD_LOCAL _Thread_local
#endif
#if defined(__linux__) && !defined(__GLIBC__) && !defined(__ANDROID__)
#include <string.h>
#include <sys/auxv.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

/* Stack kept free below the floor for what runs under a checked frame without a check of its own. CPython 3.14 keeps
   32 KiB for the same purpose; this is twice that so these guards answer first where both run. */
#define TH_STACK_MARGIN ((size_t)64 * 1024)

/* The lowest address a checked frame may run at on the calling thread: the low end of its stack plus TH_STACK_MARGIN,
   or plus half the stack when that is less; 1 when the stack has no limit to keep under. A thread's stack never
   moves, and glibc reads /proc/self/maps to answer for the main thread, so each thread asks once. */
static TH_NOINLINE uintptr_t th_stack_floor(void) {
    static TH_STACK_THREAD_LOCAL uintptr_t known;
    if (known != 0) {
        return known;
    }
#if defined(_WIN32)
    th_ulong_ptr low = 0;
    th_ulong_ptr high = 0;
    GetCurrentThreadStackLimits(&low, &high);
    uintptr_t lowest = (uintptr_t)low;
    size_t size = (size_t)(high - low);
#elif defined(__APPLE__)
    pthread_t self = pthread_self();
    size_t size = pthread_get_stacksize_np(self);
    uintptr_t lowest = (uintptr_t)pthread_get_stackaddr_np(self) - size; /* the address is the stack's high end */
#else
    pthread_attr_t attr;
    if (pthread_getattr_np(pthread_self(), &attr) != 0) { /* GCOVR_EXCL_BR_LINE: fails only without /proc or memory */
        return known = 1;                                 /* GCOVR_EXCL_LINE: no frame lies below 1, so no trip */
    }
    void *bottom = NULL;
    size_t size = 0;
    pthread_attr_getstack(&attr, &bottom, &size);
    uintptr_t lowest = (uintptr_t)bottom;
#if !defined(__GLIBC__) && !defined(__ANDROID__)
    size_t guard = 0;
    pthread_attr_getguardsize(&attr, &guard);
#endif
    pthread_attr_destroy(&attr);
#if !defined(__GLIBC__) && !defined(__ANDROID__)
    /* musl keeps no bounds for the process's first thread and reports only the part of its stack mapped so far, which
       the kernel grows on demand until the mapping spans RLIMIT_STACK. That thread is the one with no guard page: a
       thread forked into a child keeps its guard, and the thread id rules out one started on a stack its creator
       supplied. The kernel copies the executable's path to the top of the mapping, above the argument and environment
       strings that musl's answer leaves out, so the page that path ends in closes the mapping. */
    struct rlimit limit;
    if (guard == 0 && getpid() == (pid_t)syscall(SYS_gettid) && getrlimit(RLIMIT_STACK, &limit) == 0) {
        const char *path = (const char *)getauxval(AT_EXECFN);
        uintptr_t page = (uintptr_t)getauxval(AT_PAGESZ);
        uintptr_t end = path == NULL ? lowest + size : ((uintptr_t)path + strlen(path) + page) & ~(page - 1);
        if (limit.rlim_cur == RLIM_INFINITY || limit.rlim_cur >= end) {
            return known = 1; /* no limit to keep under; the callers' level caps still apply */
        }
        lowest = end - limit.rlim_cur;
        size = limit.rlim_cur;
    }
#endif
#endif
    return known = lowest + (size / 2 < TH_STACK_MARGIN ? size / 2 : TH_STACK_MARGIN);
}
#endif

#endif /* TURBOHTML_CORE_STACK_H */
