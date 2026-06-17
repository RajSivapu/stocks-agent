import json, ssl, urllib.request, urllib.parse
from lib import config, db
ctx = ssl.create_default_context(); UA={"User-Agent":"Mozilla/5.0"}
def get(u,h=None,t=15):
    return urllib.request.urlopen(urllib.request.Request(u,headers=h or UA),timeout=t,context=ctx).read()
results = {}
try: db.init_schema(); results["postgres"]="ok"
except Exception as e: results["postgres"]=f"FAIL {e!r}"[:80]
try:
    k=config.secret("finnhub_api_key"); get(f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={k}"); results["finnhub"]="ok"
except Exception as e: results["finnhub"]=f"FAIL {e!r}"[:80]
try: get("https://query1.finance.yahoo.com/v8/finance/chart/VOO?range=1d&interval=1d"); results["yahoo"]="ok"
except Exception as e: results["yahoo"]=f"FAIL {e!r}"[:80]
try:
    tok=config.secret("telegram_bot_token"); chat=config.secret("telegram_chat_id")
    data=urllib.parse.urlencode({"chat_id":chat,"text":f"healthcheck: {json.dumps(results)}"}).encode()
    urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",data=data),timeout=15,context=ctx)
    results["telegram"]="ok"
except Exception as e: results["telegram"]=f"FAIL {e!r}"[:80]
print(json.dumps(results, indent=2))
