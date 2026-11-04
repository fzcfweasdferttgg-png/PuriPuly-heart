from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest

from puripuly_heart.cli import main as cli


@pytest.mark.parametrize(("channel", "state"), [("self", "on"), ("self", "off"), ("peer", "off")])
def test_peer_terms_cannot_be_accepted_by_an_unrelated_capture_request(
    tmp_path, capsys, monkeypatch, channel, state
):
    monkeypatch.setattr(cli, "_instance", lambda _path: pytest.fail("must reject before discovery"))
    result = cli.main(
        [
            "--config",
            str(tmp_path / "settings.json"),
            "capture",
            "set",
            channel,
            state,
            "--accept-peer-terms",
        ]
    )

    output = capsys.readouterr()
    assert result == 3
    assert json.loads(output.err)["error"]["code"] == "invalid_input"
    assert output.out == ""


@pytest.mark.parametrize("value", ["not-a-number", "nan", "PRIVATE_CALIBRATION_SENTINEL"])
def test_calibration_rejects_invalid_numeric_input_without_discovery_or_disclosure(
    tmp_path, capsys, monkeypatch, value
):
    monkeypatch.setattr(cli, "_instance", lambda _path: pytest.fail("must reject before discovery"))
    result = cli.main(
        [
            "--config",
            str(tmp_path / "settings.json"),
            "overlay",
            "calibrate",
            "change",
            "--field",
            "offset_x",
            "--value",
            value,
        ]
    )

    output = capsys.readouterr()
    assert result == 3
    assert json.loads(output.err)["error"]["code"] == "invalid_input"
    assert output.out == ""
    assert value not in output.out + output.err
