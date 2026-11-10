"""Coordinates: a geographic point (lat, lon)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Coordinates:
    lat: float
    lon: float
