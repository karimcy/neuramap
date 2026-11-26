"""NeuraMap copy only: leave out of the run the tests that check GridQ against the customers' own site data.

That data (src/gridq/governance/customer_data_manifest.json: data/sites, examples/real, deliverables) is not copied
into NeuraMap (README_NEURAMAP.md), so those checks belong with the data in NeuraEnergy/uk. The tests listed in
neuramap_customer_data_tests.txt are exactly the ones that read it; they are deselected (not collected into the run).
Every other test in the same files still runs, and a test that fails for any other reason stays red.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
LISTED = frozenset(l.strip() for l in (HERE / "neuramap_customer_data_tests.txt").read_text().splitlines() if l.strip())


def _site_data_held() -> bool:
    d = HERE / "data" / "sites"
    return d.is_dir() and any(p.is_file() for p in d.rglob("*"))


def pytest_collection_modifyitems(config, items):
    if _site_data_held():
        return
    keep, drop = [], []
    for item in items:
        (drop if item.nodeid in LISTED else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
