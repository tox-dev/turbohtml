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

``transform_tags`` is the one step that *adds* rather than removes -- it renames an element and can inject attributes --
so its placement is what keeps the model intact. The rename runs at the very top, before the allowlist reads the tag,
and then the walk continues on the renamed element as if the author had written the target: the allowlist decides its
disposition, the unsafe-tag baseline still escapes a ``script`` or ``iframe`` target, and every injected attribute joins
the element's own to be scrubbed by the same gates below. A transform therefore chooses an element's *name* while every
gate underneath still governs its *safety*. Putting the additive step above the subtractive stack, instead of letting it
write past the allowlist, is why ``{"b": "script"}`` cannot smuggle a live ``<script>`` and an injected ``href`` cannot
carry a ``javascript:`` URL -- the transform hands its output back to the pipeline rather than around it.

``isolate_named_props`` is the other rewriting step, and its design turns on a constraint the layered model does not:
turbohtml has no live DOM. DOM clobbering exploits *named access* -- an ``id`` or ``name`` whose value matches a
built-in property makes that property resolve to the attacker's element (``<input name="attributes">`` shadows
``form.attributes``, ``<img name="body">`` shadows ``document.body``). Nothing about such an attribute is malformed, so
the allowlist keeps it; only a defense aimed at the collision itself removes it. DOMPurify offers two: ``SANITIZE_DOM``
tests ``value in document`` and drops a real collision, and ``SANITIZE_NAMED_PROPS`` prefixes every ``id``/``name``
value with ``user-content-``. The first needs the running engine's property set, which only a live DOM can enumerate;
the second is a pure string transform. turbohtml sanitizes a parsed tree with no DOM to probe, so it takes the second
design: prefixing is unconditional and complete, where a static reimplementation of the ``in document`` check would be a
hand-maintained name list that silently misses whatever property a future engine adds. Applying the prefix *after*
``attribute_filter`` -- the last configurable gate -- rather than before, means no filter can hand back a clobbering
value the isolation then fails to namespace; like the dangerous-value baseline, the guarantee sits below the caller's
reach. Idempotence closes the loop: a value already carrying the prefix is left untouched, so sanitizing sanitized
output is a fixpoint rather than a growing stack of ``user-content-user-content-`` markers.
