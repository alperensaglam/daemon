#!/usr/bin/env python3
"""macOS arka ucunu canli olarak dogrular ve gecikme olcer.

Neden ayri bir betik: Erisilebilirlik izni **calisan bir surece geriye donuk
uygulanmaz**. Izin, python'a degil onu calistiran uygulamaya (Terminal.app,
iTerm2, VS Code...) verilir; o uygulama tamamen kapatilip acilmadan mevcut
oturum izni goremez. Dolayisiyla dogrulama, izin verildikten SONRA acilmis bir
terminalden calistirilmalidir.

Kullanim:
    cd agent-core
    .venv/bin/python scripts/verify_macos.py            # salt okuma, guvenli
    .venv/bin/python scripts/verify_macos.py --bench 10 # gecikme olcumu
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.core.errors import AgentError                      # noqa: E402
from agent.perception.base import format_window_row           # noqa: E402
from agent.perception.pruner import PruneConfig, TreePruner    # noqa: E402

OK, NO = "  ✓", "  ✗"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench", type=int, default=0, metavar="N",
                        help="Anlik goruntu gecikmesini N kez olc")
    args = parser.parse_args()

    if sys.platform != "darwin":
        print(f"Bu betik yalnizca macOS icin. Su anki platform: {sys.platform}")
        return 2

    print("=" * 68)
    print("macOS arka uc dogrulamasi")
    print("=" * 68)

    # --- 1. Izinler -------------------------------------------------------
    print("\n[1] Izinler")
    from agent.perception.macos_ax import accessibility_status
    from agent.vision.capture_mac import screen_capture_status

    ax_ok, ax_reason = accessibility_status()
    print(f"{OK if ax_ok else NO} Erisilebilirlik: {ax_reason}")
    sc_ok, sc_reason = screen_capture_status()
    print(f"{OK if sc_ok else NO} Ekran Kaydi: {sc_reason if not sc_ok else 'izin var'}")

    # --- 2. Pencere listesi (izin gerektirmez) ----------------------------
    print("\n[2] list_windows()  — erisilebilirlik izni gerektirmez")
    from agent.perception.macos_ax import MacAXExtractor
    extractor = MacAXExtractor()
    windows = extractor.list_windows()
    print(f"{OK} {len(windows)} ust duzey pencere")
    for window in windows[:5]:
        print("     " + format_window_row(window))

    if not ax_ok:
        print("\n" + "!" * 68)
        print("Erisilebilirlik izni olmadan agac okunamaz. Yapilacaklar:")
        print("  1. Sistem Ayarlari > Gizlilik ve Guvenlik > Erisilebilirlik")
        print("  2. Terminal/iTerm/VS Code'u listeye ekleyip ISARETLEYIN")
        print("  3. O uygulamayi TAMAMEN kapatip yeniden acin (Cmd+Q)")
        print("  4. Bu betigi YENI acilan terminalden calistirin")
        print("!" * 68)
        return 1

    # --- 3. Agac cikarimi -------------------------------------------------
    print("\n[3] extract()  — aktif pencerenin AX agaci")
    result = extractor.extract()
    print(f"{OK} '{result.window_title[:50]}' ({result.process_name})")
    print(f"     ham dugum: {len(result.nodes)}   sure: {result.extract_ms:.0f} ms")
    print(f"     pencere:   {result.window_rect.as_tuple()}  handle={result.window_handle}")
    if result.warning:
        print(f"     uyari: {result.warning}")
    if not result.window_handle:
        print(f"{NO} CGWindowID cozulemedi — vision geri dusumu bu pencerede calismaz")

    # --- 4. Budama --------------------------------------------------------
    print("\n[4] TreePruner  — AX rolleri budayicinin sozlugune ceviriliyor mu")
    snapshot = TreePruner(PruneConfig(max_nodes=150)).prune(result)
    print(f"{OK if snapshot.nodes else NO} {len(result.nodes)} -> {len(snapshot.nodes)} dugum")
    roles: dict[str, int] = {}
    for node in snapshot.nodes:
        roles[node.role] = roles.get(node.role, 0) + 1
    print(f"     roller: {dict(sorted(roles.items(), key=lambda kv: -kv[1])[:8])}")
    print("\n     ilk 10 dugum:")
    for node in snapshot.nodes[:10]:
        print(f"       {node.describe()[:80]}")

    if not snapshot.nodes:
        print(f"{NO} Budama sonrasi hicbir dugum kalmadi — rol eslemesi bozuk olabilir")
        return 1

    # --- 5. Gecikme -------------------------------------------------------
    if args.bench:
        print(f"\n[5] Gecikme olcumu ({args.bench} tur)")
        pruner = TreePruner(PruneConfig(max_nodes=150))
        extract_times, prune_times, counts = [], [], []
        for _ in range(args.bench):
            start = time.perf_counter()
            raw = extractor.extract()
            extract_times.append((time.perf_counter() - start) * 1000.0)
            start = time.perf_counter()
            snap = pruner.prune(raw)
            prune_times.append((time.perf_counter() - start) * 1000.0)
            counts.append(len(raw.nodes))
        print(f"     ham dugum   : ortalama {statistics.mean(counts):.0f}")
        print(f"     extract_ms  : ortanca {statistics.median(extract_times):.0f}  "
              f"min {min(extract_times):.0f}  max {max(extract_times):.0f}")
        print(f"     prune_ms    : ortanca {statistics.median(prune_times):.1f}")
        print("\n     NOT: macOS'ta AX, dugum basina ozniteliklik IPC turu atar;")
        print("     Windows UIA tek BuildUpdatedCache cagirir. Fark API bicimindendir.")

    # --- 6. Vision --------------------------------------------------------
    print("\n[6] Vision fallback  — yakalama + OCR")
    if not sc_ok:
        print(f"{NO} Ekran Kaydi izni yok, atlandi")
    elif not result.window_handle:
        print(f"{NO} pencere tutamaci yok, atlandi")
    else:
        from agent.vision.capture_mac import capture_window
        from agent.vision.fallback import extract_via_vision

        capture = capture_window(result.window_handle)
        print(f"{OK} yakalama {capture.image.width}x{capture.image.height} px  "
              f"olcek={capture.scale}")
        ocr = extract_via_vision(result.window_handle, "tr")
        print(f"{OK if ocr.nodes else NO} OCR: {len(ocr.nodes)} satir  "
              f"dil={ocr.language_used}  {ocr.ocr_ms:.0f} ms")
        if ocr.error:
            print(f"     uyari: {ocr.error}")
        disarida = [n for n in ocr.nodes
                    if n.rect.left < capture.origin.left - 5
                    or n.rect.right > capture.origin.right + 5
                    or n.rect.top < capture.origin.top - 5
                    or n.rect.bottom > capture.origin.bottom + 5]
        mark = OK if not disarida else NO
        print(f"{mark} koordinat kontrolu: {len(ocr.nodes) - len(disarida)}/"
              f"{len(ocr.nodes)} dikdortgen pencere icinde")
        if disarida:
            print("     (olcek veya dikey flip yanlis)")

    print("\n" + "=" * 68)
    print("Eylem testleri (click/type/scroll) gercek UI'yi DEGISTIRIR, bu yuzden")
    print("buraya konmadi. Elle denemek icin:")
    print("  .venv/bin/python -m agent.cli snapshot --wait 3")
    print("  .venv/bin/python -m agent.cli click <N> --dry-run")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AgentError as exc:
        print(f"\nHATA: {exc}")
        raise SystemExit(1) from exc
