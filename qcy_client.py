#!/usr/bin/env python3
"""QCY Watch GS2 - RCSP istemcisi ve komut tarayicisi.

Kullanim:
    python qcy_client.py sweep            # butun okuma komutlarini sirayla dene
    python qcy_client.py cmd C1 00000001  # tek komut gonder

Sadece okuma komutlari gonderilir; yikici komutlar BLOCKED listesiyle engellidir.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner

from jieli_cipher import get_encrypted_auth_data, get_random_auth_data

RCSP_WRITE = "0000ae01-0000-1000-8000-00805f9b34fb"
RCSP_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"
NUS_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# Saati bozabilecek komutlar - asla gonderilmez
BLOCKED = {0x06, 0x08, 0x16, 0x17, 0x18, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F,
           0x22, 0x23, 0x28, 0xC0, 0xC3, 0xC4, 0xD1, 0xD8,
           0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8}


def hx(b):
    return " ".join(f"{x:02X}" for x in b)


class GS2:
    def __init__(self, log=print):
        self.log = log
        self.client = None
        self.sn = 1
        self.waiters = []          # (eslesme fonksiyonu, future)
        self.traffic = []          # butun ham trafik

    # ---------------------------------------------------------- baglanti
    async def connect(self, address=None, name="QCY Watch GS2", timeout=15.0,
                      arama=60.0, denemeler=6):
        """Saate baglan.

        GS2 seyrek reklam yayinliyor ve Windows bazen GATT onbelleginden eksik
        servis listesi donduruyor. Bu yuzden: bul -> baglan -> servisleri dogrula,
        olmazsa bastan dene.
        """
        for deneme in range(1, denemeler + 1):
            device = None
            if address:
                device = await BleakScanner.find_device_by_address(address, timeout=arama)
            else:
                device = await BleakScanner.find_device_by_name(name, timeout=arama)
            if device is None:
                self.log(f"  deneme {deneme}: saat yayin yapmadi")
                continue

            client = BleakClient(device, timeout=timeout)
            try:
                await client.connect()
            except Exception as e:
                self.log(f"  deneme {deneme}: baglanti hatasi ({type(e).__name__})")
                continue

            uuids = {c.uuid.lower() for s in client.services for c in s.characteristics}
            if RCSP_WRITE not in uuids or RCSP_NOTIFY not in uuids:
                self.log(f"  deneme {deneme}: servis listesi eksik ({len(uuids)} karakteristik), tekrar")
                await client.disconnect()
                continue

            self.client = client
            self.log(f"Baglandi: {device.address}")
            await self.client.start_notify(RCSP_NOTIFY, self._on_rcsp)
            if NUS_NOTIFY in uuids:
                try:
                    await self.client.start_notify(NUS_NOTIFY, self._on_nus)
                except Exception as e:
                    self.log(f"NUS bildirimi acilamadi: {e}")
            await asyncio.sleep(0.3)
            return

        raise RuntimeError("Saate baglanilamadi (yayin yok ya da baska bir merkeze bagli)")

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    # ---------------------------------------------------------- bildirimler
    def _record(self, kanal, yon, data):
        self.traffic.append({"t": datetime.now().isoformat(timespec="milliseconds"),
                             "kanal": kanal, "yon": yon, "hex": bytes(data).hex()})

    def _on_rcsp(self, _sender, data):
        data = bytes(data)
        self._record("AE02", "rx", data)
        self.log(f"  <<< {hx(data)}")

        # Saat de bize komut gonderiyor. Bayrak 0xC0 = istek + cevap bekliyor.
        # Cevapsiz kalan istekler oturumu iptal ettiriyor, bu yuzden onayliyoruz.
        if len(data) >= 10 and data[:3] == bytes([0xFE, 0xDC, 0xBA]) and data[3] == 0xC0:
            asyncio.create_task(self._ack(data[4], data[7]))
        for w in list(self.waiters):
            if w[0](data):
                self.waiters.remove(w)
                if not w[1].done():
                    w[1].set_result(data)
                break

    def _on_nus(self, _sender, data):
        data = bytes(data)
        self._record("NUS", "rx", data)
        self.log(f"  <<< [SMA] {hx(data)}")

    async def _ack(self, op, sn, status=0):
        """Saatin gonderdigi istege cevap ver: bayrak 0x00, durum + SN."""
        frame = bytes([0xFE, 0xDC, 0xBA, 0x00, op, 0x00, 0x02, status, sn, 0xEF])
        try:
            await self.write_rcsp(frame)
        except Exception as e:
            self.log(f"  onay gonderilemedi: {e}")

    async def _wait(self, pred, timeout=4.0):
        fut = asyncio.get_running_loop().create_future()
        entry = (pred, fut)
        self.waiters.append(entry)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            if entry in self.waiters:
                self.waiters.remove(entry)
            return None

    async def write_rcsp(self, data):
        self._record("AE01", "tx", data)
        self.log(f"  >>> {hx(data)}")
        await self.client.write_gatt_char(RCSP_WRITE, data, response=False)

    # ---------------------------------------------------------- dogrulama
    async def auth(self):
        self.log("Kimlik dogrulama...")
        challenge = get_random_auth_data()
        task = asyncio.create_task(self._wait(lambda d: len(d) == 17 and d[0] == 0x01))
        await self.write_rcsp(b"\x00" + challenge)
        reply = await task
        if reply is None:
            raise RuntimeError("Saat ilk sinamaya cevap vermedi")
        if reply[1:] != get_encrypted_auth_data(challenge):
            self.log("  UYARI: saatin cevabi beklenenden farkli")

        task = asyncio.create_task(self._wait(lambda d: len(d) == 17 and d[0] == 0x00))
        await self.write_rcsp(b"\x02pass")
        chal = await task
        if chal is None:
            raise RuntimeError("Saat kendi sinamasini gondermedi")

        task = asyncio.create_task(self._wait(lambda d: len(d) >= 5 and d[:5] == b"\x02pass"))
        await self.write_rcsp(b"\x01" + get_encrypted_auth_data(chal[1:]))
        if await task is None:
            raise RuntimeError("Saat 'pass' onayi gondermedi")
        self.log("Dogrulama basarili")

    # ---------------------------------------------------------- komut
    async def cmd(self, op, params=b"", timeout=4.0, flag=0xC0):
        if op in BLOCKED:
            raise ValueError(f"0x{op:02X} yikici komut listesinde, gonderilmedi")
        sn = self.sn
        self.sn = (self.sn + 1) & 0xFF
        body = bytes([sn]) + bytes(params)
        frame = bytes([0xFE, 0xDC, 0xBA, flag, op, len(body) >> 8, len(body) & 0xFF]) + body + b"\xEF"

        def match(d):
            return len(d) >= 10 and d[:3] == b"\xFE\xDC\xBA" and d[4] == op and d[8] == sn

        task = asyncio.create_task(self._wait(match, timeout))
        await self.write_rcsp(frame)
        resp = await task
        if resp is None:
            return None
        body_len = (resp[5] << 8) | resp[6]
        return {"status": resp[7], "data": resp[9:7 + body_len], "raw": resp}


def parse_tlv(data):
    """[uzunluk][tip][deger] listesi"""
    out, i = [], 0
    while i < len(data):
        ln = data[i]
        if ln == 0 or i + 1 + ln > len(data):
            break
        out.append((data[i + 1], data[i + 2:i + 1 + ln]))
        i += 1 + ln
    return out


def path_data(path=(0,), tip=0, adet=20, baslangic=1, dev_handler=0):
    """StartFileBrowseParam icin PathData.toData() ciktisini uretir.

    Bicim (APK: com.jieli.jl_filebrowse.bean.PathData.toData):
        tip(1) | okunacakAdet(1) | baslangicIndeks(2,BE) | cihazTutamaci(4,BE) | yol(4*n,BE)
    tip 0 = klasor, 1 = dosya. Kok klasor icin yol = [0].
    """
    out = bytes([tip & 0xFF, adet & 0xFF]) + baslangic.to_bytes(2, "big") + dev_handler.to_bytes(4, "big")
    for p in path:
        out += p.to_bytes(4, "big")
    return out


async def browse(dev, log, path=(0,), dinle=20.0):
    """Klasor listele: START_FILE_BROWSE (0x0C) gonder, gelen ogeleri dinle, STOP (0x0D)."""
    log(f"Klasor listeleniyor: yol={path}")
    r = await dev.cmd(0x0C, path_data(path), timeout=6.0)
    log(f"START_FILE_BROWSE sonucu: {r}")
    if r is None or r["status"] != 0:
        log("Saat gezinmeyi kabul etmedi.")
        return

    log(f"{dinle:.0f} saniye boyunca gelen ogeler dinleniyor...")
    onceki = len(dev.traffic)
    await asyncio.sleep(dinle)
    yeni = [t for t in dev.traffic[onceki:] if t["yon"] == "rx"]
    log(f"Gelen cerceve sayisi: {len(yeni)}")

    # STOP_FILE_BROWSE - sebep 0
    r = await dev.cmd(0x0D, bytes([0]), timeout=4.0)
    log(f"STOP_FILE_BROWSE sonucu: {r}")


async def sweep(dev, log):
    """Butun okuma komutlarini ve parametre varyasyonlarini dene."""
    tests = []

    # 0xC1 ADV: her bit ayri ayri + hepsi
    for bit in range(8):
        tests.append((f"ADV_GET_INFO bit {bit}", 0xC1, (1 << bit).to_bytes(4, "big")))
    tests.append(("ADV_GET_INFO hepsi", 0xC1, b"\xFF\xFF\xFF\xFF"))

    # 0x07 sistem bilgisi: bilinen butun fonksiyon gruplari, tam maske
    for grp in [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x16, 0xFF]:
        tests.append((f"SYS_INFO grup 0x{grp:02X}", 0x07, bytes([grp]) + b"\xFF\xFF\xFF\xFF"))

    # diger okuma komutlari
    tests += [
        ("TARGET_FEATURE_MAP bos", 0x02, b""),
        ("TARGET_FEATURE_MAP maske", 0x02, b"\xFF\xFF\xFF\xFF"),
        ("TARGET_INFO", 0x03, bytes.fromhex("FFFFFFFF00")),
        ("EXTERNAL_FLASH_MSG 0", 0xD6, b"\x00"),
        ("EXTERNAL_FLASH_MSG maske", 0xD6, b"\xFF\xFF\xFF\xFF"),
        ("DEVICE_CONFIG_INFO 0", 0xD9, b"\x00"),
        ("DEVICE_CONFIG_INFO maske", 0xD9, b"\xFF\xFF\xFF\xFF"),
        ("DEV_MD5", 0xD4, b""),
        ("READ_ERROR_MSG", 0x29, b""),
        ("QUERY_PHONE_BT_INFO", 0x31, b""),
    ]
    # saglik verisi tipleri
    for t in range(9):
        tests.append((f"HEALTH_DATA tip {t}", 0xA0, bytes([t])))

    ozet = []
    for isim, op, params in tests:
        log(f"\n--- {isim}  (0x{op:02X} / {hx(params) or 'parametresiz'})")
        try:
            r = await dev.cmd(op, params, timeout=3.0)
        except ValueError as e:
            log(f"  atlandi: {e}")
            continue
        if r is None:
            log("  cevap yok")
            ozet.append((isim, "cevap yok", ""))
            continue
        veri = hx(r["data"])
        log(f"  status={r['status']}  veri={veri or '(bos)'}")
        if r["status"] == 0 and r["data"]:
            for tip, deger in parse_tlv(r["data"]):
                log(f"      tip 0x{tip:02X}: {hx(deger)}")
        ozet.append((isim, f"status {r['status']}", veri))
        await asyncio.sleep(0.15)

    log("\n" + "=" * 70)
    log("OZET (status 0 ve dolu veri donenler)")
    log("=" * 70)
    for isim, durum, veri in ozet:
        if durum == "status 0" and veri.replace("00", "").strip():
            log(f"  {isim:<32} {veri}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["sweep", "cmd", "browse"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--address", default=None)
    ap.add_argument("--out", default=None, help="trafigi JSON olarak kaydet")
    a = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    lines = []

    def log(msg=""):
        print(msg, flush=True)
        lines.append(str(msg))

    dev = GS2(log)
    try:
        await dev.connect(a.address)
        await dev.auth()

        if a.mode == "browse":
            yol = tuple(int(x) for x in a.args) or (0,)
            await browse(dev, log, yol)
        elif a.mode == "cmd":
            op = int(a.args[0], 16)
            params = bytes.fromhex(a.args[1]) if len(a.args) > 1 else b""
            r = await dev.cmd(op, params)
            log(f"\nSonuc: {r}")
        else:
            await sweep(dev, log)
    finally:
        await dev.disconnect()
        out = a.out or f"tarama_{stamp}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(dev.traffic, f, indent=2)
        with open(out.replace(".json", ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log(f"\nTrafik kaydedildi: {out}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(1)
