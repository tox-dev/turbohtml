########################
 Migrating to turbohtml
########################

turbohtml replaces the HTML libraries it benchmarks against. None is API-compatible, so porting is a translation:
turbohtml uses one name per concept and a typed shape where those libraries spread the work across aliases, methods, and
treebuilder choices. This page maps each library to turbohtml; `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_ gets the deepest treatment because it shares the most surface.

.. toctree::
    :maxdepth: 1

    pandas
    markupsafe
    beautifulsoup
    lxml
    linkify-it-py
    bleach
    markdownify
    nh3
    html5lib
    html2text
    lxml-html-clean
    w3lib
    trafilatura
    selectolax
    parsel
    pyquery
    dominate
    inscriptis
    readability-lxml
    html-text
    resiliparse
    newspaper3k
    html-sanitizer
    extruct
    mechanicalsoup
    html5-parser
    stdlib
