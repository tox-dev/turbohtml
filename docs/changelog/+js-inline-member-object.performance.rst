``minify_js()`` moves a single-use initializer into the object or computed key of a property access in the next
statement: ``var b=g();return b.slice(1)`` becomes ``return g().slice(1)``.
