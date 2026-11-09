# IMS user manual

## Signing in
- Sign in with your username and password. If you work at more than one branch, pick the branch at the top. Your role (cashier, manager…) applies only at that branch.
- **Offline:** if you signed in on this till within the last 24 hours, you can sign in without internet and take **cash** sales.
- **Too many wrong passwords** lock the account for a while: the Sign in button counts down until you can try again. An administrator can check the account if you're stuck.
- **No branch chosen?** Owners and admins pick one in the **branch picker** at the top of the sidebar; stock, the till and branch reports appear once a branch is chosen.

## Typing amounts and quantities
- Amounts can be typed as `500`, `500.50`, `Rs 500`, `500/-` or in Nepali digits (`५००`). At most 2 decimal places (paisa).
- Quantities are whole numbers. A box with something it can't use turns red and says why underneath; a grey button says what is missing next to it.
- Clicking into a box selects what's there, so typing replaces it.

## Selling (POS)
1. **Open a shift**: count the drawer and enter the opening cash (0 if it's empty).
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
6. **Unfinished sale**: if the page reloads, the browser closes or you have to sign in again mid-sale, the cart comes back when you return to the POS (same cashier, within 12 hours). Choose *Start a new sale instead* if you don't want it.
7. **Printing**: the first print is the **original**. Every later print is stamped **"COPY OF ORIGINAL — Copy #n"**. Goods over Rs 10,000 can also get a **Delivery copy**.

### When the internet is down
- The status line at the bottom of the sidebar shows **Offline**. Only cash sales work. FonePay, returns, held sales and stock changes need the server.
- With per-branch invoice numbering (the default), the till prints a **provisional slip**. The tax invoice is issued when the till reconnects. Print it from **Sales → Unprinted** and hand it to the customer if they need it.
- Queued sales sync automatically. The sidebar shows **N queued** (and **failed** in red if the server refused one). Click it to see each sale and why it failed, **Retry failed**, or **Download a copy**. Keep a copy before clearing the browser's data: queued sales live only on that till.

### Returns, cancellations and corrections
- **Return / refund**: POS → *Return*, enter the invoice number, choose the quantities and a reason. This issues a **credit note (CN-…)**.
- **Cancel an invoice** (manager): Sales → open the invoice → *Cancel invoice*, with a reason. The invoice keeps its number and stays printable, marked CANCELLED.
- **Undercharged?** Raise a **debit note (DN-…)** from the invoice. Issued invoices can never be edited, only corrected by notes.

### Closing the shift
Count the drawer and enter the counted cash. The variance is recorded.

## Stock
- **Inventory**: stock on hand, low stock, negative stock, and every movement. Click a number at the top (*Low stock*, *Out of stock*) to list those products.
- **Reorder points**: the gear icon on a product sets when it counts as low and how much to reorder. Products without a reorder point never show as low.
- **Restocking**: in the *Low stock* or *Out of stock* list, **Order these** starts a purchase order with those products (quantity = reorder quantity, cost = current average cost; check both before saving). A single product: **Order** on its row.
- **Adjust**: remove damaged/expired stock or add found stock, always with a reason. *Found* stock without a purchase record shows up as **excess stock** in the reconciliation report, so record the supplier invoice where you can.
- **Transfers**: create, dispatch (stock leaves), receive (record any shortfall with a reason).
- **Stocktakes**: start a full or category count, enter counts, then finish. Big variances need a reason.

## Purchasing
1. Create a purchase order (or start one from Inventory → *Order these*) and *Mark ordered*.
2. When goods arrive, *Receive goods*: enter the **supplier invoice number and date** (needed for the purchase book) and the actual cost. Partial deliveries are fine.
3. Goods going back to the supplier: **Purchasing → Purchase returns**. This reverses the input VAT.
4. Make sure suppliers have their **PAN** recorded.

## Importing products and opening stock
**Import** → download the template, fill it in a spreadsheet and save it as **CSV or Excel (.xlsx)** (for Excel the first sheet is read). Upload it, press **Preview** (nothing is saved), fix any rows it lists, then **Commit import**. Importing the same file twice never doubles stock.

## Tax & IRD (managers, accountants)
- **Reports** (choose the fiscal year and BS month): Sales book (Schedule-9), Purchase book (Schedule-8), Sales and purchase return registers, **VAT return working** (with due date and nil-return flag), Cancelled bills, Stock vs purchase-book reconciliation, CBMS sync, Audit trail. Every report exports to **Excel, XML, PDF and CSV**.
- **Accountant export**: one zip with all the books for the month.
- **CBMS**: shows pending, synced and failed bills sent to IRD. If CBMS says the password is wrong, posting pauses: an admin updates the password in Settings and it resumes.
- **Turnover meter**: warns as turnover approaches the Rs 10 crore and Rs 20 crore CBMS lines.
- **Settings** (org admin): seller name and PAN, invoice series, invoice limits, CBMS login, digital-payment VAT discount (keep it off until IRD publishes the procedure).
- **Tax categories**: VAT 13%, 5%, zero-rated, exempt, HST… When the budget changes a rate, **add a new rate with its effective date**. Don't edit the old one, because history must stay correct.

## Hardware (per till)
Set up on each till in **POS → Devices**. Settings are saved in that computer's browser, not on the server. Everything runs in **Chrome or Edge**, over the app's **https** address (or localhost); no extra software is installed.

- **Receipt printer**: choose *USB thermal printer* or *Serial printer* and click **Pair printer**, then **Print test page**. Pick 58 or 80 mm paper. Abbreviated invoices print on the thermal printer with the invoice number as a barcode; full tax invoices and credit notes still open the print dialog (they need Devanagari). To print those without the dialog, start Chrome with `--kiosk-printing`.
  - *Linux*: if pairing says the printer is in use, run `echo "<bus-port>:1.0" | sudo tee /sys/bus/usb/drivers/usblp/unbind` (or blacklist `usblp`) and add a udev rule giving your user access to the printer.
  - *Windows*: install the WinUSB driver for the printer with Zadig. The printer then disappears from Windows printers but works from the POS.
- **Cash drawer**: plug it into the printer's drawer (RJ11/DK) port. It opens after cash sales. Use **Test drawer** to check.
- **Barcode scanner**: USB or Bluetooth scanners in keyboard mode need no setup. Set the scanner to send **Enter** after each code. Scans work anywhere on the POS screen; a high beep means found, a low beep means the code is unknown. Tap the camera icon in the search box to scan with a phone or tablet camera.
- **Barcode labels**: Products → tick products → **Print labels**. Choose A4 sticker sheets (65 or 24 per page; *Skip used labels* for part-used sheets) or a label-printer roll. In the print dialog set margins to *None* and scale to *100%*. Products without a manufacturer barcode: edit → **Generate in-store code** (EAN-13 starting with 2).
- **Customer display**: Devices → **Open customer display**, drag the window to the customer-facing monitor and press F11. It shows the items, total, the FonePay QR while paying, and the change. A serial 2×20 **pole display** can be paired in the same place.
- Camera scanning works offline only after it has been used once online on that till.

## Dates
Every document shows **BS and AD** dates. The fiscal year runs Shrawan 1 – Asar end (FY 2083/84 = 17 Jul 2026 – 16 Jul 2027).
