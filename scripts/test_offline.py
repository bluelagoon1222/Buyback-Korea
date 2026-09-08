#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline end-to-end test for collect.py.
Replaces requests.get with a fake DART / Naver server so the whole pipeline can run without network.
Usage:  python scripts/test_offline.py
"""
import datetime as dt
import io
import json
import os
import random
import re
import sys
import tempfile
import zipfile
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402

random.seed(7)
TODAY = dt.datetime.now(collect.KST).date()

COMPANIES = [
    ("005930", "삼성전자", "Y", 5969782550, "반도체와반도체장비"),
    ("000660", "SK하이닉스", "Y", 728002365, "반도체와반도체장비"),
    ("105560", "KB금융", "Y", 380000000, "은행"),
    ("035420", "NAVER", "Y", 155000000, "양방향미디어와서비스"),
    ("247540", "에코프로비엠", "K", 97800000, "전기제품"),
    ("058470", "리노공업", "K", 15200000, "반도체와반도체장비"),
    ("005380", "현대차", "Y", 205000000, "자동차"),
    ("055550", "신한지주", "Y", 500000000, "은행"),
]

# synthetic disclosures: (days_ago, code, report_nm, pblntf_ty)
EVENTS = []
_rno = 0


def add(days_ago, code, report, ty):
    global _rno
    _rno += 1
    d = TODAY - dt.timedelta(days=days_ago)
    rno = d.strftime("%Y%m%d") + ("9" if ty == "I" else "0") + "%05d" % _rno
    EVENTS.append({"days_ago": days_ago, "code": code, "report": report, "ty": ty, "rno": rno, "date": d})
    return rno


add(1, "005930", "주요사항보고서(자기주식취득결정)", "B")
add(2, "105560", "주요사항보고서(자기주식취득신탁계약체결결정)", "B")
add(3, "035420", "주요사항보고서(자기주식처분결정)", "B")
add(3, "000660", "자기주식소각결정", "I")
add(5, "005380", "기업가치 제고 계획", "I")
add(6, "055550", "기업가치 제고 계획(예고)", "I")
add(9, "247540", "[기재정정]주요사항보고서(자기주식취득결정)", "B")
add(40, "058470", "주요사항보고서(자기주식취득결정)", "B")
add(45, "105560", "자기주식소각결정", "I")
add(70, "005930", "주요사항보고서(자기주식취득신탁계약해지결정)", "B")
add(75, "000660", "주요사항보고서(자기주식처분결정)", "B")
# noise rows that must be ignored
add(2, "005930", "단일판매ㆍ공급계약체결", "I")
add(2, "035420", "주요사항보고서(유상증자결정)", "B")
add(4, "", "주요사항보고서(자기주식취득결정)", "B")  # unlisted filer -> ignored

CO = {c[0]: c for c in COMPANIES}


class FakeResp:
    def __init__(self, status=200, text=None, content=None, js=None):
        self.status_code = status
        self._text = text
        self._content = content
        self._js = js

    def json(self):
        if self._js is not None:
            return self._js
        return json.loads(self._text)

    @property
    def text(self):
        return self._text if self._text is not None else (self._content or b"").decode("utf-8", "ignore")

    @property
    def content(self):
        return self._content if self._content is not None else (self._text or "").encode("utf-8")


def price_series(code, start, end):
    base = {"KOSPI": 3200.0, "KOSDAQ": 900.0}.get(code, random.uniform(5000, 300000))
    rows = [["날짜", "시가", "고가", "저가", "종가", "거래량", "외국인소진율"]]
    d = start
    p = base
    while d <= end:
        if d.weekday() < 5:
            p *= 1 + random.uniform(-0.02, 0.021)
            rows.append([d.strftime("%Y%m%d"), round(p), round(p * 1.01), round(p * 0.99), round(p), 1000, 50.0])
        d += dt.timedelta(days=1)
    return str(rows).replace("'", '"')


def cancel_xml(ev):
    c = CO[ev["code"]]
    shares = c[3] // 100
    body = """<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY>
    <TABLE><TR><TD>자기주식 소각 결정</TD></TR>
    <TR><TD>1. 소각할 주식의 종류와 수</TD><TD>보통주식 (주)</TD><TD>%s</TD></TR>
    <TR><TD></TD><TD>기타주식 (주)</TD><TD>-</TD></TR>
    <TR><TD>2. 발행주식총수</TD><TD>보통주식 (주)</TD><TD>%s</TD></TR>
    <TR><TD></TD><TD>기타주식 (주)</TD><TD>-</TD></TR>
    <TR><TD>3. 소각 예정금액 (원)</TD><TD>%s</TD></TR>
    <TR><TD>4. 소각 예정일</TD><TD>%s</TD></TR>
    <TR><TD>5. 소각할 주식의 취득방법</TD><TD>기존 보유 자기주식</TD></TR>
    <TR><TD>6. 소각 목적</TD><TD>주주가치 제고</TD></TR>
    <TR><TD>7. 이사회결의일(결정일)</TD><TD>%s</TD></TR>
    <TR><TD>- 사외이사 참석여부</TD><TD>참석 (명)</TD><TD>4</TD></TR>
    </TABLE></BODY></DOCUMENT>""" % (
        "{:,}".format(shares), "{:,}".format(c[3]), "{:,}".format(shares * 50000),
        (ev["date"] + dt.timedelta(days=14)).isoformat(), ev["date"].isoformat())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(ev["rno"] + ".xml", body.encode("utf-8"))
    return buf.getvalue()


def fake_get(url, headers=None, params=None, timeout=None):
    params = params or {}
    u = urlparse(url)
    path = u.path
    if u.netloc == "opendart.fss.or.kr":
        assert params.get("crtfc_key") == "TESTKEY", "key must be passed"
        if path.endswith("/list.json"):
            bgn = dt.datetime.strptime(params["bgn_de"], "%Y%m%d").date()
            end = dt.datetime.strptime(params["end_de"], "%Y%m%d").date()
            rows = []
            for ev in EVENTS:
                if ev["ty"] != params["pblntf_ty"] or not (bgn <= ev["date"] <= end):
                    continue
                c = CO.get(ev["code"])
                rows.append({"corp_cls": c[2] if c else "E", "corp_name": c[1] if c else "비상장회사",
                             "corp_code": "0" + ev["code"] + "0" if c else "00000000", "stock_code": ev["code"],
                             "report_nm": ev["report"], "rcept_no": ev["rno"], "flr_nm": c[1] if c else "x",
                             "rcept_dt": ev["date"].strftime("%Y%m%d"), "rm": "유" if c and c[2] == "Y" else "코"})
            # add noise pages to exercise paging
            for i in range(230):
                rows.append({"corp_cls": "Y", "corp_name": "노이즈%d" % i, "corp_code": "99999999",
                             "stock_code": "999%03d" % i, "report_nm": "기타경영사항(자율공시)", "rcept_no": "2000%010d" % i,
                             "rcept_dt": params["bgn_de"], "rm": "유"})
            page = int(params.get("page_no", 1))
            pc = int(params.get("page_count", 10))
            total = len(rows)
            chunk = rows[(page - 1) * pc: page * pc]
            return FakeResp(js={"status": "000", "message": "정상", "page_no": page, "page_count": pc,
                                "total_count": total, "total_page": (total + pc - 1) // pc, "list": chunk})
        if path.endswith("/tsstkAqDecsn.json"):
            return FakeResp(js=detail_payload(params, collect.ACQ_DIRECT))
        if path.endswith("/tsstkAqTrctrCnsDecsn.json"):
            return FakeResp(js=detail_payload(params, collect.ACQ_TRUST))
        if path.endswith("/tsstkAqTrctrCcDecsn.json"):
            return FakeResp(js=detail_payload(params, collect.TRUST_CANCEL))
        if path.endswith("/tsstkDpDecsn.json"):
            return FakeResp(js=detail_payload(params, collect.DISPOSAL))
        if path.endswith("/document.xml"):
            ev = next(e for e in EVENTS if e["rno"] == params["rcept_no"])
            if ev["code"] == "105560":  # simulate document.xml failure -> viewer fallback
                return FakeResp(status=200, text='<?xml version="1.0"?><result><status>014</status><message>파일이 존재하지 않습니다.</message></result>')
            return FakeResp(content=cancel_xml(ev))
        raise RuntimeError("unexpected DART url " + url)
    if u.netloc == "dart.fss.or.kr":
        if path.endswith("/dsaf001/main.do"):
            return FakeResp(text="""<script>node1['text'] = "자기주식소각결정"; node1['rcpNo'] = "%s"; node1['dcmNo'] = "123"; node1['eleId'] = "1"; node1['offset'] = "10"; node1['length'] = "200"; node1['dtd'] = "HTML";</script>""" % params["rcpNo"])
        if path.endswith("/report/viewer.do"):
            ev = next(e for e in EVENTS if e["rno"] == params["rcpNo"])
            z = zipfile.ZipFile(io.BytesIO(cancel_xml(ev)))
            return FakeResp(text=z.read(z.namelist()[0]).decode("utf-8"))
    if u.netloc == "api.finance.naver.com":
        s = dt.datetime.strptime(params["startTime"], "%Y%m%d").date()
        e = dt.datetime.strptime(params["endTime"], "%Y%m%d").date()
        return FakeResp(text=price_series(params["symbol"], s, e))
    if u.netloc == "finance.naver.com":
        c = CO[params["code"]]
        html = """<html><head><title>%s : 네이버페이 증권</title></head><body>
        <a href="/sise/sise_group_detail.naver?type=upjong&amp;no=278">%s</a>
        <table><tr><th scope="row">상장주식수</th>
        <td><em>%s</em></td></tr></table></body></html>""" % (c[1], c[4], "{:,}".format(c[3]))
        return FakeResp(text=html)
    raise RuntimeError("unexpected url " + url)


def detail_payload(params, et):
    bgn = dt.datetime.strptime(params["bgn_de"], "%Y%m%d").date()
    end = dt.datetime.strptime(params["end_de"], "%Y%m%d").date()
    out = []
    for ev in EVENTS:
        if not (bgn <= ev["date"] <= end):
            continue
        c = CO.get(ev["code"])
        if not c or ("0" + ev["code"] + "0") != params["corp_code"]:
            continue
        if collect.classify(ev["report"], ev["ty"]) != et:
            continue
        common = {"rcept_no": ev["rno"], "corp_cls": c[2], "corp_code": params["corp_code"], "corp_name": c[1],
                  "bddd": ev["date"].isoformat(), "aq_wtn_div_ostk_rt": "1.25", "eaq_ostk_rt": "0.10",
                  "cs_iv_bk": "미래에셋증권"}
        if et == collect.ACQ_DIRECT:
            common.update({"aqpln_stk_ostk": "1,000,000", "aqpln_stk_estk": "-", "aqpln_prc_ostk": "70,000,000,000",
                           "aqpln_prc_estk": "-", "aqexpd_bgd": ev["date"].isoformat(),
                           "aqexpd_edd": (ev["date"] + dt.timedelta(days=90)).isoformat(),
                           "aq_pp": "주주가치 제고 및 주가 안정", "aq_mth": "장내매수", "aq_dd": ev["date"].isoformat()})
        elif et == collect.ACQ_TRUST:
            common.update({"ctr_prc": "300,000,000,000", "ctr_pd_bgd": ev["date"].isoformat(),
                           "ctr_pd_edd": (ev["date"] + dt.timedelta(days=180)).isoformat(),
                           "ctr_pp": "주주가치 제고", "ctr_cns_int": "KB증권"})
        elif et == collect.TRUST_CANCEL:
            common.update({"ctr_prc": "300,000,000,000", "ctr_pd_bgd": (ev["date"] - dt.timedelta(days=180)).isoformat(),
                           "ctr_pd_edd": ev["date"].isoformat(), "ctr_pp": "계약기간 만료"})
        elif et == collect.DISPOSAL:
            common.update({"dppln_stk_ostk": "200,000", "dppln_prc_ostk": "40,000,000,000", "dpprpd_bgd": ev["date"].isoformat(),
                           "dpprpd_edd": ev["date"].isoformat(), "dp_pp": "임직원 상여 지급", "dp_m_ovtm": "200,000",
                           "dp_dd": ev["date"].isoformat()})
        out.append(common)
    return {"status": "000", "message": "정상", "list": out} if out else {"status": "013", "message": "조회된 데이타가 없습니다."}


def main():
    collect.requests.get = fake_get
    collect.SLEEP = 0
    tmp = tempfile.mkdtemp()
    collect.DATA = tmp
    os.environ["DART_API_KEY"] = "TESTKEY"
    since = (TODAY - dt.timedelta(days=100)).replace(day=1)
    sys.argv = ["collect.py", "--since", since.strftime("%Y%m%d")]
    collect.main()
    # second run must be idempotent and cheap
    sys.argv = ["collect.py", "--since", since.strftime("%Y%m%d")]
    collect.main()

    latest = json.load(open(os.path.join(tmp, "latest.json"), encoding="utf-8"))
    status = json.load(open(os.path.join(tmp, "status.json"), encoding="utf-8"))
    evs = latest["events"]
    print("\n=== RESULT ===")
    print("status:", json.dumps(status, ensure_ascii=False))
    print("events:", len(evs), "| types:", {t: sum(1 for e in evs if e["type"] == t) for t in set(e["type"] for e in evs)})
    for e in evs:
        print(" ", e["date"], e["type"].ljust(12), e["corp"].ljust(8), "amt=", e.get("amount"), "sh=", e.get("shares"),
              "pct_sh=", e.get("pct_shares"), "pct_mcap=", e.get("pct_mcap"), "ret=",
              {k: e["ret"].get(k) for k in ("r1", "r5", "r20", "rnow", "x20")} if e.get("ret") else None,
              "| end=", e.get("period_end"), "| method=", e.get("method"), "| flags=",
              "amended" if e.get("amended") else "", e.get("valueup_kind") or "")
    # assertions
    assert len(evs) == 11, "expected 11 events, got %d" % len(evs)
    assert all(e["type"] != collect.VALUEUP and e.get("amount") for e in evs if e["type"] != collect.VALUEUP), "amounts missing"
    assert all(e.get("ret") for e in evs), "returns missing"
    canc = [e for e in evs if e["type"] == collect.CANCEL]
    assert len(canc) == 2 and all(c.get("shares") and c.get("pct_shares") for c in canc), "cancel parse failed"
    assert status["history_complete"] and status["pending_details"] == 0
    assert latest["monthly"], "monthly aggregates missing"
    print("monthly:", latest["monthly"])
    print("\nALL CHECKS PASSED. output dir:", tmp)
    return tmp


if __name__ == "__main__":
    main()
