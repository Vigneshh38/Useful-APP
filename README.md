# Snap Answer

Point your phone at the game screen, tap **Snap**, and get the answer back on your phone.
Use it instead of taking screenshots in-game, which can make the game lag.

```
phone camera ──photo──▶ your PC (server.py) ──▶ Claude Code CLI ──answer──▶ phone
```

## Needs

- Windows PC with **Python 3.9+**
- **Claude Code** installed and logged in (`claude` works in a terminal), so no API key is needed
- Phone and PC on the **same Wi-Fi**

## Run

1. Double-click `start.bat`, or run `python server.py`.
2. It prints a link like `http://192.168.1.20:8765`. Open that link on your phone.
3. Tap **Snap**, take a photo of the screen, and the answer shows up in big text.

The first time, Windows Firewall may ask about Python. Allow it on **Private networks**, or your phone can't connect.

Tip: in your phone browser, use **Add to Home screen** so it opens like an app.

## Modes

| Mode  | Model  | Speed          |
|-------|--------|----------------|
| Fast  | Haiku  | about 8 s      |
| Smart | Opus   | slower, better for hard puzzles |

**Extra instructions** (optional) lets you tell Claude about the game, for example "answer in one word".

Photos are saved in `shots/`. That folder isn't uploaded to GitHub.

## Files

- `server.py`: local web server. It receives the photo, runs `claude -p`, and returns the answer.
- `index.html`: the phone page, with the camera button and answer display.
- `start.bat`: one-click start on Windows.

## Also included: SS Answer (PC-only version)

`ss_answer.py` runs on the PC by itself. Right-click 5 times fast to take a screenshot and get the answer. Hold Ctrl + Alt to see it.
Left-click 5 times fast to pick just part of the screen.

- `run.bat`: sets up `.venv` the first time (`mss`, `pynput`, `pillow`), then starts it.
- `start_hidden.bat` / `stop.bat`: run it in the background without a window, and stop it.
