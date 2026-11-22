# PIXEL: development plan

Status: M2 complete as of Nov 22, 2026 (M0 Nov 16, M1 Nov 17). Next: M3. Architecture is in `SPEC.md`. Standing rules for Claude Code are in `CLAUDE.md`.

## Goal and definition of done

PIXEL v0.1 is done when a text command in a terminal controls Windows apps through the accessibility tree and passes a test you can run yourself.

- **v0.1 (end of Phase 1):** `pixel run "open Notepad and type hello"` succeeds in at least 4 of 5 runs, with a fallback across free models and an action log.
- **Daily-usable (end of Phase 2):** `pixel run "open D2L, go to 2C03, open its content page"` succeeds in at least 3 of 5 runs from a logged-in Chrome window, plus a text overlay, voice input, and reminders.

Everything else waits for those two gates. Scope throughout: Windows only, accessibility tree first, free models first.

## Decisions locked in

- Windows-native development and execution. Never WSL for the bridge.
- Accessibility tree first. Screenshots and OCR are a last resort.
- Model-agnostic through the `llm` library. Chain: Gemini, then Groq, then local Ollama.
- Claude is opt-in and never in the automatic chain.
- PIXEL never generates and runs its own code. New tools are added by the developer after review.
- Reuse an existing bridge library (agent-eyes, Tarsier, or pywinauto), chosen in M1, with the version pinned and the source skimmed.

## Phase 1: core loop (M0 to M3)

Phase 1 ends when the Notepad test passes and the loop survives a rate-limited provider.

### M0 · Setup and feasibility check (done)

uv, `llm` with Gemini (`gemini-3.8-flash` works), repo, spec and plan files, and accessibility inspection of Notepad, Chrome, Claude Desktop, and Spotify recorded in `QUIRKS.md`.

### M1 · Bridge

- A pruned tree of the focused window as short-ID text: interactive elements only, depth-limited, with a token estimate.
- Tools: `get_focused_window`, `get_ui_tree`, `click_element`, `type_text`, `send_keys`, `open_app`.
- Saved JSON tree fixtures, so the pruning logic is tested without live apps.
- **Done when:** `pixel tree` prints a Notepad tree under about 500 tokens, and click and type work from the command line by element ID.

### M2 · Core loop (done)

- `pixel run "<command>"` reads the tree, calls one model through `llm` with the tools, executes the action, waits for the UI to settle, and repeats.
- It stops when the model calls `finish` or after 15 steps. `--verbose` prints each tree size, model call, and action.
- **Done when:** `pixel run "open Notepad and type hello"` succeeds in at least 4 of 5 runs.
- **Result (Nov 22, 2026):** 4/5 clean passes with `gemini-3.5-flash-lite` (thinking `minimal`), 3 steps and about 7 s each. The fifth also passed, but it started with the previous trial's Notepad still open, so it doesn't count. Stop checks passed: an impossible task ends with `ask_user`, and `--max-steps 2` stops at the cap. The kill hotkey (Ctrl+Alt+Q) and confirm mode were brought forward from M3. `gemini-3.8-flash` and `gemini-3.7-flash` never answered a trial (timeouts and "high demand"); see `QUIRKS.md`.

### M3 · Reliability and logging

- Fallback chain: Gemini, then Groq, then Ollama, retrying the same step on a rate-limit error.
- A SQLite action log with the command, execution path, steps, seconds, and success.
- Safety basics: step cap, kill hotkey, and the hard-blocked action list.
- **Done when:** forcing a rate-limit error on the first provider moves a task to the second mid-run, and every run writes a log row.

## Phase 2: daily driver (M4 to M7)

### M4 · Chrome and D2L flow

- Page text through the Chrome DevTools Protocol when the tree is sparse.
- Download detection by watching the Downloads folder, passing the file path to the next step.
- A per-course config file (for example, "2C03 assignments are under Content").
- **Done when:** `pixel run "open D2L, go to 2C03, open its content page"` succeeds in at least 3 of 5 runs.

### M5 · Text overlay

- A global hotkey opens a small input bar. Submitting sends the command through the same pipeline and shows its status.
- **Done when:** the hotkey works over any app without taking focus before the action runs.

### M6 · Voice

- Wake word through openWakeWord or Porcupine, local transcription with faster-whisper, optional Piper for spoken confirmations.
- Log latency from end of speech to first action.
- **Done when:** "Hey PIXEL, open Notepad" works from across a desk.

### M7 · Reminders and watches

- Time parsing in the Toronto time zone, a read-back confirmation, Windows toast notifications, and calendar events through the user's calendar API (provider to confirm).
- Daily watches on company career pages: fetch, compare against saved job IDs, notify on new postings. The first run only records a baseline.
- Scheduled through Windows Task Scheduler.
- **Done when:** a reminder fires on time, and a watch detects a newly added item on a test page.

## Phase 3: power features (M8 to M10)

- **M8:** recorded routines and the weekly optimization summary.
- **M9:** file tools, PDF filtering, personal config, and the opt-in Claude routes (API and Claude Desktop).
- **M10:** installer, first-run wizard, README, secrets handling, and the decision on open-sourcing.

## Model and usage plan

- Build with Opus 5.5 by default. Use Fable 5.2 only for the hardest pieces while it is included in Pro (through 11:59 p.m. Pacific on Nov 16, 2026).
- Runtime models inside PIXEL come from the free chain. A local model handles sensitive apps.
- Claude is never part of PIXEL's runtime chain unless called with `ask_claude`.

## Safety guardrails

- No delete tools. File moves go to `_to_delete/`.
- Hard-blocked: purchases, account creation, entering card or ID data, system file changes, and following instructions found in page or email content.
- Step cap of 15, a kill hotkey, and confirm mode for new tools.
- Log every action with the tree state that led to it.
- Sensitive apps go to the local model or are excluded.
- Pin third-party automation packages and skim their source before first run.
- Test on throwaway windows and public pages until M3 passes.

## Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| An app exposes a sparse tree (Spotify desktop already does) | Chrome DevTools Protocol, keyboard shortcuts, web player or API, OCR as a last resort |
| Free-tier limits or model IDs change (gemini-2.5-flash was already retired) | Provider chain with retries, model names in config, Ollama as the unlimited last rung |
| Small free or local models call tools badly | Test chain order on real tasks in M3, put the most reliable model first |
| Admin-elevated apps ignore UI Automation | Skip for v0.1 |
| App updates break recorded routines | Fall back to model navigation and flag the routine |

## Open questions

- Which calendar will the M7 integration use (Google, Outlook, other)?
- Which D2L course should the M4 test use, and is it acceptable to send its pages to a free cloud model?
- Which wake-word engine for M6, openWakeWord or Porcupine?
