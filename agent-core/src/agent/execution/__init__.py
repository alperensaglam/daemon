"""Yürütme katmanı: eylemin *yapılması* ile *doğrulanması* burada birleşir.

Altındaki iki modül birbirini tamamlar:

* ``verifier``  — Action → Observation → Verification döngüsü. Bir eylemin
  gerçekten işe yarayıp yaramadığını UI ağacını önce/sonra karşılaştırarak
  ölçer; yaramadıysa modele *neden* yaramadığını söyler.
* ``router``    — Hibrit yürütme. Aynı işi hem kabuk (CLI) hem arayüz (UIA/AX)
  ile yapmak mümkünse hangisinin seçileceğine karar verir ve araç çağrısını
  ilgili şeride dağıtır.
* ``shell``     — Kabuk şeridinin motoru: PowerShell/bash çağrısı, zaman aşımı,
  çıktı kırpma ve engellenen komut kalıpları.
"""
