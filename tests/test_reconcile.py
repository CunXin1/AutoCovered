"""Flex 报表对账补录:方向判定、账本事件映射、幂等。

最关键的一类回归:同一个 IBKR 账户里既有 covered call 的空头腿,又有长期
多头 call(LEAPS)。两者都出现在 Flex 的 Trades 段,只能靠
buySell + openCloseIndicator 区分 —— 把多头腿记进账本会凭空造出轮次。
"""
from datetime import date

import pytest

from src.engine.lifecycle import ASSIGN, BUY_TO_CLOSE, EXPIRE, SELL_TO_OPEN
from src.ledger import Ledger
from src.reconcile import load_rows, parse_flex_csv, parse_flex_xml, to_events

A1 = "U24088860"
A2 = "U25868195"

FLEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="AutoCovered-Trades" type="AF">
 <FlexStatements count="2">
  <FlexStatement accountId="U24088860" fromDate="20260701" toDate="20261002">
   <Trades>
    <!-- covered call 空头腿:卖出开仓 -->
    <Trade accountId="U24088860" assetCategory="OPT" underlyingSymbol="CRWV"
      symbol="CRWV 260821C00145000" putCall="C" strike="145" expiry="20260821"
      tradeDate="20260710" dateTime="20260710;103015" buySell="SELL"
      quantity="-1" tradePrice="0.65" ibCommission="-0.6531" tradeID="1001"
      openCloseIndicator="O" notes="" transactionType="ExchTrade"/>
    <!-- 到期作废(BookTrade,code=Ep,价格 0) -->
    <Trade accountId="U24088860" assetCategory="OPT" underlyingSymbol="CRWV"
      symbol="CRWV 260821C00145000" putCall="C" strike="145" expiry="20260821"
      tradeDate="20260821" dateTime="20260821;202000" buySell="BUY"
      quantity="1" tradePrice="0" ibCommission="0" tradeID="1002"
      openCloseIndicator="C" notes="Ep" transactionType="BookTrade"/>
    <!-- 长期多头 LEAPS:买入开仓,绝不能进账本 -->
    <Trade accountId="U24088860" assetCategory="OPT" underlyingSymbol="NVDA"
      symbol="NVDA 270115C00210000" putCall="C" strike="210" expiry="20270115"
      tradeDate="20260715" dateTime="20260715;110000" buySell="BUY"
      quantity="2" tradePrice="25.55" ibCommission="-2.10" tradeID="1003"
      openCloseIndicator="O" notes="" transactionType="ExchTrade"/>
    <!-- 多头腿卖出平仓:同样不进账本 -->
    <Trade accountId="U24088860" assetCategory="OPT" underlyingSymbol="NFLX"
      symbol="NFLX 270115C00098000" putCall="C" strike="98" expiry="20270115"
      tradeDate="20260716" dateTime="20260716;140000" buySell="SELL"
      quantity="-1" tradePrice="11.55" ibCommission="-1.05" tradeID="1004"
      openCloseIndicator="C" notes="" transactionType="ExchTrade"/>
    <!-- put:不在 call 账本范围 -->
    <Trade accountId="U24088860" assetCategory="OPT" underlyingSymbol="USO"
      symbol="USO 270115P00100000" putCall="P" strike="100" expiry="20270115"
      tradeDate="20260717" dateTime="20260717;140000" buySell="SELL"
      quantity="-1" tradePrice="13.70" ibCommission="-1.00" tradeID="1005"
      openCloseIndicator="O" notes="" transactionType="ExchTrade"/>
    <!-- 正股 -->
    <Trade accountId="U24088860" assetCategory="STK" underlyingSymbol="CRWV"
      symbol="CRWV" putCall="" strike="" expiry="" tradeDate="20260601"
      buySell="BUY" quantity="100" tradePrice="107.29" ibCommission="-1.00"
      tradeID="1006" openCloseIndicator="O" notes=""/>
   </Trades>
  </FlexStatement>
  <FlexStatement accountId="U25868195" fromDate="20260701" toDate="20261002">
   <Trades>
    <!-- 买入平仓(50% 止盈) -->
    <Trade accountId="U25868195" assetCategory="OPT" underlyingSymbol="MSFT"
      symbol="MSFT 260821C00490000" putCall="C" strike="490" expiry="20260821"
      tradeDate="20260730" dateTime="20260730;133000" buySell="BUY"
      quantity="2" tradePrice="1.03" ibCommission="-1.30" tradeID="2001"
      openCloseIndicator="C" notes="" transactionType="ExchTrade"/>
    <!-- 同日卖出开新腿 → roll -->
    <Trade accountId="U25868195" assetCategory="OPT" underlyingSymbol="MSFT"
      symbol="MSFT 261120C00550000" putCall="C" strike="550" expiry="20261120"
      tradeDate="20260730" dateTime="20260730;133500" buySell="SELL"
      quantity="-2" tradePrice="17.92" ibCommission="-1.30" tradeID="2002"
      openCloseIndicator="O" notes="" transactionType="ExchTrade"/>
    <!-- 被指派 -->
    <Trade accountId="U25868195" assetCategory="OPT" underlyingSymbol="NVDA"
      symbol="NVDA 260828C00245000" putCall="C" strike="245" expiry="20260828"
      tradeDate="20260828" dateTime="20260828;202000" buySell="BUY"
      quantity="1" tradePrice="0" ibCommission="0" tradeID="2003"
      openCloseIndicator="C" notes="A" transactionType="BookTrade"/>
   </Trades>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


@pytest.fixture
def events():
    return to_events(parse_flex_xml(FLEX_XML))


def find(events, ticker, action):
    return next(e for e in events if e.ticker == ticker and e.action == action)


# ---------------------------------------------------------------- 方向判定

def test_long_legs_and_non_calls_are_filtered_out():
    rows = parse_flex_xml(FLEX_XML)
    assert len(rows) == 9
    evs = to_events(rows)
    tickers = {e.ticker for e in evs}
    assert "NFLX" not in tickers      # 多头卖出平仓
    assert "USO" not in tickers       # put
    assert all(not (e.ticker == "NVDA" and e.strike == 210) for e in evs)  # LEAPS
    assert len(evs) == 5


def test_sell_to_open_keeps_exact_price_and_fees(events):
    e = find(events, "CRWV", SELL_TO_OPEN)
    assert (e.price, e.fees) == (0.65, 0.6531)
    assert e.price_quality == "exact" and e.source == "flex"
    assert e.account == A1
    assert e.exec_id == "flex:1001"
    assert e.ts == "2026-07-10T10:30:15+00:00"


def test_expiry_row_becomes_expire_event(events):
    e = find(events, "CRWV", EXPIRE)
    assert (e.price, e.contracts, e.outcome) == (0.0, 1, "expired")
    assert "Ep" in e.note


def test_assignment_row_becomes_assign_event(events):
    e = find(events, "NVDA", ASSIGN)
    assert (e.price, e.outcome, e.account) == (0.0, "assigned", A2)


def test_same_day_close_and_open_pairs_as_roll(events):
    close_ev = find(events, "MSFT", BUY_TO_CLOSE)
    open_ev = find(events, "MSFT", SELL_TO_OPEN)
    assert close_ev.outcome == "rolled"
    assert open_ev.rolled_from == close_ev.key
    assert close_ev.price == 1.03 and open_ev.price == 17.92


# ---------------------------------------------------------------- 过滤与入账

def test_ticker_and_account_filters():
    rows = parse_flex_xml(FLEX_XML)
    assert {e.account for e in to_events(rows, accounts={A2})} == {A2}
    assert {e.ticker for e in to_events(rows, tickers={"CRWV"})} == {"CRWV"}
    assert to_events(rows, since=date(2026, 8, 1)) and all(
        e.ts[:10] >= "2026-08-01" for e in to_events(rows, since=date(2026, 8, 1)))


def test_apply_is_idempotent(tmp_path, events):
    lg = Ledger(tmp_path / "ledger.db")
    try:
        first = lg.apply(events)
        assert len(first) == len(events)
        assert lg.apply(events) == []        # 重放 = no-op
        rows = lg.conn.execute(
            "SELECT ticker, account, outcome FROM rounds ORDER BY id").fetchall()
        outcomes = {(r["ticker"], r["account"]): r["outcome"] for r in rows}
        assert outcomes[("CRWV", A1)] == "expired"
        assert outcomes[("NVDA", A2)] == "assigned"
        assert outcomes[("MSFT", A2)] == "open"      # 新腿还在世
    finally:
        lg.close()


def test_roll_chain_links_rounds(tmp_path, events):
    lg = Ledger(tmp_path / "ledger.db")
    try:
        lg.apply(events)
        row = lg.conn.execute(
            "SELECT id, rolled_from_round_id FROM rounds "
            "WHERE ticker='MSFT' AND strike=550").fetchone()
        src = lg.conn.execute(
            "SELECT id FROM rounds WHERE ticker='MSFT' AND strike=490").fetchone()
        assert row["rolled_from_round_id"] == src["id"]
    finally:
        lg.close()


# ---------------------------------------------------------------- 文件格式

def test_flat_csv_parses_same_fields():
    text = ("accountId,assetCategory,underlyingSymbol,putCall,strike,expiry,"
            "tradeDate,dateTime,buySell,quantity,tradePrice,ibCommission,"
            "tradeID,openCloseIndicator,notes\n"
            "U24088860,OPT,CRWV,C,145,20260821,20260710,20260710;103015,"
            "SELL,-1,0.65,-0.6531,1001,O,\n")
    [row] = parse_flex_csv(text)
    assert (row.ticker, row.strike, row.account) == ("CRWV", 145.0, A1)
    [e] = to_events([row])
    assert e.action == SELL_TO_OPEN and e.price == 0.65


def test_sectioned_activity_statement_is_rejected_clearly():
    text = ("Statement,Header,Field Name,Field Value\n"
            "Trades,Header,DataDiscriminator,Asset Category\n")
    with pytest.raises(ValueError, match="Activity Statement"):
        parse_flex_csv(text)


def test_file_dispatch_by_content(tmp_path):
    p = tmp_path / "flex.xml"
    p.write_text(FLEX_XML, encoding="utf-8")
    assert len(load_rows(path=p)) == 9


# ---------------------------------------------------------------- 分层去重

LEVELED_XML = """<?xml version="1.0"?>
<FlexQueryResponse>
 <FlexStatements count="1">
  <FlexStatement accountId="U25868195" fromDate="20260701" toDate="20261002"
    period="Last365CalendarDays" whenGenerated="20261002;201203">
   <Trades>
    <Trade levelOfDetail="ASSET_CLASS" accountId="U25868195" assetCategory="OPT"
      underlyingSymbol="MSFT" putCall="C" strike="550" expiry="20261120"
      tradeDate="20260730" buySell="SELL" quantity="-2" tradePrice="17.92"
      ibCommission="-1.30" tradeID="" openCloseIndicator="O" notes=""/>
    <Trade levelOfDetail="ORDER" accountId="U25868195" assetCategory="OPT"
      underlyingSymbol="MSFT" putCall="C" strike="550" expiry="20261120"
      tradeDate="20260730" buySell="SELL" quantity="-2" tradePrice="17.92"
      ibCommission="-1.30" tradeID="9001" openCloseIndicator="O" notes=""/>
    <Trade levelOfDetail="EXECUTION" accountId="U25868195" assetCategory="OPT"
      underlyingSymbol="MSFT" putCall="C" strike="550" expiry="20261120"
      tradeDate="20260730" dateTime="20260730;133500" buySell="SELL" quantity="-2"
      tradePrice="17.92" ibCommission="-1.30" tradeID="9002" openCloseIndicator="O" notes=""/>
    <Trade levelOfDetail="CLOSED_LOT" accountId="U25868195" assetCategory="OPT"
      underlyingSymbol="MSFT" putCall="C" strike="550" expiry="20261120"
      tradeDate="20260730" buySell="SELL" quantity="-2" tradePrice="17.92"
      ibCommission="0" tradeID="9003" openCloseIndicator="O" notes=""/>
   </Trades>
  </FlexStatement>
 </FlexStatements>
</FlexQueryResponse>
"""


def test_only_execution_level_is_ingested():
    """同一笔成交在 4 个层级各出现一次,只能入账一次。"""
    rows = parse_flex_xml(LEVELED_XML)
    assert len(rows) == 4
    evs = to_events(rows)
    assert len(evs) == 1
    assert evs[0].exec_id == "flex:9002" and evs[0].contracts == 2


def test_statement_metadata_and_level_counts():
    import xml.etree.ElementTree as ET

    from src.brokers.ibkr_flex import level_counts, section_inventory, statement_infos

    root = ET.fromstring(LEVELED_XML)
    [info] = statement_infos(root)
    assert info.account_id == "U25868195"
    assert info.coverage == "20260701 → 20261002"
    assert info.period == "Last365CalendarDays"
    assert section_inventory(root) == {"Trades": 4}
    assert level_counts(root) == {
        "ASSET_CLASS": 1, "ORDER": 1, "EXECUTION": 1, "CLOSED_LOT": 1}


def test_date_segments_respects_365_day_cap():
    from src.brokers.ibkr_flex import date_segments

    segs = date_segments(date(2024, 1, 1), date(2026, 10, 2))
    assert len(segs) == 3
    assert segs[0] == ("20240101", "20241230") or segs[0].fd == "20240101"
    assert segs[-1].td == "20261002"
    # 段间不重叠、不留空洞
    for a, b in zip(segs, segs[1:]):
        assert date.fromisoformat(
            f"{b.fd[:4]}-{b.fd[4:6]}-{b.fd[6:]}") - date.fromisoformat(
            f"{a.td[:4]}-{a.td[4:6]}-{a.td[6:]}") == __import__(
            "datetime").timedelta(days=1)
