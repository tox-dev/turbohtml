########################
 Phone-number detection
########################

A phone number written in prose has no scheme and no delimiter to announce it. ``650-253-0000`` is a number in a US text
and a serial in a German one; ``2012-01-02 08:00`` is a timestamp that looks like both. Deciding what to link takes the
numbering plans themselves, and :class:`turbohtml.clean.PhoneNumbers` puts them behind one switch on
:class:`~turbohtml.clean.Linkify` and :class:`~turbohtml.clean.LinkDetector`.

*****************************
 Compiled plans, not regexes
*****************************

The plans come from Google's `libphonenumber <https://github.com/google/libphonenumber>`_ metadata, the same data the
``phonenumbers`` package ships. Rather than run its regular expressions at scan time, ``tools/generate_phone.py``
compiles every national number pattern, national prefix rule, international prefix, number format and leading-digits
router into deterministic automata at build time, and writes them into ``phone_table.h``. A scan then walks tables: one
transition per digit, no backtracking, no allocation. The generator checks each automaton against the source expression
before it emits it, refuses any construct it cannot compile exactly, and pins the metadata by tag and SHA-256
(``v9.0.38`` at this release) next to the Unicode 16.0.0 data it draws digit and letter classes from.

The two runtime pieces are small. The link scanner already looks for the bytes that can start a link (``:``, ``@``,
``.``); with phones on, a digit joins that set, and on one-byte text the scanner skips 16 bytes at a time while none of
them appears. The recognizer (``src/turbohtml/_c/clean/phone.c``) then handles one run of digit groups per call and
returns the number, its region and type, or the position the next probe may start at.

*************************
 What counts as a number
*************************

The recognizer follows ``PhoneNumberMatcher`` from libphonenumber, so a text links the way that library would find it. A
run is up to 21 groups of up to 20 digits, joined by the punctuation the library allows between groups, with an optional
``+`` or bracket in front and an extension at the end. The recognizer reads the whole run first; when that fails, it
tries the library's inner splits in its order: after a slash, each bracketed part, around a spaced hyphen, around a wide
hyphen, between dots, between spaces. It reads each candidate the way ``parse`` would with a default region: an
international prefix commits to the country code that follows, the region's own country code may come off, or the
national prefix comes off and the remaining digits go to the plan of each region sharing the calling code, in the
library's routing order. A national number read this way must carry the national prefix its number format writes, so
``2012-01-02 08`` is not a German number while ``030 12345678`` is; ``require_national_prefix=False`` drops that rule
for text where numbers are written the way people dial them locally.

``grouping`` adds libphonenumber's two stricter leniencies. Both start from a valid number and compare the digit groups
the text wrote against the groups its format would write, in the international layout, and against each alternate format
the metadata lists for its calling code (``PhoneNumberAlternateFormats.xml``, pinned next to the plans): ``STRICT``
wants each group to occur in order, ``EXACT`` wants the written groups to be those groups, or the whole national number
unbroken. A candidate with two slashes fails both unless the first slash only sets off the country code. Since every
group in the metadata is a plain digit count, the groups a format writes come from the same greedy split the formatter
uses, so no regular expression runs here either.

The recognizer tries the regions you configure in order; the first reading wins. The ``regions`` tuple is the fallback
for numbers written without ``+``; with an empty tuple only ``+`` numbers link. In ``require_valid`` mode (the default)
a number must be one the plan assigns, with a resolved :class:`~turbohtml.clean.PhoneType`; ``require_valid=False`` is
the library's ``POSSIBLE`` leniency, which checks length only, reports ``UNKNOWN`` as the type, and may leave the region
empty.

***********************************
 Where the library is not followed
***********************************

Four rules are deliberate departures, each chosen for the text a linkifier sees rather than the text a parser is handed:

- A slash date (``3/10/2011``), a timestamp (``2012-01-02 08:00``), an IPv4 address and a labeled identifier (``Order
  12345``, the ``ignore_numbers_after`` words) poison only their own groups. libphonenumber discards the whole run, so
  the number after a date is lost there and kept here.
- A digit run in a payment-card shape that passes the Luhn check is not a number (``skip_card_numbers``). The library
  links it when the groups happen to form a valid number.
- ``require_separators=True`` refuses a bare digit run with no ``+``, separators or international prefix.
- A run that touches ``@`` on either side is never a number, and a URL, email or bare domain the scanner would link on
  its own wins over a number inside it: ``123@example.com`` and ``1password.com`` are what they were before phones were
  on. A ``tel:`` URI already written in the text links as itself.

The conformance suite (``tests/conformance/test_phone_phonenumbers_conformance.py``) runs the pinned ``phonenumbers``
release over every example number the metadata carries, in a dozen written forms and contexts, and fails on any
difference outside these named rules.

********************
 What a match holds
********************

A detected number is a :class:`~turbohtml.clean.PhoneNumber`: the country code, the national significant number with the
leading zeros the plan keeps, the extension digits, the region whose plan assigned it (``"001"`` for a non-geographic
code such as ``+800``) and the type. ``international_number`` is ``+`` followed by the digits with no separators, which
is what the ``tel:`` href carries; an extension follows as ``;ext=`` per RFC 3966, so ``tel:+16502530000;ext=1234``.
``e164`` is the same string when it fits the 15-digit ITU limit and ``None`` otherwise: the metadata declares longer
valid national services (Germany, Indonesia, Japan, Korea, Nigeria and Uruguay), and those link without being E.164
numbers.

Callbacks see the number on ``link.phone``, so a callback can route mobiles to ``sms:`` or drop premium-rate numbers. An
anchor already in the input reaches a callback with ``phone`` set to ``None`` whatever its ``href``; the field describes
detected plain text only.

***********************************
 Reading a string you already hold
***********************************

:meth:`PhoneNumber.parse <turbohtml.clean.PhoneNumber.parse>` is the recognizer pointed at one string, with the rules
that exist for prose switched off: no separators are required, a payment-card shape is not refused, no word before the
digits marks them as an identifier, and the national prefix need not be written. The characters before the first ``+``
or digit are skipped, which is how ``phonenumbers.parse`` treats ``Tel:`` and the ``tel:`` scheme, and after the number
only characters that cannot continue it may follow, so two numbers in one string are an error rather than the first of
them. What remains is the matcher's reading of one candidate, which is why a letter glued to the digits
(``x650-253-0000``) is refused here and accepted by ``phonenumbers``. The conformance suite parses every rendering of
every example number with both and expects the same country code, national number and extension, or a refusal from both.

******************
 Writing a number
******************

:meth:`PhoneNumber.format <turbohtml.clean.PhoneNumber.format>` writes a number the way libphonenumber's
``format_number`` does, in the four layouts of :class:`~turbohtml.clean.PhoneFormat`. The number formats of each calling
code's main region are part of the compiled tables: the leading-digits pattern of each format is an automaton, and its
digit pattern needs none. Every capture group in every format of the metadata, the alternate formats included, is a
plain digit count (``\d{3}``, ``\d{2,11}``), so a format applies when the national number has a length its groups can
sum to, and the split between groups is the one Java's backtracking would pick: each group takes the most digits that
still leave the later groups their minimum. The generator refuses any other pattern shape, so a metadata update that
introduced one would fail the build rather than format wrongly. The templates keep their ``$1 $2`` references; the
NATIONAL template has the national prefix rule already folded into its first group at generation time, and RFC 3966
collapses every separator run into one hyphen at write time.

The tables carry no geocoding, carrier or time-zone data, and no as-you-type formatter: those are lookup sets several
times the size of the numbering plans and a different product from linkifying HTML. ``phonenumbers`` remains the tool
for them, and a :class:`~turbohtml.clean.PhoneNumber` gives it the E.164 string to start from.
