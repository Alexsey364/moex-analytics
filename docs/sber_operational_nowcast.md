# Operational nowcast SBER

Nowcast — исследовательская экстраполяция, не официальный прогноз Сбера. Reported stock,
monthly flow и YTD хранятся раздельно. Из YTD период получается только для сопоставимых соседних
документов. Активны simple YTD и conservative; сезонный и guidance методы включаются лишь при
достаточной сопоставимой истории и официальном guidance. Каждый результат хранит input hash,
допущения, confidence и версию.
