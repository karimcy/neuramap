"""GridQ — behind-the-meter BESS sizing & underwriting engine (Product Engineer layer).

Deterministic pricing core + agentic perimeter (see docs/02_architecture.md §1).
No LLM and no stochastic AI ever touches a number on the pricing path.

This is the v1 scaffold: subpackages are stubs with no logic yet. Data contracts
(Pydantic models / JSON Schema) are ratified and implemented at Milestone 1.
"""

__version__ = "0.0.1"
