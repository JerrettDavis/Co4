"""Wait up to 30 seconds for a local test server; exit nonzero on failure."""
import sys
import time
import urllib.error
import urllib.request
for _ in range(60):
    try:
        with urllib.request.urlopen(sys.argv[1],timeout=2) as response:
            if response.status==200:break
    except (urllib.error.URLError,TimeoutError):
        pass
    time.sleep(.5)
else:
    raise SystemExit('Test server did not become healthy')
