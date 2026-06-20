"""
Addressing + mail export.

No resident name needed — address "To the Homeowner". Emits a mail-merge CSV
(address + flyer path) suitable for Australia Post addressed mail or a mail
house. For unaddressed campaigns you instead book delivery by postcode/route.
"""

from __future__ import annotations

import csv
from pathlib import Path


def write_mail_csv(rows: list[dict], out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cols = ["salutation", "address", "suburb", "postcode",
            "system_kw", "annual_saving", "flyer_path"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return out_path
