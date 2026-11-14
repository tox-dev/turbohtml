Minifying CSS a second time no longer changes a ``u+`` followed by a hex letter, a ``?`` followed by a hex digit in a
unicode-range token, a nested ``calc()`` left open at the end of the input, or an escape before a closer or the end of
the input. The whitespace that terminates a hex escape is now dropped when the next character cannot continue the
escape.
