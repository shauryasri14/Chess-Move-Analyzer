"""AI sidebar backend: builds grounded chess context for the selected move
and asks the local model. The model sees facts, never a raw guess-the-board prompt."""
from __future__ import annotations

import chess

from analyzer import fmt_eval

SYSTEM = """You are a friendly chess coach inside a game-review tool.
Answer ONLY from the CONTEXT below: the board, the engine's evaluations, and the engine's principal variation (PV).
Rules:
- Never invent moves, variations or evaluations that are not in the context.
- If asked about a line that is not in the PV, say you can only verify the engine's line,
  then speak in general ideas (development, king safety, material) without concrete move sequences.
- Keep answers short and in plain language. Explain any jargon.
- Always refer to the move the user is currently viewing unless they ask about another.

CONTEXT
{context}"""


def move_label(r) -> str:
    return f"{r.move_number}{'.' if r.color == 'White' else '...'} {r.played}"


def _movetext(results) -> str:
    parts = []
    for r in results:
        if r.color == "White":
            parts.append(f"{r.move_number}.")
        parts.append(r.played)
    return " ".join(parts)


def build_context(results, ply: int, headers: dict | None = None) -> str:
    """ply 0 = start position; ply k = position after move k (1-based)."""
    n = len(results)
    h = headers or {}
    lines = [f"Game: {h.get('White', '?')} (White) vs {h.get('Black', '?')} (Black), result {h.get('Result', '?')}",
             f"Full PGN moves: {_movetext(results)}", f"Total moves (plies): {n}"]
    if ply == 0:
        lines += ["Currently viewing: the starting position (no move played yet).",
                  f"FEN: {chess.STARTING_FEN}"]
        return "\n".join(lines)
    r = results[ply - 1]
    board = chess.Board(r.fen_after)
    lines += [
        f"Currently viewing: ply {ply}/{n}, move {move_label(r)} by {r.color}",
        f"FEN after the move: {r.fen_after}",
        f"FEN before the move: {r.fen_before}",
        "Board after the move (uppercase = White):", str(board),
        f"Move played: {r.played}",
        f"Classification: {r.label}",
        f"Engine's best move in the position before: {r.best}" + (" (the player found it)" if r.is_best else ""),
        f"Evaluation before the move (white POV): {fmt_eval(r.eval_before)}",
        f"Evaluation after the move (white POV): {fmt_eval(r.eval_after)}",
        f"Evaluation lost by the mover: {r.loss / 100:.2f} pawns",
        f"Engine principal variation from the position before the move: {' '.join(r.pv) or 'n/a'}",
    ]
    if r.tag_detail:
        lines.append(f"Problem found by code: {r.tag_detail}")
    if ply > 1:
        p = results[ply - 2]
        lines.append(f"Previous move: {move_label(p)} ({p.label})")
    if ply < n:
        nx = results[ply]
        lines.append(f"Next move: {move_label(nx)} ({nx.label}, engine preferred {nx.best})")
    return "\n".join(lines)


def ask(question: str, context: str, history: list, model: str = "gemma3:4b") -> str:
    msgs = [{"role": "system", "content": SYSTEM.format(context=context)}]
    for m in history[-8:]:
        content = m["content"]
        if m["role"] == "user" and m.get("move"):
            content = f"[asked while viewing {m['move']}] {content}"
        msgs.append({"role": m["role"], "content": content})
    msgs.append({"role": "user", "content": question})
    try:
        import ollama
        return ollama.chat(model=model, messages=msgs)["message"]["content"].strip()
    except Exception as e:
        return (f"I couldn't reach the local model ({type(e).__name__}). "
                "Make sure Ollama is running and the model is pulled.")
