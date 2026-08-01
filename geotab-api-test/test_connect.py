import mygeotab

passwords = ["BudBell12$", "BudBell12"]
username = "swatsoftball@yahoo.com"
database = "b_b_bros_transport"

for pwd in passwords:
    print(f"Attempting login for {username} on DB {database}...")
    try:
        api = mygeotab.API(username=username, password=pwd, database=database)
        client = api.authenticate()
        print("SUCCESS!")
        print(f"Server: {api.credentials.server}")
        print(f"Session ID: {api.credentials.session_id}")
        
        # Test basic query
        devices = api.get("Device")
        print(f"Total devices found: {len(devices)}")
        if devices:
            print(f"Sample Device: {devices[0].get('name')} (ID: {devices[0].get('id')})")
        break
    except Exception as e:
        print(f"Failed with password option: {e}")
