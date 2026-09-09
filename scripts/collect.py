#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Buyback-Korea data collector.

Sources
  * DART Open API (https://opendart.fss.or.kr)  - disclosure list, treasury-stock decisions, original documents
  * Naver Finance                                - daily prices, shares outstanding, sector

Outputs (all under data/)
  latest.json  - everything the web page needs
  status.json  - last run summary (shown in the page header)
  state.json   - collector checkpoint (scanned ranges, cached profiles). Safe to delete; it will be rebuilt.

Design notes
  * Idempotent: events are keyed by DART receipt number (rcept_no). Re-running never duplicates.
  * Time budget: the run stops gracefully when the budget is exhausted and continues next time.
  * Fail-safe: when a host is down the previous data is kept and the page keeps working.
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
import zipfile
from collections import defaultdict

import requests

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SLEEP = 0.08

DART_BASE = "https://opendart.fss.or.kr/api/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NV_HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ko-KR,ko;q=0.9",
              "Referer": "https://finance.naver.com/"}
DART_HEADERS = {"User-Agent": UA, "Accept": "*/*"}

# event types
ACQ_DIRECT, ACQ_TRUST, TRUST_CANCEL, CANCEL, DISPOSAL, VALUEUP = (
    "acq_direct", "acq_trust", "trust_cancel", "cancel", "disposal", "valueup")

DETAIL_API = {
    ACQ_DIRECT: "tsstkAqDecsn.json",
    ACQ_TRUST: "tsstkAqTrctrCnsDecsn.json",
    TRUST_CANCEL: "tsstkAqTrctrCcDecsn.json",
    DISPOSAL: "tsstkDpDecsn.json",
}

T0 = time.time()


def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


def elapsed_min():
    return (time.time() - T0) / 60.0


def to_int(v):
    if v is None:
        return None
    s = re.sub(r"[^0-9\-]", "", str(v))
    if s in ("", "-"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def to_float(v):
    if v is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(v))
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def norm_date(v):
    """'2026-01-21', '2026.01.21', '20260121', '2026년 01월 21일' -> '2026-01-21'"""
    if not v:
        return None
    d = re.findall(r"\d+", str(v))
    if len(d) >= 3 and len(d[0]) == 4:
        try:
            return dt.date(int(d[0]), int(d[1]), int(d[2])).isoformat()
        except ValueError:
            return None
    s = re.sub(r"[^0-9]", "", str(v))
    if len(s) == 8:
        try:
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:])).isoformat()
        except ValueError:
            return None
    return None


def ymd(d):
    return d.strftime("%Y%m%d")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------------------------
class Http:
    """requests wrapper with per-host fail-fast so that a dead host cannot eat the whole budget."""

    def __init__(self):
        self.calls = defaultdict(int)
        self.fails = defaultdict(int)
        self.lat = defaultdict(list)

    def degraded(self, host):
        if self.fails[host] >= 5:
            return True
        lat = self.lat[host]
        return len(lat) >= 6 and sum(lat[-6:]) / 6 > 15

    def get(self, url, headers=None, params=None, tries=3, mode="json", timeout=30):
        host = url.split("/")[2]
        if self.degraded(host) and self.calls[host] % 10 != 0:
            tries, timeout = 1, 12
        last = None
        for i in range(tries):
            t0 = time.time()
            try:
                self.calls[host] += 1
                r = requests.get(url, headers=headers or {}, params=params, timeout=timeout)
                if r.status_code != 200:
                    raise RuntimeError("HTTP %s" % r.status_code)
                if mode == "json":
                    out = r.json()
                elif mode == "bytes":
                    out = r.content
                else:
                    out = r.text
                self.fails[host] = 0
                self.lat[host].append(time.time() - t0)
                time.sleep(SLEEP)
                return out
            except Exception as e:  # noqa
                last = e
                self.lat[host].append(time.time() - t0)
                time.sleep(1.5 * (i + 1))
        self.fails[host] += 1
        if self.fails[host] == 5:
            log("host degraded (fail-fast mode):", host)
        raise RuntimeError("GET failed %s %s: %s" % (url, params, last))


class DartLimit(Exception):
    """Daily quota exhausted or key problem: stop calling DART for this run."""


class Dart:
    def __init__(self, http, key):
        self.h = http
        self.key = key
        self.calls = 0
        self.blocked = False

    def _check(self, js):
        st = str(js.get("status", "000"))
        if st in ("000", "013"):
            return
        if st in ("010", "011", "012", "020", "800", "901"):
            self.blocked = True
            raise DartLimit("DART status %s: %s" % (st, js.get("message")))
        raise RuntimeError("DART status %s: %s" % (st, js.get("message")))

    def get(self, endpoint, **params):
        if self.blocked:
            raise DartLimit("DART blocked earlier in this run")
        params["crtfc_key"] = self.key
        self.calls += 1
        js = self.h.get(DART_BASE + endpoint, DART_HEADERS, params=params)
        if isinstance(js, dict):
            self._check(js)
        return js

    def list_range(self, bgn, end, pblntf_ty, detail_ty=None, depth=0):
        """All list rows for a date range. Splits the range when it is too big for paging."""
        rows = []
        base = dict(bgn_de=bgn, end_de=end, pblntf_ty=pblntf_ty, page_count=100, sort="date", sort_mth="asc")
        if detail_ty:
            base["pblntf_detail_ty"] = detail_ty
        first = self.get("list.json", page_no=1, **base)
        total = int(first.get("total_count") or 0)
        pages = int(first.get("total_page") or 0)
        if total > 9000 and bgn != end and depth < 6:
            b = dt.datetime.strptime(bgn, "%Y%m%d").date()
            e = dt.datetime.strptime(end, "%Y%m%d").date()
            mid = b + (e - b) / 2
            return (self.list_range(bgn, ymd(mid), pblntf_ty, detail_ty, depth + 1) +
                    self.list_range(ymd(mid + dt.timedelta(days=1)), end, pblntf_ty, detail_ty, depth + 1))
        rows.extend(first.get("list") or [])
        for p in range(2, pages + 1):
            js = self.get("list.json", page_no=p, **base)
            rows.extend(js.get("list") or [])
        return rows

    def detail(self, etype, corp_code, bgn, end):
        js = self.get(DETAIL_API[etype], corp_code=corp_code, bgn_de=bgn, end_de=end)
        return js.get("list") or []

    def document_text(self, rcept_no):
        """Plain text of the original disclosure document (tags stripped)."""
        if self.blocked:
            raise DartLimit("DART blocked earlier in this run")
        self.calls += 1
        raw = self.h.get(DART_BASE + "document.xml", DART_HEADERS,
                         params={"crtfc_key": self.key, "rcept_no": rcept_no}, mode="bytes")
        if raw[:2] != b"PK":
            # error payload (xml/json)
            txt = raw.decode("utf-8", "ignore")
            m = re.search(r"<status>(\d+)</status>", txt) or re.search(r'"status"\s*:\s*"(\d+)"', txt)
            if m:
                self._check({"status": m.group(1), "message": txt[:200]})
            raise RuntimeError("document.xml: unexpected payload for %s" % rcept_no)
        zf = zipfile.ZipFile(io.BytesIO(raw))
        texts = []
        for name in zf.namelist():
            data = zf.read(name)
            for enc in ("utf-8", "cp949", "euc-kr"):
                try:
                    texts.append(data.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue
        return html_to_text("\n".join(texts))


def viewer_text(h, rcept_no):
    """Fallback: read the disclosure body through the DART web viewer (no login needed)."""
    main = h.get("https://dart.fss.or.kr/dsaf001/main.do", {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
                 params={"rcpNo": rcept_no}, mode="text")
    nodes = defaultdict(dict)
    for var, k, v in re.findall(r"(node\d+)\['(\w+)'\]\s*=\s*\"([^\"]*)\"", main):
        nodes[var][k] = v
    if not nodes:
        raise RuntimeError("viewer: no document nodes for %s" % rcept_no)
    pick = None
    for var in sorted(nodes):
        n = nodes[var]
        if n.get("dcmNo") and n.get("eleId"):
            pick = n
            if "소각" in (n.get("text") or ""):
                break
    if not pick:
        raise RuntimeError("viewer: no viewable node for %s" % rcept_no)
    html = h.get("https://dart.fss.or.kr/report/viewer.do", {"User-Agent": UA, "Referer": "https://dart.fss.or.kr/"},
                 params={"rcpNo": rcept_no, "dcmNo": pick["dcmNo"], "eleId": pick["eleId"],
                         "offset": pick.get("offset", ""), "length": pick.get("length", ""),
                         "dtd": pick.get("dtd", "HTML")}, mode="text")
    return html_to_text(html)


def html_to_text(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(td|th|tr|p|div|li|te|tu|table)>", " \n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
         .replace("&quot;", '"').replace("&#39;", "'"))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


# ----------------------------------------------------------------------------------------------
# Naver Finance
# ----------------------------------------------------------------------------------------------
def naver_history(h, symbol, start, end):
    """Daily close prices {YYYY-MM-DD: close}. Works for stocks (6 digits) and indices (KOSPI, KOSDAQ)."""
    txt = h.get("https://api.finance.naver.com/siseJson.naver", NV_HEADERS,
                params={"symbol": symbol, "requestType": "1", "startTime": ymd(start), "endTime": ymd(end),
                        "timeframe": "day"}, mode="text")
    txt = txt.strip().replace("'", '"')
    txt = re.sub(r",\s*([\]\}])", r"\1", txt)
    rows = json.loads(txt)
    out = {}
    for r in rows[1:]:
        if len(r) < 5:
            continue
        d = norm_date(r[0])
        c = to_float(r[4])
        if d and c:
            out[d] = c
    return out


def naver_profile(h, code):
    """Shares outstanding and sector from the PC stock page."""
    html = h.get("https://finance.naver.com/item/main.naver", {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
                 params={"code": code}, mode="text")
    shares = None
    m = re.search(r"상장주식수\s*</th>\s*<td>\s*<em>\s*([\d,]+)", html, flags=re.S)
    if m:
        shares = to_int(m.group(1))
    sector = None
    m = re.search(r'type=upjong&(?:amp;)?no=\d+"[^>]*>\s*([^<]+?)\s*<', html)
    if m:
        sector = re.sub(r"\s+", " ", m.group(1)).strip()
    name = None
    m = re.search(r"<title>\s*([^:<]+?)\s*:", html)
    if m:
        name = m.group(1).strip()
    return {"shares": shares, "sector": sector, "name": name}


# ----------------------------------------------------------------------------------------------
# Classification / parsing
# ----------------------------------------------------------------------------------------------
RE_ACQ = re.compile(r"자기\s*주식\s*취득\s*결정")
RE_TRUST = re.compile(r"자기\s*주식\s*취득\s*신탁\s*계약\s*체결\s*결정")
RE_TRUST_CC = re.compile(r"자기\s*주식\s*취득\s*신탁\s*계약\s*해지\s*결정")
RE_DISP = re.compile(r"자기\s*주식\s*처분\s*결정")
# KIND form title is "주식소각결정" (sometimes written 자기주식소각결정) - accept both.
RE_CANCEL = re.compile(r"(?:자기\s*)?주식\s*소각\s*결정")
RE_VALUEUP = re.compile(r"기업\s*가치\s*제고\s*계획|밸류업")
# bump when classification rules change so that already-scanned months are scanned again
SCAN_VERSION = 2


def classify(report_nm, pblntf_ty):
    n = report_nm or ""
    if pblntf_ty == "B":
        if RE_TRUST_CC.search(n):
            return TRUST_CANCEL
        if RE_TRUST.search(n):
            return ACQ_TRUST
        if RE_ACQ.search(n):
            return ACQ_DIRECT
        if RE_DISP.search(n):
            return DISPOSAL
        return None
    if RE_CANCEL.search(n):
        return CANCEL
    if RE_VALUEUP.search(n):
        return VALUEUP
    return None


def name_flags(report_nm):
    n = report_nm or ""
    return {
        "amended": "정정" in n,
        "withdrawn": "철회" in n or "취소" in n,
    }


def market_of(corp_cls):
    return {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(corp_cls or "", "기타")


def parse_cancel_text(text):
    """Parse the KIND '자기주식 소각 결정' form from plain text."""
    t = re.sub(r"\s+", " ", text)
    out = {}

    def grab(pattern, conv=None):
        m = re.search(pattern, t)
        if not m:
            return None
        v = m.group(1).strip()
        return conv(v) if conv else v

    sep = r"[\s|:：]*"
    kind = r"소각\s*할\s*주식\s*의\s*종류\s*와\s*수"
    out["shares"] = grab(kind + r".*?보통\s*주식\s*\(?주\)?" + sep + r"([\d,]{1,20})", to_int)
    out["shares_other"] = grab(kind + r".*?(?:기타|종류)\s*주식\s*\(?주\)?" + sep + r"([\d,]{1,20})", to_int)
    out["total_shares"] = grab(r"발행\s*주식\s*총\s*수.*?보통\s*주식\s*\(?주\)?" + sep + r"([\d,]{1,20})", to_int)
    out["amount"] = grab(r"소각\s*예정\s*금액\s*\(?원\)?" + sep + r"([\d,]{1,25})", to_int)
    out["cancel_date"] = grab(r"소각\s*예정\s*일" + sep + r"(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2})", norm_date)
    out["method"] = grab(r"소각\s*할\s*주식\s*의\s*취득\s*방법" + sep + r"(.+?)\s*[|]?\s*(?:\d{1,2}\s*\.\s*)?소각\s*(?:목적|사유)")
    out["purpose"] = grab(r"소각\s*(?:목적|사유)" + sep + r"(.+?)\s*[|]?\s*(?:\d{1,2}\s*\.\s*)?이사회\s*결의일")
    out["board_date"] = grab(r"이사회\s*결의일\s*\(?\s*결정일\s*\)?" + sep + r"(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2})",
                             norm_date)
    for k in ("method", "purpose"):
        if out.get(k):
            out[k] = re.sub(r"^[-:：|\s]+|[|\s]+$", "", out[k])[:120]
    return out


def sum_int(*vals):
    vals = [v for v in (to_int(x) for x in vals) if v is not None]
    return sum(vals) if vals else None


def attach_detail(ev, rec):
    """Fill event fields from a DART structured record."""
    t = ev["type"]
    if t == ACQ_DIRECT:
        ev["amount"] = sum_int(rec.get("aqpln_prc_ostk"), rec.get("aqpln_prc_estk"))
        ev["shares"] = sum_int(rec.get("aqpln_stk_ostk"), rec.get("aqpln_stk_estk"))
        ev["period_start"] = norm_date(rec.get("aqexpd_bgd"))
        ev["period_end"] = norm_date(rec.get("aqexpd_edd"))
        ev["purpose"] = (rec.get("aq_pp") or "").strip()[:120] or None
        ev["method"] = (rec.get("aq_mth") or "").strip()[:80] or None
        ev["broker"] = (rec.get("cs_iv_bk") or "").strip()[:60] or None
        ev["decision_date"] = norm_date(rec.get("aq_dd") or rec.get("bddd"))
        ev["held_pct"] = _held_pct(rec)
    elif t == ACQ_TRUST:
        ev["amount"] = to_int(rec.get("ctr_prc"))
        ev["period_start"] = norm_date(rec.get("ctr_pd_bgd"))
        ev["period_end"] = norm_date(rec.get("ctr_pd_edd"))
        ev["purpose"] = (rec.get("ctr_pp") or "").strip()[:120] or None
        ev["method"] = "신탁계약"
        ev["broker"] = (rec.get("ctr_cns_int") or rec.get("cs_iv_bk") or "").strip()[:60] or None
        ev["decision_date"] = norm_date(rec.get("bddd"))
        ev["held_pct"] = _held_pct(rec)
    elif t == TRUST_CANCEL:
        # fields: ctr_prc_bfcc (계약금액 해지 전), ctr_prc_atcc (해지 후), ctr_pd_bfcc_bgd/edd, cc_pp, cc_int, cc_prd
        ev["amount"] = to_int(rec.get("ctr_prc_bfcc") or rec.get("ctr_prc"))
        ev["amount_after"] = to_int(rec.get("ctr_prc_atcc"))
        ev["period_start"] = norm_date(rec.get("ctr_pd_bfcc_bgd") or rec.get("ctr_pd_bgd"))
        ev["period_end"] = norm_date(rec.get("cc_prd") or rec.get("ctr_pd_bfcc_edd") or rec.get("ctr_pd_edd"))
        ev["purpose"] = (rec.get("cc_pp") or rec.get("ctr_pp") or "").strip()[:120] or None
        ev["method"] = "신탁계약 해지" + (" (일부)" if ev.get("amount_after") else "")
        ev["broker"] = (rec.get("cc_int") or rec.get("ctr_cns_int") or "").strip()[:60] or None
        ev["decision_date"] = norm_date(rec.get("bddd"))
        ev["held_pct"] = _held_pct(rec)
    elif t == DISPOSAL:
        ev["amount"] = sum_int(rec.get("dppln_prc_ostk"), rec.get("dppln_prc_estk"))
        ev["shares"] = sum_int(rec.get("dppln_stk_ostk"), rec.get("dppln_stk_estk"))
        ev["period_start"] = norm_date(rec.get("dpprpd_bgd"))
        ev["period_end"] = norm_date(rec.get("dpprpd_edd"))
        ev["purpose"] = (rec.get("dp_pp") or "").strip()[:120] or None
        methods = []
        for k, label in (("dp_m_mkt", "시장매도"), ("dp_m_ovtm", "시간외대량매매"), ("dp_m_otc", "장외처분"),
                         ("dp_m_etc", "기타")):
            v = to_int(rec.get(k)) or to_int(rec.get(k + "_ostk"))
            if v:
                methods.append(label)
        ev["method"] = ", ".join(methods) or None
        ev["broker"] = (rec.get("cs_iv_bk") or "").strip()[:60] or None
        ev["decision_date"] = norm_date(rec.get("dp_dd") or rec.get("bddd"))
        ev["held_pct"] = _held_pct(rec)
    ev["detail_done"] = True


def _held_pct(rec):
    a = to_float(rec.get("aq_wtn_div_ostk_rt"))
    b = to_float(rec.get("eaq_ostk_rt"))
    if a is None and b is None:
        return None
    return round((a or 0) + (b or 0), 2)


# ----------------------------------------------------------------------------------------------
# Returns
# ----------------------------------------------------------------------------------------------
def compute_returns(ev, px, idx):
    """px/idx: {date: close}. Base = last close strictly before the disclosure date."""
    if not px or not idx:
        return None
    dates = sorted(px)
    d0 = ev["date"]
    before = [d for d in dates if d < d0]
    if not before:
        return None
    base_d = before[-1]
    after = [d for d in dates if d > d0]
    base = px[base_d]
    ibase = _idx_close(idx, base_d)
    if not base or not ibase:
        return None

    def ret(d):
        p = px.get(d)
        i = _idx_close(idx, d)
        if not p or not i:
            return None, None
        return round((p / base - 1) * 100, 2), round(((p / base) - (i / ibase)) * 100, 2)

    out = {"base_date": base_d, "base": base}
    for key, n in (("1", 1), ("5", 5), ("20", 20)):
        if len(after) >= n:
            r, x = ret(after[n - 1])
            out["r" + key], out["x" + key] = r, x
        else:
            out["r" + key], out["x" + key] = None, None
    last = dates[-1]
    r, x = ret(last)
    out["rnow"], out["xnow"], out["now_date"] = r, x, last
    out["days"] = len(after)
    return out


def _idx_close(idx, d):
    if d in idx:
        return idx[d]
    prev = [k for k in idx if k <= d]
    return idx[max(prev)] if prev else None


# ----------------------------------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------------------------------
def full_months(since, today):
    """Complete calendar months before the current month, newest first: [(key, bgn, end), ...]."""
    out = []
    cur = dt.date(today.year, today.month, 1) - dt.timedelta(days=1)  # last day of previous month
    cur = cur.replace(day=1)
    while cur >= dt.date(since.year, since.month, 1):
        nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        out.append((cur.strftime("%Y-%m"), ymd(max(cur, since)), ymd(nxt - dt.timedelta(days=1))))
        cur = (cur - dt.timedelta(days=1)).replace(day=1)
    return out


def scan_rows(dart, bgn, end, events):
    """Scan DART for one range; add/refresh events. Returns number of new events."""
    new = 0
    for ty in ("B", "I"):
        rows = dart.list_range(bgn, end, ty)
        for r in rows:
            et = classify(r.get("report_nm"), ty)
            if not et:
                continue
            code = (r.get("stock_code") or "").strip()
            if not code:
                continue  # unlisted filer
            rno = r.get("rcept_no")
            if not rno:
                continue
            if rno in events:
                continue
            fl = name_flags(r.get("report_nm"))
            events[rno] = {
                "id": rno, "type": et, "date": norm_date(r.get("rcept_dt")), "corp": (r.get("corp_name") or "").strip(),
                "code": code, "corp_code": r.get("corp_code"), "market": market_of(r.get("corp_cls")),
                "report": (r.get("report_nm") or "").strip(), "amended": fl["amended"], "withdrawn": fl["withdrawn"],
                "amount": None, "shares": None, "detail_done": et == VALUEUP,
                "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=%s" % rno,
            }
            if et == VALUEUP:
                n = r.get("report_nm") or ""
                events[rno]["valueup_kind"] = "예고" if "예고" in n else ("이행현황" if "이행" in n else "본공시")
            new += 1
    return new


def fill_details(dart, events, budget_ok):
    """Structured details for acquisition / trust / disposal events."""
    todo = defaultdict(list)
    for ev in events.values():
        if ev["type"] in DETAIL_API and not ev.get("detail_done") and not ev.get("detail_failed", 0) >= 6:
            todo[(ev["corp_code"], ev["type"])].append(ev)
    log("detail groups:", len(todo))
    done = 0
    mismatch_logged = 0
    for (corp_code, et), evs in todo.items():
        if not budget_ok():
            log("budget exhausted during details")
            break
        ds = sorted(e["date"] for e in evs if e["date"])
        if not ds:
            continue
        bgn = ymd(dt.date.fromisoformat(ds[0]) - dt.timedelta(days=1))
        end = ymd(dt.date.fromisoformat(ds[-1]) + dt.timedelta(days=1))
        try:
            recs = dart.detail(et, corp_code, bgn, end)
        except DartLimit:
            raise
        except Exception as e:  # noqa
            log("detail failed", corp_code, et, e)
            for ev in evs:
                ev["detail_failed"] = ev.get("detail_failed", 0) + 1
                ev["err"] = ("api: %s" % e)[:160]
            continue
        by_rno = {str(r.get("rcept_no") or ""): r for r in recs}
        by_date = defaultdict(list)
        for r in recs:
            by_date[str(r.get("rcept_no") or "")[:8]].append(r)
        for ev in evs:
            rec = by_rno.get(ev["id"])
            if not rec:
                # fallback: a single record filed on the same day (structured data sometimes keys differently)
                same_day = by_date.get(ev["id"][:8]) or []
                if len(same_day) == 1:
                    rec = same_day[0]
            if not rec and ev.get("amended"):
                # amendment: fall back to the most recent earlier record (the original filing) within 90 days
                lo = ymd(dt.date.fromisoformat(ev["date"]) - dt.timedelta(days=90)) if ev["date"] else "0"
                earlier = sorted(k for k in by_rno if lo <= k[:8] < ev["id"][:8] or (k[:8] == ev["id"][:8] and k < ev["id"]))
                if earlier:
                    rec = by_rno[earlier[-1]]
                    ev["detail_from_original"] = True
            if rec:
                attach_detail(ev, rec)
                ev.pop("err", None)
                done += 1
            else:
                ev["detail_failed"] = ev.get("detail_failed", 0) + 1
                ev["err"] = "no matching record (api returned %d: %s)" % (
                    len(recs), ",".join(sorted(by_rno))[:80])
                if mismatch_logged < 15:
                    log("detail mismatch", ev["corp"], et, ev["id"], ev["report"], "->", ev["err"])
                    mismatch_logged += 1
    log("details attached:", done)


def fill_cancels(dart, events, budget_ok):
    todo = [e for e in events.values() if e["type"] == CANCEL and not e.get("detail_done")
            and e.get("detail_failed", 0) < 4]
    todo.sort(key=lambda e: e["date"] or "", reverse=True)  # newest first: most useful if the budget runs out
    log("cancel documents to parse:", len(todo))
    ok = 0
    for ev in todo:
        if not budget_ok():
            log("budget exhausted during cancel documents")
            break
        p = {}
        try:
            p = parse_cancel_text(dart.document_text(ev["id"]))
        except DartLimit:
            raise
        except Exception as e:  # noqa
            log("document.xml failed", ev["id"], e)
        if p.get("shares") is None and p.get("amount") is None:
            try:
                p = parse_cancel_text(viewer_text(dart.h, ev["id"]))
            except Exception as e:  # noqa
                log("viewer failed", ev["id"], e)
        if p.get("shares") is None and p.get("amount") is None:
            ev["detail_failed"] = ev.get("detail_failed", 0) + 1
            continue
        ev["shares"] = sum_int(p.get("shares"), p.get("shares_other"))
        ev["amount"] = p.get("amount")
        ev["total_shares"] = p.get("total_shares")
        ev["period_end"] = p.get("cancel_date")
        ev["method"] = p.get("method")
        ev["purpose"] = p.get("purpose")
        ev["decision_date"] = p.get("board_date")
        ev["detail_done"] = True
        ok += 1
    log("cancel documents parsed:", ok)


def update_prices(h, events, companies, since, today, budget_ok, skip=False):
    """Profiles (shares, sector) + daily closes -> returns and ratios."""
    if skip:
        log("prices skipped by flag")
        return {}
    # Order matters: the stage can run out of budget, so do the codes that still lack returns first,
    # then the ones whose latest event is newest (their D+1/5/20 windows are still filling in).
    need, rest = set(), set()
    newest = {}
    for e in events.values():
        code = e["code"]
        newest[code] = max(newest.get(code, ""), e["date"] or "")
        (need if not e.get("ret") else rest).add(code)
    rest -= need
    codes = (sorted(need, key=lambda c: newest.get(c, ""), reverse=True) +
             sorted(rest, key=lambda c: newest.get(c, ""), reverse=True))
    log("price codes: %d (missing returns first: %d)" % (len(codes), len(need)))
    idx = {}
    for sym in ("KOSPI", "KOSDAQ"):
        try:
            idx[sym] = naver_history(h, sym, since - dt.timedelta(days=40), today)
            log("index", sym, len(idx[sym]), "days")
        except Exception as e:  # noqa
            log("index fetch failed", sym, e)
    if not idx.get("KOSPI"):
        log("no index data - returns not updated this run")
        return {}
    stale_before = (today - dt.timedelta(days=30)).isoformat()
    n_hist = 0
    by_code = defaultdict(list)
    for e in events.values():
        by_code[e["code"]].append(e)
    for code in codes:
        if not budget_ok():
            log("budget exhausted during prices")
            break
        c = companies.setdefault(code, {})
        if not c.get("shares") or (c.get("profile_date") or "") < stale_before:
            try:
                p = naver_profile(h, code)
                if p.get("shares"):
                    c.update({"shares": p["shares"], "sector": p.get("sector"), "profile_date": today.isoformat()})
                    if p.get("name"):
                        c["name"] = p["name"]
            except Exception as e:  # noqa
                log("profile failed", code, e)
        try:
            evs = by_code[code]
            first = min(dt.date.fromisoformat(e["date"]) for e in evs if e["date"])
            px = naver_history(h, code, first - dt.timedelta(days=20), today)
            n_hist += 1
        except Exception as e:  # noqa
            log("history failed", code, e)
            continue
        if px:
            last_d = max(px)
            c["last_close"], c["last_date"] = px[last_d], last_d
        for ev in evs:
            ix = idx.get("KOSPI" if ev["market"] == "KOSPI" else "KOSDAQ") or idx["KOSPI"]
            r = compute_returns(ev, px, ix)
            if r:
                ev["ret"] = r
    log("price histories fetched:", n_hist, "/", len(codes))
    return idx


def finalize(events, companies):
    """Ratios and derived fields."""
    for ev in events.values():
        c = companies.get(ev["code"], {})
        shares_out = c.get("shares") or ev.get("total_shares")
        base = (ev.get("ret") or {}).get("base") or c.get("last_close")
        ev["sector"] = c.get("sector")
        ev["pct_shares"] = None
        ev["pct_mcap"] = None
        if ev.get("shares") and shares_out:
            ev["pct_shares"] = round(ev["shares"] / shares_out * 100, 3)
        if ev.get("amount") and shares_out and base:
            ev["pct_mcap"] = round(ev["amount"] / (shares_out * base) * 100, 3)
        if ev["type"] == ACQ_TRUST and ev.get("amount") and base and shares_out and ev.get("shares") is None:
            ev["pct_shares"] = round(ev["amount"] / base / shares_out * 100, 3)
            ev["shares_est"] = True
        if ev["type"] == CANCEL and ev.get("total_shares") and ev.get("shares"):
            ev["pct_shares"] = round(ev["shares"] / ev["total_shares"] * 100, 3)
        if ev.get("amount") is None and ev.get("shares") and base and ev["type"] == CANCEL:
            ev["amount_est"] = int(ev["shares"] * base)


def count_by(items, key):
    out = defaultdict(int)
    for it in items:
        out[key(it)] += 1
    return out


def build_latest(events, companies, today, status):
    evs = sorted(events.values(), key=lambda e: (e["date"] or "", e["id"]), reverse=True)
    monthly = defaultdict(lambda: {"acq": 0, "acq_n": 0, "cancel": 0, "cancel_n": 0, "disp": 0, "disp_n": 0,
                                   "valueup_n": 0})
    for e in evs:
        if not e["date"] or e.get("amended") or e.get("withdrawn"):
            continue
        m = e["date"][:7]
        amt = e.get("amount") or e.get("amount_est") or 0
        if e["type"] in (ACQ_DIRECT, ACQ_TRUST):
            monthly[m]["acq"] += amt
            monthly[m]["acq_n"] += 1
        elif e["type"] == CANCEL:
            monthly[m]["cancel"] += amt
            monthly[m]["cancel_n"] += 1
        elif e["type"] == DISPOSAL:
            monthly[m]["disp"] += amt
            monthly[m]["disp_n"] += 1
        elif e["type"] == VALUEUP:
            monthly[m]["valueup_n"] += 1
    slim_companies = {k: {"name": v.get("name"), "shares": v.get("shares"), "sector": v.get("sector"),
                          "last_close": v.get("last_close"), "last_date": v.get("last_date")}
                      for k, v in companies.items()}
    out_events = []
    for e in evs:
        o = {k: v for k, v in e.items() if k not in ("detail_failed", "detail_done", "corp_code")}
        out_events.append(o)
    return {
        "sample": False,
        "asof": today.isoformat(),
        "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "since": min((e["date"] for e in evs if e["date"]), default=None),
        "count": len(out_events),
        "monthly": [dict(month=k, **v) for k, v in sorted(monthly.items())],
        "companies": slim_companies,
        "events": out_events,
        "status": status,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20240101", help="history start (YYYYMMDD)")
    ap.add_argument("--lookback-days", type=int, default=4, help="always rescan this many recent days")
    ap.add_argument("--budget-min", type=float, default=140.0, help="stop DART/Naver calls after this many minutes")
    ap.add_argument("--max-months", type=int, default=40, help="max history months to scan per run")
    ap.add_argument("--no-prices", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        log("ERROR: DART_API_KEY is not set. Add it under Settings > Secrets and variables > Actions.")
        sys.exit(1)

    today = dt.datetime.now(KST).date()
    since = dt.datetime.strptime(args.since, "%Y%m%d").date()
    state_path = os.path.join(DATA, "state.json")
    state = load_json(state_path, {"scanned": [], "events": {}, "companies": {}})
    events = state.get("events") or {}
    companies = state.get("companies") or {}
    scanned = set(x for x in (state.get("scanned") or []) if isinstance(x, str))
    if state.get("scan_version") != SCAN_VERSION:
        log("classification rules changed (scan_version %s -> %s): history will be scanned again"
            % (state.get("scan_version"), SCAN_VERSION))
        scanned = set()
        for ev in events.values():  # give previously failed items a fresh chance under the new rules
            ev.pop("detail_failed", None)
            if ev["type"] == TRUST_CANCEL:  # field mapping fixed in v2 -> fetch again
                ev["detail_done"] = False
    log("state loaded: events=%d companies=%d scanned=%d" % (len(events), len(companies), len(scanned)))

    h = Http()
    dart = Dart(h, key)
    # Each stage gets its own slice of the budget so that a slow early stage can never starve the later
    # ones. Fractions are cumulative deadlines: history scan 30%, details 55%, cancel documents 80%, prices 100%.
    B = args.budget_min
    stage = lambda frac: (lambda: elapsed_min() < B * frac)  # noqa
    budget_ok = stage(1.0)
    log("budget %.0f min -> scan %.0f / details %.0f / cancels %.0f / prices %.0f"
        % (B, B * .30, B * .55, B * .80, B))
    status = {"ok": True, "message": "", "started": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")}

    def checkpoint():
        save_json(state_path, {"scan_version": SCAN_VERSION, "scanned": sorted(scanned), "events": events,
                               "companies": companies, "updated": dt.datetime.now(KST).isoformat()}, compact=True)

    try:
        # 1) current month + recent lookback window (always rescanned; cheap)
        start = min(dt.date(today.year, today.month, 1), today - dt.timedelta(days=args.lookback_days))
        n = scan_rows(dart, ymd(max(start, since)), ymd(today), events)
        log("current window", ymd(start), ymd(today), "new events:", n)
        # 2) complete months not yet scanned (newest first)
        months = 0
        for key_m, bgn, end in full_months(since, today):
            if key_m in scanned:
                continue
            if months >= args.max_months or not stage(.30)():
                log("history scan paused; will continue next run")
                break
            n = scan_rows(dart, bgn, end, events)
            scanned.add(key_m)
            months += 1
            log("scanned", key_m, "new:", n, "total events:", len(events))
            if months % 3 == 0:
                checkpoint()
        checkpoint()
        # 3) details
        fill_details(dart, events, stage(.55))
        checkpoint()
        fill_cancels(dart, events, stage(.80))
        checkpoint()
    except DartLimit as e:
        status["ok"] = False
        status["message"] = "DART 호출 한도/키 문제로 일부만 갱신: %s" % e
        log("DART limit:", e)
    except Exception as e:  # noqa
        status["ok"] = False
        status["message"] = "수집 중 오류(직전 데이터 유지): %s" % e
        log("ERROR:", repr(e))

    # 4) prices (independent from DART)
    try:
        update_prices(h, events, companies, since, today, budget_ok, skip=args.no_prices)
    except Exception as e:  # noqa
        status["ok"] = False
        status["message"] += " | 주가 갱신 오류: %s" % e
        log("price stage error:", repr(e))

    finalize(events, companies)

    # 5) save
    checkpoint()
    status.update({
        "finished": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "minutes": round(elapsed_min(), 1),
        "dart_calls": dart.calls,
        "events": len(events),
        "pending_details": sum(1 for e in events.values() if not e.get("detail_done")),
        "history_complete": all(k in scanned for k, _, _ in full_months(since, today)),
        "months_pending": sum(1 for k, _, _ in full_months(since, today) if k not in scanned),
        "events_by_type": dict(sorted(count_by(events.values(), lambda e: e["type"]).items())),
        "pending_by_type": dict(sorted(count_by((e for e in events.values() if not e.get("detail_done")),
                                                lambda e: e["type"]).items())),
        "pending_errors": dict(sorted(count_by((e for e in events.values() if not e.get("detail_done")),
                                               lambda e: (e.get("err") or "none")[:60]).items(),
                                      key=lambda kv: -kv[1])[:8]),
    })
    latest = build_latest(events, companies, today, status)
    save_json(os.path.join(DATA, "latest.json"), latest, compact=True)
    save_json(os.path.join(DATA, "status.json"), status)
    log("done:", json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
