import stat
import sys
from pathlib import Path

import pytest

from r1_hermes.qr import write_qr_png


class FakeQrImage:
    save_calls = []

    def save(self, target, **kwargs):
        self.save_calls.append(kwargs)
        if hasattr(target, "write"):
            target.write(b"fake-qr-png")
            return
        with Path(target).open("wb") as handle:
            handle.write(b"fake-qr-png")


class FakeQrCodeModule:
    @staticmethod
    def make(_payload):
        return FakeQrImage()


@pytest.fixture(autouse=True)
def fake_qrcode(monkeypatch):
    FakeQrImage.save_calls = []
    monkeypatch.setitem(sys.modules, "qrcode", FakeQrCodeModule())


def test_write_qr_png_creates_owner_only_file_and_new_parent(tmp_path):
    output = tmp_path / "secret-qrs" / "pairing.png"

    written = write_qr_png("dummy-payload", output)

    assert written == output
    assert output.read_bytes() == b"fake-qr-png"
    assert FakeQrImage.save_calls == [{"format": "PNG"}]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700


def test_write_qr_png_refuses_to_overwrite_by_default(tmp_path):
    output = tmp_path / "pairing.png"
    output.write_bytes(b"existing-secret-qr")
    output.chmod(0o600)

    with pytest.raises(FileExistsError):
        write_qr_png("dummy-payload", output)

    assert output.read_bytes() == b"existing-secret-qr"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_write_qr_png_force_overwrites_with_owner_only_permissions(tmp_path):
    output = tmp_path / "pairing.png"
    output.write_bytes(b"old-secret-qr")
    output.chmod(0o644)

    write_qr_png("dummy-payload", output, overwrite=True)

    assert output.read_bytes() == b"fake-qr-png"
    assert FakeQrImage.save_calls == [{"format": "PNG"}]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
