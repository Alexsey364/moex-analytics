# Ежедневный workflow SBER

python -m moex_analytics.cli run-sber-daily выполняет импорт validated официальных фактов,
point-in-time operating state, nowcast, аудит зон и размера, immutable snapshot, matured outcomes
и live scorecard. Cutoff идемпотентен. run_sber_daily.bat запускает ту же команду. Планировщик
Windows намеренно не создаётся. Локальные пользовательские настройки находятся в
config/sber_user.local.yaml и игнорируются Git.
