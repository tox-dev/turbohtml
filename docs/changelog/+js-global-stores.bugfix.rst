``minify_js()`` keeps assignments to top-level bindings. It dropped ``x=2`` from ``var x=1;x=2`` when the script never
read ``x`` again, but a top-level binding is a global that another script on the page can read.
