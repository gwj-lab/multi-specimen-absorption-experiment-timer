# Absorption Experiment Timer

A Windows desktop timer for capillary water absorption experiments. It supports multiple specimens running at the same time, custom specimen names, per-specimen countdown state, audible reminders, and CSV-based weighing records.

## Features

- Reads a weighing schedule from `single_specimen_weighing_template.csv`.
- Supports multiple specimens with independent countdowns.
- Allows manually selecting the next weighing index for each specimen.
- Plays a sound when a specimen reaches its weighing time.
- Records weighing mass and notes into per-specimen CSV files.
- Shows the full planned schedule and recorded results in the right-side table.
- Supports editing and clearing recorded weighing values from the right-side panel.
- Saves local countdown state in `timer_state.json`.

## Requirements

- Windows
- Python 3.10 or newer
- No runtime third-party Python package is required for normal source execution.
- Optional: PyInstaller, only needed when building the `.exe`.

## Run From Source

```powershell
python .\absorption_timer.py
```

Run the built-in self-test:

```powershell
python .\absorption_timer.py --self-test
```

## Schedule CSV

The app reads `single_specimen_weighing_template.csv` from the same folder as the script or executable. Keep the columns unchanged:

```csv
序号,时间间隔,累计时间
0,0 s,0 s
1,2 s,2 s
2,5 min,5 min
3,1 h,1 h
```

Supported interval units:

- `s`
- `min`
- `h`

Restart the app after changing the schedule CSV.

## Data Files

These files are generated locally and are intentionally not tracked by Git:

- `timer_state.json`: current specimens and countdown state.
- `results/`: per-specimen weighing result CSV files.
- `dist/`, `build/`, portable release archives, and generated executables.

## Build EXE

Install PyInstaller if needed:

```powershell
python -m pip install pyinstaller
```

Build a windowed single-file executable:

```powershell
python -m PyInstaller --onefile --windowed --name AbsorptionExperimentTimer .\absorption_timer.py --noconfirm
```

For portable use, place these items in one folder:

```text
AbsorptionExperimentTimer.exe
single_specimen_weighing_template.csv
timer_state.json
results/
```

## License

MIT License.
