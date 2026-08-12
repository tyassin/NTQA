import sys
import json
import datetime
import calendar
import re

def parse_days(val_str):
    match = re.search(r'\d+', val_str)
    if match:
        return int(match.group())
    return 1

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing arguments"}))
        sys.exit(1)
        
    period = "next week" # default
    duration = 1 # default
    
    # Support both JSON string and positional arguments
    if sys.argv[1].strip().startswith("{"):
        try:
            data = json.loads(sys.argv[1])
            for key, value in data.items():
                k_lower = key.lower()
                if "duration" in k_lower or "days" in k_lower:
                    duration = parse_days(str(value))
                elif "period" in k_lower or "offset" in k_lower or "week or month" in k_lower or "timeframe" in k_lower:
                    period = str(value).strip().lower()
        except Exception as e:
            print(json.dumps({"error": f"Invalid JSON arguments: {str(e)}"}))
            sys.exit(1)
    else:
        period = sys.argv[1].strip().lower()
        if len(sys.argv) >= 3:
            duration = parse_days(sys.argv[2])
            
    try:
        base_date = datetime.datetime.now()
            
        if "month" in period:
            # Add 1 month
            month = base_date.month
            year = base_date.year + month // 12
            month = month % 12 + 1
            day = min(base_date.day, calendar.monthrange(year, month)[1])
            start_date = datetime.datetime(year, month, day, base_date.hour, base_date.minute, base_date.second)
        else:
            # Default to next week (7 days)
            start_date = base_date + datetime.timedelta(days=7)
            
        duration_days = max(1, duration)
        end_date = start_date + datetime.timedelta(days=duration_days - 1)
        
        result = {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d")
        }
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"error": f"Failed to calculate dates: {str(e)}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
