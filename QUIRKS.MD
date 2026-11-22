\# QUIRKS



\## Notepad

\- Main controls named: yes

\- Typed text readable: yes

\- Clean tree: yes

- M1 (Nov 17, 2026): Win11 Notepad is WinUI. A typical read is about 100 raw nodes and 240 to 310 estimated tokens, depending on how many tabs are open.
- A **new tab does not expose its editor** (`Document "Text editor"`) until the Notepad window has been in front at least once. Bring it forward (`pixel open notepad`), then read the tree.
- The editor supports ValuePattern, so `pixel type` sets text with no keystrokes. This **replaces** the tab's whole content.
- Tabs are TabItems named like `"name. Unmodified."`; the inner Text repeats the name and is pruned.
- `Add New Tab` and menu items activate through Invoke.
- M2 (Nov 18, 2026): Win11 Notepad can **restore earlier tabs** on launch. `pixel run` would then send those tab names and the start of the active tab's text to the model. For testing, close Notepad first and set *Settings → When Notepad starts → Start new session and discard unsaved changes* (older builds call it *Open a new window*), or keep only blank tabs. Save any unsaved tabs you want before switching, because they are discarded on the next launch.
- M2 (Nov 22, 2026): if Notepad is already running, `open_app` **focuses the existing window** instead of launching a new one. A trial started with the last trial's `*hello` window still open "passed" in 2 steps without typing. Close every Notepad window before each trial. `scripts/trial_notepad.ps1` now waits until no Notepad process is running before it starts a trial.



\## This machine

- M2 (Nov 18, 2026): **Application Control blocks the `.exe` launchers uv creates** in the venv (`pytest.exe`, `pixel.exe`, and `llm.exe` in the uv tool install), with Windows Security / Norton notifications. `python.exe` is signed and runs fine, so use the module form: `uv run python -m pytest`, `uv run python -m pixel.cli run "..."`.
- The venv lives outside OneDrive (`UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\pixel-venv`), because OneDrive locked files in `.venv` during `uv add`.
- HTTPS is intercepted. `uv` needs `--system-certs`, and Python's httpx (used by llm-gemini) fails with `CERTIFICATE_VERIFY_FAILED` against certifi's bundle.



\## Models (Gemini free tier)

- M2 (Nov 21 to 22, 2026): `gemini-3.8-flash` answered one hand test, then failed 5 of 5 trials (60 s read timeouts and "This model is currently experiencing high demand"). `gemini-3.7-flash` failed 5 of 5 with "high demand", usually within a few seconds. The free tier of the newest Flash models can be unavailable for hours.
- `gemini-3.5-flash-lite` with `thinking_level = "minimal"` passed every trial: about 0.7 s per call and 3 steps for the Notepad task. It is the navigation model in `config.toml` for now.
- With `minimal` thinking, the lite model reports no thought-token count, so `--verbose` shows `thoughts ?`.
- The `chars_per_token = 2.5` estimate was measured on 3.8 with tree text only. On full prompts sent to the lite model it **overestimates** by about 45% (about 1,564 estimated against 1,077 actual). It errs on the safe side, so it is left as is.
- `scripts/trial_notepad.ps1` stops at the first quota, "high demand" or timeout error, so an outage costs one request instead of five.



\## Chrome

\- Main controls named: yes

\- Page text readable: yes

\- Clean tree: yes

- M1 (Nov 17, 2026): the first UIA read of a Chrome window returned only the browser frame (48 nodes). Later reads returned the page too (484 nodes). Chrome likely turns on web accessibility only after a UIA client asks. If a first read looks sparse, read again.
- Chrome's IsOffscreen is accurate: content scrolled out of the viewport is flagged and pruned. On a long page most nodes are offscreen, so the pruned tree shows only what is visible (about 280 tokens on a test page).
- Page structure stays within 12 levels on the pages tried; no depth cut.



\## Claude Desktop

\- Main controls named: yes

\- Reply text readable: yes

\- Clean tree: yes

- M1 (Nov 17, 2026): **UIA sees only the window frame** (15 raw nodes: title plus Minimize, Restore and Close). The app content is a set of empty Chromium "View" panes, and there is no web-content child window. This held on repeated reads and after an MSAA `AccessibleObjectFromWindow` request. Unlike Chrome, the app does not turn on accessibility when a client asks. The M0 result (named controls) probably came from a tool that switched accessibility on another way.
- Likely fix, untested: start the app with Chromium's `--force-renderer-accessibility`. It is a Store (MSIX) app at `C:\Program Files\WindowsApps\Claude_<version>_x64__pzs8sxrjxfjjc\app\claude.exe`, so arguments have to go through `IApplicationActivationManager::ActivateApplication` with the app ID `Claude_pzs8sxrjxfjjc!Claude`. The exe cannot be launched by path, and the folder changes with every update.
- `config.toml` launches it with `shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude`.



