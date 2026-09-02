"""MCP server for producing verifiable FORRT replication chains.

Companion to `replication-radar` (discovery) and the OpenAIRE MCP (literature
search). This package covers the *production* half: reusing a published
constellation as a starting point, and proving that what goes into a signed
nanopublication is grounded in its source.

The core (`api`, `constellation`, `quotes`) imports nothing beyond the stdlib
except `pypdf`; `mcp` is needed only to run the server wrapper.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .constellation import prior_work, summary
from .quotes import verify_quote

__all__ = ["prior_work", "summary", "verify_quote", "__version__"]
