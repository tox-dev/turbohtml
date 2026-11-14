Read the feed-level Atom ``<author>`` once per feed instead of once per entry that inherits it, so
:func:`~turbohtml.extract.feed` no longer takes time and memory that grow with the product of the entry count and the
size of that ``<author>``.
