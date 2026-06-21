"""
Lógica de pontuação do Tiro de Guerra.

Regras (idênticas à planilha que está sendo substituída):

1) Tempo final da pista = tempo cronometrado + penalidades em segundos.
   - cada penalidade de "2"  soma  2s
   - cada penalidade de "5"  soma  5s
   - cada penalidade de "10" soma 10s
   - Desqualificação (DQ) => sem tempo válido => 0 pontos na etapa.

2) Pontos da etapa (dentro de cada CATEGORIA + MODALIDADE):
   - menor tempo da categoria  => 100 pontos
   - demais                    => (menor_tempo / seu_tempo) * 100
   - quem não tem tempo válido => 0 pontos

3) Resultado mensal (pistola e carabina):
   - soma das 3 MELHORES pontuações de etapa do mês.

4) Reinscrição / várias corridas na mesma etapa:
   - vale sempre a MELHOR (menor) marca de tempo final.

Estas funções são "puras" (não tocam no banco) para ficarem fáceis de testar.
"""

from typing import Dict, List, Optional


def final_time(raw_time: Optional[float], pen2: int = 0, pen5: int = 0,
               pen10: int = 0, dq: bool = False) -> Optional[float]:
    """Tempo final = tempo cru + penalidades (em segundos). DQ ou tempo vazio => None."""
    if dq or raw_time is None:
        return None
    return float(raw_time) + 2 * int(pen2) + 5 * int(pen5) + 10 * int(pen10)


def best_time(run_final_times: List[Optional[float]]) -> Optional[float]:
    """Melhor (menor) tempo final entre as corridas de um atirador numa etapa."""
    valid = [t for t in run_final_times if t is not None and t > 0]
    return min(valid) if valid else None


def stage_points(times_by_shooter: Dict[int, Optional[float]]) -> Dict[int, float]:
    """
    Recebe {shooter_id: melhor_tempo_final ou None} de UMA categoria numa etapa.
    Devolve {shooter_id: pontos} (0 a 100).
    """
    valid = [t for t in times_by_shooter.values() if t is not None and t > 0]
    if not valid:
        return {sid: 0.0 for sid in times_by_shooter}
    best = min(valid)
    points: Dict[int, float] = {}
    for sid, t in times_by_shooter.items():
        if t is None or t <= 0:
            points[sid] = 0.0
        elif t == best:
            points[sid] = 100.0
        else:
            points[sid] = best / t * 100.0
    return points


def monthly_score(stage_points_per_shooter: List[float], best_n: int = 3) -> float:
    """Soma das N melhores pontuações de etapa do mês (padrão: 3)."""
    ordered = sorted(stage_points_per_shooter, reverse=True)
    return float(sum(ordered[:best_n]))
