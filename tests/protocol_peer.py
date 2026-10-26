"""ACP peer exercising negotiation before any session or prompt can run."""

import asyncio
import os
from pathlib import Path

from acp import run_agent

from acp_peer import Peer


class ProtocolPeer(Peer):
    async def initialize(self, protocol_version, **kwargs):
        self.cwd = Path.cwd()
        (self.cwd / "peer-pid").write_text(str(os.getpid()), encoding="ascii")
        if self.mode == "missing-version":
            return {"agentCapabilities": {}}
        if self.mode == "invalid-version":
            return {"protocolVersion": "invalid", "agentCapabilities": {}}
        return {
            "protocolVersion": 999 if self.mode == "unsupported-version" else protocol_version,
            "agentCapabilities": {"futureOptionalCapability": True},
        }

    async def new_session(self, cwd, mcp_servers, **kwargs):
        (Path(cwd) / "session-created").write_text("created", encoding="ascii")
        return await super().new_session(cwd, mcp_servers, **kwargs)


if __name__ == "__main__":
    asyncio.run(run_agent(ProtocolPeer(), use_unstable_protocol=True))
