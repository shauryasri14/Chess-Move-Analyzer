import math
import os
import shutil

import chess
import chess.svg
import streamlit as st

import coach
from analyzer import (BAD, DEFAULT_THRESHOLDS, LABEL_ORDER, analyze_game, analyze_single_move,
                      explain, find_stockfish, fmt_eval, game_headers, relabel, summarize)

st.set_page_config(page_title="Chess Move Analyzer", page_icon="♟", layout="wide")

STYLE = {  # symbol, title, muted accent colour
    "best": ("★", "Best move", "#5a9e6f"), "excellent": ("", "Excellent", "#7aa874"),
    "good": ("", "Good", "#9aa38a"), "inaccuracy": ("?!", "Inaccuracy", "#d9b54a"),
    "mistake": ("?", "Mistake", "#e08a3c"), "blunder": ("??", "Blunder", "#d65050")}
DOT = {"inaccuracy": "🟡 ", "mistake": "🟠 ", "blunder": "🔴 "}
QUICK = ["Why was this a mistake?", "Why is the best move better?",
         "What did I miss here?", "Explain this position like I'm a beginner."]

st.markdown("""<style>
.verdict{border-left:4px solid var(--c);padding:.5rem .9rem;background:rgba(128,128,128,.08);border-radius:4px}
.verdict .big{font-size:1.25rem;font-weight:600}.verdict .sm{opacity:.7;font-size:.85rem}
</style>""", unsafe_allow_html=True)
st.title("♟ Chess Move Analyzer")

# ---------------- settings ----------------
with st.sidebar:
    st.header("Settings")
    engine_path = st.text_input("Stockfish path",
                                os.environ.get("STOCKFISH_PATH") or find_stockfish() or "stockfish")
    if os.path.isfile(engine_path) or shutil.which(engine_path):
        st.caption("✅ Stockfish found")
    else:
        st.warning("Stockfish not found. Put the .exe in this project folder or paste its full path.")
    depth = st.slider("Engine depth", 8, 22, 14, help="Higher is more accurate and slower.")
    model = st.text_input("Ollama model", "gemma3:4b")
    use_llm = st.checkbox("Use local LLM for explanations", True)
    color = st.radio("Summary / next-mistake for", ["Both", "White", "Black"])
    flip = st.checkbox("Flip board")
    with st.expander("Classification thresholds (centipawns lost)"):
        th = {k: st.number_input(k.title() + " from", 0, 2000, v, 5)
              for k, v in DEFAULT_THRESHOLDS.items()}
        st.caption("Excellent is below the first value, Good up to Inaccuracy. "
                   "Changing these re-labels instantly; no engine rerun.")


# ---------------- helpers ----------------
def board_svg(fen, arrows=(), lastmove=None, flipped=False, size=440):
    arr = [chess.svg.Arrow(chess.Move.from_uci(u).from_square, chess.Move.from_uci(u).to_square, color=c)
           for u, c in arrows]
    lm = chess.Move.from_uci(lastmove) if lastmove else None
    return chess.svg.board(chess.Board(fen), arrows=arr, lastmove=lm, size=size,
                           orientation=chess.BLACK if flipped else chess.WHITE)


def eval_bar(cp, height=440):
    white = 100 / (1 + math.exp(-max(-1500, min(1500, cp)) / 400))
    return (f"<div style='height:{height}px;width:26px;background:#3a3a3a;border-radius:3px;position:relative;"
            f"border:1px solid #666'><div style='position:absolute;bottom:0;width:100%;height:{white:.1f}%;"
            f"background:#f0f0f0;border-radius:0 0 3px 3px'></div></div>"
            f"<div style='font-size:.75rem;text-align:center;margin-top:4px'>{fmt_eval(cp)}</div>")


def go(ply):
    st.session_state.ply = max(0, min(ply, st.session_state.get("n", 0)))


def ask_quick(q):
    st.session_state.pending_q = q


def verdict_html(r):
    sym, title, col = STYLE[r.label]
    return (f"<div class='verdict' style='--c:{col}'><div class='sm'>Your move</div>"
            f"<div class='big'>{r.move_number}{'.' if r.color == 'White' else '...'} {r.played}{sym} "
            f"<span style='color:{col}'>{title}</span></div>"
            f"<div class='sm'>Best move: <b>{r.best}</b> &nbsp;|&nbsp; Eval: <b>{fmt_eval(r.eval_before)} → "
            f"{fmt_eval(r.eval_after)}</b> &nbsp;|&nbsp; Lost: {r.loss / 100:.2f}</div>"
            f"<div class='sm'>Engine line: {' '.join(r.pv) or 'n/a'}</div></div>")


tab_game, tab_puzzle = st.tabs(["Game review", "Puzzle mode"])

# ---------------- game review ----------------
with tab_game:
    ss = st.session_state
    with st.expander("Load a game", expanded="results" not in ss):
        up = st.file_uploader("Upload a PGN (Lichess or Chess.com export)", type=["pgn", "txt"])
        pgn_text = up.read().decode("utf-8", "ignore") if up else st.text_area("...or paste PGN", height=120)
        if st.button("Analyze", type="primary") and pgn_text.strip():
            bar = st.progress(0.0, text="Evaluating positions...")
            try:
                ss["raw"] = analyze_game(pgn_text, engine_path, depth,
                                         progress=lambda i, n: bar.progress(i / n))
                ss.update(headers=game_headers(pgn_text), ply=0, n=len(ss["raw"]), chat=[])
                ss["results"] = True
                for k in [k for k in ss if k.startswith("exp_")]:
                    del ss[k]
            except Exception as e:
                st.error(f"Analysis failed: {e}")
            bar.empty()

    if "raw" in ss:
        results = relabel(ss["raw"], th)          # cheap: derived from stored losses
        n, ply = len(results), ss.setdefault("ply", 0)
        h = ss.get("headers", {})
        st.caption(f"{h.get('White', '?')} vs {h.get('Black', '?')}  ·  {h.get('Result', '')}")
        r = results[ply - 1] if ply else None
        cp_now = r.eval_after if r else results[0].eval_before

        c_board, c_review, c_coach = st.columns([5.2, 4, 3.8], gap="medium")

        # ---- left: board + navigation ----
        with c_board:
            cb, cbd = st.columns([1, 14])
            arrows = []
            if r:
                arrows.append((r.played_uci, "#5d7fb5aa"))
                if r.label in BAD or (not r.is_best and r.loss >= th["inaccuracy"]):
                    arrows.append((r.best_uci, "#2e9e4faa"))
            fen = r.fen_after if r else results[0].fen_before
            cb.markdown(eval_bar(cp_now), unsafe_allow_html=True)
            cbd.image(board_svg(fen, arrows, r.played_uci if r else None, flip))
            nav = st.columns([1, 1, 1, 1, 3])
            nav[0].button("|<", on_click=go, args=(0,), disabled=ply == 0, use_container_width=True)
            nav[1].button("<", on_click=go, args=(ply - 1,), disabled=ply == 0, use_container_width=True)
            nav[2].button(">", on_click=go, args=(ply + 1,), disabled=ply == n, use_container_width=True)
            nav[3].button(">|", on_click=go, args=(n,), disabled=ply == n, use_container_width=True)
            nav[4].markdown(f"**Move {ply}/{n}**")
            st.progress(ply / n)
            nxt = next((i + 1 for i in range(ply, n) if results[i].label in ("mistake", "blunder")
                        and color in ("Both", results[i].color)), None)
            st.button("Next mistake ⏭", on_click=go, args=(nxt,), disabled=nxt is None)

        # ---- middle: verdict, move list, summary ----
        with c_review:
            if r:
                st.markdown(verdict_html(r), unsafe_allow_html=True)
                key = f"exp_{r.ply}_{r.label}_{model}_{use_llm}_{tuple(th.values())}"
                if key not in ss:
                    with st.spinner("Coach is thinking..."):
                        ss[key] = explain(r, model, use_llm)
                st.markdown("**Why?**")
                st.write(ss[key])
            else:
                st.info("Starting position. Press > to step through the game.")
            with st.container(height=330):
                for i in range(0, n, 2):
                    c0, c1, c2 = st.columns([1, 3, 3])
                    c0.markdown(f"**{i // 2 + 1}.**")
                    for j, col in ((i, c1), (i + 1, c2)):
                        if j < n:
                            m = results[j]
                            col.button(f"{DOT.get(m.label, '')}{m.played}{STYLE[m.label][0]}", key=f"mv{j}",
                                       type="primary" if ply == j + 1 else "secondary",
                                       on_click=go, args=(j + 1,), use_container_width=True)
            s = summarize(results, color)
            with st.expander("Game summary"):
                cols = st.columns(4)
                for col, k in zip(cols, LABEL_ORDER):
                    col.metric(k.title() + "s", s["labels"][k])
                cols[3].metric("Avg. loss (cp)", s["avg_loss"])
                if s["tags"]:
                    st.write("**Recurring problems:** " + ", ".join(f"{v} {k}" for k, v in s["tags"].items()))

        # ---- right: AI sidebar ----
        with c_coach:
            st.subheader("Ask the coach")
            ctx = coach.build_context(results, ply, h)
            st.caption("Viewing: " + (coach.move_label(r) if r else "starting position"))
            box = st.container(height=300)
            hist = ss.setdefault("chat", [])
            with box:
                for m in hist:
                    with st.chat_message(m["role"]):
                        if m["role"] == "user":
                            st.caption(m["move"])
                        st.write(m["content"])
            qcols = st.columns(2)
            for i, q in enumerate(QUICK):
                qcols[i % 2].button(q, key=f"q{i}", on_click=ask_quick, args=(q,), use_container_width=True)
            question = st.chat_input("Ask about this move...") or ss.pop("pending_q", None)
            if question:
                label = coach.move_label(r) if r else "start position"
                with box:
                    with st.chat_message("user"):
                        st.caption(label)
                        st.write(question)
                    with st.chat_message("assistant"):
                        with st.spinner("Thinking..."):
                            ans = coach.ask(question, ctx, hist, model)
                        st.write(ans)
                hist += [{"role": "user", "content": question, "move": label},
                         {"role": "assistant", "content": ans}]
            with st.expander("What the coach sees"):
                st.code(ctx, language="text")

# ---------------- puzzle mode ----------------
with tab_puzzle:
    st.write("Enter a position (FEN) and your move. Stockfish checks it and the coach explains.")
    fen = st.text_input("FEN", chess.STARTING_FEN)
    move_text = st.text_input("Your move (e.g. Nf3 or e2e4)")
    if st.button("Check my move") and move_text.strip():
        try:
            p = analyze_single_move(fen, move_text, engine_path, depth)
            p = relabel([p], th)[0]
            sym, title, col = STYLE[p.label]
            c1, c2 = st.columns(2)
            c1.image(board_svg(p.fen_before, [(p.played_uci, "#d63b3b"), (p.best_uci, "#2e9e4f")]))
            c2.markdown(verdict_html(p), unsafe_allow_html=True)
            c2.write(explain(p, model, use_llm))
        except Exception as e:
            st.error(str(e))
