Merge the attributes of repeated ``<html>`` and ``<body>`` start tags in memory linear in the attribute count, so a page
with many such tags can no longer make :func:`turbohtml.parse` allocate memory quadratic in that count.
