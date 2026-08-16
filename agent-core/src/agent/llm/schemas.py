"""LLM'e sunulan araç tanımları (OpenAI function-calling formatı).

Bu şemalar hem Ollama/OpenAI uyumlu uçlarla hem de Claude ile kullanılabilir;
Anthropic aracı tanımı aynı alanları ``input_schema`` altında ister, dönüşüm
``to_anthropic()`` ile yapılır.

Açıklama metinlerindeki en kritik bilgi **id'lerin anlık görüntüye özel
olduğu**: modelin en olası hatası, eski bir ``get_state`` çıktısının id'sini
yeniden kullanmaktır. Bu davranış her araç açıklamasında tekrar edilir çünkü
model tek bir yerdeki uyarıyı uzun bağlamda kaçırır.
"""

from __future__ import annotations

from typing import Any

_ID_WARNING = (
    "node_id, EN SON get_state çıktısındaki [@N] numarasıdır. Her eylemden "
    "sonra arayüz değişir ve id'ler geçersizleşir — eyleme geçmeden önce "
    "mutlaka yeni bir get_state alın. Eski id kullanmak yanlış elemana "
    "işlem yapmaya yol açar ve reddedilir."
)


def _tool(name: str, description: str, properties: dict[str, Any],
          required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


GET_STATE = _tool(
    "get_state",
    "Aktif pencerenin güncel erişilebilirlik ağacını döndürür: "
    '{"active_window": str, "nodes": [{"id", "role", "name", "value"}]}. '
    "Her eylemden sonra ve emin olmadığın her durumda önce bunu çağır. "
    "Sonuç 'warning' alanı içeriyorsa ağacın neden boş olduğunu açıklar.",
    {},
)

# Doğrulama katmanı (execution/verifier.py) her eylemden sonra "önce/sonra"
# ağaçlarını karşılaştırır. Model beklediği sonucu bildirirse karşılaştırma
# genel bir "bir şey değişti mi" kontrolünden çıkıp hedefe özgü hale gelir:
# "Kaydet'e tıkladım ve dosya adı kutusu açılmalı" iddiası, sessiz
# başarısızlığı yakalayan tek şeydir.
_EXPECT_FIELD = {
    "type": "string",
    "description": "İSTEĞE BAĞLI. Bu eylemden sonra ekranda belirmesini "
                   "beklediğin metin (pencere başlığı, düğme veya etiket adı). "
                   "Verirsen sonuç otomatik doğrulanır ve gerçekleşmezse "
                   "sana neden gerçekleşmediği bildirilir.",
}

CLICK = _tool(
    "click",
    "Bir UI elemanına tıklar. Butonlar, menü öğeleri, sekmeler, bağlantılar ve "
    "onay kutuları için kullanılır. İşletim sistemi seviyesinde native olarak "
    f"çalışır, piksel tahmini yapmaz. {_ID_WARNING}",
    {
        "node_id": {"type": "integer", "description": "Tıklanacak elemanın id'si"},
        "expect_appears": _EXPECT_FIELD,
    },
    ["node_id"],
)

TYPE_TEXT = _tool(
    "type_text",
    "Bir metin alanına yazı yazar. Varsayılan olarak mevcut içeriğin üzerine "
    f"yazar; sonuna eklemek için append=true ver. {_ID_WARNING}",
    {
        "node_id": {"type": "integer", "description": "Metin alanının id'si"},
        "text": {"type": "string", "description": "Yazılacak metin"},
        "append": {
            "type": "boolean",
            "description": "true ise mevcut içerik silinmez, sonuna eklenir",
        },
    },
    ["node_id", "text"],
)

PRESS_KEY = _tool(
    "press_key",
    "Klavye kısayolu gönderir, örnek: 'enter', 'ctrl+s', 'alt+f4', 'f5'. "
    "DİKKAT: tuşlar bir elemana değil, o an klavye odağı olan pencereye gider. "
    "Bu yüzden önce ilgili alana focus veya click yap. Hedef pencere ön plana "
    "getirilemezse tuş hiç gönderilmez (başka bir uygulamaya gitmesin diye).",
    {
        "keys": {"type": "string", "description": "Tuş kombinasyonu"},
        "expect_appears": _EXPECT_FIELD,
    },
    ["keys"],
)

SCROLL = _tool(
    "scroll",
    "Kaydırma yapar. node_id verilirse o konteyner, verilmezse aktif pencere "
    "kaydırılır. Aradığın eleman listede yoksa ekranda görünmüyor olabilir; "
    "kaydırıp yeniden get_state al.",
    {
        "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
        "amount": {"type": "integer", "description": "Adım sayısı (varsayılan 3)"},
        "node_id": {"type": "integer", "description": "Kaydırılacak konteynerin id'si"},
    },
    ["direction"],
)

FOCUS = _tool(
    "focus",
    "Bir elemana klavye odağını verir. press_key kullanmadan önce doğru alanın "
    f"odakta olduğundan emin olmak için kullanılır. {_ID_WARNING}",
    {"node_id": {"type": "integer", "description": "Odaklanacak elemanın id'si"}},
    ["node_id"],
)

WAIT = _tool(
    "wait",
    "Belirtilen süre bekler. Bir eylemden sonra arayüzün yüklenmesi gerektiğinde "
    "kullan (sayfa açılması, diyalog gelmesi). Boşuna bekleme: her saniye "
    "kullanıcının beklediği zamandır.",
    {"seconds": {"type": "number", "description": "Beklenecek saniye (en fazla 10)"}},
    ["seconds"],
)

RUN_SHELL = _tool(
    "run_shell",
    "İşletim sistemi kabuğunda bir komut çalıştırır (Windows'ta PowerShell, "
    "macOS/Linux'ta bash/zsh) ve çıktısını döndürür.\n"
    "BUNU TERCİH ET: dosya/klasör işlemleri (listeleme, taşıma, kopyalama, "
    "yeniden adlandırma, arşivleme), metin arama ve filtreleme, git işlemleri, "
    "süreç/port sorgulama, paket ve derleme komutları, toplu işler. Bu işleri "
    "arayüzde tıklayarak yapmak hem yavaştır hem de her adımda kırılır.\n"
    "BUNU KULLANMA: yalnızca grafik arayüzde bulunan işlevler (uygulama içi "
    "menüler, formlar, düzenleyiciler, görsel araçlar) ve kullanıcının açıkça "
    "'şu uygulamada yap' dediği işler. Onlar için get_state + click/type_text.\n"
    "Kabuk interaktif değildir: girdi bekleyen komutlar (vim, parola isteyen "
    "sudo) çalışmaz. Uzun süren komutlar zaman aşımına uğrar; komutu bölerek "
    "çalıştır.",
    {
        "command": {
            "type": "string",
            "description": "Çalıştırılacak tek satırlık komut",
        },
        "cwd": {
            "type": "string",
            "description": "İSTEĞE BAĞLI çalışma dizini (tam yol)",
        },
        "timeout": {
            "type": "number",
            "description": "İSTEĞE BAĞLI saniye cinsinden zaman aşımı",
        },
    },
    ["command"],
)

DONE = _tool(
    "done",
    "Görev tamamlandığında veya devam edilemediğinde çağrılır. Döngü burada biter.",
    {
        "success": {"type": "boolean", "description": "Görev başarıyla tamamlandı mı"},
        "summary": {"type": "string", "description": "Ne yapıldığının kısa özeti"},
    },
    ["success", "summary"],
)

#: UI şeridi — her kurulumda mevcut.
UI_TOOLS: list[dict] = [
    GET_STATE, CLICK, TYPE_TEXT, PRESS_KEY, SCROLL, FOCUS, WAIT, DONE,
]

#: Kabuk şeridi — yalnızca router'da bir ``ShellRunner`` varsa sunulur.
SHELL_TOOLS: list[dict] = [RUN_SHELL]

ALL_TOOLS: list[dict] = [*UI_TOOLS, *SHELL_TOOLS]


def tool_specs(include_shell: bool = True) -> list[dict]:
    """Sunulacak araç listesi.

    Kabuk erişimi kapalıysa ``run_shell`` listeden **çıkarılır**, açıklamasında
    "kullanma" yazılmaz. Modele var olmayan bir aracı göstermek, onu plana
    koyup ret yemesine ve bir tur kaybetmesine yol açar.
    """
    return [*UI_TOOLS, *SHELL_TOOLS] if include_shell else list(UI_TOOLS)


def openai_tools(include_shell: bool = True) -> list[dict]:
    """OpenAI uyumlu uçlar (Ollama, LM Studio, OpenRouter) için."""
    return tool_specs(include_shell)


def anthropic_tools(include_shell: bool = True) -> list[dict]:
    """Claude API için: parameters -> input_schema, düz yapı."""
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in tool_specs(include_shell)
    ]


SYSTEM_PROMPT = """Sen bir masaüstü otomasyon agent'ısın. Bilgisayarı iki \
şeritten kontrol edersin: erişilebilirlik ağacı üzerinden ARAYÜZ (get_state, \
click, type_text, press_key) ve işletim sistemi KABUĞU (run_shell).

Nasıl çalışırsın:
1. İşin hangi şeride ait olduğunu seç.
   - Dosya/klasör, metin arama-filtreleme, git, süreç/port, paket ve derleme \
işleri: run_shell. Tek komut, tek adım.
   - Uygulama içi işlevler, menüler, formlar, görsel araçlar: get_state ile \
ekranı gör, sonra tıkla/yaz.
   - Kararsızsan önce get_state al; ekranı görmek tahmin etmekten iyidir.
2. Hedefe götüren TEK bir eylem yap.
3. Sonucu oku. Arayüz eylemlerinden sonra tekrar get_state al — id'ler değişir.
4. Görev bitince done çağır.

Doğrulama ve hata:
- Her arayüz eylemi otomatik doğrulanır: eylem öncesi ve sonrası ağaç \
karşılaştırılır. Sonuçtaki "verification" alanı beklenenin gerçekleşip \
gerçekleşmediğini söyler.
- "verified": false gelirse eylem GÖNDERİLMİŞTİR ("executed": true) ama etkisi \
görülmemiştir. AYNI eylemi tekrarlama — özellikle kaydetme, gönderme gibi \
işlemlerde ikinci deneme iki kez çalışabilir. "hint" alanındaki alternatifi \
uygula: farklı eleman, focus+press_key, ya da işi kabuğa taşı.
- Ne beklediğini biliyorsan click/press_key çağrısında expect_appears ver \
(ör. "Farklı Kaydet"). Doğrulama o zaman hedefe özgü olur.

Kritik kurallar:
- node_id'ler anlık görüntüye özeldir. Eski bir get_state'in id'sini asla \
kullanma; her eylemden önce güncel durumu al.
- Ekranda göremediğin bir şeyi tahmin etme. Eleman listede yoksa scroll dene \
veya farklı bir yol ara.
- Silme, gönderme, ödeme gibi geri alınamaz eylemlerde kullanıcı onayı istenir; \
reddedilirse ısrar etme, alternatif öner.
- Kabuk interaktif değildir; girdi bekleyen komut çalışmaz. Yıkıcı komutlar \
(disk biçimlendirme, kök dizin silme) politika gereği hiç çalıştırılmaz.
- Durumda "warning" alanı varsa önce onu oku, ağaç boş görünmesinin sebebini \
açıklar."""
