# IMS user manual

## Signing in
- Sign in with your username and password. If you work at more than one branch, pick the branch at the top. Your role (cashier, manager…) applies only at that branch.
- **Offline:** if you signed in on this till within the last 24 hours, you can sign in without internet and take **cash** sales.

## Selling (POS)
1. **Open a shift**: count the drawer and enter the opening cash.
2. **Add items**: scan a barcode, or type a name or SKU and press Enter. A case barcode adds the whole pack. F2 jumps to the search box.
3. **Discounts**: click *Add* on a line, or use *Sale discount*. Every discount needs a reason, and large discounts need a manager's approval at the till. Discounts are taken **before VAT**.
4. **Buyer details**: click *Buyer* to enter the name, address and PAN.
   - With a **PAN**, the bill is a full **tax invoice (कर बीजक)**.
   - Bills **over Rs 10,000** must be full tax invoices: the till asks for the buyer's name and address.
   - Other walk-in bills are **abbreviated tax invoices**.
5. **Pay**:
   - **Cash (F9)**: enter the amount tendered; the change is shown.
   - **FonePay QR**: the customer scans the QR. The sale completes when FonePay confirms (needs internet).
   - **Card / Wallet / Bank**: enter the slip or transaction reference.
6. **Printing**: the first print is the **original**. Every later print is stamped **"COPY OF ORIGINAL — Copy #n"**. Goods over Rs 10,000 can also get a **Delivery copy**.

### When the internet is down
- The top bar shows **Offline**. Only cash sales work. FonePay, returns, held sales and stock changes need the server.
- With per-branch invoice numbering (the default), the till prints a **provisional slip**. The tax invoice is issued when the till reconnects. Print it from **Sales → Unprinted** and hand it to the customer if they need it.
- Queued sales sync automatically. If a sale can't sync, the top bar shows it in red and your manager will review it.

### Returns, cancellations and corrections
- **Return / refund**: POS → *Return*, enter the invoice number, choose the quantities and a reason. This issues a **credit note (CN-…)**.
- **Cancel an invoice** (manager): Sales → open the invoice → *Cancel invoice*, with a reason. The invoice keeps its number and stays printable, marked CANCELLED.
- **Undercharged?** Raise a **debit note (DN-…)** from the invoice. Issued invoices can never be edited, only corrected by notes.

### Closing the shift
Count the drawer and enter the counted cash. The variance is recorded.

## Stock
- **Inventory**: stock on hand, low stock, negative stock, and every movement.
- **Adjust**: remove damaged/expired stock or add found stock, always with a reason. *Found* stock without a purchase record shows up as **excess stock** in the reconciliation report, so record the supplier invoice where you can.
- **Transfers**: create, dispatch (stock leaves), receive (record any shortfall with a reason).
- **Stocktakes**: start a full or category count, enter counts, then finish. Big variances need a reason.

## Purchasing
1. Create a purchase order and *Mark ordered*.
2. When goods arrive, *Receive goods*: enter the **supplier invoice number and date** (needed for the purchase book) and the actual cost. Partial deliveries are fine.
3. Goods going back to the supplier: **Purchasing → Purchase returns**. This reverses the input VAT.
4. Make sure suppliers have their **PAN** recorded.

## Tax & IRD (managers, accountants)
- **Reports** (choose the fiscal year and BS month): Sales book (Schedule-9), Purchase book (Schedule-8), Sales and purchase return registers, **VAT return working** (with due date and nil-return flag), Cancelled bills, Stock vs purchase-book reconciliation, CBMS sync, Audit trail. Every report exports to **Excel, XML, PDF and CSV**.
- **Accountant export**: one zip with all the books for the month.
- **CBMS**: shows pending, synced and failed bills sent to IRD. If CBMS says the password is wrong, posting pauses: an admin updates the password in Settings and it resumes.
- **Turnover meter**: warns as turnover approaches the Rs 10 crore and Rs 20 crore CBMS lines.
- **Settings** (org admin): seller name and PAN, invoice series, invoice limits, CBMS login, digital-payment VAT discount (keep it off until IRD publishes the procedure).
- **Tax categories**: VAT 13%, 5%, zero-rated, exempt, HST… When the budget changes a rate, **add a new rate with its effective date**. Don't edit the old one, because history must stay correct.

## Dates
Every document shows **BS and AD** dates. The fiscal year runs Shrawan 1 – Asar end (FY 2083/84 = 17 Jul 2026 – 16 Jul 2027).
