# Stand-Up Reminder

A lightweight Windows system tray app that reminds you to stand up and stretch after sitting too long. Built to help with back pain from prolonged screen time.

## Features

- **System tray app** — runs silently in the background with a coloured icon
- **Countdown tooltip** — hover the tray icon to see exactly how much time is remaining
- **Dual alert** — Windows balloon notification + always-on-top popup dialog
- **Continuous beep** — repeating alert sound until you dismiss the popup
- **Start / Stop** — control the timer from the right-click tray menu
- **Configurable interval** — default 40 minutes, adjustable from 1–120 minutes
- **Auto-start** — registers itself to launch with Windows on first run
- **Single instance** — won't open twice if already running

## Tray icon colours

| Colour | Meaning |
|--------|---------|
| Grey   | Timer stopped |
| Green  | Timer running (hover to see countdown) |
| Red    | Time to stand up! |

## Running the app

### Option A — Pre-built `.exe` (no Python needed)

Download `StandUpReminder.exe` from the [Releases](../../releases) page and double-click it. Nothing else required.

### Option B — Run from source (requires Python + Anaconda)

**1. Install the only missing dependency:**
```
pip install pystray
```

**2. Double-click `standup_reminder.pyw`**

No console window will appear. The tray icon shows up in the bottom-right system tray.

## Usage

| Action | How |
|--------|-----|
| Start timer | Right-click tray icon → Start Timer |
| Stop timer | Right-click tray icon → Stop Timer |
| Change interval | Right-click → Settings |
| Toggle sound | Right-click → Settings |
| Disable auto-start | Right-click → Settings → uncheck "Start automatically with Windows" |
| Exit | Right-click → Exit |

## Building the `.exe` yourself

Requires Python with the dependencies below installed.

```bash
pip install pyinstaller pystray
pyinstaller --onefile --windowed --name StandUpReminder ^
  --hidden-import pystray._win32 ^
  --hidden-import win32com.client ^
  --hidden-import win32com.shell ^
  --hidden-import win32event ^
  --hidden-import win32api ^
  --hidden-import winerror ^
  --hidden-import win32timezone ^
  --hidden-import six ^
  standup_reminder.pyw
```

Output: `dist\StandUpReminder.exe`

## Dependencies (source only)

| Package | Version | Notes |
|---------|---------|-------|
| Python | 3.x | Anaconda recommended |
| pystray | 0.19.5+ | `pip install pystray` |
| Pillow | 12.0.0+ | Usually pre-installed with Anaconda |
| pywin32 | any | Usually pre-installed with Anaconda |
| tkinter | built-in | Included with Python |

## Configuration

Settings are saved automatically to:
```
%APPDATA%\StandUpReminder\config.json
```

## Uninstalling

1. Right-click tray icon → Settings → uncheck "Start automatically with Windows" → Save
2. Right-click tray icon → Exit
3. Delete `StandUpReminder.exe` (or `standup_reminder.pyw`)
4. Optionally delete `%APPDATA%\StandUpReminder\` to remove saved settings
