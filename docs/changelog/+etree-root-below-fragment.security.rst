Fix an out-of-bounds write through ``ElementView.getroottree().getroot()`` for elements in shadow trees and document
fragments; the root is now the outermost element.
