import sys
import json
import urllib.request
import urllib.parse
import ssl
import datetime

MAX_FORECAST_DAYS = 14

def fetch_json(url, context):
    with urllib.request.urlopen(url, context=context) as r:
        raw = r.read().decode()
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("error"):
            raise Exception(data.get("reason", "API error"))
        return data

def get_weather_range(lat, lon, start_date_obj, end_date_obj, context):
    today = datetime.date.today()
    forecast_cutoff = today + datetime.timedelta(days=MAX_FORECAST_DAYS)
    results = []
    note = None

    # Determine split point
    if start_date_obj >= today and end_date_obj > forecast_cutoff:
        # Split: forecast up to cutoff, then historical fallback for the rest
        forecast_end = forecast_cutoff
        historical_start = forecast_cutoff + datetime.timedelta(days=1)
        historical_end = end_date_obj

        # 1. Fetch forecast portion
        if start_date_obj <= forecast_end:
            url = (f"https://api.open-meteo.com/v1/forecast"
                   f"?latitude={lat}&longitude={lon}"
                   f"&start_date={start_date_obj}&end_date={forecast_end}"
                   f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto")
            data = fetch_json(url, context)
            results += format_daily(data.get("daily", {}))

        # 2. Fetch historical fallback for remainder (same dates last year)
        try:
            hist_start_ly = historical_start.replace(year=historical_start.year - 1)
        except ValueError:
            hist_start_ly = historical_start - datetime.timedelta(days=365)
        try:
            hist_end_ly = historical_end.replace(year=historical_end.year - 1)
        except ValueError:
            hist_end_ly = historical_end - datetime.timedelta(days=365)

        url = (f"https://archive-api.open-meteo.com/v1/archive"
               f"?latitude={lat}&longitude={lon}"
               f"&start_date={hist_start_ly}&end_date={hist_end_ly}"
               f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto")
        data = fetch_json(url, context)
        # Re-label the dates back to the requested year
        days_offset = (historical_start - hist_start_ly).days
        for entry in format_daily(data.get("daily", {})):
            orig_date = datetime.datetime.strptime(entry["date"], "%Y-%m-%d").date()
            future_date = orig_date + datetime.timedelta(days=days_offset)
            results.append({**entry, "date": str(future_date), "estimated": True})

        note = (f"Weather forecast is only available {MAX_FORECAST_DAYS} days ahead. "
                f"Dates from {historical_start} to {historical_end} are estimated using historical data from the same period last year.")

    elif start_date_obj >= today and end_date_obj <= forecast_cutoff:
        # Fully within forecast range
        url = (f"https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               f"&start_date={start_date_obj}&end_date={end_date_obj}"
               f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto")
        data = fetch_json(url, context)
        results = format_daily(data.get("daily", {}))

    elif (start_date_obj - today).days > MAX_FORECAST_DAYS:
        # Entirely beyond forecast: full historical fallback
        try:
            hist_start = start_date_obj.replace(year=start_date_obj.year - 1)
        except ValueError:
            hist_start = start_date_obj - datetime.timedelta(days=365)
        try:
            hist_end = end_date_obj.replace(year=end_date_obj.year - 1)
        except ValueError:
            hist_end = end_date_obj - datetime.timedelta(days=365)
        days_offset = (start_date_obj - hist_start).days

        url = (f"https://archive-api.open-meteo.com/v1/archive"
               f"?latitude={lat}&longitude={lon}"
               f"&start_date={hist_start}&end_date={hist_end}"
               f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto")
        data = fetch_json(url, context)
        for entry in format_daily(data.get("daily", {})):
            orig_date = datetime.datetime.strptime(entry["date"], "%Y-%m-%d").date()
            future_date = orig_date + datetime.timedelta(days=days_offset)
            results.append({**entry, "date": str(future_date), "estimated": True})

        note = (f"Forecast is only available up to {MAX_FORECAST_DAYS} days in the future. "
                f"Showing historical data from last year ({hist_start} to {hist_end}) as an estimate "
                f"for {start_date_obj} to {end_date_obj}.")
    else:
        # Past data: use archive
        url = (f"https://archive-api.open-meteo.com/v1/archive"
               f"?latitude={lat}&longitude={lon}"
               f"&start_date={start_date_obj}&end_date={end_date_obj}"
               f"&daily=temperature_2m_max,temperature_2m_min&timezone=auto")
        data = fetch_json(url, context)
        results = format_daily(data.get("daily", {}))

    return results, note

def format_daily(daily):
    entries = []
    for i, date_str in enumerate(daily.get("time", [])):
        entries.append({
            "date": date_str,
            "temp_max_c": daily["temperature_2m_max"][i] if i < len(daily.get("temperature_2m_max", [])) else None,
            "temp_min_c": daily["temperature_2m_min"][i] if i < len(daily.get("temperature_2m_min", [])) else None
        })
    return entries

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing arguments"}))
        sys.exit(1)

    location = ""
    start_date = ""
    end_date = ""

    if sys.argv[1].strip().startswith("{"):
        try:
            data = json.loads(sys.argv[1])
            for key, value in data.items():
                k_lower = key.lower()
                if "location" in k_lower or "city" in k_lower:
                    location = str(value).strip()
                elif "start" in k_lower:
                    start_date = str(value).strip()
                elif "end" in k_lower:
                    end_date = str(value).strip()
        except Exception as e:
            print(json.dumps({"error": f"Invalid JSON arguments: {str(e)}"}))
            sys.exit(1)
    else:
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Missing positional arguments (expected: location, start_date, end_date)"}))
            sys.exit(1)
        location = sys.argv[1].strip()
        start_date = sys.argv[2].strip()
        end_date = sys.argv[3].strip()

    if not location:
        print(json.dumps({"error": "Location is required"})); sys.exit(1)
    if not start_date:
        print(json.dumps({"error": "Start date is required"})); sys.exit(1)
    if not end_date:
        print(json.dumps({"error": "End date is required"})); sys.exit(1)

    context = ssl._create_unverified_context()

    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
        geo_data = fetch_json(geo_url, context)
        if not geo_data.get("results"):
            print(json.dumps({"error": f"Location not found: {location}"}))
            sys.exit(1)
        result = geo_data["results"][0]
        lat = result["latitude"]
        lon = result["longitude"]
        resolved_name = result.get("name", location)
        country = result.get("country", "")

        start_date_obj = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_obj = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

        weather, note = get_weather_range(lat, lon, start_date_obj, end_date_obj, context)

        output = {
            "location": resolved_name,
            "country": country,
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date,
            "end_date": end_date,
            "weather": weather
        }
        if note:
            output["note"] = note

        print(json.dumps(output, indent=2))

    except Exception as e:
        print(json.dumps({"error": f"Failed to fetch weather: {str(e)}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
