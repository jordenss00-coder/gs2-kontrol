# QCY Watch GS2 — Proje Bağlamı (yapay zeka asistanları için)

Bu dosya, projeye sıfırdan giren bir asistanın (Claude, Codex, Cursor, Gemini…)
hemen devam edebilmesi için yazıldı. Tek doğru kaynak budur; `CLAUDE.md` bunu içe aktarır.

**Dil:** Proje sahibi Türkçe konuşuyor. Açıklamalar Türkçe, adım adım ve
neden-sonuçlu olsun. Donanım bilgisi güçlü, kodlama başlangıç seviyesinde.

## Amaç

QCY Watch GS2 akıllı saatini üreticinin uygulaması olmadan, kendi yazılımımızla
kontrol etmek. Hedefler: saatin dosya sistemine erişmek, sağlık verisini çekmek,
kendi kadranını kurmak. Saat **iPhone'a bağlı** kullanılıyor.

## Depo yapısı

| Yol | Ne |
|---|---|
| `docs/index.html` | Web Bluetooth kontrol sayfası (GitHub Pages ile yayında) |
| `docs/jl-auth.js` | Jieli kimlik doğrulama şifresi, JavaScript |
| `qcy_tool.py` | Windows/PC deney tezgâhı (Python + bleak): tara, bağlan, doğrula, sonda |
| `jieli_cipher.py` | Aynı şifrenin Python hali |
| `apk_sabitleri.py` | APK'dan Jieli sabitlerini döken betik (androguard) |
| `rcsp_sabitleri.txt` | Bu betiğin çıktısı: bütün komut ve alan kodları |
| `THIRD_PARTY_NOTICES.md` | MIT lisans bildirimleri |

Depoda OLMAYANLAR (`.gitignore`): QCY APK'sı, `qcy_log_*`/`qcy_frames_*`/`qcy_gatt_*`
çıktıları (cihaz adresi içerir), `CLAUDE.local.md` (kişisel notlar).

## Platform kararı

- Mac ve Apple geliştirici hesabı YOK → yerli iOS uygulaması şimdilik yapılamaz.
- Seçilen yol: **Web Bluetooth sayfası + iPhone'da Bluefy tarayıcısı**.
  Safari Web Bluetooth desteklemez. Masaüstünde Chrome/Edge çalışır.
- Yayın adresi: https://jordenss00-coder.github.io/gs2-kontrol/
  (depo `jordenss00-coder/gs2-kontrol`, dal `main`, Pages kaynağı `/docs`).
- Kısıtlar: Bluefy'de arka planda çalışma yok, Apple Sağlık erişimi yok.
  Bildirimler iOS'un kendi ANCS'i ile saate zaten gidiyor; yazmaya gerek yok.
- Testte QCY uygulaması kapalı olmalı (saatle aynı anda konuşur).
- Yeni komutlar önce `qcy_tool.py` ile PC'de denenir, çalışınca sayfaya taşınır.

## Çalıştırma

```bash
pip install -r requirements.txt
python qcy_tool.py                     # adında "QCY" geçen cihazı bulur
python qcy_tool.py --address XX:XX:XX:XX:XX:XX --listen 3
python -m http.server 8000 --directory docs   # sonra Chrome'da http://localhost:8000
```

Web Bluetooth yalnızca `https://` veya `localhost` üzerinde çalışır (IP adresiyle değil).

## Donanım ve BLE yapısı (doğrulandı)

Saatte iki BLE yığını yan yana çalışıyor:

| Servis | Karakteristikler | Katman |
|---|---|---|
| `0000ae00-0000-1000-8000-00805f9b34fb` | `ae01` Write w/o Response, `ae02` Notify | Jieli RCSP — çip katmanı |
| `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | `6e400002` Write, `6e400003` Notify | Nordic UART — SMA uygulama protokolü (henüz dokunulmadı) |

- **Çip: Jieli.** Windows'taki Bluetooth kaydında `VID 05D6` = Zhuhai Jieli.
  Kardeş model GT2'nin söküm raporunda JL7012A6 (AC7012A6) çıkmıştı.
- **Uygulama katmanı: SMA** (`com.szabh.smable3`, APK'da 1821 referans, 320 mesaj sınıfı).
- Saat klasik BT ile de eşleşiyor (arama sesi); BLE adresi klasik adresle aynı.

Kaynak APK: `com.qcymall.googleearphonesetup` 4.0.7. İlgili modüller:
`com.jieli.jl_rcsp` (protokol), `jl_bt_ota`, `jl_filebrowse`, `jl_fatfs`, `bmp_convert`.
Jieli'nin `github.com/Jieli-Tech/Android-JL_Health` deposunda bunlar yalnızca derlenmiş
`.aar` olarak var; **kaynak kod yok**. Sabitler bu yüzden APK'dan androguard ile okunuyor.

## Kimlik doğrulama (çözüldü, gerçek saatte çalışıyor)

Saat, doğrulama olmadan gelen bütün RCSP komutlarını **sessizce atar** (hata da dönmez).

Paketler FE DC BA zarfı KULLANMAZ; ham olarak `ae01`'e yazılır, cevaplar `ae02`'den gelir:

```
biz  -> 00 + 16 bayt rastgele
saat -> 01 + 16 bayt şifreli      (bizim hesabımızla birebir aynı çıkıyor)
biz  -> 02 70 61 73 73            ("\x02pass")
saat -> 00 + 16 bayt rastgele
biz  -> 01 + 16 bayt şifreli
saat -> 02 70 61 73 73            -> komutlar artık kabul ediliyor
```

- Şifre Bluetooth E1 değil, Jieli'ye özel bir blok şifre (Quarkslab yazısı E1 diyor, yanıltıcı).
  Port: `hybridherbst/web-bluetooth-e87` (`jl-auth.ts`) → `jumpingmushroom/e87_badge` (`jieli_cipher.py`). MIT.
- Anahtar `06775F87918DD423005DF1D8CF0C142B`, magic `11 22 33 33 22 11`.
- QCY APK'daki arm64 `libjl_auth.so` içinde doğrulandı: anahtar 0x23E0,
  KS_TABLE 0x8E0, SBOX 0x9E0, ISBOX 0xAE0 — upstream tablolarla birebir aynı.
- Test vektörü: challenge `70B75992E05EA78FEC533BA12979B590` →
  response `FFE9E6C80CE1F40F5CCEAE20831C5879` (Python ve JS ikisi de doğru).

## RCSP çerçeve biçimi (doğrulandı)

```
İstek: FE DC BA | C0 | op | len(2, BE) | SN | param... | EF
Cevap: FE DC BA | 00 | op | len(2, BE) | status | SN | veri... | EF
```

- `len` = SN (+status) + param/veri bayt sayısı. Toplam çerçeve = 8 + len.
- **SN zorunlu.** Olmadan saat cevap vermez. Her komutta 1 artır, FF'den 00'a sarar.
- Tip baytı: bit7 = istek, bit6 = cevap bekleniyor → `C0` istek+cevap, `80` cevapsız istek, `00` cevap.
- `status`: 00 başarılı, 02 parametre hatası / desteklenmiyor.
- Cevap verisi çoğunlukla TLV: `[uzunluk][tip][değer (uzunluk-1 bayt)]`.

## Komut ve alan kodları

Tam liste: `rcsp_sabitleri.txt` (`com.jieli.jl_rcsp.constant.Command` ve `AttrAndFunCode`).

**Okuma / güvenli:** `02` GET_TARGET_FEATURE_MAP, `03` GET_TARGET_INFO, `07` GET_SYS_INFO,
`09` SYS_INFO_AUTO_UPDATE, `0C` START_FILE_BROWSE, `0D` STOP_FILE_BROWSE,
`24` READ_FILE_FROM_DEVICE, `29` READ_ERROR_MSG, `A0` GET_HEALTH_DATA, `D4` GET_DEV_MD5,
`D6` GET_EXTERNAL_FLASH_MSG, `D9` GET_DEVICE_CONFIG_INFO.

**YIKICI — gönderme** (web sayfasında da engelli):
`06` DISCONNECT_CLASSIC_BT, `08` SET_SYS_INFO, `16`–`18` dosya aktarımı, `1A` EXTERNAL_FLASH_IO_CTRL,
`1B`–`1E` büyük dosya aktarımı, `1F` FILE_BROWSE_DELETE, `22` FORMAT_DEVICE, `23` DELETE_FILE_BY_NAME,
`28` SMALL_FILE_TRANSFER, `D1` MTU ayarı, `D8` SET_DEVICE_STORAGE, `E1`–`E8` OTA ve `E7` REBOOT.

### GET_TARGET_INFO (0x03)

Parametre `FFFFFFFF 00` (tüm alanlar). Gerçek cevap (adres gizlendi):

| Tip | Ad | Değer |
|---|---|---|
| 00 | PROTOCOL_VERSION | `20` |
| 01 | POWER_UP_SYS_INFO | `00 00 00 01 1E` — anlamı çözülmedi, **pil değil** |
| 02 | EDR_ADDR | klasik BT adresi (6B) + `8E 01` |
| 04 | FUNCTION_INFO | `00 00 00 04 00 00` |
| 05 | FIRMWARE_INFO | `00 14` |
| 06 | SDK_TYPE | `09` |
| 07 | UBOOT_VERSION | `1B 34` |
| 08 | SUPPORT_DOUBLE_BACKUP | `00 01 01` |
| 09 | MANDATORY_UPGRADE_FLAG | `00 00 00` |
| 0A | VID_AND_PID | `00 02 00 81` |
| 0D | PROTOCOL_MTU | `01 10 02 1C` (272 / 540) |
| 11 | CONNECT_BLE_ONLY | `00` + BLE adresi (6B) |
| 12 | PERIPHERALS_SUPPORT | `00` |
| 13 | DEV_SUPPORT_FUNC | `90 04` |
| 15 | FILE_TRANSFER | `00 00 00 00` |

Diğer adlar: 03 PLATFORM, 0B AUTH_KEY, 0C PROJECT_CODE, 0E ALLOW_CONNECT, 10 NAME,
14 RECODE_FILE_TRANSFER, 1F CUSTOM_VER.

### GET_SYS_INFO (0x07)

- Parametre: `[fonksiyon grubu][alan maskesi 4B big-endian]`. Cevap verisi: `[grup] TLV...`.
- Grup `FF` = PUBLIC. PUBLIC alanları: 0 pil, 1 ses, 2 müzik cihaz durumu, 3 hata, 4 EQ,
  5 dosya tipi, 6 aktif mod, 7 ışık, 8 FM TX.
- Pil sorgusu: `FF 00000001`. **Henüz gerçek saatte doğrulanmadı.** Test sırasında saat %50 gösteriyordu.
- `FF 00000004` gönderildiğinde cevap tip `02`, 21 bayt sıfır geldi.
- Diğer gruplar: 00 BT, 01 müzik, 02 RTC, 03 AUX, 04 FM, 05 ışık, 06 FMTX, 07 EQ, 16 düşük güç.

### Diğer faydalı alt kodlar

- HEALTH_DATA_TYPE: 0 nabız, 1 hava basıncı, 2 irtifa, 3 adım, 4 stres, 5 SpO2, 6 antrenman yükü.
- EXT_FLASH_OP: 0 WRITE, 1 READ, 2 INSERT_FILE, 3 WATCH, 4 ERASURE, 5 DELETE, 11 GET_FILE_MSG, 12 GET_LEFT_SPACE.
- WATCH_OP (kadran): 0 GET, 1 SET, 2 NOTIFY, 3 GET_VERSION, 4 ENABLE_CUSTOM, 5 GET_CUSTOM_BG.
- `02`, `D6`, `D9` parametresiz gönderilince status 02 döndü → doğru parametre gerekiyor.

## Sıradaki adımlar

1. Pil sorgusunu (`07` / `FF 00000001`) gerçek saatte doğrula.
2. `0x02` FEATURE_MAP ve `0xD6` EXTERNAL_FLASH_MSG parametrelerini APK'dan bul
   (androguard ile ilgili `*Param` sınıflarının `getParamData()` metotları).
3. `0x0C` START_FILE_BROWSE ile saatin klasörlerini listele.
4. `0xA0` GET_HEALTH_DATA ile adım/nabız çek.
5. Nordic UART (SMA) kanalını incele: saat ayarı, sağlık geçmişi.
6. Kadran: mevcut kadranı indir, `bmp_convert` biçimini çöz.

## Güvenlik kuralları

- Saate yalnızca okuma komutu gönder. Yazma/silme/OTA komutu için kullanıcıdan açık onay al.
- Firmware/ISP (USB D+/D−, `kagaimiq/jl-uboot-tool`) tuğlalama riski taşır; önce flash dump. Şu an kapsam dışı.
- Cihaz adresini ve kişisel bilgileri bu açık depoya yazma; `CLAUDE.local.md` kullan.

## Kaynaklar

- Quarkslab — A modern tale of blinkenlights: https://blog.quarkslab.com/modern-tale-blinkenlights.html
- https://github.com/hybridherbst/web-bluetooth-e87
- https://github.com/jumpingmushroom/e87_badge (özellikle `docs/protocol.md`)
- https://github.com/Jieli-Tech/Android-JL_Health
