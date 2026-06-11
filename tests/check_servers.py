import requests
ports = {'strava': 8103, 'garmin': 8104, 'weather': 8101, 'routes': 8102}
for name, port in ports.items():
    try:
        r = requests.get(f'http://127.0.0.1:{port}/', timeout=2)
        print(f'{name}:{port} -> {r.status_code}')
    except Exception as e:
        print(f'{name}:{port} -> DOWN')
