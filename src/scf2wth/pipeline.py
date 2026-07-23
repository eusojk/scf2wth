"""
scf2wth.pipeline

Orchestrates the full chain from a seasonal climate forecast to DSSAT-ready
.WTH files:

    scfbridge (plan -> fetch -> render paramPT.txt)
        -> fresampler (FResampler1_PT: paramPT.txt -> N .WTD realizations)
        -> wtd2wth (.CLI + each .WTD -> .WTH files)
        -> saved into a dedicated output folder for DSSAT experiments

Each stage is a separate function so any one of them can be tested,
replaced, or invoked standalone. run_pipeline() at the bottom composes all
of them for the common "give me a location and get .WTH files" case.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scfbridge import (
    get_scf_trimester_plan,
    fetch_tercile_forecast_for_plan,
    build_param_pt_record,
    write_param_pt_file,
)

from .cli_file import read_cli_site_info


# Inputs

@dataclass(kw_only=True)
class SiteInputs:
    """Everything needed to run the pipeline for one location.

    lat/lon are optional: if omitted, they're read directly from the .CLI
    file's own "@ INSI LAT LONG ..." header. If supplied anyway, they're
    cross-checked against the .CLI's own values.
    """

    location_label: str          # human-readable, e.g. "KALAMAZOO_MI" - used for output folder naming
    cli_path: Path               # path to the site's .CLI file
    wtd_path: Path               # path to the site's historical baseline .WTD file
    station: str                 # 4-letter station code; must match the .CLI/.WTD filename prefix
    start_year: int
    end_year: int
    lat: float | None = None
    lon: float | None = None
    warnings: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.cli_path = Path(self.cli_path)
        self.wtd_path = Path(self.wtd_path)
        if self.cli_path.stem.upper() != self.station.upper():
            raise ValueError(
                f"station={self.station!r} does not match .CLI filename "
                f"{self.cli_path.name!r}. FResampler1_PT requires these to "
                "match (it constructs realization filenames from the station "
                "code, and reads .CLI/.WTD by that same name)."
            )
        if self.wtd_path.stem.upper() != self.station.upper():
            raise ValueError(
                f"station={self.station!r} does not match .WTD filename "
                f"{self.wtd_path.name!r} (same requirement as .CLI above)."
            )

        try:
            cli_info = read_cli_site_info(self.cli_path)
        except ValueError as e:
            if self.lat is None or self.lon is None:
                raise ValueError(
                    f"lat/lon not supplied, and could not be read from "
                    f"{self.cli_path} to derive them automatically: {e}"
                ) from e
            self.warnings.append(
                f"Could not read coordinates from {self.cli_path} to "
                f"cross-check against the supplied lat/lon: {e}"
            )
            cli_info = None

        if cli_info is not None:
            if cli_info.insi.upper() != self.station.upper():
                self.warnings.append(
                    f"station={self.station!r} matches the .CLI filename but "
                    f"not the file's own internal INSI code ({cli_info.insi!r}). "
                    "Not necessarily wrong (wtd2wth still proceeds), "
                    "but worth confirming this is the file you meant."
                )
            if self.lat is None:
                self.lat = cli_info.lat
            elif abs(self.lat - cli_info.lat) > 0.05:
                self.warnings.append(
                    f"supplied lat={self.lat} differs from {self.cli_path}'s "
                    f"own LAT ({cli_info.lat}) by more than 0.05 degrees "
                    "(~5 km). Please double check this is intentional."
                )
            if self.lon is None:
                self.lon = cli_info.lon
            elif abs(self.lon - cli_info.lon) > 0.05:
                self.warnings.append(
                    f"supplied lon={self.lon} differs from {self.cli_path}'s "
                    f"own LONG ({cli_info.lon}) by more than 0.05 degrees "
                    "(~5 km). Please double check this is intentional."
                )


@dataclass
class ForecastInputs:
    """What scfbridge needs to build paramPT.txt."""

    year: int
    planting_month: int
    harvest_month: int
    lead: int = 0
    factor_r_rain: float = 1.0
    factor_r_temp: float = 1.0
    iterations: int = 0


@dataclass
class ToolPaths:
    """External executables this pipeline shells out to. No hardcoded names
    or locations; both must be supplied explicitly."""

    fresampler_bin: Path
    wtd2wth_bin: Path
    seed: int = 42      # explicit seed for the ported PT binary

    def __post_init__(self):
        self.fresampler_bin = Path(self.fresampler_bin)
        self.wtd2wth_bin = Path(self.wtd2wth_bin)


# Stage 1: paramPT.txt (via scfbridge)

def build_param_pt(site: SiteInputs, forecast: ForecastInputs, out_dir: Path) -> Path:
    """
    Run scfbridge's plan -> fetch -> render chain and write paramPT.txt
    into out_dir. Returns the path to the written file.

    Uses the "most ideal" trimester automatically (Startmonth == planting month).
    """
    plan = get_scf_trimester_plan(
        year=forecast.year,
        planting_month=site_planting_month(forecast),
        harvest_month=forecast.harvest_month,
    )
    trimester = plan["most_ideal_trimester"]

    rainfall = fetch_tercile_forecast_for_plan(
        plan, trimester, forecast.lead, "rainfall", lat=site.lat, lon=site.lon
    )
    temperature = fetch_tercile_forecast_for_plan(
        plan, trimester, forecast.lead, "temperature", lat=site.lat, lon=site.lon
    )

    rec = build_param_pt_record(
        station=site.station,
        start_year=site.start_year,
        end_year=site.end_year,
        year=forecast.year,
        planting_month=forecast.planting_month,
        harvest_month=forecast.harvest_month,
        trimester=plan["records"][trimester],
        rainfall_tercile=rainfall,
        temperature_tercile=temperature,
        factor_r_rain=forecast.factor_r_rain,
        factor_r_temp=forecast.factor_r_temp,
        iterations=forecast.iterations,
    )
    warnings = rec.validate()
    for w in warnings:
        print(f"  WARNING: {w}", file=sys.stderr)

    out_path = out_dir / "paramPT.txt"
    write_param_pt_file(rec, str(out_path))
    return out_path


def site_planting_month(forecast: ForecastInputs) -> int:
    return forecast.planting_month


# Stage 2: fresampler (FResampler1_PT)

def run_fresampler(site: SiteInputs, tools: ToolPaths, work_dir: Path) -> list[Path]:
    """
    Stage the .CLI/.WTD inputs and paramPT.txt into work_dir (FResampler1_PT
        does its own file I/O relative to the current working directory), 
    Run the fresampler binary, and 
    Return the list of generated .WTD realization files.

    Assumes build_param_pt() already wrote work_dir/paramPT.txt.
    """
    param_path = work_dir / "paramPT.txt"
    if not param_path.exists():
        raise FileNotFoundError(
            f"{param_path} not found - call build_param_pt() before run_fresampler()"
        )

    for src in (site.cli_path, site.wtd_path):
        dest = work_dir / src.name
        if not dest.exists():
            shutil.copy(src, dest)

    result = subprocess.run(
        [str(tools.fresampler_bin), "paramPT.txt", str(tools.seed)],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"fresampler exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    realizations = sorted(work_dir.glob(f"{site.station.upper()}[0-9][0-9][0-9][0-9].WTD"))
    if not realizations:
        raise RuntimeError(
            f"fresampler ran (exit 0) but produced no {site.station}NNNN.WTD "
            f"files in {work_dir}. stdout:\n{result.stdout}"
        )
    return realizations


# Stage 3: wtd2wth

class Wtd2wthError(RuntimeError):
    """A wtd2wth invocation exited non-zero, or produced no parseable JSON."""

    def __init__(self, returncode: int, message: str, argv: list[str]):
        self.returncode = returncode
        self.message = (message or "").strip()
        self.argv = argv
        super().__init__(
            f"wtd2wth exited {returncode}: {self.message or '(no message)'}"
        )


def run_wtd2wth(site: SiteInputs, tools: ToolPaths, wtd_files: list[Path],
                 out_dir: Path, *, legacy: bool = False) -> dict:
    """
    Run wtd2wth once, across ALL supplied .WTD realizations for this site in a single invocation.

    wtd2wth writes into <out_dir>/<INSI>/ in default mode.
    Outcomes:
        * INSI is read from *inside* the .CLI file and may not match site.station.
        * Always read the actual output paths from the returned dict (outcomes[].files).

    Returns the parsed --json result:
        {
            "status": "ok",
            "cli_path": "...",
            "write_mode": "subfolder-guarded" | "flat-legacy",
            "warnings": [...],
            "outcomes": [{"wtd_path": "...", "files": ["...", ...]}],
            "total_files": N,
        }

    Raises Wtd2wthError on any non-zero exit or unparseable stdout.
    """
    argv: list[str] = [
        str(tools.wtd2wth_bin),
        str(site.cli_path),
        *[str(w) for w in wtd_files],
        "--out-dir", str(out_dir),
        "--json",
    ]
    if legacy:
        argv.append("--legacy")

    proc = subprocess.run(argv, capture_output=True, text=True)

    # --json always emits exactly one JSON document to stdout, success or failure.
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise Wtd2wthError(proc.returncode, proc.stderr or "no JSON output", argv)

    if data.get("status") != "ok":
        raise Wtd2wthError(proc.returncode, data.get("message", "unknown error"), argv)

    return data


# Full pipeline

def run_pipeline(site: SiteInputs, forecast: ForecastInputs, tools: ToolPaths,
                  wth_output_dir: Path, work_dir: Path | None = None,
                  *, legacy: bool = False) -> list[Path]:
    """
    Run the full chain: paramPT.txt -> fresampler -> wtd2wth -> saved .WTH files.
     
    Returns the list of .WTH files wtd2wth actually wrote.

    work_dir: scratch directory for staging inputs and intermediate .WTD realizations. 
        Defaults to a subfolder of wth_output_dir named after site.location_label if not given. 
        Not cleaned up automatically; the .WTD realizations and FResampler debug output in there may be worth keeping alongside the final .WTH files.
    """
    wth_output_dir = Path(wth_output_dir)
    work_dir = Path(work_dir) if work_dir else wth_output_dir / f"_work_{site.location_label}"
    work_dir.mkdir(parents=True, exist_ok=True)
    wth_output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{site.location_label}] Stage 1: building paramPT.txt", file=sys.stderr)
    build_param_pt(site, forecast, work_dir)

    print(f"[{site.location_label}] Stage 2: running fresampler", file=sys.stderr)
    realizations = run_fresampler(site, tools, work_dir)
    print(f"[{site.location_label}]   {len(realizations)} .WTD realizations generated", file=sys.stderr)

    print(f"[{site.location_label}] Stage 3: running wtd2wth on all {len(realizations)} realizations", file=sys.stderr)
    result = run_wtd2wth(site, tools, realizations, wth_output_dir, legacy=legacy)
    for w in result.get("warnings", []):
        print(f"[{site.location_label}]   wtd2wth warning: {w}", file=sys.stderr)

    all_wth = [Path(f) for outcome in result["outcomes"] for f in outcome["files"]]

    print(f"[{site.location_label}] Done: {len(all_wth)} .WTH files "
          f"({result['total_files']} reported) written under {wth_output_dir}",
          file=sys.stderr)
    return all_wth


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_run(args: argparse.Namespace) -> None:
    try:
        site = SiteInputs(
            location_label=args.location_label,
            lat=args.lat,
            lon=args.lon,
            cli_path=Path(args.cli),
            wtd_path=Path(args.wtd),
            station=args.station,
            start_year=args.start_year,
            end_year=args.end_year,
        )
    except ValueError as e:
        print(f"scf2wth: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    for w in site.warnings:
        print(f"scf2wth: WARNING: {w}", file=sys.stderr)

    forecast = ForecastInputs(
        year=args.year,
        planting_month=args.planting_month,
        harvest_month=args.harvest_month,
        lead=args.lead,
        factor_r_rain=args.factor_r_rain,
        factor_r_temp=args.factor_r_temp,
        iterations=args.iterations,
    )
    tools = ToolPaths(
        fresampler_bin=Path(args.fresampler_bin),
        wtd2wth_bin=Path(args.wtd2wth_bin),
        seed=args.seed,
    )

    try:
        wth_files = run_pipeline(
            site, forecast, tools,
            wth_output_dir=Path(args.wth_output_dir),
            work_dir=Path(args.work_dir) if args.work_dir else None,
            legacy=args.legacy,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"scf2wth: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps({"wth_files": [str(p) for p in wth_files]}, indent=2))
    else:
        for p in wth_files:
            print(p)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orchestrate scfbridge -> fresampler -> wtd2wth to "
                     "produce DSSAT-ready .WTH files from a seasonal "
                     "climate forecast."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline for one site/forecast")

    site_group = p_run.add_argument_group("site")
    site_group.add_argument("--location-label", required=True, help="e.g. KALAMAZOO_MI")
    site_group.add_argument("--lat", type=float, default=None, help="optional - read from the .CLI file if omitted")
    site_group.add_argument("--lon", type=float, default=None, help="optional - read from the .CLI file if omitted")
    site_group.add_argument("--cli", required=True, help="path to the site's .CLI file")
    site_group.add_argument("--wtd", required=True, help="path to the site's historical baseline .WTD file")
    site_group.add_argument("--station", required=True, help="4-letter station code, must match .CLI/.WTD filenames")
    site_group.add_argument("--start-year", type=int, required=True)
    site_group.add_argument("--end-year", type=int, required=True)

    forecast_group = p_run.add_argument_group("forecast")
    forecast_group.add_argument("--year", type=int, required=True)
    forecast_group.add_argument("--planting-month", type=int, required=True)
    forecast_group.add_argument("--harvest-month", type=int, required=True)
    forecast_group.add_argument("--lead", type=int, default=0)
    forecast_group.add_argument("--factor-r-rain", type=float, default=1.0)
    forecast_group.add_argument("--factor-r-temp", type=float, default=1.0)
    forecast_group.add_argument("--iterations", type=int, default=0)

    tools_group = p_run.add_argument_group("tools")
    tools_group.add_argument("--fresampler-bin", required=True, help="path to the compiled FResampler1_PT binary")
    tools_group.add_argument("--wtd2wth-bin", required=True, help="path to the compiled wtd2wth binary")
    tools_group.add_argument("--seed", type=int, default=42, help="explicit RNG seed for fresampler (see project history: this is what makes runs reproducible)")

    output_group = p_run.add_argument_group("output")
    output_group.add_argument("--wth-output-dir", required=True, help="where wtd2wth writes the final .WTH files")
    output_group.add_argument("--work-dir", default=None, help="scratch dir for staged inputs + .WTD realizations; defaults to <wth-output-dir>/_work_<location-label>")
    output_group.add_argument("--legacy", action="store_true", help="forward --legacy to wtd2wth; comparison-testing only, see README")
    output_group.add_argument("--json", action="store_true", help="print the resulting .WTH paths as JSON instead of one per line")

    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
