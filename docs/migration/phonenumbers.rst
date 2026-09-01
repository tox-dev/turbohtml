###################
 From phonenumbers
###################

.. package-meta:: phonenumbers daviddrysdale/python-phonenumbers

`phonenumbers <https://github.com/daviddrysdale/python-phonenumbers>`_ is the Python port of Google's libphonenumber:
``parse``, ``is_valid_number``, ``format_number`` and, for text, ``PhoneNumberMatcher``, which scans a string for the
numbers a default region makes readable. The port carries every numbering plan as Python data and runs the library's
regular expressions over each candidate, so a scan over a page costs milliseconds and the matcher is the slowest piece
of the package.

turbohtml covers the number handling: :class:`~turbohtml.clean.LinkDetector` with a
:class:`~turbohtml.clean.PhoneNumbers` setting finds numbers in text, with a setting for each of the matcher's
leniencies, :func:`~turbohtml.clean.linkify` rewrites them into ``tel:`` anchors inside HTML, skipping text that is
already a link, :meth:`PhoneNumber.parse <turbohtml.clean.PhoneNumber.parse>` reads a string you already hold and
:meth:`PhoneNumber.format <turbohtml.clean.PhoneNumber.format>` writes a number in the E.164, international, national
and RFC 3966 layouts. The build compiles the plans and number formats to tables, so each of those is a table walk in C.
Geocoding, carrier and time-zone lookup and the as-you-type formatter stay with ``phonenumbers``.

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
      - Find, parse, validate and format numbers; rewrite HTML into ``tel:`` anchors
      - Parse, validate, format and find numbers; geocoding, carrier and timezone data; as-you-type formatting
    - - Detection rules
      - Numbering-plan automata compiled from the same metadata (``v9.0.38``); possible, valid, strict-grouping and
        exact-grouping modes
      - libphonenumber's matcher rules at four leniencies
    - - Output
      - :class:`~turbohtml.clean.PhoneNumber` with the E.164 form, region, type and the four layouts
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
- ``Leniency.VALID`` is the default; ``Leniency.POSSIBLE`` is ``PhoneNumbers(require_valid=False)``;
  ``Leniency.STRICT_GROUPING`` and ``EXACT_GROUPING`` are ``PhoneNumbers(grouping=PhoneGrouping.STRICT)`` and ``EXACT``,
  checked against the same number formats and alternate formats.
- ``parse(text, region)`` -> :meth:`PhoneNumber.parse(text, regions=(region,)) <turbohtml.clean.PhoneNumber.parse>`,
  which raises ``ValueError`` where ``parse`` raises ``NumberParseException`` or ``is_valid_number`` returns ``False``;
  ``is_possible_number`` is ``require_valid=False``.
- ``format_number(number, PhoneNumberFormat.X)`` -> :meth:`phone.format(PhoneFormat.X)
  <turbohtml.clean.PhoneNumber.format>` for ``E164``, ``INTERNATIONAL``, ``NATIONAL`` and ``RFC3966``, with the same
  grouping, national prefix and extension marker; ``number.extension`` -> ``phone.extension``;
  ``region_code_for_number`` -> ``phone.region``; ``number_type`` -> ``phone.type``.
- One matcher takes one default region; ``regions`` takes an ordered tuple, tried in order for numbers written without
  ``+``.

What turbohtml adds
===================

- :func:`~turbohtml.clean.linkify` rewrites HTML, leaving numbers inside an existing ``<a>``, a ``<script>`` or a
  skipped tag alone, and hands each link to the same callbacks URLs and emails go through.
- turbohtml still finds a number after a date, a timestamp or an identifier label, where the matcher discards the whole
  run. Payment-card shapes that pass Luhn, IPv4 and IPv6 addresses and ``Order 12345`` are not numbers.
- A URL, email or bare domain the scanner links on its own takes precedence over a number inside it.
- Digits of every Unicode ``Nd`` script, at code-point offsets, in one pass over the text.

What phonenumbers has that turbohtml does not
=============================================

- The as-you-type formatter, and the geocoder, carrier and timezone data, which are lookup sets several times the size
  of the numbering plans. Keep ``phonenumbers`` for those; a :class:`~turbohtml.clean.PhoneNumber` holds the E.164
  string to hand it.
- ``format_out_of_country_calling_number``, ``format_in_original_format`` and formatting with a carrier code.
- ``parse`` with ``keep_raw_input`` and the country-code source it records; a :class:`~turbohtml.clean.PhoneNumber`
  holds the resolved number only.

Performance
===========

.. bench-table::
    :file: bench/phonenumbers.json

The scanning rows time both parties over the same texts with the United States as the default region, at the ``VALID``
leniency unless the row says ``possible``. The parse rows read twenty held numbers from as many regions, 7x faster than
``parse`` plus ``is_valid_number`` (5x against ``is_possible_number``). The format rows write those twenty numbers in
one layout, 14x to 18x faster than ``format_number`` except for E.164, where both sides join a few strings and the gap
is 2x. :meth:`LinkDetector.find <turbohtml.clean.LinkDetector.find>` runs 36x faster than ``PhoneNumberMatcher`` on the
mixed corpus (16x at ``POSSIBLE``), 22x to 33x on prose with a few numbers, and 54x to 107x on the digit-heavy inputs
(dates, prices, IPv4 addresses, 21-group digit runs) where the matcher's regular expressions retry the most. Prose with
no digits is the narrowest row, 8x to 9x, the cost of the trigger scan alone.
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
    - - ``PhoneNumberMatcher(text, "US", leniency=Leniency.EXACT_GROUPING)``
      - ``PhoneNumbers(regions=("US",), grouping=PhoneGrouping.EXACT)``
    - - ``is_valid_number(parse(text, "US"))``
      - :meth:`PhoneNumber.parse(text, regions=("US",)) <turbohtml.clean.PhoneNumber.parse>` (raises when not)
    - - ``format_number(number, PhoneNumberFormat.NATIONAL)``
      - :meth:`phone.format(PhoneFormat.NATIONAL) <turbohtml.clean.PhoneNumber.format>`
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

    from turbohtml.clean import LinkDetector, Linkify, PhoneFormat, PhoneNumber, PhoneNumbers, linkify

    phones = PhoneNumbers(regions=("US",))
    span = LinkDetector(phones=phones).find("Call 650-253-0000")[0]
    print(span.start, span.end, span.phone.international_number, span.phone.type.value)
    print(linkify("Call 650-253-0000", Linkify(phones=phones)))
    number = PhoneNumber.parse("(650) 253-0000 ext. 12", regions=("US",))
    print(number.format(PhoneFormat.INTERNATIONAL), "|", number.format(PhoneFormat.RFC3966))

.. testoutput::

    5 17 +16502530000 fixed_line_or_mobile
    Call <a href="tel:+16502530000">650-253-0000</a>
    +1 650-253-0000 ext. 12 | tel:+1-650-253-0000;ext=12

**********************
 Gotchas and pitfalls
**********************

- ``regions`` is ordered: the recognizer keeps the first region whose plan reads a prefix-less number, so put the region
  of most of your text first.
- ``phone.e164`` is ``None`` for the few national services longer than 15 digits; their href is RFC 3966's local form
  with a ``phone-context``, ``international_number`` holds the digits, and ``e164`` is the value to hand a system that
  enforces the ITU limit.
- A number split across elements (``<b>650-253</b>-0000``) is not joined; the scanner works on one text node at a time.
- ``PhoneNumberMatcher`` returns nothing after a slash date in the same run; turbohtml links the number, so counts
  differ on texts that mix dates and numbers.
- ``parse`` returns a ``PhoneNumber`` for an invalid number and leaves ``is_valid_number`` to you;
  :meth:`~turbohtml.clean.PhoneNumber.parse` raises for one, since every :class:`~turbohtml.clean.PhoneNumber` is a
  number the tables assign (or, with ``require_valid=False``, one of a possible length).
- A detected number must carry its national prefix; ``PhoneNumbers(require_national_prefix=False)`` links the
  prefix-less numbers ``parse`` accepts.
- ``PhoneNumbers(collapse_whitespace=True)`` reads a run of HTML whitespace as the single space it renders as, which
  links numbers ``PhoneNumberMatcher`` leaves plain: more than four separator characters, and the tab and newline a
  source formatter leaves between two groups. The default matches ``PhoneNumberMatcher`` on both.
