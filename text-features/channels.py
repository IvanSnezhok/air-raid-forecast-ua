"""Public Telegram channels to collect. Edit freely; usernames without '@'.

All are public channels that post real-time air threat updates. Check each one
still exists and is active before a long backfill.
"""

CHANNELS = {
    # Official: Ukrainian Air Force. Launches, strategic aviation take-offs,
    # missile/drone directions, morning summaries of the night attack.
    "kpszsu": "Повітряні сили ЗСУ (official)",
    # Volunteer radar-style monitors: drone/missile tracks, "курсом на ...".
    "war_monitor": "Монітор (volunteer radar)",
    "deraketaua": "Де ракета (volunteer radar)",
    "raketa_trevoga": "Ракетна тривога (volunteer radar)",
    # Alert mirror with threat notes per region (used by lbgos/AirAlerts).
    "UkraineAlarmSignal": "Тривога + нотатки про загрози",
}
