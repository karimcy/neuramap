# knowledge_base (WP-GRC-14)

One lightweight store for ADRs, the assumption changelog and meeting capture, linked to registry evidence.

- `index.json` - generated: every assumption release with its changelog heading and ADRs. Do not edit.
- `links.yaml` - hand-kept: release -> `docs/changelog.md` heading, and ADR ids where one exists.
- `adr_index.json` - snapshot of ADR front matter from the Atlas (`roadmap/adr/`). Refresh with
  `python -m gridq.dev.knowledge_base sync-adrs <atlas>/roadmap/adr`. The ADRs themselves live in the Atlas.
- `meetings/` - one file per meeting, from `meetings/TEMPLATE.md`. Record only what was said or decided.

Commands: `python -m gridq.dev.knowledge_base build | check`. `tests/test_knowledge_base.py` enforces that every
release links to a changelog section that names it, and that every ADR it cites exists.
