"""情绪与利润分层做单。

价格把大多数人的心态带到一边；下单跟着还有利润的那一边。
阶段利润拿到就平；整轮那边还有距离，不因这一段翻向。
每个币单独一本账。比特币的情绪、利润、杠杆燃料不能套到任何山寨上，山寨之间也不共用。

本模块只给出动作，不读行情、不下单、不进调度、不进实盘白名单。
自动策略仍禁止开空（INV-08）。这里的阶段空单是专业交易员自己的动作，不得接入自动开仓。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 低于此值视为「这边已经没什么钱可赚」，不再加仓。
PROFIT_EPS = 0.05

# 5 倍多单维持保证金近似。爆仓价 ≈ 开仓价 × (1 - 1/杠杆 + 维持保证金)。
DEFAULT_MMR = 0.004

Sentiment = Literal["cold", "neutral", "hot"]
Side = Literal["long", "short", "flat"]
LayerName = Literal["stage", "trend", "none"]
Action = Literal["open_long", "open_short", "close", "hold"]


@dataclass(frozen=True)
class LayerProfit:
    """一层还剩多少利润。0 是没了，1 是满的。由交易员判断，不从已走完的价格反推成结论。"""

    upside_left: float
    downside_left: float

    def __post_init__(self) -> None:
        for name in ("upside_left", "downside_left"):
            value = getattr(self, name)
            if value < 0 or value > 1:
                raise ValueError(f"{name} 必须在 0 到 1 之间，收到 {value}")


@dataclass(frozen=True)
class Book:
    """当前这一段和整轮的利润，以及价格已经把人群带到哪一边。"""

    sentiment: Sentiment
    leg: LayerProfit
    trend: LayerProfit
    # 这一段里还能被砸爆的杠杆多单。只描述这一个币，不从别的币推断。
    leg_long_fuel_alive: bool = True


@dataclass(frozen=True)
class SymbolState:
    """一个币自己的账和自己的仓。"""

    symbol: str
    book: Book
    position: Position = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.symbol or not str(self.symbol).strip():
            raise ValueError("symbol 不能为空")
        if self.position is None:
            object.__setattr__(self, "position", Position())


@dataclass(frozen=True)
class Position:
    side: Side = "flat"
    layer: LayerName = "none"


@dataclass(frozen=True)
class Decision:
    action: Action
    layer: LayerName
    flip_trend: bool
    reason: str


def long_liquidation_price(entry: float, leverage: float = 5.0, mmr: float = DEFAULT_MMR) -> float:
    """多单爆仓价。杠杆小于等于 1 不会被强平。"""
    if entry <= 0:
        raise ValueError("entry 必须大于 0")
    if leverage <= 1:
        return 0.0
    return entry * (1.0 - 1.0 / leverage + mmr)


def short_room_until_long_liquidation(
    price: float,
    entry: float,
    leverage: float = 5.0,
    mmr: float = DEFAULT_MMR,
    fuel_alive: bool = True,
) -> float:
    """从现价到这档多单爆仓，空头还剩的距离，用开仓价归一到 0~1。

    燃料没了（该档已经爆完）或现价已经到爆仓价下面，剩余利润为 0。
    """
    if not fuel_alive or price <= 0 or entry <= 0:
        return 0.0
    liq = long_liquidation_price(entry, leverage, mmr)
    if liq <= 0 or price <= liq:
        return 0.0
    return min(1.0, (price - liq) / entry)


def decide_symbols(states: list[SymbolState]) -> dict[str, Decision]:
    """按币出下一步。每个币用自己的情绪、这段利润、整轮利润和杠杆燃料。"""
    return {key: steps[0] for key, steps in plan_symbols(states).items()}


def plan_symbols(states: list[SymbolState]) -> dict[str, list[Decision]]:
    """按币给出完整动作。有仓要换时，第一步一定是平仓。"""
    if not states:
        return {}
    seen: dict[str, SymbolState] = {}
    for state in states:
        key = state.symbol.strip().upper()
        if key in seen:
            raise ValueError(f"{key} 重复了。每个币只能有一份自己的账，不能抄另一份")
        seen[key] = state
    return {key: plan(state.book, state.position) for key, state in seen.items()}


def decide(book: Book, position: Position | None = None) -> Decision:
    """下一步。手里的仓和目标不一致时，下一步只平仓，不开新仓。"""
    return plan(book, position)[0]


def plan(book: Book, position: Position | None = None) -> list[Decision]:
    """从当前仓位排出动作。要换仓时先平，平完才开。账本不变。"""
    pos = position or Position()
    leg_up, leg_down = _leg_rooms(book)
    trend_bias = _exclusive_side(book.trend.upside_left, book.trend.downside_left)
    leg_side = _exclusive_side(leg_up, leg_down)
    target_side, target_layer = _target(book.sentiment, leg_side, trend_bias)
    open_reason = _reason(book.sentiment, leg_side, trend_bias, target_side, target_layer, False)
    same_position = pos.side == target_side and pos.layer == target_layer
    if target_side == "flat" and pos.side == "flat":
        return [Decision("hold", "none", False, open_reason)]
    if same_position and target_side != "flat":
        return [Decision("hold", target_layer, False, open_reason)]

    steps: list[Decision] = []
    if pos.side != "flat":
        steps.append(Decision(
            "close",
            pos.layer,
            False,
            _close_reason(pos, target_side, target_layer),
        ))
    if target_side != "flat":
        flip_trend = (
            target_layer == "trend"
            and pos.layer == "trend"
            and pos.side in ("long", "short")
            and pos.side != target_side
        )
        action: Action = "open_long" if target_side == "long" else "open_short"
        steps.append(Decision(
            action,
            target_layer,
            flip_trend,
            _reason(book.sentiment, leg_side, trend_bias, target_side, target_layer, flip_trend),
        ))
    return steps


def apply_decision(position: Position, decision: Decision) -> Position:
    """把一个动作落到仓位上。还有仓时不能开新仓。"""
    if decision.action == "hold":
        return position
    if decision.action == "close":
        if position.side == "flat":
            raise ValueError("没有仓可平")
        if decision.layer != position.layer:
            raise ValueError("平仓层和持仓层不一致")
        return Position("flat", "none")
    if position.side != "flat":
        raise ValueError("还有仓，必须先平")
    if decision.layer not in ("stage", "trend"):
        raise ValueError("开仓必须落在阶段或整轮")
    if decision.action == "open_long":
        return Position("long", decision.layer)
    if decision.action == "open_short":
        return Position("short", decision.layer)
    raise ValueError(f"未知动作 {decision.action}")


def _close_reason(pos: Position, target_side: Side, target_layer: LayerName) -> str:
    if target_side == "flat":
        return "这张仓的利润已经拿到，平仓，不加仓。"
    if pos.side != target_side:
        return "方向要换，先平掉手里的仓。"
    if pos.layer != target_layer:
        return "这一层的利润已经拿到，先平这张仓，下一层再单独开。"
    return "先平掉手里的仓。"


def _leg_rooms(book: Book) -> tuple[float, float]:
    downside = book.leg.downside_left if book.leg_long_fuel_alive else 0.0
    return book.leg.upside_left, downside


def _exclusive_side(upside: float, downside: float) -> str:
    """利润独占一边时返回 long/short。两边都厚、或两边都薄，返回 both/none。"""
    up_open = upside > PROFIT_EPS
    down_open = downside > PROFIT_EPS
    if up_open and not down_open:
        return "long"
    if down_open and not up_open:
        return "short"
    if up_open and down_open:
        return "both"
    return "none"


def _target(sentiment: Sentiment, leg_side: str, trend_bias: str) -> tuple[Side, LayerName]:
    # 整轮利润已经换边，而且人群还挤在旧的一边：这是真正的顶或底。
    if sentiment == "hot" and trend_bias == "short":
        return "short", "trend"
    if sentiment == "cold" and trend_bias == "long":
        return "long", "trend"

    # 只是这一段换边。整轮没换，就只做这一段。
    if sentiment == "hot" and leg_side == "short":
        return "short", "stage"
    if sentiment == "cold" and leg_side == "long":
        return "long", "stage"

    # 心态和利润还在同一边：顺着拿，不提前猜顶底。
    if sentiment == "hot" and leg_side in ("long", "both") and trend_bias != "short":
        return "long", "trend"
    if sentiment == "cold" and leg_side in ("short", "both") and trend_bias != "long":
        return "short", "trend"

    # 这一段的空头燃料没了，整轮多头还有距离，情绪已经转怕：回到多。
    if sentiment == "cold" and leg_side == "none" and trend_bias == "long":
        return "long", "trend"
    if sentiment == "hot" and leg_side == "none" and trend_bias == "short":
        return "short", "trend"

    if sentiment == "neutral" and trend_bias in ("long", "short"):
        return trend_bias, "trend"  # type: ignore[return-value]

    return "flat", "none"


def _reason(
    sentiment: Sentiment,
    leg_side: str,
    trend_bias: str,
    target_side: Side,
    target_layer: LayerName,
    flip_trend: bool,
) -> str:
    mood = {"cold": "情绪差，人群想空", "hot": "情绪热，人群想多", "neutral": "情绪不极端"}[sentiment]
    if target_side == "flat":
        return f"{mood}。这一段和整轮都没有还厚的利润，平掉，不加仓。"
    if flip_trend and target_side == "short":
        return f"{mood}。整轮上面的利润没了，下面还有厚利润，这是顶，翻向做空。"
    if flip_trend and target_side == "long":
        return f"{mood}。整轮下面的利润没了，上面还有距离，这是底，翻向做多。"
    if target_layer == "stage" and target_side == "short":
        return f"{mood}。这一段上面没利润了，砸跟风多单还有利润。只做阶段空，整轮不翻。"
    if target_layer == "stage" and target_side == "long":
        return f"{mood}。这一段下面没利润了，反弹还有一段。只做阶段多，整轮不翻。"
    if target_side == "long":
        return f"{mood}。利润还在多头这边，顺着拿，不提前当顶。"
    if target_side == "short" and trend_bias == "short":
        return f"{mood}。利润还在空头这边，顺着拿，不提前当底。"
    if leg_side == "none":
        return f"{mood}。这一段的利润已经拿到，按整轮剩下的那一边做。"
    return f"{mood}。利润在{ '多头' if target_side == 'long' else '空头' }这边。"
