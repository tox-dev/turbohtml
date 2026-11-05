Stop CSS minification joining a same-query ``@media`` block onto one that ends in stray text, which fused the text into
the next rule's selector and made a second pass change the output.
