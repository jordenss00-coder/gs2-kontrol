"""APK içindeki Jieli sabit sınıflarının alan adlarını ve sayısal değerlerini döker.

Kullanım:
    python apk_sabitleri.py --apk qcy-4-0-7.apk "jl_rcsp/constant/Command;" "jl_rcsp/constant/AttrAndFunCode;"
"""
import argparse
import zipfile

from loguru import logger
logger.remove()

from androguard.core.dex import DEX

p = argparse.ArgumentParser(description="APK'dan Jieli sabitlerini döker")
p.add_argument("--apk", default="qcy-4-0-7.apk", help="QCY APK dosyasının yolu")
p.add_argument("classes", nargs="*", default=["AttrAndFunCode"], help="sınıf adında aranacak metin")
args = p.parse_args()
WANT = args.classes

z = zipfile.ZipFile(args.apk)
for name in z.namelist():
    if not name.endswith(".dex"):
        continue
    data = z.read(name)
    if not any(w.encode() in data for w in WANT):
        continue
    dex = DEX(data)
    for cls in dex.get_classes():
        cname = cls.get_name()
        if not any(w in cname for w in WANT) or "jieli" not in cname:
            continue
        print(f"\n### {cname}  ({name})")
        for f in cls.get_fields():
            v = f.get_init_value()
            val = v.get_value() if v is not None else None
            if isinstance(val, int):
                print(f"  {f.get_name():<45} = {val} (0x{val & 0xFFFFFFFF:X})")
            elif val is not None:
                print(f"  {f.get_name():<45} = {val!r}")
