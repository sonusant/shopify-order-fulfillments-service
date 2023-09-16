import os
import json
import logging
import functions_framework
import requests


# --- CONFIGURATION ---
# In a real environment, you would map WMS location codes to Shopify Location IDs
# For example, "WH-001" corresponds to Shopify Location ID 1234567890
WAREHOUSE_LOCATION_MAP = {
    "WH-001": 1234567890,
    "WH-002": 9876543210
}

SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2023-10")

def respond(data, status=200):
    return (json.dumps(data), status, {'Content-Type': 'application/json'})

# --- SHOPIFY API CLIENT ---
class ShopifyClient:
    def __init__(self, store, token, version):
        self.base_url = f"https://{store}/admin/api/{version}"
        self.headers = {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _get(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint}"
        response = requests.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, endpoint, data):
        url = f"{self.base_url}/{endpoint}"
        response = requests.post(url, headers=self.headers, json=data)
        response.raise_for_status()
        return response.json()

    def get_order_id_by_name(self, order_name):
        """Look up an order by its name (e.g., '#1001')"""
        data = self._get("orders.json", params={"name": order_name, "status": "any", "fields": "id,name"})
        orders = data.get("orders", [])
        if not orders:
            return None
        # Try to find the exact match
        for order in orders:
            if order.get("name") == order_name:
                return order.get("id")
        return orders[0].get("id")

    def get_fulfillment_orders(self, order_id):
        """Get all fulfillment orders for a specific order"""
        data = self._get(f"orders/{order_id}/fulfillment_orders.json")
        return data.get("fulfillment_orders", [])

    def create_fulfillment(self, fulfillment_order_id, tracking_number, tracking_company):
        """Create a fulfillment for a given fulfillment order"""
        payload = {
            "fulfillment": {
                "message": "Fulfilled via automated WMS integration.",
                "notify_customer": True,
                "tracking_info": {
                    "number": tracking_number,
                    "company": tracking_company
                },
                "line_items_by_fulfillment_order": [
                    {
                        "fulfillment_order_id": fulfillment_order_id
                    }
                ]
            }
        }
        return self._post("fulfillments.json", payload)

# --- CLOUD FUNCTION ENTRYPOINT ---
@functions_framework.http
def process_fulfillment(request):
    """HTTP Cloud Function to process fulfillment requests."""
    
    # 1. Validate environment
    if not SHOPIFY_STORE or not SHOPIFY_ACCESS_TOKEN:
        print("Missing required environment variables")
        return respond({"error": "Internal server configuration error"}, 200)

    # 2. Parse JSON payload
    try:
        payload = request.get_json()
    except:
        return respond({"error": "Invalid or missing JSON payload"}, 200)

    order_id = payload.get("order_id")
    order_name = payload.get("order_name")
    warehouse_reference = payload.get("warehouse_reference")
    tracking_number = payload.get("tracking_number")
    tracking_company = payload.get("tracking_company")

    if not order_id and not order_name:
        return respond({"error": "Either order_id or order_name must be provided"}, 200)
    
    if not warehouse_reference or not tracking_number or not tracking_company:
        return respond({"error": "Missing required fields"}, 200)

    # 3. Resolve Shopify Location ID from Warehouse Reference
    shopify_location_id = WAREHOUSE_LOCATION_MAP.get(warehouse_reference)
    if not shopify_location_id:
        print(f"Unknown warehouse reference: {warehouse_reference}")
        return respond({"error": f"Warehouse '{warehouse_reference}' not mapped to a Shopify Location"}, 200)

    client = ShopifyClient(SHOPIFY_STORE, SHOPIFY_ACCESS_TOKEN, SHOPIFY_API_VERSION)

    try:
        # 4. Determine Order ID
        if not order_id:
            print(f"Looking up order by name: {order_name}")
            order_id = client.get_order_id_by_name(order_name)
            if not order_id:
                return respond({"error": f"Order not found for name '{order_name}'"}, 200)
        
        print(f"Processing fulfillment for Order ID: {order_id}")

        # 5. Get Fulfillment Orders
        fulfillment_orders = client.get_fulfillment_orders(order_id)
        if not fulfillment_orders:
            return respond({"error": f"No fulfillment orders found for order {order_id}"}, 404)

        # 6. Find the matching Fulfillment Order for our location
        target_fo = None
        for fo in fulfillment_orders:
            if fo.get("assigned_location_id") == shopify_location_id:
                target_fo = fo
                break

        if not target_fo:
            return respond({
                "error": f"No fulfillment order found assigned to location {shopify_location_id} (Warehouse: {warehouse_reference})"
            }, 404)

        fo_id = target_fo.get("id")
        fo_status = target_fo.get("status")

        # 7. Validate Status (Idempotency)
        if fo_status == "closed":
            print(f"Fulfillment Order {fo_id} is already closed. Assuming success.")
            return respond({
                "message": "Fulfillment order already closed",
                "fulfillment_order_id": fo_id,
                "order_id": order_id
            }, 200)
        
        if fo_status not in ["open", "in_progress"]:
            return respond({
                "error": f"Fulfillment order {fo_id} is not in a fulfillable state (current status: {fo_status})"
            }, 200)

        # 8. Create the Fulfillment
        print(f"Creating fulfillment for Fulfillment Order ID: {fo_id} with tracking {tracking_number}")
        result = client.create_fulfillment(
            fulfillment_order_id=fo_id,
            tracking_number=tracking_number,
            tracking_company=tracking_company
        )

        fulfillment_id = result.get("fulfillment", {}).get("id")
        print(f"Successfully created fulfillment {fulfillment_id} for order {order_id}")

        return respond({
            "message": "Fulfillment created successfully",
            "fulfillment_id": fulfillment_id,
            "fulfillment_order_id": fo_id,
            "order_id": order_id
        }, 201)

    except requests.exceptions.RequestException as e:
        print(f"Shopify API Error: {str(e)}")
        if e.response is not None:
            try:
                error_details = e.response.json()
                print(f"Shopify Error Details: {json.dumps(error_details)}")
                if 400 <= e.response.status_code < 500:
                    return respond({
                        "error": "Shopify API rejected the request",
                        "details": error_details
                    }, e.response.status_code)
            except Exception:
                print("Shopify API Error Details: Unexpected error")
                pass
        
        return respond({"error": "Failed to communicate with Shopify API"}, 500)
    except Exception as e:
        print("Unexpected internal error")
        return respond({"error": "An unexpected error occurred"}, 500)
