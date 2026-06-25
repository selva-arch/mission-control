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
import json
import os
import re
from dataclasses import dataclass

import requests


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


def detect(tile_png: bytes, cfg: dict,
           lat: float | None = None, lon: float | None = None) -> Detection:
    backend = cfg.get("detect", {}).get("backend", "vision_model")
    if backend == "nearmap":
        return _detect_nearmap(cfg, lat, lon)
    if backend == "vision_model":
        return _detect_vision(tile_png, cfg)
    if backend == "cnn":
        return _detect_cnn(tile_png, cfg)
    raise ValueError(f"Unknown detect backend: {backend}")


def _detect_nearmap(cfg: dict, lat: float | None, lon: float | None) -> Detection:
    """Authoritative detection via Nearmap AI (by coordinates, not the tile)."""
    from .nearmap import NearmapClient
    nm = cfg.get("nearmap", {})
    client = NearmapClient(
        nm["api_key"], packs=nm.get("packs", "solar,roof_char"),
        ai_path=nm.get("ai_path", "/ai/features/v4/features.json"),
        min_confidence=cfg.get("detect", {}).get("min_confidence", 0.5),
        aoi_size_m=nm.get("aoi_size_m", 25))
    res = client.solar(lat, lon)
    # Nearmap is authoritative: when it finds no PV we're confident it's a target.
    return Detection(
        has_panels=res.has_solar,
        confidence=res.confidence if res.has_solar else 1.0,
        note=f"roof={res.roof_area_sqm}m2 panels={res.panel_count} {res.error}".strip())


def _detect_vision(tile_png: bytes, cfg: dict) -> Detection:
    """Classify the roof tile with a multimodal model (Anthropic or OpenAI)."""
    v = cfg.get("detect", {}).get("vision", {})
    provider = v.get("provider", "anthropic")
    img_b64 = base64.b64encode(tile_png).decode()
    if provider == "anthropic":
        text = _call_anthropic(img_b64, v)
    elif provider == "openai":
        text = _call_openai(img_b64, v)
    else:
        raise ValueError(f"Unknown vision provider: {provider}")
    return _parse_verdict(text)


def _parse_verdict(text: str) -> Detection:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return Detection(False, 0.0, note=f"unparseable: {(text or '')[:80]}")
    try:
        d = json.loads(m.group(0))
        return Detection(bool(d.get("has_panels")),
                         float(d.get("confidence", 0.5)),
                         note=str(d.get("note", "")))
    except (ValueError, TypeError):
        return Detection(False, 0.0, note=f"bad json: {text[:80]}")


def _call_anthropic(img_b64: str, v: dict) -> str:
    key = v.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Set ANTHROPIC_API_KEY (or detect.vision.api_key)")
    model = v.get("model", "claude-haiku-4-5-20251001")
    body = {
        "model": model, "max_tokens": 120,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": "image/jpeg", "data": img_b64}},
            {"type": "text", "text": PROMPT},
        ]}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=60,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def _call_openai(img_b64: str, v: dict) -> str:
    key = v.get("api_key") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Set OPENAI_API_KEY (or detect.vision.api_key)")
    model = v.get("model", "gpt-4o-mini")
    body = {
        "model": model, "max_tokens": 120,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url":
                {"url": f"data:image/jpeg;base64,{img_b64}"}},
        ]}],
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=60,
                      headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _detect_cnn(tile_png: bytes, cfg: dict) -> Detection:
    """Run a local fine-tuned panel detector (e.g. DeepSolar / YOLO seg)."""
    raise NotImplementedError(
        "Load your trained model and return Detection(has_panels, confidence)."
    )
