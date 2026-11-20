``minify_js()`` turns a guard ``if(c)return;`` at the top of a function body, or ``if(c)continue;`` at the top of a loop
body, into ``if(!c){...}`` around the statements after it, which drops the jump for a pair of braces. It did this only
when every following statement was an expression; it now also does it for loops, ``var`` declarations and other
statements, as long as none declares a ``let``, ``const``, ``class`` or function the block would rescope.
