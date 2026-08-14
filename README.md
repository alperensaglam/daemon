# Masaüstü Agent

Bilgisayarında gerçekten iş yapan, **tamamen yerel** çalışan bir masaüstü asistanı.
Prompt yazarsın; dosyalarını okur/yazar, PowerShell komutu çalıştırır, program açar, internette arar.

Beyin olarak **OpenAI-uyumlu herhangi bir endpoint** kullanır — varsayılan olarak yerel Ollama.
Bulut yok, API ücreti yok, veri dışarı çıkmaz.

---

## Kurulum

### 1. Ollama'yı kur

<https://ollama.com/download/windows> — veya PowerShell'de:

```powershell
winget install Ollama.Ollama
```

### 2. Araç kullanabilen bir model indir

```powershell
ollama pull qwen3:4b
```

Bu bilgisayarda (16 GB RAM, ayrık GPU yok) model önerileri:

| Model          | Boyut  | Not                                                                    |
|----------------|--------|------------------------------------------------------------------------|
| `qwen3:4b`     | ~2.5GB | **Varsayılan.** Doğruluk açısından en iyisi ama yavaş. Ayarlar'dan `/no_think`'i açık bırakın. |
| `llama3.2:3b`  | ~2GB   | 5–15× hızlı ama belirgin daha az güvenilir (aşağıya bakın).             |
| `qwen3:8b`     | ~5GB   | Daha kaliteli, bu donanımda belirgin yavaş.                             |
| `llama3.1:8b`  | ~4.7GB | Sağlam function calling, yavaş.                                         |

### Ölçülen karşılaştırma (bu bilgisayarda, aynı görevler, model bellekte)

| Görev | `qwen3:4b` | `llama3.2:3b` |
|---|---|---|
| Klasör listeleme | 145 s — doğru | **28 s** — yanlış klasöre baktı |
| Dosya okuma (2 araç zinciri) | 436 s — doğru | **30 s** — araç çıktısı **uydurdu** |
| Dosya yazma | 172 s — doğru | **20 s** — doğru |

`llama3.2:3b` çok daha hızlı, ama testlerde çalışma alanı talimatını yok saydı ve hiç
çalışmamış araçların sonuçlarını metin olarak uydurdu. **Doğruluk önemliyse `qwen3:4b`
kullanın**; hız kritikse ve sonuçları gözden geçirecekseniz `llama3.2:3b` bir seçenek.

> ⚠️ Model **tool calling (function calling)** desteklemek zorunda.
> `gemma2` gibi desteklemeyen modellerde agent araç kullanamaz, sadece sohbet eder.

### 3. Uygulamayı başlat

```powershell
npm install
npm start
```

---

## Kullanım

- **Enter** gönderir, **Shift+Enter** yeni satır açar.
- **Ctrl+Shift+Space** pencereyi her yerden çağırır/gizler.
- Pencereyi kapatınca uygulama sistem tepsisinde kalır (tepsi ikonundan Çıkış).

### Örnek görevler

```
Masaüstümde hangi dosyalar var?
Masaüstüne notlar.txt oluştur, içine haftalık plan yaz
Boş disk alanımı ve RAM kullanımımı söyle
En çok RAM kullanan 5 programı listele
Masaüstündeki .txt dosyalarını bul ve hepsini rapor.md'de özetle
Bugün İstanbul'da hava nasıl, ara ve söyle
```

---

## Güvenlik modeli

Yerel bir modelin komut çalıştırması ve dosya yazması gerçek bir risktir. Uygulama üç katman kullanır:

**1. Çalışma alanı (workspace) sınırı**
Dosya araçları varsayılan olarak `Masaüstü` klasörüyle sınırlıdır (Ayarlar'dan değişir).
Göreceli yollar bu klasöre göre çözülür. `..` ve symlink ile kaçış denemeleri yakalanır.
Dışına çıkan **her** işlem, izin modu ne olursa olsun onay ister.

**2. İzin modları**

| Mod             | Davranış                                                          |
|-----------------|-------------------------------------------------------------------|
| **Sor** (varsayılan) | Okuma araçları otomatik; yazma ve komut çalıştırma onay ister |
| **Otomatik**    | Sadece riskli işlemler sorar                                       |
| **Salt-okunur** | Hiçbir şey değiştirilemez, komut çalıştırılamaz                    |

**3. Her zaman soran işlemler**
`Otomatik` modda bile şunlar onay ister: özyinelemeli silme, disk biçimlendirme, `diskpart`,
kayıt defteri silme, `shutdown`/`Stop-Computer`, `Invoke-Expression`, `netsh`, `bcdedit`,
`C:\Windows` ve `Program Files` altındaki işlemler.

Onay kartında işlemin **ne yapacağının önizlemesi** gösterilir (hangi dosya, üzerine mi
yazılacak, hangi komut). "Bu oturumda hep izin ver" seçeneği yalnızca o araç için ve yalnızca
uygulama açık kaldığı sürece geçerlidir — yüksek riskli işlemleri kapsamaz.

---

## Araçlar

| Araç             | Risk        | Ne yapar                                              |
|------------------|-------------|-------------------------------------------------------|
| `list_dir`       | okuma       | Klasör içeriğini listeler                             |
| `read_file`      | okuma       | Metin dosyası okur (200 KB'ye kadar)                  |
| `search_files`   | okuma       | Desene uyan dosyaları özyinelemeli arar               |
| `write_file`     | değişiklik  | Dosya yazar/ekler, klasörleri oluşturur               |
| `delete_path`    | riskli      | Dosya/klasör siler                                    |
| `run_command`    | riskli      | PowerShell komutu çalıştırır (60 sn, 100 KB çıktı)    |
| `open_path`      | değişiklik  | Dosya/klasör/URL/uygulama açar                        |
| `list_processes` | okuma       | Süreçleri RAM'e göre listeler                         |
| `kill_process`   | riskli      | Süreç sonlandırır                                     |
| `system_info`    | okuma       | CPU/RAM/disk/pil/çalışma süresi                       |
| `web_search`     | okuma       | DuckDuckGo (veya SearXNG) ile arama                   |
| `fetch_url`      | okuma       | Sayfayı indirip okunabilir metne çevirir              |

---

## Ayarlar

Ayarlar `%APPDATA%\masaustu-agent\config.json`, sohbet geçmişi `sessions.json` içinde tutulur.

| Ayar                | Açıklama                                                                |
|---------------------|-------------------------------------------------------------------------|
| Sunucu adresi       | Ollama `http://localhost:11434/v1`, LM Studio `http://localhost:1234/v1` |
| API anahtarı        | Yerelde gerekmez; OpenRouter/Groq gibi servisler için                   |
| Model               | Sunucudan otomatik listelenir                                            |
| Sıcaklık            | 0–2. Araç kullanımı için düşük (0.2–0.4) daha kararlı                    |
| Maksimum adım       | Bir görevde kaç araç turu yapılabilir (varsayılan 25)                    |
| Akışlı yanıt        | Kapatınca yanıt tek parça gelir — bozuk endpoint'ler için                |
| **Düşünme modunu kapat** | qwen3'ün `/no_think` modu. Ölçüldü: düz sohbet **111s → 39s (2.8×)** |
| **Modeli bellekte tut**  | Ollama 5 dk sonra modeli RAM'den atar; bu açıkken 30 dk kalır      |
| SearXNG adresi      | Boşsa DuckDuckGo kullanılır                                              |
| Sistem promptu      | Agent'ın davranışını belirler                                            |

### Başka sağlayıcılar

Aynı uygulama başka OpenAI-uyumlu servislerle de çalışır — sadece Ayarlar'dan
adres, anahtar ve modeli değiştirin:

| Servis     | Adres                                    |
|------------|------------------------------------------|
| LM Studio  | `http://localhost:1234/v1`               |
| llama.cpp  | `http://localhost:8080/v1`               |
| OpenRouter | `https://openrouter.ai/api/v1`           |
| Groq       | `https://api.groq.com/openai/v1`         |

---

## Performans (bu bilgisayarda ölçüldü)

Ayrık GPU yok, CPU inference. Ölçülen üretim hızı: **~12 token/saniye**.

| İşlem | Süre |
|---|---|
| Düz sohbet (düşünme açık) | ~111 s |
| Düz sohbet (`/no_think`) | ~39 s |
| Tek araç çağrısı | ~15–25 s |
| Tam agent turu (araç + cevap) | ~80–150 s |
| Çok adımlı görev (2 araç + cevap) | ~250–440 s |

Bunlar donanım sınırı; uygulama sınırı değil. Hızlandırmak için:

1. **Ayarlar > Düşünme modunu kapat** — en büyük tek kazanç (2.8×).
2. **Ayarlar > Modeli bellekte tut** — 5 dk boştan sonraki 2.5 GB yeniden yükleme beklemesini önler.
3. **Daha küçük model** — `llama3.2:3b` düşünme yapmaz, belirgin daha hızlıdır.
4. **Kısa promptlar** — model çıktısı ne kadar uzunsa o kadar bekleme.

## Küçük modellerde araç çağrısı kurtarma

Küçük yerel modeller araç çağrısını bazen doğru alanda değil, **düz metin içinde JSON olarak**
yazar. Bu durumda normalde hiçbir araç çalışmaz ve kullanıcıya ham JSON görünür.

Uygulama bunu yakalar: metindeki JSON'u ayrıştırır, adı kayıtlı bir araca uyuyorsa gerçek bir
araç çağrısına dönüştürüp çalıştırır ve JSON'u görünür metinden temizler. Kullanıcının kendi
JSON örnekleri veya bilinmeyen araç adları kurtarma tetiklemez.

## Sorun giderme

**"Model sunucusuna bağlanılamadı"**
Ollama çalışmıyor. PowerShell'de `ollama list` deneyin; boşsa `ollama serve` ile başlatın.

**Agent araç kullanmıyor, sadece konuşuyor**
Model tool calling desteklemiyor. `qwen3:4b` veya `llama3.1:8b` deneyin.

**Yanıtlar yarım geliyor / araç çağrıları bozuk**
Ayarlar'da **Akışlı yanıt**'ı kapatın.

**Çok yavaş**
Ayrık GPU olmadığı için CPU'da çalışıyor — yukarıdaki *Performans* bölümüne bakın.
Sırasıyla deneyin: Düşünme modunu kapat → Modeli bellekte tut → `llama3.2:3b`'ye geç.

**Model araç çağırmak yerine JSON metni yazıyor**
Uygulama bunu otomatik kurtarır (*Küçük modellerde araç çağrısı kurtarma* bölümü).
Sık oluyorsa modelin araç desteği zayıf demektir; başka bir model deneyin.

**Arama sonuç vermiyor**
DuckDuckGo sayfa yapısı değişmiş olabilir. Ayarlar > SearXNG adresine bir SearXNG
sunucusu girin (ör. `https://searx.be`).

---

## Mimari

```
src/
  main/                   Electron ana süreç (Node yetkileri burada)
    main.js               Pencere, tepsi ikonu, global kısayol
    ipc.js                Arayüz ↔ agent köprüsü
    config.js             Ayar kalıcılığı
    history.js            Sohbet geçmişi
    security.js           Yol sandbox'ı + komut risk taraması
    icon.js               Tepsi ikonunu çalışma anında PNG olarak üretir
    agent/
      client.js           OpenAI-uyumlu istemci (SSE akışı, tool_call birleştirme)
      loop.js             Agent döngüsü, onay akışı, iptal
      registry.js         Araç kayıt defteri
      tools/              files · shell · system · web
  preload/preload.js      contextBridge ile daraltılmış API
  renderer/               Arayüz (Node erişimi yok)
```

Renderer `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` ile çalışır
ve katı bir CSP uygular; tüm yetenekler preload'daki dar IPC yüzeyinden geçer.

Tek çalışma zamanı bağımlılığı `electron`.
