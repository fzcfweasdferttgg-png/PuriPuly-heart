from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

PROTOCOL_VERSION = 1

WorkerCommandType = Literal["init", "decode", "shutdown"]
WorkerStatusType = Literal["ready", "ack", "error", "transcript", "bye"]


@dataclass(frozen=True, slots=True)
class InitCommand:
    provider: str
    model_dir: str
    provider_type: str = "directml"
    device: int = 0
    num_threads: int = 3
    feature_dim: int = 128
    language_hint: str | None = None
    hotwords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cmd": "init",
            "provider": self.provider,
            "model_dir": self.model_dir,
            "provider_type": self.provider_type,
            "device": self.device,
            "num_threads": self.num_threads,
            "feature_dim": self.feature_dim,
        }
        if self.language_hint is not None:
            payload["language_hint"] = self.language_hint
        if self.hotwords:
            payload["hotwords"] = list(self.hotwords)
        return payload


@dataclass(frozen=True, slots=True)
class DecodeCommand:
    audio_b64: str
    sample_rate_hz: int
    is_final: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "cmd": "decode",
            "audio_b64": self.audio_b64,
            "sample_rate_hz": self.sample_rate_hz,
            "is_final": self.is_final,
        }


@dataclass(frozen=True, slots=True)
class ShutdownCommand:
    def to_dict(self) -> dict[str, Any]:
        return {"cmd": "shutdown"}


WorkerCommand = InitCommand | DecodeCommand | ShutdownCommand


def encode_command(command: WorkerCommand) -> str:
    return json.dumps(command.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_command(line: str) -> WorkerCommand:
    data = json.loads(line)
    cmd = data.get("cmd")
    if cmd == "init":
        raw_hotwords = data.get("hotwords", ())
        hotwords = tuple(raw_hotwords) if isinstance(raw_hotwords, list) else ()
        return InitCommand(
            provider=str(data["provider"]),
            model_dir=str(data["model_dir"]),
            provider_type=str(data.get("provider_type", "directml")),
            device=int(data.get("device", 0)),
            num_threads=int(data.get("num_threads", 3)),
            feature_dim=int(data.get("feature_dim", 128)),
            language_hint=data.get("language_hint"),
            hotwords=hotwords,
        )
    if cmd == "decode":
        return DecodeCommand(
            audio_b64=str(data["audio_b64"]),
            sample_rate_hz=int(data.get("sample_rate_hz", 16000)),
            is_final=bool(data.get("is_final", True)),
        )
    if cmd == "shutdown":
        return ShutdownCommand()
    raise ValueError(f"unknown command: {cmd}")


@dataclass(frozen=True, slots=True)
class ReadyMessage:
    def to_dict(self) -> dict[str, Any]:
        return {"status": "ready", "version": PROTOCOL_VERSION}


@dataclass(frozen=True, slots=True)
class AckMessage:
    def to_dict(self) -> dict[str, Any]:
        return {"status": "ack"}


@dataclass(frozen=True, slots=True)
class TranscriptMessage:
    text: str
    is_final: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"status": "transcript", "text": self.text, "is_final": self.is_final}


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": "error", "error": self.error}


@dataclass(frozen=True, slots=True)
class ByeMessage:
    def to_dict(self) -> dict[str, Any]:
        return {"status": "bye"}


WorkerMessage = ReadyMessage | AckMessage | TranscriptMessage | ErrorMessage | ByeMessage


def encode_message(message: WorkerMessage) -> str:
    return json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_message(line: str) -> WorkerMessage:
    data = json.loads(line)
    status = data.get("status")
    if status == "ready":
        return ReadyMessage()
    if status == "ack":
        return AckMessage()
    if status == "transcript":
        return TranscriptMessage(text=str(data.get("text", "")), is_final=bool(data.get("is_final", True)))
    if status == "error":
        return ErrorMessage(error=str(data.get("error", "unknown")))
    if status == "bye":
        return ByeMessage()
    raise ValueError(f"unknown status: {status}")
