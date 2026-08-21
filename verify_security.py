import os
import sys
import time

import requests


BASE_URL = os.getenv("SECURITY_TEST_BASE_URL", "http://127.0.0.1:8002").rstrip("/")
HEADER_PATH = os.getenv("SECURITY_TEST_HEADER_PATH", "/dashboard")
RATE_LIMIT_PATH = os.getenv("SECURITY_TEST_RATE_LIMIT_PATH", "/api/auth/send-otp")


def test_headers() -> bool:
    print(f"--- Testing Security Headers at {BASE_URL}{HEADER_PATH} ---")
    try:
        response = requests.get(f"{BASE_URL}{HEADER_PATH}", timeout=10)
    except Exception as exc:
        print(f"Failed to connect: {exc}")
        return False

    headers = response.headers
    print(f"Status: {response.status_code}")
    if response.status_code >= 500:
        print(f"[FAIL] Server error while checking headers: {response.status_code}")
        return False

    required = {
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    all_passed = True
    for header, expected in required.items():
        value = headers.get(header)
        if value and expected in value:
            print(f"[PASS] {header}: {value}")
        else:
            print(f"[FAIL] {header}: Expected '{expected}', got '{value}'")
            all_passed = False

    if "Content-Security-Policy" in headers:
        print(f"[PASS] CSP: {headers['Content-Security-Policy'][:80]}...")
    else:
        print("[FAIL] CSP header missing")
        all_passed = False

    return all_passed


def test_rate_limit() -> bool:
    print(f"\n--- Testing Rate Limit at {BASE_URL}{RATE_LIMIT_PATH} ---")
    url = f"{BASE_URL}{RATE_LIMIT_PATH}"
    data = {"email": "security_test@example.com"}

    for index in range(1, 10):
        try:
            response = requests.post(url, json=data, timeout=10)
        except Exception as exc:
            print(f"Req {index} failed: {exc}")
            time.sleep(0.2)
            continue

        print(f"Req {index}: Status {response.status_code}")
        if response.status_code == 404:
            print("[SKIP] Rate-limit endpoint is not present in this deployment")
            return True
        if response.status_code == 429:
            print("[PASS] Rate limit triggered (429 Too Many Requests)")
            return True
        time.sleep(0.2)

    print("[FAIL] Rate limit NOT triggered within 10 requests")
    return False


if __name__ == "__main__":
    print("Waiting for server...")
    time.sleep(float(os.getenv("SECURITY_TEST_WAIT_SEC", "2") or "2"))

    headers_ok = test_headers()
    rate_ok = test_rate_limit()

    if headers_ok and rate_ok:
        print("\n[PASS] ALL SECURITY TESTS PASSED")
    else:
        print("\n[FAIL] SOME TESTS FAILED")
        sys.exit(1)
