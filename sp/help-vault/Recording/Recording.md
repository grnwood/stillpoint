# Voice Notes and Screen Recording

Keep a spoken thought or a screen walkthrough beside the page it explains. Open a page in a writable local vault before starting.

The microphone and video-record icons sit beside **Print** in the main toolbar and beside each other in **Attachments**. You can also open the command palette and choose **Record Voice Note** or **Record Screen**. Both commands reveal the Attachments panel.

The recorder shows the destination as a StillPoint page name, such as `Projects:Demo`. That destination stays fixed if you visit another page while recording.

## Record a Voice Note

1. Click the microphone or choose **Record Voice Note**.
2. Choose a microphone. Check its input level while speaking.
3. Press **Record**. Use **Pause/Resume** as needed, then **Stop**.
4. Press **Play** to review the audio.
5. Choose M4A for compact audio when AAC encoding is available, or WAV for uncompressed audio. Press **Save** to attach it to the original page.

Opening the voice recorder does not start the microphone. Cancel or Discard closes it; discarding captured audio requires confirmation. If saving fails, the recorder keeps the draft so you can retry.

Double-click an audio attachment to open the player. It includes play/pause, stop, a seek slider, and volume. The controls have tooltips and accessible names.

### Transcribe Audio

Open **Preferences → Audio**, select a transcription server and speech-to-text model, then open an audio attachment and press **Transcribe**. The recording is sent to that configured server. You can copy the resulting text or save it as a transcript. Transcription is optional and requires a configured service; it is not part of Qt's local audio recording.

## Record the Screen

1. Click the video-record icon or choose **Record Screen**.
2. Choose a display and press **Preview screen**. On Wayland, **Choose screen…** opens the desktop's screen-sharing picker.
3. Leave **Microphone** set to **Off** for silent video, or select a microphone for narration. System audio is not captured.
4. Press **Record**. Approve any desktop global-shortcut prompt. A large **3 → 2 → 1** countdown appears before capture is saved to the recording.
5. The window animates into a small bar at the bottom center of the selected display, or the last position where you dragged it. A ticking timer, red dot, and pulsing red border indicate recording. Pausing changes the indicator to steady amber.
6. Press **Stop**, or use **Stop and review** from the tray. Capture ends before the playback/review window opens.
7. Review the video, then press **Save attachment** to attach it to the original page. Stopping does not save the attachment automatically.

StillPoint prefers MP4 and tries another available encoder if an encoder fails before recording any media. The actual container depends on available codecs. Save failures retain the draft and display its location. Retry Save rather than discard if you want to keep it.

Double-click a video attachment to play it inside StillPoint.

### Tray Controls and a Clear Recording

During screen recording, the system tray/menu-bar icon is steady **red** while recording and **amber** while paused. Its recording menu shows elapsed time and offers **Pause/Resume**, **Stop and review**, and **Show recording bar**.

Turn off **Show recording bar** to hide the floating controls. This choice is remembered for future screen recordings. A temporary recording tray icon is provided if StillPoint's usual icon is disabled. On a desktop without a usable system tray, the bar remains visible so you can still stop the recording.

Whole-screen capture includes visible desktop content: the recording bar, tray icon, and any tray menu you open may appear in the video. Hiding the bar and using a global stop shortcut avoids opening the menu in your recording. Moving controls to another monitor also keeps them outside the captured display.

### Global Recording Shortcuts

These shortcuts work while another application has focus. Start is registered while StillPoint is running. Pause and stop are registered for an active screen recording and released when it ends. They do not control voice notes.

| Action | Windows / Linux | macOS |
| --- | --- | --- |
| Start on the current page | Control+Alt+Shift+R | Control+Option+Shift+R |
| Pause or resume | Control+Alt+Shift+P | Control+Option+Shift+P |
| Stop and review | Control+Alt+Shift+S | Control+Option+Shift+S |

**Start** uses the most recently focused StillPoint window and attaches to its current writable local page. It reuses the screen and microphone selections you last picked, then runs the countdown. Pressing Start during a recording does not create another recording; an unsaved review is brought forward rather than overwritten. If a saved screen or microphone is unavailable, choose a replacement (or explicitly turn the microphone Off) before proceeding. On Wayland, the system screen picker still appears because Qt cannot silently reuse its selection.

On macOS, **Control means the physical Control key**, not Command. The macOS bindings use the physical R, P, and S positions on an ANSI keyboard. On Wayland, the desktop may ask you to approve or choose bindings; the tray menu reports the bindings actually granted.

If another application has reserved a shortcut, a permission request is denied, or the desktop does not provide a compatible global-shortcut service, StillPoint reports the problem. Continue using the bar or tray. It does not silently substitute an in-app shortcut for a global one.

## Platform Support and Permissions

- Screen capture uses Qt Multimedia's **FFmpeg backend**. Source installations require PySide6 6.8 or newer and the dependencies in `sp/requirements.txt`.
- Windows and macOS use native global-hotkey registration. Linux X11 uses key grabs; Wayland uses the **Global Shortcuts portal**.
- Linux Wayland screen capture is experimental in Qt and requires PipeWire and a compatible desktop screen-sharing portal. Global shortcuts additionally require a portal backend implementing Global Shortcuts. Those are separate desktop capabilities.
- On macOS, allow StillPoint screen-recording access in **System Settings → Privacy & Security**. Narration and voice notes also need microphone permission. For source development, use `venv/bin/python tools/run-macos-dev.py` so the app has its microphone declaration.
- Microphone access, capture, and encoders can fail independently. Check the recorder's message for the specific problem. Screen recording currently supports local vaults.

See [:Editor] and [:Shortcuts] for the rest of the editing and keyboard workflows.

On newer Wayland desktops, global-shortcut permission also requires StillPoint's installed desktop identity. The Linux installer supplies a hidden `app.stillpoint.StillPoint.desktop` entry for this purpose; rerun the installer after updating a packaged build.
