"""Run the private desktop backend without importing a GUI toolkit."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Firik hybrid desktop stdio backend")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    # Reserve the inherited stdout descriptor before loading plugins/providers.
    # Their print/native writes go to stderr, never into the protocol stream.
    protocol_output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    from firik_agent.application.service import FirikService
    from firik_agent.application.paths import desktop_config_path
    from firik_agent.desktop_rpc.server import DesktopServer
    config = args.config
    legacy = desktop_config_path()
    if config is None and (legacy.exists() or (legacy.parent / "chats.db").exists()):
        config = legacy
    try:
        service = FirikService(config_path=config)
    except Exception as error:
        marker = ("FIRIK_BACKEND_DATA_IN_USE" if isinstance(error, RuntimeError)
                  and str(error) == "Bu veri dizini başka bir Firik uygulamasında açık."
                  else "FIRIK_BACKEND_START_FAILED")
        print(marker, file=sys.stderr, flush=True)
        protocol_output.close()
        return 1
    try:
        DesktopServer(service, protocol_output).serve(sys.stdin.buffer)
    finally:
        protocol_output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
