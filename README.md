# GS2 Kontrol

QCY Watch GS2 akıllı saatini üreticinin uygulaması olmadan kontrol etme projesi.

**Kontrol sayfası:** https://jordenss00-coder.github.io/gs2-kontrol/
iPhone'da [Bluefy](https://apps.apple.com/app/bluefy-web-ble-browser/id1492822055) ile,
bilgisayarda Chrome veya Edge ile açılır.

## Neler çalışıyor

- Saate BLE ile bağlanma ve Jieli RCSP kimlik doğrulaması
- Cihaz bilgisi (GET_TARGET_INFO) ve sistem bilgisi (GET_SYS_INFO) okuma
- Elle RCSP okuma komutu gönderme (yıkıcı komutlar engelli)

## İçerik

| Yol | Açıklama |
|---|---|
| `docs/` | Web Bluetooth kontrol sayfası (GitHub Pages) |
| `qcy_tool.py` | PC deney aracı: tarama, GATT haritası, doğrulama, sonda |
| `jieli_cipher.py` | Jieli kimlik doğrulama şifresi (Python) |
| `apk_sabitleri.py` | APK'dan Jieli komut/alan sabitlerini çıkarır |
| `rcsp_sabitleri.txt` | Çıkarılmış sabitler |
| `AGENTS.md` | Protokol notları ve proje bağlamı — yapay zeka asistanları için |

## Kurulum (PC aracı)

```bash
pip install -r requirements.txt
python qcy_tool.py
```

Test sırasında telefondaki QCY uygulamasını kapat; saat aynı anda tek uygulamayla konuşur.

## Uyarı

Resmî olmayan, tersine mühendislikle yazılmış bir araçtır. Saate yalnızca okuma komutları
gönderir. Yazma, silme ve firmware komutları saati kullanılamaz hale getirebilir.
Kullanım sorumluluğu kullanıcıya aittir.

Üçüncü taraf kod bildirimleri: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
