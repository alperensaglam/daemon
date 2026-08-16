# Masaüstü Agent Çekirdeği (UIA / AXUIElement)

Ekran görüntüsü ve VLM yerine işletim sisteminin **Erişilebilirlik Ağacı'nı**
temel alan, deterministik masaüstü agent çekirdeği.

| Platform | Algı | Eylem | Girdi | OCR |
|---|---|---|---|---|
| Windows | UI Automation (`comtypes`) | UIA pattern'leri | `SendInput` | `Windows.Media.Ocr` |
| macOS | `AXUIElement` (pyobjc) | AX eylemleri | `CGEvent` | Vision framework |

Piksel tahmini yok: LLM `node_id` ile konuşur, eylem doğrudan native pattern
üzerinden (`InvokePattern.Invoke()` / `AXPress`) işletim sistemi seviyesinde
çalışır.

Backend seçimi `agent/platform.py` içindeki fabrikada yapılır ve **arka ucu
import etmeden** karar verir; bu sayede paket, platform kütüphaneleri kurulu
olmayan bir makinede de import edilebilir ve testler her iki sistemde koşar.

> **Durum:** Algı + budama + eylem çekirdeği tamamlandı ve gerçek pencerelerde
> doğrulandı. Üzerine iki katman eklendi: **eylem doğrulama** (önce/sonra ağaç
> farkı, `execution/verifier.py`) ve **hibrit yürütme** (UI ⇄ kabuk
> yönlendirmesi, `execution/router.py`). Agent döngüsü (`orchestrator/loop.py`)
> hazır ve router üzerinden çalışır; eksik olan tek parça bir `LLMController`
> adaptörüdür (Ollama/Anthropic).

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

`requirements.txt` ortam işaretçileri kullanır; her platform yalnızca kendi
bağımlılıklarını kurar.

**Windows**

```powershell
winget install Python.Python.3.12
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install winsdk          # Vision fallback (isteğe bağlı)
```

**macOS**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # pyobjc paketleri
```

### İzinler

**Windows** — ayrı bir erişilebilirlik izni yoktur.

- **UIPI:** Yükseltilmemiş bir süreç, yönetici olarak çalışan pencereleri
  (Görev Yöneticisi, regedit) süremez. Onlar için agent'ı yönetici başlatın.
- **DPI:** Süreç başlarken `PER_MONITOR_AWARE_V2` ayarlanır. Bu yapılmazsa
  ölçekli ekranlarda bounding box'lar kayar ve piksel fallback'i ıskalar.

**macOS** — iki ayrı TCC izni gerekir:

| İzin | Ne için | Olmazsa |
|---|---|---|
| **Erişilebilirlik** | AX ağacını okumak, CGEvent göndermek | `extract()` açık bir hata verir |
| **Ekran Kaydı** | Vision fallback (yakalama), pencere başlıkları | OCR yolu kapalı; `list_windows` başlıkları AX'ten doldurur |

> İzin, `python` binary'sine değil **onu çalıştıran uygulamaya** (Terminal.app,
> iTerm2, VS Code) verilir ve **çalışan sürece geriye dönük uygulanmaz**. İzni
> verdikten sonra o uygulamayı tamamen kapatıp (Cmd+Q) yeniden açın.
>
> İzinsiz bir süreç, geçerli görünen ama `AXChildren`'ı boş bir uygulama
> elemanı alır ve her AX çağrısı `-25211` döner; bu yüzden kod izni her ağaç
> okumasından önce ayrıca kontrol eder ve ne yapılacağını söyleyen bir hata
> verir — sessizce boş ağaç dönmez.

Durumu kontrol etmek ve canlı doğrulama için:

```bash
.venv/bin/python scripts/verify_macos.py            # izinler + ağaç + OCR
.venv/bin/python scripts/verify_macos.py --bench 10 # gecikme ölçümü
```

---

## Kullanım

**Windows**

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python -m agent.cli windows              # pencereleri listele
.\.venv\Scripts\python -m agent.cli snapshot --wait 3    # aktif pencerenin durumu
.\.venv\Scripts\python -m agent.cli key "ctrl+s"
```

**macOS**

```bash
export PYTHONPATH=src
.venv/bin/python -m agent.cli windows                    # pencereleri listele
.venv/bin/python -m agent.cli snapshot --wait 3          # aktif pencerenin durumu
.venv/bin/python -m agent.cli snapshot --json            # LLM'e giden ham JSON
.venv/bin/python -m agent.cli --hwnd 18887 click 7       # --hwnd burada CGWindowID
.venv/bin/python -m agent.cli type 3 "merhaba" --yes
.venv/bin/python -m agent.cli key "meta+s"               # meta = Cmd (Windows'ta Win)
.venv/bin/python -m agent.cli bench --runs 12
```

**Hibrit yürütme (her iki platform)**

```bash
python -m agent.cli route "indirilenler klasorunu temizle"   # hangi şerit?
python -m agent.cli shell "git status --short"               # kabuk şeridi
python -m agent.cli --yes shell "ls -la ~/Desktop"
```

`route` yalnızca teşhis içindir; hiçbir şey çalıştırmaz. `shell` UI arka ucunu
kurmaz, dolayısıyla `comtypes`/`pyobjc` olmayan bir makinede de çalışır.

Terminal odaktayken `snapshot` terminali yakalar; hedef pencereye geçmek için
`--wait 3` verin veya `--hwnd` ile doğrudan hedefleyin. `--hwnd` bayrağının adı
geriye dönük uyumluluk için korundu; macOS'ta **CGWindowID** bekler ve
`agent.cli windows` çıktısındaki ilk sütun budur.

Tuş adlarında `meta` iki platformun işletim sistemi tuşunu birleştirir
(Windows'ta Win, macOS'ta Command); `cmd`, `command`, `win`, `super` hepsi
`meta`ya normalize edilir.

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
  execution/
    verifier.py           StateDiff + Expectation — eylem gerçekten oldu mu
    router.py             şerit sınıflandırma + araç dağıtımı (UI ⇄ kabuk)
    shell.py              PowerShell/bash motoru, zaman aşımı, engelli kalıplar
  orchestrator/
    loop.py               agent döngüsü: bütçe, tıkanma tespiti, bağlam hijyeni
  llm/
    schemas.py            9 araç (8 UI + run_shell), OpenAI + Anthropic biçimi
    base.py               LLMController ABC
  safety.py               risk sınıflandırması + onay kapısı (UI ve kabuk)
```

### Eylem doğrulama (Action → Observation → Verification)

Native bir çağrı hatasız dönebilir ama hiçbir şey olmayabilir: düğme devre
dışıdır, bir katman onu örter, uygulama isteği yutar. `ActionResult.ok` bu
durumda `True`dur ve model doğru düğmeye bastığını sanarak devam eder.

Her UI eyleminden sonra `Verifier` şunu yapar: ~60 ms bekle → yeni ağaç al →
eylem öncesiyle karşılaştır → beklenti karşılandı mı bak. Karşılanmadıysa
yoklamaya devam eder (bazı arayüzler 60 ms'de değil 600 ms'de tepki verir),
zaman aşımında `ActionVerificationError` üretilir ve modele **ne beklendiği,
ne gözlendiği ve hangi alternatifin denenebileceği** yazılır.

| Eylem | Varsayılan beklenti |
|---|---|
| `click` | ağaçta ölçülebilir bir değişiklik (`expect_appears` ile hedefe özgü) |
| `type_text` | hedef alanın değeri yazılan metne eşit (kırpma toleranslı) |
| `focus` | klavye odağı hedefe geçti |
| `scroll` | düğümler aynı yönde kaydı ya da liste yenilendi |
| `press_key alt+f4` | aktif pencere değişti |

Sonuç doğrulanamazsa araç çıktısı `ok: false` **ve** `executed: true` döner.
Bu ayrım kritik: model "başarısız" görünce tekrar denemek ister, oysa eylem
gönderilmiştir — geri alınamaz bir işlemi ikinci kez tetiklemek gerçek hasardır.

### Hibrit yürütme (UI ⇄ kabuk)

Saf UI agent'ı "300 dosyayı yeniden adlandır" görevini 300 tıklamayla çözmeye
çalışır; saf CLI agent'ı Photoshop'ta filtre uygulayamaz. Router iki şeridi
birden sunar ve hangi işin nereye gittiğine karar verir:

| İş | Şerit |
|---|---|
| dosya/klasör, metin filtreleme, git, süreç/port, paket-derleme | `run_shell` |
| uygulama içi menü, form, görsel araç, kullanıcının adıyla istediği uygulama | UIA / AX |

Karar modele **dayatılmaz**, sistem promptuna ipucu olarak eklenir:
sınıflandırıcı ekranı göremez, model görebilir. `agent.cli route "<hedef>"` bu
kararı dışarıdan gösterir.

Router ayrıca bir kilit tutar: bir eylem UI'yı değiştirdikten sonra yeni bir
`get_state` alınmadan başka bir UI eylemi kabul edilmez. Doğrulayıcının aldığı
ağaç önbelleğe konduğu için bu ek `get_state` yeni bir çıkarım yapmaz.

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

### macOS'a özgü sınırlar

Bunlar hata değil, **tasarım sınırıdır**; kovalanmamalı.

**AX, UIA kadar hızlı olamaz.** UIA tüm alt ağaç için tek bir
`BuildUpdatedCache` çağrısı yapar; AX **düğüm başına, öznitelik başına** bir
süreçler arası tur atar. `AXUIElementCopyMultipleAttributeValues` ile 13
öznitelik tek turda okunuyor, görünür-çocuk kısayolları kullanılıyor ve eylem
sorguları rolle sınırlanıyor — ama karşılığı yoktur. Bu bir API biçimi
farkıdır, eksik optimizasyon değil.

**Bayatlık koruması Windows'takinden zayıf.** AX'te `RuntimeId` yoktur;
kimlik `(pid, ağaç yolu, rol/subrole/identifier, başlık)` crc32'lerinden
sentezlenir ve karşılaştırma yerine **doğrulama** yapılır. Aynı ağaç yolunda
görsel olarak özdeş bir elemanla değiştirilen düğüm ayırt edilemez —
`NSTableView` hücre geri dönüşümü kaydırırken tam olarak bunu yapar.

**Menü çubuğu pencere alt ağacında değildir.** macOS'ta menüler *uygulama*
elemanına (`kAXMenuBar`) bağlıdır. Pencere kapsamlı bir `extract()` sıfır
File/Edit/View öğesi içerir, dolayısıyla "File → Save'e tıkla" akışı
Windows'taki gibi çalışmaz. Bu bir yetenek boşluğudur, eşleme hatası değil.

**Chromium ağacı tembeldir ve açılması istenmelidir.** Çıkarıcı, yürüyüşten
önce `AXManualAccessibility` bayrağını kurar; bu olmadan Chrome/VS Code/Slack
neredeyse boş ağaç döner.

**Klavye kodları konumsaldır.** `keycodes_mac` ANSI konum kodları kullanır, yani
Türkçe-F düzeninde `meta+s` fiziksel olarak S konumundaki tuşa basar. Doğrusu
`UCKeyTranslate` ile aktif düzeni ters eşlemektir; yapılmadı. **Metin yazma
bundan etkilenmez** — `type_unicode` düzenden bağımsızdır.

**`ctrl` çevirisi varsayılan olarak kapalıdır.** macOS'ta `ctrl+a`/`ctrl+e`/
`ctrl+k` gerçek Cocoa kısayollarıdır (satır başı/sonu/sil). Her `ctrl`i sessizce
`cmd`ye çevirmek bunları erişilemez kılardı; çeviri opt-in'dir ve devreye
girdiğinde `ActionResult.detail`de görünür.

**Odak her elemanda çalışmaz.** Düğmeler ve onay kutuları yalnızca *Sistem
Ayarları > Klavye > Klavye ile gezinme* açıkken klavye odağı kabul eder;
`focus()` bu durumda yeşil "OK" yerine ipucu içeren bir hata döner.

---

## Test

```bash
python -m pytest                       # birim testleri, işletim sistemi gerektirmez
python -m pytest --run-gui -m macos    # canlı macOS testleri (izin ister)
```

Birim testler gerçek pencere kullanmaz: filtreleme kuralları sabit girdilerle
doğrulanabilir olmalı, aksi halde sonuç makinede hangi uygulamanın açık
olduğuna göre değişir ve test bir şey kanıtlamaz.

Paket **her iki platformda da** yeşil koşar ve bu bir tesadüf değil, tasarım
kararıdır: tuş kod tabloları (`keycodes_win`, `keycodes_mac`), AX rol
eşlemesi (`ax_roles`) ve OCR geometrisi (`vision/geometry`) hiçbir platform
API'si import etmez, dolayısıyla Windows kodları macOS'ta (ve tersi) test
edilebilir. Öne çıkan birkaç test:

| Test | Ne yakalar |
|---|---|
| `test_ax_roles.py` | Eşleme tablosundaki her değerin budayıcının sözlüğünde olduğu. Bir yazım hatası (`"Buton"`) o rolün tüm düğümlerini sessizce düşürürdü. |
| `test_ax_roles_survive_pruner` | `AXScrollArea`nın **yalnızca** sentezlenen `Scroll` pattern'i sayesinde budamadan sağ çıktığı — yoksa düğüme göre kaydırma imkânsız olurdu. |
| `test_vision_geometry.py` | Normalize + sol-alt flip + Retina ölçeği matematiği. Hatası sessizdir: kutular kayar, istisna oluşmaz. |
| `test_platform.py::test_fabrika_backend_import_etmez` | Fabrikanın arka ucu import etmediği. Bozulursa paket yine platform kütüphanesi olmayan makinede import edilemez hale gelir. |
| `test_backend_contract.py` | Her arka ucun tüm soyut metotları uyguladığı — eksik metot import anında değil, **kurulunca** patlar. |

---

## Sonraki adım: LLM adaptörü

`llm/schemas.py` dokuz aracı tanımlar (`get_state`, `click`, `type_text`,
`press_key`, `scroll`, `focus`, `wait`, `done`, `run_shell`) ve hem OpenAI hem
Anthropic biçiminde verir. `llm/base.py` sözleşmeyi belirler; döngü
(`orchestrator/loop.py`) ve dağıtım (`execution/router.py`) hazır — eksik olan
`LLMController.propose()` uygulamasıdır:

```python
from agent.execution.router import build_router
from agent.orchestrator import AgentLoop

router = build_router(mode="ask")          # UI + kabuk + doğrulama
result = AgentLoop(MyOllamaController(), router).run("hedef metni")
print(result.success, result.summary, result.tool_calls)
```

Adaptör yazılırken bu makinede ölçülmüş iki davranış hesaba katılmalı:

1. Yerel modeller araç çağrısını bazen `tool_calls` alanı yerine **düz metne
   JSON olarak** yazar; metinden çağrı kurtaran bir katman gerekir.
2. qwen3 ailesi düşünme içeriğini `<think>` etiketiyle değil, ayrı bir
   `reasoning` alanında gönderir.

Ayrıca yerel model bu donanımda ~12 token/s üretiyor; 2347 token'lık bir Chrome
durumu tek başına dakikalar sürer. Düğüm bütçesi (varsayılan 150) bu yüzden
kritik.
