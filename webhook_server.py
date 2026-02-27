#!/usr/bin/env python3
"""
TradingView Webhook Alert Server
Receives price alerts from TradingView, generates AI analysis, sends email notification.
Deploy this to Render.com (free tier) for 24/7 availability.
"""

from flask import Flask, request, jsonify
import os
import smtplib
import requests
import anthropic
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
import pytz
import yfinance as yf

app = Flask(__name__)

# ── CONFIG (set as environment variables on Render.com) ──
GMAIL_USER      = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS  = os.environ.get("GMAIL_APP_PASS", "")
TO_EMAIL        = os.environ.get("TO_EMAIL", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "mytrading123")  # set this in TradingView alert URL too

ET = pytz.timezone("America/New_York")

# ── LIVE MARKET SNAPSHOT ──────────────────────────────────────────────────────

def get_live_snapshot(triggered_ticker):
    """Pull a quick live snapshot of ES, YM, NQ, VIX at alert time."""
    tickers = {"ES": "ES=F", "YM": "YM=F", "NQ": "NQ=F", "VIX": "^VIX"}
    snapshot = {}
    for name, sym in tickers.items():
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="1d", interval="5m")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                open_p = float(hist["Open"].iloc[0])
                pct = ((price - open_p) / open_p) * 100
                snapshot[name] = {"price": price, "pct": pct}
        except Exception:
            pass
    return snapshot


# ── AI ALERT ANALYSIS ─────────────────────────────────────────────────────────

def generate_alert_analysis(alert_data, snapshot):
    """Generate AI analysis for the triggered alert."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    ticker     = alert_data.get("ticker", "Unknown")
    price      = alert_data.get("price", "Unknown")
    level_name = alert_data.get("level_name", "key level")
    direction  = alert_data.get("direction", "hit")
    note       = alert_data.get("note", "")
    now_et     = datetime.now(ET).strftime("%I:%M %p ET")

    snapshot_str = "\n".join([
        f"  {k}: ${v['price']:,.2f} ({v['pct']:+.2f}% from open)"
        for k, v in snapshot.items()
    ]) or "  Snapshot unavailable"

    prompt = f"""A TradingView price alert just fired for a futures trader at {now_et}.

ALERT DETAILS:
  Instrument: {ticker}
  Price: {price}
  Level hit: {level_name}
  Direction: {direction}
  Trader's note: {note or 'None'}

CURRENT MARKET SNAPSHOT:
{snapshot_str}

Write a sharp, 3-4 sentence alert analysis:
1. Confirm what just happened (which level, which instrument)
2. What it means for the trade — is the setup from this morning still valid? 
3. What ES and YM are doing RIGHT NOW relative to each other (diverging or confirming?)
4. One specific thing to watch next (next level, confirmation needed, or invalidation point)

Be direct. No filler. Write like a desk analyst texting a trader between positions."""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ── EMAIL NOTIFICATION ────────────────────────────────────────────────────────

def send_alert_email(alert_data, analysis, snapshot):
    """Send alert email with AI analysis."""
    ticker     = alert_data.get("ticker", "Alert")
    level_name = alert_data.get("level_name", "Key Level")
    price      = alert_data.get("price", "")
    now_et     = datetime.now(ET).strftime("%I:%M %p ET")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚨 {ticker} hit {level_name} — {now_et}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL

    snapshot_html = "".join([
        f"<div style='display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1e2330'>"
        f"<span style='font-family:monospace;color:#5a6480'>{k}</span>"
        f"<span style='font-family:monospace;color:{'#00d4a0' if v['pct']>=0 else '#ff4d6d'}'>"
        f"${v['price']:,.2f} ({v['pct']:+.2f}%)</span></div>"
        for k, v in snapshot.items()
    ])

    html = f"""
    <div style="background:#0a0c10;padding:24px;font-family:'Courier New',monospace;max-width:560px;margin:0 auto">
      <div style="color:#ff4d6d;font-size:11px;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:6px">⚡ Alert Triggered</div>
      <h2 style="color:#eef2ff;font-size:20px;margin:0 0 4px;font-family:monospace">{ticker} — {level_name}</h2>
      <div style="color:#5a6480;font-size:12px;margin-bottom:20px">{price} &nbsp;·&nbsp; {now_et}</div>

      <div style="background:#111318;border:1px solid #1e2330;border-radius:8px;padding:18px;margin-bottom:16px;color:#c8d0e0;font-family:Georgia,serif;font-size:14px;line-height:1.8">
        {analysis.replace(chr(10), '<br>')}
      </div>

      <div style="background:#111318;border:1px solid #1e2330;border-radius:8px;padding:16px;margin-bottom:16px">
        <div style="color:#4d9fff;font-size:10px;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">Live Snapshot</div>
        {snapshot_html}
      </div>

      <div style="color:#3a4060;font-size:11px;text-align:center">Not financial advice.</div>
    </div>"""

    text = f"ALERT: {ticker} hit {level_name} at {price} — {now_et}\n\n{analysis}"
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())


# ── WEBHOOK ENDPOINT ──────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Receive TradingView alert webhook.
    
    TradingView alert message format (JSON):
    {
      "secret": "mytrading123",
      "ticker": "ES1!",
      "price": "{{close}}",
      "level_name": "PDH 5412",
      "direction": "reclaimed above",
      "note": "Long setup trigger"
    }
    """
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        # Verify secret to prevent spam
        if data.get("secret") != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401

        print(f"Alert received: {data}")

        # Get live snapshot
        snapshot = get_live_snapshot(data.get("ticker", ""))

        # Generate AI analysis
        analysis = generate_alert_analysis(data, snapshot)

        # Send email
        send_alert_email(data, analysis, snapshot)

        return jsonify({"status": "ok", "message": "Alert sent"}), 200

    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(ET).strftime("%I:%M %p ET")}), 200


@app.route("/", methods=["GET"])
def index():
    return "Morning Report Webhook Server — Active", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
