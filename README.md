# Soil-Disturbance-Detector
Compare before/after satellite images to detect ground disturbance (e.g., potential burial sites).

Key bits:
- Modular pipeline in `src/dgd/` (features, IO, model, vectorization, overlay, Sentinel download).
- CLI entrypoint: `python main.py` (or `python run_cli.py`) with tqdm progress (use `--no-progress` to silence). Repo auto-adds `src/` to PYTHONPATH.
- Tests under `tests/` (aiming for ~90% coverage) — run `pytest`.
