"""Schwab transaction-history CSV and template import."""
import pytest

import wealth_csv
import wealth_ledger

# Same columns and wording as Schwab's export; amounts invented.
SCHWAB = '''"Transactions  for account Roth Contributory IRA ...123 as of 11/18/2026 09:12:01 ET"
"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"
"11/14/2026","Buy","KLAC","KLA CORP","1","$141.20","","-$141.20"
"10/01/2026 as of 09/30/2026","Stock Split","KLAC","KLA CORP","9","","",""
"09/15/2026","Qualified Dividend","KLAC","KLA CORP","","","","$1.90"
"09/15/2026","NRA Tax Adj","TSM","TAIWAN SEMI","","","","-$0.42"
"06/02/2026","Reinvest Shares","TSM","TAIWAN SEMI","0.0081","$245.10","","-$1.99"
"06/02/2026","Reinvest Dividend","TSM","TAIWAN SEMI","","","","$1.99"
"03/03/2026","Buy","TSM","TAIWAN SEMI","2","$198.40","$0.00","-$396.80"
"02/10/2026","Buy","KLAC","KLA CORP","1","$1,250.00","","-$1,250.00"
"02/01/2026","MoneyLink Transfer","","Tfr BANK","","","","$2,000.00"
"01/15/2026","Buy to Open","NVDA 01/15/2027 200.00 C","CALL","1","$5.00","$0.65","-$500.65"
"01/10/2026","Bank Interest","","SCHWAB1 INT","","","","$0.11"
"01/05/2026","Mystery Thing","","","","","",""
"Transactions Total","","","","","","","$-286.85"
'''


def test_schwab_export_parses_into_ledger_types():
    p = wealth_csv.parse(SCHWAB)
    assert p["format"] == "schwab"
    types = [(t["type"], t.get("ticker")) for t in p["transactions"]]
    assert ("DEPOSIT", None) in types and ("SPLIT", "KLAC") in types
    assert ("DIVIDEND", "TSM") in types and ("BUY", "TSM") in types and ("TAX", None) in types
    reasons = " ".join(s["reason"] for s in p["skipped"])
    assert "Options are not tracked" in reasons and "Mystery Thing" in reasons
    dates = [t["date"] for t in p["transactions"]]
    assert dates == sorted(dates) and "2026-10-01" in dates          # "as of" date ignored


def test_schwab_import_reproduces_position_through_split():
    lg = wealth_ledger.build(wealth_csv.parse(SCHWAB)["transactions"])
    k = lg.holding("KLAC")
    # 1 share pre-split + 9 received = 10, then 1 more bought after.
    assert k["shares"] == pytest.approx(11)
    assert k["cost_basis"] == pytest.approx(1250 + 141.20)
    assert lg.holding("TSM")["shares"] == pytest.approx(2.0081)
    assert lg.dividends == pytest.approx(1.90 + 1.99)
    assert lg.cash == pytest.approx(2000 + 0.11 - 1250 - 396.80 + 1.90 - 0.42 - 141.20, abs=0.02)


def test_fingerprints_are_stable_and_distinct():
    a = wealth_csv.parse(SCHWAB)["transactions"]
    b = wealth_csv.parse(SCHWAB)["transactions"]
    assert [t["fingerprint"] for t in a] == [t["fingerprint"] for t in b]
    assert len({t["fingerprint"] for t in a}) == len(a)


def test_template_roundtrip_and_errors():
    p = wealth_csv.parse(wealth_csv.template_csv())
    assert p["format"] == "template" and len(p["transactions"]) == 4 and not p["skipped"]
    bad = wealth_csv.parse("date,type,ticker,shares,price,amount,fees,ratio,note\n2026-01-01,BUY,NVDA,0,100,,,,\n")
    assert bad["transactions"] == [] and "Shares must be" in bad["skipped"][0]["reason"]
    junk = wealth_csv.parse("a,b,c\n1,2,3\n")
    assert junk["format"] is None and junk["skipped"]
