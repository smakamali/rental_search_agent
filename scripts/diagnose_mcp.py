#!/usr/bin/env python3
"""Run a minimal MCP client flow with per-step timeouts to find where it hangs.
Usage: python scripts/diagnose_mcp.py
Each phase has a 30s timeout; the script prints elapsed time at each step.
Server stderr is written to mcp_server_stderr.log in the project root."""
import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root so we can import rental_search_agent
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TIMEOUT = 30.0


async def main():
    t0 = time.monotonic()
    server_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-u", "-m", "rental_search_agent.server"],
        cwd=project_root,
        env=server_env,
    )
    stderr_path = project_root / "mcp_server_stderr.log"
    errlog = open(stderr_path, "a", encoding="utf-8")
    try:
        errlog.write(f"\n--- diagnose_mcp session at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        errlog.flush()
    except Exception:
        pass
    try:
        print(f"[{time.monotonic() - t0:.1f}s] Spawning MCP server... (server stderr -> {stderr_path})")
        async with stdio_client(server_params, errlog=errlog) as (read_stream, write_stream):
            print(f"[{time.monotonic() - t0:.1f}s] Server connected. Initializing session...")
            session = ClientSession(read_stream, write_stream)
            await asyncio.wait_for(session.initialize(), timeout=TIMEOUT)
            print(f"[{time.monotonic() - t0:.1f}s] Session initialized. Calling tool rental_search...")
            result = await asyncio.wait_for(
                session.call_tool(
                    "rental_search",
                    {"filters": {"min_bedrooms": 1, "location": "Vancouver"}},
                ),
                timeout=TIMEOUT,
            )
            print(f"[{time.monotonic() - t0:.1f}s] Tool returned. isError={getattr(result, 'isError', None)}")
            if result.content:
                text = getattr(result.content[0], "text", "")[:200]
                print(f"  content preview: {text!r}...")
        print(f"[{time.monotonic() - t0:.1f}s] Done.")
    finally:
        try:
            errlog.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=90.0))
    except asyncio.TimeoutError:
        print("TIMEOUT - check which step above was last printed to see where it hangs.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
