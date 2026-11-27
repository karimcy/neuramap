"""Released library versions and the source hash each one had (append only; see `identity`).

Excluded from the library hash, because it records the hash.
"""

from __future__ import annotations

from typing import Final

RELEASES: Final[dict[str, str]] = {
    "1.0.0": "sha256:f23bbb03234adfb702efbf8190ca0878e9cfbdc59879bda07a53da718dec02a3",
    # 1.0.1, a patch (integration of WP-SIZE-02 into roadmap/p1-foundations): the branch's
    # decision placeholders in the package docstrings became D176 to D179, and the version
    # constant moved. No row, bound or default changed. 1.0.0 is the branch release, unedited.
    "1.0.1": "sha256:97d71c579cbb1f62697219ee7112935c76e77444374f911b80b375e59d6e3f75",
}
