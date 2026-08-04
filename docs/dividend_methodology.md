# Dividend methodology

Official endpoint: https://iss.moex.com/iss/securities/{SECID}/dividends.json.
It returns secid, isin, registryclosedate, value, and currencyid.

registryclosedate is the registry close date. It is not the declaration date,
last cum-dividend trading date, or payment date. Those fields are unavailable here;
declared_date and payment_date remain NULL and are never inferred.

actual-dividends-v1 applies confirmed cash once on the registry date for descriptive
history. It is not point-in-time because ISS lacks publication timestamps. Future
backtests need a source with announcement timestamps.

Raw OHLC is never adjusted. Dividend return is cash divided by the preceding
available close; total return is price plus dividend return. The index compounds it.
