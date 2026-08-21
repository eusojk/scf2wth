# scf2wth

Orchestrates the full chain from a seasonal climate forecast to DSSAT-ready `.WTH` files:

```
scfbridge (plan -> fetch -> render paramPT.txt)
    -> fresampler  (FResampler1_PT: paramPT.txt -> N .WTD realizations)
    -> wtd2wth     (.CLI + all .WTD realizations -> .WTH files)
    -> saved into user-defined output folder
```

## Install

Depends on [`scfbridge`](https://github.com/eusojk/scfbridge) as a local path dependency:

```
<parent-dir>/
├── scfbridge/
└── scf2wth/
```

```bash
uv sync --group dev
```

## Usage

### As a Minimal CLI example.

Everything derivable from the `.CLI` file is omitted:

```bash
uv run scf2wth run \
    --location-label KALAMAZOO_MI \
    --cli /path/to/KALA.CLI --wtd /path/to/KALA.WTD \
    --year 2025 --planting-month 5 --harvest-month 11 --lead 1 \
    --wth-output-dir /path/to/outputs/KALA_WTHS
```

Notes: 
 * See "Auto-derived fields" below for Optional fields.
 * Run `scf2wth run --help` for every option, including `--json`, `--legacy`, and the tercile/factor overrides.
 * Prints one `.WTH` path per line on success (or a single JSON document with `--json`); non-zero exit with a one-line `scf2wth: <ErrorType>: <message>` on failure.


### As a library:

```python
from pathlib import Path
from scf2wth import SiteInputs, ForecastInputs, ToolPaths, run_pipeline

site = SiteInputs(
    location_label="KALAMAZOO_MI",
    cli_path=Path("/path/to/KALA.CLI"),
    wtd_path=Path("/path/to/KALA.WTD"),
)
for w in site.warnings:
    print("warning:", w)

forecast = ForecastInputs(year=2025, planting_month=5, harvest_month=11, lead=1)
tools = ToolPaths()   # resolves fresampler/wtd2wth via bin/ - see "Locating
                      # the binaries" below; raises FileNotFoundError if
                      # neither binary can be found anywhere

wth_files = run_pipeline(
    site, forecast, tools,
    wth_output_dir=Path("/path/to/outputs/KALA_WTHS"),
)
```

Each stage (`build_param_pt`, `run_fresampler`, `run_wtd2wth`) can also be called individually if you need to inspect intermediate `.WTD` files or re-run just one step.

## Auto-derived fields

`SiteInputs`' `station`, `start_year`, `end_year`, `lat`, `lon` are all
optional, read from the `.CLI` file if omitted: 
 * `station` from the filename (what `FResampler1_PT` actually keys off of), 
 * `start_year`/`end_year` from the `@START`/`DURN` header, 
 * `lat`/`lon` from the `@ INSI LAT LONG ...` header.

If you supply any of these explicitly anyway, they're cross-checked against the file's own value rather than silently overridden. A mismatch is not raised though; it just goes into `site.warnings` (printed to stderr by the CLI).


## Locating the binaries

`fresampler-bin`/`wtd2wth-bin` resolve in this order:

1. `--fresampler-bin`/`--wtd2wth-bin` (or the `ToolPaths` constructor args)
2. `$SCF2WTH_FRESAMPLER_BIN` / `$SCF2WTH_WTD2WTH_BIN` environment variables
3. `bin/fresampler_pt_patched` / `bin/wtd2wth`

Raises `FileNotFoundError` listing all three options if none.


## Known limitation(s): 

* Supported platform(s): Linux

* Script can hang indefinitely on cold-season forecasts:
     * `FResampler`'s year-sampling loop rejects any candidate year whose seasonal average temperature is `<= 0.0`, using that as a (mistaken) proxy for "missing data." 
        * At a cold-climate site, the coldest tercile of historical years can end up with *every member* below zero; causing the process to spin forever with zero chance of success.
        * This is most likely in the original Fortran algorithm and is being tracked as a separate fix, not yet applied here. 
     * So, if a run seems to hang with no new `.WTD` files appearing for several minutes, especially for a winter-anchored trimester (JFM, DJF, NDJ) at a cold-climate site, this is almost certainly why. The current workaround is to just kill that process.

## Acknowledgements

* [Han and Ines (2017)](https://doi.org/10.1016/j.crm.2017.09.003), original authors of the methods for downscaling probabilistic seasonal climate forecasts.  
* [HONDA Kiyoshi](https://www.hondalab.net/), original author of `exportPHNT.exe`, a WTD-to-WTH utility.
