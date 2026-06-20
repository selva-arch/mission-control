"""
Has-this-roof-got-solar-panels classifier.

MVP backend: a multimodal vision model called per tile (zero training). Swap in
a fine-tuned CNN (`backend: cnn`) for cheap, fast, offline batch runs at scale.

Both backends return {has_panels: bool, confidence: float}. The orchestrator
mails only roofs classified no-solar above `min_confidence`; low-confidence
roofs should go to a human review queue (hybrid).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass


@dataclass
class Detection:
    has_panels: bool
    confidence: float
    note: str = ""


PROMPT = (
    "You are inspecting a top-down aerial photo of a single residential roof. "
    "Are there rooftop solar PV panels installed? Solar panels look like flat, "
    "dark, rectangular grids. Answer strictly as JSON: "
    '{"has_panels": true|false, "confidence": 0.0-1.0}.'
)


def detect(tile_png: bytes, cfg: dict) -> Detection:
    backend = cfg.get("detect", {}).get("backend", "vision_model")
    if backend == "vision_model":
        return _detect_vision(tile_png, cfg)
    if backend == "cnn":
        return _detect_cnn(tile_png, cfg)
    raise ValueError(f"Unknown detect backend: {backend}")


def _detect_vision(tile_png: bytes, cfg: dict) -> Detection:
    """Call a multimodal model with the tile. Wire your provider here.

    Kept provider-agnostic: implement `_call_vision(prompt, image_b64)` against
    whichever vision API you use (it should return JSON text). The b64 + prompt
    plumbing is done; only the HTTP call is left as an integration point so this
    file has no hard provider dependency.
    """
    image_b64 = base64.b64encode(tile_png).decode()
    raise NotImplementedError(
        "Wire _detect_vision to your vision API: send PROMPT + image_b64, parse "
        "the JSON reply into Detection(has_panels, confidence). "
        f"(image is {len(image_b64)} b64 chars, ready to send.)"
    )


def _detect_cnn(tile_png: bytes, cfg: dict) -> Detection:
    """Run a local fine-tuned panel detector (e.g. DeepSolar / YOLO seg)."""
    raise NotImplementedError(
        "Load your trained model and return Detection(has_panels, confidence)."
    )
