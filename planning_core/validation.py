from planning_core.repository import CanonicalRepository


def basic_health_report(repository: CanonicalRepository) -> dict:
    transactions = repository.load_table("transactions")
    inventory = repository.load_table("inventory_snapshot")
    transfers = repository.load_table("internal_transfers")
    purchase_orders = repository.load_table("purchase_orders")
    purchase_order_lines = repository.load_table("purchase_order_lines")
    purchase_receipts = repository.load_table("purchase_receipts")

    return {
        "tx_duplicate_keys": int(transactions.duplicated(["date", "sku", "location"]).sum()),
        "inventory_duplicate_keys": int(inventory.duplicated(["snapshot_date", "sku", "location"]).sum()),
        "transfer_duplicate_ids": int(transfers.duplicated(["transfer_id"]).sum()),
        "po_duplicate_ids": int(purchase_orders.duplicated(["po_id"]).sum()),
        "po_line_duplicate_ids": int(purchase_order_lines.duplicated(["po_line_id"]).sum()),
        "receipt_duplicate_ids": int(purchase_receipts.duplicated(["receipt_id"]).sum()),
        "negative_tx_qty": int((transactions["quantity"] < 0).sum()),
        "negative_on_hand": int((inventory["on_hand_qty"] < 0).sum()),
        "negative_on_order": int((inventory["on_order_qty"] < 0).sum()),
        "negative_transfer_qty": int((transfers["transfer_qty"] < 0).sum()),
        "negative_receipt_qty": int((purchase_receipts["received_qty"] < 0).sum()),
        "open_transfers_without_receipt_date": int(
            ((transfers["transfer_status"] == "open") & (transfers["receipt_date"].isna())).sum()
        ),
    }
