"""Amount in words for invoices (Rule 17), Nepali/Indian grouping:
12,34,567.50 -> 'Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Fifty Paisa Only'."""

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
        "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return ONES[n]
    return (TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")).strip()


def _three(n: int) -> str:
    hundred, rest = divmod(n, 100)
    parts = []
    if hundred:
        parts.append(f"{ONES[hundred]} Hundred")
    if rest:
        parts.append(_two(rest))
    return " ".join(parts)


def number_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for unit, size in (("Arab", 10 ** 9), ("Crore", 10 ** 7), ("Lakh", 10 ** 5), ("Thousand", 10 ** 3)):
        q, n = divmod(n, size)
        if q:
            parts.append(f"{number_words(q) if q >= 100 else _two(q)} {unit}")
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def amount_in_words(paisa: int) -> str:
    sign = "Minus " if paisa < 0 else ""
    rupees, p = divmod(abs(paisa), 100)
    words = f"{sign}Rupees {number_words(rupees)}"
    if p:
        words += f" and {_two(p)} Paisa"
    return words + " Only"
