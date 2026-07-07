####################
 Layered sanitizing
####################

Sanitizing is subtractive and layered: each configurable allowlist can only *remove* more than the layer below it, and
under all of them sits a baseline no policy can reach. The order matters, so read a kept ``style`` attribute as the
worked example.

A style declaration passes through three gates in turn. First the non-configurable safety baseline: ``expression()``
runs script in old IE, and ``url(javascript:...)`` carries a disallowed scheme, so either drops the whole declaration no
matter what the policy says. Then ``css_properties``, the property-*name* allowlist: a property outside the set is gone
even if its value is harmless. Only a declaration that clears both reaches ``allowed_styles``, the property-*value*
allowlist, which keeps it only when its value matches one of the patterns listed for the element's tag (or ``"*"``).

The layering is deliberately one-directional. ``allowed_styles`` *narrows* -- it can reject a value the earlier layers
would have kept, but it can never re-admit one they dropped. A caller who writes ``{"color": [r".*"]}`` has not opened a
hole: ``.*`` matches ``expression(alert(1))`` as a string, but the baseline already discarded that declaration two gates
earlier, so the pattern never sees it. This is why the value patterns are a *validation* step, not an *authorization*
one: they answer "is this known-good?", and the answer only ever shrinks what survives.

Keeping the dangerous-value baseline unconditional -- rather than folding it into ``css_properties`` or
``allowed_styles`` -- is what makes a permissive policy safe by construction. A caller tuning an allowlist is reasoning
about which benign values to accept; they are never one regex away from re-enabling script execution, because that
decision was taken out of the policy's hands entirely. The same shape governs the rest of the sanitizer: ``on*`` event
handlers, ``<script>``, and ``javascript:`` URLs are dropped below the allowlists, so no combination of ``tags``,
``attributes``, or ``attribute_filter`` settings can bring them back.
