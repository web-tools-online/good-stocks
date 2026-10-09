"""Static configuration: which markets are covered and how they are fetched."""

# Market codes used throughout the database and the website.
MARKET_US = "US"
MARKET_EU = "EU"
MARKET_CZ = "CZ"
MARKETS = (MARKET_US, MARKET_EU, MARKET_CZ)

# US universe: Nasdaq Trader symbol directory (covers NASDAQ, NYSE, NYSE American,
# NYSE Arca and Cboe listed securities).
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# EU universe: Yahoo Finance screener, one query per exchange (Yahoo exchange codes).
# Only the main venue of each EU member state that Yahoo covers is used, so that the
# same company is not picked up from a dozen regional German exchanges.
EU_EXCHANGES = {
    "AT": ["VIE"],  # Wiener Börse
    "BE": ["BRU"],  # Euronext Brussels
    "DE": ["GER"],  # Xetra
    "DK": ["CPH"],  # Nasdaq Copenhagen
    "EE": ["TAL"],  # Nasdaq Tallinn
    "ES": ["MCE"],  # Bolsa de Madrid (Mercado Continuo)
    "FI": ["HEL"],  # Nasdaq Helsinki
    "FR": ["PAR"],  # Euronext Paris
    "GR": ["ATH"],  # Athens Exchange
    "HU": ["BUD"],  # Budapest Stock Exchange
    "IE": ["ISE"],  # Euronext Dublin
    "IT": ["MIL"],  # Borsa Italiana
    "LT": ["LIT"],  # Nasdaq Vilnius
    "LV": ["RIS"],  # Nasdaq Riga
    "NL": ["AMS"],  # Euronext Amsterdam
    "PL": ["WSE"],  # Warsaw Stock Exchange
    "PT": ["LIS"],  # Euronext Lisbon
    "RO": ["BVB"],  # Bucharest Stock Exchange
    "SE": ["STO"],  # Nasdaq Stockholm
}

# Czech universe: Prague Stock Exchange.
CZ_EXCHANGES = {"CZ": ["PRA"]}

# Always included in the Czech universe even if the screener misses them.
CZ_SEED_SYMBOLS = [
    "CEZ.PR", "KOMB.PR", "MONET.PR", "ERBAG.PR", "TABAK.PR", "KOFOL.PR", "VIG.PR",
]

# Country names as reported by Yahoo Finance (assetProfile.country).
EU_COUNTRIES = {
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Czechia",
    "Denmark", "Estonia", "Finland", "France", "Germany", "Greece", "Hungary", "Ireland",
    "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands", "Poland",
    "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden",
}

# Currencies of EU member states; used to spot foreign companies whose Yahoo profile
# has no country (e.g. US companies traded on Xetra report in USD).
EU_CURRENCIES = {"EUR", "SEK", "DKK", "PLN", "HUF", "RON", "CZK", "BGN"}

# Yahoo exchange code -> country name, used to pick the home listing of a company that
# is listed on several EU exchanges.
EXCHANGE_COUNTRY = {
    "VIE": "Austria", "BRU": "Belgium", "GER": "Germany", "CPH": "Denmark",
    "TAL": "Estonia", "MCE": "Spain", "HEL": "Finland", "PAR": "France",
    "ATH": "Greece", "BUD": "Hungary", "ISE": "Ireland", "MIL": "Italy",
    "LIT": "Lithuania", "RIS": "Latvia", "AMS": "Netherlands", "WSE": "Poland",
    "LIS": "Portugal", "BVB": "Romania", "STO": "Sweden", "PRA": "Czech Republic",
}

# Listings that were excluded (ETF, non-EU secondary listing, duplicate) are not
# re-fetched every week; they are re-checked after this many days.
EXCLUSION_RECHECK_DAYS = 120

# Listings whose last successful fetch is older than this are not shown on the site.
MAX_STALE_DAYS = 45

# Annual revenue / net income are kept only as far back as the 5-year growth needs.
ANNUAL_FIGURES_KEEP_YEARS = 7

# ECB euro reference rates, used to convert everything to USD for sorting/filtering.
ECB_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
