"""Secure Rabbit R1 gateway for Hermes Agent."""

from .adapter import R1HermesAdapter, R1HermesConfig
from .qr import build_pairing_payload

__all__ = ["R1HermesAdapter", "R1HermesConfig", "build_pairing_payload"]
