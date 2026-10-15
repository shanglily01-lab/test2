#!/usr/bin/env python3
"""情绪与利润分层做单 — 用对话里的情景回归。无 API / 无 DB。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.sentiment_profit_strategy import (  # noqa: E402
    PROFIT_EPS,
    Book,
    LayerProfit,
    Position,
    SymbolState,
    apply_decision,
    decide,
    decide_symbols,
    long_liquidation_price,
    plan,
    plan_symbols,
    short_room_until_long_liquidation,
)

_fail_n = 0


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _fail(msg: str) -> None:
    global _fail_n
    _fail_n += 1
    print(f"  FAIL {msg}")


def _book(sentiment, leg_up, leg_down, trend_up, trend_down, fuel=True) -> Book:
    return Book(
        sentiment,
        LayerProfit(leg_up, leg_down),
        LayerProfit(trend_up, trend_down),
        fuel,
    )


def _expect(name, book, pos, action, layer, flip) -> None:
    got = decide(book, pos)
    if got.action == action and got.layer == layer and got.flip_trend is flip:
        _ok(f"{name} → {got.action}/{got.layer} flip={got.flip_trend} | {got.reason}")
        return
    _fail(
        f"{name}: 期望 {action}/{layer} flip={flip}，"
        f"得到 {got.action}/{got.layer} flip={got.flip_trend} | {got.reason}"
    )


def _expect_plan(name, book, pos, steps) -> None:
    got = [(item.action, item.layer, item.flip_trend) for item in plan(book, pos)]
    if got == steps:
        _ok(f"{name} → {got}")
        return
    _fail(f"{name}: 期望 {steps}，得到 {got}")


def test_bottoms_and_tops() -> None:
    flat = Position()
    _expect(
        "底部：情绪差但下面没利润",
        _book("cold", 0.8, 0.02, 0.8, 0.02),
        flat,
        "open_long",
        "trend",
        False,
    )
    _expect(
        "上涨途中：上面还有利润，不提前空",
        _book("hot", 0.6, 0.3, 0.7, 0.02),
        Position("long", "trend"),
        "hold",
        "trend",
        False,
    )
    _expect(
        "阶段顶：手里有多，下一步只平仓",
        _book("hot", 0.02, 0.4, 0.7, 0.02),
        Position("long", "trend"),
        "close",
        "trend",
        False,
    )
    _expect_plan(
        "阶段顶平完再开阶段空，整轮不翻",
        _book("hot", 0.02, 0.4, 0.7, 0.02),
        Position("long", "trend"),
        [("close", "trend", False), ("open_short", "stage", False)],
    )

    _expect(
        "阶段空还没砸完：继续拿",
        _book("hot", 0.02, 0.4, 0.7, 0.02),
        Position("short", "stage"),
        "hold",
        "stage",
        False,
    )
    _expect(
        "山寨燃料爆完：不再开空，有空单就平",
        _book("hot", 0.02, 0.4, 0.7, 0.02, fuel=False),
        Position("short", "stage"),
        "close",
        "stage",
        False,
    )
    _expect_plan(
        "5倍也砸完且情绪转怕：先平阶段空，再回到整轮的多",
        _book("cold", 0.5, 0.0, 0.6, 0.02, fuel=False),
        Position("short", "stage"),
        [("close", "stage", False), ("open_long", "trend", False)],
    )
    _expect_plan(
        "真正的顶：先平趋势多，再翻向趋势空",
        _book("hot", 0.0, 0.5, 0.02, 0.6),
        Position("long", "trend"),
        [("close", "trend", False), ("open_short", "trend", True)],
    )
    _expect_plan(
        "熊市阶段底：先平趋势空，再开阶段多",
        _book("cold", 0.4, 0.02, 0.1, 0.6),
        Position("short", "trend"),
        [("close", "trend", False), ("open_long", "stage", False)],
    )
    _expect_plan(
        "熊市阶段顶：先平阶段多，再回到整轮的空",
        _book("hot", 0.02, 0.4, 0.02, 0.6),
        Position("long", "stage"),
        [("close", "stage", False), ("open_short", "trend", False)],
    )
    _expect_plan(
        "同向换层：阶段空先平，再单独开趋势空",
        _book("hot", 0.0, 0.5, 0.02, 0.6),
        Position("short", "stage"),
        [("close", "stage", False), ("open_short", "trend", False)],
    )


def test_five_x_room() -> None:
    entry = 88_000.0
    liq = long_liquidation_price(entry, 5.0)
    if 70_000 < liq < 72_000:
        _ok(f"5倍爆仓价约 {liq:.0f}，在 7 万上下")
    else:
        _fail(f"5倍爆仓价 {liq:.0f} 不在 7 万上下")

    room_now = short_room_until_long_liquidation(81_000.0, entry, 5.0, fuel_alive=True)
    room_done = short_room_until_long_liquidation(70_000.0, entry, 5.0, fuel_alive=True)
    room_dead = short_room_until_long_liquidation(81_000.0, entry, 5.0, fuel_alive=False)
    if room_now > PROFIT_EPS:
        _ok(f"现价未到5倍爆仓，空头还有距离 {room_now:.3f}")
    else:
        _fail(f"5倍还没爆，剩余距离却只有 {room_now:.3f}")
    if room_done == 0 and room_dead == 0:
        _ok("价格砸过爆仓价，或燃料已灭，再做空的剩余利润为 0")
    else:
        _fail(f"砸完后仍算出利润 room_done={room_done} room_dead={room_dead}")


def test_each_symbol_has_its_own_book() -> None:
    """比特币 5 倍还在、某个山寨已爆、另一个山寨整轮已经到顶，三份动作必须不同。"""
    btc = SymbolState(
        "BTC",
        _book("hot", 0.02, 0.4, 0.7, 0.02, fuel=True),
        Position("short", "stage"),
    )
    eth = SymbolState(
        "ETH",
        _book("hot", 0.02, 0.4, 0.7, 0.02, fuel=False),
        Position("short", "stage"),
    )
    # 这个山寨自己的整轮上面已经没了，和比特币的牛市回调不是同一件事。
    sol = SymbolState(
        "SOL",
        _book("hot", 0.0, 0.5, 0.02, 0.6, fuel=True),
        Position("long", "trend"),
    )
    got = decide_symbols([btc, eth, sol])
    if set(got) != {"BTC", "ETH", "SOL"}:
        _fail(f"按币输出不全: {set(got)}")
        return
    if got["BTC"].action == "hold" and got["BTC"].layer == "stage":
        _ok("BTC 5倍燃料还在，阶段空继续拿")
    else:
        _fail(f"BTC 不应跟山寨一起平: {got['BTC']}")
    if got["ETH"].action == "close":
        _ok("ETH 自己的燃料没了，平掉自己的空，不看 BTC")
    else:
        _fail(f"ETH 应单独平仓: {got['ETH']}")
    sol_plan = plan_symbols([sol])["SOL"]
    if (
        got["SOL"].action == "close"
        and len(sol_plan) == 2
        and sol_plan[1].action == "open_short"
        and sol_plan[1].layer == "trend"
        and sol_plan[1].flip_trend
    ):
        _ok("SOL 先平自己的多，再翻向自己的趋势空")
    else:
        _fail(f"SOL 应先平再翻空: {got['SOL']} / {sol_plan}")

    actions = {(item.action, item.layer, item.flip_trend) for item in got.values()}
    if len(actions) == 3:
        _ok("三个币三种动作，没有共用一份结论")
    else:
        _fail(f"动作被合成了一份: {actions}")

    try:
        decide_symbols([btc, SymbolState("btc", btc.book, btc.position)])
        _fail("同一币两份账应被拒绝")
    except ValueError:
        _ok("同一币不能交两份账")

    # 同样一段跌幅，杠杆不同，爆不爆也不同。不能拿比特币的 5 倍去套山寨。
    drop_price = lambda entry: entry * (81_000 / 88_000)
    room5 = short_room_until_long_liquidation(drop_price(88_000), 88_000, leverage=5)
    room10 = short_room_until_long_liquidation(drop_price(100), 100, leverage=10)
    liq20 = long_liquidation_price(100, leverage=20)
    room20 = short_room_until_long_liquidation(drop_price(100), 100, leverage=20)
    if room5 > PROFIT_EPS and room10 > 0 and room20 == 0 and drop_price(100) < liq20:
        _ok(f"同一段跌幅：5倍还剩 {room5:.3f}，10倍还剩 {room10:.3f}，20倍已爆")
    else:
        _fail(f"杠杆应分开算 5x={room5} 10x={room10} 20x={room20} liq20={liq20}")


def test_close_is_applied_before_open() -> None:
    book = _book("hot", 0.02, 0.4, 0.7, 0.02)
    pos = Position("long", "trend")
    first = decide(book, pos)
    if first.action != "close":
        _fail(f"有仓换向时第一步不是平仓: {first}")
        return
    try:
        apply_decision(pos, plan(book, pos)[1])
        _fail("多单还在时不应直接开空")
    except ValueError:
        _ok("仓还在时拒绝开新仓")
    flat = apply_decision(pos, first)
    if flat.side != "flat" or flat.layer != "none":
        _fail(f"平仓后仓位不为空: {flat}")
        return
    second = decide(book, flat)
    opened = apply_decision(flat, second)
    if second.action == "open_short" and opened.side == "short" and opened.layer == "stage":
        _ok("平完多单后，下一步才开阶段空")
    else:
        _fail(f"平仓后的动作不对: {second} → {opened}")

    dead = _book("hot", 0.02, 0.4, 0.7, 0.02, fuel=False)
    closed = apply_decision(Position("short", "stage"), decide(dead, Position("short", "stage")))
    after = decide(dead, closed)
    if closed.side == "flat" and after.action == "hold" and len(plan(dead, Position("short", "stage"))) == 1:
        _ok("燃料没了只平仓，不再开新空")
    else:
        _fail(f"平完不该再开: closed={closed} after={after}")


def main() -> int:
    print("sentiment_profit_strategy")
    test_bottoms_and_tops()
    test_five_x_room()
    test_each_symbol_has_its_own_book()
    test_close_is_applied_before_open()
    if _fail_n:
        print(f"FAIL {_fail_n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
