from __future__ import annotations

import math
import unittest

from fake_teknofest_server import (
    EARTH_RADIUS_M,
    HSS_MAX_CENTER_DISTANCE_M,
    HSS_MIN_CENTER_DISTANCE_M,
    FakeTeknofestState,
    ANSI_RED,
    ANSI_RESET,
    console_log_message,
    create_server,
    log_message,
)


class FakeTeknofestHssTests(unittest.TestCase):
    def _state(self, hss_seed: int) -> FakeTeknofestState:
        return FakeTeknofestState(
            username="test",
            password="test",
            own_team_number=1,
            competitor_count=27,
            seed=2026,
            hss_seed=hss_seed,
            quiet=True,
        )

    def test_hss_locations_stay_fixed_after_anchor_changes(self) -> None:
        state = self._state(10)
        self._prime_hss(state, *state.initial_anchor[:2])
        first = state.hss_payload()["hss_koordinat_bilgileri"]
        state.anchor = (-35.3633, 149.1652, 610.0)
        second = state.hss_payload()["hss_koordinat_bilgileri"]
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_hss_locations_are_randomized_per_server_state(self) -> None:
        first_state = self._state(10)
        second_state = self._state(11)
        self._prime_hss(first_state, *first_state.initial_anchor[:2])
        self._prime_hss(second_state, *second_state.initial_anchor[:2])
        first = first_state.hss_payload()["hss_koordinat_bilgileri"]
        second = second_state.hss_payload()["hss_koordinat_bilgileri"]
        self.assertNotEqual(first, second)

    def test_hss_locations_remain_close_to_starting_center(self) -> None:
        state = self._state(12)
        latitude, longitude, _altitude = state.initial_anchor
        self._prime_hss(state, latitude, longitude)
        longitude_scale = max(0.05, math.cos(math.radians(latitude)))
        for zone in state.hss_payload()["hss_koordinat_bilgileri"]:
            north_m = math.radians(float(zone["hssEnlem"]) - latitude) * EARTH_RADIUS_M
            east_m = (
                math.radians(float(zone["hssBoylam"]) - longitude)
                * EARTH_RADIUS_M
                * longitude_scale
            )
            distance_m = math.hypot(north_m, east_m)
            self.assertGreaterEqual(distance_m, HSS_MIN_CENTER_DISTANCE_M - 0.2)
            self.assertLessEqual(distance_m, HSS_MAX_CENTER_DISTANCE_M + 0.2)

    def test_hss_is_not_generated_before_first_valid_telemetry(self) -> None:
        state = self._state(14)

        self.assertEqual(
            state.hss_payload()["hss_koordinat_bilgileri"],
            [],
        )

    def test_first_valid_telemetry_anchors_hss_near_ownship_once(self) -> None:
        state = self._state(13)
        first_center = (40.225611, 28.8749911)

        status, _response = state.telemetry_response(
            self._telemetry_payload(*first_center)
        )

        self.assertEqual(status, 200)
        first_zones = state.hss_payload()["hss_koordinat_bilgileri"]
        self._assert_zones_near(first_zones, *first_center)

        state.last_telemetry_at.clear()
        status, _response = state.telemetry_response(
            self._telemetry_payload(-35.3633, 149.1652)
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            state.hss_payload()["hss_koordinat_bilgileri"],
            first_zones,
        )

    @staticmethod
    def _telemetry_payload(latitude: float, longitude: float) -> dict:
        return {
            "takim_numarasi": 1,
            "iha_enlem": latitude,
            "iha_boylam": longitude,
            "iha_irtifa": 100.0,
            "iha_dikilme": 0.0,
            "iha_yonelme": 90.0,
            "iha_yatis": 0.0,
            "iha_hiz": 20.0,
            "iha_batarya": 80,
            "iha_otonom": 0,
            "iha_kilitlenme": 0,
            "hedef_merkez_X": 0,
            "hedef_merkez_Y": 0,
            "hedef_genislik": 0,
            "hedef_yukseklik": 0,
            "gps_saati": {
                "saat": 12,
                "dakika": 0,
                "saniye": 0,
                "milisaniye": 0,
            },
        }

    def _prime_hss(
        self,
        state: FakeTeknofestState,
        latitude: float,
        longitude: float,
    ) -> None:
        status, _response = state.telemetry_response(
            self._telemetry_payload(latitude, longitude)
        )
        self.assertEqual(status, 200)

    def _assert_zones_near(
        self,
        zones: list[dict[str, int | float]],
        latitude: float,
        longitude: float,
    ) -> None:
        longitude_scale = max(0.05, math.cos(math.radians(latitude)))
        for zone in zones:
            north_m = (
                math.radians(float(zone["hssEnlem"]) - latitude)
                * EARTH_RADIUS_M
            )
            east_m = (
                math.radians(float(zone["hssBoylam"]) - longitude)
                * EARTH_RADIUS_M
                * longitude_scale
            )
            distance_m = math.hypot(north_m, east_m)
            self.assertGreaterEqual(
                distance_m,
                HSS_MIN_CENTER_DISTANCE_M - 0.2,
            )
            self.assertLessEqual(
                distance_m,
                HSS_MAX_CENTER_DISTANCE_M + 0.2,
            )


class FakeTeknofestLanguageTests(unittest.TestCase):
    def test_only_qr_and_lock_packet_logs_are_red(self) -> None:
        for key in (
            "lock_received",
            "kamikaze_received",
        ):
            message = console_log_message("tr", key)
            self.assertTrue(message.startswith(ANSI_RED), key)
            self.assertTrue(message.endswith(ANSI_RESET), key)

        for key in (
            "server_ready",
            "control_time_request",
            "reference_qr_request",
            "reference_hss_request",
            "login_received",
            "telemetry_received",
            "lock_accepted",
            "kamikaze_accepted",
        ):
            message = console_log_message("tr", key)
            self.assertNotIn(ANSI_RED, message, key)
            self.assertNotIn(ANSI_RESET, message, key)

    def test_console_language_defaults_to_english(self) -> None:
        server = create_server(port=0, quiet=True)
        self.addCleanup(server.server_close)

        self.assertEqual(server.state.language, "en")
        self.assertIn("Server ready", log_message("en", "server_ready"))

    def test_turkish_console_language_can_be_selected(self) -> None:
        server = create_server(port=0, quiet=True, language="tr")
        self.addCleanup(server.server_close)

        self.assertEqual(server.state.language, "tr")
        self.assertIn("Sunucu hazır", log_message("tr", "server_ready"))

    def test_unknown_console_language_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported console language"):
            create_server(port=0, quiet=True, language="de")


if __name__ == "__main__":
    unittest.main()
