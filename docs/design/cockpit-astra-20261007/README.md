# Astra mining cockpit design package

Eight image-generated concepts, a recommended layout, and an isolated interactive prototype. The game capture stays untouched; all app UI is outside its pixels. The production dashboard has not been changed.

## Review locally

With the existing mining dashboard running on port 8765, from the repository root:

```powershell
& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' docs/design/cockpit-astra-20261007/run-preview.py
```

- Prototype: <http://127.0.0.1:8875/>
- Eight concepts: <http://127.0.0.1:8875/assets/gallery.html>
- Use `--upstream-port PORT` when the dashboard uses another port.
- Use the **Preview state** menu to try local simulated workflows. Live actions are disabled.

The server accepts current read-only dashboard data and rejects write requests. It adds no input, focus, reconnect, or action-enable path. The existing dashboard and cockpit preview remain separate applications.

## Files

- [design-review.md](design-review.md): Astra's comparison, recommended layout, interaction contracts, and production implementation sequence.
- [validation.md](validation.md): checks performed and limits of this prototype.
- `01-flight-deck.png` through `08-adaptive-command.png`: the eight original generated images, preserved unchanged.
- [prompts.json](prompts.json) and [generation-note.txt](generation-note.txt): exact generation prompts and the common suffix applied to concepts 2 through 8.
- [prototype.html](prototype.html), [gallery.html](gallery.html), and [run-preview.py](run-preview.py): runnable review artifacts.

The image-generated game scenes are placeholders. Their fictional HUD marks and occasional incorrect button states are not product requirements; the written specification and browser prototype define the intended behavior.
