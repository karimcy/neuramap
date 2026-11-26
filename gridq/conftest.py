"""NeuraMap copy only: skip, with the reason, the tests that read the customers' own site data.

That data (src/gridq/governance/customer_data_manifest.json: data/sites, examples/real, deliverables) is not copied
into this public repository (README_NEURAMAP.md). The tests listed in neuramap_customer_data_tests.txt failed here
for that reason alone on the first run of the copy; each is skipped only while the data is absent, and runs as
written once it is present. A test that fails for any other reason is not listed and stays red.
"""
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LISTED = frozenset(l.strip() for l in (HERE / "neuramap_customer_data_tests.txt").read_text().splitlines() if l.strip())


def _site_data_held() -> bool:
    d = HERE / "data" / "sites"
    return d.is_dir() and any(p.is_file() for p in d.rglob("*"))


def pytest_collection_modifyitems(config, items):
    if _site_data_held():
        return
    mark = pytest.mark.skip(reason="needs the customers' site data, which is not held in the NeuraMap copy")
    for item in items:
        if item.nodeid in LISTED:
            item.add_marker(mark)
