"""Text signals (public Telegram channels, news) for the alert forecast.

    archive   point-in-time store: what we saw, when we first saw it
    telegram  t.me/s web-preview collector (no login)
    news      RSS (live) and GDELT (history) collector
    lexicon   one regex lexicon: oblasts and threat classes
    features  per-(origin hour, region) text features, NaN outside coverage
    live      text features at the forecast origin for the live forecast
    collect   CLI: python -m airraid.text.collect update --root DIR
"""
