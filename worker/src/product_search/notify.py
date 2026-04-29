import os
import httpx
from typing import Optional

def notify_material_change(product_slug: str, headline: str, url: Optional[str] = None) -> bool:
    """
    Sends a push notification request to the web API for material changes.
    """
    web_url = os.environ.get("WEB_URL")
    secret = os.environ.get("PUSH_NOTIFY_SECRET")

    if not web_url or not secret:
        print("Skipping push notification: WEB_URL or PUSH_NOTIFY_SECRET not set in env.")
        return False

    api_url = f"{web_url.rstrip('/')}/api/push/notify"
    
    payload = {
        "product": product_slug,
        "headline": headline,
    }
    if url:
        payload["url"] = url

    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json"
    }

    try:
        response = httpx.post(api_url, json=payload, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            print(f"Push notification had errors: {data['errors']}")
        print(f"Push notification sent successfully to {data.get('sent', 0)} devices.")
        return True
    except httpx.HTTPError as e:
        print(f"Failed to send push notification: {e}")
        if isinstance(e, httpx.HTTPStatusError):
            print(f"Response: {e.response.text}")
        return False
