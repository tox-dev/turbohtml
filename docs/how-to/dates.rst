############################
 Extract a publication date
############################

*******************************
 Read the date a page declares
*******************************

Scrapers want one thing from a news or blog page: *when was this published*, the job of ``htmldate.find_date``.
:func:`turbohtml.dates` answers it from the markup the page declares -- ``<meta>`` tags, JSON-LD, ``<time>`` elements,
and the URL -- and returns a ``YYYY-MM-DD`` string:

.. testcode::

    from turbohtml import dates

    html = (
        "<head><meta property='article:published_time' content='2024-05-06T08:00:00Z'>"
        "<meta property='article:modified_time' content='2024-06-18T11:30:00Z'></head>"
    )
    print(dates(html))

.. testoutput::

    2024-06-18

The default returns the *most recent* date, the last modification, exactly as htmldate's ``find_date`` does. It pulls
from JSON-LD ``datePublished``/``dateModified`` and a ``/YYYY/MM/DD/`` date in the canonical or OpenGraph URL too, so a
page that declares its date in any of those places needs no extra configuration.

************************
 Choose what to extract
************************

The knobs live on a :class:`~turbohtml.DateExtraction` config, mirroring htmldate's ``find_date`` arguments. Pass
``DateExtraction.published()`` (htmldate's ``original_date=True``) to prefer the first-published date over later
modifications, set ``output_format`` to re-render the result, and bound the result with ``min_date``/``max_date`` to
reject dates outside the range you trust:

.. testcode::

    from datetime import date

    from turbohtml import DateExtraction, dates

    html = (
        "<head><meta property='article:published_time' content='2024-05-06'>"
        "<meta property='article:modified_time' content='2024-06-18'></head>"
    )
    print(dates(html, DateExtraction.published()))
    print(dates(html, DateExtraction(output_format="%d %B %Y")))
    print(dates(html, DateExtraction(min_date=date(2025, 1, 1))))

.. testoutput::

    2024-05-06
    18 June 2024
    None

``DateExtraction.fast()`` turns off the URL-pattern fallback when you trust only the structured ``<meta>``, JSON-LD, and
``<time>`` signals. A page that declares no valid date -- or whose only date falls outside the window -- yields ``None``,
so branch on the result rather than assuming a date.
