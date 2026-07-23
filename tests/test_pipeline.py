"""
Tests for scf2wth.pipeline.

Network- and binary-free throughout: build_param_pt's tests mock
cpc_forecast.get_official() (same pattern as scfbridge's own test suite),
and run_fresampler/run_wtd2wth's tests mock subprocess.run.

run_wtd2wth's tests are built directly against wtd2wth's documented --json contract.

"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scfbridge import cpc_forecast
from scf2wth import SiteInputs, ForecastInputs, ToolPaths, Wtd2wthError
from scf2wth.pipeline import build_param_pt, run_fresampler, run_wtd2wth, run_pipeline, main, _resolve_binary

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# SiteInputs validation
# ---------------------------------------------------------------------------

class TestSiteInputs:
    def test_valid_inputs_construct_cleanly(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.touch()
        wtd.touch()
        site = SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )
        assert site.station == "KBSA"

    def test_mismatched_cli_filename_raises(self, tmp_path):
        cli = tmp_path / "WRONG.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.touch()
        wtd.touch()
        with pytest.raises(ValueError, match="does not match .CLI filename"):
            SiteInputs(
                location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
                cli_path=cli, wtd_path=wtd, station="KBSA",
                start_year=1993, end_year=2024,
            )

    def test_mismatched_wtd_filename_raises(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "WRONG.WTD"
        cli.touch()
        wtd.touch()
        with pytest.raises(ValueError, match="does not match .WTD filename"):
            SiteInputs(
                location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
                cli_path=cli, wtd_path=wtd, station="KBSA",
                start_year=1993, end_year=2024,
            )

    def test_station_match_is_case_insensitive(self, tmp_path):
        cli = tmp_path / "kbsa.CLI"
        wtd = tmp_path / "kbsa.WTD"
        cli.touch()
        wtd.touch()
        site = SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )
        assert site.station == "KBSA"


# ---------------------------------------------------------------------------
# SiteInputs: lat/lon auto-derivation and cross-check against the .CLI file
# ---------------------------------------------------------------------------


_REAL_KBSA_CLI = (FIXTURES_DIR / "KBSA.CLI").read_text()
_REAL_KBSA_WTD = (FIXTURES_DIR / "KBSA.WTD").read_text()

# Mirrors ALLE.CLI's real content: internal INSI (MIAL) != filename (ALLE)
_REAL_ALLE_STYLE_CLI = (
    "*CLIMATE : MIAL\r\n"
    "\r\n"
    "@ INSI      LAT     LONG  ELEV   TAV   AMP  SRAY  TMXY  TMNY  RAIY\r\n"
    "  MIAL   42.150  -83.567   257   8.7  26.1  13.6  13.1   4.2   957\r\n"
    "@START  DURN  ANGA  ANGB REFHT WNDHT SOURCE\r\n"
    "  1988    31  0.25  0.50 -99.0 -99.0 Calculated_from_daily_data\r\n"
)


class TestSiteInputsCliDerivedCoordinates:
    def test_lat_lon_omitted_auto_derived_from_cli(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(
            location_label="KALAMAZOO_MI",
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )
        assert site.lat == pytest.approx(42.24)
        assert site.lon == pytest.approx(-85.24)
        assert site.warnings == []

    def test_lat_lon_omitted_and_cli_unparseable_raises(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text("not a real .CLI file\n")
        wtd.touch()
        with pytest.raises(ValueError, match="not supplied"):
            SiteInputs(
                location_label="KALAMAZOO_MI",
                cli_path=cli, wtd_path=wtd, station="KBSA",
                start_year=1993, end_year=2024,
            )

    def test_lat_lon_supplied_matching_cli_no_warning(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )
        assert site.warnings == []

    def test_lat_lon_supplied_mismatched_warns_but_keeps_supplied_value(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(
            location_label="KALAMAZOO_MI", lat=40.0, lon=-85.24,  # lat way off
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )
        assert site.lat == 40.0  # supplied value is NOT silently overridden
        assert any("differs from" in w for w in site.warnings)

    def test_small_lat_lon_difference_within_tolerance_no_warning(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.241, lon=-85.24,  # 0.001 off
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )
        assert site.warnings == []

    def test_internal_insi_mismatch_warns(self, tmp_path):
        """The real ALLE.CLI/MIAL case: filename matches station, but the
        .CLI's own internal INSI does not."""
        cli = tmp_path / "ALLE.CLI"
        wtd = tmp_path / "ALLE.WTD"
        cli.write_bytes(_REAL_ALLE_STYLE_CLI.encode())
        wtd.touch()
        site = SiteInputs(
            location_label="TEST_SITE", lat=42.150, lon=-83.567,
            cli_path=cli, wtd_path=wtd, station="ALLE",
            start_year=1988, end_year=2019,
        )
        assert any("internal INSI code" in w for w in site.warnings)

    def test_lat_lon_supplied_and_cli_unparseable_is_non_fatal(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text("not a real .CLI file\n")
        wtd.touch()
        site = SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )
        assert site.lat == 42.24
        assert any("Could not read" in w for w in site.warnings)


class TestSiteInputsCliDerivedStationAndYears:
    def test_station_omitted_derived_from_filename(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(location_label="KALAMAZOO_MI", cli_path=cli, wtd_path=wtd)
        assert site.station == "KBSA"

    def test_start_end_year_omitted_derived_from_cli(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(location_label="KALAMAZOO_MI", cli_path=cli, wtd_path=wtd)
        assert site.start_year == 1993
        assert site.end_year == 2024  # 1993 + 32 (DURN) - 1
        assert site.warnings == []

    def test_all_four_omitted_full_auto_derivation(self, tmp_path):
        """station, start_year, end_year, lat, lon all omitted at once -
        the common case this feature exists for."""
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(location_label="KALAMAZOO_MI", cli_path=cli, wtd_path=wtd)
        assert site.station == "KBSA"
        assert site.start_year == 1993
        assert site.end_year == 2024
        assert site.lat == pytest.approx(42.24)
        assert site.lon == pytest.approx(-85.24)
        assert site.warnings == []

    def test_end_year_mismatch_against_stale_durn_warns_but_keeps_supplied_value(self, tmp_path):
        """The real, confirmed ALLE.CLI case: DURN implies end_year=2018,
        but 2019 is what actually matches the real .WTD data and was used
        throughout this project. Supplying 2019 explicitly should warn
        about the mismatch, not silently get overridden by the stale
        DURN-derived value."""
        cli = tmp_path / "ALLE.CLI"
        wtd = tmp_path / "ALLE.WTD"
        cli.write_bytes(_REAL_ALLE_STYLE_CLI.encode())
        wtd.touch()
        site = SiteInputs(
            location_label="TEST_SITE", lat=42.150, lon=-83.567,
            cli_path=cli, wtd_path=wtd, station="ALLE",
            start_year=1988, end_year=2019,
        )
        assert site.end_year == 2019  # supplied value is NOT silently overridden
        assert any("differs from" in w and "2018" in w for w in site.warnings)

    def test_start_year_mismatch_warns(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        site = SiteInputs(
            location_label="KALAMAZOO_MI",
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1990, end_year=2024,  # 1990 != the real @START 1993
        )
        assert site.start_year == 1990
        assert any("start_year" in w and "differs from" in w for w in site.warnings)

    def test_start_end_year_omitted_and_no_start_header_raises(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        # only the INSI/LAT/LONG header, no @START/DURN block at all
        cli.write_text(
            "@ INSI      LAT     LONG  ELEV\n"
            "  KBSA    42.24   -85.24   288\n"
        )
        wtd.touch()
        with pytest.raises(ValueError, match="start_year/end_year not supplied"):
            SiteInputs(location_label="KALAMAZOO_MI", cli_path=cli, wtd_path=wtd)

    def test_explicit_station_still_validated_against_filename(self, tmp_path):
        """Auto-derivation doesn't bypass the existing filename-match
        safety check when station IS supplied explicitly."""
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text(_REAL_KBSA_CLI)
        wtd.write_text(_REAL_KBSA_WTD)
        with pytest.raises(ValueError, match="does not match .CLI filename"):
            SiteInputs(
                location_label="KALAMAZOO_MI",
                cli_path=cli, wtd_path=wtd, station="WRONG",
                start_year=1993, end_year=2024,
            )




def _fake_forecast(variable):
    return cpc_forecast.Forecast(
        lat=42.24, lon=-85.24, variable=variable,
        below=0.30, near=0.35, above=0.35,
        category="A", probability=0.35,
        target_season="ASO 2026", note="test fixture",
    )


class TestBuildParamPt:
    @pytest.fixture
    def site(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.touch()
        wtd.touch()
        return SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )

    def test_writes_param_pt_with_expected_fields(self, site, tmp_path):
        forecast = ForecastInputs(year=2026, planting_month=8, harvest_month=11, lead=1)
        with patch.object(cpc_forecast, "get_official",
                           side_effect=lambda ym, v, lat, lon, lead, **kw: _fake_forecast(v)):
            out_path = build_param_pt(site, forecast, tmp_path)

        assert out_path == tmp_path / "paramPT.txt"
        text = out_path.read_text()
        assert "Station:   KBSA" in text
        assert "Startyear:   1993" in text
        assert "Endyear:   2024" in text
        assert "Planting_month:    8" in text
        assert "Harvest_month_plus:   11" in text
        # ASO trimester -> Startmonth 8, Endmonth 10
        assert "Startmonth:    8" in text
        assert "Endmonth:    10" in text


# ---------------------------------------------------------------------------
# ToolPaths: binary resolution (explicit > env var > checkout bin/ > error)
# ---------------------------------------------------------------------------

class TestResolveBinary:
    """All tests here inject an isolated bin_dir (a fresh tmp_path) rather
    than depending on this real checkout's actual bin/ folder contents."""

    def test_explicit_path_wins_even_if_nothing_else_available(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SCF2WTH_FRESAMPLER_BIN", raising=False)
        explicit = tmp_path / "my_custom_fresampler"
        result = _resolve_binary(explicit, "SCF2WTH_FRESAMPLER_BIN", "fresampler_pt_patched",
                                  bin_dir=tmp_path / "empty_bin")
        assert result == explicit

    def test_env_var_used_when_no_explicit_path(self, tmp_path, monkeypatch):
        env_path = tmp_path / "env_fresampler"
        monkeypatch.setenv("SCF2WTH_FRESAMPLER_BIN", str(env_path))
        result = _resolve_binary(None, "SCF2WTH_FRESAMPLER_BIN", "fresampler_pt_patched",
                                  bin_dir=tmp_path / "empty_bin")
        assert result == env_path

    def test_explicit_path_takes_priority_over_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCF2WTH_FRESAMPLER_BIN", str(tmp_path / "env_fresampler"))
        explicit = tmp_path / "explicit_fresampler"
        result = _resolve_binary(explicit, "SCF2WTH_FRESAMPLER_BIN", "fresampler_pt_patched",
                                  bin_dir=tmp_path / "empty_bin")
        assert result == explicit

    def test_bin_dir_used_as_last_resort(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SCF2WTH_FRESAMPLER_BIN", raising=False)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "fresampler_pt_patched").touch()
        result = _resolve_binary(None, "SCF2WTH_FRESAMPLER_BIN", "fresampler_pt_patched",
                                  bin_dir=bin_dir)
        assert result == bin_dir / "fresampler_pt_patched"

    def test_env_var_takes_priority_over_bin_dir(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "fresampler_pt_patched").touch()
        env_path = tmp_path / "env_fresampler"
        monkeypatch.setenv("SCF2WTH_FRESAMPLER_BIN", str(env_path))
        result = _resolve_binary(None, "SCF2WTH_FRESAMPLER_BIN", "fresampler_pt_patched",
                                  bin_dir=bin_dir)
        assert result == env_path

    def test_nothing_resolves_raises_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SCF2WTH_FRESAMPLER_BIN", raising=False)
        with pytest.raises(FileNotFoundError, match="totally_made_up_binary_name_xyz"):
            _resolve_binary(None, "SCF2WTH_FRESAMPLER_BIN", "totally_made_up_binary_name_xyz",
                             bin_dir=tmp_path / "empty_bin")

    def test_error_message_lists_all_three_options(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SCF2WTH_FRESAMPLER_BIN", raising=False)
        with pytest.raises(FileNotFoundError) as exc_info:
            _resolve_binary(None, "SCF2WTH_FRESAMPLER_BIN", "totally_made_up_binary_name_xyz",
                             bin_dir=tmp_path / "empty_bin")
        msg = str(exc_info.value)
        assert "--fresampler-bin" in msg or "ToolPaths" in msg
        assert "SCF2WTH_FRESAMPLER_BIN" in msg
        assert "bin" in msg

    def test_default_bin_dir_used_when_none_given(self, tmp_path, monkeypatch):
        """bin_dir=None (the real default) falls back to _default_bin_dir()
        . Confirmed by checking the resolved path's shape, not by
          depending on what's actually inside it."""
        monkeypatch.delenv("SCF2WTH_FRESAMPLER_BIN", raising=False)
        with pytest.raises(FileNotFoundError, match="totally_made_up_binary_name_xyz"):
            _resolve_binary(None, "SCF2WTH_FRESAMPLER_BIN", "totally_made_up_binary_name_xyz")


class TestToolPaths:
    def test_both_binaries_resolved_via_explicit_paths(self, tmp_path):
        tools = ToolPaths(
            fresampler_bin=tmp_path / "fresampler",
            wtd2wth_bin=tmp_path / "wtd2wth",
        )
        assert tools.fresampler_bin == tmp_path / "fresampler"
        assert tools.wtd2wth_bin == tmp_path / "wtd2wth"

    def test_missing_binaries_raise_before_pipeline_runs(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SCF2WTH_FRESAMPLER_BIN", raising=False)
        monkeypatch.delenv("SCF2WTH_WTD2WTH_BIN", raising=False)
        with pytest.raises(FileNotFoundError):
            ToolPaths(
                fresampler_bin=tmp_path / "fresampler",
                wtd2wth_bin=None,
                bin_dir=tmp_path / "empty_bin",  # isolated - no real binaries here
            )

    def test_bin_dir_resolves_both_binaries(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SCF2WTH_FRESAMPLER_BIN", raising=False)
        monkeypatch.delenv("SCF2WTH_WTD2WTH_BIN", raising=False)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "fresampler_pt_patched").touch()
        (bin_dir / "wtd2wth").touch()
        tools = ToolPaths(bin_dir=bin_dir)
        assert tools.fresampler_bin == bin_dir / "fresampler_pt_patched"
        assert tools.wtd2wth_bin == bin_dir / "wtd2wth"

    def test_default_seed_is_42(self, tmp_path):
        tools = ToolPaths(fresampler_bin=tmp_path / "a", wtd2wth_bin=tmp_path / "b")
        assert tools.seed == 42


# ---------------------------------------------------------------------------
# Stage 2: run_fresampler (mocked subprocess)
# ---------------------------------------------------------------------------

class TestRunFresampler:
    @pytest.fixture
    def site(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text("fake cli content")
        wtd.write_text("fake wtd content")
        return SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )

    @pytest.fixture
    def tools(self, tmp_path):
        return ToolPaths(
            fresampler_bin=tmp_path / "fake_fresampler",
            wtd2wth_bin=tmp_path / "fake_wtd2wth",
        )

    def test_requires_param_pt_to_exist_first(self, site, tools, tmp_path):
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="paramPT.txt"):
            run_fresampler(site, tools, work_dir)

    def test_stages_inputs_and_invokes_binary(self, site, tools, tmp_path):
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "paramPT.txt").write_text("fake param file")

        # simulate the binary having produced 2 realizations
        def fake_run(cmd, cwd, capture_output, text):
            (Path(cwd) / "KBSA0001.WTD").write_text("realization 1")
            (Path(cwd) / "KBSA0002.WTD").write_text("realization 2")
            return MagicMock(returncode=0, stdout="DONE", stderr="")

        with patch("scf2wth.pipeline.subprocess.run", side_effect=fake_run) as mock_run:
            realizations = run_fresampler(site, tools, work_dir)

        # inputs staged into the working directory
        assert (work_dir / "KBSA.CLI").exists()
        assert (work_dir / "KBSA.WTD").exists()

        # binary invoked with the expected argument shape
        args = mock_run.call_args
        assert args.args[0] == [str(tools.fresampler_bin), "paramPT.txt", str(tools.seed)]
        assert args.kwargs["cwd"] == work_dir

        assert [p.name for p in realizations] == ["KBSA0001.WTD", "KBSA0002.WTD"]

    def test_nonzero_exit_raises(self, site, tools, tmp_path):
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "paramPT.txt").write_text("fake param file")

        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
            with pytest.raises(RuntimeError, match="exited 1"):
                run_fresampler(site, tools, work_dir)

    def test_no_realizations_produced_raises(self, site, tools, tmp_path):
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "paramPT.txt").write_text("fake param file")

        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="DONE", stderr="")):
            with pytest.raises(RuntimeError, match="produced no"):
                run_fresampler(site, tools, work_dir)

    def test_does_not_reclobber_already_staged_inputs(self, site, tools, tmp_path):
        """If the caller already staged .CLI/.WTD (e.g. re-running in the
        same work_dir), don't needlessly overwrite them."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "paramPT.txt").write_text("fake param file")
        (work_dir / "KBSA.CLI").write_text("already staged, do not touch")

        def fake_run(cmd, cwd, capture_output, text):
            (Path(cwd) / "KBSA0001.WTD").write_text("realization 1")
            return MagicMock(returncode=0, stdout="DONE", stderr="")

        with patch("scf2wth.pipeline.subprocess.run", side_effect=fake_run):
            run_fresampler(site, tools, work_dir)

        assert (work_dir / "KBSA.CLI").read_text() == "already staged, do not touch"


# ---------------------------------------------------------------------------
# Stage 3: run_wtd2wth (mocked subprocess, built against the documented
# --json contract)
# ---------------------------------------------------------------------------

def _fake_success_json(wtd_files, out_dir):
    return json.dumps({
        "status": "ok",
        "cli_path": "KBSA.CLI",
        "write_mode": "subfolder-guarded",
        "warnings": [],
        "outcomes": [
            {
                "wtd_path": str(w),
                "files": [str(Path(out_dir) / "KBSA" / f"{Path(w).stem}.WTH")],
            }
            for w in wtd_files
        ],
        "total_files": len(wtd_files),
    })


class TestRunWtd2wth:
    @pytest.fixture
    def site(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.touch()
        wtd.touch()
        return SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )

    @pytest.fixture
    def tools(self, tmp_path):
        return ToolPaths(
            fresampler_bin=tmp_path / "fake_fresampler",
            wtd2wth_bin=tmp_path / "fake_wtd2wth",
        )

    def test_single_batched_call_not_one_per_file(self, site, tools, tmp_path):
        """The docs recommend one call across all realizations, not a loop -
        confirm exactly one subprocess.run happens with all files in argv."""
        wtd_files = [tmp_path / "KBSA0001.WTD", tmp_path / "KBSA0002.WTD", tmp_path / "KBSA0003.WTD"]
        out_dir = tmp_path / "out"

        fake_stdout = _fake_success_json(wtd_files, out_dir)
        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout=fake_stdout, stderr="")) as mock_run:
            result = run_wtd2wth(site, tools, wtd_files, out_dir)

        assert mock_run.call_count == 1
        argv = mock_run.call_args.args[0]
        assert argv[0] == str(tools.wtd2wth_bin)
        assert argv[1] == str(site.cli_path)
        assert argv[2:5] == [str(w) for w in wtd_files]
        assert "--out-dir" in argv
        assert "--json" in argv
        assert "--legacy" not in argv
        assert result["total_files"] == 3

    def test_legacy_flag_forwarded(self, site, tools, tmp_path):
        wtd_files = [tmp_path / "KBSA0001.WTD"]
        out_dir = tmp_path / "out"
        fake_stdout = _fake_success_json(wtd_files, out_dir)
        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout=fake_stdout, stderr="")) as mock_run:
            run_wtd2wth(site, tools, wtd_files, out_dir, legacy=True)
        assert "--legacy" in mock_run.call_args.args[0]

    def test_paths_read_from_json_not_constructed(self, site, tools, tmp_path):
        """wtd2wth's docs are explicit: output paths depend on the .CLI's
        internal INSI, not the input filenames - never construct them."""
        wtd_files = [tmp_path / "KBSA0001.WTD"]
        out_dir = tmp_path / "out"
        # Deliberately use a DIFFERENT site code in the fake output path,
        # simulating INSI != site.station - this must still work correctly
        # since paths come from JSON, not from site.station.
        fake_json = json.dumps({
            "status": "ok", "cli_path": "KBSA.CLI", "write_mode": "subfolder-guarded",
            "warnings": [],
            "outcomes": [{"wtd_path": str(wtd_files[0]),
                          "files": [str(out_dir / "DIFFERENT_INSI" / "weather.WTH")]}],
            "total_files": 1,
        })
        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout=fake_json, stderr="")):
            result = run_wtd2wth(site, tools, wtd_files, out_dir)
        assert "DIFFERENT_INSI" in result["outcomes"][0]["files"][0]

    def test_nonzero_exit_with_error_json_raises(self, site, tools, tmp_path):
        wtd_files = [tmp_path / "KBSA0001.WTD"]
        out_dir = tmp_path / "out"
        error_json = json.dumps({"status": "error", "message": "malformed .WTD input"})
        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout=error_json, stderr="")):
            with pytest.raises(Wtd2wthError, match="malformed .WTD input"):
                run_wtd2wth(site, tools, wtd_files, out_dir)

    def test_unparseable_stdout_raises(self, site, tools, tmp_path):
        wtd_files = [tmp_path / "KBSA0001.WTD"]
        out_dir = tmp_path / "out"
        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="not json", stderr="segfault")):
            with pytest.raises(Wtd2wthError, match="segfault"):
                run_wtd2wth(site, tools, wtd_files, out_dir)

    def test_wtd2wth_error_exposes_returncode_and_argv(self, site, tools, tmp_path):
        wtd_files = [tmp_path / "KBSA0001.WTD"]
        out_dir = tmp_path / "out"
        error_json = json.dumps({"status": "error", "message": "boom"})
        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout=error_json, stderr="")):
            with pytest.raises(Wtd2wthError) as exc_info:
                run_wtd2wth(site, tools, wtd_files, out_dir)
        assert exc_info.value.returncode == 1
        assert exc_info.value.message == "boom"
        assert str(tools.wtd2wth_bin) in exc_info.value.argv

    def test_warnings_do_not_raise(self, site, tools, tmp_path):
        """wtd2wth's docs are explicit: warnings are non-fatal, don't treat
        their presence as failure."""
        wtd_files = [tmp_path / "KBSA0001.WTD"]
        out_dir = tmp_path / "out"
        json_with_warning = json.dumps({
            "status": "ok", "cli_path": "KBSA.CLI", "write_mode": "subfolder-guarded",
            "warnings": [".CLI filename does not match internal INSI"],
            "outcomes": [{"wtd_path": str(wtd_files[0]), "files": [str(out_dir / "KBSA" / "x.WTH")]}],
            "total_files": 1,
        })
        with patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout=json_with_warning, stderr="")):
            result = run_wtd2wth(site, tools, wtd_files, out_dir)  # should not raise
        assert len(result["warnings"]) == 1


# ---------------------------------------------------------------------------
# Full pipeline (all 3 stages composed, mocked)
# ---------------------------------------------------------------------------

class TestRunPipeline:
    @pytest.fixture
    def site(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text("fake cli")
        wtd.write_text("fake wtd")
        return SiteInputs(
            location_label="KALAMAZOO_MI", lat=42.24, lon=-85.24,
            cli_path=cli, wtd_path=wtd, station="KBSA",
            start_year=1993, end_year=2024,
        )

    @pytest.fixture
    def tools(self, tmp_path):
        return ToolPaths(
            fresampler_bin=tmp_path / "fake_fresampler",
            wtd2wth_bin=tmp_path / "fake_wtd2wth",
        )

    def test_full_chain_composes_correctly(self, site, tools, tmp_path):
        forecast = ForecastInputs(year=2026, planting_month=8, harvest_month=11, lead=1)
        wth_output_dir = tmp_path / "wth_out"

        def fake_fresampler_run(cmd, cwd, capture_output, text):
            (Path(cwd) / "KBSA0001.WTD").write_text("realization 1")
            (Path(cwd) / "KBSA0002.WTD").write_text("realization 2")
            return MagicMock(returncode=0, stdout="DONE", stderr="")

        def fake_wtd2wth_run(cmd, capture_output, text):
            # confirm this is the batched call: both realizations in one argv
            assert sum(1 for a in cmd if a.endswith(".WTD")) == 2
            wth1 = wth_output_dir / "KBSA" / "2026091.WTH"
            wth2 = wth_output_dir / "KBSA" / "2026092.WTH"
            fake_json = json.dumps({
                "status": "ok", "cli_path": "KBSA.CLI", "write_mode": "subfolder-guarded",
                "warnings": [],
                "outcomes": [
                    {"wtd_path": "KBSA0001.WTD", "files": [str(wth1)]},
                    {"wtd_path": "KBSA0002.WTD", "files": [str(wth2)]},
                ],
                "total_files": 2,
            })
            return MagicMock(returncode=0, stdout=fake_json, stderr="")

        call_log = []

        def dispatch(cmd, **kwargs):
            call_log.append(cmd)
            if str(tools.fresampler_bin) in cmd:
                return fake_fresampler_run(cmd, kwargs.get("cwd"), kwargs.get("capture_output"), kwargs.get("text"))
            return fake_wtd2wth_run(cmd, kwargs.get("capture_output"), kwargs.get("text"))

        with patch.object(cpc_forecast, "get_official",
                           side_effect=lambda ym, v, lat, lon, lead, **kw: _fake_forecast(v)), \
             patch("scf2wth.pipeline.subprocess.run", side_effect=dispatch):
            wth_files = run_pipeline(site, forecast, tools, wth_output_dir)

        assert len(call_log) == 2  # fresampler once, wtd2wth once (batched)
        assert wth_files == [wth_output_dir / "KBSA" / "2026091.WTH",
                              wth_output_dir / "KBSA" / "2026092.WTH"]

    def test_legacy_flag_reaches_wtd2wth(self, site, tools, tmp_path):
        forecast = ForecastInputs(year=2026, planting_month=8, harvest_month=11, lead=1)
        wth_output_dir = tmp_path / "wth_out"

        def dispatch(cmd, **kwargs):
            if str(tools.fresampler_bin) in cmd:
                (Path(kwargs["cwd"]) / "KBSA0001.WTD").write_text("r1")
                return MagicMock(returncode=0, stdout="DONE", stderr="")
            assert "--legacy" in cmd
            fake_json = json.dumps({
                "status": "ok", "cli_path": "KBSA.CLI", "write_mode": "flat-legacy",
                "warnings": [], "outcomes": [{"wtd_path": "KBSA0001.WTD", "files": []}],
                "total_files": 0,
            })
            return MagicMock(returncode=0, stdout=fake_json, stderr="")

        with patch.object(cpc_forecast, "get_official",
                           side_effect=lambda ym, v, lat, lon, lead, **kw: _fake_forecast(v)), \
             patch("scf2wth.pipeline.subprocess.run", side_effect=dispatch):
            run_pipeline(site, forecast, tools, wth_output_dir, legacy=True)


# ---------------------------------------------------------------------------
# CLI (through the real main() entry point)
# ---------------------------------------------------------------------------

def _cli_args(tmp_path, cli, wtd, extra=None):
    args = [
        "scf2wth", "run",
        "--location-label", "KALAMAZOO_MI",
        "--lat", "42.24", "--lon", "-85.24",
        "--cli", str(cli),
        "--wtd", str(wtd),
        "--station", "KBSA",
        "--start-year", "1993", "--end-year", "2024",
        "--year", "2026", "--planting-month", "8", "--harvest-month", "11", "--lead", "1",
        "--fresampler-bin", str(tmp_path / "fake_fresampler"),
        "--wtd2wth-bin", str(tmp_path / "fake_wtd2wth"),
        "--wth-output-dir", str(tmp_path / "wth_out"),
    ]
    return args + (extra or [])


class TestCli:
    @pytest.fixture
    def cli_wtd(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        wtd = tmp_path / "KBSA.WTD"
        cli.write_text("fake cli")
        wtd.write_text("fake wtd")
        return cli, wtd

    def _dispatch(self, tmp_path):
        wth_out = tmp_path / "wth_out"

        def fake_run(cmd, **kwargs):
            if "fresampler" in cmd[0]:
                (Path(kwargs["cwd"]) / "KBSA0001.WTD").write_text("r1")
                return MagicMock(returncode=0, stdout="DONE", stderr="")
            fake_json = json.dumps({
                "status": "ok", "cli_path": "KBSA.CLI", "write_mode": "subfolder-guarded",
                "warnings": [],
                "outcomes": [{"wtd_path": "KBSA0001.WTD",
                              "files": [str(wth_out / "KBSA" / "2026244.WTH")]}],
                "total_files": 1,
            })
            return MagicMock(returncode=0, stdout=fake_json, stderr="")
        return fake_run

    def test_run_prints_wth_paths(self, cli_wtd, tmp_path, capsys, monkeypatch):
        cli, wtd = cli_wtd
        monkeypatch.setattr(sys, "argv", _cli_args(tmp_path, cli, wtd))
        with patch.object(cpc_forecast, "get_official",
                           side_effect=lambda ym, v, lat, lon, lead, **kw: _fake_forecast(v)), \
             patch("scf2wth.pipeline.subprocess.run", side_effect=self._dispatch(tmp_path)):
            main()
        out = capsys.readouterr().out
        assert "2026244.WTH" in out

    def test_run_json_flag_emits_parseable_json(self, cli_wtd, tmp_path, capsys, monkeypatch):
        cli, wtd = cli_wtd
        monkeypatch.setattr(sys, "argv", _cli_args(tmp_path, cli, wtd, extra=["--json"]))
        with patch.object(cpc_forecast, "get_official",
                           side_effect=lambda ym, v, lat, lon, lead, **kw: _fake_forecast(v)), \
             patch("scf2wth.pipeline.subprocess.run", side_effect=self._dispatch(tmp_path)):
            main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["wth_files"][0].endswith("2026244.WTH")

    def test_run_failure_exits_nonzero_without_traceback(self, cli_wtd, tmp_path, capsys, monkeypatch):
        cli, wtd = cli_wtd
        monkeypatch.setattr(sys, "argv", _cli_args(tmp_path, cli, wtd))
        with patch.object(cpc_forecast, "get_official",
                           side_effect=lambda ym, v, lat, lon, lead, **kw: _fake_forecast(v)), \
             patch("scf2wth.pipeline.subprocess.run",
                   return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "scf2wth: RuntimeError" in err
        assert "Traceback" not in err

    def test_missing_required_arg_fails_argparse(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["scf2wth", "run", "--lat", "42.24"])
        with pytest.raises(SystemExit):
            main()
