import mygeotab

username = "swatsoftball@yahoo.com"
password = "BudBell12$"
database = "b_b_bros_transport"

def main():
    # Initialize Geotab API client
    api = mygeotab.API(username=username, password=password, database=database)
    api.authenticate()
    
    print(f"Connected to Geotab Server: {api.credentials.server}")
    print(f"Session ID: {api.credentials.session_id}")
    print("-" * 50)
    
    # 1. Fetch Devices (Vehicles)
    devices = api.get("Device")
    print(f"Total Vehicles/Devices: {len(devices)}")
    for dev in devices[:5]:
        print(f"  - [{dev.get('id')}] {dev.get('name')} (VIN: {dev.get('vehicleIdentificationNumber', 'N/A')})")
    
    # 2. Fetch Users (Drivers)
    users = api.get("User")
    print(f"\nTotal Users/Drivers: {len(users)}")
    for u in users[:5]:
        print(f"  - [{u.get('id')}] {u.get('name')} ({u.get('email', 'No Email')})")

if __name__ == "__main__":
    main()
