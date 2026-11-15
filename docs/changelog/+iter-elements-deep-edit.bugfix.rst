Editing a deeply nested tree during :meth:`~turbohtml.Node.iter_elements` no longer takes quadratic time, and an element
moved back under the iterated node is bounded by it again.
