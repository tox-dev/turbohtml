##########################
 Finding the main content
##########################

:meth:`~turbohtml.Node.main_content` answers a different question than the exporters: not *how do I render this tree*
but *which part of it is the article*. The field (``readability`` and ``readability-lxml``, Mozilla's
``Readability.js``, and ``resiliparse``'s main-content extractor) converged on a content-density heuristic, and
turbohtml implements the same shape in C over the arena tree, so no Python object is built for a node that loses.

The walk scores *containers* by the prose they hold. Every paragraph-like element (``<p>``, ``<td>``, ``<pre>``) with at
least twenty-five characters of text contributes a base point, one point per comma it contains (commas approximate
clause count, a cheap proxy for real sentences), and up to three points for length, one per hundred characters. That
contribution is added to the paragraph's parent in full and to its grandparent at half weight, because the article body
is usually a container *around* the paragraphs, not the paragraph itself. Each container is also seeded once with a
structural weight from its tag (``<div>`` ``+5``; ``<blockquote>``/``<td>``/``<pre>`` ``+3``; lists and ``<form>``
``-3``; headings and ``<th>`` ``-5``) and a class/id weight (``+25`` for an ``article``/``content``/``post`` hint,
``-25`` for a ``sidebar``/``comment``/``footer`` one), the well-known readability signals.

Two prunings keep boilerplate out of the count. Subtrees that are never content (``<script>``, ``<style>``, ``<nav>``,
``<aside>``, ``<header>``, ``<footer>``, and the like, plus anything in a foreign SVG or MathML namespace) are skipped
wholesale, their text excluded even from a surrounding container's totals. A subtree whose class or id reads as
boilerplate (``comment``, ``modal``, ``share``, ``sidebar``, ``pagination``...) is dropped too, *unless* the same
attribute also carries a rescue hint (``article``, ``content``, ``main``), the case where a single element is tagged
both ways. Finally each surviving candidate's score is discounted by its link density (the fraction of its text that
sits inside an ``<a>``), so a dense menu that slipped through scores near zero, and the highest remaining score wins.
When nothing scores positively (a stub page, pure navigation), there is no winner and the method returns ``None``.

The heuristic has limits worth stating. It is tuned for article-shaped pages; a search-results grid, a forum thread, or
a single-page app rendered entirely from script has no dominant prose container and may return ``None`` or a surprising
node. It selects an existing element unchanged (it does not clean inline boilerplate *within* the winner the way
``Readability.js`` rewrites the DOM), so pair it with :class:`~turbohtml.sanitizer.Sanitizer` when you need a scrubbed
fragment. And it is content extraction only: language detection and WARC/web-archive handling, which ``resiliparse``
bundles alongside, are out of scope; reach for a dedicated tool there.

The whole scoring walk is pure C and allocates only a small candidate array (a linear find-or-insert map, since the
candidate set is just the parents and grandparents of scored paragraphs). It touches no Python object until a winner is
chosen, at which point the binding (holding the same per-tree critical section the other walks use) wraps that one node,
or renders its text for :meth:`~turbohtml.Node.main_text`. Two threads extracting from two trees never interfere.
