"""Every us-gaap tag the project reads.

fetch_data.py saves only these tags to the raw-data cache, and sec_data.py refuses to read
any tag that is not listed here. Changing this file invalidates the cache, which triggers a
fresh SEC download on the next build.
"""

EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")  # diluted preferred
SHARES_TAG = "WeightedAverageNumberOfDilutedSharesOutstanding"

REVENUE_TAGS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
NET_INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss")
OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
OPERATING_CASH_FLOW_TAGS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
CAPEX_TAGS = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets")

BALANCE_SHEET_TAGS = (
    "Assets",
    "Liabilities",
    "LiabilitiesAndStockholdersEquity",
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "Goodwill",
    "IntangibleAssetsNetExcludingGoodwill",
    "FiniteLivedIntangibleAssetsNet",
    "IndefiniteLivedIntangibleAssetsExcludingGoodwill",
    "AssetsCurrent",
    "LiabilitiesCurrent",
)
DEBT_TAGS = (
    "DebtLongtermAndShorttermCombinedAmount",
    "LongTermDebt",
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "ShortTermBorrowings",
    "CommercialPaper",
)

KEEP_TAGS = frozenset(
    EPS_TAGS
    + (SHARES_TAG,)
    + REVENUE_TAGS
    + NET_INCOME_TAGS
    + OPERATING_INCOME_TAGS
    + OPERATING_CASH_FLOW_TAGS
    + CAPEX_TAGS
    + BALANCE_SHEET_TAGS
    + DEBT_TAGS
)
KEEP_UNITS = frozenset({"USD", "USD/shares", "shares"})
