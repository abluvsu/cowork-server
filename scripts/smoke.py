import sys
import httpx

BASE_URL = "http://127.0.0.1:26866"

try:
    # 1. GET /api/v1/health -> 200 (check both forms without and with trailing slash)
    client = httpx.Client(follow_redirects=True)
    
    try:
        r1 = httpx.get(f"{BASE_URL}/api/v1/health", follow_redirects=False)
    except httpx.ConnectError:
        print("SKIP stack not running")
        sys.exit(0)
    
    if r1.status_code != 200:
        print(f"FAIL health: GET /api/v1/health returned {r1.status_code}")
        sys.exit(1)
    
    r1_slash = httpx.get(f"{BASE_URL}/api/v1/health/", follow_redirects=False)
    if r1_slash.status_code != 200:
        print(f"FAIL health: GET /api/v1/health/ returned {r1_slash.status_code}")
        sys.exit(1)
    
    print("OK health")

    # 2. GET /api/v1/providers/ -> 200, JSON list
    r2 = client.get(f"{BASE_URL}/api/v1/providers/")
    if r2.status_code != 200:
        print(f"FAIL providers: GET /api/v1/providers/ returned {r2.status_code}")
        sys.exit(1)
    try:
        providers = r2.json()
        if not isinstance(providers, list):
            raise ValueError("not a list")
    except Exception as e:
        print(f"FAIL providers: Invalid JSON list: {e}")
        sys.exit(1)
    print("OK providers")

    # 3. GET /api/v1/conversations/?limit=1 (follow redirect) -> 200
    r3 = client.get(f"{BASE_URL}/api/v1/conversations/", params={"limit": 1})
    if r3.status_code != 200:
        print(f"FAIL conversations: GET /api/v1/conversations/ returned {r3.status_code}")
        sys.exit(1)
    print("OK conversations")

    # 4. GET /api/v1/settings/mcp_servers_json (follow redirect) -> 200 and parses as JSON list with id+command
    r4 = client.get(f"{BASE_URL}/api/v1/settings/mcp_servers_json")
    if r4.status_code != 200:
        print(f"FAIL mcp_servers_json: GET /api/v1/settings/mcp_servers_json returned {r4.status_code}")
        sys.exit(1)
    try:
        mcp_servers = r4.json()
        if not isinstance(mcp_servers, list):
            raise ValueError("not a list")
        for item in mcp_servers:
            if not isinstance(item, dict) or "id" not in item or "command" not in item:
                raise ValueError("entry missing id or command")
    except Exception as e:
        print(f"FAIL mcp_servers_json: Invalid JSON list of servers: {e}")
        sys.exit(1)
    print("OK mcp_servers_json")

    # 5. If port 20128 answers /v1/models -> 200, print OK omniroute, else WARN omniroute down (non-fatal)
    try:
        r5 = httpx.get("http://127.0.0.1:20128/v1/models", timeout=3.0)
        if r5.status_code == 200:
            print("OK omniroute")
        else:
            print(f"WARN omniroute down (non-fatal): returned {r5.status_code}")
    except Exception as e:
        print(f"WARN omniroute down (non-fatal): {e}")

except Exception as e:
    print(f"FAIL unexpected error: {e}")
    sys.exit(1)
