``Node.find()`` and ``Node.find_all()`` with a regex or callable ``text`` no longer write out of bounds when a callable
filter adds elements mid-search, and skip those elements.
