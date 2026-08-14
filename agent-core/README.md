# UIA Masaüstü Agent

Ekran görüntüsü ve VLM yerine işletim sisteminin **Erişilebilirlik Ağacı'nı**
(Windows UI Automation) temel alan, deterministik ve düşük gecikmeli masaüstü
agent çekirdeği.

Piksel tahmini yok: LLM `node_id` ile konuşur, eylem doğrudan UIA pattern'i
(`InvokePattern.Invoke()`, `ValuePattern.SetValue()`) üzerinden işletim sistemi
seviyesinde çalışır.

> **Durum:** Algı + budama + eylem çekirdeği tamamlandı ve gerçek pencerelerde
> doğrulandı. LLM döngüsü henüz yok — araç şemaları ve `LLMController` arayüzü
> hazır, sürücü olarak CLI kullanılıyor.

---

## Ölçülen sonuçlar

Bu bilgisayarda (Windows 11 26200, i7-13700, ayrık GPU yok):

| Pencere | Ham düğüm | Budanmış | Extract | Prune | Durum JSON |
|---|---|---|---|---|---|
| Not Defteri | 50 | 38 | **27 ms** | 0.1 ms | ~543 token |
| Chrome (YouTube) | 964 | 144 (%15) | **114 ms** | 0.3 ms | ~2347 token |
| Hesap Makinesi | 46 | 33 | **31 ms** | 0.1 ms | ~432 token |

Kabul testi (Not Defteri, 3 eylem): **%100 native UIA pattern, 0 piksel fallback.**

| Eylem | Kullanılan yol | Süre |
|---|---|---|
| `type_text` | `ValuePattern.SetValue()` | — |
| `press_key ctrl+s` | `SendInput` (pencere doğrulanarak) | — |
| `click` | `InvokePattern.Invoke()` | **5 ms** |

---

## Neden `comtypes` + CacheRequest

Bu, projedeki en önemli mimarî karar. UIA elemanlarından **canlı** özellik
okumak süreçler-arası bir COM çağrısıdır. Bu makinede ölçüldü:

```
düğüm başına canlı okuma  : 0.178 ms
düğüm başına cached okuma : 0.003 ms   ->  55x fark
```

500 düğümlü bir pencerede 8 özellik okumak canlı yolda ~700 ms, cached yolda
~12 ms eder. `uiautomation` / `pywinauto` paketleri her özellik erişiminde canlı
çağrı yapar; bu yüzden onlar yerine `comtypes` üzerinden tek bir
`BuildUpdatedCache(TreeScope_Subtree)` çağrısı kullanılır. Sonrasındaki her
okuma süreç-içidir.

---

## Kurulum

```powershell
winget install Python.Python.3.12
cd uia-agent
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install winsdk          # Vision fallback (isteğe bağlı)
```

### Windows izinleri

- macOS'taki gibi ayrı bir "Accessibility izni" **yoktur**, ek onay gerekmez.
- **UIPI:** Yükseltilmemiş bir süreç, yönetici olarak çalışan pencereleri
  (Görev Yöneticisi, regedit) süremez. Onlar için agent'ı yönetici başlatın.
- **DPI:** Süreç başlarken `PER_MONITOR_AWARE_V2` ayarlanır. Bu yapılmazsa
  ölçekli ekranlarda bounding box'lar kayar ve piksel fallback'i ıskalar.

---

## Kullanım

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python -m agent.cli windows              # pencereleri listele
.\.venv\Scripts\python -m agent.cli snapshot             # aktif pencerenin durumu
.\.venv\Scripts\python -m agent.cli snapshot --json      # LLM'e giden ham JSON
.\.venv\Scripts\python -m agent.cli --hwnd 12345 click 7
.\.venv\Scripts\python -m agent.cli type 3 "merhaba" --yes
.\.venv\Scripts\python -m agent.cli key "ctrl+s" --target
.\.venv\Scripts\python -m agent.cli bench --runs 12
```

Terminal odaktayken `snapshot` terminali yakalar; hedef pencereye geçmek için
`--wait 3` verin veya `--hwnd` ile doğrudan hedefleyin.

### Durum şeması (LLM'in gördüğü tek şey)

```json
{
  "active_window": "Hesap Makinesi",
  "nodes": [
    {"id": 1, "role": "Button", "name": "Yedi"},
    {"id": 2, "role": "Edit", "name": "Adres çubuğu", "value": "example.com"}
  ]
}
```

Boş alanlar bilinçli olarak atlanır — yerel modelde her token gecikmedir.

---

## Mimari

```
src/agent/
  cli.py                  LLM yerine insan sürücüsü
  core/
    types.py              UINode, Snapshot, ActionResult, Rect
    dpi.py                DPI awareness (her şeyden önce çalışır)
    errors.py
  perception/
    base.py               UITreeExtractor ABC — macOS/Linux genişleme dikişi
    windows_uia.py        comtypes + CacheRequest, tek round-trip
    pruner.py             filtre, dedupe, token bütçesi, [@N] id atama
  action/
    base.py               ActionExecutor ABC
    windows_uia.py        Invoke/Value/Toggle pattern + SendInput son çare
    keys.py               tuş eşlemesi, Unicode yazma, SendInput sarmalayıcı
  vision/
    fallback.py           PrintWindow + Windows.Media.Ocr
  llm/
    schemas.py            8 araç, OpenAI + Anthropic biçimi
    base.py               LLMController ABC
  safety.py               risk sınıflandırması + onay kapısı
```

### Eylem önceliği — piksel en son

| Eylem | 1. tercih | 2. tercih | Son çare |
|---|---|---|---|
| `click` | `InvokePattern` | `SelectionItem` / `Toggle` / `ExpandCollapse` | bounding box merkezine `SendInput` |
| `type_text` | `ValuePattern.SetValue` | odak + Unicode `SendInput` | — |
| `scroll` | `ScrollPattern` | fare tekerleği | — |

Her sonuçta `method` alanı hangi yolun kullanıldığını söyler. `pixel_fallback`
değeri, o eylemde mimarinin işe yaramadığını gösteren ölçülebilir sinyaldir.

---

## Güvenlik

Bir UI agent'ı dosya agent'ından farklı bir risk taşır: yanlış düğüme tıklamak
"Sil", "Gönder", "Satın al" olabilir ve model doğru butona bastığını sanar.

**Üç mod:**

| Mod | Davranış |
|---|---|
| `ask` (varsayılan) | Her değiştirici eylem sorulur |
| `--yes` | Sıradan eylemler geçer, **yüksek riskliler yine sorulur** |
| `--dry-run` | Hiçbir şey uygulanmaz, sadece ne yapılacağı yazılır |

**Türkçe eklemeli bir dil** olduğu için risk kalıpları kök eşlemesi yapar:
`\bsil\b` kalıbı "Silme"yi, "Ödeme"yi kaçırırdı. Kök + `\w*` bazen gereksiz
sorar; bu bilinçli bir tercihtir — gereksiz sormanın bedeli bir tıklama,
kaçırmanın bedeli geri alınamaz bir işlem.

**Bayat `node_id` reddi:** id'ler anlık görüntüye özeldir. Eylem öncesi
`RuntimeId` doğrulanır; uyuşmazsa işlem yapılmaz. Yanlış düğüme tıklamaktansa
hata vermek doğrudur.

---

## Test sırasında bulunan gerçek sorunlar

Bunlar kod okuyarak değil, ölçerek bulundu ve hepsi düzeltildi:

**1. `BoundingRectangle`'ın iki biçimi var.**
UIA property biçimi `(left, top, WIDTH, HEIGHT)` döner; struct biçimi
`(left, top, right, bottom)`. Struct sanıldığında genişlik negatife düşüyor,
alan sıfır çıkıyor ve düğüm "görünmez" diye eleniyordu. Hiç hata vermiyor,
ağacın büyük kısmı sessizce kayboluyordu — **Chrome'da 520 tıklanabilir düğüm.**

**2. Pencere sınırını ağaçtan tahmin etmek.**
Simge durumundaki pencereyi Windows -32000,-32000'e park eder. Kök düğümden
okunan bu rect ile "pencere dışı" filtresi çalıştırılınca tüm içerik eleniyordu
(Not Defteri 48 → 2 düğüm). Artık `GetWindowRect` + `IsIconic` kullanılıyor.

**3. Odaklanabilir layout konteynerleri gürültü yapıyordu.**
Adsız, pattern'siz `Pane`/`Group`'lar sırf `focusable` oldukları için listede
kalıyordu. Layout kontrolü artık `focusable` kontrolünden **önce** gelir.

**4. Klavye girişi düğüme değil, odağa gider.**
`press_key("ctrl+s")` hedef pencere ön planda değilken tuşu **başka bir
uygulamaya** gönderiyordu — dosya kaydedilmedi ve tuş kullanıcının o an
yazdığı yere gitti. Artık pencere öne getirilir, geldiği **doğrulanır**,
doğrulanamazsa tuş hiç gönderilmez.

---

## Bilinen sınırlar

**UWP/WinUI uygulamaları arka planda askıya alınır.** Hesap Makinesi, Ayarlar,
Takvim gibi uygulamalar ön planda değilken Windows tarafından askıya alınır ve
UIA ağaçları boşalır. Ölçüldü:

```
Hesap Makinesi, ön planda : 46 ham düğüm
Hesap Makinesi, askıda    :  1 ham düğüm
```

Uygulama bu durumu sessizce boş liste olarak dönmez; durum JSON'una açık bir
`warning` alanı ekler.

**`SetForegroundWindow` her zaman çalışmaz.** Windows'un ön plan kilidi
nedeniyle "Erişim engellendi" dönebilir. Odağa bağımlı eylemler bu durumda
iptal edilir — sessizce yanlış pencereye gitmez.

**Chromium erişilebilirliği tembel açar.** İlk sorgu sonrası ağaç büyür
(964 → 3124 düğüm gözlendi). Soğuk tur ölçümde ayrı raporlanır.

**OCR mükemmel değil.** Windows OCR "agent" kelimesini "ğggn_t" okuyabiliyor;
Türkçe karakterler doğru geliyor. Fallback bir çare, birincil yol değil.

---

## Test

```powershell
.\.venv\Scripts\python -m pytest        # 75 birim testi, UIA gerektirmez
```

Birim testler gerçek pencere kullanmaz: filtreleme kuralları sabit girdilerle
doğrulanabilir olmalı, aksi halde sonuç makinede hangi uygulamanın açık
olduğuna göre değişir ve test bir şey kanıtlamaz.

---

## Sonraki adım: LLM döngüsü

`llm/schemas.py` sekiz aracı tanımlar (`get_state`, `click`, `type_text`,
`press_key`, `scroll`, `focus`, `wait`, `done`) ve hem OpenAI hem Anthropic
biçiminde verir. `llm/base.py` sözleşmeyi belirler.

Adaptör yazılırken bu makinede ölçülmüş iki davranış hesaba katılmalı:

1. Yerel modeller araç çağrısını bazen `tool_calls` alanı yerine **düz metne
   JSON olarak** yazar; metinden çağrı kurtaran bir katman gerekir.
2. qwen3 ailesi düşünme içeriğini `<think>` etiketiyle değil, ayrı bir
   `reasoning` alanında gönderir.

Ayrıca yerel model bu donanımda ~12 token/s üretiyor; 2347 token'lık bir Chrome
durumu tek başına dakikalar sürer. Düğüm bütçesi (varsayılan 150) bu yüzden
kritik.
