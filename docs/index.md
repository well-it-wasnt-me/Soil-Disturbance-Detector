# Disturbed Ground Detector

Free software that compares free satellite imagery against itself, season by season, and points at ground that changed when it should not have. The targets are illegal dumping, waste burning and waste that was buried and covered over.

It costs nothing and needs no account. It was built to help law enforcement, and it produces **leads, not evidence**. Every lead needs a person to look at it.

## Three tools

**`dgd scan`** is the main tool. Give it a place and a time window. It needs no training data, no labels and no idea where anything is. It compares the window with the same season in previous years and reports what changed, ranked, with images.

**`dgd graves`** works at drone and aerial scale (3 cm to 1 m) and ranks grave-sized patches of disturbed ground for a search team. See [grave search](graves.md).

**`dgd classify`** is the original supervised mode. Bring a before raster, an after raster and polygons you labeled yourself. A RandomForest learns your "disturbed" class and finds more of it. Useful when you already know some sites in a region and want their cousins.

## Where to start

1. [Quickstart](quickstart.md) to install and run a first scan.
2. [Field guide](field-guide.md) before you send anyone anywhere.
3. [How it works](how-it-works.md) when somebody asks you to explain it.
4. [Evaluation](evaluation.md) when somebody asks how good it is.

## Limits, up front

- Sentinel-2 pixels are 10 m. A single grave is invisible to it. Surface dumps of a few hundred square metres and up are fair game.
- It detects change. A dump that was already there before the baseline years is part of the scenery as far as it knows.
- Clouds win sometimes. In the tropics, often.
- Farming produces most of the false alarms. The scanner knows this and ranks farm-shaped leads lower, but it does not throw them away, because burying waste in fields is a real and popular crime.
