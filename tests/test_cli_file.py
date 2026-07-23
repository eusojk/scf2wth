"""
Tests for scf2wth.cli_file.

Includes tests built directly against realistic content mirroring the two
real, independently-sourced .CLI files this parser was verified against
during development (KBSA.CLI: LF line endings; ALLE.CLI: CRLF line
endings, and an internal INSI ("MIAL") that doesn't match its own
filename ("ALLE") - a real mismatch, not a hypothetical one).
"""

import pytest

from scf2wth.cli_file import read_cli_site_info


KBSA_STYLE_CONTENT = (
    "*CLIMATE:KBSA\n"
    "\n"
    "@ INSI      LAT     LONG  ELEV   TAV   AMP  SRAY  TMXY  TMNY  RAIY\n"
    "  KBSA    42.24   -85.24   288   9.4 -99.0  14.2  32.7 -22.6 31079\n"
    "@START  DURN  ANGA  ANGB REFHT WNDHT SOURCE\n"
    "  1993    32 -99.0 -99.0 -99.0 -99.0 open-meteo\n"
    "@ GSST  GSDU\n"
    "     1   365\n"
)

# Mirrors ALLE.CLI's real content: CRLF endings, filename/INSI mismatch
ALLE_STYLE_CONTENT = (
    "*CLIMATE : MIAL\r\n"
    "\r\n"
    "@ INSI      LAT     LONG  ELEV   TAV   AMP  SRAY  TMXY  TMNY  RAIY\r\n"
    "  MIAL   42.150  -83.567   257   8.7  26.1  13.6  13.1   4.2   957\r\n"
    "@START  DURN  ANGA  ANGB REFHT WNDHT SOURCE\r\n"
    "  1988    31  0.25  0.50 -99.0 -99.0 Calculated_from_daily_data\r\n"
)


class TestReadCliSiteInfo:
    def test_lf_line_endings(self, tmp_path):
        cli = tmp_path / "KBSA.CLI"
        cli.write_text(KBSA_STYLE_CONTENT)
        info = read_cli_site_info(cli)
        assert info.insi == "KBSA"
        assert info.lat == pytest.approx(42.24)
        assert info.lon == pytest.approx(-85.24)
        assert info.elev == pytest.approx(288.0)

    def test_crlf_line_endings(self, tmp_path):
        cli = tmp_path / "ALLE.CLI"
        cli.write_bytes(ALLE_STYLE_CONTENT.encode())
        info = read_cli_site_info(cli)
        assert info.insi == "MIAL"
        assert info.lat == pytest.approx(42.150)
        assert info.lon == pytest.approx(-83.567)

    def test_internal_insi_can_differ_from_filename(self, tmp_path):
        """The real regression case: ALLE.CLI's own content says MIAL."""
        cli = tmp_path / "ALLE.CLI"
        cli.write_bytes(ALLE_STYLE_CONTENT.encode())
        info = read_cli_site_info(cli)
        assert cli.stem != info.insi

    def test_missing_header_raises(self, tmp_path):
        cli = tmp_path / "BAD.CLI"
        cli.write_text("not a real .CLI file\njust some text\n")
        with pytest.raises(ValueError, match="could not find"):
            read_cli_site_info(cli)

    def test_header_with_no_following_data_line_raises(self, tmp_path):
        cli = tmp_path / "BAD.CLI"
        cli.write_text("@ INSI      LAT     LONG  ELEV\n")
        with pytest.raises(ValueError, match="no data line"):
            read_cli_site_info(cli)

    def test_unparseable_lat_raises(self, tmp_path):
        cli = tmp_path / "BAD.CLI"
        cli.write_text(
            "@ INSI      LAT     LONG  ELEV\n"
            "  BAD1    notanumber   -85.24   288\n"
        )
        with pytest.raises(ValueError, match="could not parse LAT/LONG"):
            read_cli_site_info(cli)

    def test_missing_elev_is_tolerated(self, tmp_path):
        """ELEV is a nice-to-have, not required - a header without it
        should still parse INSI/LAT/LONG successfully."""
        cli = tmp_path / "NOEL.CLI"
        cli.write_text(
            "@ INSI      LAT     LONG\n"
            "  NOEL    42.24   -85.24\n"
        )
        info = read_cli_site_info(cli)
        assert info.insi == "NOEL"
        assert info.elev is None
