Matching a CSS selector with a very long chain of combinators raises RecursionError when the thread's stack cannot hold
it, instead of crashing.
