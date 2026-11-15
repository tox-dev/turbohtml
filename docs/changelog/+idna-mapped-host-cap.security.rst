Converting a long internationalized host to punycode now takes time close to linear in its length, and a host that
mapping lengthens past 16384 code points stays Unicode.
