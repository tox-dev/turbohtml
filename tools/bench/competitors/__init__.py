"""
One module per competitor library.

Each module imports exactly one competitor and exposes ``OPERATIONS`` mapping the operations it implements to
``(timing function, label)``. The worker imports a single one of these by name, so a competitor venv only ever needs
that one library installed.
"""

from __future__ import annotations
