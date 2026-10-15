# BSGO automated piloting

This project collects skills and observations for learning to play BSGO automatically.

## Set up on another Windows machine or VM

The live dashboard requires 64-bit Windows and Python 3.12. Install Git and
Python 3.12 (including the `py` launcher), then run the following in PowerShell.
Choose a writable parent folder for the clone:

```powershell
git clone https://github.com/lmerynda/bsgo.git
cd bsgo
py -3.12 -m venv tools/model-runtimes/fastvlm-venv
& .\tools\model-runtimes\fastvlm-venv\Scripts\python.exe -m pip install -r tools/offline-bot/requirements.txt
& .\tools\model-runtimes\fastvlm-venv\Scripts\python.exe -m pip check
```

Stop if any command fails. The environment path above is required by the
current launcher; create it on the destination machine instead of copying an
existing environment. Despite its historical `fastvlm-venv` name, this installs
only the dashboard's requirements. Codex, Ollama, Torch and model downloads are
not required.

Run a setup check without opening or controlling the game:

```powershell
$env:PYTHONPATH = (Resolve-Path 'tools/offline-bot').Path
& .\tools\model-runtimes\fastvlm-venv\Scripts\python.exe -m bsgo_offline.easy_launcher --help
& .\tools\model-runtimes\fastvlm-venv\Scripts\python.exe tools/offline-bot/run_tests.py --pattern test_easy_launcher.py
```

Install and run BSGO separately in the **same Windows desktop session** as the
dashboard, then double-click **Mining Dashboard.cmd**. New sessions start with
actions paused. Edge or Chrome provides the docked panel; the default browser
is the fallback. An optional desktop shortcut can be created with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/offline-bot/install-desktop-shortcut.ps1
```

For a VM, first verify that the game renders correctly inside the guest. Check
the dashboard's captured image and HUD readings before enabling actions. The
current control calibration uses a 2560 x 1440 **game client area** and its
calibrated HUD scale; other layouts can block controls. Successful installation
does not validate VM graphics, capture, input, or behavior after disconnecting
the VM console. The live capture and input backends do not support Linux.

Source, calibration files and packed test fixtures are included in Git. Game
installation files, credentials, Python environments and runtime logs are not.
Tests restore their own image fixtures automatically. To update a clean checkout,
run `git pull --ff-only`, then repeat the requirements installation and setup
check above. Restart the dashboard to load updated Python code.

## Project documentation

- [Mining runtime improvement plan](docs/architecture/mining-runtime-improvement-plan.md): ordered implementation tasks, concrete contracts, offline acceptance checks, and deferred BSGO validation.
- [Mining runtime offline results](docs/architecture/mining-runtime-offline-results.md): complete simulated cycle, 216 passing tests, 11 accepted threaded scenarios, and the remaining game-image perception work.
- [Standalone mining application architecture review](docs/architecture/standalone-mining-app.md): proposed thread-based runtime, published state, control ownership, and assumptions found in the current prototypes.
- [Building block experiment plan](docs/architecture/building-block-experiments.md): capture, input, OCR, small local models, Ollama planning, and shared RTX 4070 performance.
- [Decision windows and local model research](docs/architecture/decision-windows-and-local-models.md): measured request overhead, task deadlines, and Hugging Face candidates for recognition and planning.
- [Offline bot harness](tools/offline-bot/README.md): screenshot replay, independent shared state, recording-only input, a synthetic receiver, delayed decisions, and fault tests.
- [Offline experiment results](docs/architecture/offline-bot-experiments.md): observed scheduling independence, action/effect traces, cancellation, stale state, and missing brake acknowledgement.
- [Native Windows backend experiments](docs/architecture/native-backend-experiments.md): accepted client capture and guarded input delivery against an owned receiver, independent pixel observation, and remaining BSGO compatibility.
- [Ship-control skill](.agents/skills/bsgo-ship-control/SKILL.md): live flight, camera, targeting, systems, and safe stopping.
- [Mining skill](.agents/skills/bsgo-mining/SKILL.md): scan, choose, approach, and mine asteroids with the current guns in slots 3–6.
- [276 Exrun safe zone](docs/navigation/276-exrun-safe-zone.md): ring/sector operating boundary, updated October 1, 2026.
- Future scenarios such as combat can have their own skill.

Test images live packed in [tools/test-fixtures](tools/test-fixtures/restore.py): one sprite sheet plus an index. Test modules that need them import `fixture_data`, which restores them to their original (git-ignored) paths. The repository keeps no other images or captured data; runtime output goes to `logs/`.

Use both skills for mining navigation. The current game UI and the user's corrections govern equipment and controls.
