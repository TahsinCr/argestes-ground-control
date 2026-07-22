"""Sunucu saati ile Jetson lokal saatini senkronize tutan (NTP/Ping) modül.

Mimari:
1. NTP protokolü ile sunucudan hassas saat bilgisi al
2. Ping/RTT ölçümü ile gecikme düzeltmesi yap
3. Offset hesapla ve `time_syncer.now()` ile düzeltilmiş zaman döndür
4. Düzenli aralıklarla (örn. 60sn) resync yap

İkincil yöntem: NTP yoksa HTTP Date header ile senkronizasyon.

TEKNOFEST 2026:
- Video frameleri sunucu zaman damgası ile kaydedilir
- Telemetri verisi ile frame eşleştirmesi için kritik
- ±1ms hassasiyet hedeflenir
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class TimeSyncer:
    """NTP/Ping tabanlı zaman senkronizasyon modülü.

    Kullanım:
        syncer = TimeSyncer(server_host="10.0.0.1")
        syncer.sync()  # blokajlı ilk senkronizasyon
        current_time = syncer.now()  # senkronize edilmiş zaman
    """

    def __init__(
        self,
        server_host: str = "pool.ntp.org",
        sync_interval: float = 60.0,
        ntp_port: int = 123,
    ):
        """
        Args:
            server_host: NTP sunucu adresi veya IP
            sync_interval: Senkronizasyon aralığı (saniye)
            ntp_port: NTP port numarası (varsayılan 123)
        """
        self.server_host = server_host
        self.sync_interval = sync_interval
        self.ntp_port = ntp_port

        # Offset: sunucu_zamani = lokal_zaman + offset
        self._offset = 0.0
        self._last_sync_time = 0.0
        self._rtt_ms = 0.0  # Son RTT (ms)
        self._synced = False

        logger.info("TimeSyncer: server=%s interval=%.1fs", server_host, sync_interval)

    def _ntp_request(self) -> Optional[Tuple[float, float]]:
        """NTP sunucusuna sorgu gönder ve yanıt al.

        Returns:
            (sunucu_zamani, rtt_saniye) veya None
        """
        try:
            import ntplib
            ntp_client = ntplib.NTPClient()
            response = ntp_client.request(
                self.server_host,
                version=3,
                port=self.ntp_port,
                timeout=5.0,
            )
            # NTP timestamp (1900 epoch -> Unix epoch)
            server_time = response.tx_time
            # NTP RTT zaten hesaplanmış
            rtt = response.delay / 1000.0  # ms -> saniye
            return (server_time, rtt)

        except ImportError:
            logger.warning("TimeSyncer: ntplib yuklu degil, fallback HTTP kullanilacak")
            return None
        except Exception as e:
            logger.warning("TimeSyncer: NTP hatasi: %s", e)
            return None

    def _http_sync(self) -> Optional[Tuple[float, float]]:
        """HTTP Date header ile zaman senkronizasyonu (fallback).

        Returns:
            (sunucu_zamani, rtt_saniye) veya None
        """
        try:
            import requests
            t1 = time.time()
            resp = requests.get(f"http://{self.server_host}", timeout=5.0)
            t4 = time.time()

            if resp.status_code != 200:
                logger.warning("TimeSyncer: HTTP sync basarisiz, status=%d", resp.status_code)
                return None

            # Date header'dan zamanı al
            date_str = resp.headers.get("Date")
            if not date_str:
                return None

            # HTTP time GMT
            from email.utils import parsedate_to_datetime
            server_time = parsedate_to_datetime(date_str).timestamp()
            rtt = t4 - t1

            # Tek yönlü gecikme = RTT / 2
            one_way_delay = rtt / 2.0
            # t2 ≈ t1 + one_way_delay (server'a varış)
            # server_time, t2 anındaki sunucu zamanı
            # t3 ≈ t4 - one_way_delay (server'dan çıkış)
            # Ortalama offset
            offset = (server_time - t1 - one_way_delay + server_time - t4 + one_way_delay) / 2.0
            offset = server_time - (t1 + t4) / 2.0

            return (server_time + offset, rtt)

        except Exception as e:
            logger.warning("TimeSyncer: HTTP sync hatasi: %s", e)
            return None

    def sync(self) -> float:
        """Zaman senkronizasyonu yap.

        İlk olarak NTP dene, başarısız olursa HTTP fallback kullan.
        İkisi de başarısız olursa lokal saat kullan.

        Returns:
            Senkronize edilmiş Unix timestamp (saniye)
        """
        result = None

        # Yöntem 1: NTP
        if result is None:
            result = self._ntp_request()

        # Yöntem 2: HTTP fallback
        if result is None:
            result = self._http_sync()

        if result is not None:
            server_time, rtt = result
            local_time = time.time()
            self._offset = server_time - local_time
            self._rtt_ms = rtt * 1000.0
            self._last_sync_time = local_time
            self._synced = True

            logger.info("TimeSyncer: senkronize offset=%.2fms rtt=%.1fms",
                        self._offset * 1000, self._rtt_ms)
        else:
            # Hiçbir yöntem başarılı değil
            self._offset = 0.0
            self._synced = False
            logger.warning("TimeSyncer: senkronizasyon basarisiz, lokal saat kullaniliyor")

        return self.now()

    def now(self) -> float:
        """Senkronize edilmiş anlık zamanı döndür.

        Returns:
            Unix timestamp (saniye)
        """
        local = time.time()

        # Sync interval aşıldıysa yeniden senkronize et
        if self._synced and local - self._last_sync_time > self.sync_interval:
            logger.debug("TimeSyncer: sync interval asildi, yeniden senkronize...")
            # Non-bloking sync (arka planda yapılabilir)
            # Bu örnekte senkron yapıyoruz
            self.sync()

        return local + self._offset

    def get_offset_ms(self) -> float:
        """Offset değerini milisaniye cinsinden döndür."""
        return self._offset * 1000.0

    def get_rtt_ms(self) -> float:
        """Son RTT değerini milisaniye cinsinden döndür."""
        return self._rtt_ms

    @property
    def is_synced(self) -> bool:
        return self._synced