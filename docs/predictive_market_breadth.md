# Market universe and breadth

Текущая TQBR-вселенная маркируется current_universe_only и не выдаётся за историческую.
Исторические SECID/boards хранятся явно. Breadth рассчитывается по бумагам, реально имеющим
цену на дату: advances/declines, доли выше SMA, highs/lows, median/equal-weight return,
cross-sectional volatility и drawdown share. Это уменьшает, но не устраняет survivorship bias
без платной/архивной истории constituents.
