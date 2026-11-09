export interface BranchAccess {
  id: number
  name: string
  code: string
  timezone: string
  role: string
  permissions: string[]
}

export interface Me {
  id: number
  username: string
  email: string
  email_verified: boolean
  /** Opted in to the emailed sign-in code. */
  otp_enabled: boolean
  /** Must enter a code at sign-in (opted in, or the role requires it). */
  otp_required: boolean
  name: string
  /** Operator account for the console; holds no business roles. */
  is_platform_admin?: boolean
  /** Every business this user works for (switcher shown when more than one). */
  businesses: { id: number; name: string; code: string; role: string | null }[]
  active_org_id: number | null
  active_org_name: string | null
  /** Role and permissions in the active business. */
  org_role: string | null
  org_permissions: string[]
  branches: BranchAccess[]
  active_branch_id: number | null
  /** Testing phase: one-click sign-in is on (server IMS_TEST_LOGIN). */
  test_login?: boolean
}

export interface Barcode {
  id?: number
  code: string
  type: string
  pack_qty: number
}

export interface Product {
  id: number
  sku: string
  name: string
  category: number | null
  category_name: string | null
  supplier: number | null
  supplier_name: string | null
  unit: string
  cost_price_paisa?: number
  sale_price_paisa: number
  branch_price_paisa: number | null
  tax_rate: string
  tax_inclusive: boolean
  tax_category: number | null
  tax_category_code: string | null
  hs_code: string
  excise_rate: string
  input_credit_blocked_pct: number
  is_active: boolean
  archived_at: string | null
  barcodes: Barcode[]
  on_hand: number | null
  /** Active branch's reorder point (null when the product isn't stocked there). */
  reorder_point: number | null
  /** Product photo (800 px) and thumbnail (160 px); null without a photo. */
  image_url: string | null
  thumb_url: string | null
}

/** Offline catalog entry — no cost fields by design. */
export interface PosProduct {
  id: number
  sku: string
  name: string
  unit: string
  sale_price_paisa: number
  tax_rate: string
  tax_inclusive: boolean
  tax_kind?: 'standard' | 'reduced' | 'zero' | 'exempt'
  excise_rate?: string
  levy_type?: '' | 'hst' | 'esf'
  levy_rate?: string
  barcodes: { code: string; pack_qty: number }[]
  stock_estimate: number
  /** Thumbnail URL; absent in catalogs cached before photos existed. */
  thumb?: string | null
}

export interface Shift {
  id: number
  register: number
  register_code: string
  cashier_name: string
  status: 'open' | 'closed'
  opened_at: string
  opening_cash_paisa: number
  closed_at: string | null
  counted_cash_paisa: number | null
  expected_cash_paisa: number | null
  variance_paisa: number | null
}

export interface RegisterCfg {
  id: number
  code: string
  name: string
  printer_driver: 'browser' | 'network_escpos' | 'usb_bridge'
  printer_config: { url?: string }
  next_receipt_number: number
  open_shift: Shift | null
}

export interface PosConfig {
  branch: { id: number; code: string; name: string; address: string; timezone: string }
  seller: { name: string; name_np: string; pan: string }
  fiscal_year: string
  invoice_series: 'branch' | 'register'
  abbreviated_invoice_limit_paisa: number
  delivery_copy_limit_paisa: number
  digital_vat_discount_bp: number
  digital_vat_discount_methods: string[]
  cash_rounding_paisa: number
  cashier_max_discount_bp: number
  return_approval_threshold_paisa: number
  offline_login_max_age_hours: number
  fonepay_backend: string
  registers: RegisterCfg[]
}

interface SaleLine {
  id: number
  product: number
  sku: string
  product_name: string
  qty: number
  unit_price_paisa: number
  discount_type: string
  discount_value: number
  line_discount_paisa: number
  line_total_paisa: number
  tax_rate: string
  tax_amount_paisa: number
  returned_qty: number
}

export type PayMethod = 'cash' | 'fonepay' | 'card' | 'wallet' | 'bank'

interface Payment {
  id: number
  method: PayMethod
  amount_paisa: number
  tendered_paisa: number
  change_paisa: number
  ref: string
  fonepay_prn: string | null
  status: string
}

export interface Sale {
  id: number
  branch_code: string
  register: number | null
  register_code: string | null
  cashier_name: string
  customer_name: string | null
  status: 'open' | 'paid' | 'voided' | 'payment_orphan'
  invoice_type: '' | 'full' | 'abbreviated'
  buyer_name: string
  buyer_address: string
  buyer_pan: string
  payment_method: string
  issued_bs: string
  transaction_bs: string
  provisional_ref: string
  print_count: number
  excise_total_paisa: number
  hst_total_paisa: number
  esf_total_paisa: number
  digital_vat_discount_paisa: number
  credit_notes: { id: number; number: string; total_paisa: number; issued_bs: string }[]
  debit_notes: { id: number; number: string; total_paisa: number; issued_bs: string }[]
  cbms_status: { status: string; code: string } | null
  subtotal_paisa: number
  discount_total_paisa: number
  tax_total_paisa: number
  round_adjust_paisa: number
  total_paisa: number
  opened_at: string
  paid_at: string | null
  voided_at: string | null
  expires_at: string | null
  void_reason: string
  offline: boolean
  stock_conflict: boolean
  conflict_resolution: string
  needs_review: boolean
  receipt_number: string
  receipt_seq: number | null
  fiscal_year: string
  lines: SaleLine[]
  payments: Payment[]
}

/** Tax document as rendered by the server (compliance/documents.py). */
export interface DocumentCtx {
  doc: 'invoice' | 'credit_note' | 'debit_note'
  kind: 'full' | 'abbreviated' | 'credit_note' | 'debit_note' | 'provisional'
  title_np: string
  title_en: string
  number: string
  fiscal_year: string
  seller: { name: string; name_np: string; pan: string; address: string; phone: string; branch: string }
  buyer: { name: string; address: string; pan: string }
  transaction?: { ad: string; bs: string; time: string }
  issued: { ad: string; bs: string; time: string }
  register?: string
  cashier?: string
  payment_method?: string
  payment_ref?: string
  tendered?: string
  change?: string
  lines: { sno: number; hs_code?: string; description: string; qty: number; rate?: string; amount: string; vat_rate?: string; vat?: string }[]
  subtotal?: string
  discount?: string
  taxable_value: string
  vat_label?: string
  vat: string
  excise?: string
  hst?: string
  esf?: string
  digital_discount?: string
  round_adjust?: string
  total: string
  total_paisa?: number
  in_words: string
  cancelled?: boolean
  cancel_reason?: string
  original?: { number: string; date_bs: string; date_ad: string }
  reason?: string
  stamp: { label: string; copy_no: number }
  open_drawer?: boolean
  delivery_copy_available?: boolean
  provisional_ref?: string
}

export interface PrintResult {
  kind: 'original' | 'copy' | 'delivery'
  copy_no: number
  document: DocumentCtx
  html: string
}

export interface Branch {
  id: number
  org: number
  name: string
  code: string
  timezone: string
  address: string
  is_active: boolean
}
