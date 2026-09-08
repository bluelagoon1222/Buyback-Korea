#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create data/latest.json with clearly-labelled SAMPLE data so the page can be checked before the first real run."""
import datetime as dt
import json
import os
import random

random.seed(20260908)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()

CO = [
    ("005930", "삼성전자", "KOSPI", 5969782550, 72000, "반도체와반도체장비"),
    ("000660", "SK하이닉스", "KOSPI", 728002365, 250000, "반도체와반도체장비"),
    ("105560", "KB금융", "KOSPI", 380000000, 110000, "은행"),
    ("055550", "신한지주", "KOSPI", 500000000, 62000, "은행"),
    ("086790", "하나금융지주", "KOSPI", 290000000, 78000, "은행"),
    ("316140", "우리금융지주", "KOSPI", 740000000, 19000, "은행"),
    ("035420", "NAVER", "KOSPI", 155000000, 210000, "양방향미디어와서비스"),
    ("005380", "현대차", "KOSPI", 205000000, 240000, "자동차"),
    ("000270", "기아", "KOSPI", 400000000, 110000, "자동차"),
    ("012330", "현대모비스", "KOSPI", 93000000, 280000, "자동차부품"),
    ("028260", "삼성물산", "KOSPI", 170000000, 160000, "복합기업"),
    ("034730", "SK", "KOSPI", 72000000, 190000, "복합기업"),
    ("003550", "LG", "KOSPI", 157000000, 88000, "복합기업"),
    ("015760", "한국전력", "KOSPI", 642000000, 25000, "전기유틸리티"),
    ("032830", "삼성생명", "KOSPI", 200000000, 105000, "보험"),
    ("000810", "삼성화재", "KOSPI", 47000000, 380000, "보험"),
    ("006800", "미래에셋증권", "KOSPI", 560000000, 12000, "증권"),
    ("016360", "삼성증권", "KOSPI", 89000000, 52000, "증권"),
    ("030200", "KT", "KOSPI", 250000000, 45000, "다각화된통신서비스"),
    ("017670", "SK텔레콤", "KOSPI", 215000000, 58000, "무선통신서비스"),
    ("247540", "에코프로비엠", "KOSDAQ", 97800000, 140000, "전기제품"),
    ("058470", "리노공업", "KOSDAQ", 15200000, 210000, "반도체와반도체장비"),
    ("035900", "JYP Ent.", "KOSDAQ", 35500000, 62000, "엔터테인먼트"),
    ("041510", "에스엠", "KOSDAQ", 23800000, 95000, "엔터테인먼트"),
    ("263750", "펄어비스", "KOSDAQ", 64000000, 40000, "게임엔터테인먼트"),
    ("214150", "클래시스", "KOSDAQ", 64700000, 55000, "건강관리장비와용품"),
    ("086520", "에코프로", "KOSDAQ", 133000000, 75000, "화학"),
    ("039030", "이오테크닉스", "KOSDAQ", 12300000, 180000, "반도체와반도체장비"),
]
PURPOSE_ACQ = ["주주가치 제고 및 주가 안정", "주주환원 정책 이행", "주가 안정을 통한 주주가치 제고", "밸류업 계획에 따른 주주환원"]
PURPOSE_DISP = ["임직원 상여 지급", "우리사주 조합 출연", "교환사채 발행 대상", "임직원 성과보상(RSU)", "전략적 제휴"]
MARKET_IDX = {"KOSPI": 3200.0, "KOSDAQ": 900.0}


def rnd_ret():
    r1 = round(random.gauss(1.2, 2.5), 2)
    r5 = round(r1 + random.gauss(0.5, 3), 2)
    r20 = round(r5 + random.gauss(0.8, 5), 2)
    rnow = round(r20 + random.gauss(1.0, 8), 2)
    return r1, r5, r20, rnow


def main():
    events = []
    companies = {}
    n = 0
    for c in CO:
        code, name, mkt, shares, px, sector = c
        companies[code] = {"name": name, "shares": shares, "sector": sector, "last_close": px,
                           "last_date": TODAY.isoformat()}
    for days_ago in range(0, 620):
        d = TODAY - dt.timedelta(days=days_ago)
        if d.weekday() >= 5:
            continue
        for _ in range(random.choice([0, 0, 0, 1, 1, 2])):
            code, name, mkt, shares, px, sector = random.choice(CO)
            t = random.choices(["acq_direct", "acq_trust", "cancel", "disposal", "valueup", "trust_cancel"],
                               weights=[34, 18, 22, 14, 9, 3])[0]
            n += 1
            rno = d.strftime("%Y%m%d") + ("9" if t in ("cancel", "valueup") else "0") + "%05d" % n
            base = px * (1 + random.uniform(-0.25, 0.25))
            mcap = base * shares
            ev = {"id": rno, "type": t, "date": d.isoformat(), "corp": name, "code": code, "market": mkt,
                  "sector": sector, "amended": random.random() < 0.06, "withdrawn": False,
                  "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + rno}
            pct = random.choice([0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0]) * random.uniform(0.6, 1.4)
            amount = int(mcap * pct / 100)
            if t == "acq_direct":
                ev.update({"report": "주요사항보고서(자기주식취득결정)", "amount": amount, "shares": int(amount / base),
                           "period_start": d.isoformat(), "period_end": (d + dt.timedelta(days=90)).isoformat(),
                           "purpose": random.choice(PURPOSE_ACQ), "method": "장내매수", "broker": "미래에셋증권",
                           "held_pct": round(random.uniform(0, 6), 2)})
            elif t == "acq_trust":
                ev.update({"report": "주요사항보고서(자기주식취득신탁계약체결결정)", "amount": amount, "shares": None,
                           "shares_est": True, "period_start": d.isoformat(),
                           "period_end": (d + dt.timedelta(days=180)).isoformat(),
                           "purpose": random.choice(PURPOSE_ACQ), "method": "신탁계약", "broker": "KB증권",
                           "held_pct": round(random.uniform(0, 6), 2)})
            elif t == "trust_cancel":
                ev.update({"report": "주요사항보고서(자기주식취득신탁계약해지결정)", "amount": amount, "shares": None,
                           "period_start": (d - dt.timedelta(days=180)).isoformat(), "period_end": d.isoformat(),
                           "purpose": "계약기간 만료", "method": "신탁계약 해지"})
            elif t == "cancel":
                ev.update({"report": "자기주식소각결정", "amount": amount, "shares": int(amount / base),
                           "total_shares": shares, "period_end": (d + dt.timedelta(days=14)).isoformat(),
                           "purpose": "주주가치 제고", "method": random.choice(["기존 보유 자기주식", "배당가능이익 범위 내 취득"]),
                           "decision_date": d.isoformat()})
            elif t == "disposal":
                ev.update({"report": "주요사항보고서(자기주식처분결정)", "amount": amount, "shares": int(amount / base),
                           "period_start": d.isoformat(), "period_end": (d + dt.timedelta(days=3)).isoformat(),
                           "purpose": random.choice(PURPOSE_DISP),
                           "method": random.choice(["시간외대량매매", "장외처분", "시장매도"]),
                           "held_pct": round(random.uniform(0, 6), 2)})
            else:
                kind = random.choice(["본공시", "본공시", "예고"])
                ev.update({"report": "기업가치 제고 계획" + ("(예고)" if kind == "예고" else ""), "amount": None,
                           "shares": None, "valueup_kind": kind})
            if ev.get("shares") and shares:
                ev["pct_shares"] = round(ev["shares"] / shares * 100, 3)
            elif t == "acq_trust":
                ev["pct_shares"] = round(amount / base / shares * 100, 3)
            else:
                ev["pct_shares"] = None
            ev["pct_mcap"] = round(amount / mcap * 100, 3) if ev.get("amount") else None
            r1, r5, r20, rnow = rnd_ret()
            after = days_ago
            ret = {"base_date": (d - dt.timedelta(days=1)).isoformat(), "base": round(base),
                   "r1": r1 if after >= 1 else None, "x1": round(r1 - random.gauss(0, 1), 2) if after >= 1 else None,
                   "r5": r5 if after >= 7 else None, "x5": round(r5 - random.gauss(0, 1.5), 2) if after >= 7 else None,
                   "r20": r20 if after >= 29 else None, "x20": round(r20 - random.gauss(0, 3), 2) if after >= 29 else None,
                   "rnow": rnow if after >= 1 else None, "xnow": round(rnow - random.gauss(0, 4), 2) if after >= 1 else None,
                   "now_date": TODAY.isoformat(), "days": max(0, int(after * 5 / 7))}
            ev["ret"] = ret
            events.append(ev)
    events.sort(key=lambda e: (e["date"], e["id"]), reverse=True)
    monthly = {}
    for e in events:
        if e.get("amended"):
            continue
        m = monthly.setdefault(e["date"][:7], {"month": e["date"][:7], "acq": 0, "acq_n": 0, "cancel": 0, "cancel_n": 0,
                                                "disp": 0, "disp_n": 0, "valueup_n": 0})
        a = e.get("amount") or 0
        if e["type"] in ("acq_direct", "acq_trust"):
            m["acq"] += a; m["acq_n"] += 1
        elif e["type"] == "cancel":
            m["cancel"] += a; m["cancel_n"] += 1
        elif e["type"] == "disposal":
            m["disp"] += a; m["disp_n"] += 1
        elif e["type"] == "valueup":
            m["valueup_n"] += 1
    out = {"sample": True, "asof": TODAY.isoformat(),
           "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST") + " (SAMPLE)",
           "since": events[-1]["date"], "count": len(events),
           "monthly": [monthly[k] for k in sorted(monthly)], "companies": companies, "events": events,
           "status": {"ok": True, "message": "sample data", "events": len(events), "pending_details": 0,
                      "history_complete": True, "months_pending": 0}}
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(DATA, "status.json"), "w", encoding="utf-8") as f:
        json.dump(out["status"], f, ensure_ascii=False, indent=1)
    print("sample events:", len(events))


if __name__ == "__main__":
    main()
