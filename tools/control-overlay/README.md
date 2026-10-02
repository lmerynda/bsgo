# Manual BSGO control overlay

`bsgo-controls.au3` is an AutoIt prototype for a floating, always-on-top button
panel. It has no automatic gameplay loop, startup keys, focus activation or
background commands. Button clicks send input only when the active window is
named `Battlestar Galactica Online` and belongs to `bsgo.exe`.

AutoIt is installed at `C:\Program Files (x86)\AutoIt3\AutoIt3.exe`.
On October 1, 2026, Au3Check v3.3.18.0 checked the script with zero errors and
zero warnings. The prototype has not been launched or live-tested. Double-click
`bsgo-controls.au3` when ready to test, or launch it with that AutoIt interpreter.
If the game's title changes, `_ActiveGame()` must be updated from the observed
title.

The panel includes left-Shift/M and right-Shift/M, plain M, Shift/F, Z, G and
PageDown. PageDown commands idle throttle; read the speed HUD to verify stopping.
Map buttons send shortcuts and do not detect which map opened.

The panel uses a non-activating tool window and `WM_MOUSEACTIVATE` handling to
preserve game focus. Click the game first, then the desired panel command. Focus
preservation and button delivery still need validation on the live client. Use
windowed or borderless mode if an exclusive fullscreen client hides the overlay.

## Investigate Shift+M

1. Start stationary in the same flight view for each trial.
2. Compare physical Shift+M with the left-Shift/M button at 100 ms.
3. Verify the resulting map visually. Return to the same starting view.
4. Try 250/500 ms using the timing button, then right-Shift/M.
5. Record which map opened and the settings. Do not interpret `Sent` as success.

For a shifted command the sequence is modifier down, delay, key down, delay,
key up, delay, modifier up. Each delay is the selected timing value. Commands
abort before the main key if focus changes after modifier-down; owned keys are
released on normal exit and chord completion. Closing the overlay sends no new
command. Abruptly killing the interpreter during a chord can bypass cleanup.

Keep the mining agent paused while testing manual controls. This prototype is
for human-clicked investigation; it has not replaced the supported Sky backend
or the guarded mining approach controller.

References: [AutoIt Send](https://www.autoitscript.com/autoit3/docs/functions/Send.htm),
[GUICreate](https://www.autoitscript.com/autoit3/docs/functions/GUICreate.htm),
[GUIRegisterMsg](https://www.autoitscript.com/autoit3/docs/functions/GUIRegisterMsg.htm).
