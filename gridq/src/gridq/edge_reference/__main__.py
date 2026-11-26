"""``python -m gridq.edge_reference --role relay|edge-twin|cloud-twin --port 0 --profile P ...``

Prints one JSON line ``{"url": ..., "role": ...}`` when listening, then serves until interrupted.
The profile must be a ``SYNTHETIC-`` site's IC-5 `LinkProfile`; the twins also take the IC-3
envelope they hold (``--envelope``) and the simulated boot instant (``--start``).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from gridq.contracts.dispatch_envelope import DispatchEnvelope
from gridq.contracts.edge_cloud import ControlStage, LinkProfile
from gridq.edge_reference.http import ReferenceServer
from gridq.edge_reference.machines import Simulation


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--role", choices=("relay", "edge-twin", "cloud-twin"), required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--envelope", type=Path)
    ap.add_argument(
        "--start", required=True, help="simulated boot instant, e.g. 2027-01-04T15:50:00Z"
    )
    ap.add_argument(
        "--stage", choices=[s.value for s in ControlStage], default="l2_bess_closed_loop"
    )
    ap.add_argument(
        "--soe-kwh", type=int, default=0, help="the cloud twin's planned state of energy"
    )
    args = ap.parse_args(argv)
    profile = LinkProfile.model_validate_json(args.profile.read_text(encoding="utf-8"))
    envelope = (
        DispatchEnvelope.model_validate_json(args.envelope.read_text(encoding="utf-8"))
        if args.envelope
        else None
    )
    sim = Simulation(
        args.role,
        profile,
        boot_utc=datetime.fromisoformat(args.start.replace("Z", "+00:00")),
        envelope=envelope,
        stage=ControlStage(args.stage),
        soe_kwh=args.soe_kwh,
    )
    server = ReferenceServer(sim, args.port)
    print(json.dumps({"url": server.url, "role": args.role}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
