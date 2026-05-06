"""
MAP-E (RFC 7597) PSID and CE address calculation as Ansible filters.

Used by the network role's MAP-E mode (Phase MAP-E, issue #19) to derive the
CE IPv4 address, port set, and CE IPv6 address from the customer's WAN IPv6
prefix and a JPNE-style mapping rule definition.

The math follows RFC 7597 sections 5.1-5.2. No external Ansible utilities or
collections are required; pure Python on top of the stdlib `ipaddress` module.

Usage from a template / task:

    {% set m = wan_ipv6_prefix | mape_compute(mape_rule) %}
    {{ m.ce_ipv4 }}              -> "192.0.2.18"
    {{ m.ce_ipv6 }}              -> "2001:db8:12:3400:0:c000:212:34"
    {{ m.psid }}                 -> 52
    {{ m.port_ranges | length }} -> 63   (when psid_offset=6, k=8)
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, IPv6Network


def _bits_at(value: int, msb_index: int, count: int, total_bits: int = 128) -> int:
    """Extract `count` bits from `value`, starting at `msb_index` (MSB=0)."""
    shift = total_bits - msb_index - count
    if shift < 0:
        raise ValueError(
            f"bit window {msb_index}+{count} exceeds {total_bits}-bit value"
        )
    mask = (1 << count) - 1
    return (value >> shift) & mask


def mape_compute(customer_v6_prefix: str, rule: dict) -> dict:
    """
    Compute MAP-E parameters for ``customer_v6_prefix`` under ``rule``.

    Parameters
    ----------
    customer_v6_prefix : str
        Customer's delegated IPv6 prefix, e.g. ``"2001:db8:0012:3400::/56"``.
    rule : dict
        Mapping rule. Required keys:
            * ``br_address``            (str)  IPv6 of the Border Relay
            * ``rule_v6_prefix``        (str)  rule IPv6 prefix
            * ``rule_v6_prefix_length`` (int)  /N
            * ``rule_v4_prefix``        (str)  rule IPv4 prefix
            * ``rule_v4_prefix_length`` (int)  /N
            * ``ea_length``             (int)  EA-bits length
        Optional:
            * ``psid_offset``           (int)  PSID offset (a), defaults to 0;
                                               JPNE v6プラス uses 6 (excludes
                                               m=0 = well-known port range).

    Returns
    -------
    dict
        ``ce_ipv4``     (str): assigned CE IPv4 address
        ``psid``        (int): assigned PSID
        ``port_ranges`` (list[list[int]]): TCP/UDP port set as [start, end] pairs
        ``ce_ipv6``     (str): CE IPv6 address (BMR endpoint, used as inner of ip6tnl)
        ``br_address``  (str): pass-through BR address from the rule
    """
    customer_net = IPv6Network(customer_v6_prefix, strict=False)
    customer_int = int(customer_net.network_address)

    rule_v6_len = int(rule["rule_v6_prefix_length"])
    rule_v4_len = int(rule["rule_v4_prefix_length"])
    ea_length = int(rule["ea_length"])
    psid_offset = int(rule.get("psid_offset", 0))

    # The CE IPv6 (BMR) layout reserves [64, 128) for the interface ID, so EA-bits
    # at [rule_v6_len, rule_v6_len + ea_length) must end on or before bit 64. If
    # they spill into [64, ...), the IID overwrite below silently wipes them and
    # produces a wrong ce_ipv6 — so fail loudly instead.
    if rule_v6_len + ea_length > 64:
        raise ValueError(
            f"rule_v6_prefix_length ({rule_v6_len}) + ea_length ({ea_length}) "
            f"must be <= 64; EA-bits would overlap the interface-ID region"
        )

    # The customer prefix must actually contain the EA-bit window; otherwise the
    # bit-window read returns the zero-fill below the prefix and silently yields
    # the wrong CE.
    if customer_net.prefixlen < rule_v6_len + ea_length:
        raise ValueError(
            f"customer prefix length /{customer_net.prefixlen} is shorter than "
            f"rule_v6_prefix_length ({rule_v6_len}) + ea_length ({ea_length}); "
            f"cannot extract EA-bits"
        )

    # The customer prefix must lie inside the rule's IPv6 prefix, otherwise the
    # caller is asking for a CE that doesn't belong to this rule.
    rule_v6_net = IPv6Network(
        f"{rule['rule_v6_prefix']}/{rule_v6_len}", strict=False
    )
    if not rule_v6_net.supernet_of(customer_net):
        raise ValueError(
            f"customer prefix {customer_v6_prefix} is not contained in rule "
            f"prefix {rule['rule_v6_prefix']}/{rule_v6_len}"
        )

    # EA-bits = bits [rule_v6_len, rule_v6_len + ea_length) of customer prefix.
    ea_bits = _bits_at(customer_int, rule_v6_len, ea_length, 128)

    # EA splits into IPv4 suffix (high q bits) and PSID (low k bits).
    q = 32 - rule_v4_len  # IPv4 suffix bits
    k = ea_length - q     # PSID length
    if k < 0:
        raise ValueError(
            f"EA-bits length ({ea_length}) is shorter than the IPv4 suffix "
            f"({q}); rule_v4_prefix_length too short or ea_length too short"
        )
    a = psid_offset
    if a + k > 16:
        raise ValueError(
            f"PSID offset ({a}) + PSID length ({k}) exceeds the 16-bit port space"
        )

    ipv4_suffix = ea_bits >> k if k > 0 else ea_bits
    psid = ea_bits & ((1 << k) - 1) if k > 0 else 0

    # CE IPv4 = rule v4 prefix with the suffix in the lower q bits.
    rule_v4_int = int(IPv4Address(rule["rule_v4_prefix"]))
    rule_v4_aligned = (rule_v4_int >> q) << q if q > 0 else rule_v4_int
    ce_v4_int = rule_v4_aligned | ipv4_suffix
    ce_v4 = str(IPv4Address(ce_v4_int))

    # Port ranges per RFC 7597 §5.1:
    #   port = m * 2^(16-a) + psid * 2^(16-a-k) + n
    #   m ∈ [0, 2^a),  n ∈ [0, 2^(16-a-k))
    # When a > 0, the m=0 stride covers ports [0, 2^(16-a)) which includes the
    # system / well-known port range; RFC 7597 §5.1 specifies this stride is
    # excluded for a > 0 ("the j=0 case is excluded since it includes ports
    # less than 2^(16-A)"). The motivation for choosing a > 0 in real
    # deployments (e.g. JPNE v6プラス: a=6) is precisely to obtain that
    # exclusion, so they're tied: if the operator wants the well-known range
    # included they set a=0 and accept a single 2^(16-k)-port block.
    n_per_block = 1 << (16 - a - k)
    m_max = 1 << a
    m_start = 1 if a > 0 else 0
    port_ranges: list[list[int]] = []
    for m in range(m_start, m_max):
        base = (m << (16 - a)) | (psid << (16 - a - k))
        port_ranges.append([base, base + n_per_block - 1])

    # CE IPv6 (BMR address) per RFC 7597 §5.1:
    #   bits [0, rule_v6_len)                     : rule v6 prefix
    #   bits [rule_v6_len, rule_v6_len + ea_len)  : EA-bits
    #   bits [rule_v6_len + ea_len, 64)           : subnet-id (zeros here)
    #   bits [64, 128)                            : interface ID
    #         16 zero | 32 CE-IPv4 | 16 PSID
    rule_v6_int = int(IPv6Address(rule["rule_v6_prefix"]))
    rule_v6_aligned = (rule_v6_int >> (128 - rule_v6_len)) << (128 - rule_v6_len)
    ea_shift = 128 - rule_v6_len - ea_length
    ce_prefix_int = rule_v6_aligned | (ea_bits << ea_shift)

    iid = (ce_v4_int << 16) | psid  # leading 16 zero bits implied
    ce_v6_int = (ce_prefix_int & ~((1 << 64) - 1)) | iid
    ce_v6 = str(IPv6Address(ce_v6_int))

    return {
        "ce_ipv4": ce_v4,
        "psid": psid,
        "port_ranges": port_ranges,
        "ce_ipv6": ce_v6,
        "br_address": rule["br_address"],
    }


class FilterModule:
    """Ansible filter plugin entry point."""

    def filters(self) -> dict:
        return {
            "mape_compute": mape_compute,
        }
