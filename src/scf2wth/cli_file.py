"""
scf2wth.cli_file

Parses the small amount of DSSAT .CLI header info this package needs: station code (INSI) and coordinates (LAT/LONG). 
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CliSiteInfo:
    insi: str
    lat: float
    lon: float
    elev: float | None = None
    start_year: int | None = None
    duration: int | None = None

    @property
    def end_year(self) -> int | None:
        if self.start_year is None or self.duration is None:
            return None
        return self.start_year + self.duration - 1


def _find_header_row(lines: list[str], required_tokens: set[str]) -> dict[str, str] | None:
    """
    Find a "@ COL1 COL2 ..."  and zip it with the data line immediately after it. 
    Handles both a space-separated "@" marker ("@ INSI ...") and one glued onto the first column name ("@START ...").

    Returns None if no matching header is found - the caller decides
    whether that's fatal for what it needed.
    """
    for i, line in enumerate(lines):
        tokens = line.split()
        if not tokens:
            continue

        if tokens[0] == "@":
            header_tokens = tokens[1:]
        elif tokens[0].startswith("@"):
            header_tokens = [tokens[0][1:]] + tokens[1:]
        else:
            continue

        if not required_tokens.issubset(header_tokens):
            continue

        if i + 1 >= len(lines):
            return None  # header found but no data line follows
        data_tokens = lines[i + 1].split()
        if len(data_tokens) < len(header_tokens):
            return None  # column-count mismatch - let caller report specifics
        return dict(zip(header_tokens, data_tokens))

    return None


def read_cli_site_info(cli_path: str | Path) -> CliSiteInfo:
    """
    Read INSI/LAT/LONG/ELEV (from the "@ INSI LAT LONG ELEV ..." header)
    and Startyear/Endyear (derived from the "@START DURN ..." header) from
    a .CLI file.

    INSI/LAT/LONG are required. Raises ValueError if that header can't be found or parsed.

    @START/DURN is optional. If missing or unparseable, start_year/
    duration/end_year are simply None on the returned CliSiteInfo.
    """
    cli_path = Path(cli_path)
    lines = cli_path.read_text().splitlines()

    site_row = _find_header_row(lines, {"INSI", "LAT", "LONG"})
    if site_row is None:
        raise ValueError(
            f"{cli_path}: could not find a '@ INSI ... LAT LONG ...' header "
            "line with a matching data line; is this a valid DSSAT .CLI file?"
        )

    try:
        insi = site_row["INSI"]
        lat = float(site_row["LAT"])
        lon = float(site_row["LONG"])
    except KeyError as e:
        raise ValueError(f"{cli_path}: header is missing expected column {e}") from e
    except ValueError as e:
        raise ValueError(f"{cli_path}: could not parse LAT/LONG as numbers: {e}") from e

    elev = None
    if "ELEV" in site_row:
        try:
            elev = float(site_row["ELEV"])
        except ValueError:
            pass  # ELEV is a nice-to-have, not worth failing the whole parse over

    start_year = None
    duration = None
    date_row = _find_header_row(lines, {"START", "DURN"})
    if date_row is not None:
        try:
            start_year = int(float(date_row["START"]))
            duration = int(float(date_row["DURN"]))
        except (KeyError, ValueError):
            pass  # optional - leave as None rather than failing the whole parse

    return CliSiteInfo(
        insi=insi, lat=lat, lon=lon, elev=elev,
        start_year=start_year, duration=duration,
    )
