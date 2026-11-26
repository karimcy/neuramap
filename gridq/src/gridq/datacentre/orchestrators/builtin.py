"""The adapters shipped with this package, registered on the registry's first use.

`gridq.datacentre.orchestrators.registry.orchestrator_adapters` imports this module the first time
it is read (its ``home``); nothing registers at package import. Each adapter registers under this
plug-in package's owner, never ``CORE_OWNER`` (D159).
"""

from __future__ import annotations

from gridq.datacentre.orchestrators.registry import OWNER, orchestrator_adapters
from gridq.datacentre.orchestrators.slurm import ADAPTER_VERSION, API_ID, SlurmAdapter

orchestrator_adapters.register(API_ID, SlurmAdapter(), owner=OWNER, version=ADAPTER_VERSION)
