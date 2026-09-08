import requests 

SEC_URL = "https://www.sec.gov/files/company_tickers.json"

HEADERS = {"User-Agent": "ResearchTeam analyst@example.com"}

def fetch_equity_universe():
    """Pulls US company tickers from the SEC API."""
    response = requests.get(SEC_URL, headers=HEADERS, timeout=10)
    response.raise_for_status()
    data = response.json()

    valid_tickers = []

    for item in data.values():
        ticker = item["ticker"]

        #exclude preferred shares, warrants, and foreing share classess containing '.' or '-'
        if "." not in ticker and "-" not in ticker: 
            validate_ticker.append(ticker)


    return sorted(list(set(valid_tickers)))


if __name__ == "__main__":
    symbols = fetch_equity_universe()
    print(f"Discovered {len(symbols)} candidate US equities")

    