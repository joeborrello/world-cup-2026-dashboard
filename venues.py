"""
Static venue table for the 16 host stadiums of the 2026 World Cup.

Keyed by the openfootball "ground" string so seed_data.py can join match
fixtures to coordinates. openfootball does not ship lat/lng, and these venues
are fixed, so this hand-curated table is the source of truth for the map.
"""

# ground (openfootball) -> metadata
VENUES = {
    "Atlanta": {
        "stadium": "Mercedes-Benz Stadium", "city": "Atlanta",
        "country": "USA", "lat": 33.7554, "lng": -84.4008,
        "tz": "America/New_York",
    },
    "Boston (Foxborough)": {
        "stadium": "Gillette Stadium", "city": "Boston",
        "country": "USA", "lat": 42.0909, "lng": -71.2643,
        "tz": "America/New_York",
    },
    "Dallas (Arlington)": {
        "stadium": "AT&T Stadium", "city": "Dallas",
        "country": "USA", "lat": 32.7473, "lng": -97.0945,
        "tz": "America/Chicago",
    },
    "Guadalajara (Zapopan)": {
        "stadium": "Estadio Akron", "city": "Guadalajara",
        "country": "Mexico", "lat": 20.6819, "lng": -103.4625,
        "tz": "America/Mexico_City",
    },
    "Houston": {
        "stadium": "NRG Stadium", "city": "Houston",
        "country": "USA", "lat": 29.6847, "lng": -95.4107,
        "tz": "America/Chicago",
    },
    "Kansas City": {
        "stadium": "Arrowhead Stadium", "city": "Kansas City",
        "country": "USA", "lat": 39.0489, "lng": -94.4839,
        "tz": "America/Chicago",
    },
    "Los Angeles (Inglewood)": {
        "stadium": "SoFi Stadium", "city": "Los Angeles",
        "country": "USA", "lat": 33.9535, "lng": -118.3392,
        "tz": "America/Los_Angeles",
    },
    "Mexico City": {
        "stadium": "Estadio Azteca", "city": "Mexico City",
        "country": "Mexico", "lat": 19.3029, "lng": -99.1505,
        "tz": "America/Mexico_City",
    },
    "Miami (Miami Gardens)": {
        "stadium": "Hard Rock Stadium", "city": "Miami",
        "country": "USA", "lat": 25.9580, "lng": -80.2389,
        "tz": "America/New_York",
    },
    "Monterrey (Guadalupe)": {
        "stadium": "Estadio BBVA", "city": "Monterrey",
        "country": "Mexico", "lat": 25.6692, "lng": -100.2444,
        "tz": "America/Monterrey",
    },
    "New York/New Jersey (East Rutherford)": {
        "stadium": "MetLife Stadium", "city": "New York/New Jersey",
        "country": "USA", "lat": 40.8135, "lng": -74.0745,
        "tz": "America/New_York",
    },
    "Philadelphia": {
        "stadium": "Lincoln Financial Field", "city": "Philadelphia",
        "country": "USA", "lat": 39.9008, "lng": -75.1675,
        "tz": "America/New_York",
    },
    "San Francisco Bay Area (Santa Clara)": {
        "stadium": "Levi's Stadium", "city": "San Francisco Bay Area",
        "country": "USA", "lat": 37.4030, "lng": -121.9698,
        "tz": "America/Los_Angeles",
    },
    "Seattle": {
        "stadium": "Lumen Field", "city": "Seattle",
        "country": "USA", "lat": 47.5952, "lng": -122.3316,
        "tz": "America/Los_Angeles",
    },
    "Toronto": {
        "stadium": "BMO Field", "city": "Toronto",
        "country": "Canada", "lat": 43.6332, "lng": -79.4185,
        "tz": "America/Toronto",
    },
    "Vancouver": {
        "stadium": "BC Place", "city": "Vancouver",
        "country": "Canada", "lat": 49.2768, "lng": -123.1118,
        "tz": "America/Vancouver",
    },
}
