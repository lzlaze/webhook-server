from flask import Flask, request, jsonify
import os, requests, anthropic
from datetime import datetime
import pytz

app = Flask(__name__)

GMAIL_USER      = os.environ.get("GMAIL_USER", "")
TO_EMAIL        = os.environ.get("TO_EMAIL", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "mytrading2024")
SENDGRID_KEY    = os.environ.get("SENDGRID_API_KEY", "")
ET = pytz.timezone("America/New_York")

def get_snapshot():
    snap = {}
    symbols = {"ES":"ES=F","YM":"YM=F","NQ":"NQ=F","VIX":"^VIX"}
    for name, sym in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=1d"
            r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
            data = r.json()
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if closes:
                price = closes[-1]
                pct = ((price - closes[0]) / closes[0]) * 100
                snap[name] = {"price": price, "pct": pct}
        except Exception:
            pass
    return snap

def generate_analysis(alert_data, snapshot):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    now_et = datetime.now(ET).strftime("%I:%M %p ET")
    snap_str = "\n".join([f"  {k}: ${v['price']:,.2f} ({v['pct']:+.2f}% from open)" for k,v in snapshot.items()]) or "  Unavailable"
    prompt = f"""Alert fired at {now_et}.
Instrument: {alert_data.get('ticker')}
Price: {alert_data.get('price')}
Level: {alert_data.get('level_name')}
Direction: {alert_data.get('direction')}
Note: {alert_data.get('note','')}

Live snapshot:
{snap_str}

Write 3-4 sentences: what just happened, is the setup still valid, are ES and YM confirming or diverging, what to watch next. Direct, no filler."""
    msg = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=300, messages=[{"role":"user","content":prompt}])
    return msg.content[0].text

def send_email(alert_data, analysis, snapshot):
    now_et = datetime.now(ET).strftime("%I:%M %p ET")
    ticker = alert_data.get("ticker","Alert")
    level  = alert_data.get("level_name","Key Level")
    price  = alert_data.get("price","")
    snap_html = "".join([
        f"<div style='display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #1e2330'>"
        f"<span style='color:#5a6480;font-family:monospace'>{k}</span>"
        f"<span style='color:{'#00d4a0' if v['pct']>=0 else '#ff4d6d'};font-family:monospace'>${v['price']:,.2f} ({v['pct']:+.2f}%)</span></div>"
        for k,v in snapshot.items()
    ])
    html = f"""<div style="background:#0a0c10;padding:24px;font-family:monospace;max-width:560px;margin:0 auto">
      <div style="color:#ff4d6d;font-size:11px;letter-spacing:0.15em;text-transform:uppercase;margin-bottom:6px">⚡ Alert Triggered</div>
      <h2 style="color:#eef2ff;font-size:20px;margin:0 0 4px">{ticker} — {level}</h2>
      <div style="color:#5a6480;font-size:12px;margin-bottom:20px">{price} · {now_et}</div>
      <div style="background:#111318;border:1px solid #1e2330;border-radius:8px;padding:18px;margin-bottom:16px;color:#c8d0e0;font-family:Georgia,serif;font-size:14px;line-height:1.8">{analysis.replace(chr(10),'<br>')}</div>
      <div style="background:#111318;border:1px solid #1e2330;border-radius:8px;padding:16px">
        <div style="color:#4d9fff;font-size:10px;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">Live Snapshot</div>
        {snap_html}
      </div>
      <div style="color:#3a4060;font-size:11px;text-align:center;margin-top:16px">Not financial advice.</div>
    </div>"""
    text = f"ALERT: {ticker} hit {level} at {price} — {now_et}\n\n{analysis}"
    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
        json={
            "personalizations": [{"to": [{"email": TO_EMAIL}]}],
            "from": {"email": GMAIL_USER},
            "subject": f"🚨 {ticker} hit {level} — {now_et}",
            "content": [{"type":"text/plain","value":text},{"type":"text/html","value":html}]
        },
        timeout=10
    )
    print(f"Email sent: {r.status_code}")

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        if not data: return jsonify({"error":"No JSON"}), 400
        if data.get("secret") != WEBHOOK_SECRET: return jsonify({"error":"Unauthorized"}), 401
        print(f"Alert received: {data}")
        snapshot = get_snapshot()
        analysis = generate_analysis(data, snapshot)
        send_email(data, analysis, snapshot)
        return jsonify({"status":"ok"}), 200
    except Exception as e:
        print(f"Error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"error":str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok","time":datetime.now(ET).strftime("%I:%M %p ET")}), 200

@app.route("/", methods=["GET"])
def index():
    return "Morning Report Webhook — Active", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
