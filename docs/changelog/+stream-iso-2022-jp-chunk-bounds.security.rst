Prevent an out-of-bounds write in :class:`~turbohtml.IncrementalParser` when an ISO-2022-JP chunk ends on the escape
byte that cuts a two-byte character short.
