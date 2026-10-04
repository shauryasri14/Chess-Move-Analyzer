# ♟ Chess Move Analyzer

**Free, offline chess game review that tells you what you did wrong, in plain language.**

Stockfish judges every move, code tags the cause, and a small local LLM (via Ollama) explains it. No account, no subscription, no game limit, and your games never leave your machine.

> Built for a friend who loves chess but doesn't play often enough to justify paying for game analysis.
> *Submission for the Hacktoberfest Weekend Challenge: Build for a Friend.*

<!-- Add a screenshot or GIF here, for example: -->
<!-- ![Game review screen](docs/screenshot-review.png) -->

---

## Why this exists

Casual players finish a game or a puzzle and want one thing: *what did I do wrong?* Many tools lock full analysis behind a paid plan, which doesn't make sense for someone who plays a few games a month.

This project gives that analysis for free, on your own computer:

- **Stockfish** (open-source engine) decides which moves were mistakes.
- **Code** finds the likely cause: a hung piece, a missed capture, or a missed mate.
- **A local LLM** turns those facts into a short explanation a beginner can follow.

## Features

**Game review**
- Upload or paste a PGN (works with Lichess and Chess.com exports)
- Board with arrows (your move vs. the engine's best move) and an evaluation bar
- Step through the game with `|<  <  >  >|`, or jump to the next mistake
- Clickable move list with colour dots: 🟡 inaccuracy, 🟠 mistake, 🔴 blunder
- Verdict card showing evaluation before and after, the best move, and the engine's line
- Plain-language "Why?" explanation for the move you are viewing
- Game summary with counts per mistake type and **recurring problems** (for example "7 hung pieces")

**Ask the coach**
- Chat box that automatically knows the current position, evaluations, engine line, and neighbouring moves
- Quick-question buttons ("Why was this a mistake?", "Explain this position like I'm a beginner")
- A "What the coach sees" panel that shows exactly what the model is given

**Puzzle mode**
- Enter a position (FEN) and your move. Stockfish checks it and the coach explains the difference

**Other**
- Classification thresholds are editable in the sidebar and re-label instantly (no engine rerun)
- Works without Ollama: you get template explanations instead of LLM ones
- Command-line mode for quick checks

## How it works

```
PGN → Stockfish evaluates each position → code classifies and tags mistakes → local LLM explains → summary
```

| Step | What happens | Who does it |
|---|---|---|
| 1. Evaluate | Each position is analyzed once; the evaluation after one move is reused as the evaluation before the next | Stockfish via `python-chess` |
| 2. Classify | Centipawn loss is turned into a label: best, excellent, good, inaccuracy, mistake, blunder | Code (`classify`) |
| 3. Tag | Likely cause: **missed mate**, **hung piece**, **missed capture**, or other | Code (`tag_move`) |
| 4. Explain | The model receives only these facts and explains them in 1 to 2 sentences | Local LLM (Ollama) |

**Key design rule:** the engine and code decide what is true, and the model only explains it. Small models misjudge chess positions, so the model is never asked to evaluate a position itself.

### Classification

Loss is measured in centipawns (1 centipawn = 1/100 of a pawn). Defaults:

| Label | Centipawns lost |
|---|---|
| Best | Engine's top move |
| Excellent | under 20 |
| Good | 20 to 59 |
| Inaccuracy | 60 to 149 |
| Mistake | 150 to 299 |
| Blunder | 300 or more |

Evaluations are clamped to ±15 pawns, so reducing a huge lead (+15 to +9) is not flagged as a blunder. Missing a forced mate counts as at least a mistake.

## Setup

### 1. Requirements
- Python 3.9 or newer
- [Stockfish](https://stockfishchess.org/download/)
- [Ollama](https://ollama.com) (optional, for LLM explanations)

### 2. Install

```bash
git clone https://github.com/shauryasri14/<repo-name>.git
cd <repo-name>
pip install -r requirements.txt
```

### 3. Install Stockfish

| System | Command |
|---|---|
| Linux | `sudo apt install stockfish` |
| macOS | `brew install stockfish` |
| Windows | Download from stockfishchess.org and put the `.exe` in the project folder (or note its full path) |

The app looks for Stockfish automatically. To set it yourself, use the `STOCKFISH_PATH` environment variable or the sidebar field.

### 4. (Optional) Pull a local model

```bash
ollama pull gemma3:4b
```

You can use any model Ollama supports. Change it in the sidebar.

### 5. Run

```bash
streamlit run app.py
```

## Usage

1. Open the **Game review** tab and upload a PGN (or paste one), then click **Analyze**.
2. Step through the game, or press **Next mistake**.
3. Read the **Why?** explanation, or ask the coach a question.
4. Expand **Game summary** to see your recurring problems.

**Command line**

```bash
python analyzer.py sample.pgn --color White          # analyze White's moves
python analyzer.py sample.pgn --depth 18 --no-llm    # deeper search, no Ollama
```

## Settings

| Setting | Default | Notes |
|---|---|---|
| Stockfish path | auto-detected | Or set `STOCKFISH_PATH` |
| Engine depth | 14 | 8 to 22. Higher is more accurate and slower |
| Ollama model | `gemma3:4b` | Any local Ollama model |
| Use local LLM | on | Off gives template explanations |
| Thresholds | 20 / 60 / 150 / 300 | Editable, re-labels instantly |

## Project structure

```
├── app.py            Streamlit UI (game review, board, summary, puzzle mode)
├── analyzer.py       Engine analysis, classification, tagging, explanations, summary, CLI
├── coach.py          "Ask the coach" backend: builds grounded context for the selected move
├── sample.pgn        Tiny test game
├── requirements.txt
└── README.md
```

## Limitations

- **Tags are heuristics.** Hung-piece and missed-capture checks are simple and can miss exchange sequences or pins. The tag is a hint, not a verdict.
- **Small models can still slip.** The model only sees engine facts and is told not to invent moves, but a 4B model may occasionally phrase things badly. Use "What the coach sees" to check its input.
- **Thresholds are a starting point.** Tune them on your own games.
- **Strength depends on depth.** Depth 14 is fast; use a higher depth for key positions.

## Privacy and security

- Analysis and explanations run locally. The app sends your games only to the Stockfish process and your local Ollama server.
- Streamlit may collect anonymous usage statistics by default. You can turn this off with `browser.gatherUsageStats = false` in `.streamlit/config.toml`.
- **This app is designed for local use.** The sidebar lets the user choose which program to run as the engine, so do not expose it on a public server as it is.

## Why open innovation matters

- **Stockfish is open source and the strongest engine available**, so top-level analysis is free.
- **No subscription or game limit.** That is the actual problem this solves for casual players.
- **The model runs locally.** Games and weaknesses stay on the player's machine, and the app works offline.
- **You can swap models and tune the coach** to the player's level, which a closed API wouldn't let you do as freely.

## Built for a friend

<!-- Fill this in after you hand it over. Judges like to see this. -->

- **Who:** [friend's name or "a friend from my college chess group"]
- **The problem they described:** [their words, for example "I can't pay for analysis just to play a few games a month"]
- **What they said after using it:** [their reaction]
- **What I changed because of their feedback:** [one or two changes]

## Credits

- [Stockfish](https://stockfishchess.org/): chess engine
- [python-chess](https://github.com/niklasf/python-chess): board, PGN, and engine communication
- [Ollama](https://ollama.com): local model runner
- [Streamlit](https://streamlit.io): UI

## License

Released under the GPL-3.0 license. See `LICENSE`.
