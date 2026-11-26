"""Shared helpers for the IC-1 / IC-2 / IC-3 / IC-4 / IC-5 / IC-11 / IC-13 conformance tests
(tests/contracts/)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"

IC4_FIXTURES = sorted(FIXTURES.glob("ic4_*.synthetic.json"))
IC1_FIXTURES = sorted(FIXTURES.glob("ic1_*.synthetic.json"))
IC2_FIXTURES = sorted(FIXTURES.glob("ic2_*.synthetic.json"))
IC11_FIXTURES = sorted(FIXTURES.glob("ic11_*.synthetic.json"))
IC13_CLAIMS_FIXTURES = sorted(FIXTURES.glob("ic13_claims_*.synthetic.json"))
IC13_AUDIT_LOG = FIXTURES / "ic13_audit_log.synthetic.jsonl"
#: Head of the committed audit chain (seq 5). Changes only if an event or the canonical form does.
IC13_AUDIT_HEAD = "sha256:681a0da058ca2e69fb55703e83e3b88f1e9ebdae47564a6160cf54a514629e67"

#: Fixture-name prefix -> (module, class): the contract model each single-instance fixture is.
#: Strings, so a fresh interpreter (the PYTHONHASHSEED test) can import them by name.
MODEL_FOR_PREFIX: dict[str, tuple[str, str]] = {
    "ic11_surface_": ("gridq.contracts.thermal_flex", "ThermalFlexAvailability"),
    "ic11_instruction_": ("gridq.contracts.thermal_flex", "FlexInstruction"),
    "ic11_ack_": ("gridq.contracts.thermal_flex", "FlexAcknowledgement"),
    "ic3_envelope_": ("gridq.contracts.dispatch_envelope", "DispatchEnvelope"),
    "ic3_timeline_": ("gridq.contracts.dispatch_envelope", "ResolvedTimeline"),
    "ic13_": ("gridq.contracts.identity", "IdentityClaims"),
    "ic1_": ("gridq.contracts.connection", "ConnectionAgreement"),
    "ic2_": ("gridq.contracts.curtailment", "CurtailmentScenarioSet"),
    "ic4_": ("gridq.contracts.evidence", "EvidenceObject"),
}
#: A string field per prefix whose change must change the hash.
ID_FIELD_FOR_PREFIX: dict[str, str] = {
    "ic11_surface_": "surface_id",
    "ic11_instruction_": "instruction_id",
    "ic11_ack_": "ack_id",
    "ic3_envelope_": "envelope_id",
    "ic3_timeline_": "site_id",
    "ic13_": "jti",
    "ic1_": "agreement_ref",
    "ic2_": "set_id",
    "ic4_": "title",
}


def prefix_of(name: str) -> str:
    return next(p for p in MODEL_FOR_PREFIX if name.startswith(p))


#: Golden content hashes. A change here means either a fixture or the canonical form changed;
#: the second is a contract change and needs a new contract version.
GOLDEN_HASHES: dict[str, str] = {
    "ic11_ack_accepted.synthetic.json": (
        "sha256:8685221fc1055d915a585ffff101c20b2ef3a34b39a5f566b5d485fabc530bd8"
    ),
    "ic11_ack_partial.synthetic.json": (
        "sha256:3f758fded720712ec07e0ab2f45db5490c3438439de9b2af031e2aec9441abd9"
    ),
    "ic11_ack_rejected.synthetic.json": (
        "sha256:e7a9387ca2bc3931c7414b66673a0a25528112064c4d4018f5e2768a54a7e3a6"
    ),
    "ic11_instruction_winter_night.synthetic.json": (
        "sha256:67e7e2da64b768bdcd0a3e18afa2b53e978823fe6cb0ff1f5cd6406ed88965b4"
    ),
    "ic11_surface_chilled_water_modelled.synthetic.json": (
        "sha256:bbac57be8fd2e8b05fa131ec21659d20e1c67b0e33c04bbf618f0d38a2d07025"
    ),
    "ic11_surface_metered_trial.synthetic.json": (
        "sha256:9faa103a5e690f8a046f0f66aa9bcdcbea39a4c0db10c1db079270132a5a8aa4"
    ),
    "ic13_claims_customer.synthetic.json": (
        "sha256:c1dddcdb0e196fc8b0fb72abd4b37492ff849eea04d2f4738a090c9266934fad"
    ),
    "ic13_claims_lender_readonly.synthetic.json": (
        "sha256:97698765134a5f090ecfb096d4911bfe39cdef5ea07a5a6ae5989d0c30e9bfee"
    ),
    "ic13_claims_neura_ops.synthetic.json": (
        "sha256:348bdaaa54d0c3ec48857926064accc9ad01366f04497820e1d0c51368e340cb"
    ),
    "ic13_claims_verifier.synthetic.json": (
        "sha256:3c9de5909f28370da09b379f1382ca8a8c4ed9ad5f2f3090a80165b8f9dba97c"
    ),
    "ic1_archetype_firm.synthetic.json": (
        "sha256:93f1919f8179e71eec8628e85b60a2095a395e0221fc4b188bf42f31847306cc"
    ),
    "ic1_archetype_fully_non_firm.synthetic.json": (
        "sha256:9af61222cc0d62140960d2f3f85d90128e5d6071c31882b427b56aa67bcbb790"
    ),
    "ic1_archetype_hybrid.synthetic.json": (
        "sha256:c6ee3fb9bc76f2811be935c53cc8549b19a0b66aacffd57d567c33ac35891505"
    ),
    "ic2_modelled_hh_only.synthetic.json": (
        "sha256:034cd711fa8f2f61ddd237660d18c8ad7a22f78246ddf840b64fec0acafcf2c0"
    ),
    "ic2_replay_three_years.synthetic.json": (
        "sha256:56a525894228a739aeac359a53cb604059f1a9a98d97fd0d5a5f949356b6bdcf"
    ),
    "ic4_expired_aggregator_agreement.synthetic.json": (
        "sha256:36f92d01408edd7d0369cb0cc6cea6f90e2c6e243b6c11547b3612b235f6f107"
    ),
    "ic4_no_expiry_customer_consent.synthetic.json": (
        "sha256:8868f4515f6d092f314c9c5905d8ecea32b9284d4d84092abbe961bedd613b56"
    ),
    "ic4_unverified_regulatory_publication.synthetic.json": (
        "sha256:4d172a7242b0c83b32cd98d1bb7d54e62ed24f8f3fa36717fbea7cee39471d99"
    ),
    "ic4_valid_connection_offer.synthetic.json": (
        "sha256:5db5a645f88c7a252705ee3bbb5f6f9095b1b3225daac26a337d3283ee40fe21"
    ),
}


#: Fixtures at contract version 1.1.0 (WP-BO-06). A separate folder, so the 1.0.0 globs above and
#: their ``len(...)`` assertions are undisturbed. Each document hash is the SHA-256 of
#: ``"SYNTHETIC DOCUMENT: " + title``: no real document exists.
FIXTURES_V110 = FIXTURES / "v1_1_0"
ALL_V110_FIXTURES = sorted(FIXTURES_V110.glob("ic*.synthetic.json"))
IC4_V110_FIXTURES = sorted(FIXTURES_V110.glob("ic4_*.synthetic.json"))

#: Golden content hashes of the 1.1.0 fixtures. Kept apart from `GOLDEN_HASHES`, which is not
#: edited: the 1.0.0 fixtures keep their hashes under 1.1.0 code.
GOLDEN_HASHES_V110: dict[str, str] = {
    "ic4_data_processing_agreement.synthetic.json": (
        "sha256:9633939dd0c43f596edd28a666acd4abf22311cc6941ec07458becd89eb70151"
    ),
    "ic4_self_issued_decision_record.synthetic.json": (
        "sha256:518595ab3bd374411227db48287b619c9c47f9e08f8091776b01d6a2df6c9ed4"
    ),
    "ic4_self_issued_baseline_method.synthetic.json": (
        "sha256:7db1389a7de85da60c3d797eb0c1ecd4ed549b853ef879a8f24460ff1e6bf6ea"
    ),
    "ic4_self_issued_trial_record.synthetic.json": (
        "sha256:7b4ac2d71f2e1d686f4776efdc44769cabe2fae419f52085a59d4f42c1b4a260"
    ),
    "ic4_legal_opinion.synthetic.json": (
        "sha256:91ccabb39d79c96b96bb393b4ca9fd7afaa86f32359f971b03ffd688b00445ed"
    ),
    "ic1_hybrid_evidence_backed.synthetic.json": (
        "sha256:122618785497c4f998cbdb76b49746be63415bf56cc09c4a6187a333c3d7c6b1"
    ),
    "ic2_modelled_v110.synthetic.json": (
        "sha256:69acdae915c6b4c0c6e6610bddea1a04b6f83b9300c6216b0935eaead2f51ddf"
    ),
    "ic11_surface_trialled_self_issued.synthetic.json": (
        "sha256:3463e5d8345803704915da82b504b58745872448fa1da3b073984fedf8e50c09"
    ),
}


#: IC-3 1.0.0 fixtures (WP-SIZE-07), in their own folder for the same reason. Built by
#: ``_ic3_fixtures.py``; every input and evidence hash in them is the SHA-256 of a synthetic
#: placeholder text, and the hashes they bind to are those of the IC-1, IC-2 and IC-11 fixtures.
FIXTURES_IC3 = FIXTURES / "ic3"
IC3_FIXTURES = sorted(FIXTURES_IC3.glob("ic3_*.synthetic.json"))

#: Golden content hashes of the IC-3 fixtures (a new table: `GOLDEN_HASHES` is not edited).
GOLDEN_HASHES_IC3: dict[str, str] = {
    "ic3_envelope_clock_change_week.synthetic.json": (
        "sha256:6aaf42c703ef6ce090cb26d84b7a6962c146ac804543b64bc58bb71815096c45"
    ),
    "ic3_envelope_dc_thermal.synthetic.json": (
        "sha256:a1ad61ac55d2ce0e717ee0e99c88a66d4783cb9793e6fc2f05e18ba893f5f856"
    ),
    "ic3_envelope_hybrid_bess.synthetic.json": (
        "sha256:72e1ad4a3d218df1877813f7c09dcf225c21cde2cee1979adb1ce670f061cfad"
    ),
    "ic3_envelope_hybrid_bess_successor.synthetic.json": (
        "sha256:2811f1700f5c9b217c1ffcf3a3609d154522d6d1beac7c86026bd7516dbbacdb"
    ),
    "ic3_timeline_clock_change_week.synthetic.json": (
        "sha256:0c3a3dc0a14d9b1cf366bfdc052e128c2510347a418d0e141faf611e13334453"
    ),
}


#: IC-5 1.0.0 fixtures (WP-EDGE-01), in their own folder. Built by ``_ic5_fixtures.py``: one link
#: profile and one frame chain per direction (JSONL, one canonical frame per line).
FIXTURES_IC5 = FIXTURES / "ic5"
IC5_FIXTURES = sorted(FIXTURES_IC5.glob("ic5_*.synthetic.json*"))

#: Golden hashes of the IC-5 fixtures (a new table): the profile's content hash, and the head frame
#: hash of each chain (which binds every frame before it).
GOLDEN_HASHES_IC5: dict[str, str] = {
    "ic5_link_profile_hybrid.synthetic.json": (
        "sha256:441fdb77bb83ae7b394eef59bbe6b4d5279193a21340880aaa43ff44c599f09f"
    ),
    "ic5_cloud_to_edge_hybrid.synthetic.jsonl": (
        "sha256:bb7e6853ab2b1581064671f729cc03e3d7ecef73ebc0fa5ac9526e7cc044f2dc"
    ),
    "ic5_edge_to_cloud_hybrid.synthetic.jsonl": (
        "sha256:4ea11211f4d4fd35f6b55a562b8fdd3d343b9e78b00088136991c7409ff223b9"
    ),
}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def load_v110(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_V110 / name).read_text(encoding="utf-8"))
    return data


def load_ic3(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_IC3 / name).read_text(encoding="utf-8"))
    return data


def dumps(data: Any) -> str:
    return json.dumps(data)


# ------------------------------------------------------------------ mutation runner

#: The repository root and its ``src``: a mutant's interpreter imports ``gridq`` from here.
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

#: Runs in a fresh interpreter. A finder placed first on ``sys.meta_path`` serves the named module
#: from its own file with ``old`` replaced by ``new`` (in memory; nothing on disk changes), so every
#: importer, including the modules that depend on it, sees the mutant. Then pytest runs one node.
#: Exit 1 is pytest's "tests ran and failed". A mutant whose target is not exactly once in the
#: source, or that cannot load, or that was not the module actually imported, exits 3; a
#: collection error exits 2. Neither passes as red. Moved here from the IC-11 tests (WP-BO-06).
MUTANT_RUNNER = """
import importlib, importlib.abc, importlib.util, sys
name, path, old, new, nodeid = sys.argv[1:6]
src = open(path, encoding="utf-8").read()
if old:
    if src.count(old) != 1:
        print("MUTATION TARGET NOT FOUND EXACTLY ONCE")
        sys.exit(3)
    src = src.replace(old, new)

class Mutant(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, target_path=None, target=None):
        if fullname != name:
            return None
        return importlib.util.spec_from_file_location(fullname, path, loader=self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        exec(compile(src, path, "exec"), module.__dict__)

finder = Mutant()
sys.meta_path.insert(0, finder)
try:
    module = importlib.import_module(name)
    importlib.import_module("gridq.contracts")
except BaseException as exc:  # a mutant that cannot load must not pass as "red"
    print("MUTANT DID NOT LOAD:", repr(exc))
    sys.exit(3)
if module.__spec__.loader is not finder or sys.modules[name] is not module:
    print("MUTANT IS NOT THE MODULE IMPORTED")
    sys.exit(3)
import pytest
sys.exit(pytest.main([nodeid, "-q", "-o", "addopts=", "-p", "no:cacheprovider", "--noconftest",
                      "-W", "ignore"]))
"""


def run_mutant(module: str, old: str, new: str, nodeid: str) -> int:
    """Exit code of pytest on ``nodeid`` with ``module`` mutated (``old`` -> ``new``) in a fresh
    interpreter. ``old == ""`` is the control: the module is served unmutated by the same finder."""
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None, module
    res = subprocess.run(
        [sys.executable, "-c", MUTANT_RUNNER, module, spec.origin, old, new, nodeid],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        check=False,
    )
    return res.returncode


# ------------------------------------------------------------------ IC-9 (WP-SITE-07)
# Appended at the end (not beside the other tables) so it merges cleanly with sibling branches.

FIXTURES_IC9 = FIXTURES / "ic9"
IC9_FIXTURES = sorted(FIXTURES_IC9.glob("ic9_*.synthetic.json"))

#: Golden content hashes of the IC-9 fixtures (``fixtures/ic9/``). Pinned once; never re-pinned.
GOLDEN_HASHES_IC9: dict[str, str] = {
    "ic9_declaration_clock_change_autumn.synthetic.json": (
        "sha256:20305f0754d139a3a43306f1cd4aaa5ecee73dc72f558eb742fff94000ec88c1"
    ),
    "ic9_declaration_clock_change_spring.synthetic.json": (
        "sha256:f74207b12b127a85e7bc8a29054d02ba09c6bff865c65649cf6de66d1d960083"
    ),
    "ic9_declaration_gpu_campus.synthetic.json": (
        "sha256:7bf795257e7ba98697c8b2f529b3f339d8097b742f517fe0896146369a89da4c"
    ),
    "ic9_declaration_gpu_campus_successor.synthetic.json": (
        "sha256:6a2d05747a8fd1a5605598e67aae1fb34715d8d84bac48d8b5fb6297db38f3f6"
    ),
    "ic9_declaration_relative_curve.synthetic.json": (
        "sha256:b920688a37746e3099632a3a8dd55e8acc99ecf3632f0585f6c4dadcd9957a3b"
    ),
    "ic9_timeline_clock_change_spring.synthetic.json": (
        "sha256:13f72d967022de1e59f77989203641fc930f47c5d7edf2cc535c052b5eb6700a"
    ),
}


def load_ic9(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_IC9 / name).read_text(encoding="utf-8"))
    return data


# ------------------------------------------------------------------ IC-14 (WP-TEL-01)
# Appended at the end (not beside the other tables) so it merges cleanly with sibling branches.

FIXTURES_IC14 = FIXTURES / "ic14"
IC14_FIXTURES = sorted(FIXTURES_IC14.glob("ic14_*.synthetic.json*"))

#: Golden hashes of the IC-14 fixtures (``fixtures/ic14/``): each record's content hash, and for the
#: batch chain (JSONL) the hash of its last batch, which binds every batch before it through
#: ``prev_batch_hash`` and ``prior_epoch_head``. Pinned once; never re-pinned.
GOLDEN_HASHES_IC14: dict[str, str] = {
    "ic14_annotation_flatline.synthetic.json": (
        "sha256:1bfa7fd717217b285924e4bfb425c7aeb47010150ad4bc35d57922a4bd742cb2"
    ),
    "ic14_batches_site_power_meter.synthetic.jsonl": (
        "sha256:76603c2d91a8791a06b721e70cbceea2295d7945fac7d50bc4f7bc9fc8f56c8f"
    ),
    "ic14_curated_site_power_1min_rev0.synthetic.json": (
        "sha256:a645bfc582e7961235bca6aeddd8c4e46b1ba37fe89541ccdd7e61d10c55391a"
    ),
    "ic14_curated_site_power_1min_rev1.synthetic.json": (
        "sha256:0a9c99b00182f55dc82db33064cda665cb09dd05d96838eb5cbfadf7ff38673d"
    ),
    "ic14_descriptor_bess_soc.synthetic.json": (
        "sha256:0bcf6dc30476d4654a81cadee139e6269d5a153edb99f6baf5938d225111a9d9"
    ),
    "ic14_descriptor_site_power_meter.synthetic.json": (
        "sha256:619d6b108074d3e1b1940bcb8ae6156566044e42334c6504df40494bf98390f3"
    ),
    "ic14_descriptor_site_power_scada.synthetic.json": (
        "sha256:58acb8cd0da93b58e752d7598aa610484fb818a69c6263cf0ead3c96611c7128"
    ),
}


def load_ic14(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES_IC14 / name).read_text(encoding="utf-8"))
    return data
