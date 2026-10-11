from app.schemas.terms import FieldName

FIELD_QUERIES: dict[FieldName, str] = {
    "contract_type": (
        "Agreement title purpose power purchase agreement PPA engineering procurement "
        "construction EPC scope"
    ),
    "parties": "Parties legal names seller buyer owner contractor employer project company",
    "effective_date": "Effective date commencement date conditions precedent execution date",
    "term_years": "Term duration years expiry initial term renewal extension",
    "COD": "Commercial operation date COD scheduled actual commissioning completion tests deadline",
    "price_tariff": (
        "Tariff electricity price kWh MWh contract price lump sum currency price schedule"
    ),
    "escalation": (
        "Escalation indexation inflation price adjustment annual rate formula base date "
        "cap"
    ),
    "delivery_point": "Delivery point interconnection metering point transfer electricity losses",
    "guaranteed_output": (
        "Guaranteed output energy generation capacity availability performance guarantee "
        "exclusions"
    ),
    "termination_rights": (
        "Termination rights default cure notice termination payment early termination"
    ),
    "liquidated_damages": (
        "Liquidated damages delay performance shortfall rate cap liability exclusions"
    ),
    "change_in_law": "Change in law regulatory change taxes costs relief compensation procedure",
    "force_majeure": (
        "Force majeure definition exclusions notice mitigation relief prolonged "
        "termination"
    ),
    "payment_terms": (
        "Payment invoice due date milestone retention dispute interest payment security"
    ),
}

FIELD_GUIDANCE: dict[FieldName, str] = {
    "contract_type": (
        "Classify this agreement itself, not another agreement mentioned in it. Only PPA "
        "or EPC. Return null for hybrid, other, or unclear documents."
    ),
    "parties": (
        "Extract all identified contracting parties with their legal names and roles. "
        "Cite evidence supporting every party. Do not add advisers or referenced third "
        "parties."
    ),
    "effective_date": (
        "Return an ISO date only when the effective date is explicit and unambiguous. Do "
        "not substitute signature/execution dates or resolve unknown conditions."
    ),
    "term_years": (
        "Extract the initial duration in years; preserve renewal and start-trigger "
        "qualifications in reason. Do not add optional renewals. Convert months to years "
        "only when explicit; do not infer from ambiguous dates."
    ),
    "COD": (
        "Separate scheduled and actual COD. Dates may be null when only a "
        "milestone/relative deadline is stated. Preserve that condition. Do not "
        "substitute EPC completion for COD unless explicitly equivalent."
    ),
    "price_tariff": (
        "Preserve currency, units, taxes, capacity/energy components and conditions in "
        "basis. For variable formulas or multiple rates set amount null and describe the "
        "full formula/rates. Never infer currency from an ambiguous dollar sign or "
        "invent unit conversions."
    ),
    "escalation": (
        "Preserve rate, index, formula, timing, base date, cap and exceptions. No "
        "escalation is a valid value only if explicitly stated."
    ),
    "delivery_point": (
        "Identify the contractual delivery or metering point and transfer/loss "
        "responsibilities. Do not assume the plant location is the delivery point."
    ),
    "guaranteed_output": (
        "Keep output quantity and unit distinct from availability percentage. Preserve "
        "measurement period, exclusions and weather adjustments."
    ),
    "termination_rights": (
        "Include triggering events, entitled party, notice, cure period, exceptions and "
        "any stated termination payments. Avoid implying this list is exhaustive if "
        "evidence is partial."
    ),
    "liquidated_damages": (
        "Include delay/performance triggers, calculation, currency, caps and exclusions. "
        "Distinguish liquidated damages from general damages or penalties."
    ),
    "change_in_law": (
        "Include trigger, allocated party, notice, relief/compensation and exceptions."
    ),
    "force_majeure": (
        "Include defined events, exclusions, notice, relief and prolonged-event "
        "termination where supported."
    ),
    "payment_terms": (
        "Include invoice/milestone timing, due days, retention, disputes, interest, "
        "currency and conditions where supported."
    ),
}
