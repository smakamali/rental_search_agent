# MCP UI Investigation Report

**Date:** 2026-02-25  
**Issue:** Streamlit MCP UI never returns an answer; `session.initialize()` hangs until 120s timeout. No "[MCP] Session initialized" line in logs.

---

## Steps Taken

### 1. Run the MCP server directly

**Command run:** `python -u -m rental_search_agent.server`

**Result:** The server process **does not crash on startup**. It stays running and waits for input on stdin (expected for stdio transport). So the server process starts and is ready to handle JSON-RPC.

**Conclusion:** The problem is not a server import/runtime crash at startup. It is either (a) the handshake once a client connects, or (b) environment when the server is spawned by the client (e.g. Streamlit/worker thread).

---

### 2. Run the diagnose script and capture full traceback

**Change made:** In `scripts/diagnose_mcp.py`, the top-level `except Exception` now calls `traceback.print_exc()` so the full traceback is printed instead of only "Error: unhandled errors in a TaskGroup (2 sub-exceptions)".

**Result in this environment (Cursor sandbox on Windows):** The script failed **before** "Server connected" with:

```text
PermissionError: [WinError 5] Access is denied
```

at:

```text
File "C:\Users\smaka\miniconda3\Lib\site-packages\asyncio\windows_utils.py", line 136, in __init__
  stdin_rh, stdin_wh = pipe(overlapped=(False, True), duplex=True)
```

So in the sandbox, **creating the subprocess pipes** (stdin/stdout to the MCP server) fails with "Access is denied". The MCP client never gets as far as "Server connected" in that environment.

**On your machine:** You previously saw:

- "[MCP] Spawning server subprocess (0.0s)"
- "[MCP] Server connected (0.0s)"
- "[MCP] Initializing session... (0.0s)"
- Then either timeout (UI) or "Error: unhandled errors in a TaskGroup (2 sub-exceptions)" (diagnose script).

So on your machine the subprocess **is** created and the client reaches "Initializing session", but `session.initialize()` never completes. The "TaskGroup (2 sub-exceptions)" usually means the MCP client’s internal tasks (stdout reader and/or stdin writer) hit an error—often because the server closed the connection or sent invalid data.

---

## Root cause (inference)

1. **Server starts** when spawned by the client (you see "Server connected").
2. **Client sends** the MCP `Initialize` request on the stdio stream.
3. **Something in the handshake never completes**, so `session.initialize()` blocks until timeout, or the client’s reader/writer tasks raise and you get the TaskGroup error.

Possible reasons:

- **A. Server stdout buffering**  
  Server writes the `Initialize` response but it’s stuck in a buffer, so the client never sees it. We already tried `PYTHONUNBUFFERED=1` and `-u`; if the handshake still hangs, the server might be using a different code path that buffers.

- **B. Server exits or crashes after client connects**  
  e.g. the server hits an error when handling the first request and exits; the client then sees a closed stream and the TaskGroup fails. You would not see "Session initialized" in that case.

- **C. Protocol / transport mismatch**  
  Less likely if both use the same `mcp` package, but possible if client and server expect different message boundaries or JSON-RPC shapes.

- **D. Server blocking before responding**  
  The server might block on something (e.g. import, config, or I/O) before it can read from stdin or write the response.

---

## What you should do next (on your machine)

1. **Get the full traceback from the diagnose script**  
   Run (from project root):

   ```bash
   python scripts/diagnose_mcp.py
   ```

   The script now prints the full traceback. Copy the **entire** traceback (including the "sub-exceptions" and "Cause" lines if any) and share it. That will show whether the failure is in the stdout reader, stdin writer, or elsewhere, and what exception the server side might have caused (e.g. broken pipe).

2. **Confirm the server stays alive when the client connects**  
   - In **Terminal 1:** run `python -u -m rental_search_agent.server` and leave it running.  
   - In **Terminal 2:** run `python scripts/diagnose_mcp.py`.  
   - Watch Terminal 1: does the server process exit or print an error when the diagnose script runs? If it exits, the traceback or error there is the root cause.

3. **Optional: run the server under the same conditions as the UI**  
   From the same env and working directory you use for `rental-search-ui-mcp`, run the server with the same env the UI passes (including `PYTHONUNBUFFERED=1`):

   ```bash
   set PYTHONUNBUFFERED=1
   python -u -m rental_search_agent.server
   ```

   Then in another terminal run `python scripts/diagnose_mcp.py` again and see if the behavior or traceback changes.

---

## Summary

| Step | Result |
|------|--------|
| Server run directly | Does not crash; waits on stdin. |
| Diagnose script (sandbox) | Fails with WinError 5 when creating subprocess pipes (sandbox restriction). |
| Diagnose script (your machine) | Reaches "Server connected" and "Initializing session..." then TaskGroup error; full traceback not yet captured. |
| UI (your machine) | Reaches "Initializing session..." then 120s timeout; no "Session initialized". |

**Next step:** Run `python scripts/diagnose_mcp.py` on your machine and share the **full traceback**. With that, we can see the exact exception(s) in the TaskGroup and whether the server process is exiting or sending invalid data, and then fix the handshake or server startup accordingly.

---

## Update: Traceback analysis (2026-02-25)

Full traceback from `python scripts/diagnose_mcp.py` was provided. Summary:

- **Sub-exception 1:** In `mcp/client/stdio/__init__.py` (stdout_reader task), `read_stream_writer.send(session_message)` raised `anyio.BrokenResourceError`. So the **server process closed its stdout** (the subprocess exited or closed the pipe).
- **Sub-exception 2:** `session.initialize()` was waiting on `response_stream_reader.receive()`; it then received `CancelledError` (from the TaskGroup shutting down after sub-exception 1) and surfaced as `asyncio.TimeoutError`.

**Conclusion:** The MCP server **subprocess** spawned by the client exits (or closes stdout) during or right after the handshake. The manually-run server in a separate terminal does not error because the diagnose script spawns its **own** server process; that spawned process is the one that exits. So the bug is in the server’s behavior when run as a child process (stdio pipes, not TTY).

**Mitigations applied:**

1. **Server stderr capture**  
   `stdio_client(..., errlog=<file>)` is used so server stderr is written to `mcp_server_stderr.log`. If the server crashes, the traceback will appear there.

2. **Streamable HTTP transport (workaround)**  
   Set `MCP_USE_HTTP=1` to run the MCP server in-process over HTTP (port 8000, path `/mcp`) instead of spawning a stdio subprocess. This avoids the Windows stdio/subprocess issue entirely.

   **To use:** In PowerShell, before starting the MCP UI:
   ```powershell
   $env:MCP_USE_HTTP = "1"
   rental-search-ui-mcp
   ```
   Or in one line: `$env:MCP_USE_HTTP="1"; rental-search-ui-mcp`
