# Orin MAVLink uygulama kanalı

Fiziksel yol şudur:

`YKI -> RFD -> Pixhawk MAVLink router -> Orin`

YKI, Orin seri portunu ayrıca açmaz. Telemetriyi okuyan mevcut `MavlinkClient`
tek okuyucu olarak kalır. Orin paketleri aynı bağlantıdaki hedefli
`V2_EXTENSION` mesajlarıdır:

- uygulama ağ kimliği: `77`
- yerel mesaj tipi: `42000`
- YKI: system `255`, component `0`
- Pixhawk ve Orin: aynı system ID (varsayılan `1`)
- Orin component ID: `191` (`MAV_COMP_ID_ONBOARD_COMPUTER`)

Orin standart MAVLink heartbeat'ini component `191` olarak gönderir. Böylece
Pixhawk hedefli paketlerin hangi telemetri portuna yönlendirileceğini öğrenir.
Uygulama sağlık heartbeat'i varsayılan olarak 5 saniyede bir, YKI zaman aşımı
ise 12 saniyedir.

## Orin'de çalıştırma

```bash
python3 -m pip install "pymavlink>=2.4.49,<3"
python3 orin_mavlink_agent.py \
  --config config.json
```

Bu kurulumda kalıcı Orin yolu `/home/kirlangic/kirlangic-orin-link`, endpoint
`/dev/ttyTHS1` ve baud `57600` olarak yapılandırılmıştır. Config kontrolü:

```bash
python3 orin_mavlink_agent.py --config config.json --check-config
```

Seri portu aynı anda yalnızca bir süreç açmalıdır. Orin'de başka bir süreç
Pixhawk portunun sahibiyse bu ajanı aynı porta bağlamak yerine `mavlink-router`
üzerinde bu ajana ayrılmış bir UDP çıkışı kullanın.

Pixhawk'ın Orin'e bağlı `SERIALx_PROTOCOL` değeri MAVLink2, baud değeri de
scriptteki baud ile aynı olmalıdır. RFD/YKI hattı zaten Pixhawk telemetri portu
olarak çalışmaya devam eder.

## Yeni paket ve görev ekleme

`OrinMavlinkAgent.register_handler()` yeni bir çift yönlü paket tipi ekler.
Uzun sürebilecek işleyiciler okuyucu döngüsünden ayrı worker üzerinde çalışır.

```python
def camera_control(packet):
    mode = str(packet.payload.get("mode", "track"))
    camera.set_mode(mode)
    return True, "camera mode changed", {"mode": mode}

agent.register_handler("camera.control", camera_control)
```

Görev listesindeki bir işi gerçek algoritmaya bağlamak için görev işleyicisi
kaydedilir:

```python
def start_kamikaze(parameters):
    kamikaze_controller.start(parameters)
    return True, "kamikaze controller started", {"execution": "running"}

agent.register_task_handler("KAMIKAZE", start_kamikaze)
```

Geçici script eşlemesi için `config.json` içindeki `commands` nesnesine doğrudan
`"KOMUT": "script"` kaydı eklenebilir:

```json
{
  "commands": {
    "PING_TEST": "orin_commands/ping_test.py",
    "KAMIKAZE": "orin_commands/start_kamikaze.py"
  }
}
```

Komutlar shell açılmadan argüman listesi olarak çalıştırılır. Parametre JSON'u
scriptin stdin akışına ve `ORIN_PAYLOAD_JSON` ortam değişkenine verilir. Config
değişikliğinden sonra servis yeniden başlatılır:

```bash
sudo systemctl restart kirlangic-orin-link.service
```

Boot servisini kurmak veya kaldırmak için Orin'de:

```bash
cd /home/kirlangic/kirlangic-orin-link
./install_orin_autostart.sh
./remove_orin_autostart.sh
```

Kaldırma scripti yalnızca systemd otomatik başlangıcını kaldırır; config ve
komut dosyalarını silmez.

İşleyici kaydedilmemiş bir görev yine Orin'in `active_task` koordinasyon
durumunu değiştirir, ancak ACK içinde `execution: state_only` döner. Gerçek
algoritma bağlı olduğunda işleyici kendi çalışma durumunu döndürmelidir.

YKI tarafında UI dışındaki bir istemci de aynı backend yüzeyini kullanabilir:

```python
receipt = runtime.facade.issue_orin("camera.control", {"mode": "track"})
subscription = runtime.facade.subscribe_orin(on_orin_message)
```
