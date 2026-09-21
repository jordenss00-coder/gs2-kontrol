#!/usr/bin/env python3
"""QCY Watch GS2 - SMA (com.szabh.smable3) kanali istemcisi.

Saatin uygulama katmani. Jieli RCSP'den ayri; Nordic UART servisini kullanir.
Cerceve bicimi APK'daki MessageFactory.create()'ten cikarildi:

    AB | bayrak | uzunluk(2,BE) | crc16(2,BE) | komut | anahtar | anahtarBayragi | veri...

    - bayrak    : gonderirken 0x01 (create icinde her zaman |1 yapiliyor)
    - uzunluk   : veri uzunlugu + 3
    - crc16     : CRC-16/ARC (poly 0xA001, init 0), 7. bayttan (komut) sona kadar
    - komut     : BleKey'in ust bayti (UPDATE=1 SET=2 CONNECT=3 PUSH=4 DATA=5 CONTROL=6 IO=7)
    - anahtar   : BleKey'in alt bayti
    - bayrak2   : BleKeyFlag - UPDATE=0x00 READ=0x10 READ_CONTINUE=0x11 CREATE=0x20 DELETE=0x30 RESET=0x40

Kullanim:
    python sma_client.py POWER FIRMWARE_VERSION TIME
    python sma_client.py --listen 20            # sadece dinle
    python sma_client.py --all-read             # guvenli okuma anahtarlarini sirayla dene
"""
import argparse
import asyncio
import json
import re
from datetime import datetime

from bleak import BleakClient, BleakScanner

NUS_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

FLAG_UPDATE, FLAG_READ, FLAG_READ_CONTINUE = 0x00, 0x10, 0x11

# SET (2) ve DATA (5) komutlarinda okuma guvenli; yazma bayraklari kullanilmiyor.
# Bu anahtarlar saatte veri degistirmez, sadece okur.
GUVENLI_OKUMA = [
    "POWER", "FIRMWARE_VERSION", "TIME", "DEVICE_INFO", "BATTERY_USAGE",
    "BT_NAME", "STEP_GOAL", "USER_PROFILE", "ACTIVITY_REALTIME", "WATCH_FACE",
]


def crc16_arc(data: bytes) -> int:
    """MessageFactory.queryCrc16 ile ayni: yansimali tablo, init 0."""
    crc = 0
    for b in data:
        crc = ((crc >> 8) ^ _TABLE[(crc ^ b) & 0xFF]) & 0xFFFF
    return crc


def _make_table():
    tbl = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
        tbl.append(c)
    return tbl


_TABLE = _make_table()


def anahtarlari_oku(path="sma_anahtarlari.txt"):
    keys = {}
    try:
        for line in open(path, encoding="utf-8"):
            m = re.match(r"0x([0-9A-F]{4})\s+\d+\s+([A-Z_0-9]+)", line)
            if m:
                keys[m.group(2)] = int(m.group(1), 16)
    except FileNotFoundError:
        pass
    return keys


def paket_kur(key16: int, key_flag: int = FLAG_READ, data: bytes = b"") -> bytes:
    govde = bytes([key16 >> 8, key16 & 0xFF, key_flag]) + data
    crc = crc16_arc(govde)
    return bytes([0xAB, 0x01, (len(govde) >> 8) & 0xFF, len(govde) & 0xFF,
                  (crc >> 8) & 0xFF, crc & 0xFF]) + govde


def paket_coz(d: bytes):
    """Gelen SMA cercevesini ayristir."""
    if len(d) < 9 or d[0] != 0xAB:
        return None
    uz = (d[2] << 8) | d[3]
    crc = (d[4] << 8) | d[5]
    govde = d[6:6 + uz]
    return {"uzunluk": uz, "crc": crc, "crc_dogru": crc16_arc(govde) == crc,
            "komut": govde[0] if govde else None,
            "anahtar": govde[1] if len(govde) > 1 else None,
            "bayrak": govde[2] if len(govde) > 2 else None,
            "veri": govde[3:]}


def hx(b):
    return " ".join(f"{x:02X}" for x in b)



async def cihaz_bul(address=None, log=print, timeout=90.0):
    """Saati bul.

    GS2 yaklasik 45 saniyede bir tek reklam yayini yapiyor, bu yuzden kisa
    araliklarla tekrarlanan taramalar onu kaciriyor. Kesintisiz tarama ile
    reklam gorulur gorulmez cihaz nesnesi doner.
    """
    log(f"Saat araniyor (en fazla {timeout:.0f} sn, yayin araligi ~45 sn)...")
    if address:
        d = await BleakScanner.find_device_by_address(address, timeout=timeout)
    else:
        d = await BleakScanner.find_device_by_name("QCY Watch GS2", timeout=timeout)
    if d:
        log(f"Bulundu: {d.name} ({d.address})")
    return d


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="okunacak anahtar adlari, orn. POWER")
    ap.add_argument("--address", default=None)
    ap.add_argument("--listen", type=float, default=6.0, help="her komuttan sonra dinleme suresi")
    ap.add_argument("--all-read", action="store_true", help="guvenli okuma listesini dene")
    a = ap.parse_args()

    ad_key = anahtarlari_oku()
    istenen = a.keys or (GUVENLI_OKUMA if a.all_read else ["POWER"])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    kayit = []

    def log(msg=""):
        print(msg, flush=True)
        kayit.append(str(msg))

    device = await cihaz_bul(a.address, log)
    if device is None:
        log("Saat bulunamadi. Baska bir uygulamaya bagli olabilir ya da uyuyor olabilir.")
        return

    gelen = []

    def on_notify(_s, data):
        data = bytes(data)
        gelen.append(data)
        p = paket_coz(data)
        log(f"  <<< {hx(data)}")
        if p:
            log(f"      komut=0x{p['komut']:02X} anahtar=0x{p['anahtar']:02X} "
                f"bayrak=0x{p['bayrak']:02X} crc={'ok' if p['crc_dogru'] else 'HATALI'} "
                f"veri={hx(p['veri']) or '(bos)'}")

    async with BleakClient(device, timeout=25.0) as client:
        log("Baglandi")
        await client.start_notify(NUS_NOTIFY, on_notify)
        await asyncio.sleep(0.5)

        for ad in istenen:
            key = ad_key.get(ad)
            if key is None:
                log(f"{ad}: bilinmeyen anahtar, atlandi")
                continue
            pkt = paket_kur(key, FLAG_READ)
            log(f"\n--- {ad} (0x{key:04X})")
            log(f"  >>> {hx(pkt)}")
            once = len(gelen)
            try:
                await client.write_gatt_char(NUS_WRITE, pkt, response=True)
            except Exception as e:
                log(f"  yazilamadi: {e}")
                continue
            await asyncio.sleep(a.listen)
            if len(gelen) == once:
                log("  cevap yok")

        log("\nDinleme bitti.")

    with open(f"sma_{stamp}.json", "w", encoding="utf-8") as f:
        json.dump([g.hex() for g in gelen], f, indent=2)
    with open(f"sma_{stamp}.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(kayit))
    log(f"Kayit: sma_{stamp}.txt")


if __name__ == "__main__":
    asyncio.run(main())
