#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone


def main() -> int:
    raw_payload = sys.stdin.read().strip() or os.environ.get("ORIN_PAYLOAD_JSON", "{}")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "command": os.environ.get("ORIN_COMMAND", "PING_TEST"),
                "hostname": socket.gethostname(),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
