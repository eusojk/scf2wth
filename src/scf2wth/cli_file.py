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


def read_cli_site_info(cli_path: str | Path) -> CliSiteInfo:
    """
    Read the INSI/LAT/LONG (and ELEV, if present) from a .CLI file's
    "@ INSI LAT LONG ELEV ..." header/data line pair.

    Raises ValueError if that header line (or its data line) can't be found or parsed.
    """
    cli_path = Path(cli_path)
    lines = cli_path.read_text().splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        tokens = line.split()
        if tokens and tokens[0] == "@" and "INSI" in tokens and "LAT" in tokens:
            header_idx = i
            break
        # some .CLI files may glue "@" onto "INSI" (no space); so handle both
        if tokens and tokens[0].upper() in ("@INSI",) :
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            f"{cli_path}: could not find a '@ INSI ... LAT LONG ...' header "
            "line - is this a valid DSSAT .CLI file?"
        )

    header_tokens = lines[header_idx].split()
    if header_tokens[0] == "@":
        header_tokens = header_tokens[1:]  
    else:
        header_tokens[0] = header_tokens[0][1:]  

    if header_idx + 1 >= len(lines):
        raise ValueError(f"{cli_path}: header line found but no data line follows it")
    data_tokens = lines[header_idx + 1].split()

    if len(data_tokens) < len(header_tokens):
        raise ValueError(
            f"{cli_path}: header has {len(header_tokens)} columns "
            f"({header_tokens}) but data line only has {len(data_tokens)} "
            f"values ({data_tokens}). Possible column-alignment issue "
            "(fixed-width values touching, no whitespace between them)?"
        )

    row = dict(zip(header_tokens, data_tokens))

    try:
        insi = row["INSI"]
        lat = float(row["LAT"])
        lon = float(row["LONG"])
    except KeyError as e:
        raise ValueError(f"{cli_path}: header is missing expected column {e}") from e
    except ValueError as e:
        raise ValueError(f"{cli_path}: could not parse LAT/LONG as numbers: {e}") from e

    elev = None
    if "ELEV" in row:
        try:
            elev = float(row["ELEV"])
        except ValueError:
            pass  # ELEV is a nice-to-have, not worth failing the whole parse over

    return CliSiteInfo(insi=insi, lat=lat, lon=lon, elev=elev)
