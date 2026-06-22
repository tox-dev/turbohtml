########################
 Migrating to turbohtml
########################

turbohtml replaces the HTML libraries it benchmarks against. None is API-compatible, so porting is a translation:
turbohtml uses one name per concept and a typed shape where those libraries spread the work across aliases, methods, and
treebuilder choices. This page maps each library to turbohtml; `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_ gets the deepest treatment because it shares the most surface.

.. toctree::
    :maxdepth: 1

    beautifulsoup
    lxml
    selectolax
    resiliparse
    html5-parser
    parsel
    html5lib
    pyquery
    stdlib
    w3lib
    markupsafe
    bleach-linkify
    linkify-it-py
    bleach-clean
    nh3
    lxml-html-clean
    html-sanitizer
    html2text-markdownify
    inscriptis
