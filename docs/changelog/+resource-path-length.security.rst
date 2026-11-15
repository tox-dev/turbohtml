Refuse an ``xsl:import`` or RELAX NG ``externalRef``/``include`` path longer than the platform path limit, even when it
would normalize shorter, so a long ``href`` is rejected quickly.
