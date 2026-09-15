#!/usr/bin/env python3
"""
QCY Watch GS2 - BLE Keşif ve Sonda Aracı
=========================================

Ne yapar:
  1. Yakındaki BLE cihazlarını tarar, QCY saatini bulur
  2. Bağlanır ve tüm GATT servis/karakteristik haritasını çıkarır
  3. Bildirim (notify) veren bütün karakteristikleri dinler
  4. Jieli RCSP servisine (AE00/AE01/AE02) deneme komutları gönderip
     hangi çerçeve biçiminin cevap ürettiğini bulmaya çalışır
  5. Her şeyi zaman damgalı bir log dosyasına ve bir JSON haritasına yazar

Kurulum:
    pip install bleak

Çalıştırma:
    python qcy_tool.py                # tara, bağlan, dinle, sonda gönder
    python qcy_tool.py --no-probe     # hiçbir şey gönderme, sadece dinle
    python qcy_tool.py --listen 30    # pasif dinleme süresini uzat
    python qcy_tool.py --address XX   # cihazı adresiyle seç

ÖNEMLİ: Test sırasında telefondaki QCY uygulamasını kapat. Saat aynı anda
tek bir merkeze bağlanabiliyor olabilir. En temizi telefonun Bluetooth'unu
tamamen kapatmak.

Gönderilen sonda komutları okuma amaçlıdır (cihaz bilgisi sorgular).
Yazma, silme, biçimlendirme veya bağlantı kesme komutu GÖNDERİLMEZ.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("HATA: 'bleak' kurulu değil.\n\nŞunu çalıştır:\n    pip install bleak\n")
    sys.exit(1)


# ---------------------------------------------------------------- sabitler

# Jieli RCSP servisi - GS2'de bu var
RCSP_SERVICE = "0000ae00-0000-1000-8000-00805f9b34fb"
RCSP_WRITE   = "0000ae01-0000-1000-8000-00805f9b34fb"   # Write Without Response
RCSP_NOTIFY  = "0000ae02-0000-1000-8000-00805f9b34fb"   # Notify

# Nordic UART - SMA uygulama protokolü
NUS_SERVICE  = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_WRITE    = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY   = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# APK'dan çıkarılan RCSP komut kodları (com.jieli.jl_rcsp.constant.Command)
# Sadece OKUMA komutları listelendi - bilinçli olarak tehlikeli olanlar yok.
RCSP_READ_COMMANDS = {
    0x02: "CMD_GET_TARGET_FEATURE_MAP",
    0x03: "CMD_GET_TARGET_INFO",
    0x07: "CMD_GET_SYS_INFO",
    0xD6: "CMD_GET_EXTERNAL_FLASH_MSG",
    0xD9: "CMD_GET_DEVICE_CONFIG_INFO",
}

# APK içinde bulunan gerçek bir RCSP çerçevesi:
#     FE DC BA  C0  06  00 02  00 01  EF
#     |--head--|typ|op |--len-|-data-|end
# Bu, aşağıdaki yapıyı doğruluyor. type baytı kesin bilinmiyor,
# bu yüzden birkaç aday deneniyor.
RCSP_HEAD = bytes([0xFE, 0xDC, 0xBA])
RCSP_TAIL = bytes([0xEF])
TYPE_CANDIDATES = [0xC0, 0x80, 0x00, 0x40]


def build_rcsp(type_byte: int, opcode: int, payload: bytes = b"") -> bytes:
    """RCSP çerçevesi kur: head + type + opcode + uzunluk(2, big endian) + veri + tail"""
    return (
        RCSP_HEAD
        + bytes([type_byte & 0xFF, opcode & 0xFF])
        + len(payload).to_bytes(2, "big")
        + payload
        + RCSP_TAIL
    )


# ---------------------------------------------------------------- log

class Log:
    def __init__(self, path):
        self.path = path
        self.fh = open(path, "w", encoding="utf-8")

    def __call__(self, msg=""):
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{stamp}] {msg}" if msg else ""
        print(line)
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()


def hexdump(data: bytes, width: int = 16) -> str:
    """Klasik hex + ASCII dökümü"""
    out = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexs = " ".join(f"{b:02X}" for b in chunk).ljust(width * 3 - 1)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"    {i:04X}  {hexs}  |{text}|")
    return "\n".join(out)


# ---------------------------------------------------------------- tarama

async def find_watch(log, name_hint, address):
    log("BLE taraması başlıyor (12 saniye)...")
    log("Saati bilgisayarın yanına koy.")
    log()

    found = await BleakScanner.discover(timeout=12.0)

    if not found:
        log("Hiç BLE cihazı bulunamadı.")
        log("Bilgisayarda Bluetooth adaptörü var mı ve açık mı kontrol et.")
        return None

    named = [d for d in found if d.name]
    log(f"{len(found)} cihaz bulundu ({len(named)} tanesi isimli):")
    log()
    for i, d in enumerate(found):
        mark = ""
        if d.name and name_hint.lower() in d.name.lower():
            mark = "   <-- ARADIĞIMIZ BU"
        log(f"  [{i:2d}] {d.name or '(isimsiz)':<28} {d.address}{mark}")
    log()

    if address:
        for d in found:
            if d.address.lower() == address.lower():
                return d
        log(f"'{address}' adresli cihaz bu taramada görünmedi.")
        return None

    matches = [d for d in found if d.name and name_hint.lower() in d.name.lower()]
    if len(matches) == 1:
        log(f"Eşleşme: {matches[0].name} ({matches[0].address})")
        return matches[0]
    if len(matches) > 1:
        log(f"Birden fazla eşleşme var, ilki seçiliyor: {matches[0].name}")
        return matches[0]

    log(f"İsminde '{name_hint}' geçen cihaz yok.")
    log("Saat telefona bağlıysa yayın yapmıyor olabilir - telefonun")
    log("Bluetooth'unu kapatıp tekrar dene. Ya da --address ile elle seç.")
    return None


# ---------------------------------------------------------------- GATT haritası

def map_gatt(client, log):
    log()
    log("=" * 64)
    log("GATT HARİTASI")
    log("=" * 64)

    tree = []
    for service in client.services:
        log()
        log(f"SERVIS  {service.uuid}")
        if service.description:
            log(f"        {service.description}")
        svc = {"uuid": service.uuid, "description": service.description, "characteristics": []}

        for char in service.characteristics:
            props = ",".join(char.properties)
            log(f"  KAR.  {char.uuid}")
            log(f"        özellikler: {props}")
            svc["characteristics"].append({
                "uuid": char.uuid,
                "properties": list(char.properties),
                "description": char.description,
            })
        tree.append(svc)

    return tree


def classify(tree, log):
    """Hangi platformlar var, özetle"""
    uuids = {c["uuid"].lower() for s in tree for c in s["characteristics"]}
    uuids |= {s["uuid"].lower() for s in tree}

    log()
    log("=" * 64)
    log("PLATFORM TESPİTİ")
    log("=" * 64)

    has_rcsp = RCSP_SERVICE in uuids
    has_nus = NUS_SERVICE in uuids

    log(f"  Jieli RCSP (AE00)        : {'VAR' if has_rcsp else 'yok'}")
    log(f"  Nordic UART / SMA        : {'VAR' if has_nus else 'yok'}")

    if has_rcsp and has_nus:
        log()
        log("  -> Beklenen GS2 yapısı. Çip Jieli, uygulama katmanı SMA.")
    elif has_rcsp:
        log()
        log("  -> Sadece Jieli RCSP. Jieli SDK'sı doğrudan kullanılabilir.")
    elif has_nus:
        log()
        log("  -> Sadece SMA protokolü.")
    else:
        log()
        log("  -> Bilinmeyen yapı. Yukarıdaki haritayı incelemek gerek.")

    return has_rcsp, has_nus


# ---------------------------------------------------------------- ana akış

async def run(args):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = Log(f"qcy_log_{stamp}.txt")

    log("=" * 64)
    log("QCY Watch - BLE Keşif Aracı")
    log(f"Başlangıç: {datetime.now():%Y-%m-%d %H:%M:%S}")
    log("=" * 64)
    log()

    device = await find_watch(log, args.name, args.address)
    if device is None:
        log()
        log("Cihaz seçilemedi, çıkılıyor.")
        log.close()
        return

    log()
    log(f"Bağlanılıyor: {device.address}")

    # gelen bütün bildirimler burada toplanır
    inbox = []

    def make_cb(uuid):
        def cb(_sender, data: bytearray):
            data = bytes(data)
            inbox.append({"uuid": uuid, "time": datetime.now().isoformat(), "hex": data.hex()})
            log()
            log(f"<<< BİLDİRİM  {uuid}  ({len(data)} bayt)")
            log(hexdump(data))
        return cb

    try:
        async with BleakClient(device.address, timeout=25.0) as client:
            log("Bağlantı kuruldu.")

            tree = map_gatt(client, log)
            has_rcsp, has_nus = classify(tree, log)

            with open(f"qcy_gatt_{stamp}.json", "w", encoding="utf-8") as f:
                json.dump({"device": {"name": device.name, "address": device.address},
                           "services": tree}, f, indent=2, ensure_ascii=False)
            log()
            log(f"GATT haritası kaydedildi: qcy_gatt_{stamp}.json")

            # --- bildirim aboneliği
            log()
            log("=" * 64)
            log("BİLDİRİM ABONELİĞİ")
            log("=" * 64)
            subscribed = []
            for service in client.services:
                for char in service.characteristics:
                    if "notify" in char.properties or "indicate" in char.properties:
                        try:
                            await client.start_notify(char.uuid, make_cb(char.uuid))
                            subscribed.append(char.uuid)
                            log(f"  abone olundu: {char.uuid}")
                        except Exception as e:
                            log(f"  BAŞARISIZ    : {char.uuid}  ({e})")

            if not subscribed:
                log("  Hiçbir bildirim karakteristiğine abone olunamadı.")

            # --- pasif dinleme
            log()
            log("=" * 64)
            log(f"PASİF DİNLEME ({args.listen} saniye)")
            log("=" * 64)
            log("Saatin kendiliğinden bir şey göndermesini bekliyoruz.")
            log("Bu sırada saate dokun, ekranı uyandır, bir uygulama aç.")
            before = len(inbox)
            await asyncio.sleep(args.listen)
            log()
            log(f"Pasif dinlemede {len(inbox) - before} bildirim geldi.")

            # --- sonda
            if args.probe and has_rcsp:
                if await rcsp_auth(client, log, inbox):
                    await probe_rcsp(client, log, inbox)
                else:
                    log("Doğrulama geçmedi, sonda atlanıyor.")
            elif args.probe and not has_rcsp:
                log()
                log("RCSP servisi yok, sonda atlanıyor.")
            else:
                log()
                log("Sonda kapalı (--no-probe).")

            # --- abonelikleri kapat
            for uuid in subscribed:
                try:
                    await client.stop_notify(uuid)
                except Exception:
                    pass

    except Exception as e:
        log()
        log(f"BAĞLANTI HATASI: {type(e).__name__}: {e}")
        log()
        log("Sık görülen sebepler:")
        log("  - Saat telefona bağlı. Telefonun Bluetooth'unu kapat.")
        log("  - Bilgisayarda Bluetooth adaptörü yok ya da kapalı.")
        log("  - Saat menzil dışında.")

    # --- özet
    log()
    log("=" * 64)
    log("ÖZET")
    log("=" * 64)
    log(f"  Toplam bildirim: {len(inbox)}")
    if inbox:
        with open(f"qcy_frames_{stamp}.json", "w", encoding="utf-8") as f:
            json.dump(inbox, f, indent=2)
        log(f"  Ham çerçeveler kaydedildi: qcy_frames_{stamp}.json")
    log()
    log(f"  Log dosyası: {log.path}")
    log()
    log("Bu üç dosyayı Claude'a gönder, bir sonraki adımı ona göre kuralım.")
    log.close()


async def wait_rcsp_notify(inbox, start, pred, timeout=5.0):
    """AE02'den gelen, pred(data) koşulunu sağlayan ilk bildirimi bekle."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        for item in inbox[start:]:
            if item["uuid"].lower() == RCSP_NOTIFY:
                data = bytes.fromhex(item["hex"])
                if pred(data):
                    return data
        await asyncio.sleep(0.05)
    return None


async def rcsp_auth(client, log, inbox):
    """Jieli karşılıklı kimlik doğrulaması (RcspAuth), 6 adım.

    Bu paketler FE DC BA zarfı KULLANMAZ, ham olarak AE01'e yazılır:
      biz  -> 00 + 16 rastgele     saat -> 01 + 16 şifreli
      biz  -> 02 "pass"
      saat -> 00 + 16 rastgele     biz  -> 01 + 16 şifreli
      saat -> 02 "pass"
    """
    from jieli_cipher import get_encrypted_auth_data, get_random_auth_data

    log()
    log("=" * 64)
    log("KİMLİK DOĞRULAMA (RcspAuth)")
    log("=" * 64)

    async def send(data):
        log(f"  >>> {data.hex(' ').upper()}")
        await client.write_gatt_char(RCSP_WRITE, data, response=False)

    # 1-2: biz sınıyoruz
    start = len(inbox)
    challenge = get_random_auth_data()
    await send(b"\x00" + challenge)
    reply = await wait_rcsp_notify(inbox, start, lambda d: len(d) == 17 and d[0] == 0x01)
    if reply is None:
        log("  Saat ilk sınamaya cevap vermedi.")
        return False
    if reply[1:] == get_encrypted_auth_data(challenge):
        log("  Saatin cevabı beklenen şifreyle BİREBİR aynı -> anahtar doğru.")
    else:
        log("  UYARI: saatin cevabı beklenenden farklı, yine de devam ediliyor.")

    # 3: kabul ettik
    start = len(inbox)
    await send(b"\x02pass")

    # 4-5: saat bizi sınıyor
    chal = await wait_rcsp_notify(inbox, start, lambda d: len(d) == 17 and d[0] == 0x00)
    if chal is None:
        log("  Saat kendi sınamasını göndermedi.")
        return False
    start = len(inbox)
    await send(b"\x01" + get_encrypted_auth_data(chal[1:]))

    # 6: saat onaylıyor
    ok = await wait_rcsp_notify(inbox, start, lambda d: len(d) >= 5 and d[:5] == b"\x02pass")
    if ok is None:
        log("  Saat 'pass' onayı göndermedi.")
        return False
    log("  *** DOĞRULAMA BAŞARILI - saat 'pass' dedi ***")
    return True


async def probe_rcsp(client, log, inbox):
    """RCSP çerçeve biçimini ve komutları dene."""
    log()
    log("=" * 64)
    log("RCSP SONDASI")
    log("=" * 64)
    log("Sadece okuma komutları gönderiliyor. Cevap gelirse çerçeve biçimi doğru.")
    log()

    results = []

    for type_byte in TYPE_CANDIDATES:
        log(f"--- type baytı 0x{type_byte:02X} deneniyor ---")
        got_any = False

        for opcode, cmd_name in RCSP_READ_COMMANDS.items():
            # RCSP komut verisinin ilk baytı sıra numarası (SN) - APK çerçevesinde "00"
            # kayıtta gerçek uygulamanın gönderdiği parametreler (E87 rozet yakalaması)
            params = {0x03: bytes.fromhex("ffffffff00"), 0x07: bytes.fromhex("ff00000004")}.get(opcode, b"")
            frame = build_rcsp(type_byte, opcode, bytes([opcode & 0xFF]) + params)
            before = len(inbox)

            log(f"  >>> {cmd_name} (0x{opcode:02X})")
            log(f"      {' '.join(f'{b:02X}' for b in frame)}")

            try:
                await client.write_gatt_char(RCSP_WRITE, frame, response=False)
            except Exception as e:
                log(f"      YAZILAMADI: {e}")
                continue

            await asyncio.sleep(1.2)
            new = len(inbox) - before
            if new:
                got_any = True
                log(f"      CEVAP GELDİ ({new} çerçeve)")
                results.append({"type": type_byte, "opcode": opcode,
                                "command": cmd_name, "replies": new})
            else:
                log("      sessiz")

        if got_any:
            log()
            log(f"*** 0x{type_byte:02X} type baytı cevap üretti. Doğru biçim bu olabilir. ***")
            break
        log()

    log()
    if results:
        log("Cevap veren komutlar:")
        for r in results:
            log(f"  type=0x{r['type']:02X}  {r['command']}  ->  {r['replies']} çerçeve")
    else:
        log("Hiçbir sonda cevap almadı.")
        log("Muhtemel sebep: RCSP komut kabul etmeden önce kimlik doğrulama")
        log("(RcspAuth) bekliyor. Bu beklenen bir sonuç, sorun değil -")
        log("bir sonraki adımda kimlik doğrulama katmanını ekleyeceğiz.")


def main():
    p = argparse.ArgumentParser(description="QCY Watch BLE keşif aracı")
    p.add_argument("--name", default="QCY", help="cihaz adında aranacak metin (varsayılan: QCY)")
    p.add_argument("--address", default=None, help="cihazı doğrudan adresiyle seç")
    p.add_argument("--listen", type=int, default=15, help="pasif dinleme süresi, saniye")
    p.add_argument("--no-probe", dest="probe", action="store_false",
                   help="hiçbir komut gönderme, sadece dinle")
    args = p.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nKullanıcı durdurdu.")


if __name__ == "__main__":
    main()
