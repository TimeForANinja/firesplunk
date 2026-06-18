import argparse
import csv
import random
import ipaddress
from datetime import datetime, timedelta

def generate_random_ip():
    return str(ipaddress.IPv4Address(random.getrandbits(32)))

def generate_logs():
    parser = argparse.ArgumentParser(description="Generate sample_accesslog.csv for load testing.")
    
    parser.add_argument("--src-ips", type=int, default=2000, help="Amount of unique source IPs (default: 2000)")
    parser.add_argument("--destinations", type=int, default=500, help="Amount of unique destinations (default: 500)")
    parser.add_argument("--ports", type=int, default=40, help="Amount of unique ports (default: 40)")
    parser.add_argument("--crs", type=int, default=50, help="Amount of unique CRs (rules) (default: 50)")
    parser.add_argument("--logs-per-day", type=int, default=10000, help="Amount of logs per day (default: 10000)")
    parser.add_argument("--start-day", type=str, help="Start day (YYYY-MM-DD)")
    parser.add_argument("--end-day", type=str, help="End day (YYYY-MM-DD)")
    parser.add_argument("--last-n-days", type=int, help="Generate logs for the last N days (default: 7). If provided, --start-day and --end-day are ignored and must not be set.")
    parser.add_argument("--output", type=str, default="sample_accesslog.csv", help="Output filename (default: sample_accesslog.csv)")

    args = parser.parse_args()

    # Validation and date calculation
    if args.last_n_days is not None:
        if args.start_day or args.end_day:
            print("Error: Cannot define both --last-n-days and --start-day/--end-day.")
            return
        n_days = args.last_n_days
    elif args.start_day or args.end_day:
        if not (args.start_day and args.end_day):
            print("Error: Both --start-day and --end-day must be provided if one is.")
            return
        n_days = None
    else:
        # Default to last 7 days
        n_days = 7

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    if n_days is not None:
        end_date = yesterday
        start_date = end_date - timedelta(days=n_days - 1)
    else:
        try:
            start_date = datetime.strptime(args.start_day, "%Y-%m-%d").date()
            end_date = datetime.strptime(args.end_day, "%Y-%m-%d").date()
        except ValueError as e:
            print(f"Error parsing dates: {e}")
            return
    
    if start_date > end_date:
        print("Error: Start date must be before or equal to end date.")
        return

    print(f"Generating logs from {start_date} to {end_date}...")

    # Pre-generate unique pools
    src_ips = [generate_random_ip() for _ in range(args.src_ips)]
    dest_ips = [generate_random_ip() for _ in range(args.destinations)]
    
    # Ports can be single or range like "80:443". For simplicity, let's mix them.
    # Common ports as base
    base_ports = [80, 443, 22, 53, 25, 587, 3389, 8080]
    ports_pool = []
    for _ in range(args.ports):
        if random.random() < 0.2: # 20% chance of range
            p1 = random.randint(1, 65535)
            p2 = random.randint(1, 65535)
            ports_pool.append(f"{min(p1, p2)}:{max(p1, p2)}")
        else:
            ports_pool.append(str(random.randint(1, 65535)))
    
    rules_pool = [f"r-{i}" for i in range(args.crs)]

    days = (end_date - start_date).days + 1
    total_logs = days * args.logs_per_day

    with open(args.output, "w", newline="") as csvfile:
        fieldnames = ["src_ip", "dest_ip", "count", "ports", "date", "rule"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.isoformat()
            
            # Use a dictionary to aggregate logs for the same (src_ip, dest_ip, rule, date)
            # key: (src_ip, dest_ip, rule, date), value: {count: sum, ports: set}
            daily_logs = {}
            
            for _ in range(args.logs_per_day):
                src = random.choice(src_ips)
                dst = random.choice(dest_ips)
                rule = random.choice(rules_pool)
                port = random.choice(ports_pool)
                count = random.randint(1, 100)
                
                key = (src, dst, rule, date_str)
                if key not in daily_logs:
                    daily_logs[key] = {"count": 0, "ports": set()}
                
                daily_logs[key]["count"] += count
                daily_logs[key]["ports"].add(port)

            for (src, dst, rule, d_str), data in daily_logs.items():
                writer.writerow({
                    "src_ip": src,
                    "dest_ip": dst,
                    "count": data["count"],
                    "ports": ",".join(sorted(list(data["ports"]))),
                    "date": d_str,
                    "rule": rule
                })
            current_date += timedelta(days=1)

    print(f"Successfully generated {total_logs} logs in {args.output}")

if __name__ == "__main__":
    generate_logs()
