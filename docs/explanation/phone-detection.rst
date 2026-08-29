########################
 Phone-number detection
########################

A phone number written in prose has no scheme and no delimiter. ``650-253-0000`` is a number in a US text and a serial
in a German one; ``2012-01-02 08:00`` is a timestamp that looks like both. Deciding what to link takes the numbering
plans, and :class:`turbohtml.clean.PhoneNumbers` puts them behind one switch on :class:`~turbohtml.clean.Linkify` and
:class:`~turbohtml.clean.LinkDetector`.

****************
 Compiled plans
****************

The plans are the numbering-plan metadata Google publishes in `libphonenumber
<https://github.com/google/libphonenumber>`_, pinned by tag and SHA-256 (``v9.0.38`` at this release) next to the
Unicode 16.0.0 data the digit and letter classes come from. ``tools/generate_phone.py`` compiles each national number
pattern, national prefix rule, international prefix, number format and leading-digits router into a deterministic
automaton at build time and writes them into ``phone_table.h``, so no regular expression runs at scan time. A scan walks
those tables with one transition per digit and neither backtracks nor allocates. The generator checks each automaton
against the source expression before it emits it and refuses any construct it cannot compile to an equivalent automaton.

Two pieces run at scan time. The link scanner looks for the bytes that can start a link (``:``, ``@``, ``.``); with
phones on, the digits are in that set, and on one-byte text the scanner skips 16 bytes at a time while none of them
appears. The recognizer (``src/turbohtml/_c/clean/phone.c``) then handles one run of digit groups per call and returns
the number, its region and type, or the position the next probe may start at.

********************
 The shape of a run
********************

A run is up to 21 groups of up to 20 digits, the last of them starting within 250 code points of the first digit, joined
by the punctuation a written number carries between groups, with an optional ``+`` or bracket in front and an extension
at the end. The recognizer reads the whole run first; when that fails, it tries the inner splits in order: after a
slash, each bracketed part, around a spaced hyphen, around a wide hyphen, between dots, between spaces. For each
candidate and default region, the recognizer takes the country code after an international prefix, strips the region's
own country code when the text carries it, or strips the national prefix and hands the remaining digits to the plan of
each region sharing the calling code, in the plan's routing order. A national number read this way must carry the
national prefix its number format writes, so ``2012-01-02 08`` is not a German number while ``030 12345678`` is;
``require_national_prefix=False`` drops that rule for text where people write numbers the way a local caller dials them.

``grouping`` adds two stricter checks. Both start from a valid number and compare the digit groups as written against
the groups of its number format, in the international layout, and against each alternate format the metadata lists for
its calling code (``PhoneNumberAlternateFormats.xml``, pinned next to the plans): ``STRICT`` requires each group to
occur in order, ``EXACT`` requires the written groups to be those groups, or the whole national number unbroken. A
candidate with two slashes fails both unless the text before the first slash is the country code. Since each group in
the metadata is a plain digit count, a format's groups come from the same greedy split the formatter uses, so no regular
expression runs here either.

The recognizer tries the regions you configure in order and keeps the first reading. The ``regions`` tuple is the
fallback for numbers written without ``+``; with an empty tuple, ``+`` numbers alone link. In ``require_valid`` mode
(the default) a number must be one the plan assigns, with a resolved :class:`~turbohtml.clean.PhoneType`;
``require_valid=False`` checks the length alone, so the type is ``UNKNOWN`` and the region may be ``None``.

*****************
 Rules for prose
*****************

Prose mixes dates and identifiers with phone numbers, so detection adds these rules:

- The groups of a slash date (``3/10/2011``), a timestamp (``2012-01-02 08:00``), an IPv4 address or a labeled
  identifier (``Order 12345``, the ``ignore_numbers_after`` words) are not a number, and the recognizer still reads the
  rest of the run; a label covers the groups joined to it without whitespace, so ``Order 650-253-0000`` is an identifier
  and ``Order 12345, 650-253-0000`` holds a number.
- A digit run in a payment-card shape that passes the Luhn check is not a number (``skip_card_numbers``), and neither is
  an unbroken card of 13 to 19 digits wherever it sits in a run, so the recognizer still finds the phone written after
  one.
- The hextets and port of an IPv6 literal (``2001:db8::8888``, ``[::1]:8080``) are not numbers. This covers well-formed
  literals alone; ``6502530000:6502530000:1`` holds two numbers.
- ``require_separators=True`` refuses a bare digit run with no ``+``, separators or international prefix.
- In prose, letters do not stand for digits: ``1-800-FLOWERS`` is not a number in a text, while :meth:`PhoneNumber.parse
  <turbohtml.clean.PhoneNumber.parse>` reads it.
- A run that touches ``@`` on either side is not a number, and a URL, email or bare domain the scanner links on its own
  takes precedence over a number inside it: ``123@example.com`` and ``1password.com`` link as they do with phones off. A
  ``tel:`` URI already written in the text is one phone link when its payload reads as a number under the same settings,
  scheme and parameters included, with the number's own ``tel:`` URI as the href; ``tel:not-a-number`` stays text.
- The number before a ``/x`` (``650-253-0000 / x12``) ends on its last digit; the text after the slash starts a second
  candidate.

***********************
 The fields of a match
***********************

A detected number is a :class:`~turbohtml.clean.PhoneNumber`: the country code, the national significant number with the
leading zeros the plan keeps, the extension digits, the region whose plan assigned it (``"001"`` for a non-geographic
code such as ``+800``) and the type. ``international_number`` is ``+`` followed by the digits with no separators, which
is what the ``tel:`` href carries; an extension follows as ``;ext=`` per RFC 3966, so ``tel:+16502530000;ext=1234``.
``e164`` is the same string when it fits the 15-digit ITU limit and ``None`` otherwise: the metadata declares longer
valid national services (Germany, Indonesia, Japan, Korea, Nigeria and Uruguay), and since RFC 3966 composes a global
number from E.164, their href is the local form it allows, ``tel:200000000000000;phone-context=+49``.

Callbacks see the number on ``link.phone``, so a callback can route mobiles to ``sms:`` or drop premium-rate numbers. An
anchor already in the input reaches a callback with ``phone`` set to ``None`` whatever its ``href``; the field describes
detected plain text and nothing else.

***********************************
 Reading a string you already hold
***********************************

:meth:`PhoneNumber.parse <turbohtml.clean.PhoneNumber.parse>` is the recognizer pointed at one string, with the rules
for prose switched off: it accepts a bare digit run, a payment-card shape and a number without its national prefix,
ignores the words before the digits, and reads the auto-dialling extension forms (``,,1234``, ``;1234``) alongside the
written ones. ``parse`` starts the number at the first ``+`` or digit, so it skips ``Tel:``, the ``tel:`` scheme and a
letter glued to the digits (``x650-253-0000``); from the end it drops characters that are neither digits, letters nor
``#``, and it cuts off a second number after ``/x``. The rest must be digits, separators and ASCII letters with an
extension at the end, so ``650-253-0000 or 650-253-0001`` is an error rather than the first of them. Three or more
letters spell a vanity number (``1-800-FLOWERS``); ``parse`` drops one or two, so ``650-253-0000 today`` is an error,
since ``today`` spells five more digits. An RFC 3966 local number reads through its ``phone-context``, a calling code
put in front of the digits or a domain under which the digits read as a national number, and ``;isub=`` ends the number.

******************
 Writing a number
******************

:meth:`PhoneNumber.format <turbohtml.clean.PhoneNumber.format>` writes a number in the four layouts of
:class:`~turbohtml.clean.PhoneFormat`. The number formats of each calling code's main region are part of the compiled
tables: the leading-digits pattern of each format is an automaton, and its digit pattern needs none. Each capture group
in each format of the metadata, the alternate formats included, is a plain digit count (``\d{3}``, ``\d{2,11}``), so a
format applies when the national number has a length its groups can sum to, and the split between groups is greedy: each
group takes the most digits that still leave the later groups their minimum. The generator refuses any other pattern
shape, so a metadata update that introduced one would fail the build rather than write a wrong layout. The templates
keep their ``$1 $2`` references; the NATIONAL template has the national prefix rule folded into its first group at
generation time, and ``format`` collapses each separator run into one hyphen for the RFC 3966 layout.

The tables carry no geocoding, carrier or time-zone data, and no as-you-type formatter: those are lookup sets several
times the size of the numbering plans and a different product from linkifying HTML; a
:class:`~turbohtml.clean.PhoneNumber` holds the E.164 string such a tool starts from.
