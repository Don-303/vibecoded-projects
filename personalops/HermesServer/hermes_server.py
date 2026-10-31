#!/usr/bin/env python3
"""
PersonalOps Hermes server — chat brain + backup receiver for the iOS app.

Runs on the PC (Windows/macOS/Linux) that lives on the same Wi-Fi as the
phone. Standard library only: no pip installs needed.

SETUP (Windows):
  1. Install Python 3.9+ from python.org — tick "Add python.exe to PATH".
  2. Give the server a brain (checked in this order, first found wins):
       a. Hermes Agent Desktop's gateway API (recommended if you have Hermes
          Agent Desktop) — plain HTTP, no subprocess involved, and the
          gateway is meant to run continuously as a service rather than
          being invoked one-shot:
            hermes config set API_SERVER_ENABLED true
            hermes config set API_SERVER_KEY <pick-a-secret>
            hermes gateway              (or: hermes gateway install, to run
                                         it as a persistent system service)
          Then:  setx API_SERVER_KEY <the-same-secret>  (new terminal after).
       b. Hermes Agent Desktop's CLI — if `hermes` is on PATH but the
          gateway API above isn't configured, the server shells out to
          `hermes chat -q "..."` instead (a bit slower, one process per
          message, but zero extra setup).
       c. Claude Code — install and log in (https://claude.com/claude-code),
          the server shells out to the `claude` CLI.
       d. An Anthropic API key:  setx ANTHROPIC_API_KEY sk-ant-...
          (then open a NEW terminal so the variable is picked up).
  3. Run:  python hermes_server.py
  4. When Windows Firewall asks, click "Allow access" (private networks).
  5. Find this PC's address:  ipconfig  → "IPv4 Address" (e.g. 192.168.0.42)
  6. In the PersonalOps app: Hermes tab → gear icon → enter
     192.168.0.42:8787 → TEST CONNECTION.

ENDPOINTS:
  POST /chat    {"message": "...", "context": "{...}"}  → {"reply", "actions"}
  POST /backup  <full JSON export>                      → {"ok": true}
  GET  /restore → contents of latest.json (404 if no backup exists yet)
  GET  /health                                          → {"ok", "brain"}

Backups land in <home>/PersonalOpsBackups/ — one timestamped file per
upload plus latest.json.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8787
BACKUP_DIR = Path.home() / "PersonalOpsBackups"
LOG_FILE = Path.home() / "PersonalOpsBackups" / "server.log"
CLAUDE_TIMEOUT_SECONDS = 150
API_MODEL = "claude-sonnet-5"
MAX_CHAT_BODY_BYTES = 2 * 1024 * 1024       # 2 MB — a chat message + context
MAX_BACKUP_BODY_BYTES = 50 * 1024 * 1024    # 50 MB — a full data export
MAX_CONTEXT_CHARS = 12_000                  # applies to every brain, not just hermes-cli
MAX_ERROR_CHARS = 300                       # never echo a huge/sensitive error back to the client


def log(message: str) -> None:
    """Prints AND appends to a persistent log file, so troubleshooting
    doesn't depend on the terminal window staying open/scrolled-back."""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # logging must never be the reason a request fails


def safe_error_text(error: Exception) -> str:
    """Short, safe-to-return description of an exception. Some exceptions
    (notably subprocess.TimeoutExpired) embed the FULL command they ran —
    which for us includes the entire prompt, i.e. potentially the whole app
    context — in their default __str__. Never let that reach the client or
    bloat the log."""
    if isinstance(error, subprocess.TimeoutExpired):
        return f"the {BRAIN} process timed out after {CLAUDE_TIMEOUT_SECONDS}s"
    if isinstance(error, FileNotFoundError):
        return f"brain executable not found ({BRAIN}) — is it still on PATH?"
    text = str(error)
    return text[:MAX_ERROR_CHARS] + ("..." if len(text) > MAX_ERROR_CHARS else "")

# Shared by both prompts below — the full action vocabulary, kept in one
# place so the two variants can't silently drift out of sync as actions are
# added. Interpolated into each prompt's f-string.
ACTION_SCHEMA_DOC = """\
```json
{"actions": [ ... ]}
```

Each action object supports:
  {"action": "log_note",   "content": "<note text>", "tags": ["tag"]}
  {"action": "add_task",   "content": "<title>", "priority": 0|1|2,
   "targetDate": "<ISO8601>", "details": "...", "projectId": "<uuid>"}
  {"action": "update_task", "content": "<new title, or empty to keep it>",
   "id": "<task uuid>", "priority": 0|1|2, "targetDate": "<ISO8601>",
   "projectId": "<uuid>", "recurrence": "daily"|"weekly"|"" (empty clears it),
   "showOnCalendar": true|false}
   — any field you omit keeps its current value; only "id" is required.
  {"action": "delete_task", "content": "", "id": "<task uuid>"}
  {"action": "complete_task", "content": "", "id": "<task uuid from context>"}
  {"action": "toggle_habit",  "content": "", "id": "<habit uuid from context>"}
  {"action": "add_habit",  "content": "<name>",
   "segment": "morning"|"afternoon"|"evening"}
  {"action": "update_habit", "content": "<new name, or empty to keep it>",
   "id": "<habit uuid>", "segment": "morning"|"afternoon"|"evening"}
  {"action": "delete_habit", "content": "", "id": "<habit uuid>"}
  {"action": "create_project", "content": "<name>",
   "projectColor": "cyan"|"green"|"amber"|"alert"}
  {"action": "set_project_goal", "content": "<goal description>",
   "id": "<project uuid>", "targetDate": "<ISO8601>" (omit to clear it)}
  {"action": "archive_project", "content": "", "id": "<project uuid>",
   "archived": true|false (default true; false unarchives)}
  {"action": "delete_project", "content": "", "id": "<project uuid>"}
   — protected projects (e.g. the built-in Reading List) silently refuse.
  {"action": "assign_task_to_project", "content": "",
   "id": "<task uuid>", "projectId": "<project uuid>"}
  {"action": "add_book",   "content": "<title>", "author": "...",
   "category": "personal"|"textbook"}
  {"action": "update_book_status", "content": "", "id": "<book uuid>",
   "status": "queued"|"reading"|"finished"}
  {"action": "delete_book", "content": "", "id": "<book uuid>"}
  {"action": "remember", "content": "<durable fact/preference to store>"}
  {"action": "forget", "content": "", "id": "<memory uuid from context's memory array>"}

For adding a book to the Reading List, ALWAYS use "add_book" — never
"add_task". The Reading List is a dedicated book tracker (title/author/
status), completely separate from generic tasks; a task assigned to the
Reading List "project" will not show up there at all.

Use "remember" whenever you learn something durable and worth keeping
beyond this one conversation — a stated preference, a recurring goal, a
fact about the user's life or work. This is real, permanent memory: it's
included in every future conversation regardless of how long ago it was
said, unlike the rolling recent-chat window. Don't "remember" things
already present in the memory list, and don't remember trivial one-off
details that don't matter later.

Rules: "content" is required on every action (empty string when unused).
UUIDs must be copied exactly from the context — never invent them. Dates are
ISO8601 (e.g. "2026-07-04T09:00:00Z"). Only emit the block when the user
asked you to change something; plain questions get plain replies. Do not
mention the JSON block in your prose — the app strips and applies it silently.
"""

SYSTEM_PROMPT = f"""\
You are Hermes, the user's personal assistant inside the PersonalOps iOS
app. Treat this like any general-purpose assistant chat: answer questions,
help think things through, give advice, hold a normal conversation — you are
not restricted to app-related requests. Be concise, direct, and useful —
terminal-style brevity suits the app, but don't be curt or robotic.

You also receive a JSON context snapshot of the user's current PersonalOps
state: pending tasks, habits (with completedToday flags), projects, today's
log entries, the recent chat transcript, and memory — durable facts you've
chosen to remember about the user across every past conversation, not just
this one. Use it naturally when relevant (e.g. if asked "what's on my plate
today") — it's there to make you more useful, not to narrow what you're
willing to talk about.

When the user wants you to change something in the app — not just tasks,
but habits, projects, or their Reading List too — ACT on it by appending
exactly one fenced JSON block at the very end of your reply:

{ACTION_SCHEMA_DOC}"""

# Hermes Agent Desktop already has its own built-in Hermes persona, so
# telling it "You are Hermes..." again (as SYSTEM_PROMPT does) makes it
# treat the whole request as a role/identity assignment and just reply with
# a canned self-introduction, ignoring the actual question underneath. This
# variant drops the identity framing and leads with the real task instead —
# used only for the hermes-cli brain.
HERMES_CLI_INSTRUCTIONS = f"""\
Treat this like a normal personal-assistant chat, not a narrow command tool
— answer questions, hold a conversation, give advice, help with anything the
user brings up. Do not introduce yourself or restate your role, just address
what they asked, the same as you would in any other conversation.

Below is the current state of a personal-operations app (PersonalOps) the
user also has open — their pending tasks, habits, projects, today's log,
recent chat, and memory (durable facts you've chosen to remember about them
across every past conversation, not just this one). Use it naturally when
relevant (e.g. "what's on my plate today"), but it doesn't limit what you're
willing to discuss.

You have live web search/browsing tools enabled for this request. For
anything time-sensitive or requiring current information — weather,
breaking news, financial/economic events, or any "what's happening
today/right now" question — actually use those tools to look it up rather
than answering from training data or refusing. Only say you can't find
something after actually attempting to search for it.

When the user wants you to change something in the app — not just tasks,
but habits, projects, or their Reading List too — ACT on it by appending
exactly one fenced JSON block at the very end of your reply:

{ACTION_SCHEMA_DOC}"""

ACTIONS_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```\s*$", re.DOTALL)
# Hermes Agent Desktop's renderer sometimes strips the triple-backtick fence
# entirely and leaves a bare "json" label line followed by the object, e.g.
# "json\n{...}" with no closing marker at all. Fallback for that case.
BARE_ACTIONS_BLOCK = re.compile(r"`{0,3}json\s*\n(\{.*\})\s*`{0,3}\s*$", re.DOTALL | re.IGNORECASE)
# Last-resort catch-all: a trailing JSON object containing an "actions" key,
# with no fence or label at all. The user should never see raw action
# payloads in the chat — this hides one even in formats we haven't seen yet.
TRAILING_JSON_BLOCK = re.compile(r"(\{[^{}]*\"actions\"\s*:\s*\[.*?\]\s*\})\s*$", re.DOTALL)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# Hermes Agent Desktop's CLI wraps replies in extra chrome beyond just the
# reply text:
#   - an echoed "Query: <input, wrapped/truncated>" line
#   - an "Initializing agent..." status line
#   - (older/alternate renderer) a decorative rounded-box panel
#   - a trailing "Resume this session with: ..." / "Session: ..." footer
# Strip all of it so only the actual reply text reaches the chat bubble.
BOX_BORDER_LINE = re.compile(r"^[\s╭╰╮╯─━=]*$")
FOOTER_MARKERS = (
    "\nResume this session with:",
    "\nSession:",
)


def strip_cli_chrome(text: str) -> str:
    text = ANSI_ESCAPE.sub("", text)

    for marker in FOOTER_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]

    init_idx = text.find("Initializing agent...")
    if init_idx != -1:
        text = text[init_idx + len("Initializing agent...") :]
    elif text.lstrip().startswith("Query:"):
        # Fallback for CLI versions without the "Initializing agent..."
        # line: drop the echoed query up through the first blank line.
        parts = text.split("\n\n", 1)
        if len(parts) == 2:
            text = parts[1]

    lines = text.splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if BOX_BORDER_LINE.match(stripped) or (
            stripped.startswith(("╭", "╰")) and stripped.endswith(("╮", "╯"))
        ):
            continue
        # Box interior lines are left-padded with spaces but have no
        # vertical bar characters in this renderer — just dedent them.
        kept.append(stripped)
    return "\n".join(kept).strip()


def detect_brain():
    # hermes-api (the gateway's OpenAI-compatible REST server) is preferred
    # over hermes-cli when configured: no per-request subprocess spin-up, no
    # Windows cmd.exe quoting fragility, and `hermes gateway` is meant to
    # run continuously as a service rather than being invoked one-shot.
    # It only activates if the user has actually set it up (API_SERVER_KEY
    # in the environment) — otherwise this falls back exactly as before.
    if os.environ.get("API_SERVER_KEY"):
        return "hermes-api"
    if shutil.which("hermes"):
        return "hermes-cli"
    if shutil.which("claude"):
        return "claude-cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return "none"


BRAIN = detect_brain()

# Full resolved path to the hermes/claude executables (includes the real
# extension, e.g. hermes.exe / hermes.cmd on Windows). Resolving once lets
# us invoke WITHOUT shell=True — see cli_command() for why that matters.
HERMES_PATH = shutil.which("hermes")
CLAUDE_PATH = shutil.which("claude")


def cli_command(exe_path, args):
    """Builds an argv list that runs `exe_path args...` without a shell.

    Passing our large prompt (full of double quotes, JSON braces, and
    cmd.exe metacharacters like <, >, |, %) through shell=True on Windows
    corrupts it: cmd.exe reinterprets those characters and truncates the
    command line, so the agent ends up seeing a mangled, empty-looking
    context and asks the user to "provide the current state". Invoking the
    resolved executable directly (no shell) hands the prompt to the process
    as one intact argument via CreateProcess. .cmd/.bat shims still require
    cmd.exe, so those route through COMSPEC explicitly — but Python does the
    quoting once, correctly, rather than us relying on shell string parsing.
    """
    if os.name == "nt" and exe_path and exe_path.lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c", exe_path, *args]
    return [exe_path, *args]


def think(message: str, context: str) -> str:
    # Applies to every brain: an unbounded context (e.g. a corrupted client
    # sending its whole SQLite export as "context" by mistake) shouldn't be
    # able to produce a runaway-slow or runaway-expensive request.
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "...(truncated for length)"
    if len(message) > MAX_CONTEXT_CHARS:
        message = message[:MAX_CONTEXT_CHARS] + "...(truncated for length)"

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"## Current app state\n{context}\n\n"
        f"## User message\n{message}"
    )
    if BRAIN == "hermes-api":
        # Hermes Agent Desktop's own OpenAI-compatible gateway API
        # (`hermes gateway`, API_SERVER_ENABLED=true) — a plain HTTP call,
        # no subprocess/cmd.exe involved at all. Same identity-collision
        # issue as hermes-cli applies (it's the same underlying agent), so
        # HERMES_CLI_INSTRUCTIONS is used as the system message here too.
        host = os.environ.get("API_SERVER_HOST", "127.0.0.1")
        port = os.environ.get("API_SERVER_PORT", "8642")
        api_key = os.environ["API_SERVER_KEY"]
        body = json.dumps({
            "model": "hermes-agent",
            "messages": [
                {"role": "system", "content": HERMES_CLI_INSTRUCTIONS},
                {"role": "user",
                 "content": f"## Current app state\n{context}\n\n## User message\n{message}"},
            ],
        }).encode()
        request = urllib.request.Request(
            f"http://{host}:{port}/v1/chat/completions",
            data=body,
            headers={
                "content-type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=CLAUDE_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read()).get("error", {}).get("message", "")
            except Exception:
                detail = ""
            raise RuntimeError(
                f"Hermes API server error {error.code}: {detail or error.reason}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"could not reach Hermes gateway at {host}:{port} — "
                f"is `hermes gateway` running on this machine?") from error

        choices = payload.get("choices", [])
        if not choices:
            raise RuntimeError(f"Hermes gateway returned no choices: {str(payload)[:200]}")
        text = choices[0].get("message", {}).get("content", "")
        return strip_cli_chrome(text)

    if BRAIN == "hermes-cli":
        # Hermes Agent Desktop's scriptable one-shot mode: `hermes chat -q`.
        # Uses HERMES_CLI_INSTRUCTIONS instead of SYSTEM_PROMPT — see its
        # comment for why (avoids the canned self-introduction bug).
        #
        # Keep the context tighter still: the prompt is passed as a single
        # command-line argument, and while cli_command() avoids the cmd.exe
        # corruption, a very large argument is still best avoided.
        cli_max_context_chars = 6000
        safe_context = (
            context if len(context) <= cli_max_context_chars
            else context[:cli_max_context_chars] + "...(truncated for length)"
        )
        hermes_prompt = (
            f"{HERMES_CLI_INSTRUCTIONS}\n\n"
            f"## Current app state\n{safe_context}\n\n"
            f"## User message\n{message}"
        )
        if HERMES_PATH is None:
            raise RuntimeError("hermes CLI was detected earlier but is no longer on PATH")
        # --toolsets isn't documented as on-by-default for -q's one-shot
        # mode, so it's passed explicitly to guarantee Hermes can actually
        # browse the web for things like weather/breaking news rather than
        # silently answering from training data or refusing.
        result = subprocess.run(
            cli_command(
                HERMES_PATH,
                ["chat", "-q", hermes_prompt, "--toolsets", "web,terminal,skills"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError(f"hermes CLI failed: {result.stderr.strip()[:400]}")
        return strip_cli_chrome(result.stdout)

    if BRAIN == "claude-cli":
        # claude reads its prompt from stdin, so no giant-argument concerns
        # here — but still invoke the resolved path without a shell.
        if CLAUDE_PATH is None:
            raise RuntimeError("claude CLI was detected earlier but is no longer on PATH")
        result = subprocess.run(
            cli_command(CLAUDE_PATH, ["-p", "--output-format", "text"]),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLAUDE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {result.stderr.strip()[:400]}")
        return result.stdout.strip()

    if BRAIN == "api":
        body = json.dumps({
            "model": API_MODEL,
            "max_tokens": 1500,
            "system": SYSTEM_PROMPT,
            "messages": [{
                "role": "user",
                "content": f"## Current app state\n{context}\n\n## User message\n{message}",
            }],
        }).encode()
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=CLAUDE_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            # Surface Anthropic's actual error message (e.g. "invalid x-api-key")
            # rather than a bare "HTTP Error 401: Unauthorized".
            try:
                detail = json.loads(error.read()).get("error", {}).get("message", "")
            except Exception:
                detail = ""
            raise RuntimeError(f"Anthropic API error {error.code}: {detail or error.reason}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"could not reach api.anthropic.com: {error.reason}") from error

        if payload.get("type") == "error":
            raise RuntimeError(f"Anthropic API error: {payload.get('error', {}).get('message', payload)}")
        return "".join(
            block.get("text", "")
            for block in payload.get("content", [])
        ).strip()

    raise RuntimeError(
        "No brain configured: install Hermes Agent Desktop or the claude CLI, "
        "or set ANTHROPIC_API_KEY."
    )


def _sanitize_action(action: dict) -> dict:
    """Empty-string date fields (Hermes sometimes writes "" instead of
    omitting the key) fail to decode as Swift's Date? on the app side and
    silently drop the whole reply. Null them out instead."""
    for key in ("targetDate", "date"):
        if action.get(key) == "":
            action[key] = None
    return action


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# What each action type requires/allows, checked before an action is ever
# sent to the phone. Anything that fails validation is dropped and reported
# back as a warning instead of silently corrupting or losing the whole
# reply — see the "please provide the current state" / "1984 task" bugs
# this is meant to catch going forward.
KNOWN_ACTIONS = {
    "log_note": {"requires": [], "optional": ["timestamp", "tags"]},
    "add_task": {
        "requires": [],
        "optional": ["priority", "targetDate", "details", "projectId"],
        "enums": {"priority": (0, 1, 2)},
        "uuid_fields": ["projectId"],
    },
    "update_task": {
        "requires": ["id"],
        "optional": ["priority", "targetDate", "projectId", "recurrence", "showOnCalendar"],
        "enums": {"priority": (0, 1, 2), "recurrence": ("daily", "weekly", "")},
        "uuid_fields": ["id", "projectId"],
    },
    "delete_task": {"requires": ["id"], "optional": [], "uuid_fields": ["id"]},
    "complete_task": {"requires": ["id"], "optional": [], "uuid_fields": ["id"]},
    "toggle_habit": {"requires": ["id"], "optional": ["date"], "uuid_fields": ["id"]},
    "add_habit": {
        "requires": [],
        "optional": ["segment"],
        "enums": {"segment": ("morning", "afternoon", "evening")},
    },
    "update_habit": {
        "requires": ["id"],
        "optional": ["segment"],
        "enums": {"segment": ("morning", "afternoon", "evening")},
        "uuid_fields": ["id"],
    },
    "delete_habit": {"requires": ["id"], "optional": [], "uuid_fields": ["id"]},
    "create_project": {
        "requires": [],
        "optional": ["projectColor"],
        "enums": {"projectColor": ("cyan", "green", "amber", "alert")},
    },
    "set_project_goal": {
        "requires": ["id"],
        "optional": ["targetDate"],
        "uuid_fields": ["id"],
    },
    "archive_project": {
        "requires": ["id"],
        "optional": ["archived"],
        "uuid_fields": ["id"],
    },
    "delete_project": {"requires": ["id"], "optional": [], "uuid_fields": ["id"]},
    "assign_task_to_project": {
        "requires": ["id"],
        "optional": ["projectId"],
        "uuid_fields": ["id", "projectId"],
    },
    "add_book": {
        "requires": [],
        "optional": ["author", "category"],
        "enums": {"category": ("personal", "textbook")},
    },
    "update_book_status": {
        "requires": ["id", "status"],
        "optional": [],
        "enums": {"status": ("queued", "reading", "finished")},
        "uuid_fields": ["id"],
    },
    "delete_book": {"requires": ["id"], "optional": [], "uuid_fields": ["id"]},
    "remember": {"requires": [], "optional": []},
    "forget": {"requires": ["id"], "optional": [], "uuid_fields": ["id"]},
}


def validate_action(action):
    """Returns None if the action is well-formed, else a short reason string
    describing why it was dropped."""
    if not isinstance(action, dict):
        return "not a JSON object"

    name = action.get("action")
    spec = KNOWN_ACTIONS.get(name)
    if spec is None:
        return f"unknown action type {name!r}"

    if "content" not in action or not isinstance(action.get("content"), str):
        return f"{name}: missing/invalid \"content\""

    for field in spec.get("requires", []):
        if not action.get(field):
            return f"{name}: missing required \"{field}\""

    for field, allowed in spec.get("enums", {}).items():
        value = action.get(field)
        if value is not None and value not in allowed:
            return f"{name}: \"{field}\"={value!r} not one of {allowed}"

    for field in spec.get("uuid_fields", []):
        value = action.get(field)
        if value is not None and not UUID_RE.match(str(value)):
            return f"{name}: \"{field}\" is not a valid UUID"

    return None


def split_reply(raw: str):
    """Splits the model output into (prose, actions, warnings).

    Tries the properly fenced ```json block first, then a bare "json\\n{...}"
    block with no fence, then a naked trailing {"actions": [...]} object with
    no label at all. Whichever one matches, its text is ALWAYS removed from
    what the user sees — the user should only ever see the chat, never the
    action payload, even if the JSON inside turns out to be malformed and
    unusable as actions.

    Every action is also validated against KNOWN_ACTIONS before being
    accepted; anything malformed is dropped and reported as a warning
    instead of reaching the phone (where an invalid action used to be able
    to corrupt or drop the entire reply — see HermesTransport.Reply.parse
    on the app side for the matching client-side defense).
    """
    match = (
        ACTIONS_BLOCK.search(raw)
        or BARE_ACTIONS_BLOCK.search(raw)
        or TRAILING_JSON_BLOCK.search(raw)
    )
    if not match:
        return raw, [], []

    prose = raw[: match.start()].strip()
    try:
        raw_actions = json.loads(match.group(1)).get("actions", [])
    except json.JSONDecodeError:
        return prose, [], ["Hermes emitted an action block that wasn't valid JSON"]

    actions = []
    warnings = []
    for action in raw_actions:
        reason = validate_action(action)
        if reason:
            warnings.append(reason)
        else:
            actions.append(_sanitize_action(action))
    return prose, actions, warnings


class BodyTooLarge(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    # Large values here are just belt-and-suspenders against a slow/stuck
    # client — this server only ever expects one phone on the local network.
    timeout = 200

    def _respond(self, status: int, payload: dict):
        # The socket write can fail at send_response/end_headers just as
        # easily as at wfile.write — a phone that dropped WiFi/backgrounded
        # mid-request resets the connection at any point. The whole thing
        # needs to be inside the try, not just the final write; otherwise a
        # ConnectionResetError here escapes into do_POST's own except block,
        # which then calls _respond(500, ...) AGAIN on the same dead socket,
        # producing a second uncaught crash on top of the first.
        try:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Phone navigated away / lost the network mid-response — nothing
            # to do about it, and it must never crash the server thread.
            log("client disconnected before response was fully sent")

    def _read_body(self, max_bytes: int) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("missing Content-Length header")
        try:
            length = int(raw_length)
        except ValueError:
            raise ValueError(f"invalid Content-Length header: {raw_length!r}") from None
        if length < 0:
            raise ValueError(f"negative Content-Length: {length}")
        if length > max_bytes:
            raise BodyTooLarge(f"body of {length:,} bytes exceeds the {max_bytes:,} byte limit")
        return self.rfile.read(length)

    def do_GET(self):
        try:
            if self.path == "/health":
                self._respond(200, {"ok": True, "brain": BRAIN})
            elif self.path == "/restore":
                latest = BACKUP_DIR / "latest.json"
                if latest.exists():
                    data = latest.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    log(f"[restore] served {len(data):,} bytes")
                else:
                    self._respond(404, {"error": "no backup on this machine yet"})
            else:
                self._respond(404, {"error": "not found"})
        except Exception as error:  # noqa: BLE001 — a GET handler must never take the server down
            log(f"[{self.path}] ERROR: {safe_error_text(error)}")
            self._respond(500, {"error": safe_error_text(error)})

    def do_POST(self):
        if self.path == "/chat":
            try:
                payload = json.loads(self._read_body(MAX_CHAT_BODY_BYTES))
                message = payload.get("message", "")
                context = payload.get("context", "{}")
                if not isinstance(message, str) or not isinstance(context, str):
                    raise ValueError('"message" and "context" must both be strings')
                log(f"[chat] {message[:80]!r}")
                reply, actions, warnings = split_reply(think(message, context))
                log(f"[chat] -> {len(actions)} action(s), {len(warnings)} warning(s)")
                self._respond(200, {"reply": reply, "actions": actions, "warnings": warnings})
            except BodyTooLarge as error:
                log(f"[chat] REJECTED: {error}")
                self._respond(413, {"error": str(error)})
            except Exception as error:  # noqa: BLE001 — report all failures to the app
                log(f"[chat] ERROR: {safe_error_text(error)}")
                self._respond(500, {"error": safe_error_text(error)})
        elif self.path == "/backup":
            try:
                data = self._read_body(MAX_BACKUP_BODY_BYTES)
                json.loads(data)  # sanity check before writing anything
                BACKUP_DIR.mkdir(exist_ok=True)
                # Microsecond precision avoids two rapid-fire backups (e.g.
                # background + manual "BACK UP NOW" within the same second)
                # silently overwriting each other's timestamped file.
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                (BACKUP_DIR / f"personalops-{stamp}.json").write_bytes(data)
                (BACKUP_DIR / "latest.json").write_bytes(data)
                log(f"[backup] saved {len(data):,} bytes")
                self._respond(200, {"ok": True})
            except BodyTooLarge as error:
                log(f"[backup] REJECTED: {error}")
                self._respond(413, {"error": str(error)})
            except Exception as error:  # noqa: BLE001
                log(f"[backup] ERROR: {safe_error_text(error)}")
                self._respond(500, {"error": safe_error_text(error)})
        else:
            self._respond(404, {"error": "not found"})

    def log_message(self, *_args):
        pass  # custom log() calls above; silence the default access log


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        return "<this PC's IPv4 from ipconfig>"


if __name__ == "__main__":
    log("PersonalOps Hermes server starting")
    log(f"  brain:   {BRAIN}"
        + ("  (install claude CLI or set ANTHROPIC_API_KEY!)" if BRAIN == "none" else ""))
    log(f"  backups: {BACKUP_DIR}")
    log(f"  log:     {LOG_FILE}")
    log(f"  app setting -> {lan_ip()}:{PORT}")
    print("Ctrl+C to stop.")

    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as error:
        if error.errno in (98, 10048):  # EADDRINUSE (Linux/macOS, Windows)
            log(f"ERROR: port {PORT} is already in use — is another copy of "
                f"this server (or something else) already running?")
        else:
            log(f"ERROR: could not start server: {error}")
        sys.exit(1)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down (Ctrl+C).")
