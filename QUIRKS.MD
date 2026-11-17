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



