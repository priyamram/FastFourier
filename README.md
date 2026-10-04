# Fast Fourier

A card-unlocked stock terminal for the Arduino UNO Q. Tap your card to open your Alpaca **paper trading** account, see your Portfolio Score (300–850), check simple price signals, and buy or sell from the touchscreen.

## What's in here

```
fast-fourier/
  linux/                 runs on the UNO Q's Linux (Qualcomm) side
    app.py               main program: web server, card sessions, trading
    alpaca_client.py     talks to Alpaca (paper trading only, by design)
    scoring.py           Portfolio Score math
    signals.py           moving averages + RSI for the trade panel
    autopilot.py         Autopilot: FFT + learned model + volatility sizing
    card_reader.py       receives card taps
    config.example.json  template for linking cards to accounts
    web/index.html       the touchscreen dashboard
  sketch/sketch.ino      runs on the STM32 side: reads the RC522 card reader
```

Install once on the board: `sudo apt install -y python3-requests python3-numpy` (numpy is only needed for Autopilot).

## 1. Copy the project to the board

On your **Mac**, in a regular Terminal window (prompt ends in `%`):

```bash
scp ~/Downloads/fast-fourier.zip arduino@BOARD_IP:~
```

Replace `BOARD_IP` with the board's address. To find it, run `hostname -I` in the board terminal and use the first number.

Then in the **board** terminal (prompt `arduino@Q`):

```bash
cd ~
python3 -m zipfile -e fast-fourier.zip .
cd ~/fast-fourier/linux
cp config.example.json config.json
nano config.json
```

Paste your paper key ID and secret into `config.json`, then save (Ctrl+O, Enter, Ctrl+X).

## 2. Run it

```bash
pkill -f alpaca_direct.py   # make sure the old test script isn't running
python3 app.py
```

On your Mac, open `http://BOARD_IP:8080` in a browser. On the lock screen, type `TEST-CARD` and press Unlock. Press Ctrl+C in the board terminal to stop.

## 3. Hook up the real card reader

1. Wire the RC522 as described at the top of `sketch/sketch.ino` (3.3 V only).
2. In Arduino App Lab, create an app, put `sketch.ino` in its sketch, add the **MFRC522** and **Arduino_RouterBridge** libraries, and put the files from `linux/` (including the `web` folder and `config.json`) into its Python folder with `app.py` as the main file.
3. Run the app and tap your card. The lock screen shows **"This card isn't linked… Card ID: XXXXXXXX"**.
4. In `config.json`, replace `"TEST-CARD"` with that ID and restart the app. Now your card unlocks your account.

## 4. Show it on the touchscreen

Plug the USB-C display into the board and open `http://localhost:8080` in the board's browser in full-screen mode. Once everything works, set `"dev_mode": false` in `config.json` to hide the typed card-ID box.

## How the Portfolio Score works

| Factor | Weight | How it's measured |
|---|---|---|
| Diversification | 35% | How evenly money is spread across holdings and sectors, and beta vs. the S&P 500 |
| Risk-adjusted return | 30% | Sharpe ratio of daily account returns over 3 months |
| Drawdown resilience | 20% | Largest drop from a peak (0% = full marks, 30%+ = zero) |
| Cash buffer | 15% | Cash share of the account (5–25% scores best) |

Each factor scores 0–100. The weighted total maps onto 300–850. Factors without enough history yet score a neutral 50, so a brand-new all-cash account starts around 575.

## Autopilot

Turn it on from the Autopilot panel. **Dry run** shows what it would buy and sell without placing orders. **Live** places paper orders by itself while the market is open.

Every few minutes, for each of the 15 watchlist stocks, it:

1. **Runs an FFT** on the last 64 days of prices to find the main cycle (8–32 days), where the stock sits in that cycle, and how much of the movement is noise. The gold line in each mini-chart is the price with the noise removed.
2. **Estimates the chance of rising** over the next 5 trading days with a logistic regression trained on 2 years of watchlist history: `P(up) = 1 / (1 + e^-(w·x + b))`.
3. **Sizes the position with a GARCH(1,1) volatility forecast**: `σ²ₜ = ω + α·ε²ₜ₋₁ + β·σ²ₜ₋₁`. Jumpier stocks get smaller positions.
4. **Applies guardrails**: sells at a 5% loss (stop-loss) or 10% gain (take-profit), at most 10% of the account per stock and 30% per sector, keeps 10% cash, at most 10 trades a day, and skips stocks that are mostly noise.

The panel shows the model's accuracy on past data it never trained on, next to the "always guess the same way" baseline. Expect numbers close together: markets are hard to predict, and the honest comparison is the point.

Autopilot only manages stocks on its watchlist (including ones you bought by hand), and it turns off when the terminal locks. To change any of this, add an `"autopilot"` section to `config.json`, e.g. `"autopilot": {"watchlist": ["AAPL", "MSFT"], "stop_on_lock": false}`. All settings are listed in `DEFAULTS` at the top of `autopilot.py`.

## Safety notes

- Trading is hard-coded to Alpaca's paper endpoint. No real money can move.
- Selling more shares than you own is blocked (no short selling).
- The terminal locks itself after 5 minutes without a touch.
- Card IDs can be copied by anyone with a cheap reader, so treat card unlock as a prototype, not real security. Keep `config.json` private, since it holds your keys.
