########################
 Migrating to turbohtml
########################

turbohtml replaces the HTML libraries it benchmarks against. None is API-compatible, so porting is a translation:
turbohtml uses one name per concept and a typed shape where those libraries spread the work across aliases, methods, and
treebuilder choices. This page maps each library to turbohtml; `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_ gets the deepest treatment because it shares the most surface.

The guides are grouped by task so you can jump to the job you are porting; within each group, and across the sidebar as
a whole, they are ordered by adoption, most to least monthly PyPI downloads, so the libraries you are most likely to
port from come first. The all-time totals and each library's documentation sit alongside for context.

***************
 Parsing & DOM
***************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`beautifulsoup <beautifulsoup>`
      - `docs <https://www.crummy.com/software/BeautifulSoup/bs4/doc/>`__
      - .. image:: https://static.pepy.tech/badge/beautifulsoup4/month
            :alt: beautifulsoup4 monthly downloads
            :target: https://pepy.tech/project/beautifulsoup4
      - .. image:: https://static.pepy.tech/badge/beautifulsoup4
            :alt: beautifulsoup4 total downloads
            :target: https://pepy.tech/project/beautifulsoup4
    - - 2
      - :doc:`lxml <lxml>`
      - `docs <https://lxml.de/>`__
      - .. image:: https://static.pepy.tech/badge/lxml/month
            :alt: lxml monthly downloads
            :target: https://pepy.tech/project/lxml
      - .. image:: https://static.pepy.tech/badge/lxml
            :alt: lxml total downloads
            :target: https://pepy.tech/project/lxml
    - - 3
      - :doc:`html5lib <html5lib>`
      - `docs <https://html5lib.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/html5lib/month
            :alt: html5lib monthly downloads
            :target: https://pepy.tech/project/html5lib
      - .. image:: https://static.pepy.tech/badge/html5lib
            :alt: html5lib total downloads
            :target: https://pepy.tech/project/html5lib
    - - 4
      - :doc:`selectolax <selectolax>`
      - `docs <https://github.com/rushter/selectolax>`__
      - .. image:: https://static.pepy.tech/badge/selectolax/month
            :alt: selectolax monthly downloads
            :target: https://pepy.tech/project/selectolax
      - .. image:: https://static.pepy.tech/badge/selectolax
            :alt: selectolax total downloads
            :target: https://pepy.tech/project/selectolax
    - - 5
      - :doc:`resiliparse <resiliparse>`
      - `docs <https://resiliparse.chatnoir.eu/>`__
      - .. image:: https://static.pepy.tech/badge/resiliparse/month
            :alt: resiliparse monthly downloads
            :target: https://pepy.tech/project/resiliparse
      - .. image:: https://static.pepy.tech/badge/resiliparse
            :alt: resiliparse total downloads
            :target: https://pepy.tech/project/resiliparse
    - - 6
      - :doc:`mechanicalsoup <mechanicalsoup>`
      - `docs <https://mechanicalsoup.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/MechanicalSoup/month
            :alt: MechanicalSoup monthly downloads
            :target: https://pepy.tech/project/MechanicalSoup
      - .. image:: https://static.pepy.tech/badge/MechanicalSoup
            :alt: MechanicalSoup total downloads
            :target: https://pepy.tech/project/MechanicalSoup
    - - 7
      - :doc:`html5-parser <html5-parser>`
      - `docs <https://html5-parser.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/html5-parser/month
            :alt: html5-parser monthly downloads
            :target: https://pepy.tech/project/html5-parser
      - .. image:: https://static.pepy.tech/badge/html5-parser
            :alt: html5-parser total downloads
            :target: https://pepy.tech/project/html5-parser
    - - 8
      - :doc:`stdlib <stdlib>`
      - `docs <https://docs.python.org/3/library/html.parser.html>`__
      - Bundled with Python
      - --

********************
 Encoding detection
********************

:func:`turbohtml.detect.detect` sniffs the character encoding of raw bytes with the same C pipeline
:func:`turbohtml.parse` runs -- the WHATWG sniff, then Firefox's chardetng scoring -- so it answers what these libraries
answer, with the result a browser would pick.

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`charset-normalizer <charset-normalizer>`
      - `docs <https://charset-normalizer.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/charset-normalizer/month
            :alt: charset-normalizer monthly downloads
            :target: https://pepy.tech/project/charset-normalizer
      - .. image:: https://static.pepy.tech/badge/charset-normalizer
            :alt: charset-normalizer total downloads
            :target: https://pepy.tech/project/charset-normalizer
    - - 2
      - :doc:`chardet <chardet>` (also covers ``cchardet``)
      - `docs <https://chardet.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/chardet/month
            :alt: chardet monthly downloads
            :target: https://pepy.tech/project/chardet
      - .. image:: https://static.pepy.tech/badge/chardet
            :alt: chardet total downloads
            :target: https://pepy.tech/project/chardet

*********************
 Query — CSS & XPath
*********************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`soupsieve <soupsieve>`
      - `docs <https://facelessuser.github.io/soupsieve/>`__
      - .. image:: https://static.pepy.tech/badge/soupsieve/month
            :alt: soupsieve monthly downloads
            :target: https://pepy.tech/project/soupsieve
      - .. image:: https://static.pepy.tech/badge/soupsieve
            :alt: soupsieve total downloads
            :target: https://pepy.tech/project/soupsieve
    - - 2
      - :doc:`cssselect <cssselect>`
      - `docs <https://cssselect.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/cssselect/month
            :alt: cssselect monthly downloads
            :target: https://pepy.tech/project/cssselect
      - .. image:: https://static.pepy.tech/badge/cssselect
            :alt: cssselect total downloads
            :target: https://pepy.tech/project/cssselect
    - - 3
      - :doc:`parsel <parsel>`
      - `docs <https://parsel.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/parsel/month
            :alt: parsel monthly downloads
            :target: https://pepy.tech/project/parsel
      - .. image:: https://static.pepy.tech/badge/parsel
            :alt: parsel total downloads
            :target: https://pepy.tech/project/parsel
    - - 4
      - :doc:`pyquery <pyquery>`
      - `docs <https://pyquery.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/pyquery/month
            :alt: pyquery monthly downloads
            :target: https://pepy.tech/project/pyquery
      - .. image:: https://static.pepy.tech/badge/pyquery
            :alt: pyquery total downloads
            :target: https://pepy.tech/project/pyquery

******************
 Clean & sanitize
******************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`linkify-it-py <linkify-it-py>`
      - `docs <https://github.com/tsutsu3/linkify-it-py>`__
      - .. image:: https://static.pepy.tech/badge/linkify-it-py/month
            :alt: linkify-it-py monthly downloads
            :target: https://pepy.tech/project/linkify-it-py
      - .. image:: https://static.pepy.tech/badge/linkify-it-py
            :alt: linkify-it-py total downloads
            :target: https://pepy.tech/project/linkify-it-py
    - - 2
      - :doc:`bleach <bleach>`
      - `docs <https://bleach.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/bleach/month
            :alt: bleach monthly downloads
            :target: https://pepy.tech/project/bleach
      - .. image:: https://static.pepy.tech/badge/bleach
            :alt: bleach total downloads
            :target: https://pepy.tech/project/bleach
    - - 3
      - :doc:`nh3 <nh3>`
      - `docs <https://nh3.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/nh3/month
            :alt: nh3 monthly downloads
            :target: https://pepy.tech/project/nh3
      - .. image:: https://static.pepy.tech/badge/nh3
            :alt: nh3 total downloads
            :target: https://pepy.tech/project/nh3
    - - 4
      - :doc:`lxml-html-clean <lxml-html-clean>`
      - `docs <https://lxml-html-clean.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/lxml_html_clean/month
            :alt: lxml_html_clean monthly downloads
            :target: https://pepy.tech/project/lxml_html_clean
      - .. image:: https://static.pepy.tech/badge/lxml_html_clean
            :alt: lxml_html_clean total downloads
            :target: https://pepy.tech/project/lxml_html_clean
    - - 5
      - :doc:`minify-html <minify-html>`
      - `docs <https://github.com/wilsonzlin/minify-html>`__
      - .. image:: https://static.pepy.tech/badge/minify-html/month
            :alt: minify-html monthly downloads
            :target: https://pepy.tech/project/minify-html
      - .. image:: https://static.pepy.tech/badge/minify-html
            :alt: minify-html total downloads
            :target: https://pepy.tech/project/minify-html
    - - 6
      - :doc:`htmlmin <htmlmin>`
      - `docs <https://htmlmin.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/htmlmin/month
            :alt: htmlmin monthly downloads
            :target: https://pepy.tech/project/htmlmin
      - .. image:: https://static.pepy.tech/badge/htmlmin
            :alt: htmlmin total downloads
            :target: https://pepy.tech/project/htmlmin
    - - 7
      - :doc:`html-sanitizer <html-sanitizer>`
      - `docs <https://github.com/matthiask/html-sanitizer>`__
      - .. image:: https://static.pepy.tech/badge/html-sanitizer/month
            :alt: html-sanitizer monthly downloads
            :target: https://pepy.tech/project/html-sanitizer
      - .. image:: https://static.pepy.tech/badge/html-sanitizer
            :alt: html-sanitizer total downloads
            :target: https://pepy.tech/project/html-sanitizer

******************
 CSS minification
******************

:func:`turbohtml.clean.minify_css` is a value-safe CSS minifier -- it rewrites every value to its shortest equivalent
form, so the output is the smallest of any round-trip-safe minifier (the :doc:`performance </development/performance>`
page benchmarks all of them).

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`rcssmin <rcssmin>`
      - `docs <https://opensource.perlig.de/rcssmin/>`__
      - .. image:: https://static.pepy.tech/badge/rcssmin/month
            :alt: rcssmin monthly downloads
            :target: https://pepy.tech/project/rcssmin
      - .. image:: https://static.pepy.tech/badge/rcssmin
            :alt: rcssmin total downloads
            :target: https://pepy.tech/project/rcssmin
    - - 2
      - :doc:`csscompressor <csscompressor>`
      - `docs <https://github.com/sprymix/csscompressor>`__
      - .. image:: https://static.pepy.tech/badge/csscompressor/month
            :alt: csscompressor monthly downloads
            :target: https://pepy.tech/project/csscompressor
      - .. image:: https://static.pepy.tech/badge/csscompressor
            :alt: csscompressor total downloads
            :target: https://pepy.tech/project/csscompressor
    - - 3
      - :doc:`lightningcss <lightningcss>`
      - `docs <https://pypi.org/project/lightningcss/>`__
      - .. image:: https://static.pepy.tech/badge/lightningcss/month
            :alt: lightningcss monthly downloads
            :target: https://pepy.tech/project/lightningcss
      - .. image:: https://static.pepy.tech/badge/lightningcss
            :alt: lightningcss total downloads
            :target: https://pepy.tech/project/lightningcss

***************************
 Text, Markdown & escaping
***************************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`markupsafe <markupsafe>`
      - `docs <https://markupsafe.palletsprojects.com/>`__
      - .. image:: https://static.pepy.tech/badge/markupsafe/month
            :alt: markupsafe monthly downloads
            :target: https://pepy.tech/project/markupsafe
      - .. image:: https://static.pepy.tech/badge/markupsafe
            :alt: markupsafe total downloads
            :target: https://pepy.tech/project/markupsafe
    - - 2
      - :doc:`markdownify <markdownify>`
      - `docs <https://github.com/matthewwithanm/python-markdownify>`__
      - .. image:: https://static.pepy.tech/badge/markdownify/month
            :alt: markdownify monthly downloads
            :target: https://pepy.tech/project/markdownify
      - .. image:: https://static.pepy.tech/badge/markdownify
            :alt: markdownify total downloads
            :target: https://pepy.tech/project/markdownify
    - - 3
      - :doc:`html2text <html2text>`
      - `docs <https://github.com/Alir3z4/html2text>`__
      - .. image:: https://static.pepy.tech/badge/html2text/month
            :alt: html2text monthly downloads
            :target: https://pepy.tech/project/html2text
      - .. image:: https://static.pepy.tech/badge/html2text
            :alt: html2text total downloads
            :target: https://pepy.tech/project/html2text
    - - 4
      - :doc:`inscriptis <inscriptis>`
      - `docs <https://inscriptis.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/inscriptis/month
            :alt: inscriptis monthly downloads
            :target: https://pepy.tech/project/inscriptis
      - .. image:: https://static.pepy.tech/badge/inscriptis
            :alt: inscriptis total downloads
            :target: https://pepy.tech/project/inscriptis
    - - 5
      - :doc:`html-text <html-text>`
      - `docs <https://github.com/zytedata/html-text>`__
      - .. image:: https://static.pepy.tech/badge/html-text/month
            :alt: html-text monthly downloads
            :target: https://pepy.tech/project/html-text
      - .. image:: https://static.pepy.tech/badge/html-text
            :alt: html-text total downloads
            :target: https://pepy.tech/project/html-text

*******************************
 Content & metadata extraction
*******************************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`pandas <pandas>`
      - `docs <https://pandas.pydata.org/docs/>`__
      - .. image:: https://static.pepy.tech/badge/pandas/month
            :alt: pandas monthly downloads
            :target: https://pepy.tech/project/pandas
      - .. image:: https://static.pepy.tech/badge/pandas
            :alt: pandas total downloads
            :target: https://pepy.tech/project/pandas
    - - 2
      - :doc:`w3lib <w3lib>`
      - `docs <https://w3lib.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/w3lib/month
            :alt: w3lib monthly downloads
            :target: https://pepy.tech/project/w3lib
      - .. image:: https://static.pepy.tech/badge/w3lib
            :alt: w3lib total downloads
            :target: https://pepy.tech/project/w3lib
    - - 3
      - :doc:`trafilatura <trafilatura>`
      - `docs <https://trafilatura.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/trafilatura/month
            :alt: trafilatura monthly downloads
            :target: https://pepy.tech/project/trafilatura
      - .. image:: https://static.pepy.tech/badge/trafilatura
            :alt: trafilatura total downloads
            :target: https://pepy.tech/project/trafilatura
    - - 4
      - :doc:`courlan <courlan>`
      - `docs <https://github.com/adbar/courlan>`__
      - .. image:: https://static.pepy.tech/badge/courlan/month
            :alt: courlan monthly downloads
            :target: https://pepy.tech/project/courlan
      - .. image:: https://static.pepy.tech/badge/courlan
            :alt: courlan total downloads
            :target: https://pepy.tech/project/courlan
    - - 5
      - :doc:`justext <justext>`
      - `docs <https://github.com/miso-belica/jusText>`__
      - .. image:: https://static.pepy.tech/badge/justext/month
            :alt: justext monthly downloads
            :target: https://pepy.tech/project/justext
      - .. image:: https://static.pepy.tech/badge/justext
            :alt: justext total downloads
            :target: https://pepy.tech/project/justext
    - - 6
      - :doc:`readability-lxml <readability-lxml>`
      - `docs <https://github.com/buriy/python-readability>`__
      - .. image:: https://static.pepy.tech/badge/readability-lxml/month
            :alt: readability-lxml monthly downloads
            :target: https://pepy.tech/project/readability-lxml
      - .. image:: https://static.pepy.tech/badge/readability-lxml
            :alt: readability-lxml total downloads
            :target: https://pepy.tech/project/readability-lxml
    - - 7
      - :doc:`readabilipy <readabilipy>`
      - `docs <https://readabilipy.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/readabilipy/month
            :alt: readabilipy monthly downloads
            :target: https://pepy.tech/project/readabilipy
      - .. image:: https://static.pepy.tech/badge/readabilipy
            :alt: readabilipy total downloads
            :target: https://pepy.tech/project/readabilipy
    - - 8
      - :doc:`newspaper3k <newspaper3k>`
      - `docs <https://newspaper.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/newspaper3k/month
            :alt: newspaper3k monthly downloads
            :target: https://pepy.tech/project/newspaper3k
      - .. image:: https://static.pepy.tech/badge/newspaper3k
            :alt: newspaper3k total downloads
            :target: https://pepy.tech/project/newspaper3k
    - - 9
      - :doc:`boilerpy3 <boilerpy3>`
      - `docs <https://github.com/jmriebold/BoilerPy3>`__
      - .. image:: https://static.pepy.tech/badge/boilerpy3/month
            :alt: boilerpy3 monthly downloads
            :target: https://pepy.tech/project/boilerpy3
      - .. image:: https://static.pepy.tech/badge/boilerpy3
            :alt: boilerpy3 total downloads
            :target: https://pepy.tech/project/boilerpy3
    - - 10
      - :doc:`goose3 <goose3>`
      - `docs <https://goose3.readthedocs.io/>`__
      - .. image:: https://static.pepy.tech/badge/goose3/month
            :alt: goose3 monthly downloads
            :target: https://pepy.tech/project/goose3
      - .. image:: https://static.pepy.tech/badge/goose3
            :alt: goose3 total downloads
            :target: https://pepy.tech/project/goose3
    - - 11
      - :doc:`news-please <news-please>`
      - `docs <https://github.com/fhamborg/news-please>`__
      - .. image:: https://static.pepy.tech/badge/news-please/month
            :alt: news-please monthly downloads
            :target: https://pepy.tech/project/news-please
      - .. image:: https://static.pepy.tech/badge/news-please
            :alt: news-please total downloads
            :target: https://pepy.tech/project/news-please

*****************
 Structured data
*****************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`extruct <extruct>`
      - `docs <https://github.com/scrapinghub/extruct>`__
      - .. image:: https://static.pepy.tech/badge/extruct/month
            :alt: extruct monthly downloads
            :target: https://pepy.tech/project/extruct
      - .. image:: https://static.pepy.tech/badge/extruct
            :alt: extruct total downloads
            :target: https://pepy.tech/project/extruct
    - - 2
      - :doc:`metadata_parser <metadata_parser>`
      - `docs <https://github.com/jvanasco/metadata_parser>`__
      - .. image:: https://static.pepy.tech/badge/metadata_parser/month
            :alt: metadata_parser monthly downloads
            :target: https://pepy.tech/project/metadata_parser
      - .. image:: https://static.pepy.tech/badge/metadata_parser
            :alt: metadata_parser total downloads
            :target: https://pepy.tech/project/metadata_parser

***************
 HTML builders
***************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`dominate <dominate>`
      - `docs <https://github.com/Knio/dominate>`__
      - .. image:: https://static.pepy.tech/badge/dominate/month
            :alt: dominate monthly downloads
            :target: https://pepy.tech/project/dominate
      - .. image:: https://static.pepy.tech/badge/dominate
            :alt: dominate total downloads
            :target: https://pepy.tech/project/dominate
    - - 2
      - :doc:`yattag <yattag>`
      - `docs <https://www.yattag.org/>`__
      - .. image:: https://static.pepy.tech/badge/yattag/month
            :alt: yattag monthly downloads
            :target: https://pepy.tech/project/yattag
      - .. image:: https://static.pepy.tech/badge/yattag
            :alt: yattag total downloads
            :target: https://pepy.tech/project/yattag
    - - 3
      - :doc:`htbuilder <htbuilder>`
      - `docs <https://github.com/tvst/htbuilder>`__
      - .. image:: https://static.pepy.tech/badge/htbuilder/month
            :alt: htbuilder monthly downloads
            :target: https://pepy.tech/project/htbuilder
      - .. image:: https://static.pepy.tech/badge/htbuilder
            :alt: htbuilder total downloads
            :target: https://pepy.tech/project/htbuilder
    - - 4
      - :doc:`htpy <htpy>`
      - `docs <https://htpy.dev/>`__
      - .. image:: https://static.pepy.tech/badge/htpy/month
            :alt: htpy monthly downloads
            :target: https://pepy.tech/project/htpy
      - .. image:: https://static.pepy.tech/badge/htpy
            :alt: htpy total downloads
            :target: https://pepy.tech/project/htpy
    - - 5
      - :doc:`airium <airium>`
      - `docs <https://gitlab.com/kamichal/airium>`__
      - .. image:: https://static.pepy.tech/badge/airium/month
            :alt: airium monthly downloads
            :target: https://pepy.tech/project/airium
      - .. image:: https://static.pepy.tech/badge/airium
            :alt: airium total downloads
            :target: https://pepy.tech/project/airium
    - - 6
      - :doc:`markyp <markyp>`
      - `docs <https://github.com/volfpeter/markyp-html>`__
      - .. image:: https://static.pepy.tech/badge/markyp-html/month
            :alt: markyp-html monthly downloads
            :target: https://pepy.tech/project/markyp-html
      - .. image:: https://static.pepy.tech/badge/markyp-html
            :alt: markyp-html total downloads
            :target: https://pepy.tech/project/markyp-html
    - - 7
      - :doc:`fast-html <fast-html>`
      - `docs <https://github.com/pcarbonn/fast_html>`__
      - .. image:: https://static.pepy.tech/badge/fast-html/month
            :alt: fast-html monthly downloads
            :target: https://pepy.tech/project/fast-html
      - .. image:: https://static.pepy.tech/badge/fast-html
            :alt: fast-html total downloads
            :target: https://pepy.tech/project/fast-html
    - - 8
      - :doc:`simple-html <simple-html>`
      - `docs <https://github.com/keithasaurus/simple_html>`__
      - .. image:: https://static.pepy.tech/badge/simple-html/month
            :alt: simple-html monthly downloads
            :target: https://pepy.tech/project/simple-html
      - .. image:: https://static.pepy.tech/badge/simple-html
            :alt: simple-html total downloads
            :target: https://pepy.tech/project/simple-html
    - - 9
      - :doc:`hyperpython <hyperpython>`
      - `docs <https://github.com/fabiommendes/hyperpython>`__
      - .. image:: https://static.pepy.tech/badge/hyperpython/month
            :alt: hyperpython monthly downloads
            :target: https://pepy.tech/project/hyperpython
      - .. image:: https://static.pepy.tech/badge/hyperpython
            :alt: hyperpython total downloads
            :target: https://pepy.tech/project/hyperpython

*************************
 JavaScript minification
*************************

.. list-table::
    :header-rows: 1
    :widths: auto

    - - #
      - Library
      - Docs
      - Monthly downloads
      - Total downloads
    - - 1
      - :doc:`rjsmin <rjsmin>`
      - `docs <https://opensource.perlig.de/rjsmin/>`__
      - .. image:: https://static.pepy.tech/badge/rjsmin/month
            :alt: rjsmin monthly downloads
            :target: https://pepy.tech/project/rjsmin
      - .. image:: https://static.pepy.tech/badge/rjsmin
            :alt: rjsmin total downloads
            :target: https://pepy.tech/project/rjsmin
    - - 2
      - :doc:`jsmin <jsmin>`
      - `docs <https://github.com/tikitu/jsmin>`__
      - .. image:: https://static.pepy.tech/badge/jsmin/month
            :alt: jsmin monthly downloads
            :target: https://pepy.tech/project/jsmin
      - .. image:: https://static.pepy.tech/badge/jsmin
            :alt: jsmin total downloads
            :target: https://pepy.tech/project/jsmin
    - - 3
      - :doc:`calmjs.parse <calmjs-parse>`
      - `docs <https://github.com/calmjs/calmjs.parse>`__
      - .. image:: https://static.pepy.tech/badge/calmjs.parse/month
            :alt: calmjs.parse monthly downloads
            :target: https://pepy.tech/project/calmjs.parse
      - .. image:: https://static.pepy.tech/badge/calmjs.parse
            :alt: calmjs.parse total downloads
            :target: https://pepy.tech/project/calmjs.parse

.. toctree::
    :hidden:
    :maxdepth: 1

    charset-normalizer
    pandas
    markupsafe
    beautifulsoup
    chardet
    soupsieve
    lxml
    cssselect
    linkify-it-py
    bleach
    markdownify
    nh3
    html5lib
    html2text
    lxml-html-clean
    w3lib
    trafilatura
    courlan
    justext
    selectolax
    parsel
    rcssmin
    rjsmin
    dominate
    pyquery
    readability-lxml
    readabilipy
    inscriptis
    minify-html
    html-text
    jsmin
    resiliparse
    htmlmin
    csscompressor
    newspaper3k
    boilerpy3
    goose3
    news-please
    html-sanitizer
    yattag
    extruct
    htbuilder
    htpy
    mechanicalsoup
    airium
    calmjs-parse
    markyp
    html5-parser
    metadata_parser
    lightningcss
    fast-html
    simple-html
    hyperpython
    stdlib
