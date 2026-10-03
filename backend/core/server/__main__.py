"""Entry point for ``python -m core.server``.

Using this instead of ``python -m core.server.app`` avoids the runpy warning
about ``core.server.app`` already being imported by the package ``__init__``.
"""

from .app import main

main()
