"""`underwriting_basis_v1` (WP-UW-01): the evidence-gated study basis, as a GridQ plug-in.

A study basis registered beside the academic basis (``all_streams_enabled_study_v1``, D55). It
enforces gates, consents, size minimums, stacking rules and route to market from IC-4 evidence
objects, so a market stream enters the priced floor only on valid, verified third-party evidence.

- `gridq.underwriting.sources`: the published rules, retrieved and quoted.
- `gridq.underwriting.rules`: the versioned, content-hashed evidence-requirement table.
- `gridq.underwriting.basis`: `UnderwritingBasis`, the `NamedBasis` that enters ``input_hash``.
- `gridq.underwriting.gate`: the evidence verdict and the tighten-only registry decorator.
- `gridq.underwriting.engine`: `UnderwritingEngine`, the engine wrapper and its post-pass.
- `gridq.underwriting.eligibility`: the site eligibility engine (WP-UW-02, spec A6-02). It
  stands alone: nothing above imports it and it registers nothing.

**Registration.** This package lives outside the CORE-owned paths and registers through
`gridq.extensions` like any other workstream: ``load_plugin("gridq.underwriting")``. The owner
(the module name) and ``__version__`` then enter every run's ``input_hash`` under
``study_basis_extension``. Importing the package registers nothing; `UnderwritingEngine` loads the
plug-in when it is built (D102), and the CLI and app wiring is WP-UW-04's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gridq.extensions import ExtensionHost

__version__ = "1.0.0"


def register(host: ExtensionHost) -> None:
    """Register `UnderwritingBasis` as the study basis ``underwriting_basis_v1``."""
    from gridq.underwriting.basis import UnderwritingBasis

    host.register_study_basis(UnderwritingBasis)
