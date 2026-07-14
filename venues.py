"""
Static venue tables: the 16 host stadiums of the men's 2026 World Cup and the
8 host stadiums of the 2027 Women's World Cup (Brazil).

Keyed by the openfootball "ground" string so seed_data.py can join match
fixtures to coordinates. openfootball does not ship lat/lng, and these venues
are fixed, so this hand-curated table is the source of truth for the map.

``roof`` records whether the pitch can be enclosed — it drives the daily map's
open-vs-covered note for each stadium:
  * "open"        -> open-air, the field is always exposed to the weather
  * "retractable" -> a movable roof that can close the field off
  * "fixed"       -> a permanent roof that always covers the field
Both "retractable" and "fixed" count as "covered" for display; the distinction
is kept so the note can say *which* kind of roof a venue has.
"""

# ground (openfootball) -> metadata
VENUES = {
    "Atlanta": {
        "stadium": "Mercedes-Benz Stadium", "city": "Atlanta",
        "country": "USA", "lat": 33.7554, "lng": -84.4008,
        "tz": "America/New_York", "roof": "retractable",
    },
    "Boston (Foxborough)": {
        "stadium": "Gillette Stadium", "city": "Boston",
        "country": "USA", "lat": 42.0909, "lng": -71.2643,
        "tz": "America/New_York", "roof": "open",
    },
    "Dallas (Arlington)": {
        "stadium": "AT&T Stadium", "city": "Dallas",
        "country": "USA", "lat": 32.7473, "lng": -97.0945,
        "tz": "America/Chicago", "roof": "retractable",
    },
    "Guadalajara (Zapopan)": {
        "stadium": "Estadio Akron", "city": "Guadalajara",
        "country": "Mexico", "lat": 20.6819, "lng": -103.4625,
        "tz": "America/Mexico_City", "roof": "open",
    },
    "Houston": {
        "stadium": "NRG Stadium", "city": "Houston",
        "country": "USA", "lat": 29.6847, "lng": -95.4107,
        "tz": "America/Chicago", "roof": "retractable",
    },
    "Kansas City": {
        "stadium": "Arrowhead Stadium", "city": "Kansas City",
        "country": "USA", "lat": 39.0489, "lng": -94.4839,
        "tz": "America/Chicago", "roof": "open",
    },
    "Los Angeles (Inglewood)": {
        "stadium": "SoFi Stadium", "city": "Los Angeles",
        "country": "USA", "lat": 33.9535, "lng": -118.3392,
        "tz": "America/Los_Angeles", "roof": "fixed",
    },
    "Mexico City": {
        "stadium": "Estadio Azteca", "city": "Mexico City",
        "country": "Mexico", "lat": 19.3029, "lng": -99.1505,
        "tz": "America/Mexico_City", "roof": "open",
    },
    "Miami (Miami Gardens)": {
        "stadium": "Hard Rock Stadium", "city": "Miami",
        "country": "USA", "lat": 25.9580, "lng": -80.2389,
        "tz": "America/New_York", "roof": "open",
    },
    "Monterrey (Guadalupe)": {
        "stadium": "Estadio BBVA", "city": "Monterrey",
        "country": "Mexico", "lat": 25.6692, "lng": -100.2444,
        "tz": "America/Monterrey", "roof": "open",
    },
    "New York/New Jersey (East Rutherford)": {
        "stadium": "MetLife Stadium", "city": "New York/New Jersey",
        "country": "USA", "lat": 40.8135, "lng": -74.0745,
        "tz": "America/New_York", "roof": "open",
    },
    "Philadelphia": {
        "stadium": "Lincoln Financial Field", "city": "Philadelphia",
        "country": "USA", "lat": 39.9008, "lng": -75.1675,
        "tz": "America/New_York", "roof": "open",
    },
    "San Francisco Bay Area (Santa Clara)": {
        "stadium": "Levi's Stadium", "city": "San Francisco Bay Area",
        "country": "USA", "lat": 37.4030, "lng": -121.9698,
        "tz": "America/Los_Angeles", "roof": "open",
    },
    "Seattle": {
        "stadium": "Lumen Field", "city": "Seattle",
        "country": "USA", "lat": 47.5952, "lng": -122.3316,
        "tz": "America/Los_Angeles", "roof": "open",
    },
    "Toronto": {
        "stadium": "BMO Field", "city": "Toronto",
        "country": "Canada", "lat": 43.6332, "lng": -79.4185,
        "tz": "America/Toronto", "roof": "open",
    },
    "Vancouver": {
        "stadium": "BC Place", "city": "Vancouver",
        "country": "Canada", "lat": 49.2768, "lng": -123.1118,
        "tz": "America/Vancouver", "roof": "retractable",
    },
}

# 2027 Women's World Cup (Brazil) — the eight host stadiums. Keyed by city the
# way openfootball grounds its Brazilian fixtures; if the eventual 2027 feed
# spells a ground differently, seed_data's unmapped-venue check will name it
# loudly and the key just needs renaming here. Every pitch is open-air.
WOMENS_VENUES = {
    "Rio de Janeiro": {
        "stadium": "Estádio do Maracanã", "city": "Rio de Janeiro",
        "country": "Brazil", "lat": -22.9121, "lng": -43.2302,
        "tz": "America/Sao_Paulo", "roof": "open",
    },
    "São Paulo": {
        "stadium": "Neo Química Arena", "city": "São Paulo",
        "country": "Brazil", "lat": -23.5453, "lng": -46.4742,
        "tz": "America/Sao_Paulo", "roof": "open",
    },
    "Brasília": {
        "stadium": "Estádio Nacional Mané Garrincha", "city": "Brasília",
        "country": "Brazil", "lat": -15.7835, "lng": -47.8992,
        "tz": "America/Sao_Paulo", "roof": "open",
    },
    "Belo Horizonte": {
        "stadium": "Estádio Mineirão", "city": "Belo Horizonte",
        "country": "Brazil", "lat": -19.8658, "lng": -43.9719,
        "tz": "America/Sao_Paulo", "roof": "open",
    },
    "Fortaleza": {
        "stadium": "Arena Castelão", "city": "Fortaleza",
        "country": "Brazil", "lat": -3.8072, "lng": -38.5222,
        "tz": "America/Fortaleza", "roof": "open",
    },
    "Recife (São Lourenço da Mata)": {
        "stadium": "Arena de Pernambuco", "city": "Recife",
        "country": "Brazil", "lat": -8.0405, "lng": -35.0080,
        "tz": "America/Recife", "roof": "open",
    },
    "Salvador": {
        "stadium": "Arena Fonte Nova", "city": "Salvador",
        "country": "Brazil", "lat": -12.9787, "lng": -38.5044,
        "tz": "America/Bahia", "roof": "open",
    },
    "Porto Alegre": {
        "stadium": "Estádio Beira-Rio", "city": "Porto Alegre",
        "country": "Brazil", "lat": -30.0654, "lng": -51.2358,
        "tz": "America/Sao_Paulo", "roof": "open",
    },
}
