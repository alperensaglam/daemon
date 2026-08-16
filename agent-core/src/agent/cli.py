"""Komut satiri arayuzu — LLM olmadan cekirdegi surmek icin.

LLM dongusu henuz yok; bu CLI onun yerini tutar. Insan `snapshot` alir,
`[@N]` gorur, `click 7` der. Ayni cagri yollari LLM eklendiginde degismeden
kullanilacak, dolayisiyla burada calisan sey orada da calisir.

Kullanim:
    python -m agent.cli snapshot
    python -m agent.cli snapshot --json --all
    python -m agent.cli click 7
    python -m agent.cli type 3 "merhaba" --yes
    python -m agent.cli key "ctrl+s"
    python -m agent.cli scroll down
    python -m agent.cli shell "git status"
    python -m agent.cli route "indirilenler klasorunu temizle"
    python -m agent.cli bench --runs 10
    python -m agent.cli windows
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

from .action.base import ActionExecutor
from .core.errors import AgentError
from .core.types import Snapshot
from .execution.router import classify
from .execution.shell import ShellConfig, ShellRunner
from .perception.base import UITreeExtractor, format_window_row
from .perception.pruner import PruneConfig, TreePruner
from .platform import create_backend, resolve_backend
from .safety import ApprovalGate
from .vision.fallback import extract_via_vision, should_fallback


# --------------------------------------------------------------------------- #
#  Kurulum
# --------------------------------------------------------------------------- #

def build(args) -> tuple[UITreeExtractor, TreePruner, ActionExecutor, ApprovalGate]:
    """Platformun arka ucunu kurar.

    Somut sinif adlari burada gecmez; secimi ``platform.create_backend`` yapar.
    Desteklenmeyen bir platformda veya eksik bagimlilikta ``BackendUnavailable``
    firlar ve main()'deki mevcut ``except AgentError`` onu tek satirlik bir
    hataya cevirir.
    """
    extractor, executor = create_backend(verify=not args.no_verify)
    pruner = TreePruner(PruneConfig(max_nodes=args.max_nodes))
    # Dogrulama yeniden budama yapar; ayni yapilandirmayi kullanmazsa
    # "once" ve "sonra" parmak izleri hep farkli cikar (bkz. common.BaseExecutor).
    executor.attach_pruner(pruner)
    mode = "dry_run" if args.dry_run else ("yes" if args.yes else "ask")
    return extractor, pruner, executor, ApprovalGate(mode)


def _merge_vision(snap: Snapshot, force: bool = False) -> None:
    """UIA dugumlerinin uzerine OCR dugumlerini ekler.

    Ayni ``UINode`` semasi kullanildigi icin ust katman (ve LLM) tek bir
    arayuz gorur; kaynagi ``snapshot.source`` alanindan anlasilir.
    """
    if not snap.window_handle:
        return
    result = extract_via_vision(snap.window_handle)
    if result.error:
        snap.warning = (snap.warning + " | " if snap.warning else "") + \
            f"Vision fallback: {result.error}"
        return
    if not result.nodes:
        return

    start = len(snap.nodes)
    for offset, ocr_node in enumerate(result.nodes, start=1):
        ocr_node.node_id = start + offset
        snap.nodes.append(ocr_node)
    snap.source = "uia+ocr" if start else "ocr"


def take_snapshot(extractor, pruner, hwnd=None, countdown: int = 0) -> Snapshot:
    """Anlik goruntu alir.

    ``countdown``, konsoldan calistirirken ise yarar: hedef pencereye gecmek
    icin sure tanir, yoksa daima terminalin kendisi yakalanir.
    """
    if countdown:
        for remaining in range(countdown, 0, -1):
            print(f"  {remaining}… hedef pencereye gecin", end="\r", file=sys.stderr)
            time.sleep(1)
        print(" " * 40, end="\r", file=sys.stderr)
    return pruner.prune(extractor.extract(hwnd))


# --------------------------------------------------------------------------- #
#  Komutlar
# --------------------------------------------------------------------------- #

def cmd_snapshot(args) -> int:
    extractor, pruner, _, _ = build(args)
    snap = take_snapshot(extractor, pruner, args.hwnd, args.wait)

    # Erisilebilirlik agaci bos gelen pencerelerde (oyun, canvas) OCR'a dus.
    if args.vision or should_fallback(len(snap.nodes)):
        _merge_vision(snap, force=args.vision)

    if args.json:
        print(json.dumps(snap.to_state_dict(), ensure_ascii=False, indent=2))
        return 0

    stats = snap.stats()
    print(f"pencere : {snap.active_window}")
    print(f"surec   : {stats['process']}  (hwnd={snap.window_handle})")
    print(f"dugum   : {stats['raw_nodes']} ham -> {stats['pruned_nodes']} budanmis")
    print(f"sure    : extract {stats['extract_ms']}ms + prune {stats['prune_ms']}ms")
    payload = json.dumps(snap.to_state_dict(), ensure_ascii=False)
    print(f"durum   : {len(payload)} karakter (~{len(payload)//4} token)")
    print()

    limit = len(snap.nodes) if args.all else min(len(snap.nodes), 40)
    for node in snap.nodes[:limit]:
        value = f"  = {node.value[:45]!r}" if node.value else ""
        marker = "*" if node.focused else " "
        print(f"{marker}[@{node.node_id:>3}] {node.role:<14} {node.name[:52]!r}{value}")
    if limit < len(snap.nodes):
        print(f"\n… {len(snap.nodes) - limit} dugum daha (--all ile hepsini gorun)")
    return 0


def cmd_click(args) -> int:
    extractor, pruner, executor, gate = build(args)
    snap = take_snapshot(extractor, pruner, args.hwnd, args.wait)
    node = snap.by_id(args.node_id)
    if node is None:
        print(f"HATA: [@{args.node_id}] bulunamadi. Once 'snapshot' calistirin.")
        return 1

    allowed, reason = gate.check("click", node)
    if not allowed:
        print(f"atlandi: {reason}")
        return 0 if gate.is_dry_run else 2

    result = executor.click(snap, args.node_id)
    return _report(result)


def cmd_type(args) -> int:
    extractor, pruner, executor, gate = build(args)
    snap = take_snapshot(extractor, pruner, args.hwnd, args.wait)
    node = snap.by_id(args.node_id)
    if node is None:
        print(f"HATA: [@{args.node_id}] bulunamadi.")
        return 1

    allowed, reason = gate.check("type_text", node, args.text)
    if not allowed:
        print(f"atlandi: {reason}")
        return 0 if gate.is_dry_run else 2

    result = executor.type_text(snap, args.node_id, args.text,
                                clear_first=not args.append)
    return _report(result)


def cmd_key(args) -> int:
    extractor, pruner, executor, gate = build(args)
    allowed, reason = gate.check("press_key", None, args.keys)
    if not allowed:
        print(f"atlandi: {reason}")
        return 0 if gate.is_dry_run else 2

    # Hedef pencere biliniyorsa tus ancak o pencere on plandayken gonderilir;
    # aksi halde kullanicinin baska penceresine gidebilir.
    snapshot = None
    if args.hwnd or args.target:
        snapshot = take_snapshot(extractor, pruner, args.hwnd, args.wait)
    return _report(executor.press_key(args.keys, snapshot))


def cmd_scroll(args) -> int:
    extractor, pruner, executor, gate = build(args)
    snap = take_snapshot(extractor, pruner, args.hwnd, args.wait)
    allowed, reason = gate.check("scroll", None, args.direction)
    if not allowed:
        print(f"atlandi: {reason}")
        return 0 if gate.is_dry_run else 2
    return _report(executor.scroll(snap, args.direction, args.amount, args.node_id))


def cmd_windows(args) -> int:
    """Gorunur ust duzey pencereleri listeler — pencere tutamaci bulmak icin."""
    extractor, *_ = build(args)
    rows = extractor.list_windows()
    for window in rows:
        print(format_window_row(window))
    print(f"\n{len(rows)} pencere  (* = on planda, _ = simge durumunda)")
    return 0


def cmd_shell(args) -> int:
    """Kabuk seridini surer — hibrit yurutmenin CLI tarafi.

    UI arka ucunu KURMAZ: bir kabuk komutu icin comtypes/pyobjc gerekmez ve
    bunlarin kurulu olmadigi bir makinede de bu komut calisir.
    """
    runner = ShellRunner(ShellConfig(timeout=args.timeout, cwd=args.cwd))
    mode = "dry_run" if args.dry_run else ("yes" if args.yes else "ask")
    gate = ApprovalGate(mode)

    allowed, reason = gate.check("run_shell", None, args.command)
    if not allowed:
        print(f"atlandi: {reason}")
        return 0 if gate.is_dry_run else 2

    try:
        result = runner.run(args.command)
    except AgentError as exc:
        print(f"ENGELLENDI: {exc}", file=sys.stderr)
        return 3

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.error:
        print(f"HATA: {result.error}", file=sys.stderr)
    print(f"[{result.shell}] cikis={result.exit_code} "
          f"sure={result.elapsed_ms:.0f}ms", file=sys.stderr)
    return result.exit_code if result.exit_code else 0


def cmd_route(args) -> int:
    """Bir hedef metninin hangi seride dustugunu gosterir (teshis)."""
    decision = classify(args.text)
    print(f"serit    : {decision.lane.value}")
    print(f"guven    : {decision.confidence}")
    print(f"puanlar  : {decision.scores}")
    print(f"gerekce  : {', '.join(decision.reasons)}")
    print()
    print(decision.hint())
    return 0


def cmd_bench(args) -> int:
    """Gecikme olcumu — 'dusuk gecikmeli' iddiasinin kaniti."""
    extractor, pruner, _, _ = build(args)
    if args.wait:
        take_snapshot(extractor, pruner, args.hwnd, args.wait)

    extract_times: list[float] = []
    prune_times: list[float] = []
    cold: tuple[float, float, int] | None = None
    snap = None

    for i in range(args.runs):
        raw = extractor.extract(args.hwnd)
        snap = pruner.prune(raw)
        # Ilk tur "soguk": Chromium gibi uygulamalar erisilebilirlik agacini
        # tembel acar, ilk sorgu belirgin yavas olur. Ortalamaya katilirsa
        # sonucu yaniltir; ayri raporlanir.
        if i == 0 and args.runs > 1:
            cold = (raw.extract_ms, snap.prune_ms, len(snap.nodes))
            continue
        extract_times.append(raw.extract_ms)
        prune_times.append(snap.prune_ms)

    if not extract_times or snap is None:
        print("Yeterli tur yok (--runs >= 2 verin).")
        return 1

    state_json = json.dumps(snap.to_state_dict(), ensure_ascii=False)

    print(f"pencere: {snap.active_window[:60]}")
    print(f"surec  : {snap.process_name}")
    if cold is not None:
        print(f"soguk tur: extract {cold[0]:.1f}ms, prune {cold[1]:.1f}ms, "
              f"{cold[2]} dugum")
    print(f"sicak turlar: {len(extract_times)}")
    print()
    _stat("extract (ms)", extract_times)
    _stat("prune   (ms)", prune_times)
    _stat("TOPLAM  (ms)", [e + p for e, p in zip(extract_times, prune_times)])
    print()
    print(f"ham dugum    : {snap.raw_node_count}")
    print(f"budanmis     : {len(snap.nodes)}  "
          f"(%{100 * len(snap.nodes) / max(snap.raw_node_count, 1):.1f})")
    print(f"durum JSON   : {len(state_json)} karakter (~{len(state_json) // 4} token)")
    return 0


def _stat(label: str, values: list[float]) -> None:
    print(f"  {label}: ort {statistics.mean(values):6.1f} | "
          f"ortanca {statistics.median(values):6.1f} | "
          f"min {min(values):6.1f} | maks {max(values):6.1f}")


def _report(result) -> int:
    payload = result.to_dict()
    if result.ok:
        changed = payload.get("ui_changed")
        suffix = ""
        if changed is False:
            suffix = "  (UYARI: UI degismedi — eylem etkisiz kalmis olabilir)"
        elif changed is True:
            suffix = "  (UI degisti)"
        print(f"OK  {result.action} -> {result.method}  "
              f"{result.elapsed_ms:.0f}ms{suffix}")
        if result.detail:
            print(f"    {result.detail}")
        return 0
    print(f"BASARISIZ  {result.action}: {result.error}")
    return 1


# --------------------------------------------------------------------------- #
#  Ayristirici
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent.cli",
        description="Erisilebilirlik agaci tabanli masaustu agent — cekirdek CLI",
    )
    # Tutamacin adi platforma gore degisir (HWND / CGWindowID); bayrak adi
    # geriye donuk uyumluluk icin --hwnd kaldi.
    try:
        handle_label = resolve_backend().handle_label
    except AgentError:
        handle_label = "pencere tutamaci"
    parser.add_argument("--hwnd", type=int, default=None, metavar=handle_label.upper(),
                        help=f"Hedef pencere tutamaci / {handle_label} "
                             "(varsayilan: aktif pencere)")
    parser.add_argument("--wait", type=int, default=0, metavar="SN",
                        help="Islemden once N saniye bekle (hedef pencereye gecmek icin)")
    parser.add_argument("--max-nodes", type=int, default=150,
                        help="Durumdaki azami dugum sayisi (varsayilan 150)")
    parser.add_argument("--yes", action="store_true",
                        help="Siradan eylemleri sormadan yap (yuksek riskliler yine sorulur)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Hicbir eylemi uygulama, sadece ne yapilacagini yaz")
    parser.add_argument("--no-verify", action="store_true",
                        help="Eylem sonrasi 'UI degisti mi' kontrolunu atla (daha hizli)")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("snapshot", help="Aktif pencerenin durum JSON'unu al")
    p.add_argument("--json", action="store_true", help="Ham JSON yaz")
    p.add_argument("--all", action="store_true", help="Tum dugumleri listele")
    p.add_argument("--vision", action="store_true",
                   help="UIA agaci dolu olsa bile OCR dugumlerini de ekle")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("click", help="Bir dugume tikla")
    p.add_argument("node_id", type=int)
    p.set_defaults(func=cmd_click)

    p = sub.add_parser("type", help="Bir dugume metin yaz")
    p.add_argument("node_id", type=int)
    p.add_argument("text")
    p.add_argument("--append", action="store_true", help="Mevcut icerigi silme")
    p.set_defaults(func=cmd_type)

    p = sub.add_parser("key", help="Tus kombinasyonu gonder")
    p.add_argument("keys", help='ornek: "ctrl+s", "enter", "alt+f4"')
    p.add_argument("--target", action="store_true",
                   help="Aktif pencereyi hedef al ve on planda oldugunu dogrula "
                        "(tusun baska uygulamaya gitmesini onler)")
    p.set_defaults(func=cmd_key)

    p = sub.add_parser("scroll", help="Kaydir")
    p.add_argument("direction", choices=["up", "down", "left", "right"])
    p.add_argument("--amount", type=int, default=3)
    p.add_argument("--node-id", type=int, default=None)
    p.set_defaults(func=cmd_scroll)

    p = sub.add_parser("windows", help="Gorunur pencereleri listele")
    p.set_defaults(func=cmd_windows)

    p = sub.add_parser("shell", help="Kabuk komutu calistir (hibrit yurutme)")
    p.add_argument("command", help='ornek: "git status"')
    p.add_argument("--cwd", default=None, help="Calisma dizini")
    p.add_argument("--timeout", type=float, default=20.0, help="Saniye")
    p.set_defaults(func=cmd_shell)

    p = sub.add_parser("route", help="Bir hedef hangi seride dusuyor (teshis)")
    p.add_argument("text", help='ornek: "indirilenler klasorunu temizle"')
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("bench", help="Gecikme olcumu")
    p.add_argument("--runs", type=int, default=10)
    p.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AgentError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\niptal edildi", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
