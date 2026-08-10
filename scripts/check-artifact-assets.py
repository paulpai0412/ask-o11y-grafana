#!/usr/bin/env python3
from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artifact_store import ArtifactStore
from artifact_assets import read_signed_output, sign_output_url


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(Path(tmp) / "runs")
        context = {"org_id": "1", "user_id": "asset-check"}
        run_id = store.create_run(context)
        ref = store.write_json(context, run_id, "sandbox-execution", {"results": [{"display_name": "plot.png", "mime": {"image/png": base64.b64encode(b"\x89PNG\r\n\x1a\nplot").decode()}}]})
        secret = os.environ.get("MCP_SHARED_TOKEN") or ("test-" * 8)
        url = sign_output_url(public_base="http://example", secret=secret, context=context, execution_ref=ref, output_index=0, expires_at=int(time.time()) + 60)
        token = url.rsplit("/", 1)[1]
        data, mime, name = read_signed_output(token, secret=secret, artifacts=store)
        if not data.startswith(b"\x89PNG") or mime != "image/png" or name != "plot.png":
            raise AssertionError("signed artifact output was not preserved")
        for invalid_token in [
            token[:-1] + ("A" if token[-1] != "A" else "B"),
            sign_output_url(public_base="http://example", secret=secret, context=context, execution_ref=ref, output_index=0, expires_at=int(time.time()) - 1).rsplit("/", 1)[1],
        ]:
            rejected = False
            try:
                read_signed_output(invalid_token, secret=secret, artifacts=store)
            except PermissionError:
                rejected = True
            if not rejected:
                raise AssertionError("tampered or expired asset URL accepted")
        print("artifact asset checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
