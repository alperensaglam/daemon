# Masaüstü Agent

Bilgisayarında gerçekten iş yapan, **tamamen yerel** çalışan bir masaüstü asistanı.
Prompt yazarsın; dosyalarını okur/yazar, terminal komutu çalıştırır, program açar, internette arar.

Beyin olarak **OpenAI-uyumlu herhangi bir endpoint** kullanır — varsayılan olarak yerel Ollama.
Bulut yok, API ücreti yok, veri dışarı çıkmaz.

**Windows ve macOS'ta çalışır.** Kabuk, sistem araçları, güvenlik kuralları ve
sistem istemi platforma göre kendini ayarlar: Windows'ta PowerShell, macOS'ta zsh.

**Telefondan kullanılabilir.** İsteğe bağlı Telegram katmanıyla uzaktan komut
gönderir, riskli işlemleri satır içi butonlarla onaylar, ekran görüntüsü
alırsınız — port açmadan (bkz. [Uzaktan kontrol](#uzaktan-kontrol-telegram)).

---

## Kurulum

### 1. Ollama'yı kur

**Windows** — <https://ollama.com/download/windows> veya PowerShell'de:

```powershell
winget install Ollama.Ollama
```

**macOS** — <https://ollama.com/download/mac> veya Terminal'de:

```bash
brew install ollama
```

### 2. Araç kullanabilen bir model indir

```bash
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

```bash
npm install
npm start
```

### 4. (İsteğe bağlı) Paketle

```bash
npm run build:mac    # dmg + zip  (arm64 ve x64)
npm run build:win    # nsis kurulum dosyası
```

İkon `build/icon.png` olarak derleme öncesi otomatik üretilir (depoda ikili
varlık tutulmuyor); electron-builder ondan `.icns` ve `.ico` türetir.

> **macOS Gatekeeper.** Derlemeler imzasızdır (`identity: null`). İlk açılışta
> "geliştirici doğrulanamadı" uyarısı çıkar. Çözüm: uygulamaya **sağ tık > Aç**,
> ya da:
> ```bash
> xattr -dr com.apple.quarantine "/Applications/Masaustu Agent.app"
> ```
> İmzalı/notarize dağıtım isterseniz Apple Developer hesabı ve
> `mac.identity` + `notarize` yapılandırması gerekir.

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

`Otomatik` modda bile bazı işlemler onay ister. Bu liste **platforma göre değişir**, çünkü
komut sözlüğü ve korunan klasörler iki sistemde tamamen farklıdır:

| | Windows | macOS |
|---|---|---|
| **Yıkıcı komutlar** | `Remove-Item -Recurse`, `rmdir /s`, `format`, `diskpart`, `Clear-Disk`, `reg delete`, `Stop-Computer`, `Invoke-Expression`, `netsh`, `bcdedit`, `vssadmin delete` | `sudo`, `diskutil`, `dd of=`, `mkfs`, `chmod -R`, `chown -R`, `launchctl`, `killall`, `csrutil`, `spctl --master-disable`, `nvram`, `defaults delete`, `> /dev/…`, `pmset`, `tmutil delete` |
| **Korunan klasörler** | `C:\Windows`, `Program Files`, `Program Files (x86)` | `/System`, `/usr`, `/bin`, `/sbin`, `/Library`, `/Applications`, `/private/etc`, `/Volumes`, `~/Library` |

Her iki platformda ortak: `rm -rf`, `shutdown`, `curl … | sh`.

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
| `run_command`    | riskli      | Kabuk komutu çalıştırır — Windows'ta PowerShell, macOS'ta zsh (60 sn, 100 KB çıktı) |
| `open_path`      | değişiklik  | Dosya/klasör/URL/uygulama açar                        |
| `list_processes` | okuma       | Süreçleri RAM'e göre listeler                         |
| `kill_process`   | riskli      | Süreç sonlandırır                                     |
| `system_info`    | okuma       | CPU/RAM/disk/pil/çalışma süresi                       |
| `web_search`     | okuma       | DuckDuckGo (veya SearXNG) ile arama                   |
| `fetch_url`      | okuma       | Sayfayı indirip okunabilir metne çevirir              |

---

## Uzaktan kontrol (Telegram)

Telefondan komut gönderin, riskli işlemleri uzaktan onaylayın, ekran görüntüsü
isteyin. **Long-polling** kullanılır: bağlantıyı daima bilgisayarınız başlatır,
dolayısıyla port açmanız, webhook kurmanız veya makineyi internete açmanız
gerekmez.

### Kurulum

1. Telegram'da **@BotFather**'a `/newbot` yazıp bir bot oluşturun, verdiği
   token'ı alın.
2. **@userinfobot**'a bir mesaj atıp kendi sayısal kullanıcı ID'nizi öğrenin.
3. Ayarlar > Uzaktan kontrol bölümüne ikisini girin ve "Uygula ve bağlan"a basın.
4. Botunuza Telegram'dan ilk mesajı **siz** yazın (bot, konuşmayı kendisi
   başlatamaz — sohbet kimliğini o ilk mesajdan öğrenir).

Kimlik bilgilerini diske hiç yazmadan çalıştırmak isterseniz ortam
değişkenleri ayardaki değeri **ezer**:

```bash
TELEGRAM_BOT_TOKEN=... TELEGRAM_USER_ID=... npm start
```

### Komutlar

| Komut | Ne yapar |
|---|---|
| *(düz metin)* | Agent'ı çalıştırır |
| *(sesli mesaj)* | Çözümleyip agent'a verir — ayrı bir STT ucu gerekir (aşağıya bakın) |
| `/durum` | Agent durumu, aktif pencere, model, çalışma alanı, son çalışmanın özeti |
| `/ekran [pencere adı]` | Ekran veya belirli bir pencerenin görüntüsü |
| `/dur` | Çalışan işlemi durdurur |
| `/sifirla` | Telegram sohbet geçmişini temizler |
| `/yardim` | Komut listesi |

### Onay mekanizması (human-in-the-loop)

Agent riskli bir işleme karar verdiğinde (`rm -rf`, `sudo`, çalışma alanı
dışına yazma, korumalı klasöre dokunma…) Telegram'a satır içi butonlarla onay
isteği gider ve **cevap gelene kadar işlem başlamaz**.

Onay isteği **hem uygulama arayüzüne hem Telegram'a** aynı anda gider; ilk
cevaplayan kazanır, diğer taraftaki kart otomatik kapanır ("Uygulama
arayüzünden yanıtlandı" / "Uzaktan yanıtlandı"). Böylece masabaşındayken
telefonu açmanız gerekmez, uzaktayken de takılı kalmazsınız.

Cevapsız kalan onay isteği **10 dakika sonra zaman aşımına uğrar** ve işlem
yapılmaz. Hiçbir onay kanalı yanıt veremiyorsa (uygulama penceresi kapalı ve
Telegram bağlı değil) işlem **reddedilir** — soracak kimse yokken bir şeyin
sessizce çalışmaması bilinçli bir karardır.

### Güvenlik

- Yalnızca yapılandırdığınız kullanıcı ID'sinden gelen mesajlar **ve butonlar**
  kabul edilir. Buton basımlarının ayrıca doğrulanması kritik: yalnızca
  mesajları süzmek, yabancı birinin onay butonuna basabilmesi demek olurdu.
- Yetkisiz mesajlara **cevap verilmez**; cevap vermek botun canlı olduğunu ve
  doğru token'ın bulunduğunu doğrulardı. Denemeler yerel loga yazılır.
- Bot token'ı Telegram API'sinde URL'in içinde gider. Bu katmandan çıkan tüm
  hata metinleri token'dan temizlenir (`scrub`), böylece log veya arayüz
  üzerinden sızmaz.
- Uzaktan kontrol **izin modunu değiştirmez**. `Salt-okunur` moddaysanız
  Telegram'dan da hiçbir şey değiştirilemez.

### Sesli mesaj

Telegram sesli mesajları OGG/Opus gönderir. Çözümleme için **OpenAI uyumlu bir
`/audio/transcriptions` ucu** gerekir; Ayarlar > Uzaktan kontrol > "Ses
çözümleme adresi" alanına girilir.

> **Ollama ses çözümlemez.** Yerel bir seçenek için
> [whisper.cpp](https://github.com/ggerganov/whisper.cpp) sunucusu veya
> faster-whisper kullanabilirsiniz. Adres boşsa sesli mesajlar sessizce yok
> sayılmaz; bunun yerine neden çalışmadığını açıklayan bir cevap gelir.

### Test

Gerçek Telegram'a bağlanmadan, yerel sahte bir Bot API sunucusuyla:

```bash
npm test            # 20 birim + 11 entegrasyon testi
```

Test edilenler: yetkisiz kullanıcı ve buton reddi, onay yarışında ilk cevabın
kazanması, onaylayıcı yokken fail-closed davranışı, token'ın hata metinlerine
sızmaması, HTML enjeksiyonuna kapalılık, mesaj bölme.

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
Ollama çalışmıyor. Terminalde `ollama list` deneyin; boşsa `ollama serve` ile başlatın.

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
    platform.js           Tek platform dikişi (kabuk, tırnaklama, aktif pencere)
    icon.js               Tepsi ikonunu çalışma anında PNG olarak üretir
    agent/
      client.js           OpenAI-uyumlu istemci (SSE akışı, tool_call birleştirme)
      loop.js             Agent döngüsü, onay akışı, iptal
      runner.js           Tek çalışma noktası + onay dağıtımı (arayüz ⟷ uzaktan)
      registry.js         Araç kayıt defteri
      tools/              files · shell · system · web
    remote/
      controller.js       Telegram denetleyicisi (yetkilendirme, komutlar, onay)
      telegram.js         Bot API istemcisi — bağımlılıksız, long-polling
      screenshot.js       desktopCapturer sarmalayıcısı
      stt.js              Sesli mesaj → metin (OpenAI uyumlu uç)
  preload/preload.js      contextBridge ile daraltılmış API
  renderer/               Arayüz (Node erişimi yok)
```

**Onay dağıtımı.** `loop.js` tek bir `requestApproval` bekler ve bunu kimin
sağladığını bilmez. `runner.js` bu sözleşmeyi birden fazla kanala dağıtır
(arayüz, Telegram) ve ilk cevabı kazandırır. Yeni bir uzaktan kanal eklemek
(ör. e-posta, masaüstü bildirimi) `loop.js`'e dokunmayı gerektirmez —
`registerApprover` ile kaydolmak yeterlidir.

Renderer `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` ile çalışır
ve katı bir CSP uygular; tüm yetenekler preload'daki dar IPC yüzeyinden geçer.

Tek çalışma zamanı bağımlılığı `electron`.
