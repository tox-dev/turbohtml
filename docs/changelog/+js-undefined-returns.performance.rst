``minify_js()`` writes ``return void 0`` as a bare ``return``, which lets a guard such as ``if(a)return void 0;g()``
fold to ``a||g()``. A function that ends in ``return c?x:void 0`` ends in ``if(c)return x`` instead, and
``if(c)return;return x`` merges into a conditional return like any other pair of returns.
