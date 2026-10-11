---
title: Air Raid Forecast UA
emoji: 🚨
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Live air raid alerts and 1-6 h forecasts for Ukrainian oblasts
---

Live air raid alert state and probabilistic forecasts for the next 1-6 hours for every
oblast of Ukraine. Source code: https://github.com/IvanSnezhok/air-raid-forecast-ua

This Space is deployed automatically from the GitHub repository by
`.github/workflows/deploy-space.yml`. Set `ALERTS_IN_UA_TOKEN` as a Space secret to show
live alerts; without it the app shows the state at the end of the volunteer dataset.
