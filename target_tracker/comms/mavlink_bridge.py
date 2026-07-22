"""Pixhawk'a yönelim hız komutları (GUIDED mod manipülasyonu, limit harcamaz).

MAVLink protokolü üzerinden:
- ATTITUDE_TARGET mesajı ile yönelim hızı (roll/pitch/yaw rate) gönderimi
- SET_ATTITUDE_TARGET ile GUIDED mod kontrolü
- Heartbeat alımı ile bağlantı takibi
"""

import logging
import struct
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# MAVLink mesaj tipleri (pymavlink import olmadan kullanmak için)
# Gerçek kullanımda pymavlink kullanılmalı
MAVLINK_MSG_ID_HEARTBEAT = 0
MAVLINK_MSG_ID_SET_ATTITUDE_TARGET = 82
MAVLINK_MSG_ID_ATTITUDE_TARGET = 83

# MAVLink frame enum
MAV_FRAME_BODY_OFFSET_NED = 8


class MavlinkBridge:
    """Pixhawk ile MAVLink protokolü üzerinden haberleşme.

    UDP, TCP veya Serial bağlantı desteklenir.

    Kullanım:
        bridge = MavlinkBridge(connection_string="udp:127.0.0.1:14550")
        bridge.connect()
        bridge.send_attitude_rate(roll_rate=0.0, pitch_rate=0.0, yaw_rate=0.5, thrust=0.6)
    """

    def __init__(
        self,
        connection_string: str = "udp:127.0.0.1:14550",
        system_id: int = 1,
        component_id: int = 1,
        target_system: int = 1,
        target_component: int = 1,
        timeout_sec: float = 5.0,
    ):
        """
        Args:
            connection_string: Bağlantı adresi (udp:tcp:serial:)
            system_id: Bu sistemin ID'si
            component_id: Bu bileşenin ID'si
            target_system: Hedef (Pixhawk) sistem ID
            target_component: Hedef (Pixhawk) bileşen ID
            timeout_sec: Heartbeat timeout süresi
        """
        self.connection_string = connection_string
        self.system_id = system_id
        self.component_id = component_id
        self.target_system = target_system
        self.target_component = target_component
        self.timeout_sec = timeout_sec

        self._master = None
        self._connected = False
        self._last_heartbeat = 0.0
        self._attitude_target_seq = 0

        logger.info("MavlinkBridge: %s sys=%d comp=%d", connection_string, system_id, component_id)

    def connect(self) -> bool:
        """MAVLink bağlantısını kur.

        Returns:
            Bağlantı başarılı mı?
        """
        try:
            # Gerçek kullanımda pymavlink kullanılır
            # from pymavlink import mavutil
            # self._master = mavutil.mavlink_connection(self.connection_string)
            # self._master.wait_heartbeat(timeout=self.timeout_sec)
            # self._connected = True

            # Simülasyon modu
            logger.info("MavlinkBridge: baglanti basarili (simulasyon)")
            self._connected = True
            self._last_heartbeat = time.time()
            return True

        except Exception as e:
            logger.error("MavlinkBridge baglanti hatasi: %s", e)
            self._connected = False
            return False

    def disconnect(self):
        """MAVLink bağlantısını kapat."""
        if self._master is not None:
            # self._master.close()
            pass
        self._connected = False
        self._master = None
        logger.info("MavlinkBridge: baglanti kapatildi")

    def send_attitude_rate(
        self,
        roll_rate: float = 0.0,
        pitch_rate: float = 0.0,
        yaw_rate: float = 0.0,
        thrust: float = 0.5,
    ) -> bool:
        """SET_ATTITUDE_TARGET mesajı gönder.

        Pixhawk GUIDED modunda bu komutları alır ve uygular.

        Args:
            roll_rate: Roll açısal hızı (rad/sn)
            pitch_rate: Pitch açısal hızı (rad/sn)
            yaw_rate: Yaw açısal hızı (rad/sn)
            thrust: İtki (0.0 - 1.0, normalize)

        Returns:
            Gönderim başarılı mı?
        """
        if not self._connected:
            logger.warning("MavlinkBridge: baglanti yok, komut gonderilemedi")
            return False

        try:
            # Gerçek implementasyon:
            # self._master.mav.set_attitude_target_send(
            #     time.time() * 1000,  # time_boot_ms
            #     self.target_system,
            #     self.target_component,
            #     0b111111,  # type_mask: tüm rate'leri aktif et
            #     [0.0, 0.0, 0.0],  # q (quaternion, kullanılmıyorsa boş)
            #     roll_rate, pitch_rate, yaw_rate,
            #     thrust,
            # )

            self._attitude_target_seq += 1
            logger.debug(
                "ATTITUDE_TARGET [%d]: roll=%.2f pitch=%.2f yaw=%.2f thrust=%.2f",
                self._attitude_target_seq, roll_rate, pitch_rate, yaw_rate, thrust,
            )
            return True

        except Exception as e:
            logger.error("MavlinkBridge: komut gonderim hatasi: %s", e)
            return False

    def send_attitude_quaternion(self, q: Tuple[float, float, float, float], thrust: float = 0.5) -> bool:
        """Quaternion bazlı hedef yönelimi gönder.

        Args:
            q: (w, x, y, z) quaternion
            thrust: İtki (0.0 - 1.0)

        Returns:
            Gönderim başarılı mı?
        """
        if not self._connected:
            return False

        try:
            # self._master.mav.set_attitude_target_send(
            #     int(time.time() * 1000),
            #     self.target_system,
            #     self.target_component,
            #     0b111111,
            #     list(q),  # quaternion
            #     0.0, 0.0, 0.0,  # rate'ler (quaternion mode'da kullanılmaz)
            #     thrust,
            # )
            logger.debug("QUATERNION: w=%.3f x=%.3f y=%.3f z=%.3f thrust=%.2f", *q, thrust)
            return True

        except Exception as e:
            logger.error("MavlinkBridge: quaternion gonderim hatasi: %s", e)
            return False

    def is_connected(self) -> bool:
        """Bağlantı durumunu kontrol et.

        Returns:
            Bağlantı aktif mi?
        """
        if not self._connected:
            return False

        # Heartbeat timeout kontrolü
        if time.time() - self._last_heartbeat > self.timeout_sec:
            logger.warning("MavlinkBridge: heartbeat timeout")
            self._connected = False
            return False

        return True

    def get_attitude(self) -> Optional[Dict[str, float]]:
        """Pixhawk'dan anlık yönelim bilgisini al.

        Returns:
            {"roll": float, "pitch": float, "yaw": float} veya None
        """
        if not self._connected:
            return None

        # Gerçek kullanımda ATTITUDE mesajı okunur
        # msg = self._master.recv_match(type='ATTITUDE', blocking=False)
        # if msg:
        #     return {"roll": msg.roll, "pitch": msg.pitch, "yaw": msg.yaw}

        # Simülasyon
        return {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}

    def arm_disarm(self, arm: bool = True) -> bool:
        """Motor arm/disarm komutu gönder.

        Args:
            arm: True = arm, False = disarm

        Returns:
            Başarılı mı?
        """
        if not self._connected:
            return False

        try:
            # self._master.mav.command_long_send(
            #     self.target_system,
            #     self.target_component,
            #     mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            #     0,
            #     1 if arm else 0,
            #     0, 0, 0, 0, 0, 0,
            # )
            logger.info("MavlinkBridge: ARM=%s", arm)
            return True

        except Exception as e:
            logger.error("MavlinkBridge: arm/disarm hatasi: %s", e)
            return False

    def set_mode(self, mode: str = "GUIDED") -> bool:
        """Uçuş modu değiştir.

        Args:
            mode: "GUIDED", "STABILIZE", "LOITER", "LAND", "RTL"

        Returns:
            Başarılı mı?
        """
        if not self._connected:
            return False

        mode_map = {
            "STABILIZE": 0,
            "GUIDED": 4,
            "LOITER": 5,
            "RTL": 6,
            "LAND": 9,
        }

        mode_id = mode_map.get(mode.upper())
        if mode_id is None:
            logger.error("MavlinkBridge: gecersiz mod: %s", mode)
            return False

        try:
            # self._master.mav.set_mode_send(
            #     self.target_system,
            #     mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            #     mode_id,
            # )
            logger.info("MavlinkBridge: mode -> %s (%d)", mode, mode_id)
            return True

        except Exception as e:
            logger.error("MavlinkBridge: mode degistirme hatasi: %s", e)
            return False