"""The plug-in module the chassis loads: ``load_plugin("gridq.datacentre.plugin")``.

Registers the ``data_centre`` load archetype through IC-12's `ExtensionHost`, from this package,
with no edit to any CORE-owned path. Importing `gridq.datacentre` registers nothing; only loading
this module through the chassis does.
"""

from __future__ import annotations

from typing import Final

from gridq.datacentre.archetype import ARCHETYPE_ID, DATA_CENTRE
from gridq.extensions import ExtensionHost

__all__ = ["ARCHETYPE_ID", "PLUGIN_MODULE", "__version__", "register"]

__version__ = "1.0.0"

PLUGIN_MODULE: Final = "gridq.datacentre.plugin"


def register(host: ExtensionHost) -> None:
    host.register_load_archetype(DATA_CENTRE)
