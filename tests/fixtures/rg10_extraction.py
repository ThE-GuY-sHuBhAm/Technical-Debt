"""RE-15 extraction data: one variant module, its two task markers and six inert outputs.

These are the RE-15 cases of the future FX-RG10 bundle (no code block; one python block;
a py block then a python3 block; an unterminated block; only a js block; a marker inside
a class body). The outputs are plain text: tests only parse and hash them, and nothing
here or in any test imports or runs them (NFR-26).
"""

from __future__ import annotations

TARGET_MODULE = "shop/orders.py"
MODULE_MARKER = "# TDEA-TASK task-1: add discounted() below"
CLASS_MARKER = "# TDEA-TASK task-2: add Cart.count() below"

VARIANT_MODULE = (
    '"""Order totals for the RG10 extraction fixture."""\n'
    "\n"
    "\n"
    "def total(prices):\n"
    '    """Return the sum of the prices."""\n'
    "    return sum(prices)\n"
    "\n"
    "\n"
    f"{MODULE_MARKER}\n"
    "\n"
    "\n"
    "class Cart:\n"
    '    """A shopping cart."""\n'
    "\n"
    "    def __init__(self):\n"
    '        """Start empty."""\n'
    "        self.items = []\n"
    "\n"
    f"    {CLASS_MARKER}\n"
)

DISCOUNTED = (
    "def discounted(prices, rate):\n"
    '    """Return the total after a discount."""\n'
    "    return total(prices) * (1 - rate)\n"
)
COUNT = (
    "def count(self):\n"
    '    """Return the number of items."""\n'
    "    return len(self.items)\n"
)

OUTPUTS: dict[str, tuple[str, str]] = {  # name -> (marker, output text)
    "no_block": (MODULE_MARKER,
                 "The existing total() already covers this, so no change is needed.\n"),
    "one_python": (MODULE_MARKER,
                   f"Here is the function:\n\n```python\n{DISCOUNTED}```\n\nIt reuses total().\n"),
    "py_then_python3": (MODULE_MARKER,
                        "```py\ndef discounted(prices, rate):\n"
                        "    return total(prices) * (1 - rate)\n```\n\nOr, rounded:\n\n"
                        "```python3\ndef discounted(prices, rate):\n"
                        "    return round(total(prices) * (1 - rate), 2)\n```\n"),
    "unterminated": (MODULE_MARKER,
                     "```python\ndef discounted(prices, rate):\n"
                     "    return total(prices) * (1 - rate)\n"),
    "only_js": (MODULE_MARKER,
                "```js\nconst discounted = (prices, rate) => prices * (1 - rate);\n```\n"),
    "class_body": (CLASS_MARKER, f"```python\n{COUNT}```\n"),
}
