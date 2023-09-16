# Shopify Fulfillment Cloud Function

This project provides a Google Cloud Function (Gen2) written in Python 3.11 that automatically creates fulfillments in Shopify using the Shopify Admin REST API. It is designed to act as a webhook receiver from an external Warehouse Management System (WMS) or 3PL.

## Project Overview

When a warehouse successfully ships an order, it sends a payload to this Cloud Function containing tracking information. The function parses the request, determines the correct Shopify Fulfillment Order for the matching warehouse location, and creates a fulfillment in Shopify, optionally notifying the customer.

## Fulfillment Workflow

```
External System (WMS)
        ↓
Google Cloud Function (HTTP Trigger)
        ↓
1. Validate JSON Payload
2. Look up Order ID by `order_name` (if ID not provided)
3. Retrieve Fulfillment Orders for the Order
4. Match WMS `warehouse_reference` to Shopify `location_id`
5. Verify Fulfillment Order is in `open` or `in_progress` state
6. Create Fulfillment via Shopify Admin API
7. Return success to WMS
```

## Shopify Fulfillment Order Concepts

Shopify now strongly recommends managing fulfillments via **Fulfillment Orders** rather than directly fulfilling line items.
- A **Fulfillment Order** represents work intended to be done at a specific location.
- A **Fulfillment** represents the work that has been completed (i.e., shipped with tracking).

Our system queries the `fulfillment_orders.json` endpoint to find the work assigned to our specific location, and then fulfills it using `fulfillments.json`.

## Required Shopify Scopes

The app or custom token used by this function requires the following access scopes:
- `read_orders` / `read_all_orders` (to look up orders by name)
- `write_merchant_managed_fulfillment_orders` or `write_assigned_fulfillment_orders`
- `write_fulfillments`

## Architecture & Design Decisions

- **Python 3.11** for a lightweight serverless environment.
- **REST Admin API**: Utilizes Shopify's REST endpoints (`/orders.json`, `/fulfillment_orders.json`, `/fulfillments.json`).
- **Global Variable Mapping**: Warehouse references from the WMS are mapped to Shopify Location IDs using a global variable in `main.py`.

## Environment Variables

Copy `.env.example` to `.env` or set these in your Google Cloud Function deployment:

- `SHOPIFY_STORE`: The `.myshopify.com` domain (e.g., `my-store.myshopify.com`).
- `SHOPIFY_ACCESS_TOKEN`: The Admin API access token starting with `shpat_`.
- `SHOPIFY_API_VERSION`: The API version (e.g., `2023-10`).

## Warehouse Mapping Configuration

Inside `main.py`, you must configure the `WAREHOUSE_LOCATION_MAP` dictionary to map incoming WMS references to your actual Shopify Location IDs.

```python
WAREHOUSE_LOCATION_MAP = {
    "WH-001": 1234567890,
    "WH-002": 9876543210
}
```

## Local Testing

You can run the function locally using the `functions-framework`.

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the local server:
   ```bash
   functions-framework --target=process_fulfillment --source=main.py --debug
   ```

## Cloud Function Deployment

Deploy to Google Cloud Functions Gen 2:

```bash
gcloud functions deploy process-fulfillment \
  --gen2 \
  --runtime=python311 \
  --region=us-central1 \
  --source=. \
  --entry-point=process_fulfillment \
  --trigger-http \
  --allow-unauthenticated \
  --set-env-vars SHOPIFY_STORE=your-store.myshopify.com,SHOPIFY_ACCESS_TOKEN=shpat_...,SHOPIFY_API_VERSION=2023-10
```

## Sample Requests

### Standard Fulfillment by Order Name
```bash
curl -X POST https://YOUR_REGION-YOUR_PROJECT.cloudfunctions.net/process-fulfillment \
  -H "Content-Type: application/json" \
  -d '{
    "order_name": "#1001",
    "warehouse_reference": "WH-001",
    "tracking_number": "TRACK123456",
    "tracking_company": "BlueDart"
  }'
```

### Request by Order ID
```bash
curl -X POST https://YOUR_REGION-YOUR_PROJECT.cloudfunctions.net/process-fulfillment \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": 1234567890123,
    "warehouse_reference": "WH-002",
    "tracking_number": "TRACK987654",
    "tracking_company": "FedEx"
  }'
```

## Sample Responses

**Success (201 Created)**
```json
{
  "message": "Fulfillment created successfully",
  "fulfillment_id": 987654321,
  "fulfillment_order_id": 1046000799,
  "order_id": 1234567890123
}
```

**Already Fulfilled / Closed (200 OK - Idempotent)**
```json
{
  "message": "Fulfillment order already closed",
  "fulfillment_order_id": 1046000799,
  "order_id": 1234567890123
}
```

**Validation Error (200 OK - Prevents WMS Retry)**
```json
{
  "error": "Either order_id or order_name must be provided"
}
```

## Error Handling & Logging Strategy

- **Validation errors** (missing fields, invalid locations, invalid state) return an HTTP 200 response with an error message to prevent the external WMS system from constantly retrying invalid requests.
- **Shopify API Validation errors** resulting from bad data format return an HTTP 4xx to the WMS (where supported) to flag bad data.
- **Transient network issues or Shopify 5xx errors** raise an exception, returning an HTTP 500. This triggers Cloud Functions' built-in retry mechanism (if enabled), ensuring temporary glitches don't cause permanent failure.
- Simple `print()` statements are used for logging tracking info and exceptions directly to standard output, which are automatically captured by Google Cloud.

## Retry Considerations & Idempotency

External webhooks can be retried automatically by the WMS. To prevent duplicate fulfillments:
1. We check the status of the target Fulfillment Order.
2. If it is already `closed`, the function assumes success and returns HTTP 200 without attempting to modify Shopify.
3. This guarantees the webhook can be safely retried.

## Future Improvements
- **Authentication**: Add an API key check or HMAC signature validation to the webhook endpoint to secure it from unauthorized requests.
- **Partial Fulfillments**: Enhance payload to support fulfilling specific line items or quantities rather than the entire fulfillment order at once.
