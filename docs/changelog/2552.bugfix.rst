``turbohtml.etree`` covers three more ``lxml.html`` behaviors: ``ElementTree.iter`` iterates from the root element,
``ElementView.nsmap`` returns an empty mapping, and ``to_lxml`` / ``to_lxml_html`` strip characters XML cannot carry
from text, tails and attribute values instead of raising ``ValueError`` or dropping the attribute.
