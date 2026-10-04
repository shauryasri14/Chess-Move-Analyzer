"""Chess Move Analyzer core.

Stockfish decides what is wrong, code tags the cause,
and a small local LLM (via Ollama) only explains it.
"""
from __future__ import annotations

import io
import shutil
from collections import Counter
from dataclasses import dataclass, asdict, field
from typing import Callable, Optional

import chess
import chess.engine
import chess.pgn

MATE = 10000          # centipawn value used for mate scores
CAP = 1500            # evals are clamped so "+15 -> +9" is not a blunder
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
LABEL_ORDER = ["blunder", "mistake", "inaccuracy"]
BAD = ("inaccuracy", "mistake", "blunder")
GOOD = ("best", "excellent", "good")
TAG_MIN_LOSS = 30     # only tag causes for moves losing at least this much
PV_LEN = 6
# Centipawn-loss cutoffs (lower bounds), configurable from the UI:
#   loss < excellent -> Excellent, < inaccuracy -> Good, < mistake -> Inaccuracy,
#   < blunder -> Mistake, otherwise Blunder. The engine's own move is always "Best".
DEFAULT_THRESHOLDS = {"excellent": 20, "inaccuracy": 60, "mistake": 150, "blunder": 300}
_CACHE: dict = {}     # (position, depth) -> (white cp, best move, pv); never re-analyse a position
TAG_NAMES = {
    "missed_mate": "missed mates",
    "hung_piece": "hung pieces",
    "missed_capture": "missed captures",
    "other": "other / positional",
}


def find_stockfish() -> Optional[str]:
    """Look for a Stockfish executable: env var, PATH, project folder, then common folders."""
    import os
    env = os.environ.get("STOCKFISH_PATH")
    if env and os.path.isfile(env):
        return env
    on_path = shutil.which("stockfish") or shutil.which("stockfish.exe")
    if on_path:
        return on_path

    here = os.path.dirname(os.path.abspath(__file__))
    home = os.path.expanduser("~")
    roots = [here, os.getcwd(),
             os.path.join(home, "Downloads"), os.path.join(home, "Desktop"),
             os.path.join(home, "Documents"), "C:\\stockfish", "C:\\Stockfish",
             "C:\\Program Files", "C:\\Program Files (x86)", "D:\\stockfish",
             "/usr/games", "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin"]
    is_win = os.name == "nt"

    def is_engine(path: str) -> bool:
        name = os.path.basename(path).lower()
        if not name.startswith("stockfish"):
            return False
        if is_win:
            return name.endswith(".exe")
        return os.path.isfile(path) and os.access(path, os.X_OK) and "." not in name

    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= 3:   # keep the search shallow and quick
                dirnames[:] = []
            for f in sorted(filenames):
                p = os.path.join(dirpath, f)
                if is_engine(p):
                    return p
    return None


def classify(loss: float, is_best: bool = False, thresholds: Optional[dict] = None) -> str:
    t = thresholds or DEFAULT_THRESHOLDS
    if is_best or loss <= 0:
        return "best"
    if loss < t["excellent"]:
        return "excellent"
    if loss < t["inaccuracy"]:
        return "good"
    if loss < t["mistake"]:
        return "inaccuracy"
    if loss < t["blunder"]:
        return "mistake"
    return "blunder"


def fmt_eval(cp: int) -> str:
    """Format a white-POV centipawn score as +0.8 or #3 / -#3."""
    if abs(cp) >= MATE - 100:
        n = MATE - abs(cp)
        return f"{'' if cp > 0 else '-'}#{max(n, 1)}"
    return f"{cp / 100:+.1f}"


def game_headers(pgn_text: str) -> dict:
    g = chess.pgn.read_game(io.StringIO(pgn_text))
    return dict(g.headers) if g else {}


def relabel(results, thresholds: Optional[dict] = None):
    """Re-classify from stored losses. No engine calls needed."""
    from dataclasses import replace
    return [replace(r, label=classify(r.loss, r.is_best, thresholds)) for r in results]


@dataclass
class MoveResult:
    ply: int
    move_number: int
    color: str
    played: str
    best: str
    played_uci: str
    best_uci: str
    loss: int
    label: str
    fen_before: str
    fen_after: str
    tag: str = "other"
    tag_detail: str = ""
    explanation: str = ""
    is_best: bool = False
    eval_before: int = 0          # white POV centipawns, before the move
    eval_after: int = 0           # white POV centipawns, after the move
    pv: list = field(default_factory=list)   # engine line from the position before the move (SAN)

    def to_dict(self):
        return asdict(self)


# ---------- engine helpers ----------

def _evaluate(engine, board: chess.Board, depth: int):
    """Return (white-POV centipawns, best move or None, pv moves). Cached per position."""
    key = (board.epd(), depth)
    if key in _CACHE:
        return _CACHE[key]
    if board.is_checkmate():
        out = ((-MATE if board.turn == chess.WHITE else MATE), None, [])
    elif board.is_game_over():
        out = (0, None, [])
    else:
        info = engine.analyse(board, chess.engine.Limit(depth=depth))
        pv = list(info.get("pv") or [])[:PV_LEN]
        out = (info["score"].white().score(mate_score=MATE), pv[0] if pv else None, pv)
    _CACHE[key] = out
    return out


def _pv_san(board: chess.Board, pv) -> list:
    b, out = board.copy(), []
    for m in pv:
        out.append(b.san(m))
        b.push(m)
    return out


def _pov(white_cp: int, color: chess.Color) -> int:
    return white_cp if color == chess.WHITE else -white_cp


def _clamp(x: int) -> int:
    return max(-CAP, min(CAP, x))


# ---------- tagging (facts handed to the LLM) ----------

def hanging_pieces(board: chess.Board, color: chess.Color):
    """Squares of `color` pieces that are attacked and either undefended
    or attacked by a cheaper piece."""
    out = []
    for sq, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        attackers = board.attackers(not color, sq)
        if not attackers:
            continue
        defended = bool(board.attackers(color, sq))
        cheapest = min(VALUES[board.piece_type_at(a)] for a in attackers)
        if not defended or cheapest < VALUES[piece.piece_type]:
            out.append(sq)
    return out


def tag_move(board_before: chess.Board, move: chess.Move, best: Optional[chess.Move],
             ev_before: int, ev_after: int):
    """Return (tag, detail) describing why the move was bad."""
    mover = board_before.turn

    # Missed mate: the engine had a forced mate and the move gave it up.
    if ev_before >= MATE - 100 and ev_after < MATE - 100:
        return "missed_mate", "the engine had a forced checkmate available"

    after = board_before.copy()
    after.push(move)

    # Hung piece: something of ours can now be taken for free (or for a trade up).
    captured_value = 0
    if board_before.is_capture(move):
        victim = board_before.piece_type_at(move.to_square)
        captured_value = VALUES.get(victim, 1)  # en passant -> pawn
    hung = [sq for sq in hanging_pieces(after, mover)
            if VALUES[after.piece_type_at(sq)] > captured_value]
    if hung:
        sq = max(hung, key=lambda s: VALUES[after.piece_type_at(s)])
        name = chess.piece_name(after.piece_type_at(sq))
        return "hung_piece", f"the {name} on {chess.square_name(sq)} can be captured"

    # Missed capture: the best move wins material and the played move did not.
    if best and board_before.is_capture(best) and not board_before.is_capture(move):
        victim = board_before.piece_type_at(best.to_square) or chess.PAWN
        return "missed_capture", (
            f"{board_before.san(best)} captured a {chess.piece_name(victim)}")

    return "other", ""


# ---------- main entry points ----------

def analyze_game(pgn_text: str, engine_path: str = "stockfish", depth: int = 14,
                 progress: Optional[Callable[[int, int], None]] = None,
                 thresholds: Optional[dict] = None):
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None or not list(game.mainline_moves()):
        raise ValueError("No moves found in that PGN.")
    moves = list(game.mainline_moves())

    board = game.board()
    boards = [board.copy()]
    for m in moves:
        board.push(m)
        boards.append(board.copy())

    # One engine call per position; each "after" is the next "before".
    evals = []
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        for i, b in enumerate(boards):
            evals.append(_evaluate(engine, b, depth))
            if progress:
                progress(i + 1, len(boards))

    results = []
    for i, move in enumerate(moves):
        b = boards[i]
        mover = b.turn
        w_before, best, pv = evals[i]
        w_after, _, _ = evals[i + 1]
        ev_before, ev_after = _pov(w_before, mover), _pov(w_after, mover)
        loss = max(0, _clamp(ev_before) - _clamp(ev_after))
        if ev_before >= MATE - 100 and ev_after < MATE - 100:
            loss = max(loss, 150)  # missing a forced mate is at least a mistake
        if best is not None and move == best:
            loss = 0
        is_best = best is not None and move == best
        label = classify(loss, is_best, thresholds)
        tag, detail = ("other", "")
        if loss >= TAG_MIN_LOSS:
            tag, detail = tag_move(b, move, best, ev_before, ev_after)
        results.append(MoveResult(
            ply=i + 1, move_number=b.fullmove_number,
            color="White" if mover == chess.WHITE else "Black",
            played=b.san(move), best=b.san(best) if best else b.san(move),
            played_uci=move.uci(), best_uci=(best or move).uci(),
            loss=int(loss), label=label,
            fen_before=b.fen(), fen_after=boards[i + 1].fen(),
            tag=tag, tag_detail=detail, is_best=is_best,
            eval_before=w_before, eval_after=w_after, pv=_pv_san(b, pv)))
    return results


def analyze_single_move(fen: str, move_text: str, engine_path: str = "stockfish",
                        depth: int = 14) -> MoveResult:
    """Puzzle mode: check one move in one position."""
    board = chess.Board(fen)
    try:
        move = board.parse_san(move_text.strip())
    except ValueError:
        try:
            move = board.parse_uci(move_text.strip())
        except ValueError:
            raise ValueError(f"'{move_text}' is not a legal move in this position.")
    after = board.copy()
    after.push(move)
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        w_before, best, pv = _evaluate(engine, board, depth)
        w_after, _, _ = _evaluate(engine, after, depth)
    mover = board.turn
    ev_before, ev_after = _pov(w_before, mover), _pov(w_after, mover)
    loss = max(0, _clamp(ev_before) - _clamp(ev_after))
    if best is not None and move == best:
        loss = 0
    is_best = best is not None and move == best
    label = classify(loss, is_best)
    tag, detail = ("other", "")
    if loss >= TAG_MIN_LOSS:
        tag, detail = tag_move(board, move, best, ev_before, ev_after)
    return MoveResult(
        ply=1, move_number=board.fullmove_number,
        color="White" if mover == chess.WHITE else "Black",
        played=board.san(move), best=board.san(best) if best else board.san(move),
        played_uci=move.uci(), best_uci=(best or move).uci(),
        loss=int(loss), label=label, fen_before=board.fen(), fen_after=after.fen(),
        tag=tag, tag_detail=detail, is_best=is_best,
        eval_before=w_before, eval_after=w_after, pv=_pv_san(board, pv))


# ---------- explanation ----------

SYSTEM = """You are a friendly chess coach for a casual player.
Explain in 2 short sentences why the played move was a {label} and what the better move does.
Use only the facts given. Do not invent moves or variations.
Do not use jargon without explaining it."""

SYSTEM_GOOD = """You are a friendly chess coach for a casual player.
In 1-2 short sentences say why the played move is fine. Use only the facts given.
Do not invent moves or variations."""


def _facts(r: MoveResult) -> str:
    return (f"Move played: {r.played}\nClassification: {r.label}\n"
            f"Engine's best move: {r.best}\n"
            f"Evaluation (white POV): {fmt_eval(r.eval_before)} before, {fmt_eval(r.eval_after)} after\n"
            f"Evaluation drop: {r.loss / 100:.1f} pawns\n"
            f"Engine line: {' '.join(r.pv) or 'n/a'}\n"
            f"Problem found by code: {r.tag_detail or 'none specific'}")


def _fallback(r: MoveResult) -> str:
    if r.label in GOOD:
        base = ("is the engine's top choice" if r.is_best else
                f"is a {r.label} move (the engine slightly preferred {r.best})")
        return f"{r.played} {base}. The evaluation stays at {fmt_eval(r.eval_after)}."
    text = f"{r.played} lost about {r.loss / 100:.1f} pawns of advantage ({fmt_eval(r.eval_before)} to {fmt_eval(r.eval_after)}). {r.best} was stronger."
    return f"{r.played}: {r.tag_detail}. " + text if r.tag_detail else text


def explain(r: MoveResult, model: str = "gemma3:4b", use_llm: bool = True,
            llm_for_good: bool = False) -> str:
    """Plain-language explanation grounded in engine facts. Falls back to a template."""
    if not use_llm or (r.label in GOOD and not llm_for_good):
        return _fallback(r)
    system = SYSTEM.format(label=r.label) if r.label in BAD else SYSTEM_GOOD
    try:
        import ollama
        out = ollama.chat(model=model, messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": _facts(r)},
        ])
        return out["message"]["content"].strip()
    except Exception:
        return _fallback(r) + " (Ollama was not reachable, so this is a basic explanation.)"


# ---------- summary ----------

def summarize(results, color: Optional[str] = None):
    rs = [r for r in results if color in (None, "Both", r.color)]
    bad = [r for r in rs if r.label in BAD]
    labels = Counter(r.label for r in bad)
    tags = Counter(r.tag for r in bad if r.label in ("blunder", "mistake"))
    return {
        "moves": len(rs),
        "labels": {k: labels.get(k, 0) for k in LABEL_ORDER},
        "tags": {TAG_NAMES[k]: v for k, v in tags.most_common()},
        "classes": dict(Counter(r.label for r in rs)),
        "avg_loss": round(sum(r.loss for r in rs) / len(rs)) if rs else 0,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Analyze a PGN file from the command line.")
    p.add_argument("pgn")
    p.add_argument("--depth", type=int, default=14)
    p.add_argument("--engine", default=find_stockfish() or "stockfish")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--color", default="Both", choices=["White", "Black", "Both"])
    a = p.parse_args()
    res = analyze_game(open(a.pgn).read(), a.engine, a.depth)
    for r in res:
        if r.label in ("blunder", "mistake") and a.color in ("Both", r.color):
            print(f"\n{r.move_number}. {r.color} {r.played} ({r.label}, -{r.loss / 100:.1f})")
            print("  ", explain(r, use_llm=not a.no_llm))
    print("\nSummary:", summarize(res, a.color))
