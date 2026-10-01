"""Every XBRL tag the project reads, grouped by meaning, for each accounting standard.

US companies report under US GAAP (the "us-gaap" taxonomy); most foreign companies report
under IFRS ("ifrs-full"), which names the same concepts differently. sec_data.py asks for a
group ("net_income", "capex", ...) and gets the tags for the company's own taxonomy, in
order of preference.

fetch_data.py saves only these tags to the raw-data cache, and sec_data.py refuses to read
any tag that is not listed here. Changing this file invalidates the cache, which triggers a
fresh SEC download on the next build.
"""

TAXONOMIES = {
    "us-gaap": {
        "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),  # diluted preferred
        "shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        "revenue": (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
        "net_income": ("NetIncomeLoss", "ProfitLoss"),
        "operating_income": ("OperatingIncomeLoss",),
        "operating_cash_flow": (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
        "assets": ("Assets",),
        "liabilities": ("Liabilities",),
        "liabilities_and_equity": ("LiabilitiesAndStockholdersEquity",),
        "equity": ("StockholdersEquity",),
        "equity_with_minorities": (
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "StockholdersEquity",
        ),
        "goodwill": ("Goodwill",),
        # "us-gaap:Intangibles" is not a real taxonomy element; these are the tags filers use.
        "intangibles": ("IntangibleAssetsNetExcludingGoodwill",),
        "intangibles_parts": (
            "FiniteLivedIntangibleAssetsNet",
            "IndefiniteLivedIntangibleAssetsExcludingGoodwill",
        ),
        "intangibles_with_goodwill": (),
        "current_assets": ("AssetsCurrent",),
        "current_liabilities": ("LiabilitiesCurrent",),
        "debt_total": ("DebtLongtermAndShorttermCombinedAmount",),
        "debt_long_total": (
            "LongTermDebt",
            "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        ),
        "debt_long_noncurrent": ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"),
        "debt_long_current": ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"),
        "debt_short": ("ShortTermBorrowings", "CommercialPaper"),
    },
    "ifrs-full": {
        "eps": ("DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"),
        "shares": ("AdjustedWeightedAverageShares", "WeightedAverageShares"),
        "revenue": ("Revenue", "RevenueFromContractsWithCustomers"),
        "net_income": ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss"),
        "operating_income": ("ProfitLossFromOperatingActivities",),
        "operating_cash_flow": ("CashFlowsFromUsedInOperatingActivities",),
        "capex": (
            "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets",
            "PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets",
        ),
        "assets": ("Assets",),
        "liabilities": ("Liabilities",),
        "liabilities_and_equity": ("EquityAndLiabilities",),
        "equity": ("EquityAttributableToOwnersOfParent", "Equity"),
        "equity_with_minorities": ("Equity",),
        "goodwill": ("Goodwill",),
        "intangibles": ("IntangibleAssetsOtherThanGoodwill",),
        "intangibles_parts": (),
        "intangibles_with_goodwill": ("IntangibleAssetsAndGoodwill",),
        "current_assets": ("CurrentAssets",),
        "current_liabilities": ("CurrentLiabilities",),
        "debt_total": ("Borrowings",),
        "debt_long_total": (),
        "debt_long_noncurrent": ("LongtermBorrowings", "NoncurrentPortionOfNoncurrentBorrowings"),
        "debt_long_current": (),
        "debt_short": (
            "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
            "ShorttermBorrowings",
            "CurrentPortionOfLongtermBorrowings",
        ),
    },
}

KEEP_TAGS = {
    taxonomy: frozenset(tag for group in groups.values() for tag in group)
    for taxonomy, groups in TAXONOMIES.items()
}
