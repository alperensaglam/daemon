"""Görev yürütme döngüleri.

``loop.AgentLoop`` düz (tek seviyeli) döngüdür: hedefi doğrudan modele verir ve
araç çağrılarını ``execution.router`` üzerinden yürütür. Planner/Worker/Critic
ayrımına sahip hiyerarşik döngü bunun üzerine kurulacak — ``AgentLoop`` bir
*alt hedefi* de aynı arayüzle çalıştırabildiği için (``run(goal)``) hiyerarşik
katman onu bir Worker olarak kullanabilir.
"""

from .loop import AgentLoop, LoopConfig, LoopResult, Step

__all__ = ["AgentLoop", "LoopConfig", "LoopResult", "Step"]
