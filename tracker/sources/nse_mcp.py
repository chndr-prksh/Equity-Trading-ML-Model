"""NSE's official MCP servers (Streamable HTTP, no key): https://mcp.nseindia.in

They are built for assistants, not bulk download: history comes three months per call and
is unadjusted. What they add over the archive files is `get_corporate_actions`, which
returns NSE's own adjustment factor per event. We use that to audit the factors this
project derives itself, for the stocks with a fresh signal.
"""
import json
import logging
import time

import requests

log = logging.getLogger(__name__)
BHAVCOPY = "https://mcp.nseindia.in/bhavcopy/cm/mcp"
LIVE = "https://mcp.nseindia.in/cmmkt/mcp"
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class Client:
    def __init__(self, url):
        self.url, self.sid, self._id, self.http = url, None, 0, requests.Session()
        self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "ledgerline", "version": "1"}})
        self._rpc("notifications/initialized", notify=True)

    def _rpc(self, method, params=None, notify=False):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id
        headers = dict(_HEADERS, **({"Mcp-Session-Id": self.sid} if self.sid else {}))
        r = self.http.post(self.url, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        self.sid = r.headers.get("Mcp-Session-Id", self.sid)
        text = r.text
        if "text/event-stream" in r.headers.get("content-type", ""):
            text = "\n".join(line[5:].strip() for line in text.splitlines() if line.startswith("data:"))
        return json.loads(text) if text.strip() else None

    def tool(self, name, **args):
        res = self._rpc("tools/call", {"name": name, "arguments": args})
        if "error" in res or res["result"].get("isError"):
            raise RuntimeError(f"{name} failed: {json.dumps(res)[:200]}")
        return json.loads(res["result"]["content"][0]["text"])


MAX_SYMBOLS = 40      # the gateway blocks clients that query in bulk; stay far below that
PAUSE = 0.5


def corporate_action_factors(symbols, start, end):
    """{symbol: {ex_date: factor}} for price-rescaling actions (splits, bonuses).

    Sequential and capped on purpose. Stops at the first refusal; symbols not reached are omitted.
    """
    client = Client(BHAVCOPY)
    result = {}
    for sym in list(symbols)[:MAX_SYMBOLS]:
        try:
            data = client.tool("get_corporate_actions", symbol=sym, fromDate=str(start), toDate=str(end))
        except requests.HTTPError as e:
            log.warning("NSE MCP refused further calls (%s); audited %d symbols", e, len(result))
            break
        except Exception as e:
            log.debug("mcp corporate actions failed for %s: %s", sym, e)
            continue
        out = {}
        for a in data.get("actions", []):
            f = a.get("adjustmentFactor") or 1.0
            if abs(f - 1.0) > 1e-6:
                out[a["exDate"]] = out.get(a["exDate"], 1.0) * f
        result[sym] = out
        time.sleep(PAUSE)
    return result


def audit_adjustments(applied, symbols, start, end, first_bar=None):
    """Compare our applied factors with NSE's for `symbols`. Returns (disagreements, symbols checked).

    `first_bar` maps symbol -> ISO date of its first stored bar; older events cannot affect us.
    """
    official = corporate_action_factors(symbols, start, end)
    ours = {}
    for a in applied:
        ours.setdefault(a["symbol"], {})[a["date"]] = a["factor"]
    issues = []
    for sym, off in official.items():
        mine = ours.get(sym, {})
        for d, f in off.items():
            if d <= (first_bar or {}).get(sym, str(start)):
                continue
            if d not in mine:
                issues.append({"symbol": sym, "date": d, "nse": round(f, 4), "ours": None})
            elif abs(mine[d] / f - 1) > 0.02:
                issues.append({"symbol": sym, "date": d, "nse": round(f, 4), "ours": round(mine[d], 4)})
    return issues, len(official)
