########################
 Migrating to turbohtml
########################

turbohtml replaces the HTML libraries it benchmarks against. None is API-compatible, so porting is a translation:
turbohtml uses one name per concept and a typed shape where those libraries spread the work across aliases, methods, and
treebuilder choices. This page maps each library to turbohtml; `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_ gets the deepest treatment because it shares the most surface.

.. toctree::
    :maxdepth: 1

    markupsafe
    lxml
    beautifulsoup
    html5lib
    w3lib
    bleach
    html2text
    parsel
    markdownify
    selectolax
    pyquery
    nh3
    inscriptis
    linkify-it-py
    lxml-html-clean
    html-sanitizer
    html5-parser
    resiliparse
    stdlib
