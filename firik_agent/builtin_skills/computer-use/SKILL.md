---
name: computer-use
description: Cross-platform desktop GUI automation, multimodal screen observation, mouse, keyboard, and window interaction
---

# Computer Use Skill

This skill teaches the agent how to interact with the host operating system's Graphical User Interface (GUI) safely, reliably, and effectively across macOS, Windows, and Linux.

## Core Principle: When to Use GUI vs. Shell

1. **CLI / Shell / API First**: Execute shell commands safely and purposefully. If a task can be accomplished directly via shell command, filesystem operation, or API call, always prefer that approach—it is faster, deterministic, and less prone to UI race conditions.

2. **GUI Automation**: Use the `computer_*` toolset when:
   - Interacting with graphical desktop applications (browsers, IDEs, office suites, design tools).
   - Performing actions that have no CLI equivalent or are locked behind UI interactions.
   - Verifying visual layouts, rendered web pages, or application UI states.

---

## The Automation Loop: Observe → Plan → Act → Verify

Every GUI automation workflow MUST follow this four-phase lifecycle:

### 1. Observe
- Always begin by understanding the current screen state.
- Call `computer_environment` to inspect OS, display server (e.g., macOS Quartz, Windows Win32, Linux X11/Wayland), scaling factor, and permissions.
- Call `computer_get_displays` to identify display dimensions, offsets, and primary display.
- Call `computer_screenshot` to inspect the visual layout of the current screen or focused window.
- Optionally call `computer_accessibility_snapshot` or `computer_list_windows` to inspect UI element trees, labels, and window IDs.

### 2. Plan
- Identify the target UI element from the visual screenshot or accessibility tree.
- Note the element's logical center coordinates `(x, y)`.
- Never guess coordinates blindly without observing the screen first.
- Deconstruct complex tasks into atomic steps (e.g., focus window → click input field → wait briefly → type text → press Return → verify result).

### 3. Act
- Perform the planned action using the appropriate canonical tool:
  - `computer_move_pointer(x, y)`: Move cursor to coordinates.
  - `computer_click(x, y, button="left", click_type="single")`: Click or double-click.
  - `computer_drag(start_x, start_y, end_x, end_y)`: Drag and drop.
  - `computer_type_text(text)`: Enter text into focused control.
  - `computer_key(key)`: Single key (e.g. `Return`, `Escape`, `Tab`, `BackSpace`).
  - `computer_hotkey(keys)`: Shortcuts (e.g. `['Command', 'space']` on macOS or `['Ctrl', 'Shift', 'p']` on Windows/Linux).
  - `computer_scroll(dy, dx)`: Scroll up/down or left/right.
  - `computer_wait(seconds)`: Pause for UI animations or network loads.

### 4. Verify
- **Crucial Rule**: Never assume an action succeeded.
- Take a new screenshot or check window status after executing an action to verify that:
  - The menu opened, button triggered the action, modal appeared, or form submitted.
  - If the UI state did not change as expected, pause with `computer_wait`, re-observe, and adapt strategy rather than spamming clicks.

---

## Coordinate Guidelines & HiDPI Scaling

- **Logical Coordinates**: All coordinates `(x, y)` passed to computer-use tools are **logical desktop coordinates**.
- Retina displays and Windows DPI scaling (e.g. 200% scale) are handled automatically by the backend. Never multiply coordinates by the scale factor manually.
- In multi-monitor setups, monitors may have negative offsets (e.g. a secondary monitor positioned to the left of the primary). Check `computer_get_displays` to understand the virtual desktop geometry.

---

## Platform-Specific Conventions

### macOS
- Primary modifier key is `Command` (or `cmd`). E.g., copy is `['Command', 'c']`, paste is `['Command', 'v']`, search is `['Command', 'f']`.
- Applications often have menu bars at the top of the primary screen.
- Screen Recording and Accessibility permissions in System Settings are required. If an operation fails with a permission error, guide the user cleanly.

### Windows
- Primary modifier key is `Ctrl` (e.g. `['Ctrl', 'c']`, `['Ctrl', 'v']`).
- Start menu and system tray are typically at the bottom of the screen.
- Administrative (UAC) elevated windows cannot be controlled unless running with equivalent privileges.

### Linux
- X11 environments support full pointer, keyboard, and window automation.
- Wayland environments enforce compositor isolation: global pointer and keyboard injection may be restricted unless using tools like `ydotool` or XDG desktop portals. The backend automatically detects and reports capability levels.

---

## Safety & Security Constraints

1. **Destructive Actions**: Never click "Delete Account", "Purge Data", "Format Disk", or submit payment transactions (`Confirm Purchase`, `Pay Now`) without explicit user instruction and confirmation.
2. **Blocked Applications**: Do not interact with sensitive system management applications (Terminal/Shell, Keychain Access, Password Managers, System Settings, Windows Registry).
3. **Emergency Stop**: If the user asks to stop, cancel, or abort, stop computer use immediately.
4. **Credential Protection**: Never paste passwords, credit cards, or private API keys directly into unverified input fields.
