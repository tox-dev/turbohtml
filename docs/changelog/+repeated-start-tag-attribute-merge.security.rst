Merge the attributes of repeated ``<html>`` and ``<body>`` start tags in time and memory linear in the attribute count,
so a page with many such tags can no longer make :func:`turbohtml.parse` take quadratic time or allocate quadratic
memory.
