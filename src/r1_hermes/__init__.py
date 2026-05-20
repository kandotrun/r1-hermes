"""Secure Rabbit R1 gateway for Hermes Agent."""

from .adapter import R1HermesAdapter, R1HermesConfig
from .hermes_runner import HermesCliRunner, build_session_name
from .qr import build_pairing_payload
from .r1_client import R1ProbeClient, R1ProbeError, R1ProbeResult

__all__ = [
    "HermesCliRunner",
    "R1HermesAdapter",
    "R1HermesConfig",
    "R1ProbeClient",
    "R1ProbeError",
    "R1ProbeResult",
    "build_pairing_payload",
    "build_session_name",
]
