from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import socket
import sys

from .app_paths import default_paths


@dataclass(slots=True)
class CommitReply:
    ok: bool
    detail: str


class IBusBridgeClient:
    def __init__(self, socket_path: Path | None = None) -> None:
        self.socket_path = socket_path or default_paths().engine_socket

    def commit_text(self, text: str) -> CommitReply:
        request = {"action": "commit_text", "text": text}
        return self._send(request)

    def ping(self) -> CommitReply:
        return self._send({"action": "ping"})

    def _send(self, payload: dict[str, str]) -> CommitReply:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self.socket_path))
                client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                response = client.recv(65536)
        except OSError as exc:
            return CommitReply(ok=False, detail=str(exc))

        if not response:
            return CommitReply(ok=False, detail="No response from Gabbee IBus bridge.")

        try:
            body = json.loads(response.decode("utf-8"))
        except json.JSONDecodeError:
            return CommitReply(ok=False, detail="Bridge returned invalid JSON.")

        return CommitReply(ok=bool(body.get("ok")), detail=str(body.get("detail", "")))


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    text = " ".join(argv).strip() if argv else sys.stdin.read().strip()
    if not text:
        print("gabbee-commit requires text on argv or stdin.", file=sys.stderr)
        return 1

    reply = IBusBridgeClient().commit_text(text)
    if not reply.ok:
        print(reply.detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
