###################
 From phonenumbers
###################

.. package-meta:: phonenumbers daviddrysdale/python-phonenumbers

`phonenumbers <https://github.com/daviddrysdale/python-phonenumbers>`_ is the Python port of Google's libphonenumber:
``parse``, ``is_valid_number``, ``format_number`` and, for text, ``PhoneNumberMatcher``, which scans a string for the
numbers a default region makes readable. The port carries every numbering plan as Python data and runs the library's
regular expressions over each candidate, so a scan over a page costs milliseconds and the matcher is the slowest piece
of the package.

turbohtml covers the scanning half. :class:`~turbohtml.clean.LinkDetector` with a :class:`~turbohtml.clean.PhoneNumbers`
setting locates numbers the way the matcher does, and :func:`~turbohtml.clean.linkify` rewrites them into ``tel:``
anchors inside HTML, skipping text that is already a link. The build compiles the plans to automata, so the scan is a
table walk in C. Parsing a number you already hold, formatting it for display, geocoding and carrier lookup stay with
``phonenumbers``.

***************************
 turbohtml vs phonenumbers
***************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - phonenumbers
    - - Scope
      - Find numbers in text and rewrite HTML into ``tel:`` anchors
      - Parse, validate, format and find numbers; geocoding, carrier and timezone data
    - - Detection rules
      - libphonenumber's matcher rules, compiled from the same metadata (``v9.0.38``)
      - libphonenumber's matcher rules
    - - Output
      - :class:`~turbohtml.clean.PhoneNumber` with the E.164 form, region and type
      - ``PhoneNumberMatch`` with a ``PhoneNumber`` object
    - - Performance
      - C table walk; see below
      - Pure Python over the library's regular expressions
    - - Typing
      - Fully annotated, ``py.typed``
      - Stub files shipped
    - - Dependencies
      - Single package, C extension bundled
      - Pure Python

Feature overlap
===============

- ``PhoneNumberMatcher(text, region)`` -> :meth:`LinkDetector(phones=PhoneNumbers(regions=(region,))).find(text)
  <turbohtml.clean.LinkDetector.find>`; a ``PhoneNumberMatch`` (``start`` / ``end`` / ``raw_string`` / ``number``) is a
  :class:`~turbohtml.clean.LinkSpan` (``start`` / ``end`` / ``text`` / ``phone``).
- ``Leniency.VALID`` is the default; ``Leniency.POSSIBLE`` is ``PhoneNumbers(require_valid=False)``.
- ``format_number(number, PhoneNumberFormat.E164)`` -> :attr:`PhoneNumber.international_number
  <turbohtml.clean.PhoneNumber.international_number>`; ``number.extension`` -> ``phone.extension``;
  ``region_code_for_number`` -> ``phone.region``; ``number_type`` -> ``phone.type``.
- One matcher takes one default region; ``regions`` takes an ordered tuple, tried in order for numbers written without
  ``+``.

What turbohtml adds
===================

- :func:`~turbohtml.clean.linkify` rewrites HTML, leaving numbers inside an existing ``<a>``, a ``<script>`` or a
  skipped tag alone, and hands each link to the same callbacks URLs and emails go through.
- A number after a date, a timestamp or an identifier label is still found; the matcher discards the whole run.
  Payment-card shapes that pass Luhn, IPv4 addresses and ``Order 12345`` are not numbers.
- A URL, email or bare domain the scanner links on its own always wins over a number inside it.
- Digits of every Unicode ``Nd`` script, at code-point offsets, in one pass over the text.

What phonenumbers has that turbohtml does not
=============================================

- ``parse`` and ``format_number`` for a number you already hold, ``is_valid_number`` on its own, the as-you-type
  formatter, and the geocoder, carrier and timezone data. Keep ``phonenumbers`` for those; a
  :class:`~turbohtml.clean.PhoneNumber` gives you the E.164 string to hand it.
- ``Leniency.STRICT_GROUPING`` and ``EXACT_GROUPING``, which check that the written grouping matches the number's
  format. turbohtml offers the ``VALID`` and ``POSSIBLE`` levels.
- ``parse`` accepts a number written without the national prefix its format requires; the matcher, and turbohtml, do not
  link it.

Performance
===========

.. bench-table::
    :file: bench/phonenumbers.json

Both parties scan the same texts for numbers with the United States as the default region, at the ``VALID`` leniency
unless the row says ``possible``. :meth:`LinkDetector.find <turbohtml.clean.LinkDetector.find>` runs 36x faster than
``PhoneNumberMatcher`` on the mixed corpus (16x at ``POSSIBLE``), 22x to 33x on prose with a few numbers, and over 70x
on the digit-heavy inputs (dates, prices, IPv4 addresses, 21-group digit runs) where the matcher's regular expressions
retry the most. Prose with no digits at all is the narrowest row, 8x to 9x, the cost of the trigger scan alone.
:meth:`~turbohtml.clean.LinkDetector.has_link` stops at the first number and runs 50x faster than
``PhoneNumberMatcher.has_next``. The eight-region rows configure turbohtml with eight fallback regions and give
``phonenumbers`` its first one, so its figure there is the single-region cost.

****************
 How to migrate
****************

.. code-block:: python

    # phonenumbers
    from phonenumbers import PhoneNumberMatcher

    for match in PhoneNumberMatcher("Call 650-253-0000", "US"):
        print(match.start, match.end, match.number.national_number)

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `phonenumbers <https://github.com/daviddrysdale/python-phonenumbers>`__
      - turbohtml
    - - ``PhoneNumberMatcher(text, "US")``
      - :meth:`LinkDetector(phones=PhoneNumbers(regions=("US",))).find(text) <turbohtml.clean.LinkDetector.find>`
    - - ``PhoneNumberMatcher(text, "US", leniency=Leniency.POSSIBLE)``
      - ``PhoneNumbers(regions=("US",), require_valid=False)``
    - - ``match.start`` / ``match.end`` / ``match.raw_string``
      - ``span.start`` / ``span.end`` / ``span.text``
    - - ``format_number(match.number, PhoneNumberFormat.E164)``
      - ``span.phone.international_number``
    - - ``match.number.extension``
      - ``span.phone.extension``
    - - ``region_code_for_number(match.number)``
      - ``span.phone.region``
    - - ``number_type(match.number)``
      - ``span.phone.type``
    - - (rewrite HTML yourself)
      - :func:`~turbohtml.clean.linkify` with ``Linkify(phones=...)``

.. testcode::

    from turbohtml.clean import LinkDetector, Linkify, PhoneNumbers, linkify

    phones = PhoneNumbers(regions=("US",))
    span = LinkDetector(phones=phones).find("Call 650-253-0000")[0]
    print(span.start, span.end, span.phone.international_number, span.phone.type.value)
    print(linkify("Call 650-253-0000", Linkify(phones=phones)))

.. testoutput::

    5 17 +16502530000 fixed_line_or_mobile
    Call <a href="tel:+16502530000">650-253-0000</a>

**********************
 Gotchas and pitfalls
**********************

- ``regions`` is ordered: the first region whose plan reads a prefix-less number wins, so put the region most of your
  text is written for first.
- ``phone.e164`` is ``None`` for the few national services longer than 15 digits; use ``international_number`` for the
  ``tel:`` form and ``e164`` when a downstream system enforces the ITU limit.
- A number split across elements (``<b>650-253</b>-0000``) is not joined; the scanner works on one text node at a time.
- ``PhoneNumberMatcher`` returns nothing after a slash date in the same run; turbohtml links the number, so counts
  differ on texts that mix dates and numbers.
