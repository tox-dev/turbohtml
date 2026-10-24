###########
 Changelog
###########

.. towncrier-draft-entries:: Unreleased

.. towncrier release notes start

.. _release-1-15-3:

**********************
 v1.15.3 (2026-10-19)
**********************

.. _release-1-15-3-fixed:

Fixed
=====

- Preserve XML names and visible content, Markdown whitespace, delimiters, and image URLs, and source text across
  chunked input. Keep CSS minification stable across repeated passes, align sanitizer handling of nested options, and
  limit Markdown output growth for deeply nested inline markup. Raise ``MemoryError`` for allocation failures in DOM,
  CSS, XPath, and extraction APIs. (:pr:`1536`) by :user:`gaborbernat`.
- Keep sanitizer string and node outputs aligned for select and nested options. Preserve nested Markdown emphasis,
  punctuation, and breaks across CommonMark reparses. Preserve CSS margin values when a shorthand falls between
  longhands, and keep ragged Markdown tables as HTML. Report allocation failures during text rendering, element text and
  textarea value assignment, microdata and feed extraction, and encoding detection. Preserve escaped commas and spacing
  before negative dimensions in CSS, and fold valid ``calc()`` expressions with comments in one pass. Bound parsing and
  sanitizing time on deeply nested lists, and avoid rescanning stray CSS preludes without a block. (:pr:`1537`) by
  :user:`gaborbernat`.

.. _release-1-15-2:

**********************
 v1.15.2 (2026-10-15)
**********************

.. _release-1-15-2-fixed:

Fixed
=====

- Reject explicit ports in ``file:`` URLs passed to :func:`~turbohtml.extract.normalize_url`. (:pr:`1395`) by
  :user:`gaborbernat`.
- Reject malformed XML qualified names in element, attribute, and namespace declarations. (:pr:`1396`) by
  :user:`gaborbernat`.
- Restore the empty XML namespace for HTML elements nested under SVG or MathML. (:pr:`1397`) by :user:`gaborbernat`.
- Omit invalid public identifiers from XML output while retaining valid system identifiers. (:pr:`1398`) by
  :user:`gaborbernat`.
- Drop XML-forbidden characters from doctype system identifiers during XML serialization. (:pr:`1399`) by
  :user:`gaborbernat`.
- Omit invalid Unicode attribute names from XML serialization. (:pr:`1400`) by :user:`gaborbernat`.
- Require XML whitespace around doctype external identifiers. (:pr:`1401`) by :user:`gaborbernat`.
- Reject relative namespace URIs during XML canonicalization. (:pr:`1402`) by :user:`gaborbernat`.
- Limit inherited XML attributes in Canonical XML 1.1 output. (:pr:`1403`) by :user:`gaborbernat`.
- Omit invalid processing-instruction targets from XML serialization. (:pr:`1404`) by :user:`gaborbernat`.
- Keep CSS backgrounds with two colors invalid during minification. (:pr:`1405`) by :user:`gaborbernat`.
- Resolve backslashes as separators in relative web URLs. (:pr:`1406`) by :user:`gaborbernat`.
- Keep CSS borders with two widths invalid during minification. (:pr:`1407`) by :user:`gaborbernat`.
- Keep required spaces around math operators in CSS filters. (:pr:`1408`) by :user:`gaborbernat`.
- Keep CSS borders with two colors invalid during minification. (:pr:`1409`) by :user:`gaborbernat`.
- Normalize ``file:`` paths and web URLs with omitted authority slashes; reject hosts that map to an empty domain.
  (:pr:`1412`) by :user:`gaborbernat`.
- Shorten constant CSS filter calculations, such as ``blur(calc(1px + 2px))`` to ``blur(3px)``. (:pr:`1414`) by
  :user:`gaborbernat`.
- Resolve links beginning with backslashes against HTTP(S) document URLs the way browsers do. (:pr:`1416`) by
  :user:`gaborbernat`.
- Resolve links with extra leading slashes to their target host, matching browser URL handling. (:pr:`1417`) by
  :user:`gaborbernat`.
- Resolve HTTP(S) links and document base URLs with backslash authority separators without adding a path slash.
  (:pr:`1418`) by :user:`gaborbernat`.
- Remove literal `.` and `..` path segments from absolute document base URLs. (:pr:`1419`) by :user:`gaborbernat`.
- Percent-encode brackets in URL userinfo when resolving document base URLs. (:pr:`1420`) by :user:`gaborbernat`.
- Resolve document base URLs with canonical hosts, file paths, and relative non-special schemes; reject malformed web
  hosts when cleaning URLs. (:pr:`1421`) by :user:`gaborbernat`.
- Raise ``MemoryError`` when XPath or XSLT runs out of memory instead of reporting malformed input or returning a
  result. (:pr:`1426`) by :user:`gaborbernat`.
- Keep CSS shorthand merges on one side of nested rules, and drop fallback fragments when resolving empty HTML base
  URLs. (:pr:`1434`) by :user:`gaborbernat`.
- Preserve invalid background declarations and both background-size components during CSS minification. (:pr:`1435`) by
  :user:`gaborbernat`.
- Preserve token boundaries in CSS filter values, including before variable functions and inside unresolved functions.
  (:pr:`1436`) by :user:`gaborbernat`.
- Reject qualified XML element and attribute names whose local part starts with an invalid character, and namespace
  declarations whose prefix starts with one. (:pr:`1437`) by :user:`gaborbernat`.
- Treat a backslash after a web URL's authority as one path separator and normalize following dot segments when
  resolving document URLs. (:pr:`1438`) by :user:`gaborbernat`.
- Resolve literal and encoded parent segments in special URL references that omit the authority slashes, including
  same-scheme encoded dots and backslash path separators. (:pr:`1439`) by :user:`gaborbernat`.
- Resolve single-slash and single-backslash special URL references before removing path segments. (:pr:`1440`) by
  :user:`gaborbernat`.
- Use the document fallback URL when a base element's href cannot be parsed, including missing or malformed hosts.
  (:pr:`1441`) by :user:`gaborbernat`.
- Resolve or reject special URL hosts before shortening paths after extra authority slashes. (:pr:`1442`) by
  :user:`gaborbernat`.
- Resolve a same-scheme special URL ending in a backslash to the host root instead of adding a second slash.
  (:pr:`1446`) by :user:`gaborbernat`.
- Reject extra host text after a bracketed IPv6 address. (:pr:`1447`) by :user:`gaborbernat`.
- Reject URL ports containing characters outside 0–9 or values above 65535 during normalization and cleaning.
  (:pr:`1448`) by :user:`gaborbernat`.
- Use the document URL when a relative base reference cannot resolve against an opaque-path URL. (:pr:`1449`) by
  :user:`gaborbernat`.
- Preserve a single-dot host when resolving a foreign special URL with one slash after its scheme. (:pr:`1450`) by
  :user:`gaborbernat`.
- Drop the fallback URL's fragment when resolving a reference that contains only the same special scheme. (:pr:`1451`)
  by :user:`gaborbernat`.
- Omit ancestor ``xml:*`` attributes when exclusively canonicalizing XML or HTML subtrees. (:pr:`1452`) by
  :user:`gaborbernat`.
- Resolve ``xml:base`` values across omitted ancestors when Canonical XML 1.1 serializes a subtree. (:pr:`1453`) by
  :user:`gaborbernat`.
- Escape CSS punctuation and leading digits in tag names and ID anchors returned by ``Element.css_path()`` so the path
  selects the element in HTML and XML trees. (:pr:`1454`) by :user:`gaborbernat`.
- Validate XML Schema name datatypes against the XML 1.0 character ranges instead of accepting every non-ASCII
  character. (:pr:`1455`) by :user:`gaborbernat`.
- Match XML Schema regex ``\w`` and ``\W`` using Unicode general categories instead of treating every non-ASCII
  character as a word character. (:pr:`1456`) by :user:`gaborbernat`.
- Match XML Schema regex name escapes ``\i``, ``\I``, ``\c``, and ``\C`` against the XML 1.0 name character ranges.
  (:pr:`1457`) by :user:`gaborbernat`.
- Match XML Schema regex ``\d`` and ``\D`` against Unicode decimal digits rather than ASCII digits alone. (:pr:`1458`)
  by :user:`gaborbernat`.
- Apply XML Schema regex character class subtraction, including nested and negated classes. (:pr:`1459`) by
  :user:`gaborbernat`.
- Match XML Schema regex Unicode general category escapes, including complements and character classes. (:pr:`1460`) by
  :user:`gaborbernat`.
- Match XML Schema regex Unicode block escapes, including complements and character classes. (:pr:`1461`) by
  :user:`gaborbernat`.
- Reject reversed XML Schema regex character ranges and multi-character range endpoints. (:pr:`1462`) by
  :user:`gaborbernat`.
- Reject malformed XML Schema regex character classes instead of compiling them with altered matching behavior.
  (:pr:`1463`) by :user:`gaborbernat`.
- Reject unknown and incomplete XML Schema regex escapes instead of treating them as literals. (:pr:`1464`) by
  :user:`gaborbernat`.
- Reject unmatched XML Schema regex parentheses instead of accepting partial patterns. (:pr:`1465`) by
  :user:`gaborbernat`.
- Reject malformed and misplaced XML Schema regex quantifiers, including unescaped braces. (:pr:`1466`) by
  :user:`gaborbernat`.
- Reject unescaped closing brackets outside XML Schema regex character classes. (:pr:`1467`) by :user:`gaborbernat`.
- Keep repeated CSS longhands when merging a shorthand would change which value wins. (:pr:`1468`) by
  :user:`gaborbernat`.
- Resolve CSS declaration names without ASCII case differences, while preserving custom-property case and ``!important``
  precedence over later normal declarations. (:pr:`1469`) by :user:`gaborbernat`.
- Keep commas inside `srcset` and `imagesrcset` URLs when finding, rewriting, and checking image candidates.
  (:pr:`1470`) by :user:`gaborbernat`.
- Resolve feed links against nested `xml:base` values in XML feeds, including Atom and RSS links. (:pr:`1471`) by
  :user:`gaborbernat`.
- Resolve CSS-wide keywords across every supported longhand of ``border``, ``border-*``, and ``outline``. Apply ``all``
  to supported properties except ``direction``, while preserving cascade order and importance. (:pr:`1472`) by
  :user:`gaborbernat`.
- Recognize CSS-escaped property names in declarations and computed styles while retaining their source spelling.
  (:pr:`1473`) by :user:`gaborbernat`.
- Extract JSON-LD scripts whose media type includes a ``profile`` or other parameter. (:pr:`1474`) by
  :user:`gaborbernat`.
- Honor rowspans above 1000 rows, up to the HTML table model's limit of 65534. (:pr:`1475`) by :user:`gaborbernat`.
- Ignore URL-like text inside CSS comments and strings when extracting or rewriting links. (:pr:`1476`) by
  :user:`gaborbernat`.
- Recognize RSS Content and Dublin Core fields by XML namespace URI when feeds use nonstandard prefixes. (:pr:`1477`) by
  :user:`gaborbernat`.
- Recognize RDF feed roots and item IDs by XML namespace URI when feeds use alternate prefixes. (:pr:`1478`) by
  :user:`gaborbernat`.
- Recognize Dublin Core and RSS content fields declared with a default XML namespace. (:pr:`1480`) by
  :user:`gaborbernat`.
- Read RSS 1.0 channel and item fields when the RSS namespace uses a prefix. (:pr:`1481`) by :user:`gaborbernat`.
- Read Atom feed and entry fields when the Atom namespace uses a prefix. (:pr:`1482`) by :user:`gaborbernat`.
- Preserve markup inside Atom ``type="xhtml"`` text constructs when extracting feeds. (:pr:`1483`) by
  :user:`gaborbernat`.
- Ignore Atom and RDF fields bound to a foreign default namespace when extracting feeds. (:pr:`1485`) by
  :user:`gaborbernat`.
- Preserve child XML markup in Atom content with an XML media type. (:pr:`1486`) by :user:`gaborbernat`.
- Keep inherited namespace bindings when serializing an XML subtree. (:pr:`1487`) by :user:`gaborbernat`.
- Decode base64 Atom content with a binary media type to bytes. (:pr:`1488`) by :user:`gaborbernat`.
- Order RSS 1.0 entries by the channel's RDF sequence when present. (:pr:`1489`) by :user:`gaborbernat`.
- Preserve explicit empty query and fragment markers when normalizing URLs. (:pr:`1490`) by :user:`gaborbernat`.
- Select Atom alternate links case-sensitively for feed and entry permalinks, and resolve empty ``href`` references
  against ``xml:base``. (:pr:`1491`) by :user:`gaborbernat`.
- Resolve relative RDFa vocabularies and empty resource references against the document base with ``base_url``.
  (:pr:`1492`) by :user:`gaborbernat`.
- Keep CSS rules in cascade order when their selectors can match the same element or a nested descendant. (:pr:`1493`)
  by :user:`gaborbernat`.
- Preserve valid CSS longhands when a later shorthand has an invalid value. (:pr:`1495`) by :user:`gaborbernat`.
- Inherit an Atom entry's author from its source or containing feed when the entry has no author. (:pr:`1496`) by
  :user:`gaborbernat`.
- Resolve an RSS 1.0 item's ``rdf:about`` against ``xml:base`` when populating its ID. (:pr:`1497`) by
  :user:`gaborbernat`.
- Preserve valid CSS longhands when another longhand cannot form a valid shorthand. (:pr:`1499`) by :user:`gaborbernat`.
- Expand RDFa CURIE prefixes without regard to case, including prefixes declared with ``@prefix``. (:pr:`1500`) by
  :user:`gaborbernat`.
- Ignore longhand declarations that mix CSS-wide keywords with other value tokens, preserving earlier valid values.
  (:pr:`1501`) by :user:`gaborbernat`.
- Use Dublin Core ``dc:title`` as an RSS or RDF feed or item title when its core ``title`` is absent. (:pr:`1502`) by
  :user:`gaborbernat`.
- Preserve every rule in top-level CSS @starting-style blocks. (:pr:`1503`) by :user:`gaborbernat`.
- Prefer the first repeated Open Graph value, following the Open Graph protocol's array ordering. (:pr:`1504`) by
  :user:`gaborbernat`.
- Preserve case-sensitive aliases and every block in CSS @font-feature-values. (:pr:`1505`) by :user:`gaborbernat`.
- Ignore foreign XML namespaces on RSS 2.0 roots, channels, items, and core fields. (:pr:`1506`) by :user:`gaborbernat`.
- Resolve the full RDFa 1.1 initial-context prefixes and terms in RDFa items. (:pr:`1507`) by :user:`gaborbernat`.
- Decode escaped CSS-wide keywords in computed styles and reject declarations that mix them with other tokens.
  (:pr:`1508`) by :user:`gaborbernat`.
- Preserve case-sensitive CSS selector names in XML and namespace-qualified stylesheets. (:pr:`1509`) by
  :user:`gaborbernat`.
- Respect the feed's default namespace when inheriting an Atom author. (:pr:`1510`) by :user:`gaborbernat`.
- Preserve RGB channels in fully transparent hexadecimal CSS colors. (:pr:`1511`) by :user:`gaborbernat`.
- Ignore non-CSS ``<style>`` blocks when resolving computed styles, with case-sensitive type matching for SVG.
  (:pr:`1512`) by :user:`gaborbernat`.
- Match Atom element names case-sensitively in XML feeds. (:pr:`1513`) by :user:`gaborbernat`.
- Expand RDFa ``:reference`` CURIEs with the fixed default prefix, ignoring attempts to redefine it. (:pr:`1514`) by
  :user:`gaborbernat`.
- Keep fractional RGB percentages precise and handle large channels without invalid output. (:pr:`1515`) by
  :user:`gaborbernat`.
- Keep the streaming tokenizer memory check stable on free-threaded Python. (:pr:`1516`) by :user:`gaborbernat`.
- Expand RDFa CURIE and SafeCURIE values in ``about`` and ``resource`` attributes. (:pr:`1517`) by :user:`gaborbernat`.
- Keep CSS calculation units through multiplication by zero so angle and time declarations remain valid. (:pr:`1518`) by
  :user:`gaborbernat`.
- Require an RSS, Atom, or RDF feed to be the XML document root; keep embedded feed discovery for HTML. (:pr:`1520`) by
  :user:`gaborbernat`.
- Resolve RDFa CURIEs built from relative ``prefix`` IRIs against the document base. (:pr:`1521`) by
  :user:`gaborbernat`.
- Preserve signed zero inside nested CSS math expressions. (:pr:`1522`) by :user:`gaborbernat`.
- Require the Atom namespace on XML ``<feed>`` roots, including when no default namespace is declared. HTML feed
  discovery remains unchanged. (:pr:`1524`) by :user:`gaborbernat`.
- Preserve large HSL hues during CSS minification. (:pr:`1525`) by :user:`gaborbernat`.
- Require ``version="2.0"`` on XML RSS 2.0 roots. HTML feed discovery remains unchanged. Fold unitless zero in nested
  CSS math without dropping units from typed zero. (:pr:`1526`) by :user:`gaborbernat`.
- Preserve HSL colors with saturation above 100% when minifying CSS. (:pr:`1527`) by :user:`gaborbernat`.
- Match RSS 2.0 and RSS 1.0 element names case-sensitively in XML feeds. (:pr:`1528`) by :user:`gaborbernat`.
- Ignore malformed RDFa terms and bracketed SafeCURIEs in ``property`` and ``typeof``. (:pr:`1529`) by
  :user:`gaborbernat`.

.. _release-1-15-2-packaging:

Packaging
=========

- Allow GCC 16 to build CSS path escaping with ``-Werror``. (:pr:`1494`) by :user:`gaborbernat`.
- Allow the dev environment to resolve Sphinx and Docutils together. (:pr:`1519`) by :user:`gaborbernat`.

.. _release-1-15-1:

**********************
 v1.15.1 (2026-10-12)
**********************

.. _release-1-15-1-fixed:

Fixed
=====

- Close unclosed CSS blocks at EOF so repeated minification keeps the same output. (:pr:`1285`) by :user:`gaborbernat`.
- Keep CSS zero-length dimensions followed by negative numbers stable across repeated minification. (:pr:`1286`) by
  :user:`gaborbernat`.
- Preserve invalid CSS Unicode ranges so minification cannot turn them into valid font ranges. (:pr:`1287`) by
  :user:`gaborbernat`.
- Keep empty ``<h1>`` and ``<h2>`` elements when exporting Markdown with setext headings. (:pr:`1288`) by
  :user:`gaborbernat`.
- Escape a literal ``!`` before a Markdown link so CommonMark preserves the link instead of treating it as an image.
  (:pr:`1289`) by :user:`gaborbernat`.
- Preserve zero-length units in CSS declarations when dropping them would change the value type or make an invalid
  declaration valid. (:pr:`1290`) by :user:`gaborbernat`.
- Preserve emphasis around nonbreaking and other Unicode spaces in Markdown output. (:pr:`1291`) by :user:`gaborbernat`.
- Preserve earlier CSS background longhands when a later shorthand has an invalid bare identifier. (:pr:`1292`) by
  :user:`gaborbernat`.
- Keep invalid font shorthand dimensions invalid during CSS minification. (:pr:`1293`) by :user:`gaborbernat`.
- Preserve hash tokens in CSS properties that do not accept colors. (:pr:`1294`) by :user:`gaborbernat`.
- Preserve invalid integer syntax in CSS order declarations during minification. (:pr:`1295`) by :user:`gaborbernat`.
- Preserve nested emphasis when adjacent closing delimiters would change the rendered Markdown. (:pr:`1296`) by
  :user:`gaborbernat`.
- Preserve ordered-list numbering when HTML ``reversed`` or ``value`` attributes cannot be represented in Markdown.
  (:pr:`1297`) by :user:`gaborbernat`.
- Preserve checked task-list items when serializing HTML to Markdown. (:pr:`1298`) by :user:`gaborbernat`.
- Preserve ``colspan`` and ``rowspan`` in Markdown tables by serializing those tables as HTML. (:pr:`1299`) by
  :user:`gaborbernat`.
- Drop element wrappers with invalid XML names while retaining their children during XML serialization. (:pr:`1300`) by
  :user:`gaborbernat`.
- Preserve CSS exponent and unit boundaries during minification. (:pr:`1301`) by :user:`gaborbernat`.
- Preserve numeric types when folding zero-valued CSS calc() expressions. (:pr:`1302`) by :user:`gaborbernat`.
- Keep ``calc()`` when folding a negative value for a CSS property that clamps it at computed-value time. (:pr:`1303`)
  by :user:`gaborbernat`.
- Keep valid CSS rules active when another rule with the same declarations has an invalid selector. (:pr:`1304`) by
  :user:`gaborbernat`.
- Preserve emphasis next to strikethrough when punctuation would change how Markdown delimiters bind. (:pr:`1307`) by
  :user:`gaborbernat`.
- Keep invalid background-position orders invalid during CSS minification. (:pr:`1308`) by :user:`gaborbernat`.
- Preserve paragraphs whose ordered-list markers cross inline element boundaries in Markdown output. (:pr:`1309`) by
  :user:`gaborbernat`.
- Preserve invalid border-color declarations with more than four values. (:pr:`1310`) by :user:`gaborbernat`.
- Percent-encode carets in URL paths. (:pr:`1311`) by :user:`gaborbernat`.
- Add root paths to hosted FTP and WebSocket URLs. (:pr:`1312`) by :user:`gaborbernat`.
- Preserve invalid border-color declarations containing commas or slashes. (:pr:`1313`) by :user:`gaborbernat`.
- Keep repeated CSS-wide keywords invalid in border-color declarations. (:pr:`1314`) by :user:`gaborbernat`.
- Reject a line break after ``throw`` in JavaScript minification. (:pr:`1315`) by :user:`gaborbernat`.
- Keep repeated CSS-wide keywords invalid in box shorthands. (:pr:`1316`) by :user:`gaborbernat`.
- Use the document fallback URL for data: and javascript: base elements. (:pr:`1317`) by :user:`gaborbernat`.
- Percent-encode usernames and passwords when normalizing URLs. (:pr:`1318`) by :user:`gaborbernat`.
- Keep apostrophes in non-special URL queries when normalizing URLs. (:pr:`1319`) by :user:`gaborbernat`.
- Collapse dot segments in hostless hierarchical URL paths. (:pr:`1320`) by :user:`gaborbernat`.
- Preserve hostless non-special URL paths without adding an empty authority. (:pr:`1321`) by :user:`gaborbernat`.
- Preserve invalid CSS ``outline`` declarations that use the retired ``invert`` color. (:pr:`1322`) by
  :user:`gaborbernat`.
- Keep overlong CSS ``gap``, ``overflow``, and ``overscroll-behavior`` values invalid. (:pr:`1323`) by
  :user:`gaborbernat`.
- Trim trailing C0 controls from URLs before normalization. (:pr:`1324`) by :user:`gaborbernat`.
- Keep invalid CSS ``font`` declarations with repeated explicit weights from becoming active after minification.
  (:pr:`1325`) by :user:`gaborbernat`.
- Keep invalid CSS ``font`` values with excess pre-size options invalid during minification. (:pr:`1326`) by
  :user:`gaborbernat`.
- Parse bracketed URL hosts correctly after userinfo. (:pr:`1327`) by :user:`gaborbernat`.
- Resolve percent-encoded dot segments in URL paths. (:pr:`1328`) by :user:`gaborbernat`.
- Preserve printable ASCII in opaque URL paths during normalization. (:pr:`1329`) by :user:`gaborbernat`.
- Preserve invalid zero dimensions in CSS flex bases, background positions, and shadows. (:pr:`1330`) by
  :user:`gaborbernat`.
- Preserve descending CSS Unicode ranges so invalid font descriptors stay ignored. (:pr:`1331`) by :user:`gaborbernat`.
- Canonicalize dotted IPv4 tails inside IPv6 URL hosts. (:pr:`1332`) by :user:`gaborbernat`.
- Preserve fractional and large CSS background-position offsets measured from an edge. (:pr:`1333`) by
  :user:`gaborbernat`.
- Preserve malformed CSS Unicode-range lists instead of activating ignored font descriptors. (:pr:`1334`) by
  :user:`gaborbernat`.
- Normalize ``localhost`` file URL hosts to empty. (:pr:`1335`) by :user:`gaborbernat`.
- Reject malformed IPv6 URL hosts. (:pr:`1336`) by :user:`gaborbernat`.
- Normalize Windows drive separators in file URL paths. (:pr:`1337`) by :user:`gaborbernat`.
- Preserve angle hue units and invalid dimensions in CSS color functions during minification. (:pr:`1338`) by
  :user:`gaborbernat`.
- Preserve invalid CSS text-decoration shorthands when removing initial values would make them valid. (:pr:`1339`) by
  :user:`gaborbernat`.
- Preserve in-scope namespace declarations and namespace URI attribute order when canonicalizing parsed XML.
  (:pr:`1340`) by :user:`gaborbernat`.
- Preserve invalid CSS ``text-emphasis`` shorthands and defaults before ``var()`` substitutions. (:pr:`1341`) by
  :user:`gaborbernat`.
- Honor ``__html__`` on values returned by ``__html__`` in the MarkupSafe migration API. (:pr:`1342`) by
  :user:`gaborbernat`.
- Keep the source tree stable while `sanitize_node` copies a node on free-threaded Python. (:pr:`1343`) by
  :user:`gaborbernat`.
- Preserve invalid border, outline, and column-rule shorthands that mix a CSS-wide keyword with another value.
  (:pr:`1344`) by :user:`gaborbernat`.
- Accept ``suffix=`` in ``Markup.removesuffix`` in the MarkupSafe migration API. (:pr:`1345`) by :user:`gaborbernat`.
- Preserve box shadows whose length values are split by a color or ``inset`` token. (:pr:`1346`) by :user:`gaborbernat`.
- Restore base URL resolution speed after URL normalization changes. (:pr:`1347`) by :user:`gaborbernat`.
- Trim trailing C0 controls and spaces before normalizing URLs. (:pr:`1348`) by :user:`gaborbernat`.
- Normalize backslashes as path separators for special-scheme URLs. (:pr:`1349`) by :user:`gaborbernat`.
- Keep text-decoration line keywords separated by default styles or colors so invalid values stay invalid. (:pr:`1350`)
  by :user:`gaborbernat`.
- Reject invalid XML characters in doctype markup and literals. (:pr:`1351`) by :user:`gaborbernat`.
- Preserve border, outline, and column-rule shorthands with duplicate style values. (:pr:`1352`) by :user:`gaborbernat`.
- Preserve repeated substitution functions in the border-color shorthand. (:pr:`1353`) by :user:`gaborbernat`.
- Remove dangling ampersands when cleaning empty URL queries. (:pr:`1354`) by :user:`gaborbernat`.
- Preserve CSS drop-shadow offsets during minification. (:pr:`1355`) by :user:`gaborbernat`.
- Preserve empty URL authorities during normalization. (:pr:`1356`) by :user:`gaborbernat`.
- Keep Windows drive roots when normalizing file URLs. (:pr:`1357`) by :user:`gaborbernat`.
- Preserve IDNA sigma mapping in URL hosts. (:pr:`1358`) by :user:`gaborbernat`.
- Reject URL credentials when no host is present. (:pr:`1359`) by :user:`gaborbernat`.
- Preserve letter case and percent-encode non-ASCII characters in opaque URL hosts. (:pr:`1360`) by :user:`gaborbernat`.
- Treat Windows drive letters in file URL authorities as paths. (:pr:`1361`) by :user:`gaborbernat`.
- Preserve non-ASCII port text when normalizing URLs. (:pr:`1362`) by :user:`gaborbernat`.
- Keep the root slash when trimming URL paths. (:pr:`1363`) by :user:`gaborbernat`.
- Accept long zero-padded URL ports within the valid numeric range. (:pr:`1364`) by :user:`gaborbernat`.
- Reject userinfo in file URL authorities. (:pr:`1365`) by :user:`gaborbernat`.
- Encode query-shaped URL fragments with the URL Standard fragment percent-encode set. (:pr:`1367`) by
  :user:`gaborbernat`.
- Preserve long CSS percentage offsets during minification. (:pr:`1369`) by :user:`gaborbernat`.
- Preserve long RGB channel values during CSS minification. (:pr:`1370`) by :user:`gaborbernat`.
- Keep malformed RGB comma syntax invalid during CSS minification. (:pr:`1371`) by :user:`gaborbernat`.
- Keep mixed legacy RGB syntax invalid during CSS minification. (:pr:`1372`) by :user:`gaborbernat`.
- Keep font shorthands with misplaced CSS-wide keywords invalid during minification. (:pr:`1374`) by
  :user:`gaborbernat`.
- Keep malformed font line-height values invalid during CSS minification. (:pr:`1375`) by :user:`gaborbernat`.
- Preserve quoted font-family boundaries during CSS minification. (:pr:`1376`) by :user:`gaborbernat`.
- Preserve nonbreaking and other Unicode spaces at Markdown prose edges. (:pr:`1377`) by :user:`gaborbernat`.
- Keep duplicate background attachments invalid during CSS minification. (:pr:`1380`) by :user:`gaborbernat`.
- Preserve leading U+FEFF characters in Markdown exports for CommonMark readers. (:pr:`1382`) by :user:`gaborbernat`.
- Keep invalid background size keywords after a position slash from becoming valid lengths. (:pr:`1383`) by
  :user:`gaborbernat`.
- Keep content inside a heading when a nested MathML or SVG element blocks its end tag. (:pr:`1385`) by
  :user:`gaborbernat`.
- Preserve Unicode whitespace in base URLs. (:pr:`1386`) by :user:`gaborbernat`.
- Keep whitespace in active formatting when table content moves outside the table. (:pr:`1387`) by :user:`gaborbernat`.
- Keep empty nested list items when their only content is a trailing line break. (:pr:`1388`) by :user:`gaborbernat`.
- Preserve spaces in opaque base URL paths. (:pr:`1389`) by :user:`gaborbernat`.
- Keep invalid zero-unit RGB channels invalid when minifying CSS. (:pr:`1392`) by :user:`gaborbernat`.
- Keep malformed background values stable across repeated CSS minification. (:pr:`1393`) by :user:`gaborbernat`.
- Preserve line breaks inside emphasized Markdown that needs HTML tags. (:pr:`1394`) by :user:`gaborbernat`.

.. _release-1-15-1-documentation:

Documentation
=============

- Document the email address for private vulnerability reports. (:pr:`1390`) by :user:`gaborbernat`.

.. _release-1-15-1-packaging:

Packaging
=========

- Ship third-party license notices in the ``licenses/`` directory. (:pr:`1378`) by :user:`gaborbernat`.

.. _release-1-15-0:

**********************
 v1.15.0 (2026-10-10)
**********************

.. _release-1-15-0-added:

Added
=====

- Add ``turbohtml.etree`` views for extraction code that uses ElementTree text, tail, mutation and XPath operations. The
  C adapter shares native DOM nodes and supports copying across lxml API boundaries. (:pr:`1284`) by
  :user:`gaborbernat`.

.. _release-1-15-0-fixed:

Fixed
=====

- Preserve inline CSS ``white-space`` modes in Markdown output, including inherited modes on selected subtrees. Use
  preformatted blocks for preserved spacing and embedded ``<pre>`` HTML inside table cells, headings and links.
  (:pr:`1283`) by :user:`gaborbernat`.

.. _release-1-14-1:

**********************
 v1.14.1 (2026-10-10)
**********************

.. _release-1-14-1-fixed:

Fixed
=====

- Retain Markdown fence languages across class ordering and whitespace around ``<code>``; recognize ``language-*``
  classes on ``<pre>``. (:pr:`1281`) by :user:`gaborbernat`.
- Speed up XPath ``re:test()`` and ``matches()`` filters with many alternatives or repeated patterns. (:pr:`1282`) by
  :user:`gaborbernat`.

.. _release-1-14-0:

**********************
 v1.14.0 (2026-10-09)
**********************

.. _release-1-14-0-added:

Added
=====

- The standalone IDNA fuzz target checks URL and Unicode normalization invariants. (:issue:`1010`) by
  :user:`gaborbernat`.
- Add bounded HTML grammar generation and export materialized production sweeps for corpus seeding. (:issue:`1018`) by
  :user:`gaborbernat`.
- Reduce saved HTML, CSS and JavaScript findings with language-aware deletion. (:issue:`1019`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` shortens empty static blocks, ``new X()``, ``async ()=>`` and nested
  ``if``/``else``. (:issue:`1111`) by :user:`gaborbernat`.
- CSS minification shortens escaped names and preserves their token boundaries. (:issue:`1112`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` drops non-directive constant statements and prints an empty loop body as ``;``.
  (:issue:`1113`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` drops the space next to a string in an at-rule prelude. (:pr:`1134`) by
  :user:`gaborbernat`.
- Detect URL output changes and rejection during invariant fuzzing. (:pr:`1145`) by :user:`gaborbernat`.
- Add encoding stream and decoded-text invariants to the fuzz oracles. (:pr:`1152`) by :user:`gaborbernat`.
- Add a bounded public URL-host oracle for Unicode mappings, NFC and canonical Punycode. (:pr:`1155`) by
  :user:`gaborbernat`.
- Add fixed-target and repeated link-resolution checks to the fuzz oracles. (:pr:`1159`) by :user:`gaborbernat`.
- Add fixed text and encoding checks for streaming byte parsing. (:pr:`1160`) by :user:`gaborbernat`.
- Add bounded DOM sequence and observer callback invariant checks. (:pr:`1162`) by :user:`gaborbernat`.
- Add bounded iterator edit-sequence invariant checks. (:pr:`1164`) by :user:`gaborbernat`.
- Add bounded custom-property grammar generation with independent payload checks. (:pr:`1167`) by :user:`gaborbernat`.
- Add bounded HTML grammar generation with literal sibling-tree checks. (:pr:`1168`) by :user:`gaborbernat`.
- Add bounded XML generation with literal names and decoded whitespace expectations. (:pr:`1170`) by
  :user:`gaborbernat`.
- Add bounded XML generation with literal comment and CDATA expectations. (:pr:`1172`) by :user:`gaborbernat`.
- Fuzz canonical-equivalent IDNA hosts against independent normalized URL targets. (:pr:`1176`) by :user:`gaborbernat`.
- Generate SVG integration trees with independent namespace and parent expectations. (:pr:`1177`) by
  :user:`gaborbernat`.
- Share IDNA conversion between URL normalization and the standalone harness. (:pr:`1179`) by :user:`gaborbernat`.
- Extend encoding fuzz fixtures with BOM and conflicting declarations. (:pr:`1180`) by :user:`gaborbernat`.
- Extend encoding fuzz fixtures with both content-type declaration attribute orders. (:pr:`1181`) by
  :user:`gaborbernat`.
- Add encoding fuzz fixtures for UTF-16 declarations that HTML maps to UTF-8. (:pr:`1183`) by :user:`gaborbernat`.
- Generate finite nested lists with independent expectations for each item's parent. (:pr:`1184`) by
  :user:`gaborbernat`.
- Generate finite tables with independent row group and cell parent expectations. (:pr:`1185`) by :user:`gaborbernat`.
- Add encoding fuzz fixtures for labels that decode as windows-1252. (:pr:`1186`) by :user:`gaborbernat`.
- Expand HTML corpus generation with pinned Domato tag, attribute and value alternatives, document-only tag profiles,
  and formatting and SVG integration shapes. (:pr:`1192`) by :user:`gaborbernat`.
- Check URL split/recomposition and reparse invariants through public normalization. (:pr:`1193`) by
  :user:`gaborbernat`.
- Add bounded XML structure generation with materialized corpus bytes and separate node and expansion budgets.
  (:pr:`1196`) by :user:`gaborbernat`.
- Compare inline XSLT and schema outcomes with libxml2 and libxslt, including repaired seed-tree mutations. (:pr:`1198`)
  by :user:`gaborbernat`.
- Add bounded CSS structure generation from pinned Domato and Blink tables, with distinct variable/keyframe bindings and
  independent AST budget checks. (:pr:`1201`) by :user:`gaborbernat`.
- Add bounded DOM byte programs with independent state and register-order checks. (:pr:`1203`) by :user:`gaborbernat`.
- Add bounded Markdown source and HTML grammars with independent first-conversion checks and materialized corpus bytes.
  (:pr:`1204`) by :user:`gaborbernat`.
- Add bounded encoded corpus generation with explicit codec expectations. (:pr:`1205`) by :user:`gaborbernat`.
- Admit generated XML, CSS, Markdown and encoding bytes through public fuzz consumers, and seed coverage-guided CI from
  their generation contexts. (:pr:`1207`) by :user:`gaborbernat`.
- Add a fuzz-only C build mode that poisons arena gaps and parked wrappers and checks its sanitizer at startup.
  (:pr:`1217`) by :user:`gaborbernat`.
- :class:`~turbohtml.validate.RelaxNG` reads ``include`` and ``externalRef`` files resolved against the new ``base_url``
  argument, confined to ``include_root`` when set. (:pr:`1218`) by :user:`gaborbernat`.
- Fuzz retained wrappers, nested callbacks, parser reentry, hostile argument conversions and garbage collection with
  byte programs that a native tree verifier checks after every step. (:pr:`1223`) by :user:`gaborbernat`.
- Run the IDNA, phone, JavaScript and CSS standalone fuzz harnesses under MemorySanitizer, and the DOM lifecycle
  programs in parallel threads under ThreadSanitizer. (:pr:`1244`) by :user:`gaborbernat`.
- Add an allocation-failure sweep that fails each PyMem allocation of the fuzz seeds in turn and expects MemoryError.
  (:pr:`1277`) by :user:`gaborbernat`.

.. _release-1-14-0-fixed:

Fixed
=====

- The JavaScript minifier no longer reads past the inline-``<script>`` buffer when a member access ends with an empty
  name, such as ``t.0.``. (:pr:`949`) by :user:`gaborbernat`.
- Keep :meth:`~turbohtml.Node.iter_elements` safe when ``set_text``, the ``text`` setter, ``field_value``, or
  :meth:`~turbohtml.ShadowRoot.set_inner_html` replaces the iterated children, instead of crashing. Return ``None`` from
  :meth:`~turbohtml.TreeWalker.first_child` and :meth:`~turbohtml.TreeWalker.last_child` when a ``NodeFilter`` detaches
  the walked node, instead of crashing. (:pr:`950`) by :user:`gaborbernat`.
- Reject a :class:`RELAX NG <turbohtml.validate.RelaxNG>` ``<ref>`` with no name, a reference cycle that never crosses
  an ``element``, and an ``interleave`` whose branches share an element name or both match text when the schema
  compiles, and validate an ambiguous ``choice`` in bounded memory, instead of crashing, hanging, or exhausting memory.
  (:pr:`951`) by :user:`gaborbernat`.
- Minifying CSS that contains a selector-less rule such as ``{--x:}`` no longer crashes the process. Minifying CSS with
  many same-selector rules no longer costs time and memory quadratic in the rule count; a repeat merges only with a rule
  at most 256 back. (:pr:`952`) by :user:`gaborbernat`.
- Count the XSLT built-in template rules against the 400-level nesting limit, raising ``RecursionError`` on a deeper
  source document. (:pr:`953`) by :user:`gaborbernat`.
- Parsing HTML with formatting elements nested past the 512 open-element limit no longer builds a quadratic number of
  DOM nodes. (:pr:`954`) by :user:`gaborbernat`.
- Detect duplicate attributes while parsing XML in time linear in the attribute count; an element with many attributes
  in one namespace, or a repeated attribute name, no longer stalls :func:`turbohtml.parse_xml`. (:pr:`955`) by
  :user:`gaborbernat`.
- Reject an XSD :class:`~turbohtml.validate.XMLSchema` that references an undeclared component, forms a group,
  attribute-group, or type-derivation cycle or a reference chain over 100 hops, or declares an ``xs:unique``,
  ``xs:key``, or ``xs:keyref`` identity constraint, instead of crashing or validating the document against an incomplete
  schema. (:pr:`956`) by :user:`gaborbernat`.
- Raise ``ValueError`` for an EXSLT ``str:padding`` length above 100,000 characters. (:pr:`957`) by :user:`gaborbernat`.
- Drop a disallowed CSS declaration written before ``{`` in a scrubbed ``<style>`` body, such as
  ``color:expression(...)`` or ``-moz-binding:url(...)``. (:pr:`958`) by :user:`gaborbernat`.
- Attribute URL hosts the way a browser resolves them in ``normalize_url``, ``clean_url``, ``extract_links``,
  ``resolve_links``, and the sanitizer's ``media_hosts`` allowlist: a special-scheme authority ends at a backslash, an
  IPv4 address is read in all four notations and emitted dotted-decimal, the host is percent-decoded, and an IPv6
  literal is zero-compressed. (:pr:`960`) by :user:`gaborbernat`.
- Decode byte-order-mark input through :attr:`EncodingMatch.codec <turbohtml.detect.EncodingMatch>` with U+FFFD
  replacement instead of raising :exc:`UnicodeDecodeError` on malformed bytes. (:pr:`961`) by :user:`gaborbernat`.
- Skip a JSON-LD block that nests arrays or objects more than 400 levels deep instead of raising ``RecursionError`` from
  ``Document.json_ld`` and ``Document.structured_data``. (:pr:`962`) by :user:`gaborbernat`.
- Make ``serialize(Html(xml=True))`` emit well-formed XML, dropping the characters, attribute names, and comment
  spellings XML 1.0 forbids. (:pr:`963`) by :user:`gaborbernat`.
- :func:`turbohtml.detect.detect` reports ``UTF-16LE`` for the ``FF FE 00 00`` byte-order mark, matching
  :func:`turbohtml.parse`, instead of the ``UTF-32LE`` label the parser does not produce. (:pr:`964`) by
  :user:`gaborbernat`.
- Reject ``xsl:import`` hrefs that resolve to a UNC path (``\\host\share`` or ``file:////host/share``). (:pr:`965`) by
  :user:`gaborbernat`.
- The URL extraction helpers :func:`~turbohtml.extract.normalize_url`, :func:`~turbohtml.extract.clean_url`, and
  :func:`~turbohtml.extract.extract_links` no longer take time quadratic in the length of a non-ASCII host, leave a host
  longer than 16384 code points unencoded, and keep a host carrying a code point UTS #46 disallows as Unicode rather
  than converting it to punycode. (:pr:`966`) by :user:`gaborbernat`.
- The CSS minifier no longer crashes, or merges a rule past another that sets the same property, on a declaration such
  as ``c:d[;e]`` whose ``;`` does not end it. (:pr:`972`) by :user:`gaborbernat`.
- ``sanitize`` drops a URL such as ``ftp\u00a0://host`` when ``allow_relative_urls`` is off, as the URL is relative.
  (:pr:`974`) by :user:`gaborbernat`.
- Fix undefined behavior that sanitizer builds report on empty attribute values, text nodes, and namespace names.
  (:pr:`977`) by :user:`gaborbernat`.
- An XML document with many namespace prefix declarations in scope no longer stalls :func:`turbohtml.parse_xml`.
  (:pr:`979`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` rejects two statements on one line with no separator. (:issue:`984`) by
  :user:`gaborbernat`.
- The JavaScript minifier no longer crashes on a function declaration that shares a parameter's name. (:pr:`986`) by
  :user:`gaborbernat`.
- Reject a prefixed namespace declaration with an empty value, such as ``xmlns:p=""``, in :func:`turbohtml.parse_xml`.
  (:pr:`989`) by :user:`gaborbernat`.
- The JavaScript minifier now raises ``ValueError`` for a function or class declaration without a name and for a string
  or template that ends in a backslash. (:pr:`990`) by :user:`gaborbernat`.
- The JavaScript minifier no longer revisits code it already removed. (:pr:`992`) by :user:`gaborbernat`.
- Serialize element and comment children of ``<script>``, ``<style>`` and the other raw-text elements as markup.
  (:pr:`994`) by :user:`gaborbernat`.
- ``inner_html`` of a ``frame`` with children added through the DOM now returns an empty string. (:issue:`996`) by
  :user:`gaborbernat`.
- Write the text and CDATA children of ``<script>``, ``<style>`` and the other raw-text elements unescaped in
  ``inner_html`` and ``serialize(inner=True)``. (:pr:`999`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` keeps an async function expression's name and ``*``. (:pr:`1000`) by
  :user:`gaborbernat`.
- The JavaScript minifier no longer folds a variable that a block-level function reassigns. (:pr:`1001`) by
  :user:`gaborbernat`.
- Make :func:`turbohtml.clean.sanitize` HTML output re-parse to the tree the policy judged. (:pr:`1002`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` keeps the parentheses V8 needs around a destructuring argument. (:pr:`1003`) by
  :user:`gaborbernat`.
- Folding in :func:`~turbohtml.clean.minify_js` no longer changes what a script does. (:pr:`1004`) by
  :user:`gaborbernat`.
- The CSS minifier no longer merges rules past an override hidden behind ``\(`` or ``url()``. (:pr:`1006`) by
  :user:`gaborbernat`.
- The JavaScript minifier no longer shadows a used outer binding or drops a ``var`` over a catch parameter. (:pr:`1007`)
  by :user:`gaborbernat`.
- Run independent JavaScript invariants on exact public fuzz inputs. (:issue:`1014`) by :user:`gaborbernat`.
- A trailing backslash no longer escapes the closer the CSS minifier adds at end of input. (:pr:`1022`) by
  :user:`gaborbernat`.
- The CSS minifier no longer merges rules past an override with an escaped property name. (:pr:`1023`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` accepts more valid scripts, such as ``await`` identifiers and class static blocks.
  (:pr:`1029`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` rejects ``let`` as a lexical binding name and duplicate unique parameters.
  (:pr:`1030`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` keeps emphasis, strikethrough and code intact when read back. (:issue:`1032`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` no longer turns an invalid ``rgb()`` shape into a valid color. (:issue:`1033`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` keeps spaces that a selector or value needs. (:issue:`1034`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` no longer drops a declaration whose binding is still read. (:issue:`1035`) by
  :user:`gaborbernat`.
- A ``selectedcontent`` nested in an ``option`` or another ``selectedcontent`` stays empty instead of exhausting memory.
  (:pr:`1044`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` and the other text renderers handle any tree depth without :exc:`RecursionError`.
  (:pr:`1046`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` and the other text renderers keep rows under nested row groups. (:pr:`1047`) by
  :user:`gaborbernat`.
- The CSS minifier no longer closes the input inside an unterminated string or ``url(``. (:pr:`1048`) by
  :user:`gaborbernat`.
- The CSS minifier keeps the space that ends a hex escape in a property name. (:pr:`1049`) by :user:`gaborbernat`.
- The CSS minifier no longer splits a dimension whose unit has digits, escapes or non-ASCII. (:pr:`1050`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` drops a declaration or rule holding a string a newline cuts short. (:issue:`1051`)
  by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` no longer joins two idents in a property name. (:issue:`1052`) by
  :user:`gaborbernat`.
- Parsing many selected ``option`` elements is no longer quadratic, and nested options stay out of ``selectedcontent``.
  (:pr:`1057`) by :user:`gaborbernat`.
- XSLT transform cost no longer depends on where nodes sit in memory. (:issue:`1058`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` drops a rule whose selector holds a stray ``;`` or ``}``. (:issue:`1059`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` closes a ``(`` or ``[`` left open at the end of input. (:issue:`1060`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` no longer trims the space of a backslash-space escape. (:issue:`1061`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` drops a rule with an empty selector. (:issue:`1062`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` no longer merges a number into the next token in at-rules and ``calc()``.
  (:issue:`1063`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` no longer leaves some value and selector rewrites for a second call.
  (:issue:`1064`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` merges rules from combined same-query ``@media`` blocks in one call.
  (:issue:`1065`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` drops a ``<br>`` at the end of a block instead of leaving a blank line.
  (:issue:`1068`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` renders a ``<br>`` inside a heading as a space. (:issue:`1069`) by
  :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` escapes a backtick in an image's alt text. (:issue:`1072`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` keeps the block after an empty list item or block quote outside the container.
  (:issue:`1073`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` keeps a list nested when a non-``li`` element wraps it inside a list item.
  (:issue:`1074`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` no longer turns a unit starting with ``e`` into an exponent. (:issue:`1077`) by
  :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` and :meth:`~turbohtml.Node.to_text` cap list and quote indentation at 20 levels.
  (:pr:`1079`) by :user:`gaborbernat`.
- Selectors with ``~``, ``:has(~ ...)``, ``:has(+ ...)`` or chained descendant combinators match in linear time.
  (:pr:`1081`) by :user:`gaborbernat`.
- The CSS minifier keeps ``url()`` with a modifier in ``@import`` and ``@namespace``. (:pr:`1082`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` keeps the case of an escaped custom property name. (:pr:`1083`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` reads a leading ``let`` with no binding after it as an identifier. (:pr:`1084`) by
  :user:`gaborbernat`.
- :meth:`~turbohtml.Node.xpath` accepts ``or``, ``and``, ``div`` and ``mod`` after a ``*`` name test. (:pr:`1085`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.convert.css_to_xpath` makes ``[att="" i]`` match only elements carrying the attribute. (:pr:`1086`)
  by :user:`gaborbernat`.
- :func:`~turbohtml.parse_xml` accepts an empty comment before the root element. (:pr:`1087`) by :user:`gaborbernat`.
- XML output from ``serialize(Html(xml=True))`` now reads back unchanged with ``parse_xml``. (:pr:`1088`) by
  :user:`gaborbernat`.
- ``source_locations=True`` no longer reports an end tag the source never wrote. (:pr:`1089`) by :user:`gaborbernat`.
- The parser no longer builds ``head`` and ``body`` inside SVG or MathML content. (:pr:`1090`) by :user:`gaborbernat`.
- A ``pre`` in a template table row no longer gains a line feed per round trip. (:pr:`1091`) by :user:`gaborbernat`.
- The parser ignores ``</body>`` and ``</html>`` when the body element is out of scope. (:pr:`1092`) by
  :user:`gaborbernat`.
- ``<selectedcontent>`` mirrors the option the spec selects. (:pr:`1093`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` flattens all nested blocks in one call. (:pr:`1094`) by :user:`gaborbernat`.
- The sanitizer keeps escaped table sections and rows in source order. (:pr:`1095`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` rejects declarations in single-statement bodies and invalid binding patterns.
  (:issue:`1108`) by :user:`gaborbernat`.
- :func:`~turbohtml.convert.css_to_xpath` makes ``[att|="" i]`` match only elements carrying the attribute.
  (:issue:`1109`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` ends a stylesheet's final statement at-rule with ``;``. (:issue:`1110`) by
  :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` keeps ``{}`` blocks in custom property values. (:issue:`1114`) by
  :user:`gaborbernat`.
- :class:`~turbohtml.Element` rejects children for ``frame``, like the other elements serialized without them.
  (:issue:`1118`) by :user:`gaborbernat`.
- ``<selectedcontent>`` mirrors the selected option when written after it or past a ``<datalist>`` option.
  (:issue:`1119`) by :user:`gaborbernat`.
- Deeply nested :meth:`~turbohtml.Node.to_markdown` output reads back in full with markdown-it's CommonMark preset.
  (:issue:`1121`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` no longer crashes on a long statement list. (:pr:`1126`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_js` rejects more invalid declarations, catch parameters and binding names.
  (:issue:`1127`) by :user:`gaborbernat`.
- :func:`~turbohtml.clean.minify_css` merges same-query ``@media`` blocks separated by a rule it drops. (:issue:`1128`)
  by :user:`gaborbernat`.
- Selectors using ``~`` run faster on long runs of sibling elements. (:pr:`1141`) by :user:`gaborbernat`.
- Preserve valid CSS declarations beside empty values and invalid box edges. (:pr:`1143`) by :user:`gaborbernat`.
- Preserve quoted attribute names and misplaced universal selectors during CSS minification. (:pr:`1144`) by
  :user:`gaborbernat`.
- Preserve invalid top-level delimiters in background-position values during CSS minification. (:pr:`1146`) by
  :user:`gaborbernat`.
- Preserve invalid whitespace around operators in CSS math functions. (:pr:`1147`) by :user:`gaborbernat`.
- Preserve trailing decimal points in CSS numeric declarations. (:pr:`1148`) by :user:`gaborbernat`.
- Reject cleaned URLs whose canonical authority loses its web-host shape. (:pr:`1149`) by :user:`gaborbernat`.
- Preserve numeric token types and fractional math wrappers in z-index values. (:pr:`1151`) by :user:`gaborbernat`.
- Preserve quoted position components in malformed background-position declarations. (:pr:`1154`) by
  :user:`gaborbernat`.
- RELAX NG rejects schemas whose root uses a foreign namespace. (:pr:`1156`) by :user:`gaborbernat`.
- Treat RELAX NG values without an explicit type as tokens. (:pr:`1161`) by :user:`gaborbernat`.
- Preserve separators between stray CSS text and following nested rules. (:pr:`1163`) by :user:`gaborbernat`.
- Enforce RELAX NG data-pattern exclusions. (:pr:`1165`) by :user:`gaborbernat`.
- Keep invalid background size and repeat pairs invalid. (:pr:`1166`) by :user:`gaborbernat`.
- Keep trailing auto after unresolved background size functions. (:pr:`1169`) by :user:`gaborbernat`.
- Accept empty element content for RELAX NG empty string patterns. (:pr:`1171`) by :user:`gaborbernat`.
- Keep repeated box shorthand components when functions remain unresolved. (:pr:`1173`) by :user:`gaborbernat`.
- Enforce numeric range bounds in RELAX NG datatype validation. (:pr:`1174`) by :user:`gaborbernat`.
- Normalize literal file URL path backslashes and empty root paths. (:pr:`1175`) by :user:`gaborbernat`.
- Preserve namespace constraints when minifying universal selectors inside functions. (:pr:`1178`) by
  :user:`gaborbernat`.
- Preserve background-position values that contain unresolved functions. (:pr:`1182`) by :user:`gaborbernat`.
- Preserve font shorthand components around unresolved functions during CSS minification. (:pr:`1187`) by
  :user:`gaborbernat`.
- Reject documented API errors from Atheris-generated corpora. (:pr:`1190`) by :user:`gaborbernat`.
- Reject unresolved XSD built-in type names and global elements without names. (:pr:`1191`) by :user:`gaborbernat`.
- Reject forbidden characters after URL host decoding and IDNA mapping. (:pr:`1193`) by :user:`gaborbernat`.
- Exercise qualified public APIs through checked Atheris consumers. (:pr:`1197`) by :user:`gaborbernat`.
- Reject undefined RELAX NG references and ignore foreign annotation subtrees during compilation. (:pr:`1198`) by
  :user:`gaborbernat`.
- Reject RELAX NG include and externalRef patterns without href attributes, including unused definitions. (:pr:`1199`)
  by :user:`gaborbernat`.
- Keep generated corpus bytes in separate executable target directories with their production metadata. (:pr:`1200`) by
  :user:`gaborbernat`.
- The Markdown conversion check accepts HTML shapes CommonMark cannot write and rejects list and table content outside
  its HTML content model. (:pr:`1208`) by :user:`gaborbernat`.
- ``to_markdown`` writes an empty ``<blockquote>`` as ``>`` and an empty ``<pre>`` as a fence without a blank line.
  (:pr:`1209`) by :user:`gaborbernat`.
- ``to_markdown`` writes a leading ``<br>`` and each ``<br>`` of a consecutive run as a hard line break. (:pr:`1210`) by
  :user:`gaborbernat`.
- The Markdown conversion check treats every HTML block as a paragraph boundary and wraps quote and list item text in
  paragraphs. (:pr:`1211`) by :user:`gaborbernat`.
- ``to_markdown`` keeps a link with an empty ``href`` and writes an empty link or image destination as ``<>``.
  (:pr:`1212`) by :user:`gaborbernat`.
- ``to_markdown`` writes a line break inside inline code as a space so the next line cannot open a block. (:pr:`1213`)
  by :user:`gaborbernat`.
- ``to_markdown`` keeps block content inside a heading on the heading line and reads a block edge in link text or a
  heading as a space. (:pr:`1214`) by :user:`gaborbernat`.
- ``to_markdown`` closes and reopens emphasis around a block inside it and starts a new paragraph for inline content
  after that block. (:pr:`1215`) by :user:`gaborbernat`.
- ``to_markdown`` separates a list item's text from a paragraph or block before it with a blank line. (:pr:`1216`) by
  :user:`gaborbernat`.
- The Markdown conversion check reads the parse tree that ``to_markdown`` converts. (:pr:`1221`) by :user:`gaborbernat`.
- ``Element.css_path`` no longer hangs on a detached element whose id the document lacks, a node filter that steps its
  own traverser raises instead of corrupting the outer step, and ``MutationObserver.observe``, ``TreeWalker``,
  ``NodeIterator``, ``append`` and ``extend`` keep a node and its tree together when a callback or another thread moves
  it. (:pr:`1222`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Element.css_path` no longer anchors a detached element on an attached element's id. (:pr:`1227`) by
  :user:`gaborbernat`.
- A :class:`~turbohtml.NodeIterator` now follows removals made by ``normalize()``, ``Range`` methods and query edits.
  (:pr:`1228`) by :user:`gaborbernat`.
- Inserting or wrapping with nodes from another tree no longer races a concurrent move of the target. (:pr:`1229`) by
  :user:`gaborbernat`.
- :class:`~turbohtml.Range` and :class:`~turbohtml.StaticRange` now read a moving container together with its tree.
  (:pr:`1230`) by :user:`gaborbernat`.
- :class:`~turbohtml.Range` methods now read a moving node argument together with its tree. (:pr:`1231`) by
  :user:`gaborbernat`.
- Raise ``MemoryError`` when a CSS selector query fails to allocate memory. (:pr:`1233`) by :user:`gaborbernat`.
- ``to_markdown`` drops tabs and line breaks from link and image destinations. (:pr:`1237`) by :user:`gaborbernat`.
- ``to_markdown`` keeps the spaces at both ends of inline code. (:pr:`1238`) by :user:`gaborbernat`.
- ``to_markdown`` applies emphasis around a table to every cell. (:pr:`1241`) by :user:`gaborbernat`.
- Raise MemoryError when an allocation fails while minifying CSS or a style attribute, instead of returning truncated
  output or crashing. (:pr:`1242`) by :user:`gaborbernat`.
- ``to_markdown`` keeps a ``<br>`` and a block edge inside ``<pre>`` as line breaks of the code block. (:pr:`1245`) by
  :user:`gaborbernat`.
- :class:`~turbohtml.cssom.StyleSheet` and :class:`~turbohtml.cssom.StyleDeclaration` keep ``{}`` blocks in custom
  property values. (:pr:`1246`) by :user:`gaborbernat`.
- :class:`~turbohtml.cssom.StyleSheet` and :class:`~turbohtml.cssom.StyleDeclaration` read a backslash-escaped delimiter
  as part of a name. (:pr:`1246`) by :user:`gaborbernat`.
- Raise ``MemoryError`` when ``to_markdown`` fails to allocate memory inside a table cell, a nested list or quote, or a
  converter. (:pr:`1247`) by :user:`gaborbernat`.
- Write a link destination that holds an ASCII control character in the ``<...>`` form, the only form CommonMark reads
  it in. (:pr:`1248`) by :user:`gaborbernat`.
- ``to_markdown`` closes emphasis before a block quote inside it and reopens it in the quote. (:pr:`1249`) by
  :user:`gaborbernat`.
- Skip ``<title>``, ``<datalist>``, ``<noembed>``, ``<noframes>`` and ``<rp>`` in ``to_markdown`` and ``to_text``, which
  browsers do not render. (:pr:`1251`) by :user:`gaborbernat`.
- Raise ``MemoryError`` when parsing or rewriting HTML fails to allocate memory. (:pr:`1252`) by :user:`gaborbernat`.
- Raise MemoryError when an allocation fails while minifying JavaScript, instead of crashing or returning unmangled
  output. (:pr:`1253`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Element.extend` no longer links a node twice when another thread links its imported copy first.
  (:pr:`1254`) by :user:`gaborbernat`.
- :class:`~turbohtml.cssom.StyleSheet` and :class:`~turbohtml.cssom.StyleDeclaration` no longer read an escaped slash as
  the start of a comment. (:pr:`1255`) by :user:`gaborbernat`.
- ``to_markdown`` escapes a ``&`` that starts a character reference finished by the next text node. (:pr:`1256`) by
  :user:`gaborbernat`.
- ``to_markdown`` escapes emphasis, tag, reference and line-break characters in image alt text. (:pr:`1258`) by
  :user:`gaborbernat`.
- ``to_markdown`` writes line breaks in link and image titles as character references and escapes a ``&`` that starts
  one. (:pr:`1259`) by :user:`gaborbernat`.
- Skip ``<template>`` content in ``to_markdown`` and ``to_text``, which browsers do not render. (:pr:`1260`) by
  :user:`gaborbernat`.
- ``to_markdown`` leaves out a table row that has no cells. (:pr:`1261`) by :user:`gaborbernat`.
- Raise ``MemoryError`` when building, copying or sanitizing elements fails to allocate memory. (:pr:`1262`) by
  :user:`gaborbernat`.
- ``to_markdown`` keeps a list separate from one before it when only an empty block stands between them. (:pr:`1263`) by
  :user:`gaborbernat`.
- ``to_markdown`` writes emphasis as an HTML tag where its delimiters would merge or could not open or close.
  (:pr:`1264`) by :user:`gaborbernat`.
- ``xsl:number`` now inserts the string value of a ``value`` that is NaN, infinite or below 0.5, and formats a ``value``
  of 2^63 or more as its exact integer. (:pr:`1265`) by :user:`gaborbernat`.
- ``to_markdown`` places a nested list or an item's first block on its own line where CommonMark reads it back.
  (:pr:`1266`) by :user:`gaborbernat`.
- ``to_markdown`` drops a ``<br>`` that nothing visible follows and keeps one after an inner block in the next block.
  (:pr:`1267`) by :user:`gaborbernat`.
- ``to_markdown`` flattens a block inside a table cell and writes a ``<pre>`` on a single line as a code span.
  (:pr:`1268`) by :user:`gaborbernat`.
- ``to_markdown`` makes a list item loose when it holds two paragraphs or text after a nested block. (:pr:`1269`) by
  :user:`gaborbernat`.
- ``Linker.linkify`` and ``Linker.linkify_node`` raise ``MemoryError`` when an allocation fails, instead of
  ``SystemError``. (:pr:`1273`) by :user:`gaborbernat`.

.. _release-1-13-1:

**********************
 v1.13.1 (2026-10-01)
**********************

.. _release-1-13-1-fixed:

Fixed
=====

- Raise ``MemoryError`` for an EXSLT ``str:padding`` length above ``sys.maxsize // 4`` characters instead of writing
  past a small buffer. (:pr:`933`) by :user:`gaborbernat`.
- Split sanitized CSS declarations where a browser does, reading a backslash outside strings as an escape and an
  unquoted ``url(...)`` as one token, and drop a declaration whose string a newline ends (GHSA-9x48-74fj-x342).
  (:pr:`934`) by :user:`gaborbernat`.
- Raise ``ValueError`` on a circular ``xsl:attribute-set`` and ``RecursionError`` past 400 levels of
  ``use-attribute-sets`` and template nesting instead of crashing, and apply a shared attribute set once per element.
  (:pr:`935`) by :user:`gaborbernat`.
- Keep XSLT comment and processing-instruction text inside its node, and raise ``ValueError`` when the html method would
  write ``>`` inside a processing instruction. (:pr:`936`) by :user:`gaborbernat`.
- Leave out an XSLT element, attribute or processing instruction whose computed name is invalid, keeping an element's
  content, instead of writing the name as markup. (:pr:`937`) by :user:`gaborbernat`.
- Raise ``ValueError`` in :func:`~turbohtml.rewrite.rewrite` when ``set_attribute`` gets a name, or a comment's
  ``set_text`` gets text, that would inject markup. (:pr:`938`) by :user:`gaborbernat`.
- Keep ``<`` and ``/`` apart when minifying CSS, and return a stylesheet unchanged when its minified form would spell a
  ``</style`` its source did not (GHSA-wh2c-9vrv-w597). (:pr:`939`) by :user:`gaborbernat`.
- Cap the CSS minifier at 100 nesting levels, dropping a deeper block and copying a deeper function argument through
  unchanged, instead of overflowing the C stack. (:pr:`940`) by :user:`gaborbernat`.
- Escape ``"`` as ``&quot;`` in attribute values under ``Formatter.MINIMAL``. (:pr:`941`) by :user:`gaborbernat`.
- Escape ``<`` and ``>`` in attribute values under every formatter and in the start tags
  :func:`~turbohtml.rewrite.rewrite` writes for edited elements. (:pr:`942`) by :user:`gaborbernat`.
- Stop :func:`~turbohtml.clean.minify_js` from creating a ``</script`` or ``<!--`` the source did not spell, and emit an
  inline script verbatim when its minified form still holds either sequence. (:pr:`943`) by :user:`gaborbernat`.
- Raise :exc:`RuntimeError` from a :meth:`~turbohtml.Node.serialize_iter` stream after a tree edit such as ``extract``
  or ``clear``, instead of crashing or streaming a cut document. (:pr:`944`) by :user:`gaborbernat`.
- Space apart the sequences that close DOM-built leaf data: a leading ``>`` or ``->`` in a comment outside canonical
  XML, ``--`` before ``>`` or ``!>`` in an HTML comment, a ``-`` before another ``-`` or at the end in an XML or
  canonical comment, and ``?>`` in XML instruction data, and split XML CDATA at ``]]>`` (GHSA-7vrw-9mvp-2x7j).
  (:pr:`945`) by :user:`gaborbernat`.
- Match unsafe names and the per-name policy options in any ASCII case when sanitizing :func:`~turbohtml.parse_xml`
  trees, and replace an attribute spelled in another case when writing one (GHSA-pjrh-5m66-3wwf). (:pr:`946`) by
  :user:`gaborbernat`.
- Stop ``re:test``, ``re:replace``, ``matches`` and ``replace`` from hanging on patterns such as ``(a+)+$``; oversized
  patterns and runaway back-references raise ``ValueError``. (:pr:`947`) by :user:`gaborbernat`.
- Raise ``ValueError`` for a schema pattern nested deeper than 250 groups, needing more than 2,000,000 automaton states,
  or holding a ``{n,m}`` quantifier with ``n`` above ``m``, and match long alternations without exhausting the stack.
  (:pr:`948`) by :user:`gaborbernat`.

.. _release-1-13-0:

**********************
 v1.13.0 (2026-09-30)
**********************

.. _release-1-13-0-added:

Added
=====

- Add :func:`turbohtml.transform.strparam` to pass text as an XSLT parameter without hand-quoting it. (:pr:`928`) by
  :user:`gaborbernat`.

.. _release-1-13-0-fixed:

Fixed
=====

- Reject disallowed URL schemes hidden behind character references in the bleach shim. (:pr:`919`) by
  :user:`gaborbernat`.
- Check ``<meta http-equiv="refresh">`` redirect URLs against ``url_schemes``. (:pr:`920`) by :user:`gaborbernat`.
- Serialize a CDATA section or processing instruction holding ``>`` as HTML without turning its text into markup.
  (:pr:`921`) by :user:`gaborbernat`.
- Refuse an ``xsl:import`` of a FIFO, device or directory with ``ValueError`` instead of hanging or exhausting memory.
  (:pr:`922`) by :user:`gaborbernat`.
- Stop the JavaScript minifier from turning a string it folded or moved into a ``"use strict"`` directive. (:pr:`923`)
  by :user:`gaborbernat`.
- Stop the CSS minifier from gluing two tokens into one when it drops a comment or space between them. (:pr:`924`) by
  :user:`gaborbernat`.
- Write text into ``script``, ``style`` and other raw-text elements unescaped in :func:`~turbohtml.rewrite.rewrite`.
  (:pr:`925`) by :user:`gaborbernat`.
- Accept ``>`` and ``]`` inside the quoted parts of an XML ``<!DOCTYPE>`` in :func:`~turbohtml.parse_xml`. (:pr:`926`)
  by :user:`gaborbernat`.
- Keep headings and links in wrapped list items parseable as Markdown. (:pr:`929`) by :user:`gaborbernat`.
- Preserve list markers in Markdown table cells and word boundaries in code with block descendants. (:pr:`930`) by
  :user:`gaborbernat`.

.. _release-1-13-0-documentation:

Documentation
=============

- Warn that builder text inside raw-text elements is written unescaped and that feed fields come back unsanitized.
  (:pr:`927`) by :user:`gaborbernat`.

.. _release-1-12-0:

**********************
 v1.12.0 (2026-09-29)
**********************

.. _release-1-12-0-added:

Added
=====

- Use :meth:`~turbohtml.Node.iter_elements` for lazy, tag-filtered traversal of large trees. The iterator keeps its next
  match across edits and follows the tree that then contains it. (:pr:`915`) by :user:`gaborbernat`.

.. _release-1-11-0:

**********************
 v1.11.0 (2026-09-29)
**********************

.. _release-1-11-0-added:

Added
=====

- Add ``Markdown.Escaping(mode="none")``, which leaves prose as written in :meth:`~turbohtml.Node.to_markdown` apart
  from the ``asterisks`` and ``underscores`` choices and a ``|`` inside a table cell. (:issue:`874`) by
  :user:`gaborbernat`.
- Assigning :attr:`Element.tag <turbohtml.Element.tag>` renames the element in place, keeping its namespace, attributes,
  children and position. (:issue:`883`) by :user:`gaborbernat`.
- Add :func:`turbohtml.migration.bleach.attribute_policy` for retaining bleach attribute rules with native CSS settings.
  Preserve callable precedence, source values before safety checks, and changes to rule mappings. Treat ``*`` as an
  attribute name. (:pr:`895`) by :user:`gaborbernat`.
- Add ``Policy.attribute_predicate`` to inspect attributes before allowlist and safety checks. (:pr:`901`) by
  :user:`gaborbernat`.

.. _release-1-11-0-fixed:

Fixed
=====

- :meth:`~turbohtml.Node.to_markdown` keeps a list's content that is not an ``<li>``: text or a block before the first
  item renders on its own, one after an item continues it, and items wrapped in another element keep their markers.
  (:issue:`864`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` keeps a table's ``<caption>``, rendering it as a paragraph above the pipe table
  and the stripped table text. (:issue:`865`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` ends each pipe-table row at its closing ``|`` with no trailing space, and a cell
  that holds a block no longer starts with two spaces. (:issue:`866`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` keeps two adjacent lists of the same kind apart: the second switches its bullet or
  ordered delimiter, since CommonMark joins items that share one into a single list. (:issue:`867`) by
  :user:`gaborbernat`.
- :meth:`~turbohtml.Node.to_markdown` keeps whitespace at the edges of a link or a converted inline element outside its
  output, so ``x<a href="h"> t </a>y`` renders ``x [t](h) y`` where the leading space used to vanish. (:issue:`868`) by
  :user:`gaborbernat`.
- An absolute XPath or ``id()`` evaluated on a node outside any document, such as an element built with ``Element(...)``
  or held by a ``DocumentFragment``, now starts at the root of that node's tree instead of crashing the interpreter.
  (:issue:`876`) by :user:`gaborbernat`.
- ``hash()`` on a node no longer slows down as its tree takes in nodes from other trees: the hashes those nodes carried
  over now sit in a hash table instead of a list scanned on every call. (:issue:`878`) by :user:`gaborbernat`.
- Negative child indexes on PyPy 8.0 no longer get the sequence length added twice, so ``node[-len(node) - 1]`` raises
  :class:`IndexError` again instead of returning a child. (:pr:`879`) by :user:`gaborbernat`.
- Setting an attribute accepts every name the DOM Standard allows, including a quote, ``<`` or a control character the
  parser keeps from markup; only ASCII whitespace, U+0000, ``/``, ``=`` and ``>`` are rejected. (:issue:`881`) by
  :user:`gaborbernat`.
- An XPath descendant step without a predicate, such as the second step of ``.//div//a``, no longer runs in quadratic
  time when its context nodes nest inside one another. (:issue:`885`) by :user:`gaborbernat`.
- Moving a subtree between trees preserves references to its descendants and their attribute views. Aliases of the moved
  node retain their identity and hashes. (:issue:`887`) by :user:`gaborbernat`.
- Source installs on Windows activate MSVC even when MinGW is on ``PATH``. This prevents ARM64 builds from selecting an
  x64 compiler and failing to link Python. (:issue:`888`) by :user:`gaborbernat`.
- Predicate-free XPath descendant steps skip nested contexts during traversal. Deep trees no longer require a separate
  ancestor walk for each context. (:issue:`890`) by :user:`gaborbernat`.
- Reduce Markdown conversion overhead by inlining child dispatch and skipping converter calls without configured
  converters. (:issue:`893`) by :user:`gaborbernat`.
- Render ``kbd`` and ``samp`` as Markdown code spans with safe backtick fences. Omit empty inline code spans.
  (:pr:`896`) by :user:`gaborbernat`.
- Speed up XPath ``re:test()`` and ``matches()`` predicates by reusing compiled regular expressions. (:pr:`897`) by
  :user:`gaborbernat`.
- Reuse parent styles and CSS sibling positions across computed-style calls and reverse traversal. (:pr:`898`) by
  :user:`gaborbernat`.
- Reject unmatched brackets in URL authorities during sanitization, including rewritten and forced attributes. Preserve
  bracketed hostnames and brackets outside the authority. (:pr:`899`) by :user:`gaborbernat`.
- Keep cloned element attributes independent during DOM edits and sanitization. (:pr:`900`) by :user:`gaborbernat`.
- Fix XSLT transforms of standalone and detached nodes, including document fragments. Keep source trees alive across
  callbacks. (:pr:`902`) by :user:`gaborbernat`.
- Reuse compiled stylesheet text to reduce XSLT allocations. (:pr:`904`) by :user:`gaborbernat`.
- Honor form ownership and tree boundaries when setting a radio input's ``checked`` state. (:pr:`905`) by
  :user:`gaborbernat`.
- Reduce query ordering costs for large sets of sibling roots. (:pr:`906`) by :user:`gaborbernat`.
- Reduce parent query costs for nodes without siblings. (:pr:`907`) by :user:`gaborbernat`.
- Avoid repeated attribute-name length calculations during XSLT execution. (:pr:`908`) by :user:`gaborbernat`.
- Retain attribute predicates when callbacks clear the ``attrs`` mapping during ``find()`` or ``find_all()``.
  (:pr:`910`) by :user:`gaborbernat`.
- Count default XSLT numbers by expanded name and processing-instruction target. (:pr:`911`) by :user:`gaborbernat`.

.. _release-1-10-0:

**********************
 v1.10.0 (2026-09-24)
**********************

.. _release-1-10-0-added:

Added
=====

- Add ``DocumentFragment``: ``Range.extract_contents`` and ``Range.clone_contents`` return one, and every insertion
  method moves a fragment's children into place and leaves it empty, as the DOM insert algorithm does. (:pr:`857`) by
  :user:`gaborbernat`.

.. _release-1-10-0-fixed:

Fixed
=====

- Stop :func:`~turbohtml.clean.sanitize` escape mode from rendering a start tag the source never wrote: the ``tbody`` a
  bare ``<table><tr>`` implies, a formatting element the parser clones, or the skeleton of an empty document.
  (:pr:`807`) by :user:`gaborbernat`.
- Keep the end tag of an element built with :mod:`turbohtml.build` when :func:`~turbohtml.clean.sanitize_node` escapes
  it, so ``<span>x</span>y`` no longer escapes as ``&lt;span&gt;xy``. (:pr:`808`) by :user:`gaborbernat`.
- Match elements named like HTML tags (``title``, ``p``, ``div``) with CSS selectors on a :func:`~turbohtml.parse_xml`
  tree, so ``select("title")`` from the document and combinators such as ``p > x`` find them. (:pr:`809`) by
  :user:`gaborbernat`.
- :class:`turbohtml.IncrementalParser` with ``source_locations=True`` reports the same absolute offsets as
  :func:`turbohtml.parse` when a tag spans two feeds, and ``to_source()`` on its document no longer reads freed memory.
  (:pr:`810`) by :user:`gaborbernat`.
- Stop :func:`~turbohtml.clean.sanitize` from leaving a kept HTML element such as ``<style>`` directly under ``<svg>``
  or ``<math>`` when it escapes the ``desc``, ``title``, ``foreignObject``, or MathML text element that held it.
  (:pr:`811`) by :user:`gaborbernat`.
- :class:`turbohtml.Tokenizer` with ``capture_source=True`` reports the full source of a tag split across two ``feed()``
  calls, where it used to truncate it or raise ``ValueError``. (:pr:`812`) by :user:`gaborbernat`.
- ``minify_css`` keeps the zero units the ``flex`` shorthand needs: ``flex:0px`` and ``flex:1 1 0px`` no longer change
  grow or basis, and ``flex-basis:0%`` no longer becomes ``flex-basis:0``. (:pr:`813`) by :user:`gaborbernat`.
- ``minify_css`` keeps zero units inside ``round()``, ``abs()``, ``hypot()``, ``mod()``, ``rem()`` and the other CSS
  Values 4 math functions, where ``round(0px,1px)`` used to become the invalid ``round(0,1px)``. (:pr:`814`) by
  :user:`gaborbernat`.
- Make :func:`~turbohtml.clean.sanitize` keep HTML content under MathML ``annotation-xml`` only while the output still
  carries an ``encoding`` of ``text/html`` or ``application/xhtml+xml``. (:pr:`815`) by :user:`gaborbernat`.
- :class:`turbohtml.IncrementalParser` honors a byte-order mark at the start of bytes input: the mark picks the encoding
  over the ``encoding`` argument and is stripped, as :func:`turbohtml.parse` does. (:pr:`816`) by :user:`gaborbernat`.
- Stop :func:`~turbohtml.clean.sanitize` from keeping an HTML ``mglyph`` or ``malignmark`` under a MathML ``mi``,
  ``mo``, ``mn``, ``ms``, or ``mtext``, where a reparse turns it and its children into MathML. (:pr:`817`) by
  :user:`gaborbernat`.
- A ``colgroup`` fragment or a template holding a ``<col>`` keeps the whitespace that follows ignored text, so
  ``parse_fragment("a b\nc", "colgroup")`` yields ``" \n"`` instead of an empty fragment. (:pr:`818`) by
  :user:`gaborbernat`.
- ``Range`` operations raise ``IndexError`` or ``ValueError`` when a tree edit left a boundary past the end of its text,
  in another tree, or after the end, instead of reading out of bounds or crashing. (:pr:`819`) by :user:`gaborbernat`.
- ``node.wrap(node)`` raises ``ValueError`` instead of making the node its own parent and looping. (:pr:`820`) by
  :user:`gaborbernat`.
- :func:`turbohtml.parse_fragment` keeps the SVG mixed case on its root, so an ``"svg foreignObject"`` context returns a
  ``foreignObject`` element instead of ``foreignobject``. (:pr:`821`) by :user:`gaborbernat`.
- Make :func:`~turbohtml.clean.sanitize_node` and ``transform_tags`` scrub every text run of a kept ``<style>``, drop
  its element children, and escape a ``</style`` in its body so the stylesheet cannot close the element early.
  (:pr:`822`) by :user:`gaborbernat`.
- Appending a shadow host into its own shadow tree raises ``ValueError`` instead of creating a cycle that made
  ``assigned_nodes(flatten=True)`` loop forever. (:pr:`823`) by :user:`gaborbernat`.
- Make :func:`~turbohtml.clean.sanitize` and the bleach ``clean`` shim drop ``javascript:`` URLs even when
  ``url_schemes`` or ``protocols`` lists ``javascript``, as the safety baseline documents. (:pr:`824`) by
  :user:`gaborbernat`.
- ``minify_js`` keeps ``(0,o.f)()``, ``(1&&o.f)()`` and similar value callees, tags and ``delete``/``typeof`` operands
  as ``(0,o.f)`` instead of unwrapping them to ``o.f``, which changed ``this``, deleted properties and turned indirect
  ``eval`` direct. (:pr:`825`) by :user:`gaborbernat`.
- Make ``strip_template_markers`` in :func:`~turbohtml.clean.sanitize` catch a marker whose halves end up adjacent once
  a comment or disallowed element between them is removed, stripped, or escaped, and markers inside an escaped tag.
  (:pr:`826`) by :user:`gaborbernat`.
- Make :func:`~turbohtml.clean.linkify` leave the text of ``xmp``, ``iframe``, ``noembed``, ``noframes``, ``plaintext``,
  ``textarea``, ``title``, and scripting-parsed ``noscript`` untouched instead of dropping or nesting link text there.
  (:pr:`827`) by :user:`gaborbernat`.
- Stop :func:`~turbohtml.clean.sanitize` from appending another ``;}`` on every pass to a ``<style>`` body that ends
  inside an unterminated string or comment; the trailing declaration is now dropped. (:pr:`828`) by :user:`gaborbernat`.
- ``minify_js`` keeps a function declared in a block, and the binding its name reaches outside the block, when mangling:
  Annex B makes the function visible after the block, so dropping or inlining it broke ``typeof g`` and calls there.
  (:pr:`829`) by :user:`gaborbernat`.
- ``minify_js`` keeps assignments to parameters in functions that read ``arguments``, which mirrors each parameter in
  sloppy code; ``function(a){a=2;return arguments[0]}`` used to lose the ``a=2``. (:pr:`830`) by :user:`gaborbernat`.
- ``minify_js`` accepts a raw U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR inside a string literal, legal since
  ES2019, instead of raising a lexical error. (:pr:`831`) by :user:`gaborbernat`.
- ``wrap``, ``wrap_siblings``, ``insert_before``, ``insert_after``, ``replace_with``, ``unwrap`` and
  ``Range.insert_node`` no longer corrupt the tree or crash on the free-threaded build when another thread edits it
  while a node moves in from another tree. (:pr:`832`) by :user:`gaborbernat`.
- ``minify_js`` and the HTML minifier read ``<!--`` and a line-leading ``-->`` as line comments in classic scripts, as
  Annex B requires; ``<script type=module>`` keeps them as operators. (:pr:`833`) by :user:`gaborbernat`.
- ``Element`` and ``E`` keep the first of several attribute keys that differ only in ASCII case, matching the parser,
  instead of storing the attribute twice. (:pr:`834`) by :user:`gaborbernat`.
- ``Range.surround_contents`` raises ``ValueError`` for a wrapper that is not an element, such as a ``Text`` node,
  instead of silently dropping the range contents. (:pr:`835`) by :user:`gaborbernat`.
- ``minify_js`` output is a fixed point again when a binding named ``undefined`` exists: reads that reach the global
  ``undefined`` fold to ``void 0`` on the first pass instead of the second. (:pr:`836`) by :user:`gaborbernat`.
- ``NodeIterator`` follows the DOM pre-remove steps: removing an ancestor of its current node, even from inside the
  filter, moves the iterator out of the removed subtree so iteration continues with the remaining nodes. (:pr:`837`) by
  :user:`gaborbernat`.
- XPath predicates and steps on an attribute now use the attribute as the context node: ``//@n[. > 5]`` compares the
  attribute value and ``//@n/..`` returns the owner element. (:pr:`838`) by :user:`gaborbernat`.
- Parse well-formed feeds in ``turbohtml.extract.feed()`` as XML, so CDATA summaries and content keep their markup, a
  channel ``<image>`` no longer overrides the feed title and link, and ``parse_xml(...).feed()`` reads RSS ``<link>``
  text. (:pr:`839`) by :user:`gaborbernat`.
- ``Element.attrs`` implements the full ``MutableMapping`` interface: ``update``, ``pop``, ``popitem``, ``setdefault``,
  ``clear``, ``copy``, equality with any mapping, and ``|`` / ``|=``. (:pr:`840`) by :user:`gaborbernat`.
- XPath name tests match SVG and MathML elements by their exact spelling: ``//foreignObject`` finds the SVG element and
  ``//foreignobject`` no longer does. (:pr:`841`) by :user:`gaborbernat`.
- ``to_markdown()`` escapes text a CommonMark reader would parse as markup: list-item line starts such as ``1.``, ``~``,
  ``<`` and ``&`` before markup, a heading's trailing ``#``, and unbalanced parentheses in link destinations.
  (:pr:`842`) by :user:`gaborbernat`.
- Nodes adopted between ``parse_xml`` and HTML trees take the destination tree's naming and raw-text rules, and
  ``set_inner_html`` / ``insert_adjacent_html`` on an XML element use the XML fragment parser. (:pr:`843`) by
  :user:`gaborbernat`.
- Apply each input type's value sanitization in ``Element.form_data()`` (newlines stripped, ``url``/``email`` trimmed,
  invalid ``number`` and date values emptied, ``range`` clamped), normalize textarea line breaks, and skip controls
  inside ``<datalist>``. (:pr:`844`) by :user:`gaborbernat`.
- Stop a ``rowspan`` at the end of its ``<thead>``/``<tbody>``/``<tfoot>`` in ``Element.rows()``, ``records()``, and
  ``Node.tables()``, and return ``<tfoot>`` rows last, as the WHATWG table model does. (:pr:`845`) by
  :user:`gaborbernat`.
- Type ``Node.xpath()``, ``xpath_one()``, ``xpath_iter()``, and ``XPath.__call__`` with the full result union: numbers,
  booleans, and strings from scalar expressions, and any node kind in a node-set. (:pr:`846`) by :user:`gaborbernat`.
- List a microdata value once per property name when an ``itemprop`` attribute repeats a name, as in ``itemprop="a a"``.
  (:pr:`847`) by :user:`gaborbernat`.
- Skip a JSON-LD block that uses the non-JSON ``NaN``, ``Infinity``, or ``-Infinity`` literals in
  ``Document.json_ld()``, as the docs promise for invalid JSON, instead of returning ``nan`` or ``inf``. (:pr:`848`) by
  :user:`gaborbernat`.
- XPath ``round()`` no longer rounds up the largest doubles below a half or integers past 2^52, and returns negative
  zero for arguments in ``[-0.5, 0)``; ``substring()`` positions round the same way. (:pr:`849`) by :user:`gaborbernat`.
- Pickling a ``Document``, fragment, or ``ShadowRoot`` rebuilds its structure instead of reparsing serialized markup:
  doctype identifiers, quirks mode, adjacent text, template contents, and foreign-element case survive, and elements
  holding a ``<template>`` pickle again. (:pr:`850`) by :user:`gaborbernat`.
- Stop ``Html(layout=Indent())`` from adding whitespace inside inline content, which changed the rendered text
  (``<p>a<b>b</b>c</p>`` reparsed as "a b c"); it now breaks lines only between block-level elements. (:pr:`851`) by
  :user:`gaborbernat`.
- XPath ``lang()`` and CSS ``:lang()`` honor ``xml:lang``: an XML tree reads it, and on an HTML tree it overrides
  ``lang`` on SVG and MathML elements. ``//@xml:lang`` now matches those attributes. (:pr:`852`) by :user:`gaborbernat`.
- Node insertion methods and ``Range.insert_node`` raise ``ValueError`` instead of putting a doctype outside a
  ``Document``, or a second doctype, a second root element, or a ``Text`` node directly into a ``Document``. (:pr:`853`)
  by :user:`gaborbernat`.
- Compare attribute values exactly on a :func:`~turbohtml.parse_xml` tree: ``[type=checkbox]`` no longer matches
  ``type="CheckBox"``, since the HTML case-insensitive value list applies to HTML documents only. (:pr:`854`) by
  :user:`gaborbernat`.
- Minify keeps the ``</p>``, ``</li>`` or ``</dd>`` of the last child of a phrasing parent such as ``<span>`` or
  ``<label>``, whose end tag does not close it on reparse. (:pr:`855`) by :user:`gaborbernat`.
- XPath number literals and ``number()`` convert decimals to the nearest double, so ``0.49999999999999994 < 0.5`` holds
  and ``number(string(x))`` round-trips. (:pr:`856`) by :user:`gaborbernat`.
- Inserting a range's contents or a ``ShadowRoot`` no longer links the fragment node itself into the tree; its children
  move into place and a shadow root stays attached to its host. (:pr:`857`) by :user:`gaborbernat`.
- Minify keeps the doctype's public and system identifiers and a ``</p>`` before a ``<table>`` in quirks mode, so the
  output reparses in the same document mode and to the same tree. (:pr:`858`) by :user:`gaborbernat`.
- Minify writes no ``</plaintext>`` and no end tag after a ``<plaintext>`` element, which the parser would read back as
  text, so minifying such a page is idempotent. (:pr:`859`) by :user:`gaborbernat`.
- ``Formatter.NAMED_ENTITIES`` writes ``&lang;`` and ``&rang;`` for U+27E8 and U+27E9, their HTML5 meaning, and keeps
  U+2329 and U+232A literal, so the output reparses to the same text. (:pr:`860`) by :user:`gaborbernat`.

.. _release-1-9-0:

*********************
 v1.9.0 (2026-09-14)
*********************

.. _release-1-9-0-added:

Added
=====

- Add native DOM whitespace collapse, comment removal, and ordered transformation composition. Serialize subtree
  children with ``inner=True`` on ``serialize``, ``encode``, and ``serialize_iter``. (:issue:`791`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of long guard-return sequences. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of declarations with single-use initializers.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up descendant ``:has()`` selectors on nested elements. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of libraries containing boolean literals.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`canonical serialization <turbohtml.Node.serialize>` with explicit options. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up importing turbohtml from an installed wheel. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``translate()`` with large character maps. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up sanitization of elements with many rejected attributes. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :class:`XSLT <turbohtml.transform.Transform>` numbering with explicit element, wildcard or document-root
  patterns. (:pr:`795`) by :user:`gaborbernat`.
- Speed up XML parsing of long text without character references. (:pr:`795`) by :user:`gaborbernat`.
- Speed up HTML parsing with deeply nested formatting elements. (:pr:`795`) by :user:`gaborbernat`.
- Speed up repeated :func:`computed-style <turbohtml.cssom.computed_style>` lookups on an unchanged tree. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up HTML parsing with repeated scope checks in nested structures. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation with inherited simple-type facets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up repeated named-slot assignment queries on wide shadow roots. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :func:`CSS minification <turbohtml.clean.minify_css>` with long conflicting declaration values. (:pr:`795`)
  by :user:`gaborbernat`.
- Speed up parent queries over selections with shared parents. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`HTML serialization <turbohtml.Node.serialize>` with explicit options. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up XML parsing of attributes with many namespace prefixes. (:pr:`795`) by :user:`gaborbernat`.
- Speed up generation of the :func:`Unicode normalization <turbohtml.detect.normalize>` tables. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`form-data collection <turbohtml.Element.form_data>` from deeply nested disabled fieldsets. (:pr:`795`)
  by :user:`gaborbernat`.
- Speed up :func:`language detection <turbohtml.detect.detect_language>` on text with many distinct trigrams.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up queries combining many roots supplied out of document order. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`article extraction <turbohtml.Node.article>` from deeply nested content. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` equality comparisons between node sets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up positional CSS queries over wide sibling lists. (:pr:`795`) by :user:`gaborbernat`.
- Speed up repeated default :class:`XSLT <turbohtml.transform.Transform>` ``level="any"`` numbering. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`computed-style <turbohtml.cssom.computed_style>` matching of class-qualified selectors. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` inequality comparisons between node sets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``str:replace()`` with sparse matches of long strings. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up HTML parsing of long text with carriage returns. (:pr:`795`) by :user:`gaborbernat`.
- Speed up HTML parsing that merges text around tables. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`Markdown wrapping <turbohtml.Node.to_markdown>` of long lines. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of mixed retained and removable declarations.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of elements with many instance attributes. (:pr:`795`)
  by :user:`gaborbernat`.
- Speed up :class:`RELAX NG <turbohtml.validate.RelaxNG>` validation of optional groups and interleaves. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`microdata extraction <turbohtml.Document.microdata>` when referenced properties are already ordered.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up closest-ancestor queries over overlapping selections. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:intersection()`` on large node sets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` that removes declarations from long statement
  lists. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``id()`` when its argument contains many short nodes. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up streaming rewrites that add many attributes to a start tag. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``str:concat()`` over many short text nodes. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up compilation of schema patterns with large character classes. (:pr:`795`) by :user:`gaborbernat`.
- Speed up external-link extraction from URLs with many subdomains. (:pr:`795`) by :user:`gaborbernat`.
- Speed up flattening nested shadow-slot assignments. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :class:`XSLT <turbohtml.transform.Transform>` applications with many template rules. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`canonical serialization <turbohtml.Node.serialize>` of deeply nested trees with xlink attributes.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up :func:`JavaScript literal propagation <turbohtml.clean.minify_js>` within large multi-binding declarations.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`plain-text serialization <turbohtml.Node.to_text>` with explicit options. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up repeated default :class:`XSLT <turbohtml.transform.Transform>` sibling numbering. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up adding many attributes to an element. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :func:`CSS minification <turbohtml.clean.minify_css>` with many rules sharing declaration bodies. (:pr:`795`)
  by :user:`gaborbernat`.
- Speed up mutations observed only for unrelated event kinds. (:pr:`795`) by :user:`gaborbernat`.
- Speed up repeated element path generation on an unchanged tree. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`table extraction <turbohtml.Node.tables>` with large row and column spans. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`Unicode normalization <turbohtml.detect.normalize>` of long combining-mark sequences. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up cloning partially selected :class:`Range <turbohtml.Range>` ancestors. (:pr:`795`) by :user:`gaborbernat`.
- Speed up serialization of elements with large attribute sets. (:pr:`795`) by :user:`gaborbernat`.
- Speed up publication-date extraction from text containing many distinct dates. (:pr:`795`) by :user:`gaborbernat`.
- Speed up pruning selections that share matching ancestors. (:pr:`795`) by :user:`gaborbernat`.
- Speed up shadow-slot assignment queries on hosts with many children. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`DOM normalization <turbohtml.Element.normalize>` of empty text nodes. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up reading attributes from tokens with many attributes. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :class:`Range <turbohtml.Range>` operations on fully contained sibling intervals. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of long expression sequences. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :class:`XSLT <turbohtml.transform.Transform>` numbering with static predicates and union patterns.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of values with named-type facets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :class:`XSLT <turbohtml.transform.Transform>` stylesheets with repeated numbering patterns. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` that merges long declaration lists. (:pr:`795`)
  by :user:`gaborbernat`.
- Speed up :func:`computed styles <turbohtml.cssom.computed_style>` for complex selector alternatives. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up repeated validation with schema patterns. (:pr:`795`) by :user:`gaborbernat`.
- Speed up queries combining many detached roots. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`Markdown conversion <turbohtml.Node.to_markdown>` of long runs of punctuation. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`Node.equals() <turbohtml.Node.equals>` for elements with many attributes. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`article extraction <turbohtml.Node.article>` from pages with many content candidates. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up XML parsing of elements with many distinct attributes. (:pr:`795`) by :user:`gaborbernat`.
- Speed up queries spanning many documents. (:pr:`795`) by :user:`gaborbernat`.
- Speed up JavaScript initialization-order checks across many variable pairs. (:pr:`795`) by :user:`gaborbernat`.
- Speed up explicit :class:`XSLT <turbohtml.transform.Transform>` numbering across repeated source-node visits.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:difference()`` on large node sets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript literal propagation <turbohtml.clean.minify_js>` across many interleaved declarations.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ordered numeric comparisons between node sets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of integer arrays. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of elements with many declared attributes. (:pr:`795`)
  by :user:`gaborbernat`.
- Speed up :class:`Range <turbohtml.Range>` operations whose boundary is near the start of a sibling list. (:pr:`795`)
  by :user:`gaborbernat`.
- Speed up iteration over :func:`SAX element records <turbohtml.saxparse.iter_events>`. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`encoding detection <turbohtml.detect.detect>` of short inputs. (:pr:`795`) by :user:`gaborbernat`.
- Speed up repeated document-wide radio-group updates. (:pr:`795`) by :user:`gaborbernat`.
- Speed up XML parsing of long attribute values without character references. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of decimals without numeric bounds. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:distinct()`` on large node sets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` equality comparisons of long strings. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`DOM normalization <turbohtml.Element.normalize>` of adjacent text nodes. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:has-same-node()`` on large node sets. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up HTML parsing with many active formatting elements. (:pr:`795`) by :user:`gaborbernat`.
- Speed up sibling queries over selections with shared parents. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :meth:`microdata extraction <turbohtml.Document.microdata>` with repeated item references. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`microdata extraction <turbohtml.Document.microdata>` when properties are local to their item.
  (:pr:`795`) by :user:`gaborbernat`.
- Speed up HTML parsing of long text containing isolated NUL characters. (:pr:`795`) by :user:`gaborbernat`.
- Speed up :func:`feed extraction <turbohtml.extract.feed>` from entries with extension fields. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` unions of results already in document order. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`CSS minification <turbohtml.clean.minify_css>` with many mergeable media blocks. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` with interleaved unused bindings. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :func:`DOM linkification <turbohtml.clean.linkify_node>` of long non-ASCII text. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :class:`XSLT <turbohtml.transform.Transform>` applications with many named declarations. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``translate()`` on text with repeated characters. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :class:`streaming encoding detection <turbohtml.detect.EncodingDetector>` of short inputs. (:pr:`795`) by
  :user:`gaborbernat`.
- Speed up :meth:`structured-data extraction <turbohtml.Document.structured_data>` from documents without metadata.
  (:pr:`795`) by :user:`gaborbernat`.
- Skip repeated NUL scans in :class:`~turbohtml.IncrementalParser`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up :func:`~turbohtml.treebuild.parse_into` by reusing namespace strings within each parse. (:pr:`799`) by
  :user:`gaborbernat`.
- Avoid repeated duplicate scans when :class:`~turbohtml.transform.Transform` indexes XSLT keys. (:pr:`799`) by
  :user:`gaborbernat`.
- Avoid shifting existing variable bindings when :class:`~turbohtml.transform.Transform` enters and leaves scopes.
  (:pr:`799`) by :user:`gaborbernat`.
- Speed up escaping disallowed tags with many attributes in :class:`~turbohtml.clean.Sanitizer`. (:pr:`799`) by
  :user:`gaborbernat`.
- Skip disqualified encoding candidates in :class:`~turbohtml.detect.EncodingDetector`. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up :attr:`~turbohtml.Node.text` by determining string width while collecting text. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up text emission in :class:`~turbohtml.transform.Transform`. (:pr:`799`) by :user:`gaborbernat`.
- Reduce rule matching work in :meth:`~turbohtml.Node.to_annotated_text` with many annotation rules. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up :func:`~turbohtml.conformance.check` on nested sections without headings. (:pr:`799`) by :user:`gaborbernat`.
- Speed up exact text filters in :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all`. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up :class:`~turbohtml.Element` construction. (:pr:`799`) by :user:`gaborbernat`.
- Speed up template-safe :func:`~turbohtml.clean.sanitize`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up Unicode range sorting in :func:`~turbohtml.clean.minify_css`. (:pr:`799`) by :user:`gaborbernat`.
- Skip impossible composition lookups in :func:`~turbohtml.detect.normalize`. (:pr:`799`) by :user:`gaborbernat`.
- Avoid redundant string copies and integer conversions in :class:`~turbohtml.transform.Transform` numeric sorting.
  (:pr:`799`) by :user:`gaborbernat`.
- Select only the highest-ranked trigrams when :func:`~turbohtml.detect.detect_language` scores text. (:pr:`799`) by
  :user:`gaborbernat`.
- Avoid temporary text copies when :meth:`~turbohtml.Node.xpath` concatenates node strings. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up zero terms with large exponents in :func:`~turbohtml.clean.minify_css`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up boolean attribute filters in :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all`. (:pr:`799`)
  by :user:`gaborbernat`.
- Avoid temporary function-rendering buffers in :func:`~turbohtml.clean.minify_css`. (:pr:`799`) by :user:`gaborbernat`.
- Avoid rescanning ASCII hosts in :func:`~turbohtml.extract.normalize_url`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up unescaped query keys in :func:`~turbohtml.extract.normalize_url`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up dot-segment handling in :func:`~turbohtml.extract.normalize_url`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up child and sibling ``:has()`` selectors in :meth:`~turbohtml.Node.select`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up :func:`~turbohtml.clean.sanitize` when checking allowed attribute prefixes. (:pr:`799`) by
  :user:`gaborbernat`.
- Use indexed country-code lookup when formatting and validating :class:`~turbohtml.clean.PhoneNumber`. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up phone table generation. (:pr:`799`) by :user:`gaborbernat`.
- Speed up Unicode normalization table generation. (:pr:`799`) by :user:`gaborbernat`.
- Skip unused attribute-name conversions in :class:`~turbohtml.clean.Sanitizer` prefix checks. (:pr:`799`) by
  :user:`gaborbernat`.
- Reuse namespace declarations for consecutive copies of a literal element in :class:`~turbohtml.transform.Transform`.
  (:pr:`799`) by :user:`gaborbernat`.
- Speed up literal conversion in :func:`~turbohtml.convert.css_to_xpath`. (:pr:`799`) by :user:`gaborbernat`.
- Skip unchanged prefixes in :func:`~turbohtml.detect.normalize`. (:pr:`799`) by :user:`gaborbernat`.
- Index Unicode digit ranges when parsing :class:`~turbohtml.clean.PhoneNumber`. (:pr:`799`) by :user:`gaborbernat`.
- Reuse each form's first submit control when matching ``:default`` in :meth:`~turbohtml.Node.select`. (:pr:`799`) by
  :user:`gaborbernat`.
- Stop temporal text scanning after the first valid date in :func:`~turbohtml.extract.dates`. (:pr:`799`) by
  :user:`gaborbernat`.
- Reuse adjacent sibling positions in :meth:`~turbohtml.Node.select` nth selectors. (:pr:`799`) by :user:`gaborbernat`.
- Use direct UTF decoders when :func:`~turbohtml.parse` reads UTF-8 or UTF-16 bytes. (:pr:`799`) by :user:`gaborbernat`.
- Speed up single-character renaming in :func:`~turbohtml.clean.minify_js`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up named-entity serialization with :class:`~turbohtml.Html`. (:pr:`799`) by :user:`gaborbernat`.
- Avoid intermediate metadata copies in :meth:`~turbohtml.Document.opengraph`. (:pr:`799`) by :user:`gaborbernat`.
- Speed up :func:`~turbohtml.treebuild.parse_into` by avoiding temporary text copies. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up attribute sorting with :class:`~turbohtml.Html` for elements with many attributes. (:pr:`799`) by
  :user:`gaborbernat`.
- Speed up :meth:`~turbohtml.validate.RelaxNG.is_valid` and :meth:`~turbohtml.validate.XMLSchema.is_valid` for invalid
  documents. (:pr:`799`) by :user:`gaborbernat`.
- Use direct combining-class lookup for common marks in :func:`~turbohtml.detect.normalize`. (:pr:`799`) by
  :user:`gaborbernat`.

.. _release-1-9-0-fixed:

Fixed
=====

- Avoid crashes when minifying detached subtrees containing adjacent ruby annotations or option groups. (:pr:`795`) by
  :user:`gaborbernat`.
- Avoid crashes and stale results when selector-cache cleanup callbacks move or modify the queried node. (:pr:`795`) by
  :user:`gaborbernat`.
- Preserve JavaScript initialization errors when a closure reads a ``let`` or ``const`` binding before initialization.
  (:pr:`795`) by :user:`gaborbernat`.
- Protect Query parent, child and sibling traversal against concurrent mutation of the same tree on free-threaded
  Python. (:pr:`795`) by :user:`gaborbernat`.
- Separate multi-level :class:`XSLT <turbohtml.transform.Transform>` numbers with periods when the format contains no
  separator token. (:pr:`795`) by :user:`gaborbernat`.
- Avoid invalid integer conversions when formatting large :meth:`XPath <turbohtml.Node.xpath>` numbers. (:pr:`795`) by
  :user:`gaborbernat`.
- Fix memory leaks when schema validation reads text and CDATA nodes. (:pr:`795`) by :user:`gaborbernat`.
- Keep unrelated benchmark inputs out of each timing worker's setup and report invalid inputs as errors. (:pr:`795`) by
  :user:`gaborbernat`.
- Fix crashes when annotation exporters consume generators that yield temporary labels. (:pr:`795`) by
  :user:`gaborbernat`.
- Keep a captured JavaScript ``var`` undefined when an earlier initializer invokes its closure. (:pr:`795`) by
  :user:`gaborbernat`.
- Preserve JavaScript initialization errors when a switch-case jump skips a binding's initializer. (:pr:`795`) by
  :user:`gaborbernat`.
- Release temporary validation buffers after each call when reusing an :class:`XSD <turbohtml.validate.XMLSchema>` or
  RELAX NG schema. (:pr:`795`) by :user:`gaborbernat`.
- Keep :class:`RELAX NG <turbohtml.validate.RelaxNG>` definition state separate between concurrent validations using the
  same schema. (:pr:`795`) by :user:`gaborbernat`.
- Preserve JavaScript initialization errors when a ``let`` or ``const`` binding is read before its initializer.
  (:pr:`795`) by :user:`gaborbernat`.
- Release temporary text buffers after :func:`boilerplate extraction <turbohtml.extract.boilerplate>`. (:pr:`795`) by
  :user:`gaborbernat`.
- Avoid concurrent writes to shared schema text during :class:`RELAX NG <turbohtml.validate.RelaxNG>` validation.
  (:pr:`795`) by :user:`gaborbernat`.
- Prevent repeated descendant ``:has()`` queries from growing retained cache memory without new entries. (:pr:`795`) by
  :user:`gaborbernat`.
- Prevent extra unwritten bytes in escaped output when escapable characters occupy adjacent byte positions. (:pr:`795`)
  by :user:`gaborbernat`.
- Handle empty leading text nodes when serializing ``pre``, ``textarea`` and ``listing`` elements. (:pr:`795`) by
  :user:`gaborbernat`.
- Avoid crashes during :meth:`form-data collection <turbohtml.Element.form_data>` when a garbage-collection callback
  detaches a control's ancestor. (:pr:`795`) by :user:`gaborbernat`.
- Include the ``of`` selector list when calculating ``:nth-child()`` and ``:nth-last-child()`` specificity. (:pr:`795`)
  by :user:`gaborbernat`.
- Propagate exceptions raised while looking up an object's ``__html__`` method during MarkupSafe-compatible escaping.
  (:pr:`795`) by :user:`gaborbernat`.
- Preserve JavaScript variable behavior when duplicate declarations include an uninitialized declaration. (:pr:`795`) by
  :user:`gaborbernat`.
- Prevent buffer overruns when :meth:`XPath <turbohtml.Node.xpath>` regular-expression flags contain repeated letters.
  (:pr:`795`) by :user:`gaborbernat`.
- Avoid concurrent writes while resolving the reserved ``xml`` namespace during schema validation. (:pr:`795`) by
  :user:`gaborbernat`.
- Preserve the receiver type in :meth:`~turbohtml.Node.strip_tags` annotations. (:issue:`796`) by :user:`gaborbernat`.
- Preserve the receiver type in :meth:`~turbohtml.Node.unwrap` annotations. (:issue:`796`) by :user:`gaborbernat`.
- Preserve the receiver type in :meth:`~turbohtml.Node.prune` annotations. (:issue:`796`) by :user:`gaborbernat`.
- Preserve the input node type in :func:`~turbohtml.clean.strip_comments_node` annotations. (:issue:`796`) by
  :user:`gaborbernat`.
- Preserve the receiver type in :meth:`~turbohtml.Node.extract` annotations. (:issue:`796`) by :user:`gaborbernat`.
- Preserve the input node type in :func:`~turbohtml.clean.sanitize_node` annotations. (:issue:`796`) by
  :user:`gaborbernat`.
- Preserve the wrapper type in :meth:`~turbohtml.Range.surround_contents` annotations. (:issue:`796`) by
  :user:`gaborbernat`.
- Preserve the input node type in :func:`~turbohtml.clean.sanitize_report_node` annotations. (:issue:`796`) by
  :user:`gaborbernat`.
- Preserve the input node type in :func:`~turbohtml.clean.linkify_node` annotations. (:issue:`796`) by
  :user:`gaborbernat`.
- Preserve the receiver type in :meth:`~turbohtml.Node.remove` annotations. (:issue:`796`) by :user:`gaborbernat`.
- Infer :func:`~turbohtml.clean.transform_node` results from the input and callback types. (:issue:`796`) by
  :user:`gaborbernat`.
- Preserve the input node type in :func:`~turbohtml.clean.collapse_whitespace_node` annotations. (:issue:`796`) by
  :user:`gaborbernat`.
- Preserve prefix encoding errors when sanitizing attributes without exact-name rules. (:pr:`799`) by
  :user:`gaborbernat`.
- Preserve numeric mantissas longer than 128 digits in :func:`~turbohtml.clean.minify_css`. (:pr:`799`) by
  :user:`gaborbernat`.
- Prevent numeric exponent overflow in :func:`~turbohtml.clean.minify_css`. (:pr:`799`) by :user:`gaborbernat`.
- Preserve benchmark notes for libraries that use different labels across operations in migration tables. (:pr:`799`) by
  :user:`gaborbernat`.
- Bound decimal expansion of exponent notation in :func:`~turbohtml.clean.minify_css`. (:pr:`799`) by
  :user:`gaborbernat`.

.. _release-1-8-0:

*********************
 v1.8.0 (2026-09-08)
*********************

.. _release-1-8-0-added:

Added
=====

- ``PhoneNumbers(collapse_whitespace=True)`` reads a run of HTML whitespace between the parts of a number as the one
  space it renders as, so a number a source formatter or a template broke across a line still links. (:issue:`758`) by
  :user:`gaborbernat`.
- Detect a URL whose authority is a single-label host or an IP literal when its scheme is written, so
  ``http://localhost:8000/path``, ``http://intranet/`` and ``http://[::1]:8080/`` link. A bare domain still needs a dot
  and a known top-level domain to be told apart from an ordinary word. Recognize an internationalized top-level domain
  written as its Unicode label, so ``президент.рф`` links the way ``президент.xn--p1ai`` already did. (:pr:`768`) by
  :user:`gaborbernat`.
- Add ``unique=True`` to :meth:`LinkDetector.find <turbohtml.clean.LinkDetector.find>`, which keeps the first span of
  each distinct URL. (:pr:`770`) by :user:`gaborbernat`.
- Move the work behind the shipped link callbacks :func:`~turbohtml.clean.nofollow` and
  :func:`~turbohtml.clean.target_blank` into the C core. They keep their names, their signatures and their behavior,
  with one correction: the web-scheme test now matches the whole scheme, so ``ht:`` and ``httpx:`` no longer count as
  web links. (:pr:`774`) by :user:`gaborbernat`.
- Move the :class:`~turbohtml.query.Query` facade's set algebra into the C core: deduplicating by node identity,
  collecting siblings, the combined ``text``, the joined attribute read, and the four class operations. Behavior is
  unchanged. (:pr:`775`) by :user:`gaborbernat`.
- Move the encoding detector's candidate ranking into the C core: deduplicating the scored candidates, normalizing each
  score to its share, promoting the detector's own winner, and applying the :class:`~turbohtml.detect.Detection`
  allowlist, exclusions, language preference and confidence floor. Results are unchanged. (:pr:`776`) by
  :user:`gaborbernat`.
- Move the boilerplate classifier into the C core: segmenting a page into paragraph units, collapsing each unit's text,
  and deciding which units are article content against the :class:`~turbohtml.extract.Extraction` thresholds. Results
  are unchanged. (:pr:`777`) by :user:`gaborbernat`.
- Move the structured-data shaping into the C core: which JSON-LD blocks carry data, and rendering a
  :class:`~turbohtml.MicrodataItem` as nested plain dicts. Decoding each block stays with the standard library's JSON
  parser. Results are unchanged. (:pr:`778`) by :user:`gaborbernat`.
- Move the publication-date stages into the C core: the canonical-URL, ``<meta>``, JSON-LD, ``<time>`` and visible-text
  signals :func:`~turbohtml.extract.dates` reads now run in one walk each. Results are unchanged. (:pr:`779`) by
  :user:`gaborbernat`.
- Move the URL cleaning pipeline into the C core: :func:`~turbohtml.extract.normalize_url`,
  :func:`~turbohtml.extract.clean_url` and :func:`~turbohtml.extract.extract_links` now run their normalization, gates
  and anchor walk in one C call each. Results are unchanged. (:pr:`780`) by :user:`gaborbernat`.
- Move the conformance verdict and the severity views into the C core: :func:`~turbohtml.conformance.check` reads
  ``valid`` from the walk, and ``errors``/``warnings``/``infos`` filter there. Results are unchanged. (:pr:`781`) by
  :user:`gaborbernat`.
- Move the query facade's traversal into the C core: :meth:`~turbohtml.query.Query.parent`,
  :meth:`~turbohtml.query.Query.children` and :meth:`~turbohtml.query.Query.closest` walk there, and a
  :class:`~turbohtml.query.Matcher` applies its ``limit`` and filters a node's children inside the walk. Results are
  unchanged. (:pr:`782`) by :user:`gaborbernat`.
- Move :class:`~turbohtml.cssom.StyleDeclaration`'s accessors into the C core: which declaration wins a repeated
  property and the ``text`` serialization are computed there. Results are unchanged. (:pr:`783`) by :user:`gaborbernat`.
- Move the encoding and language detectors' last decisions into the C core: which label a ``whatwg-*`` codec name
  resolves to, when a byte-order mark settles an :class:`~turbohtml.detect.EncodingDetector`, the no-match answer, and
  the language confidence floor. Results are unchanged. (:pr:`784`) by :user:`gaborbernat`.
- Move the sanitizer's policy compilation into the C core: the ``rel`` value, the value allowlists, the style patterns
  and the transform rules a :class:`~turbohtml.clean.Sanitizer` indexes are built there. Results are unchanged.
  (:pr:`785`) by :user:`gaborbernat`.
- Move the bleach shim's ``attributes`` translation into the C core: the flat, per-tag and callable shapes of
  :func:`turbohtml.migration.bleach.clean` compile there, and the per-tag predicates run through a C-bound filter.
  Results are unchanged. (:pr:`786`) by :user:`gaborbernat`.
- Move the link and phone detectors' configuration folding into the C core: tag, top-level-domain, scheme and word
  lists, region codes, the phone type mask and the E.164 check of :class:`~turbohtml.clean.PhoneNumber` are computed
  there. Results are unchanged. (:pr:`787`) by :user:`gaborbernat`.
- Move the whitespace fold of :meth:`turbohtml.migration.markupsafe.Markup.striptags` into the C core. Results are
  unchanged. (:pr:`789`) by :user:`gaborbernat`.
- Move :mod:`turbohtml.build`'s argument sorting into the C core: a leading mapping becomes the attributes and a string
  becomes a text node there, for :data:`~turbohtml.build.E` and :func:`~turbohtml.build.document` alike. Results are
  unchanged. (:pr:`790`) by :user:`gaborbernat`.
- Add :func:`~turbohtml.clean.sanitize_node` and :func:`~turbohtml.clean.sanitize_report_node`, the tree-to-tree
  sanitizer for a pipeline that parses once and serializes once; :func:`~turbohtml.clean.sanitize` and
  :func:`~turbohtml.clean.sanitize_report` also accept a parsed node. (:pr:`793`) by :user:`gaborbernat`.
- Add :func:`~turbohtml.clean.linkify_node`, which links URLs, email addresses and phone numbers in an already parsed
  tree in place; :func:`~turbohtml.clean.linkify` also accepts a parsed node. (:pr:`794`) by :user:`gaborbernat`.

.. _release-1-8-0-fixed:

Fixed
=====

- Escape a ``|`` where turbohtml writes a Markdown table cell's content rather than over the finished cell, so a nested
  table no longer adds a backslash per level; a table, list or ``<br>`` in a cell keeps its HTML unless
  ``Markdown.Tables(cell_blocks="text")``. (:issue:`764`) by :user:`gaborbernat`.
- Leave a URL whose scheme syntax is malformed entirely plain instead of linking its host on its own, so
  ``https:/nonsense.com`` no longer renders as ``https:/<a href="http://nonsense.com">nonsense.com</a>``. Link a written
  ``mailto:`` URI as one anchor covering its own scheme. (:pr:`766`) by :user:`gaborbernat`.
- End a host before a trailing hyphen rather than dropping the whole link, so ``http://example.com-`` links
  ``http://example.com``. Treat a port outside 0-65535 as text, and stop reading an underscore in a host's last two
  labels as a bare domain, so ``_example.com`` stays plain while the ``_dmarc.example.com`` of a DNS record still links.
  (:pr:`767`) by :user:`gaborbernat`.
- A ``<script type="application/ld+json">`` block that the decoder cannot process for a reason other than malformed
  JSON, such as nesting past the interpreter's recursion budget, now raises from :meth:`~turbohtml.Document.json_ld` and
  :func:`~turbohtml.extract.dates` instead of being skipped. (:pr:`788`) by :user:`gaborbernat`.

.. _release-1-8-0-documentation:

Documentation
=============

- Add a :doc:`migration guide from urlextract <migration/urlextract>`, which maps its ``find_urls``/``has_urls`` surface
  onto :class:`~turbohtml.clean.LinkDetector` and states what turbohtml does not do: no DNS check, no bare IP addresses,
  no tunable stop characters, and no runtime download of the top-level-domain list. (:pr:`772`) by :user:`gaborbernat`.

.. _release-1-7-0:

*********************
 v1.7.0 (2026-08-29)
*********************

.. _release-1-7-0-added:

Added
=====

- Link phone numbers as ``tel:`` anchors with a :class:`~turbohtml.clean.PhoneNumbers` setting; each match carries a
  :class:`~turbohtml.clean.PhoneNumber`, the class that parses and formats numbers. (:issue:`758`) by
  :user:`gaborbernat`.

.. _release-1-6-1:

*********************
 v1.6.1 (2026-08-15)
*********************

.. _release-1-6-1-fixed:

Fixed
=====

- Parse a document, or feed a first stream chunk, that opens with a carriage return without touching the not yet
  allocated input buffer; UndefinedBehaviorSanitizer reported the empty append as a zero offset applied to a null
  pointer. (:pr:`752`) by :user:`gaborbernat`.

.. _release-1-6-0:

*********************
 v1.6.0 (2026-08-11)
*********************

.. _release-1-6-0-added:

Added
=====

- Compile reusable XSLT stylesheet state during :class:`~turbohtml.transform.Transform` construction. (:issue:`713`) by
  :user:`gaborbernat`.
- Add ``allow_imports`` and ``import_root`` controls for XSLT import filesystem access. (:issue:`714`) by
  :user:`gaborbernat`.

.. _release-1-6-0-fixed:

Fixed
=====

- Process ``</p>`` and ``</br>`` as foreign-content breakout tokens so the resulting HTML elements follow the foreign
  root, matching the HTML standard and WPT. (:issue:`32`) by :user:`gaborbernat`.
- Recheck sanitizer callback and forced-attribute rewrites against the mandatory baseline safety rules. (:issue:`708`)
  by :user:`gaborbernat`.
- Reject SVG animation elements that can assign event handlers or script URLs at runtime. (:issue:`709`) by
  :user:`gaborbernat`.
- Make XML tag-name queries case-sensitive, including names shared with known HTML tags. (:issue:`710`) by
  :user:`gaborbernat`.
- Stop :meth:`~turbohtml.clean.LinkDetector.has_link` after the first match without allocating a span list.
  (:issue:`711`) by :user:`gaborbernat`.
- Avoid building a whole-tree tag index for first-result and small-limit queries. (:issue:`712`) by :user:`gaborbernat`.
- Move HTML-aware linkifier traversal and mutation into one native operation. (:issue:`715`) by :user:`gaborbernat`.
- Parse living-standard HTML processing instructions as typed nodes and events while keeping reserved XML targets as
  comments. (:issue:`716`) by :user:`gaborbernat`.
- Apply the living-standard ``keygen`` rules inside ``select`` elements. (:issue:`717`) by :user:`gaborbernat`.
- Keep repeated ``nobr`` elements as siblings across table formatting scope. (:issue:`718`) by :user:`gaborbernat`.
- Decode CSS escapes when checking sanitizer function names and URL schemes. (:issue:`721`) by :user:`gaborbernat`.
- Complete deep XML and programmatic-tree operations without silent truncation or C stack exhaustion. Bound recursive
  Microdata and RDFa records at 400 levels, reject cyclic Microdata ``itemref`` graphs, and read combined metadata from
  one tree snapshot. (:issue:`722`) by :user:`gaborbernat`.
- Remove a redundant :class:`list` cast from :meth:`turbohtml.query.Matcher.filter` under current ``ty`` releases.
  (:issue:`738`) by :user:`gaborbernat`.
- Report unacknowledged self-closing start tags as HTML parse errors. (:issue:`743`) by :user:`gaborbernat`.

.. _release-1-5-1:

*********************
 v1.5.1 (2026-07-27)
*********************

.. _release-1-5-1-fixed:

Fixed
=====

- Match tag and attribute names case-sensitively on a :func:`turbohtml.parse_xml` document: an uppercase name like ``Y``
  reads through :attr:`~turbohtml.Element.attrs` instead of raising :exc:`KeyError`, CSS selectors tell ``[Attr]`` from
  ``[attr]`` and ``Child`` from ``child``, and :func:`pickle.loads`/:func:`copy.deepcopy` round-trip the tree without
  folding its names to the HTML parser's lowercase. (:issue:`695`) by :user:`gaborbernat`.
- Normalize line endings in a :func:`turbohtml.parse_xml` document per XML 1.0 §2.11 across attribute values, CDATA
  sections, comments, and processing instructions: a literal ``CRLF`` folds to a single ``LF`` (one space in an
  attribute value) rather than two, matching the text-content path and every other conformant XML parser. (:pr:`696`) by
  :user:`gaborbernat`.

.. _release-1-5-0:

*********************
 v1.5.0 (2026-07-21)
*********************

.. _release-1-5-0-added:

Added
=====

- Speed the C core across the read, query, validation, and extraction paths. Measured against the 1.4.0 release on a
  fixed workload, an id-selective XPath lookup runs about 260x faster, overlapping text search over 100x faster, Unicode
  normalization about 6x, microdata itemref resolution about 5x, Relax NG compilation about 4x, and CSS minification,
  conformance checking, attribute rewriting, date extraction, and computed-style resolution roughly 2x to 3x. Linear
  table scans give way to hashed or indexed lookups for tag atoms, XPath id tokens, microdata itemrefs, the Relax NG and
  XSD global tables, the encoding labels, and the entity names (:pr:`659`, :pr:`672`, :pr:`671`, :pr:`657`, :pr:`656`,
  :pr:`661`, :pr:`660`, :pr:`651`, :pr:`654`). The substring and literal-regex searches, the transform sort, and the CSS
  rule merge now bound their worst case instead of scanning to the end (:pr:`665`, :pr:`664`, :pr:`666`, :pr:`652`,
  :pr:`662`). The tokenizer name buffers, the table grid rows, and the DOM arena grow by doubling so a large document
  amortizes its allocation (:pr:`669`, :pr:`668`, :pr:`667`). The GB18030 decoder, the NFC normalizer, the attribute
  rewriter, and the IDNA mapper drop the branchy table walks that a common input never needed (:pr:`674`, :pr:`673`,
  :pr:`649`, :pr:`670`, :pr:`650`). Stylesheets cache their parse, list filtering batches, the language detector merges
  its trigram profiles, and date extraction trims its scan (:pr:`653`, :pr:`663`, :pr:`648`, :pr:`658`). Serialize
  documents faster. The clean-run scan that hunts the next character needing an escape steps four UCS-4 code points per
  SIMD probe rather than two, and since the tree stores text as UCS-4 this scan runs on every serialization. The
  serialize benchmark improves by about 24%, and the gain carries into the strip and sanitize operations that serialize
  their result (:pr:`676`). IDNA host normalization rejects a non-composing mark pair with one comparison ahead of the
  composition-table search, saving the probes a run of unaccented text would otherwise spend (:pr:`680`, :pr:`679`).
  (:pr:`676`) by :user:`gaborbernat`.
- Number a run of siblings in one pass. ``xsl:number`` computes a level's number as the count of preceding siblings that
  match the count criteria, which rescanned the whole run for each sibling and cost time quadratic in the run length;
  ``number_counts`` was 10.13% of all instructions executed by the instruction-dense transform workload, the largest
  single entry ahead of every CPython symbol. The engine now carries the previous answer forward, since a node's number
  is its previous sibling's number plus whether that sibling counted, and holds the criteria alongside it so a run
  numbered under different criteria does not reuse it. Callgrind measures 5.9% fewer instructions over that workload
  (:issue:`687`). (:issue:`687`) by :user:`gaborbernat`.
- Dispatch the ``html.parser`` adapter's tokens from C. :class:`turbohtml.migration.stdlib.HTMLParser` used to loop in
  Python over the token stream, building a token object and reading six of its fields for every token; the tokenizer now
  calls the ``handle_*`` methods itself, binding them once per feed and passing the strings straight through. Under
  Callgrind that removes 30.8% of the instructions the whole workload executes and 46.7% of its indirect branches, which
  is what the interpreter spends on dispatch. The adapter now leads lxml's native target parser by 1.7 to 2.7 times
  where it previously trailed it, and html.parser by 7.5 to 11.2 times. (:pr:`689`) by :user:`gaborbernat`.
- Move the read path's per-item loops into C. :func:`turbohtml.saxparse.sax_parse` built a tuple, a typed record, and an
  isinstance chain for every event; the tokenizer now binds the six handler methods once and drives them off the tree
  walk, for 43.7% fewer instructions with instruction-cache misses down 71% on the WHATWG specification. URL cleaning
  (:func:`turbohtml.extract.clean_url`, :func:`~turbohtml.extract.normalize_url`,
  :func:`~turbohtml.extract.extract_links`), the :func:`~turbohtml.extract.dates` ``<meta>`` stage,
  :func:`turbohtml.query.escape_identifier`, and :func:`turbohtml.clean.linkify`'s scheme classification each moved
  their loops to C as well, for 3.5% to 56.8% fewer instructions on those operations. Differential runs against the
  retired Python across 1,484,640, 809,481, 360,282, 302,762, 38,590, and 4,828 inputs found one divergence: the URL
  resolver matches ``%2e`` in either case as the `WHATWG URL path state <https://url.spec.whatwg.org/#path-state>`_
  specifies, which no public call reaches because percent-encoding uppercases escape hex first.
  :func:`~turbohtml.saxparse.iter_events` keeps its typed records, since those are its public product. Other hot paths
  keep their Python entry and shed work. XSD validation resolves each schema element's qname once at compile time into a
  binary-searched table for 17.2% fewer instructions, built for XSD only after the eager cache slowed RELAX NG
  compilation by 8.15%. The link walk mints element wrappers on first use (14.8% fewer) and skips the URL join that
  already returns an absolute reference (67.8% fewer); the ``<meta>`` encoding prescan jumps inert bytes with ``memchr``
  (8.67% fewer on ASCII); the gb18030 decoder maps astral pointers by arithmetic (13.4% fewer); and CSS property
  dispatch gates on the first byte (9.3% fewer on ``bootstrap.css``). The migration and benchmark tables also stopped
  over-claiming: a caveat marks only the rows of the operation it describes, a shared measurement shows in both columns,
  XPath labels render as inline code, and the CSS-stripper note gives the measured 1-3% size difference. (:pr:`692`) by
  :user:`gaborbernat`.

.. _release-1-5-0-documentation:

Documentation
=============

- Refresh every benchmark comparison table in the performance and migration guides from a full profile-guided run, widen
  the read-path tables to the competitors the suites gained, and rewrite the surrounding prose to the measured numbers.
  (:pr:`686`) by :user:`gaborbernat`.

.. _release-1-4-0:

*********************
 v1.4.0 (2026-07-11)
*********************

.. _release-1-4-0-added:

Added
=====

- Add ``capture_attributes=False`` for tag-and-text token streams, and reduce DOM allocation for large documents.
  (:pr:`640`) by :user:`gaborbernat`.

.. _release-1-4-0-fixed:

Fixed
=====

- Keep a :class:`turbohtml.Node` hash stable across cross-document adoption so sets and dictionaries can still find it.
  (:pr:`634`) by :user:`gaborbernat`.
- Copy source subtrees from one state during DOM adoption, Range operations, and XSLT under free-threaded Python.
  (:pr:`635`) by :user:`gaborbernat`.
- Recognize XSLT instructions by namespace URI and local name, including stylesheets with a default XSLT namespace or
  rebound prefix. (:pr:`636`) by :user:`gaborbernat`.
- Raise :exc:`ValueError` with the import chain for circular ``xsl:import`` references. (:pr:`637`) by
  :user:`gaborbernat`.
- Return :meth:`turbohtml.query.Query.find` results in document order across connected roots; retain root input order
  across disconnected trees. (:pr:`638`) by :user:`gaborbernat`.

.. _release-1-3-1:

*********************
 v1.3.1 (2026-07-10)
*********************

.. _release-1-3-0:

*********************
 v1.3.0 (2026-07-10)
*********************

.. _release-1-3-0-added:

Added
=====

- Run the C core on PyPy 3.10 and 3.11 through ``cpyext``, with the same conformance and byte-identical output as
  CPython; see :doc:`/explanation/interpreters` for what ``cpyext`` costs and for the three behaviors that do not carry
  over. (:pr:`623`) by :user:`gaborbernat`.
- Decode the legacy encodings 1.2x to 1.5x faster by inlining each WHATWG decoder into its specialized loop, and stop
  reallocating :class:`turbohtml.IncrementalParser`'s decode scratch on every :meth:`~turbohtml.IncrementalParser.feed`.
  (:pr:`624`) by :user:`gaborbernat`.

.. _release-1-3-0-fixed:

Fixed
=====

- Raise :exc:`IndexError` for a negative subscript further back than a node's child count; ``node[-len - 1]`` answered
  the first child, where :class:`list` raises. (:pr:`623`) by :user:`gaborbernat`.
- Resume the ISO-2022-JP escape state across a :class:`turbohtml.IncrementalParser` chunk boundary. A chunk ending on an
  incomplete escape sequence dropped back to the output state, so the next chunk read the escape's first byte as content
  and lost one ``U+FFFD``. (:pr:`624`) by :user:`gaborbernat`.
- :func:`turbohtml.parse` no longer mojibakes two common inputs. Bytes that are valid UTF-8 but declare no encoding now
  decode as UTF-8 rather than reaching the windows-1252 fallback. Validity is a structural proof rather than a frequency
  guess, so it costs none of the candidate scoring ``detect_encoding=True`` adds, and pure ASCII still resolves to
  windows-1252, which decodes it to the same text. A ``<meta>`` charset sitting past the prescan's 1024-byte window now
  redoes the parse against what it declares, following the WHATWG "changing the encoding while parsing" step, so where
  the declaration sits in the document no longer changes the answer. Only a real ``<meta>`` element counts, so a charset
  written inside a ``<script>`` string cannot pose as one, and a byte-order mark or the ``encoding`` argument still
  settles the encoding. :attr:`turbohtml.Document.encoding_confidence` reports which step answered: ``"certain"`` when
  the document named its own encoding through a byte-order mark, the ``encoding`` argument, or a ``<meta>`` charset,
  ``"tentative"`` when the sniff guessed, and ``None`` for ``str`` input. :func:`turbohtml.detect.detect` reads bytes
  and has no tree to consult, so on a document whose only ``<meta>`` sits past the prescan window it stops at the
  prescan and can disagree with :func:`turbohtml.parse`. :attr:`turbohtml.Document.errors` reports every parse error the
  WHATWG tokenizer defines. It carried 8 of the 48 codes the algorithm names, so ``a\x00b`` and a malformed
  ``<!DOCTYPE>`` both parsed without a word. turbohtml now reports the other 45, along with the preprocessing errors a
  control, noncharacter, or surrogate code point raises by being in the input at all, interleaved with the tokenizer's
  in source order. Reading :attr:`~turbohtml.Document.errors` is what finds the preprocessing ones, so a parse that
  never asks for them does not pay to look. ``strict=True`` therefore raises on documents it used to accept, and
  :class:`turbohtml.IncrementalParser` keeps no source to walk, so it reports none of them. Nothing gated the ``errors``
  arrays the vendored ``html5lib-tests`` tokenizer suite carries; they now gate the codes and their order on all 7032
  cases, and the line and column on every BMP-only input. (:issue:`625`) by :user:`gaborbernat`.
- The encoding detector ignored the domain the bytes came from. chardetng, which ``_c/encoding/detect.h`` ports, takes
  the host's rightmost DNS label and reads it three ways: the encoding that label expects gains a point, the encodings
  native to it keep their score, and the rest pay a penalty. The port dropped all three, so turbohtml scored a Czech
  page served from a ``.ru`` domain the same as one served from ``.cz``. :class:`turbohtml.detect.Detection` now carries
  a ``tld`` field that reaches the scoring, and leaving it unset detects as before. A differential test runs both
  implementations over all 133 labels chardetng's classifier carries. :class:`turbohtml.detect.EncodingDetector` no
  longer buffers the stream. It appended every chunk to a ``bytearray`` and detected once over the concatenation at
  :meth:`~turbohtml.detect.EncodingDetector.close`, so a caller streaming a large file paid its length in memory, in a
  class documented as mirroring chardet's ``UniversalDetector``. Each candidate now carries its own state between feeds,
  and the only bytes the detector keeps are the leading 1024 the byte-order-mark check and the ``<meta>`` prescan read,
  which the spec bounds. Where the chunks fall does not change the answer: the stateful detector is the only
  implementation, so :func:`turbohtml.detect.detect` drives it with a single feed and the differential test against
  chardetng covers the scoring both paths share. A four-byte gb18030 sequence that resolved to no code point returned an
  error without naming the byte that caused it, leaving ``error_at`` at whatever the previous error had set. Reading a
  whole buffer at once never noticed, because the value that means "the input ended mid-sequence" is only ever set at
  the end. A resumed decoder did notice, and so would :class:`turbohtml.IncrementalParser`, which tells "wait for more
  input" from "this input is wrong" by that same value. (:issue:`627`) by :user:`gaborbernat`.
- Report parse errors from :class:`turbohtml.IncrementalParser`, which collected none: the streaming tokenizer never
  attached an error sink, and the preprocessing errors were found by a pass over a source no stream keeps. Each chunk is
  now swept as it arrives. (:issue:`629`) by :user:`gaborbernat`.
- Report a control character that follows a tab, form feed, or NUL inside the same eight bytes of input. The
  word-at-a-time skip built its mask from a borrow-based test whose bits are not exact per byte, so an ordinary low byte
  erased its neighbor's error. (:issue:`630`) by :user:`gaborbernat`.

.. _release-1-3-0-documentation:

Documentation
=============

- Explain what running the C core on PyPy costs, in :doc:`/explanation/interpreters`, from a table the benchmark harness
  generates (``tox -e bench -- interpreters``) rather than from figures typed into prose. How the core adapts to
  ``cpyext`` moved to :doc:`/development/cpyext`, alongside the invariant that keeps CPython's machine code unchanged.
  (:pr:`623`) by :user:`gaborbernat`.

.. _release-1-3-0-packaging:

Packaging
=========

- Publish PyPy 3.10 and 3.11 wheels (``pp310``, ``pp311``) for Linux, macOS, and Windows. They carry LTO but skip the
  profile-guided optimization the CPython Linux wheels use, since a profile trained under PyPy measures ``cpyext``
  rather than the C hot paths. (:pr:`623`) by :user:`gaborbernat`.

.. _release-1-2-0:

*********************
 v1.2.0 (2026-07-09)
*********************

.. _release-1-2-0-added:

Added
=====

- Add :attr:`EncodingMatch.codec <turbohtml.detect.EncodingMatch>`; ``data.decode(match.codec)`` reproduces what
  :func:`turbohtml.parse` saw, where ``match.encoding`` corrupted or raised. (:pr:`622`) by :user:`gaborbernat`.

.. _release-1-2-0-fixed:

Fixed
=====

- Lock free-threaded DOM tree reads and attribute views; concurrent mutation no longer crashes those readers.
  (:pr:`617`) by :user:`gaborbernat`.
- Resolve local ``file://`` stylesheet URLs for ``xsl:import``; ``Path.as_uri()`` bases now load sibling imports.
  (:pr:`618`) by :user:`gaborbernat`.
- Normalize configured ``Linkify.skip_tags`` names before matching parsed HTML tags; ``CODE`` now skips ``<code>`` text.
  (:pr:`619`) by :user:`gaborbernat`.
- Keep controls inside the first ``<legend>`` child of a disabled ``<fieldset>`` in ``form_data()`` output. (:pr:`621`)
  by :user:`gaborbernat`.
- Decode legacy bytes with the WHATWG decoders rather than CPython's same-named codecs, whose tables and error handling
  both differ: ``koi8-u`` is KOI8-RU, and GBK ``0x80`` is the euro sign. The ``<meta>`` prescan, the content detector,
  and :class:`~turbohtml.IncrementalParser` now follow the spec too. (:pr:`622`) by :user:`gaborbernat`.

.. _release-1-2-0-breaking-changes:

Breaking changes
================

- :func:`turbohtml.detect.detect` reports ``windows-1252``, not ``ascii``, for pure-ASCII input;
  :class:`~turbohtml.detect.EncodingMatch` gained a trailing ``codec`` field; and :class:`~turbohtml.IncrementalParser`
  takes WHATWG labels, so ``latin-1`` raises where ``iso-8859-1`` works. (:pr:`622`) by :user:`gaborbernat`.

.. _release-1-1-1:

*********************
 v1.1.1 (2026-07-08)
*********************

.. _release-1-1-0:

*********************
 v1.1.0 (2026-07-08)
*********************

.. _release-1-1-0-added:

Added
=====

- :class:`~turbohtml.clean.Policy` gained ``strip_template_markers``: with it on, sanitizing collapses template-engine
  expressions (``{{ }}``, ``${ }``, ``<% %>``) in kept text and attribute values to a single space, so the output cannot
  re-inject when a template engine renders it. This matches DOMPurify's ``SAFE_FOR_TEMPLATES``. (:issue:`527`) by
  :user:`gaborbernat`.
- :func:`turbohtml.clean.sanitize_report` (and :meth:`turbohtml.clean.Sanitizer.sanitize_report`) sanitize a fragment
  and return what the policy dropped alongside the cleaned HTML: one :class:`turbohtml.clean.Removed` record per removed
  element or stripped attribute, in walk order. This matches DOMPurify's ``DOMPurify.removed``. (:issue:`528`) by
  :user:`gaborbernat`.
- :func:`turbohtml.convert.css_specificity` returns the ``(a, b, c)`` specificity of each selector in a comma-separated
  list, per CSS Selectors Level 4 §17, the value ``cssselect`` exposes as ``Selector.specificity()``. It weighs the
  parsed selector in one C pass, with ``:is()``/``:not()``/``:has()`` taking their most specific argument and
  ``:where()`` contributing zero. (:issue:`529`) by :user:`gaborbernat`.
- :func:`turbohtml.extract.feed` normalizes an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one frozen, typed
  :class:`~turbohtml.extract.Feed` of :class:`~turbohtml.extract.Entry` records, the ``feedparser.parse`` entry point
  over :meth:`turbohtml.Document.feed`. It detects the format from the root element and maps each dialect's spelling of
  a field -- the entry ``title``, ``link``, ``id``, ``updated``/``published``, ``summary``/``content``, and ``author``
  -- onto one shape in a single C walk of the parsed tree, over 12x faster than feedparser on a 30-item feed.
  (:issue:`530`) by :user:`gaborbernat`.
- :class:`turbohtml.clean.Policy` gains ``transform_tags``: a map that renames elements while sanitizing,
  sanitize-html's ``transformTags``. Map a source tag to a string to rename it, or to a
  :class:`turbohtml.clean.Transform` to rename it and add attributes (sanitize-html's ``simpleTransform``). The rename
  runs before the allowlist in the same C walk, so the renamed element is re-checked from scratch -- a transform decides
  an element's name but never its safety: mapping a tag to ``script`` still drops it, and an added attribute is scrubbed
  like the element's own. (:issue:`531`) by :user:`gaborbernat`.
- A new :mod:`turbohtml.saxparse` module adds a DOM-less, event-driven parse. :func:`turbohtml.saxparse.sax_parse`
  drives a document through the WHATWG tree builder and fires a callback on a :class:`turbohtml.saxparse.SaxHandler`
  subclass for each construct it builds -- a start tag, an end tag, a run of text, a comment, the doctype, and a
  ``<?...>`` processing instruction -- while :func:`turbohtml.saxparse.iter_events` yields the same stream as typed
  records. The events reflect the fully spec-correct tree (implied ``html``/``head``/``body``, foster parenting, the
  adoption agency), so unlike :class:`html.parser.HTMLParser` you see the tree the parser built; no per-node Python
  object is created and nothing is retained after the parse, so a one-pass extraction never builds a document-sized
  object graph. The tokenization, tree construction, and walk all run in C. (:issue:`532`) by :user:`gaborbernat`.
- ``turbohtml.clean.Policy`` gains ``allowed_styles``, a per-element, per-property value allowlist for the ``style``
  attribute keyed ``{tag: {property: [pattern, ...]}}`` with ``"*"`` matching every tag. A declaration survives only
  when its value matches one of the property's patterns, porting sanitize-html's ``allowedStyles``. It narrows
  ``css_properties`` by value and never weakens the baseline that drops ``expression()`` and disallowed-scheme
  ``url()``. (:issue:`533`) by :user:`gaborbernat`.
- :class:`~turbohtml.clean.Policy` gained ``isolate_named_props``: with it on, sanitizing prefixes every kept ``id`` and
  ``name`` value with ``user-content-``, moving it out of the property namespace so it cannot shadow a built-in
  ``document`` or form property through named access (DOM clobbering, where ``<input name="attributes">`` makes
  ``form.attributes`` resolve to the input and ``<img name="body">`` hides ``document.body``). An already-prefixed value
  is left alone, so re-sanitizing is a fixpoint. This matches DOMPurify's ``SANITIZE_NAMED_PROPS``. (:issue:`534`) by
  :user:`gaborbernat`.
- :class:`~turbohtml.Html` gained ``xml``: with ``xml=True``, :meth:`~turbohtml.Node.serialize`,
  :meth:`~turbohtml.Node.encode`, and :meth:`~turbohtml.Node.serialize_iter` emit XML/XHTML instead of HTML -- the
  equivalent of lxml's ``tostring(method="xml")``. Every empty element self-closes (``<br/>``), foreign SVG and MathML
  subtrees carry their namespace declarations, and text and attribute values follow the XML escaping rules, with no HTML
  void-element or raw-text special casing. It composes with ``sort_attributes`` and an :class:`~turbohtml.Indent`
  layout. (:issue:`535`) by :user:`gaborbernat`.
- :class:`~turbohtml.clean.Policy` gained a predicate-based custom-element allowance and split content profiles, porting
  DOMPurify's ``CUSTOM_ELEMENT_HANDLING`` and ``USE_PROFILES``. ``custom_element_check`` keeps an unlisted hyphenated
  custom element (``my-widget``, ``x-card``) when a caller-supplied matcher admits its name, ``custom_attribute_check``
  extends the same idea to that element's attributes, and ``allow_customized_builtins`` keeps an ``is`` attribute whose
  value names a custom element. ``allow_html``, ``allow_svg``, and ``allow_mathml`` gate each namespace independently,
  so a policy can keep SVG but drop MathML, or the reverse. All of it runs in the one C sanitize walk, and the
  non-configurable safety baseline -- ``on*`` handlers, ``javascript:`` URLs, unsafe tags -- still applies to whatever a
  matcher keeps. (:issue:`536`) by :user:`gaborbernat`.
- :mod:`turbohtml.transform` adds a full XSLT 1.0 processor, the job `lxml <https://lxml.de>`_'s ``etree.XSLT`` does.
  :class:`turbohtml.transform.Transform` compiles a stylesheet (parsed with :func:`turbohtml.parse_xml`) and applies it
  to source documents, and :func:`turbohtml.transform.transform` does both in one call. The whole transform runs in the
  C extension, reusing turbohtml's XPath 1.0 engine for every match pattern and select expression. It covers the entire
  XSLT 1.0 instruction set: templates with ``match``/``name``/``mode``/``priority``, ``apply-templates`` with ``sort``
  and ``with-param``, ``call-template``, ``for-each``, ``if``, ``choose``, ``value-of``, ``copy``/``copy-of``,
  ``element``/ ``attribute``/``text``, ``variable``/``param``, multi-level ``number``, ``key`` with the ``key()``
  function, ``strip-space``/``preserve-space``, ``attribute-set`` with ``use-attribute-sets``, ``namespace-alias``,
  ``fallback``, simplified literal-result-element stylesheets, ``xsl:import`` with import precedence (resolved against a
  ``base_url``), ``cdata-section-elements``, and the ``xml``/``html``/``text`` output methods (html auto-selected for a
  null-namespace ``html`` root, with ``<meta>`` injection). Validated against libxslt's XSLT 1.0 Recommendation corpus
  at 76 of 79 cases byte-for-byte; the three remaining need a locale-collation, DTD, or XPath-namespace-axis layer
  turbohtml does not carry. (:issue:`537`) by :user:`gaborbernat`.
- :meth:`turbohtml.Node.canonicalize` serializes a subtree to Canonical XML (c14n), the byte-exact form an XML signature
  signs. A :class:`turbohtml.Canonical` config selects the algorithm: Canonical XML 1.0 or 1.1, the exclusive variant
  that renders only the namespaces a subtree visibly uses, the with-comments variant, and an ``inclusive_ns_prefixes``
  prefix list for exclusive mode. Attributes are reordered (namespace declarations first, then by namespace URI and
  local name), redundant namespace declarations are dropped, empty elements are written as start-end pairs, and
  character references are normalized, matching ``lxml``'s ``tostring(method="c14n")`` byte-for-byte over the same
  infoset. (:issue:`538`) by :user:`gaborbernat`.
- :class:`turbohtml.validate.XMLSchema` and :class:`turbohtml.validate.RelaxNG` validate a document parsed with
  :func:`turbohtml.parse_xml` against an XSD 1.0 or RELAX NG schema, mirroring lxml's ``etree.XMLSchema`` /
  ``etree.RelaxNG``. A schema compiles once in the C core and each :meth:`~turbohtml.validate.XMLSchema.validate`
  returns a :class:`~turbohtml.validate.ValidationResult` -- a ``valid`` flag plus one
  :class:`~turbohtml.validate.ValidationError` per violation, each with the document-order path that located it. XSD
  covers the element/attribute declarations, the sequence/choice/all content models with ``minOccurs``/``maxOccurs``,
  references, complex/simple types with extension, the built-in datatypes, and the constraining facets; RELAX NG covers
  the full XML-syntax pattern set (including ``interleave``) through the derivative algorithm. (:issue:`539`) by
  :user:`gaborbernat`.
- :func:`turbohtml.parse_xml` parses a document under XML 1.0 well-formedness instead of the WHATWG HTML tree builder,
  returning the same navigable :class:`~turbohtml.Document`. Names stay case-sensitive, ``<x/>`` self-closes any
  element, CDATA sections and processing instructions become :class:`~turbohtml.CData` and
  :class:`~turbohtml.ProcessingInstruction` nodes, only the five predefined entities and numeric references resolve, and
  a namespace prefix must be declared with ``xmlns``. Names follow the exact XML 1.0 ``NameStartChar`` /``NameChar``
  productions, and the Namespaces in XML 1.0 well-formedness constraints hold in full: the reserved ``xml`` and
  ``xmlns`` prefixes and their namespace names cannot be rebound, a prefix declaration cannot be empty, a
  processing-instruction target carries no colon, and no two attributes share an expanded name. There is no HTML
  recovery: the first well-formedness violation -- a mismatched or unclosed tag, an undeclared prefix, an undefined
  entity, a duplicate attribute -- raises :exc:`~turbohtml.HTMLParseError`. This is the equivalent of
  ``lxml.etree.fromstring`` / ``etree.XMLParser`` over turbohtml's dependency-free, fully typed node API. (:issue:`540`)
  by :user:`gaborbernat`.
- Added an HTML5 authoring-conformance checker with a severity model. :func:`turbohtml.conformance.check` walks a parsed
  document and returns a :class:`~turbohtml.conformance.ConformanceReport` -- a ``valid`` verdict plus every
  :class:`~turbohtml.conformance.ConformanceMessage`, each carrying a stable ``code``, a ``severity`` (``"error"``,
  ``"warning"``, or ``"info"``), a human-readable message, and a source line and column. It flags the
  document-conformance requirements the parser does not raise as a :class:`~turbohtml.ParseError`: a missing ``img``
  alt, obsolete presentational elements and attributes, duplicate ids, invalid or redundant ARIA roles, empty headings,
  a ``section`` without a heading, and a document with no title or ``lang``. The document is valid exactly when nothing
  is an error, so warnings and info notes never change the verdict. The whole walk runs in the C core against the WHATWG
  authoring rules and WAI-ARIA 1.2, the model the Nu Html Checker (validator.nu) uses;
  :func:`~turbohtml.conformance.check_html` parses a markup string first. (:issue:`541`) by :user:`gaborbernat`.
- :meth:`~turbohtml.Node.xpath` gained the string subset of XPath 2.0: ``ends-with``, ``string-join(seq, sep)``,
  ``lower-case`` and ``upper-case`` (Unicode case mapping), and the regex ``matches(input, pattern[, flags])`` and
  ``replace(input, pattern, repl[, flags])`` spellings, where ``replace`` reads ``$1``-style group references and
  rewrites every match. They dispatch in the compiled-C engine alongside the XPath 1.0 core and the EXSLT namespaces, so
  an expression ported from ``elementpath``, ``lxml``, or ``htmlquery`` that leans on them runs without registration.
  (:issue:`542`) by :user:`gaborbernat`.
- :func:`turbohtml.detect.normalize` returns text in a Unicode normalization form (UAX #15) -- ``NFC``, ``NFD``,
  ``NFKC``, or ``NFKD`` -- the C successor to :func:`python:unicodedata.normalize`, and
  :func:`turbohtml.detect.is_normalized` tests membership. Both run over tables generated from the interpreter's own
  ``unicodedata``, so they agree with it exactly, and a quick check returns already-normalized text without allocating.
  (:issue:`543`) by :user:`gaborbernat`.
- :func:`turbohtml.rewrite.rewrite` transforms HTML in a single streaming pass without building a tree, the model
  Cloudflare's lol-html popularized. It runs the WHATWG tokenizer over the input while tracking only the open-element
  stack, hands each element a CSS selector matches -- and, on request, each run of text, each comment, and the doctype
  -- to a Python handler that edits it in place (set or remove an attribute, insert markup before, after, or around it,
  replace its inner content, unwrap it, or drop it), and emits the result incrementally. Working memory stays
  proportional to the open-element depth, not the document size, so a multi-megabyte page rewrites in a fixed footprint,
  and an untouched construct is reproduced verbatim. Because the pass never looks ahead, the matchable selector subset
  is the one decidable from an element and its ancestors -- type, universal, id, class, and attribute selectors, the
  descendant and child combinators, ``:root``, and ``:is()``/``:where()``/``:not()`` over that subset; a sibling
  combinator, a positional or structural pseudo-class, or ``:has()`` raises :class:`~turbohtml.SelectorSyntaxError`.
  (:issue:`544`) by :user:`gaborbernat`.
- A new :mod:`turbohtml.treebuild` module retargets the parser at a tree of your own.
  :func:`turbohtml.treebuild.parse_into` runs the WHATWG tree builder and drives a builder object -- a ``create_*``
  method per node kind plus an ``append`` that links a child under its parent -- to construct the tree directly,
  returning whatever the builder made its document root. No navigable :class:`turbohtml.Node` is materialized and the
  tree is walked only once, so an index, a diff tree, or another library's nodes is populated straight from the parse
  rather than by a second descent. Each element carries its namespace URI and its attributes as ``(name, value)`` pairs,
  a ``<template>``'s content is appended under the template handle, and a bogus ``<?...>`` construct reaches a distinct
  ``create_pi``. This is Rust html5ever's ``TreeSink`` and Node parse5's ``TreeAdapter`` in turbohtml shape; the tree
  construction, the walk, and the string extraction all run in C. (:issue:`545`) by :user:`gaborbernat`.
- :mod:`turbohtml.cssom` runs the CSS Object Model cascade: :func:`~turbohtml.cssom.computed_style` resolves the
  ``getComputedStyle`` of an element by collecting every ``<style>`` sheet plus the inline ``style`` along its ancestor
  chain, matching the native selector engine, ordering the declarations by origin importance, the style attribute,
  specificity, and source order, then applying inheritance, shorthand expansion, and each property's initial value --
  all in the C core under the per-tree critical section. Alongside it, :class:`~turbohtml.cssom.StyleSheet`,
  :class:`~turbohtml.cssom.RuleList`, :class:`~turbohtml.cssom.StyleRule`, and
  :class:`~turbohtml.cssom.StyleDeclaration` are the read-only, turbohtml-native spelling of the CSSOM ``CSSStyleSheet``
  / ``CSSRuleList`` / ``CSSStyleRule`` / ``CSSStyleDeclaration`` interfaces. The returned value is the computed value,
  not the used value: turbohtml runs no layout, so lengths and percentages come back as written, the same boundary jsdom
  and cssstyle draw. Shorthand expansion covers the distributive families (``margin``, ``padding``,
  ``border-width``/``style``/``color``, ``overflow``) and the ``<line-width> || <line-style> || <color>`` shorthands
  ``border``, each ``border-<side>``, and ``outline``, whose components resolve in any order and reset every longhand
  they cover. (:issue:`546`) by :user:`gaborbernat`.
- :meth:`turbohtml.Node.to_source` losslessly serializes a tree back to HTML, re-emitting the verbatim source bytes of
  every element and text run a parse left untouched and reserializing only the parts a mutation changed. Parse with
  ``source_locations=True`` and an unedited round trip reproduces the input byte for byte -- author quoting, tag-name
  case, character-reference spelling, and insignificant whitespace intact -- for markup that parsed without implied
  elements or content reordering; after an edit only the changed node's markup is rewritten while every untouched
  sibling and subtree keeps its original span. It is the tree-based counterpart to the streaming
  :func:`turbohtml.rewrite.rewrite`, the model Cloudflare's lol-html popularized. (:issue:`547`) by :user:`gaborbernat`.
- :func:`turbohtml.parse`, :func:`turbohtml.parse_fragment`, and :class:`~turbohtml.IncrementalParser` gained a
  ``source_locations`` flag (default ``False``) that records the granular source spans parse5 exposes as
  ``sourceCodeLocationInfo``. With it on, each element's :attr:`~turbohtml.Node.source_location` returns a
  :class:`~turbohtml.SourceLocation` giving the :class:`~turbohtml.SourceSpan` of its start tag, its end tag (``None``
  when the source never closed it), and each attribute's whole ``name="value"``, every span carrying start/end line,
  column, and code-point offset so ``source[start_offset:end_offset]`` slices the construct out. The tokenizer stamps
  the spans in C as it runs and the tree builder hangs the record off each element, so the feature is zero-overhead when
  off; it implies ``positions`` when on, keeping :attr:`~turbohtml.Node.source_line` and
  :attr:`~turbohtml.Node.position` available beside the spans. (:issue:`548`) by :user:`gaborbernat`.
- Added declarative Shadow DOM to the parser. When the tree builder meets a ``<template>`` carrying a ``shadowrootmode``
  of ``open`` or ``closed`` on a valid shadow host, it attaches a shadow root to the template's parent and parses the
  template's content into it, reusing the Shadow DOM tree model -- the template element never joins the light tree.
  ``shadowrootdelegatesfocus`` and ``shadowrootclonable`` set the matching flags, readable as the new
  :attr:`~turbohtml.ShadowRoot.delegates_focus` and :attr:`~turbohtml.ShadowRoot.clonable` properties. Following the
  WHATWG per-document flag, :func:`turbohtml.parse` honors declarative shadow roots by default (a browser navigation)
  while :func:`turbohtml.parse_fragment` does not (an ``innerHTML`` assignment); the new
  ``allow_declarative_shadow_roots`` argument flips either default, matching ``setHTMLUnsafe`` when turned on for a
  fragment. (:issue:`549`) by :user:`gaborbernat`.
- Added the DOM Living Standard traversal objects :class:`turbohtml.TreeWalker` and :class:`turbohtml.NodeIterator`,
  with the :class:`turbohtml.NodeFilter` constants for the ``what_to_show`` bitmask and the filter verdicts. A
  ``TreeWalker`` is a movable cursor over a subtree -- ``parent_node``, ``first_child``, ``last_child``,
  ``next_sibling``, ``previous_sibling``, ``next_node``, ``previous_node`` -- while a ``NodeIterator`` is the flat,
  filtered forward/backward view and iterates directly in a ``for`` loop. Both take a ``what_to_show`` node-type mask
  and an optional filter callback returning ``FILTER_ACCEPT``, ``FILTER_REJECT``, or ``FILTER_SKIP``; reject drops a
  node and its whole subtree while skip drops only the node, so a ``TreeWalker`` prunes where a ``NodeIterator`` (having
  no subtree) treats the two alike. The state machine and the ``what_to_show`` test run in the C core; the filter is the
  one callback into Python. This ports traversal code written against the browser DOM or jsdom. (:issue:`550`) by
  :user:`gaborbernat`.
- :func:`turbohtml.parse` and :func:`turbohtml.parse_fragment` gained a ``scripting`` flag (default ``False``). With it
  on, turbohtml sets the WHATWG scripting flag: ``<noscript>`` becomes a raw-text element, so its content is one text
  run rather than parsed markup and serializes back unescaped, reproducing the tree a scripting browser builds. The flag
  is a property of the parsed tree, so the serializer and ``inner_html`` stay consistent with how it was parsed. parse5
  and html5ever default this on for browser fidelity; turbohtml keeps it off so ``<noscript>`` fallback content stays
  navigable. (:issue:`551`) by :user:`gaborbernat`.
- Added the DOM Living Standard :class:`~turbohtml.Range` and :class:`~turbohtml.StaticRange` types. A ``Range`` holds
  two boundary points -- each a ``(container, offset)`` pair -- and carries the full boundary API
  (:meth:`~turbohtml.Range.set_start`/:meth:`~turbohtml.Range.set_end` and their ``_before``/``_after`` variants,
  :meth:`~turbohtml.Range.select_node`, :meth:`~turbohtml.Range.select_node_contents`,
  :meth:`~turbohtml.Range.collapse`), the derived :attr:`~turbohtml.Range.collapsed` and
  :attr:`~turbohtml.Range.common_ancestor_container` properties, the comparisons
  (:meth:`~turbohtml.Range.compare_boundary_points`, :meth:`~turbohtml.Range.compare_point`,
  :meth:`~turbohtml.Range.is_point_in_range`, :meth:`~turbohtml.Range.intersects_node`), and the content operations
  (:meth:`~turbohtml.Range.clone_contents`, :meth:`~turbohtml.Range.extract_contents`,
  :meth:`~turbohtml.Range.delete_contents`, :meth:`~turbohtml.Range.insert_node`,
  :meth:`~turbohtml.Range.surround_contents`, :meth:`~turbohtml.Range.clone_range`), each following the WHATWG
  boundary-point ordering and extract/clone/delete algorithms in C under the per-tree critical section. ``StaticRange``
  is the immutable four-value snapshot. Offsets index code points in character data and children elsewhere, so a Python
  string's own indexing lines up with a text-node offset. (:issue:`552`) by :user:`gaborbernat`.
- Added the DOM Living Standard Shadow DOM tree model. :meth:`~turbohtml.Element.attach_shadow` attaches an open or
  closed shadow tree and returns a :class:`~turbohtml.ShadowRoot` -- a document-fragment-like root held off the light
  tree, so it never appears among the host's children or in its serialization -- reachable through
  :attr:`~turbohtml.Element.shadow_root` (``None`` for a closed root) and carrying :attr:`~turbohtml.ShadowRoot.mode`,
  :attr:`~turbohtml.ShadowRoot.host`, :meth:`~turbohtml.ShadowRoot.set_inner_html`, and
  :meth:`~turbohtml.ShadowRoot.append`. ``<slot>`` elements assign the host's children by name (the unnamed default slot
  takes the rest): :meth:`~turbohtml.Element.assigned_nodes` and :meth:`~turbohtml.Element.assigned_elements` read what
  a slot received, with a ``flatten`` option that falls back to a slot's own children and expands nested shadow slots,
  and :attr:`~turbohtml.Node.assigned_slot` gives the slot a child landed in. :attr:`~turbohtml.Node.flattened_children`
  returns the composed tree with every slot replaced by its assigned nodes. The assignment and flattening algorithms run
  in C under the per-tree critical section and are computed on demand, so they always reflect the current tree.
  (:issue:`553`) by :user:`gaborbernat`.
- Added :class:`~turbohtml.MutationObserver`, a synchronous take on the DOM ``MutationObserver`` for recording tree
  edits. Register a node with :meth:`~turbohtml.MutationObserver.observe` and the DOM options (``child_list``,
  ``attributes``, ``character_data``, ``subtree``, ``attribute_old_value``, ``character_data_old_value``,
  ``attribute_filter``); every change made through the mutation API queues a :class:`~turbohtml.MutationRecord` carrying
  the added and removed nodes, the surrounding siblings, and the attribute name and old value when asked, following the
  WHATWG "queue a mutation record" algorithm in C under the per-tree critical section. Because turbohtml has no event
  loop, delivery is synchronous rather than microtask-scheduled: :meth:`~turbohtml.MutationObserver.take_records`
  returns and clears the queued batch, and :meth:`~turbohtml.MutationObserver.deliver` drains it and calls the
  observer's callback. :meth:`~turbohtml.MutationObserver.disconnect` stops observing and discards pending records.
  (:issue:`554`) by :user:`gaborbernat`.
- :class:`~turbohtml.clean.Policy` gained ``xml``: with it on, the sanitizer serializes the cleaned tree as well-formed
  XML/XHTML instead of HTML. Every kept empty element self-closes (``<br/>``), foreign SVG and MathML subtrees declare
  their namespace, text and attribute values follow the XML escaping rules, and a kept comment, a control character
  outside XML's ``Char`` production, or an attribute name XML cannot hold is neutralized, so the output always reparses
  through :func:`turbohtml.parse_xml`. The walk and the safety baseline are unchanged, so an XML-mode policy is exactly
  as safe as its HTML-mode twin. This clones DOMPurify's ``PARSER_MEDIA_TYPE: 'application/xhtml+xml'`` and replaces the
  brittle ``.replace("<br>", "<br/>")`` a bleach-based cleaner needs to feed a strict XHTML consumer such as Reportlab's
  RML. :attr:`turbohtml.Node.inner_xml` exposes the same children-only XML serialization for any node. (:issue:`565`) by
  :user:`gaborbernat`.
- The ``rewrite`` benchmark now runs against a fair in-process peer. lxml and BeautifulSoup do the same edits --
  ``rel=nofollow`` on every link, ``loading=lazy`` on every image, every comment dropped -- through the parse, mutate,
  and serialize round trip that :func:`turbohtml.rewrite.rewrite` skips, and the table reports each party's peak
  resident memory beside throughput, so the tree the streaming rewriter never builds shows up as memory it never holds.
  The :doc:`lol-html migration guide </migration/lol-html>` carries the numbers. (:pr:`612`) by :user:`gaborbernat`.

.. _release-1-0-0:

*********************
 v1.0.0 (2026-07-05)
*********************

.. _release-1-0-0-added:

Added
=====

- Query a tree with the full Selectors Level 4 grammar (``:is()``, ``:where()``, ``:has()``, ``:not()``,
  ``:nth-child(An+B of S)``, the structural, input, ``:lang()``, ``:dir()``, and ``:scope`` pseudo-classes) and an XPath
  1.0 engine: :meth:`~turbohtml.Node.xpath`, a compiled reusable :class:`turbohtml.XPath`, the EXSLT function set, and
  namespace, variable, and extension binding. A pyquery-style :class:`turbohtml.query.Query`, a soupsieve-shaped
  matcher, and :func:`turbohtml.convert.css_to_xpath` cover the BeautifulSoup, cssselect, parsel, and pyquery surfaces.
  (:issue:`179`) by :user:`gaborbernat`.
- Serialize back to HTML with pretty-print, whitespace minification, and lazy streaming
  (:meth:`~turbohtml.Node.serialize_iter`), and minify HTML, CSS, and JavaScript through native engines under
  :mod:`turbohtml.clean`. Every transform is round-trip safe, replacing rcssmin, csscompressor, rjsmin, jsmin,
  minify-html, and htmlmin. (:issue:`343`, :pr:`346`) by :user:`gaborbernat`.
- Convert a tree to GitHub-Flavored Markdown, layout-aware text, or annotated ``(start, end, label)`` spans, and extract
  a page's main article, boilerplate paragraphs, publication date, tables, links, and structured data (JSON-LD,
  Microdata, RDFa, Dublin Core, Open Graph) through :mod:`turbohtml.extract`, replacing markdownify, html2text,
  inscriptis, trafilatura, readability, htmldate, extruct, and microdata. (:issue:`273`, :issue:`276`) by
  :user:`gaborbernat`.
- Detect a byte stream's encoding and a text's natural language from the standalone :mod:`turbohtml.detect` module,
  which reports confidence and a BOM label, covers 69 languages across nine scripts, and replaces chardet,
  charset-normalizer, and cchardet. (:issue:`315`, :pr:`474`) by :user:`gaborbernat`.
- Parse incrementally from a stream with :class:`turbohtml.IncrementalParser`, read the WHATWG parse errors recovery
  swallows through :attr:`~turbohtml.Document.errors`, and locate each element in the source through
  :attr:`~turbohtml.Node.source_line` and :attr:`~turbohtml.Node.source_col`. (:issue:`210`, :issue:`212`) by
  :user:`gaborbernat`.
- Sanitize untrusted HTML against a frozen allowlist :class:`turbohtml.clean.Policy` that is safe with no arguments and
  scrubs inline and embedded CSS, foreign namespaces, and media hosts, and auto-link URLs and email addresses, together
  replacing bleach. (:issue:`8`, :issue:`9`) by :user:`gaborbernat`.
- Build and edit the tree in place: construct nodes and a whole HTML5 page with :data:`turbohtml.build.E` and
  :func:`turbohtml.build.document`, edit the class token set and inner HTML or text, wrap and unwrap subtrees, trim a
  document to a selector with :meth:`~turbohtml.Node.prune`, read and fill form fields, and compare two subtrees with
  :meth:`~turbohtml.Node.equals`. (:issue:`275`, :issue:`225`, :pr:`468`) by :user:`gaborbernat`.
- Clean, canonicalize, and extract page URLs through :mod:`turbohtml.extract`, backed by a native URL pipeline (WHATWG
  splitting, percent-coding, relative-reference joining, UTS #46 IDNA, and Public Suffix List registrable-domain
  filtering), and run the toolkit from a ``turbohtml`` command line covering minify, detect, to-markdown, to-text, and
  sanitize. (:issue:`321`, :pr:`470`) by :user:`gaborbernat`.

.. _release-1-0-0-fixed:

Fixed
=====

- Bring tree construction to WHATWG conformance across foster parenting, foreign content, the select, table, and
  template insertion modes, doctype and quirks detection, duplicate and void-element handling, validated against the
  html5lib-tests corpus. (:issue:`32`) by :user:`gaborbernat`.
- Resolve every label in the WHATWG Encoding Standard, decode the replacement and gb18030 families and the windows-1252
  C1 bytes, and honor the 1024-byte ``<meta>`` prescan. (:issue:`54`, :issue:`423`) by :user:`gaborbernat`.
- Match the Selectors Level 4 corrections: forgiving ``:is()``/``:where()`` lists, quirks-mode case folding, CSS escape
  decoding, namespace prefixes, whitespace-only ``:empty``, and ``:scope`` resolution inside ``:has()``. (:issue:`174`)
  by :user:`gaborbernat`.
- Fix XPath 1.0 semantics: shortest round-tripping number formatting, half-to-positive-infinity rounding, fixed function
  arity, node-set typing, and arena-safe predicate compilation. (:issue:`398`) by :user:`gaborbernat`.
- Correct the Markdown and text exporters on nested and loose lists, link and image escaping, the ``<pre>`` leading
  newline, ordinal ``<li value>``, and raw-text suppression in every namespace. (:issue:`384`) by :user:`gaborbernat`.
- Fix extraction on landmark-wrapped and list-structured articles, Microdata ``itemref`` merging, table ``rowspan``
  bounds, and the WHATWG form ``select``/``optgroup`` submission rules. (:issue:`385`, :issue:`408`) by
  :user:`gaborbernat`.
- Keep the CSS and JavaScript minifiers value-safe: ``calc()`` type mixing, duplicate-declaration fallbacks,
  ``currentcolor``, conditional folding, and adjacent-string-literal concatenation. (:issue:`415`) by
  :user:`gaborbernat`.
- Raise a precise, typed error from every public failure path in place of a silent wrong result or a leaked low-level
  type, and document each callable's exceptions. (:issue:`434`) by :user:`gaborbernat`.

.. _release-1-0-0-documentation:

Documentation
=============

- Ship a migration guide for each library turbohtml replaces, every one carrying a monthly-downloads badge, a measured
  benchmark, and a speed-up multiplier, with the index ordered by adoption. (:issue:`313`) by :user:`gaborbernat`.
- Restructure the reference, how-to, and migration trees around the eight-namespace taxonomy (parse/DOM, detect, query,
  clean, convert, extract, build, serialize), and add ``llms.txt`` maps, a sitemap, and per-page meta descriptions.
  (:issue:`478`) by :user:`gaborbernat`.
- Add a written security policy, a "How turbohtml was built" page, and the seven design principles that shape the
  library. (:pr:`500`) by :user:`gaborbernat`.

.. _release-1-0-0-breaking-changes:

Breaking changes
================

- Give the public surface one name per concept. CSS matching folds from ``turbohtml.match`` into :mod:`turbohtml.query`;
  the sanitizer, linkifier, and every minifier gather under :mod:`turbohtml.clean`; a malformed selector raises one
  :class:`turbohtml.SelectorSyntaxError` from every parse path; the two ``Detector`` classes split into
  :class:`turbohtml.detect.EncodingDetector` and :class:`turbohtml.clean.LinkDetector`; and each surface with more than
  six arguments takes one frozen ``options`` config. (:issue:`478`) by :user:`gaborbernat`.
- Select a serialization mode with a single ``layout`` argument in place of ``indent``, so ``serialize(indent=2)``
  becomes ``serialize(layout=Indent(2))`` and :class:`~turbohtml.Minify` selects minified output. (:issue:`171`) by
  :user:`gaborbernat`.
- Report a valueless attribute (``<x a>``) as the empty string rather than ``None`` in :attr:`turbohtml.Element.attrs`,
  matching the WHATWG tokenizer and the DOM. (:issue:`87`) by :user:`gaborbernat`.

.. _release-1-0-0-security:

Security
========

- Harden against untrusted input: ASan and UBSan fuzz gates on every entry point, a DOMPurify XSS oracle and an
  html5lib-tests tree-equality oracle over the sanitizer, mutation-XSS namespace checks, caps on element nesting and
  duplicate attributes, and one overflow-safe buffer-growth helper across the C core. (:pr:`503`, :pr:`511`) by
  :user:`gaborbernat`.

.. _release-1-0-0-performance:

Performance
===========

- Speed the read path: a per-tree atom index runs a tag-pinned :meth:`~turbohtml.Node.find` several times faster,
  ``:has()`` evaluates in one amortized-linear pass, streaming serialization sizes its buffer up front, and link-time
  optimization re-inlines across a subsystem-first C layout. (:pr:`162`, :pr:`509`) by :user:`gaborbernat`.

.. _release-1-0-0-packaging:

Packaging
=========

- Build the release wheels with profile-guided, link-time optimization trained offline over a real-world corpus of
  clean, malformed, legacy-encoded, and structured-data markup. (:pr:`481`) by :user:`gaborbernat`.
- Pin every network-sourced C data table (IANA TLDs, the Public Suffix List, and the Unicode IDNA and NFC tables) to a
  named source commit and a SHA-256 checksum, so a rebuild is reproducible and aborts on a poisoned upstream.
  (:issue:`478`) by :user:`gaborbernat`.
- Strip the compiled extension's local symbol table from the release wheels at link time, trimming about 65 KB from the
  Linux ``.so`` and 46 KB from the macOS bundle; the source distribution still carries every test, tool, and generated
  table needed to build from source. (:issue:`478`) by :user:`gaborbernat`.

.. _release-0-4-0:

*********************
 v0.4.0 (2026-06-16)
*********************

.. _release-0-4-0-added:

Added
=====

- Build and edit the tree, not just read it: construct :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and
  :class:`~turbohtml.Comment` nodes and rearrange them with the full set of insert, wrap, extract, and normalize
  methods, with :attr:`~turbohtml.Element.attrs` and ``.text``/``.data`` as live setters. ``copy``, ``deepcopy``, and
  ``pickle`` duplicate a subtree. (:issue:`19`) by :user:`gaborbernat`.
- Round out the node model: :class:`~turbohtml.ProcessingInstruction` and :class:`~turbohtml.CData` join the hierarchy,
  :class:`~turbohtml.Doctype` exposes its :attr:`~turbohtml.Doctype.public_id` and :attr:`~turbohtml.Doctype.system_id`,
  and every node type supports structural pattern matching. (:issue:`22`) by :user:`gaborbernat`.

.. _release-0-4-0-documentation:

Documentation
=============

- Learn the write path through new tutorial, how-to, and explanation docs, backed by benchmarks showing turbohtml builds
  and rewrites trees about twice as fast as `lxml <https://lxml.de/>`__ and an order of magnitude faster than
  `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`__. (:issue:`19`) by :user:`gaborbernat`.
- Port to turbohtml with migration guides from `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`__, `lxml
  <https://lxml.de/>`__, `selectolax <https://github.com/rushter/selectolax>`__, `html5lib
  <https://github.com/html5lib/html5lib-python>`__, and the standard library, each mapping the source library's idioms
  to their turbohtml equivalents and flagging behavior differences. (:issue:`23`) by :user:`gaborbernat`.

.. _release-0-3-0:

*********************
 v0.3.0 (2026-06-16)
*********************

.. _release-0-3-0-added:

Added
=====

- Query any node with CSS through :meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.select_one`, a native
  matcher covering type, universal, ``#id``, ``.class``, and attribute selectors (all operators plus the
  case-sensitivity flag) across the descendant, child, adjacent, and sibling combinators, returning comma groups in
  document order. An invalid selector raises ``ValueError``. (:issue:`14`) by :user:`gaborbernat`.
- Search with a richer :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` filter grammar: match the tag
  and attributes by string, regex, bool, callable, or list (including ``class_`` and the ``attrs`` mapping), and choose
  the search direction with the ``axis`` keyword. ``find_all`` takes a ``limit`` and returns a ``list``. (:issue:`15`)
  by :user:`gaborbernat`.
- Test a node against a selector with :meth:`~turbohtml.Node.matches` and :meth:`~turbohtml.Node.closest`: ``matches()``
  reports whether the node satisfies a CSS selector in context, and ``closest()`` returns the nearest matching ancestor
  (or the node itself), or ``None``. (:issue:`16`) by :user:`gaborbernat`.
- Walk the tree by axis with new iterators: :attr:`~turbohtml.Node.next_siblings`,
  :attr:`~turbohtml.Node.previous_siblings`, document-order :attr:`~turbohtml.Node.following` and
  :attr:`~turbohtml.Node.preceding`, plus the :attr:`~turbohtml.Node.strings` and
  :attr:`~turbohtml.Node.stripped_strings` text iterators. (:issue:`17`) by :user:`gaborbernat`.
- Read HTML token-list attributes (``class``, ``rel``, ``headers``, ``sizes``, ``sandbox``, and the rest) as a
  ``list[str]`` in :attr:`turbohtml.Element.attrs`, split on ASCII whitespace; other attributes stay strings and
  valueless ones stay ``None``. (:issue:`18`) by :user:`gaborbernat`.
- Control serialization on any node: :attr:`~turbohtml.Node.inner_html` returns the children, while
  :meth:`~turbohtml.Node.serialize` and :meth:`~turbohtml.Node.encode` take a ``formatter`` (the
  :class:`~turbohtml.Formatter` enum picks the escape policy) and an ``indent`` for pretty output. The default stays
  WHATWG-conformant HTML. (:issue:`20`) by :user:`gaborbernat`.
- Parse ``bytes`` directly: :func:`turbohtml.parse` sniffs the encoding with the WHATWG algorithm (BOM, ``encoding``
  argument, ``<meta>`` charset, then windows-1252), decodes with U+FFFD replacement, and reports the result in
  :attr:`~turbohtml.Document.encoding`. (:issue:`21`) by :user:`gaborbernat`.

.. _release-0-2-0:

*********************
 v0.2.0 (2026-06-11)
*********************

.. _release-0-2-0-added:

Added
=====

- Tokenize HTML directly with a WHATWG-conformant tokenizer: :func:`turbohtml.tokenize` for whole strings, the streaming
  :class:`turbohtml.Tokenizer`, and the :class:`turbohtml.Token` / :class:`turbohtml.TokenType` types, validated against
  the html5lib-tests tokenizer conformance suite. (:pr:`6`) by :user:`gaborbernat`.
- Run :func:`turbohtml.escape` and :func:`turbohtml.unescape` faster: vectorized scanning and bulk copying speed up both
  calls, with unescaping of real escaped HTML about three times faster than the general lookup path. The benchmark now
  uses `pyperf <https://pyperf.readthedocs.io>`_ over multi-MiB real documents. (:pr:`7`) by :user:`gaborbernat`.

.. _release-0-1-1:

*********************
 v0.1.1 (2026-06-09)
*********************

.. _release-0-1-1-added:

Added
=====

- Speed up entity handling with C-accelerated :func:`turbohtml.escape` and :func:`turbohtml.unescape`, drop-in
  replacements for :func:`python:html.escape` and :func:`python:html.unescape`, shipped as wheels for CPython 3.10
  through 3.15. (:pr:`1`) by :user:`gaborbernat`.
- Escape non-ASCII text that needs no escaping several times faster with a vectorized special-character scan, ahead of
  :func:`python:html.escape`. (:pr:`3`) by :user:`gaborbernat`.

.. _release-0-1-1-documentation:

Documentation
=============

- See the measured :func:`turbohtml.escape`/:func:`turbohtml.unescape` speedups in the README and docs, reproduce them
  with ``tox -e bench``, and browse a typed API reference with intersphinx links. (:pr:`2`) by :user:`gaborbernat`.

.. _release-0-1-1-packaging:

Packaging
=========

- Install reliably from PyPI again: publishing each wheel in its own job keeps PEP 740 attestations within the Sigstore
  identity's lifetime, fixing the ``sigstore.oidc.ExpiredIdentity`` failure that blocked the first upload. (:pr:`4`) by
  :user:`gaborbernat`.
