``minify_js()`` lets a nested function reuse the short name of an outer binding it never reads, and gives the shortest
names to the most-read bindings first. Output gets smaller, and minifying it a second time now returns it unchanged in
cases where names used to shift between runs.
