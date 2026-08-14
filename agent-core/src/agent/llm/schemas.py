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

CLICK = _tool(
    "click",
    "Bir UI elemanına tıklar. Butonlar, menü öğeleri, sekmeler, bağlantılar ve "
    "onay kutuları için kullanılır. İşletim sistemi seviyesinde native olarak "
    f"çalışır, piksel tahmini yapmaz. {_ID_WARNING}",
    {"node_id": {"type": "integer", "description": "Tıklanacak elemanın id'si"}},
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
    {"keys": {"type": "string", "description": "Tuş kombinasyonu"}},
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

DONE = _tool(
    "done",
    "Görev tamamlandığında veya devam edilemediğinde çağrılır. Döngü burada biter.",
    {
        "success": {"type": "boolean", "description": "Görev başarıyla tamamlandı mı"},
        "summary": {"type": "string", "description": "Ne yapıldığının kısa özeti"},
    },
    ["success", "summary"],
)

ALL_TOOLS: list[dict] = [
    GET_STATE, CLICK, TYPE_TEXT, PRESS_KEY, SCROLL, FOCUS, WAIT, DONE,
]


def openai_tools() -> list[dict]:
    """OpenAI uyumlu uçlar (Ollama, LM Studio, OpenRouter) için."""
    return ALL_TOOLS


def anthropic_tools() -> list[dict]:
    """Claude API için: parameters -> input_schema, düz yapı."""
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in ALL_TOOLS
    ]


SYSTEM_PROMPT = """Sen bir masaüstü otomasyon agent'ısın. Bilgisayardaki \
pencereleri erişilebilirlik ağacı üzerinden görür ve araçlarla kontrol edersin.

Nasıl çalışırsın:
1. Önce get_state ile ekranda ne olduğunu gör.
2. Hedefe götüren TEK bir eylem seç ve yap.
3. Eylemden sonra tekrar get_state al — arayüz değişmiş olabilir.
4. Görev bitince done çağır.

Kritik kurallar:
- node_id'ler anlık görüntüye özeldir. Eski bir get_state'in id'sini asla \
kullanma; her eylemden önce güncel durumu al.
- Ekranda göremediğin bir şeyi tahmin etme. Eleman listede yoksa scroll dene \
veya farklı bir yol ara.
- Bir eylem "ui_changed: false" dönerse aynı şeyi tekrar deneme; başka bir \
yaklaşım seç.
- Silme, gönderme, ödeme gibi geri alınamaz eylemlerde kullanıcı onayı istenir; \
reddedilirse ısrar etme.
- Durumda "warning" alanı varsa önce onu oku, ağaç boş görünmesinin sebebini \
açıklar."""
