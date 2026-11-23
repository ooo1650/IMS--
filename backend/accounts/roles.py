"""Role -> permission map (design doc §7). Deny by default: a permission not
listed for a role is not granted."""

SUPER_ADMIN = "SuperAdmin"
ORG_ADMIN = "OrgAdmin"
BRANCH_MANAGER = "BranchManager"
CASHIER = "Cashier"
INVENTORY_CLERK = "InventoryClerk"
VIEWER = "Viewer"

ROLE_CHOICES = [
    (SUPER_ADMIN, "Super admin"),
    (ORG_ADMIN, "Org admin"),
    (BRANCH_MANAGER, "Branch manager"),
    (CASHIER, "Cashier"),
    (INVENTORY_CLERK, "Inventory clerk"),
    (VIEWER, "Viewer"),
]

ORG_ONLY_ROLES = {SUPER_ADMIN, ORG_ADMIN}
BRANCH_ONLY_ROLES = {BRANCH_MANAGER, CASHIER, INVENTORY_CLERK}

ALL_PERMISSIONS = {
    "pos.sell",            # ring up sales, take payments
    "pos.void",            # void a paid sale
    "shift.manage",        # open/close own cashier shift
    "returns.create",      # start a return / credit note
    "returns.approve",     # approve returns over the threshold
    "discount.approve",    # approve manager-only discounts
    "inventory.view",
    "inventory.adjust",    # stock in/out adjustments
    "transfer.manage",
    "stocktake.manage",
    "purchasing.manage",
    "catalog.view",
    "catalog.manage",      # create/edit products, prices, archive
    "reports.view",
    "conflicts.resolve",   # resolve stock_conflict sales
    "dashboard.view",      # org-wide dashboard
    "users.manage",
    "branches.manage",
    "audit.view",
    "import.run",
    "staff.manage",        # staff list and their pay (business-wide roles only)
}

ROLE_PERMISSIONS = {
    SUPER_ADMIN: set(ALL_PERMISSIONS),
    ORG_ADMIN: set(ALL_PERMISSIONS),
    BRANCH_MANAGER: {
        "pos.sell", "pos.void", "shift.manage", "returns.create", "returns.approve",
        "discount.approve", "inventory.view", "inventory.adjust", "transfer.manage",
        "stocktake.manage", "purchasing.manage", "catalog.view", "reports.view",
        "conflicts.resolve", "audit.view", "import.run",
    },
    CASHIER: {"pos.sell", "shift.manage", "returns.create", "catalog.view"},
    INVENTORY_CLERK: {
        "inventory.view", "inventory.adjust", "transfer.manage", "stocktake.manage",
        "catalog.view",
    },
    VIEWER: {"reports.view", "inventory.view", "catalog.view"},
}


def permissions_for(role):
    return ROLE_PERMISSIONS.get(role, set())
